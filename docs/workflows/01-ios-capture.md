# Workflow 1 — iPhone RGBD capture

## Outcome and boundary

This lane produces reproducible `HMC1/RGBD_FRAME` packets from each physical iPhone. It owns ARKit acquisition, safe buffer copying, JPEG/depth encoding, session/sequence metadata, clock replies, and WebSocket transport. It does not decide calibration, remove the background, run pose models, or build the canonical point cloud.

The authoritative DTOs are in [../contracts.md](../contracts.md). Any Swift model shown here is an implementation mapping of those DTOs.

## Technology choice

- Swift 6 and SwiftUI.
- ARKit `ARWorldTrackingConfiguration` with `.sceneDepth`.
- Core Image or VideoToolbox/ImageIO for correct bi-planar YCbCr-to-JPEG conversion.
- `URLSessionWebSocketTask` for text and binary WebSocket messages.
- `os.Logger` locally; Sentry Apple SDK is optional until the Python/Java prize path is complete.

Apple documents that scene depth is populated alongside `capturedImage` on supported LiDAR devices and must be gated with `supportsFrameSemantics`. See [scene depth](https://developer.apple.com/documentation/arkit/arconfiguration/framesemantics-swift.struct/scenedepth) and Apple's [point-cloud sample](https://developer.apple.com/documentation/arkit/displaying-a-point-cloud-using-scene-depth).

## Project setup

1. On a Mac with current Xcode, create an iOS SwiftUI app named `HumansCapture` under `ios-capture/`.
2. Set an explicit deployment target supported by both demo phones. Do not select a target solely because the simulator builds; scene depth requires physical LiDAR hardware.
3. Add `NSCameraUsageDescription` and `NSLocalNetworkUsageDescription` to the target's Info settings. If Bonjour discovery is later added, also declare the service type; v1 uses an entered backend URL and does not need discovery.
4. Lock the first build to landscape-right in target orientation settings. The preview may rotate only if the encoded raster and metadata rules remain unchanged.
5. Add unit-test and UI-test targets. Unit tests cover framing/DTOs without ARKit; device tests cover real buffers.
6. Store `device_id` and backend URL in app settings. The two installations must use stable, different IDs such as `front-phone` and `side-phone`.

Suggested source layout:

```text
HumansCapture/
├── App/HumansCaptureApp.swift
├── Capture/ARCaptureController.swift
├── Capture/CapturedBuffers.swift
├── Encoding/RGBEncoder.swift
├── Encoding/DepthEncoder.swift
├── Protocol/HMCEnvelope.swift
├── Protocol/CaptureDTOs.swift
├── Transport/CaptureSocket.swift
├── Transport/ClockResponder.swift
├── State/CaptureStore.swift
└── UI/CaptureView.swift
```

## Core Swift data structures

Keep ARKit/Core Video objects inside the capture callback. The object crossing to an encoder owns copied bytes:

```swift
struct CapturedBuffers: Sendable {
    let deviceID: String
    let sessionID: UUID
    let captureID: UUID
    let sequence: UInt64
    let captureTimestampSeconds: Double
    let orientation: ImageOrientation
    let trackingState: TrackingState
    let rgbWidth: Int
    let rgbHeight: Int
    let rgbIntrinsics: simd_double3x3
    let rgbPixelBuffer: SendablePixelBufferCopy
    let depthWidth: Int
    let depthHeight: Int
    let depthMetersLE: Data
    let confidence: Data
    let arkitWorldFromCamera: simd_double4x4
}
```

`SendablePixelBufferCopy` may own a new `CVPixelBuffer` from a pool or already-converted image bytes. It must not be a borrowed `ARFrame.capturedImage` that outlives the delegate callback. `RGBDFrameHeader`, `BufferDescriptor`, and every control message are `Codable` structs with explicit `CodingKeys` for the contract's snake-case field names.

`HMCEnvelope.encode` accepts a validated header and ordered buffers, uses `Data.reserveCapacity`, appends fixed-width integers in little-endian order, then header UTF-8 and buffers. It checks each count fits `UInt32` and the v1 limits before allocation.

## Capture session lifecycle

`ARCaptureController` is the only `ARSessionDelegate`.

1. On start, create a new `session_id` and reset sequence to zero.
2. Check `ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)`. Show a hard unsupported-device state if false.
3. Configure `frameSemantics = [.sceneDepth]`. Do not use smoothed depth until it wins an explicit moving-boundary comparison.
4. Run `ARSession`. Record the negotiated `camera.imageResolution`; a change invalidates the current backend calibration.
5. For each `didUpdate frame`, update the UI diagnostics but only enqueue an RGBD copy when capture/live mode requests it.
6. Require `frame.sceneDepth`, a normal or explicitly allowed limited tracking state, and the locked orientation.
7. Copy data quickly, then return from the delegate. JPEG compression and WebSocket sends happen on a dedicated actor/task.
8. On backgrounding, stop the session/socket. On foregrounding, create a new session ID; never resume a session whose monotonic-clock/calibration assumptions may have changed.

## Copying and encoding the frame

### RGB

`capturedImage` is normally a bi-planar full-range YCbCr pixel buffer, not packed RGB. Use a supported conversion path (for example `CIImage(cvPixelBuffer:)` rendered through a reused `CIContext`) and JPEG-encode the unmirrored, uncropped landscape raster. Do not manually index it as BGRA.

- Reuse `CIContext`, color space, and buffer pools.
- Start at JPEG quality 0.85 and record encoded bytes/duration; tune using actual hand detail and traces.
- Strip or ignore EXIF orientation. Pixel `(u,v)` in the transmitted JPEG must be the same raster convention used by the transmitted intrinsics.
- If resizing RGB, apply `sx = newWidth/oldWidth`, `sy = newHeight/oldHeight` to `fx,cx` and `fy,cy`, and record the new dimensions. No cropping in v1.

### Depth and confidence

Lock each pixel buffer with `.readOnly`, read width/height/bytes-per-row, and copy row by row. Never assume `bytesPerRow == width * elementSize`.

- Depth format must be `kCVPixelFormatType_DepthFloat32` or explicitly converted to float32 meters.
- Serialize each finite positive depth as IEEE-754 float32 LE. Serialize invalid/non-positive/non-finite samples as `0.0`; confidence/masking prevents their use.
- Copy confidence as one byte per pixel and allow only ARKit levels 0, 1, and 2.
- Unlock buffers in `defer` paths.
- The depth and confidence dimensions must match.

### Intrinsics and transforms

ARKit exposes the camera intrinsics in a simd matrix whose memory layout must not be blindly flattened. Build the contract's mathematical rows explicitly: `[fx, skew, cx, 0, fy, cy, 0, 0, 1]` after any resize. Do the same explicit element access for the row-major 4×4 diagnostic pose. A unit test with distinct values in every cell must catch accidental transpose.

## Transport actor and backpressure

`CaptureSocket` is an actor owning one `URLSessionWebSocketTask`, receive loop, reconnect state, and a single pending binary send.

- Connect to `ws://<backend>:8000/ws/capture` on the trusted demo LAN; use `wss://` outside that environment.
- Send `client_hello`; wait for `server_hello` before sending binary data.
- Maintain one receive loop for `clock_ping`, `capture_request`, `ack`, and `error`.
- When a capture is ready and no send is active, send it. If one is active, keep only the newest unsent live frame. Snapshot requests are individually acknowledged and not silently replaced.
- A successful WebSocket send does not mean backend acceptance. Keep snapshot status pending until its backend `ack`/error is received.
- Reconnect with exponential delays capped at five seconds, but create a new session on a full app/AR session restart.

Clock pongs record receive/send timestamps using the same monotonic domain as `ARFrame.timestamp` (for example `ProcessInfo.processInfo.systemUptime`). Do not use `Date()` in that DTO.

## UI needed for the demo

The main screen exposes:

- Device ID and backend WebSocket URL.
- Connect/disconnect and start/stop AR session.
- Capture button and snapshot status.
- RGB preview and a false-color depth preview.
- RGB/depth dimensions, sequence, tracking state, valid-depth fraction, queue/send state, and last backend error.
- A prominent calibration-invalid warning if raster dimensions/orientation differ from backend expectations.

## Saved fixture

For every device build used in the demo, save at least one raw `.hmc` envelope exactly as sent. Next to it save a human-readable decoded header and SHA-256. The fixture must contain a measured stationary target and must not contain secrets or sensitive background content that cannot be committed.

The backend recorder is the normal persistence point; a debug Share Sheet export on the phone is useful for transport-independent diagnosis.

## Tests and completion gate

Unit tests:

- Exact first 16 envelope bytes and little-endian lengths.
- Matrix flattening is not transposed.
- Row-strided depth copy produces the expected tightly packed output.
- DTO rejects wrong field counts, orientation values, and oversized buffers.
- Bounded queue replaces an older live frame but preserves an explicit snapshot request.

Physical-device acceptance:

- Backend decodes a real packet from each phone.
- JPEG color/orientation and depth edges align when overlaid.
- A measured target's median depth is plausible in meters.
- RGB intrinsics match the transmitted raster.
- Valid-depth fraction and tracking state appear in UI/logs.
- Repeated capture for ten minutes does not grow memory or pending sends.
- Disconnect/reconnect never reuses the prior session's queued frames.

The lane is not complete if it only works with a simulator or a synthetic depth fixture.
