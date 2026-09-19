import CoreVideo
import Foundation
import simd

struct CaptureIntent: Sendable {
    let captureID: UUID
    let requestID: UUID?
    let mode: CaptureMode
    let notBeforePhoneTimeSeconds: Double?
}

/// Owns strong references to the ARFrame pixel buffers until the bounded encoder
/// pipeline has copied/encoded them. The pipeline never retains more than its
/// configured snapshot capacity plus one replaceable live frame.
struct CapturedFrameSource: @unchecked Sendable {
    let sessionID: UUID
    let intent: CaptureIntent
    let sequence: UInt64
    let captureTimestampSeconds: Double
    let trackingState: CaptureTrackingState
    let rgbPixelBuffer: CVPixelBuffer
    let depthPixelBuffer: CVPixelBuffer
    let confidencePixelBuffer: CVPixelBuffer?
    let rgbIntrinsics: simd_float3x3
    let arkitWorldFromCamera: simd_float4x4
}

struct CaptureDiagnostics: Sendable {
    let rgbWidth: Int
    let rgbHeight: Int
    let depthWidth: Int
    let depthHeight: Int
    let trackingState: CaptureTrackingState
    let captureTimestampSeconds: Double
}

enum CaptureLifecycleEvent: Sendable {
    case started(sessionID: UUID)
    case stopped
    case unsupportedSceneDepth
    case missingDepth
    case trackingChanged(CaptureTrackingState)
    case frameReady(CapturedFrameSource)
    case diagnostic(CaptureDiagnostics)
    case depthPreview(DepthPreviewSource)
    case failed(String)
}

struct DepthPreviewSource: @unchecked Sendable {
    let pixelBuffer: CVPixelBuffer
}
