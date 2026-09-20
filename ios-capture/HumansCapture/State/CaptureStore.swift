import Foundation
import SwiftUI
import UIKit

@MainActor
final class CaptureStore: ObservableObject {
    @AppStorage("capture.device_id") var deviceID = "front-phone"
    @AppStorage("capture.backend_url") var backendURL = "ws://192.168.1.2:8000/ws/capture"

    @Published private(set) var isRunning = false
    @Published private(set) var liveEnabled = false
    @Published private(set) var socketState: CaptureSocketState = .disconnected
    @Published private(set) var sessionID: UUID?
    @Published private(set) var lastSequence: UInt64?
    @Published private(set) var trackingState: CaptureTrackingState = .notAvailable
    @Published private(set) var rgbDimensions = "—"
    @Published private(set) var depthDimensions = "—"
    @Published private(set) var validDepthFraction: Double?
    @Published private(set) var queueDepth = 0
    @Published private(set) var droppedFrames: UInt64 = 0
    @Published private(set) var lastStatus = "Ready"
    @Published private(set) var depthPreview: CGImage?
    @Published private(set) var lastFixtureURL: URL?

    let captureController: ARCaptureController
    let discovery = BackendDiscovery()
    private let socket: CaptureSocket
    private let pipeline: CapturePipeline
    private let diagnostics = CaptureEventLogger.shared
    private var wantsConnection = false
    private var reconnectAttempt = 0
    private var reconnectTask: Task<Void, Never>?
    private var wasRunningBeforeBackground = false

    private func updateIdleTimer() {
        // Prevent screen from turning off/locking when connected to backend or actively capturing
        UIApplication.shared.isIdleTimerDisabled = (socketState == .ready || isRunning)
    }

    init() {
        let controller = ARCaptureController()
        let captureSocket = CaptureSocket()
        captureController = controller
        socket = captureSocket
        pipeline = CapturePipeline(deviceID: "front-phone", socket: captureSocket)

        discovery.start()

        controller.eventHandler = { [weak self] event in
            Task { @MainActor [weak self] in self?.handleCaptureEvent(event) }
        }
        Task {
            await captureSocket.setEventHandler { [weak self] event in
                Task { @MainActor [weak self] in self?.handleSocketEvent(event) }
            }
            await pipeline.setEventHandler { [weak self] event in
                Task { @MainActor [weak self] in self?.handlePipelineEvent(event) }
            }
        }
    }

    func selectDiscoveredBackend(_ backend: DiscoveredBackend) {
        backendURL = backend.url.absoluteString
        connect()
    }

    func start() {
        guard !isRunning else { return }
        lastStatus = "Starting AR session…"
        captureController.start()
    }

    func stop() {
        wantsConnection = false
        reconnectTask?.cancel()
        reconnectTask = nil
        captureController.stop()
        Task {
            await socket.disconnect()
            await pipeline.clear()
        }
        updateIdleTimer()
    }

    func connect() {
        wantsConnection = true
        reconnectAttempt = 0
        if !isRunning { start() }
        else { connectCurrentSession() }
    }

    func disconnect() {
        wantsConnection = false
        reconnectTask?.cancel()
        Task { await socket.disconnect() }
        updateIdleTimer()
    }

    func captureSnapshot() {
        guard isRunning else {
            lastStatus = "Start capture before requesting a snapshot."
            return
        }
        let captureID = UUID()
        diagnostics.beginCapture(captureID: captureID, mode: .snapshot, attributes: commonAttributes())
        Task {
            if await captureController.requestCapture(captureID: captureID) {
                lastStatus = "Snapshot requested"
            } else {
                let error = PipelineError.snapshotQueueFull
                diagnostics.log(.warning, "capture.request.rejected", attributes: ["reason": error.localizedDescription])
                diagnostics.finishCapture(captureID: captureID, error: error)
                lastStatus = error.localizedDescription
            }
        }
    }

    func toggleLive() {
        // Live frames are requested by the backend so both phones share each
        // capture_id; a phone-side timer would produce frames that never pair.
        let next = !liveEnabled
        Task { [weak self] in
            do {
                try await self?.pipeline.setLive(next)
                await MainActor.run {
                    self?.liveEnabled = next
                    self?.lastStatus = next ? "Live capture requested" : "Live capture stopped"
                }
            } catch {
                await MainActor.run { self?.lastStatus = "Live toggle failed: \(error.localizedDescription)" }
            }
        }
    }

