import Foundation
import simd

/// A frame boundary value: every byte here is owned and may safely leave ARSession's callback.
public struct CapturedBuffers: Sendable {
    public let deviceID: String
    public let sessionID: UUID
    public let captureID: UUID
    public let requestID: UUID?
    public let mode: CaptureMode
    public let sequence: UInt64
    public let captureTimestampSeconds: Double
    public let orientation: ImageOrientation
    public let trackingState: TrackingState
    public let rgbWidth: Int
    public let rgbHeight: Int
    public let rgbIntrinsics: simd_double3x3
    public let rgbPixelBuffer: SendablePixelBufferCopy
    public let depthWidth: Int
    public let depthHeight: Int
    public let depthMetersLE: Data
    public let confidence: Data
    public let arkitWorldFromCamera: simd_double4x4

    public init(
        deviceID: String,
        sessionID: UUID,
        captureID: UUID,
        requestID: UUID?,
        mode: CaptureMode,
        sequence: UInt64,
        captureTimestampSeconds: Double,
        orientation: ImageOrientation,
        trackingState: TrackingState,
        rgbWidth: Int,
        rgbHeight: Int,
        rgbIntrinsics: simd_double3x3,
        rgbPixelBuffer: SendablePixelBufferCopy,
        depthWidth: Int,
        depthHeight: Int,
        depthMetersLE: Data,
        confidence: Data,
        arkitWorldFromCamera: simd_double4x4
    ) {
        self.deviceID = deviceID
        self.sessionID = sessionID
        self.captureID = captureID
        self.requestID = requestID
        self.mode = mode
        self.sequence = sequence
        self.captureTimestampSeconds = captureTimestampSeconds
        self.orientation = orientation
        self.trackingState = trackingState
        self.rgbWidth = rgbWidth
        self.rgbHeight = rgbHeight
        self.rgbIntrinsics = rgbIntrinsics
        self.rgbPixelBuffer = rgbPixelBuffer
        self.depthWidth = depthWidth
        self.depthHeight = depthHeight
        self.depthMetersLE = depthMetersLE
        self.confidence = confidence
        self.arkitWorldFromCamera = arkitWorldFromCamera
    }
}
