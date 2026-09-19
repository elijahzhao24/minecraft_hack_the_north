# HumansCapture (Workflow 1)

Native Swift/ARKit capture app for LiDAR-equipped iPhones. It retains RGB, scene depth, confidence, camera intrinsics/pose, and one monotonic timestamp from the same `ARFrame`; emits validated `HMC1/RGBD_FRAME` packets; and records explicit snapshots as local replay fixtures.

## Pinned baseline

- Xcode 16.4, Apple Swift 6.1.2 (Swift language mode 6)
- iOS 17.0+
- Sentry Cocoa 9.24.0 via Swift Package Manager, exact pin
- Landscape-right, unmirrored and uncropped sensor rasters

Sentry 9.24.0 is intentional for Xcode 16.4. Sentry 9.29.0 was checked on 2026-09-19, but its Swift 6.1 package manifest combines a stable package release with a revision-based KSCrash dependency that this toolchain refuses to resolve. Re-evaluate the pin when upgrading Xcode; do not change it without rebuilding the device target and repeating the Sentry validation.

## Configure

1. Copy `Config/Secrets.xcconfig.example` to `Config/Secrets.xcconfig` and set the public Sentry DSN. The secrets file is ignored by Git. Leave the DSN blank to run entirely without Sentry.
2. Open `HumansCapture.xcodeproj`, select the `HumansCapture` scheme, choose a personal development team, and select a physical LiDAR-capable iPhone.
3. Keep the phone in landscape-right. Enter a stable unique device ID (`front-phone` or `side-phone`) and `ws://<laptop>:8000/ws/capture` on the trusted demo LAN.
4. Start AR capture, connect, then request a snapshot or enable slow live recapture.

The app requires camera and local-network permission. Scene depth is checked before session start and the UI reports an explicit unsupported-device error.

## Data and privacy

Explicit snapshots are saved on-device under Application Support in `HumansCapture/Fixtures` as the exact `.hmc` envelope, a pretty-printed decoded header, and a SHA-256 file. Export the last envelope through the Share Sheet. These files contain a real RGB image and depth data; inspect the scene before committing a fixture.

Sentry receives scalar metadata only. The integration never attaches RGB, depth, confidence, the DSN, or packet bytes. Live-frame logs are aggregated every 30 seconds; repeated warnings are rate-limited.

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

## Sentry validation

With a DSN configured:

1. Capture one explicit snapshot and locate the `rgbd.snapshot` transaction with its `capture.*` spans.
2. Confirm `capture.frame.sent`, fixture, reconnect, and quality events appear in Sentry Logs—not only as breadcrumbs.
3. Tap **Emit safe Sentry test error** and confirm the deliberate non-fatal issue arrives.
4. Clear the DSN or block network access and repeat capture. The local fixture and socket pipeline must continue; Sentry failure must not block capture.

`sentry-trace` and `baggage` values are embedded in sampled RGBD headers. The Python receiver must extract them to continue a distributed trace; until it does, `capture_id`/`sequence` provide correlation only.

## Validation status

The generic iOS device target and unit-test bundle compile locally. Physical-device gates remain unverified until the team runs this on both LiDAR iPhones: real packet decode, RGB/depth overlay, measured target depth, ten-minute memory/queue stability, reconnect/session isolation, and evidence in the configured Sentry project. Do not mark those gates complete from a simulator or synthetic fixture.
