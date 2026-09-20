# HumanCraft Sentry observability

HumanCraft instruments three failure modes: pipeline latency, silently bad scans, and cloud/hitbox identity or freshness drift. Telemetry is optional, asynchronous, sampled, and contains only scalar metadata—never RGB images, depth buffers, point clouds, or full sensor payloads. A missing or unreachable Sentry service does not stop capture, processing, or rendering.

## What is instrumented

### Latency

- iOS transaction `ios.capture_frame` contains `hmc.queue_wait`, `hmc.point_cloud_generation`, `hmc.serialize`, and `hmc.websocket_forward`. The point-cloud-generation label measures preparation of valid LiDAR depth samples; actual 3D unprojection happens in Python.
- Python continues the trace from each binary WebSocket message. `hmc.calibration_fusion` contains detection, calibrated reconstruction, merge/downsample, fitting, and assembly spans. The pairing-queue wait is derived only from Python's local monotonic clock.
- Fabric measures the decoder executor's queue wait, `client.character_decode`, and downstream `hmc.prepare_cloud` work before the render/server install boundary.

Spans use local clocks. Phone, backend, and game timestamps are never subtracted to claim network latency. Useful attributes are `frame_id`, `fusion_id`/source frame IDs, `point_count`, and `payload_size`; routine frame logs are aggregated every 30 seconds while detailed traces are sampled (default 20%).

In Sentry, open **Explore → Traces** and search transaction names `ios.capture_frame`, `character.snapshot`, or `client.install_snapshot`. Open one trace and compare the `hmc.*` spans, then group slow spans by point count or payload size.

### Scan quality

Python reuses each detector's existing person-mask heuristic and tracked landmarks:

- `background_proportion` is the fraction of valid depth samples outside that mask. It is an estimate, not ground-truth segmentation.
- hand, foot, and torso coverage are the proportions of reliable, in-view landmarks with masked depth support nearby.

A missing/unreliable/out-of-view landmark makes that region unavailable, not failed. After three poor frames for one camera/calibration, one rate-limited `scan_quality_poor` warning includes the camera, frame/fusion ID, calibration version, and all four measurements. `scan_quality_recovered` is recorded once when the signal recovers.

In Sentry, search **Logs/Issues** for `scan_quality_poor`, then group by `camera_id` or `calibration_version`.

### Identity and freshness

RGBD schema v2 assigns a UUID `source_frame_id` to every phone frame and carries Sentry `sentry-trace`/`baggage` in every WebSocket message. A fused result preserves both source IDs and adds `fusion_id`. Fabric passes the fusion ID through its client→logical-server install payload; the ack must match before the cloud is activated. Repeated normal-path mismatches emit `frame_identity_mismatch` with source IDs, and recovery is recorded.

For live mode, the mod emits `fresh_results_stale` when no fresh result arrives for the configured live TTL, then clears the stale cloud/hitboxes as before. A later result emits `fresh_results_recovered`.

In Sentry Logs, search `consistency kind=frame_identity_mismatch` or `consistency kind=fresh_results_stale`, then use `fusion_id`/`source_frame_ids` to follow the result upstream.

## Configuration

Backend:

```bash
export HMC_SENTRY_DSN='https://public-key@your-sentry-host/project-id'
export HMC_ENVIRONMENT='local-observability'
export HMC_TRACES_SAMPLE_RATE='0.2'
export HMC_SENTRY_SEND_DEFAULT_PII='false'
```

Set `HMC_SENTRY_SEND_DEFAULT_PII=true` only when the project privacy policy permits
request headers, client IP addresses, and other default request identity data to be sent.

Fabric's checked-in `minecraft-mod/sentry.properties` configures the `java-minecraft`
project, logs, tracing, and profiling. `./gradlew runClient` resolves and attaches the
Sentry OpenTelemetry agent automatically. For a packaged Minecraft launch, add these JVM
arguments in the launcher:

```text
-javaagent:/absolute/path/to/sentry-opentelemetry-agent-8.57.0.jar
-Dsentry.properties.file=/absolute/path/to/sentry.properties
```

