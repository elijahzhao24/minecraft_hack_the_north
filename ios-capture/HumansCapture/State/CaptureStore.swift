import ARKit
import Combine
import Foundation
import HumansCaptureCore
import UIKit

@MainActor
final class CaptureStore: ObservableObject {
    @Published var deviceID: String {
        didSet { UserDefaults.standard.set(deviceID, forKey: Self.deviceIDKey) }
    }
    @Published var backendURL: String {
        didSet { UserDefaults.standard.set(backendURL, forKey: Self.backendURLKey) }
    }
    @Published private(set) var sessionRunning = false
    @Published private(set) var socketState: CaptureSocketState = .disconnected
    @Published private(set) var snapshotStatus = "Idle"
    @Published private(set) var trackingState: TrackingState = .notAvailable
    @Published private(set) var rgbDimensions = "—"
    @Published private(set) var depthDimensions = "—"
    @Published private(set) var sequence: UInt64 = 0
    @Published private(set) var validDepthFraction = 0.0
    @Published private(set) var queueDepth = 0
    @Published private(set) var lastBackendError: String?
    @Published private(set) var calibrationInvalid = false
    @Published private(set) var depthPreview: UIImage?
    @Published var liveModeEnabled = false {
        didSet { captureController.setLiveModeEnabled(liveModeEnabled) }
    }

    let captureController: ARCaptureController
    private let assembler = RGBDFrameAssembler()
    private var socket: CaptureSocket?
    private var socketEventsTask: Task<Void, Never>?
    private var sessionID: UUID?
    private var backgrounded = false

    private static let deviceIDKey = "capture.device-id"
    private static let backendURLKey = "capture.backend-url"

    init(captureController: ARCaptureController = ARCaptureController()) {
        self.captureController = captureController
        self.deviceID = UserDefaults.standard.string(forKey: Self.deviceIDKey) ?? "front-phone"
        self.backendURL = UserDefaults.standard.string(forKey: Self.backendURLKey) ?? "ws://192.168.1.10:8000/ws/capture"

        captureController.onDiagnostics = { [weak self] diagnostics in
            Task { @MainActor in self?.apply(diagnostics) }
        }
        captureController.onCapturedBuffers = { [weak self] buffers in
            Task { @MainActor in self?.process(buffers) }
        }
        captureController.onError = { [weak self] message in
            Task { @MainActor in self?.lastBackendError = message }
        }
    }

    func startSession() {
        do {
            if socket != nil { disconnect() }
            sessionID = try captureController.start(deviceID: deviceID.trimmingCharacters(in: .whitespacesAndNewlines))
            sessionRunning = true
            calibrationInvalid = false
            snapshotStatus = "Ready"
        } catch {
            sessionRunning = false
            lastBackendError = error.localizedDescription
        }
    }

    func stopSession() {
        captureController.stop()
        sessionRunning = false
        liveModeEnabled = false
        snapshotStatus = "Stopped"
    }

