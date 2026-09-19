# HumansCapture (Workflow 1)

Native Swift/ARKit capture app for LiDAR-equipped iPhones. It retains RGB, scene depth, confidence, camera intrinsics/pose, and one monotonic timestamp from the same `ARFrame`; emits validated `HMC1/RGBD_FRAME` packets; and records explicit snapshots as local replay fixtures.

## Pinned baseline

- Xcode 16.4, Apple Swift 6.1.2 (Swift language mode 6)
- iOS 17.0+
- Landscape-right, unmirrored and uncropped sensor rasters

## Configure

1. Open `HumansCapture.xcodeproj`, select the `HumansCapture` scheme, choose a personal development team, and select a physical LiDAR-capable iPhone.
2. Keep the phone in landscape-right. Enter a stable unique device ID (`front-phone` or `side-phone`) and `ws://<laptop>:8000/ws/capture` on the trusted demo LAN.
3. Start AR capture, connect, then request a snapshot or enable slow live recapture.

Live recapture is coordinated by the backend. Pressing the live button requests one shared session; connected phones show `starting`, `running`, `paused`, or `stopped`. The backend starts after at least one calibrated phone is clock-ready and issues 3 FPS capture requests to every ready phone. A second phone is incorporated automatically once its clock is ready. Do not enable independent local capture timers.

The app requires camera and local-network permission. Scene depth is checked before session start and the UI reports an explicit unsupported-device error.

## Data and privacy

Explicit snapshots are saved on-device under Application Support in `HumansCapture/Fixtures` as the exact `.hmc` envelope, a pretty-printed decoded header, and a SHA-256 file. Export the last envelope through the Share Sheet. These files contain a real RGB image and depth data; inspect the scene before committing a fixture.

Capture diagnostics remain local through `os.Logger`. Live-frame logs are aggregated every 30 seconds and repeated warnings are rate-limited. Raw RGB, depth, confidence, and packet bytes are never included in logs.

## Build and tests

```sh
xcodebuild \
  -project HumansCapture.xcodeproj \
  -scheme HumansCapture \
  -destination 'generic/platform=iOS' \
  -derivedDataPath .build/DerivedData \
  CODE_SIGNING_ALLOWED=NO build
```

Run unit tests from Xcode on a connected device. Tests cover envelope magic/endianness/lengths, descriptor mismatch, row-strided depth/confidence copies, matrix layout, and backpressure policy. To validate an exported fixture independently:

```sh
python3 scripts/inspect_hmc.py /path/to/exported-packet.hmc
```

## Validation status

The generic iOS device target and unit-test bundle compile locally. Physical-device gates remain unverified until the team runs this on both LiDAR iPhones: real packet decode, RGB/depth overlay, measured target depth, ten-minute memory/queue stability, and reconnect/session isolation. Do not mark those gates complete from a simulator or synthetic fixture.
