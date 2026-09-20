# HumansCapture (Workflow 1)

Native Swift/ARKit capture app for LiDAR-equipped iPhones. It retains RGB, scene depth, confidence, camera intrinsics/pose, and one monotonic timestamp from the same `ARFrame`; emits validated `HMC1/RGBD_FRAME` packets; and records explicit snapshots as local replay fixtures.

## Pinned baseline

- Xcode 16.4, Apple Swift 6.1.2 (Swift language mode 6)
- iOS 17.0+
- Sentry Cocoa 9.29.0+
- Landscape-right, unmirrored and uncropped sensor rasters

## Sentry set-up

Macos
```
brew install getsentry/tools/sentry-wizard && sentry-wizard -i ios --saas --org jonathan-zhu --project apple-ios
```

Linux
```
downloadUrl="https://github.com/getsentry/sentry-wizard/releases/download/v4.0.1/sentry-wizard-linux-x64"
curl -L $downloadUrl -o sentry-wizard
chmod +x sentry-wizard
./sentry-wizard -i ios --saas --org jonathan-zhu --project apple-ios
```

## Configure

1. Open `HumansCapture.xcodeproj`, select the `HumansCapture` scheme, choose a personal development team, and select a physical LiDAR-capable iPhone.
2. Keep the phone in landscape-right. Enter a stable unique device ID (`front-phone` or `side-phone`) and `ws://<laptop>:8000/ws/capture` on the trusted demo LAN.
3. Start AR capture, connect, then request a snapshot or enable slow live recapture.

The app requires camera and local-network permission. Scene depth is checked before session start and the UI reports an explicit unsupported-device error.

### Sentry

Sentry is configured once, in `CaptureEventLogger`. The DSN, environment and
trace sample rate are build settings in `Config/Base.xcconfig`
(`HMC_SENTRY_DSN`, `HMC_SENTRY_ENVIRONMENT`, `HMC_SENTRY_TRACES_SAMPLE_RATE`)
and are baked into `Info.plist`, so a phone launched from the home screen
reports too. An Xcode scheme environment variable of the same name overrides
the baked value for a developer run; `HMC_SENTRY_DEBUG=true` prints SDK
diagnostics. Leave `HMC_SENTRY_DSN` empty in the xcconfig to disable reporting.

With a DSN present, Sentry receives structured capture logs, sampled
`ios.capture_frame` traces and their `hmc.*` child spans, plus capture metrics.
Snapshot metrics are emitted immediately and live-mode measurements are aggregated
every 30 seconds. Search Metrics for `humancraft.capture.*` and Traces for
`ios.capture_frame`.

## Data and privacy

Explicit snapshots are saved on-device under Application Support in `HumansCapture/Fixtures` as the exact `.hmc` envelope, a pretty-printed decoded header, and a SHA-256 file. Export the last envelope through the Share Sheet. These files contain a real RGB image and depth data; inspect the scene before committing a fixture.

Capture diagnostics always remain available locally through `os.Logger` and are also
sent through Sentry structured Logs when configured. Live-frame logs and metrics are
aggregated every 30 seconds and repeated warnings are rate-limited. Raw RGB, depth,
confidence, and packet bytes are never included in telemetry.

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
