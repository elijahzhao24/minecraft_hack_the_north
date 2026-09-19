import ARKit
import Foundation
import HumansCaptureCore
import os
import simd

struct CaptureFrameDiagnostics: Sendable {
    let rgbWidth: Int
    let rgbHeight: Int
    let depthWidth: Int
    let depthHeight: Int
    let sequence: UInt64
    let trackingState: TrackingState
    let calibrationInvalid: Bool
}

struct ExpectedRasterDimensions: Sendable, Equatable {
    let rgbWidth: Int
    let rgbHeight: Int
    let depthWidth: Int
    let depthHeight: Int
}

enum ARCaptureControllerError: Error, LocalizedError {
    case sceneDepthUnsupported

    var errorDescription: String? {
        switch self {
        case .sceneDepthUnsupported:
            "This device does not support ARKit scene depth. A physical LiDAR-capable iPhone is required."
        }
    }
}

/// The sole ARSessionDelegate. Its private serial queue owns every mutable capture decision.
final class ARCaptureController: NSObject, ARSessionDelegate, @unchecked Sendable {
    let session = ARSession()

    var onCapturedBuffers: (@Sendable (CapturedBuffers) -> Void)?
    var onDiagnostics: (@Sendable (CaptureFrameDiagnostics) -> Void)?
    var onError: (@Sendable (String) -> Void)?
    var onCaptureFailed: (@Sendable (UUID, UUID?, String) -> Void)?
    var onSessionStopped: (@Sendable (String) -> Void)?

    private struct RequestedCapture {
        let captureID: UUID
        let requestID: UUID?
        let mode: CaptureMode
        let notBeforePhoneTimeSeconds: Double?
    }

    private let logger = Logger(subsystem: "dev.hmc.HumansCapture", category: "ARCapture")
    private let delegateQueue = DispatchQueue(label: "dev.hmc.capture.arkit", qos: .userInitiated)
    private var deviceID = "front-phone"
    private var sessionID = UUID()
    private var sequence: UInt64 = 0
    private var requestedCapture: RequestedCapture?
    private var liveModeEnabled = false
    private var lastLiveCaptureTime: TimeInterval = -.infinity
    private var negotiatedRGBSize: (width: Int, height: Int)?
    private var expectedRasterDimensions: ExpectedRasterDimensions?
    private var calibrationInvalid = false
    private var running = false

    // Ten frames per second is an initial ceiling for live mode; encoding/backpressure may lower it.
    private let minimumLiveFrameIntervalSeconds = 0.1

    override init() {
        super.init()
        session.delegate = self
        session.delegateQueue = delegateQueue
    }