    func handleScenePhase(_ phase: ScenePhase) {
        switch phase {
        case .background:
            wasRunningBeforeBackground = isRunning
            discovery.stop()
            UIApplication.shared.isIdleTimerDisabled = false
            if wasRunningBeforeBackground {
                captureController.stop()
                Task {
                    await socket.disconnect()
                    await pipeline.clear()
                }
            }
        case .active:
            discovery.start()
            updateIdleTimer()
            if wasRunningBeforeBackground || wantsConnection {
                wasRunningBeforeBackground = false
                captureController.start()
            }
        default:
            break
        }
    }

    private func handleCaptureEvent(_ event: CaptureLifecycleEvent) {
        switch event {
        case .started(let newSessionID):
            isRunning = true
            sessionID = newSessionID
            lastSequence = nil
            lastStatus = "AR session running"
            diagnostics.log(.info, "capture.session.started", attributes: commonAttributes())
            Task { await pipeline.updateDeviceID(deviceID.trimmingCharacters(in: .whitespacesAndNewlines)) }
            if wantsConnection { connectCurrentSession() }
            updateIdleTimer()
        case .stopped:
            isRunning = false
            liveEnabled = false
            diagnostics.log(.info, "capture.session.stopped", attributes: commonAttributes())
            lastStatus = "Capture stopped"
            updateIdleTimer()
        case .unsupportedSceneDepth:
            isRunning = false
            lastStatus = "This device does not support ARKit scene depth."
            diagnostics.log(.error, "capture.scene_depth.unsupported", attributes: commonAttributes())
            updateIdleTimer()
        case .missingDepth:
            diagnostics.log(
                .warning,
                "capture.depth.missing",
                attributes: commonAttributes(),
                rateLimitKey: "missing_depth"
            )
        case .trackingChanged(let state):
            trackingState = state
            if state != .normal {
                diagnostics.log(
                    .warning,
                    "capture.tracking.degraded",
                    attributes: commonAttributes().merging(["tracking_state": state.rawValue]) { _, new in new },
                    rateLimitKey: "tracking_\(state.rawValue)"
                )
            }
        case .diagnostic(let diagnostic):
            rgbDimensions = "\(diagnostic.rgbWidth) × \(diagnostic.rgbHeight)"
            depthDimensions = "\(diagnostic.depthWidth) × \(diagnostic.depthHeight)"
        case .depthPreview(let source):
            Task { @MainActor [weak self] in
                let rendered = await Task.detached(priority: .utility) {
                    RenderedDepthPreview(image: DepthPreviewRenderer.render(source.pixelBuffer, rangeGate: source.rangeGate))
                }.value
                self?.depthPreview = rendered.image
            }
        case .frameReady(let source):
            if source.intent.mode == .live {
                diagnostics.beginCapture(captureID: source.intent.captureID, mode: .live, attributes: commonAttributes())
            }
            let acquireSpan = diagnostics.startSpan(captureID: source.intent.captureID, operation: "capture.acquire")
            acquireSpan?.setData(value: source.sequence, key: "sequence")
            acquireSpan?.finish(status: .ok)
            Task { await pipeline.submit(source) }
        case .failed(let message):
            lastStatus = message
            diagnostics.log(.error, "capture.session.failed", attributes: ["error": message])
        }
    }