    func connect() {
        guard socket == nil, let sessionID else {
            lastBackendError = sessionID == nil ? "Start the AR session before connecting." : nil
            return
        }
        do {
            let endpoint = try Self.captureEndpoint(from: backendURL)
            let appVersion = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.1.0"
            let newSocket = CaptureSocket(
                endpoint: endpoint,
                deviceID: deviceID,
                sessionID: sessionID,
                appVersion: appVersion,
                supportsSceneDepth: ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)
            )
            socket = newSocket
            socketEventsTask = Task { [weak self, events = newSocket.events] in
                for await event in events {
                    guard let self else { return }
                    self.handle(event)
                }
            }
            Task { await newSocket.connect() }
        } catch {
            lastBackendError = error.localizedDescription
        }
    }

    func disconnect() {
        socketEventsTask?.cancel()
        socketEventsTask = nil
        let oldSocket = socket
        socket = nil
        Task { await oldSocket?.disconnect() }
        socketState = .disconnected
        queueDepth = 0
    }

    func captureSnapshot() {
        guard sessionRunning else {
            lastBackendError = "Start the AR session before capturing."
            return
        }
        if captureController.requestSnapshot() {
            snapshotStatus = "Waiting for a tracked depth frame"
        } else {
            snapshotStatus = "Snapshot already pending"
        }
    }

    func scenePhaseChanged(isActive: Bool) {
        if !isActive {
            backgrounded = true
            disconnect()
            stopSession()
        } else if backgrounded {
            backgrounded = false
            // A foreground transition always creates a fresh session/sequence domain.
            startSession()
        }
    }

    private func process(_ buffers: CapturedBuffers) {
        depthPreview = DepthPreviewRenderer.render(
            depthMetersLE: buffers.depthMetersLE,
            confidence: buffers.confidence,
            width: buffers.depthWidth,
            height: buffers.depthHeight
        )
        snapshotStatus = "Encoding"
        let assembler = self.assembler
        let socket = self.socket
        Task { [weak self] in
            do {
                let encoded = try await assembler.assemble(buffers)
                guard let self else { return }
                self.validDepthFraction = encoded.validDepthFraction
                self.snapshotStatus = socket == nil ? "Encoded (not connected)" : "Queued for upload"
                guard let socket else { return }
                let result = await socket.enqueue(encoded.pendingCapture)
                switch result {
                case .rejectedSnapshotQueueFull:
                    self.snapshotStatus = "Snapshot queue full"
                case .droppedLiveQueueFull:
                    break
                case .accepted, .replacedLive:
                    if encoded.mode == .snapshot, encoded.requestID != nil {
                        self.snapshotStatus = "Awaiting backend acceptance"
                    }
                }
            } catch {
                self?.lastBackendError = error.localizedDescription
                self?.snapshotStatus = "Encoding failed"
            }
        }
    }

    private func handle(_ event: CaptureSocketEvent) {
        switch event {
        case .stateChanged(let state):
            socketState = state
        case .captureRequested(let request):
            let accepted = captureController.requestSnapshot(request)
            snapshotStatus = accepted ? "Backend snapshot queued" : "Snapshot queue busy"
            if let socket {
                Task {
                    await socket.acknowledge(ClientAcknowledgement(
                        requestID: request.requestID,
                        accepted: accepted,
                        code: accepted ? "capture_queued" : "processor_busy",
                        detail: accepted ? nil : "phone already has a pending snapshot"
                    ))
                }
            }
        case .acknowledged(let acknowledgement):
            snapshotStatus = acknowledgement.accepted ? "Accepted: \(acknowledgement.code)" : "Rejected: \(acknowledgement.code)"
            if !acknowledgement.accepted { lastBackendError = acknowledgement.detail ?? acknowledgement.code }
        case .serverError(let error):
            lastBackendError = "\(error.code): \(error.message)"
            snapshotStatus = "Backend error"
        case .localError(let message):
            lastBackendError = message
        case .queueChanged(let count):
            queueDepth = count
        }
    }

    private func apply(_ diagnostics: CaptureFrameDiagnostics) {
        rgbDimensions = "\(diagnostics.rgbWidth) × \(diagnostics.rgbHeight)"
        depthDimensions = diagnostics.depthWidth > 0 ? "\(diagnostics.depthWidth) × \(diagnostics.depthHeight)" : "Unavailable"
        sequence = diagnostics.sequence
        trackingState = diagnostics.trackingState
        calibrationInvalid = diagnostics.calibrationInvalid
    }

    private static func captureEndpoint(from input: String) throws -> URL {
        let trimmed = input.trimmingCharacters(in: .whitespacesAndNewlines)
        let withScheme = trimmed.contains("://") ? trimmed : "ws://\(trimmed)"
        guard var components = URLComponents(string: withScheme),
              components.scheme == "ws" || components.scheme == "wss",
              components.host != nil else {
            throw URLError(.badURL)
        }
        if components.port == nil { components.port = 8_000 }
        if components.path.isEmpty || components.path == "/" { components.path = "/ws/capture" }
        guard let url = components.url else { throw URLError(.badURL) }
        return url
    }
}

private enum DepthPreviewRenderer {
    // This range is a visualization choice only; transmitted metric depth is never clamped.
    private static let nearMeters: Float32 = 0.25
    private static let farMeters: Float32 = 4.0

    static func render(
        depthMetersLE: Data,
        confidence: Data,
        width: Int,
        height: Int
    ) -> UIImage? {
        guard depthMetersLE.count == width * height * 4, confidence.count == width * height else { return nil }
        var rgba = Data(count: width * height * 4)
        rgba.withUnsafeMutableBytes { output in
            depthMetersLE.withUnsafeBytes { depth in
                for index in 0..<(width * height) {
                    let bits = depth.loadUnaligned(fromByteOffset: index * 4, as: UInt32.self)
                    let meters = Float32(bitPattern: UInt32(littleEndian: bits))
                    let outputOffset = index * 4
                    guard meters > 0, confidence[index] > 0 else {
                        output[outputOffset] = 0
                        output[outputOffset + 1] = 0
                        output[outputOffset + 2] = 0
                        output[outputOffset + 3] = 255
                        continue
                    }
                    let normalized = min(1, max(0, (meters - nearMeters) / (farMeters - nearMeters)))
                    output[outputOffset] = UInt8((1 - normalized) * 255)
                    output[outputOffset + 1] = UInt8((1 - abs(normalized - 0.5) * 2) * 255)
                    output[outputOffset + 2] = UInt8(normalized * 255)
                    output[outputOffset + 3] = 255
                }
            }
        }
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        guard let provider = CGDataProvider(data: rgba as CFData),
              let cgImage = CGImage(
                width: width,
                height: height,
                bitsPerComponent: 8,
                bitsPerPixel: 32,
                bytesPerRow: width * 4,
                space: colorSpace,
                bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.last.rawValue),
                provider: provider,
                decode: nil,
                shouldInterpolate: false,
                intent: .defaultIntent
              ) else { return nil }
        return UIImage(cgImage: cgImage)
    }
}