    @discardableResult
    func start(deviceID: String) throws -> UUID {
        guard ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) else {
            throw ARCaptureControllerError.sceneDepthUnsupported
        }
        let newSessionID = UUID()
        delegateQueue.async { [self] in
            self.deviceID = deviceID
            self.sessionID = newSessionID
            self.sequence = 0
            self.requestedCapture = nil
            self.lastLiveCaptureTime = -.infinity
            self.negotiatedRGBSize = nil
            self.calibrationInvalid = false
            self.running = true

            let configuration = ARWorldTrackingConfiguration()
            configuration.frameSemantics = [.sceneDepth]
            self.session.run(configuration, options: [.resetTracking, .removeExistingAnchors])
            self.logger.info("Started AR session \(newSessionID.uuidString, privacy: .public)")
        }
        return newSessionID
    }

    func stop() {
        delegateQueue.async { [self] in
            session.pause()
            requestedCapture = nil
            liveModeEnabled = false
            running = false
        }
    }

    func setExpectedRasterDimensions(_ dimensions: ExpectedRasterDimensions?) {
        delegateQueue.async { [self] in expectedRasterDimensions = dimensions }
    }

    func setLiveModeEnabled(_ enabled: Bool) {
        delegateQueue.async { [self] in liveModeEnabled = enabled }
    }

    @discardableResult
    func requestSnapshot(
        captureID: UUID,
        requestID: UUID? = nil,
        notBeforePhoneTimeSeconds: Double? = nil
    ) -> Bool {
        delegateQueue.sync {
            guard running, !calibrationInvalid, requestedCapture == nil else { return false }
            requestedCapture = RequestedCapture(
                captureID: captureID,
                requestID: requestID,
                mode: .snapshot,
                notBeforePhoneTimeSeconds: notBeforePhoneTimeSeconds
            )
            return true
        }
    }

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        let trackingState = Self.mapTrackingState(frame.camera.trackingState)
        let rgbWidth = CVPixelBufferGetWidth(frame.capturedImage)
        let rgbHeight = CVPixelBufferGetHeight(frame.capturedImage)
        let depthData = frame.sceneDepth
        let depthWidth = depthData.map { CVPixelBufferGetWidth($0.depthMap) } ?? 0
        let depthHeight = depthData.map { CVPixelBufferGetHeight($0.depthMap) } ?? 0

        let changedResolution: Bool
        if let negotiatedRGBSize {
            changedResolution = negotiatedRGBSize.width != rgbWidth || negotiatedRGBSize.height != rgbHeight
        } else {
            negotiatedRGBSize = (rgbWidth, rgbHeight)
            changedResolution = false
        }
        let differsFromExpected = expectedRasterDimensions.map {
            $0.rgbWidth != rgbWidth || $0.rgbHeight != rgbHeight
                || (depthWidth > 0 && ($0.depthWidth != depthWidth || $0.depthHeight != depthHeight))
        } ?? false
        calibrationInvalid = changedResolution || differsFromExpected
        onDiagnostics?(CaptureFrameDiagnostics(
            rgbWidth: rgbWidth,
            rgbHeight: rgbHeight,
            depthWidth: depthWidth,
            depthHeight: depthHeight,
            sequence: sequence,
            trackingState: trackingState,
            calibrationInvalid: calibrationInvalid
        ))

        guard trackingState == .normal,
              !calibrationInvalid,
              let depthData,
              let confidenceMap = depthData.confidenceMap else { return }

        let request: RequestedCapture?
        if let requestedCapture,
           requestedCapture.notBeforePhoneTimeSeconds.map({ frame.timestamp >= $0 }) ?? true {
            request = requestedCapture
            self.requestedCapture = nil
        } else if liveModeEnabled,
                  frame.timestamp - lastLiveCaptureTime >= minimumLiveFrameIntervalSeconds {
            request = RequestedCapture(
                captureID: UUID(),
                requestID: nil,
                mode: .live,
                notBeforePhoneTimeSeconds: nil
            )
            lastLiveCaptureTime = frame.timestamp
        } else {
            request = nil
        }
        guard let request else { return }

        do {
            // These are row copies only; JPEG compression happens later on RGBDFrameAssembler.
            let rgbCopy = try SendablePixelBufferCopy(copying: frame.capturedImage)
            let encodedDepth = try DepthEncoder.copy(
                depthPixelBuffer: depthData.depthMap,
                confidencePixelBuffer: confidenceMap
            )
            let currentSequence = sequence
            sequence += 1
            onCapturedBuffers?(CapturedBuffers(
                deviceID: deviceID,
                sessionID: sessionID,
                captureID: request.captureID,
                requestID: request.requestID,
                mode: request.mode,
                sequence: currentSequence,
                captureTimestampSeconds: frame.timestamp,
                orientation: .landscapeRight,
                trackingState: trackingState,
                rgbWidth: rgbWidth,
                rgbHeight: rgbHeight,
                rgbIntrinsics: Self.doubleMatrix(frame.camera.intrinsics),
                rgbPixelBuffer: rgbCopy,
                depthWidth: encodedDepth.width,
                depthHeight: encodedDepth.height,
                depthMetersLE: encodedDepth.depthMetersLE,
                confidence: encodedDepth.confidence,
                arkitWorldFromCamera: Self.doubleMatrix(frame.camera.transform)
            ))
        } catch {
            logger.error("Frame copy failed: \(error.localizedDescription, privacy: .public)")
            onCaptureFailed?(request.captureID, request.requestID, error.localizedDescription)
            onError?(error.localizedDescription)
        }
    }

    func session(_ session: ARSession, didFailWithError error: Error) {
        running = false
        requestedCapture = nil
        onSessionStopped?("AR session failed: \(error.localizedDescription)")
    }

    func sessionWasInterrupted(_ session: ARSession) {
        running = false
        requestedCapture = nil
        onSessionStopped?("AR session was interrupted; restart to create a fresh session.")
    }

    func sessionInterruptionEnded(_ session: ARSession) {
        // Do not silently resume: clock and calibration assumptions require a new session ID.
        onSessionStopped?("AR interruption ended; start a new capture session.")
    }

    private static func mapTrackingState(_ state: ARCamera.TrackingState) -> TrackingState {
        switch state {
        case .normal: .normal
        case .notAvailable: .notAvailable
        case .limited(let reason):
            switch reason {
            case .initializing: .limitedInitializing
            case .excessiveMotion: .limitedExcessiveMotion
            case .insufficientFeatures: .limitedInsufficientFeatures
            case .relocalizing: .limitedRelocalizing
            @unknown default: .notAvailable
            }
        }
    }

    private static func doubleMatrix(_ matrix: simd_float3x3) -> simd_double3x3 {
        simd_double3x3(columns: (
            SIMD3<Double>(matrix.columns.0),
            SIMD3<Double>(matrix.columns.1),
            SIMD3<Double>(matrix.columns.2)
        ))
    }

    private static func doubleMatrix(_ matrix: simd_float4x4) -> simd_double4x4 {
        simd_double4x4(columns: (
            SIMD4<Double>(matrix.columns.0),
            SIMD4<Double>(matrix.columns.1),
            SIMD4<Double>(matrix.columns.2),
            SIMD4<Double>(matrix.columns.3)
        ))
    }
}