    private func handleSocketEvent(_ event: CaptureSocketEvent) {
        switch event {
        case .stateChanged(let state):
            socketState = state
            updateIdleTimer()
            switch state {
            case .ready:
                reconnectAttempt = 0
                lastStatus = "Backend connected"
                diagnostics.log(.info, "capture.websocket.connected", attributes: commonAttributes())
            case .failed(let message):
                lastStatus = "Connection lost: \(message)"
                diagnostics.log(.warning, "capture.websocket.reconnect", attributes: [
                    "device_id": deviceID,
                    "reconnect_attempt": reconnectAttempt,
                    "error": message
                ])
                scheduleReconnect()
            default:
                break
            }
        case .clockPing(let ping, let receivedAt):
            let transactionID = UUID()
            diagnostics.beginCapture(captureID: transactionID, mode: .live, attributes: ["clock_probe_id": ping.requestID.uuidString.lowercased()])
            let span = diagnostics.startSpan(captureID: transactionID, operation: "capture.clock_reply")
            Task {
                do {
                    let sentAt = ProcessInfo.processInfo.systemUptime
                    try await socket.sendClockPong(ClockPong(
                        requestID: ping.requestID,
                        backendSendTimeSeconds: ping.backendSendTimeSeconds,
                        phoneReceiveTimeSeconds: receivedAt,
                        phoneSendTimeSeconds: sentAt
                    ))
                    span?.finish(status: .ok)
                    diagnostics.log(.debug, "capture.clock.reply", attributes: ["clock_probe_id": ping.requestID.uuidString.lowercased()])
                    diagnostics.finishCapture(captureID: transactionID)
                } catch {
                    span?.finish(status: .internalError)
                    diagnostics.finishCapture(captureID: transactionID, error: error)
                }
            }
        case .captureRequest(let request):
            diagnostics.beginCapture(captureID: request.captureID, mode: request.mode, attributes: commonAttributes())
            Task {
                let accepted = await captureController.requestCapture(
                    captureID: request.captureID,
                    requestID: request.requestID,
                    mode: request.mode,
                    notBeforePhoneTimeSeconds: request.notBeforePhoneTimeSeconds
                )
                if !accepted {
                    diagnostics.finishCapture(
                        captureID: request.captureID,
                        error: PipelineError.snapshotQueueFull
                    )
                }
                try? await socket.sendAcknowledgement(
                    requestID: request.requestID,
                    accepted: accepted,
                    code: accepted ? "capture_queued" : "processor_busy",
                    detail: accepted ? nil : "phone capture request queue is full"
                )
                lastStatus = accepted ? "Backend snapshot requested" : "Backend capture rejected: queue full"
            }
        case .acknowledgement(let acknowledgement):
            lastStatus = acknowledgement.accepted ? "Backend accepted capture" : "Backend rejected capture: \(acknowledgement.code)"
        case .serverError(let error):
            lastStatus = "Backend error: \(error.code) — \(error.message)"
            diagnostics.log(.warning, "capture.backend.error", attributes: [
                "code": error.code,
                "retryable": error.retryable
            ])
        }
    }

    private func handlePipelineEvent(_ event: CapturePipelineEvent) {
        switch event {
        case .statistics(let statistics):
            queueDepth = statistics.queueDepth
            droppedFrames = statistics.droppedLiveFrames
            validDepthFraction = statistics.lastValidDepthFraction
            lastFixtureURL = statistics.lastFixture?.envelopeURL
        case .snapshotRejected(_, let reason):
            lastStatus = "Snapshot rejected: \(reason)"
        case .frameSent(_, let sequence):
            lastSequence = sequence
            lastStatus = "Frame \(sequence) sent"
        case .failed(_, let message):
            lastStatus = "Frame failed: \(message)"
        }
    }

    private func connectCurrentSession() {
        guard let sessionID,
              let url = URL(string: backendURL.trimmingCharacters(in: .whitespacesAndNewlines)),
              !deviceID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            lastStatus = "Enter a valid device ID and ws:// or wss:// backend URL."
            return
        }
        let id = deviceID.trimmingCharacters(in: .whitespacesAndNewlines)
        Task {
            do {
                await pipeline.updateDeviceID(id)
                try await socket.connect(url: url, hello: ClientHello(
                    deviceID: id,
                    sessionID: sessionID,
                    appVersion: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0",
                    supportsSceneDepth: ARCaptureController.supportsSceneDepth,
                    imageOrientation: UIDevice.current.orientation.isLandscape ? .landscapeRight : .portrait
                ))
            } catch {
                await MainActor.run {
                    lastStatus = error.localizedDescription
                    scheduleReconnect()
                }
            }
        }
    }

    private func scheduleReconnect() {
        guard wantsConnection, reconnectTask == nil else { return }
        reconnectAttempt += 1
        let delay = min(pow(2.0, Double(reconnectAttempt - 1)), 5.0)
        reconnectTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(delay))
            guard !Task.isCancelled else { return }
            guard let self else { return }
            await self.pipeline.clear()
            await self.socket.disconnect()
            await MainActor.run {
                self.reconnectTask = nil
                // Starting a fresh AR session guarantees a new session_id and clears
                // monotonic-clock/pairing state before the new hello.
                self.captureController.start()
            }
        }
    }

    private func commonAttributes() -> [String: Any] {
        var attributes: [String: Any] = ["device_id": deviceID]
        if let sessionID { attributes["session_id"] = sessionID.uuidString.lowercased() }
        if let lastSequence { attributes["sequence"] = lastSequence }
        return attributes
    }
}
