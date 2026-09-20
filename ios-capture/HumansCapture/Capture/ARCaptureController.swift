@preconcurrency import ARKit
import Foundation
import os

final class ARCaptureController: NSObject, @unchecked Sendable {
    let session = ARSession()

    private let logger = Logger(subsystem: "dev.hmc.HumansCapture", category: "capture")
    private let captureQueue = DispatchQueue(label: "dev.hmc.capture.frames", qos: .userInitiated)
    private var sessionID = UUID()
    private var sequence: UInt64 = 0
    private var pendingSnapshots: [CaptureIntent] = []
    private var liveEnabled = false
    private var lastLiveCaptureTime = -Double.infinity
    private let liveIntervalSeconds = 0.066
    private let maximumPendingRequests = 2
    private var lastTrackingState: CaptureTrackingState?
    private var lastPreviewTime = -Double.infinity

    var eventHandler: (@Sendable (CaptureLifecycleEvent) -> Void)?

    override init() {
        super.init()
        session.delegate = self
        session.delegateQueue = captureQueue
    }

    static var supportsSceneDepth: Bool {
        ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)
    }

    func start() {
        captureQueue.async { [weak self] in
            guard let self else { return }
            guard Self.supportsSceneDepth else {
                self.eventHandler?(.unsupportedSceneDepth)
                return
            }
            let configuration = ARWorldTrackingConfiguration()
            configuration.frameSemantics = [.sceneDepth]
            configuration.worldAlignment = .gravity
            self.sessionID = UUID()
            self.sequence = 0
            self.pendingSnapshots.removeAll(keepingCapacity: true)
            self.lastLiveCaptureTime = -.infinity
            self.lastPreviewTime = -.infinity
            self.session.run(configuration, options: [.resetTracking, .removeExistingAnchors])
            self.eventHandler?(.started(sessionID: self.sessionID))
        }
    }

    func stop() {
        captureQueue.async { [weak self] in
            guard let self else { return }
            self.session.pause()
            self.pendingSnapshots.removeAll()
            self.liveEnabled = false
            self.eventHandler?(.stopped)
        }
    }

    func setLiveEnabled(_ enabled: Bool) {
        captureQueue.async { [weak self] in self?.liveEnabled = enabled }
    }

    func requestCapture(
        captureID: UUID = UUID(),
        requestID: UUID? = nil,
        mode: CaptureMode = .snapshot,
        notBeforePhoneTimeSeconds: Double? = nil
    ) async -> Bool {
        await withCheckedContinuation { continuation in
            captureQueue.async { [weak self] in
                guard let self, self.pendingSnapshots.count < self.maximumPendingRequests else {
                    continuation.resume(returning: false)
                    return
                }
                if mode == .live {
                    self.liveEnabled = true
                }
                self.pendingSnapshots.append(CaptureIntent(
                captureID: captureID,
                requestID: requestID,
                mode: mode,
                notBeforePhoneTimeSeconds: notBeforePhoneTimeSeconds
                ))
                continuation.resume(returning: true)
            }
        }
    }
}

extension ARCaptureController: ARSessionDelegate {
    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        let trackingState = Self.mapTrackingState(frame.camera.trackingState)
        if trackingState != lastTrackingState {
            lastTrackingState = trackingState
            eventHandler?(.trackingChanged(trackingState))
        }

        guard let sceneDepth = frame.sceneDepth else {
            eventHandler?(.missingDepth)
            return
        }

        eventHandler?(.diagnostic(CaptureDiagnostics(
            rgbWidth: CVPixelBufferGetWidth(frame.capturedImage),
            rgbHeight: CVPixelBufferGetHeight(frame.capturedImage),
            depthWidth: CVPixelBufferGetWidth(sceneDepth.depthMap),
            depthHeight: CVPixelBufferGetHeight(sceneDepth.depthMap),
            trackingState: trackingState,
            captureTimestampSeconds: frame.timestamp
        )))
        if frame.timestamp - lastPreviewTime >= 0.5 {
            lastPreviewTime = frame.timestamp
            eventHandler?(.depthPreview(DepthPreviewSource(pixelBuffer: sceneDepth.depthMap)))
        }

        let intent: CaptureIntent
        if !pendingSnapshots.isEmpty,
           pendingSnapshots[0].notBeforePhoneTimeSeconds.map({ frame.timestamp >= $0 }) ?? true {
            intent = pendingSnapshots.removeFirst()
        } else if liveEnabled && frame.timestamp - lastLiveCaptureTime >= liveIntervalSeconds {
            intent = CaptureIntent(
                captureID: UUID(),
                requestID: nil,
                mode: .live,
                notBeforePhoneTimeSeconds: nil
            )
            lastLiveCaptureTime = frame.timestamp
        } else {
            return
        }

        let source = CapturedFrameSource(
            sourceFrameID: UUID(),
            sessionID: sessionID,
            intent: intent,
            sequence: sequence,
            captureTimestampSeconds: frame.timestamp,
            trackingState: trackingState,
            rgbPixelBuffer: frame.capturedImage,
            depthPixelBuffer: sceneDepth.depthMap,
            confidencePixelBuffer: sceneDepth.confidenceMap,
            rgbIntrinsics: frame.camera.intrinsics,
            arkitWorldFromCamera: frame.camera.transform
        )
        sequence &+= 1
        eventHandler?(.frameReady(source))
    }

    func session(_ session: ARSession, didFailWithError error: Error) {
        logger.error("AR session failed: \(error.localizedDescription, privacy: .public)")
        eventHandler?(.failed(error.localizedDescription))
    }

    private static func mapTrackingState(_ state: ARCamera.TrackingState) -> CaptureTrackingState {
        switch state {
        case .normal:
            return .normal
        case .notAvailable:
            return .notAvailable
        case .limited(let reason):
            switch reason {
            case .initializing: return .limitedInitializing
            case .excessiveMotion: return .limitedExcessiveMotion
            case .insufficientFeatures: return .limitedInsufficientFeatures
            case .relocalizing: return .limitedRelocalizing
            @unknown default: return .notAvailable
            }
        }
    }
}