The existing `HUMANCRAFT_SENTRY_DSN`, `HUMANCRAFT_SENTRY_ENVIRONMENT`, and
`HUMANCRAFT_SENTRY_TRACES_SAMPLE_RATE` variables remain supported and take precedence
when a DSN is supplied. For iOS, add the `HMC_SENTRY_*` variables to the Xcode scheme's
Run environment.

To upload Java source context, create an organization token and keep it only in the
environment:

```bash
cd minecraft-mod
export SENTRY_AUTH_TOKEN='...'
./gradlew sentryUploadSourceBundleJava
```

The token is never stored in this repository. The checked-in Java DSN is a client ingest
key, not the source-upload credential. The supplied Java settings enable default PII and
100% tracing/profiling; review and reduce those settings before a broad production rollout.

The pinned SDK/API choices are Sentry Cocoa 8.56 (`startTransaction`, child spans, `toTraceHeader`, `baggageHttpHeader`), `sentry-sdk` 2.x (`continue_trace`, `start_transaction`, span headers), and Sentry Java 8.57 (`continueTrace`, structured Logs, metrics, async profiler, and OpenTelemetry agent). See the official [Cocoa SDK](https://github.com/getsentry/sentry-cocoa/tree/8.56.0), [Python tracing API](https://github.com/getsentry/sentry-python/blob/master/sentry_sdk/tracing.py), and [Java SDK](https://github.com/getsentry/sentry-java/tree/8.57.0).

## Controlled demonstrations

These hooks are opt-in and separate from normal operation.

1. Known processing delay:

   ```bash
   cd backend
   HMC_SENTRY_DSN="$HMC_SENTRY_DSN" uv run python scripts/observability_demo.py delay --delay-ms 750
   ```

   Expected fixture evidence: one frame publishes and reports the injected delay. Expected Sentry evidence: transaction `observability.demo.delay`, with `hmc.calibration_fusion` taking at least roughly 750 ms. The hook is controlled by `HMC_OBSERVABILITY_DEMO_DELAY_MS`/the demo argument and defaults to zero.

2. Background contamination plus missing hand support:

   ```bash
   cd backend
   HMC_SENTRY_DSN="$HMC_SENTRY_DSN" uv run python scripts/observability_demo.py quality
   uv run pytest -q tests/test_scan_quality.py
   ```

   Expected fixture evidence: exactly one warning on frame 3 with `excess_background,missing_hands`, followed by a tested recovery path. Expected Sentry evidence: one `scan_quality_poor` warning with background `0.82`, hand coverage `0.0`, camera, calibration, frame, and fusion IDs.

3. Inconsistent identity (or stopped updates):

   ```bash
   cd minecraft-mod
   ./gradlew test --tests dev.humancraft.telemetry.FrameConsistencyMonitorTest

   # Live Sentry demo, with backend running:
   HUMANCRAFT_OBSERVABILITY_FAULT=mismatch_ids \
   HUMANCRAFT_SENTRY_DSN="$HUMANCRAFT_SENTRY_DSN" \
   ./gradlew runClient
   ```

   Request/install a frame in the client. Expected Sentry evidence: `consistency kind=frame_identity_mismatch` with expected/actual fusion IDs and both source frame IDs; the mismatched cloud is not activated. To demonstrate freshness instead, use `HUMANCRAFT_OBSERVABILITY_FAULT=stale_updates` and live mode; updates after the first are deliberately ignored until the configured TTL produces `fresh_results_stale`.

To send the one-time SDK installation exception and three verification metrics during a
development launch, opt in explicitly:

```bash
cd minecraft-mod
HUMANCRAFT_SENTRY_VERIFY=true ./gradlew runClient
```

Search Sentry for `HumanCraft Sentry installation test` and the
`humancraft.sentry_verify`, `humancraft.queue_size`, and
`humancraft.response_time` metrics. Without this environment variable, the intentional
exception and test metrics are never emitted.

## Verification status

Fixture verification performed locally: backend tests and both Python demos pass; all 62
Fabric tests pass with Sentry Java 8.57, the source-context plugin, async profiler, metrics
API, and agent dependency resolved; the generic iOS device target builds with Sentry
Cocoa. DSNs are now configured, but the live Sentry UI was not opened in this workspace,
so no claim is made that an event, metric, profile, or source bundle was observed there.
Run the opt-in commands above to complete that gate.
