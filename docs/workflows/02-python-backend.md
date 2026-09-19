# Workflow 2 — Python backend, calibration, and reconstruction

## Outcome and boundary

The backend is one Python process that accepts phone frames, estimates clock offsets, records/replays inputs, pairs frames, orchestrates vision/reconstruction, assembles a single immutable `CharacterFrame`, and publishes it to Minecraft.

It owns the canonical stage frame and calibration. Workflow 3 is a Python module inside this process, not a second network service. Keeping it in-process avoids a redundant image serialization boundary; its typed call boundary is still documented and tested.

## Framework and runtime decision

- Python 3.12.
- `uv` for virtual environment, dependency resolution, and lock file.
- FastAPI/Starlette for `/health` and WebSockets.
- Uvicorn with **one worker**.
- Pydantic v2 plus `pydantic-settings` for untrusted DTO/config validation.
- NumPy and OpenCV for calibration/reconstruction.
- MediaPipe Tasks for workflow 3.
- `sentry-sdk` with tracing and structured logs.
- Pytest, AnyIO, and Hypothesis for tests.

FastAPI supports binary/text WebSockets and disconnection handling; its official [WebSocket guide](https://fastapi.tiangolo.com/advanced/websockets/) is the API baseline. MediaPipe's current [Python setup guide](https://developers.google.com/edge/mediapipe/solutions/setup_python) supports Python 3.9+, making 3.12 a deliberate project pin rather than the lowest allowed version.

## Bootstrap

When the `backend/` directory is created:

```bash
cd backend
uv init --package --python 3.12
uv add fastapi "uvicorn[standard]" pydantic pydantic-settings numpy opencv-contrib-python mediapipe sentry-sdk
uv add --dev pytest pytest-asyncio hypothesis httpx ruff mypy
uv lock
```

Pin actual resolved versions in `uv.lock` and commit it. Do not hand-copy version numbers from this document into a requirements file. Model `.task` artifacts are versioned independently: store the expected filename, source URL, license note, and SHA-256 in `backend/models/manifest.json`; verify checksums at startup.

Run locally from `backend/`:

```bash
uv run uvicorn hmc_backend.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Use `fastapi dev` only for local route iteration. Auto-reload restarts in-memory sessions and is not used during capture or the demo.

## Configuration

`Settings` reads `HMC_`-prefixed environment variables and a checked-in non-secret default file:

| Field | Default | Purpose |
|---|---:|---|
| `bind_host` | `0.0.0.0` | LAN access for phones/game |
| `port` | `8000` | HTTP/WebSocket port |
| `expected_device_ids` | `front-phone,side-phone` | exact capture identities |
| `max_rgbd_bytes` | `16777216` | allocation guard |
| `max_character_bytes` | `8388608` | publisher guard |
| `device_queue_size` | `2` | ingress backpressure |
| `subscriber_queue_size` | `1` | latest-frame publication |
| `pair_skew_limit_ms` | `50` | reject mismatched captures |
| `clock_uncertainty_limit_ms` | `20` | reject poorly normalized time |
| `voxel_size_m` | `0.015` | initial downsample cell |
| `max_points` | `50000` | renderer budget |
| `stage_min/max_*_m` | rig-specific | hard spatial crop |
| `calibration_path` | `data/calibration.json` | active calibration |
| `recording_root` | `data/recordings` | replayable inputs |
| `pose_model_path` | model manifest entry | required model |
| `hand_model_path` | model manifest entry | required model |
| `sentry_dsn` | unset | optional; absence must not break demo |

Secrets such as Sentry DSN stay in environment variables. `/health` never returns them.

## FastAPI lifecycle and routes

Use an async lifespan context. Startup performs settings validation, loads and validates calibration, verifies model hashes, creates long-lived MediaPipe task instances on the processor thread, starts pair/processing/publisher tasks, then marks readiness. Shutdown stops accepting frames, cancels tasks, closes models/sockets, and flushes Sentry with a short timeout.

### `GET /health`

Returns HTTP 200 only when the process can accept a complete capture; otherwise 503:

```json
{
  "status": "ready",
  "protocol_version": 1,
  "calibration": {"loaded": true, "calibration_id": "f766f462-d405-4c30-89f9-f626da70547b"},
  "models": {"pose": "ready", "hands": "ready"},
  "devices": {
    "front-phone": {"connected": true, "clock_ready": true, "queue_depth": 0},
    "side-phone": {"connected": true, "clock_ready": true, "queue_depth": 0}
  },
  "latest_frame_id": 42
}
```

### `WS /ws/capture`

One socket per phone. The handler:

1. Accepts with a 10-second hello deadline.
2. Validates `client_hello` and registers the device/session.
3. Runs one receive loop. Text goes to the control dispatcher; binary goes to `decode_rgbd_envelope`.
4. Validates sequence/capture identity and records the exact envelope before decoded arrays are enqueued.
5. Uses `put_latest`: if the two-item device queue is full, removes the oldest unprocessed live frame, logs the drop, then inserts the new frame. Explicit requested snapshots receive rejection instead of silent replacement.
6. On disconnect, unregisters only if the stored connection token still matches, preventing an old handler from removing a newer reconnect.

### `WS /ws/character`

The Minecraft client sends `character_hello`. A per-subscriber sender task reads an async queue of size one. When a new frame arrives, a pending older frame is removed. The text dispatcher also accepts `request_capture`: after checking both phones, clock readiness, and processor capacity, it forwards a shared capture ID to both phone sockets and acknowledges dispatch. The endpoint never performs serialization or blocking game I/O in the processing worker.

## Module interfaces

The pipeline dependencies are explicit protocols so fixtures can substitute for hardware/models:

```python
class ViewDetector(Protocol):
    def detect_view(self, frame: CapturedFrame) -> ViewDetection: ...

class ViewReconstructor(Protocol):
    def reconstruct_view(
        self,
        frame: CapturedFrame,
        detection: ViewDetection,
        calibration: CameraCalibration,
    ) -> ColoredPointCloud: ...

class CharacterFitter(Protocol):
    def fit_character(
        self,
        pair: PairedFrames,
        detections: Mapping[str, ViewDetection],
        cloud: ColoredPointCloud,
        calibration: RigCalibration,
    ) -> FittedCharacter: ...

class CharacterPublisher(Protocol):
    async def publish(self, frame: CharacterFrame) -> None: ...
```

`CharacterProcessor.process(pair)` runs all three synchronous CPU-heavy stages in the dedicated worker, then returns a complete result to the event loop. MediaPipe objects are created and used on that same worker. The async event loop never calls OpenCV/MediaPipe directly.

## Pairing algorithm

Maintain a deque per active device. For each new frame:

1. Require an active calibration matching its device ID, RGB/depth dimensions, and locked orientation.
2. Require clock uncertainty below the configured limit.
3. Convert its phone capture time using the current session's clock estimate.
4. Search the other device's deque for the smallest absolute normalized time difference with the same `capture_id` for explicit snapshots. Live mode may pair without a common capture ID only when enabled.
5. If skew is at most `pair_skew_limit_ms`, remove both frames and emit one `PairedFrames`; otherwise retain within a short window and eventually reject the older candidate.
6. Record both source IDs and the calculated skew. A consumed frame is never reused.

Held-pose capture reduces motion artifacts but does not waive the skew validation.

## Calibration workflow

Use a printed ChArUco board with measured square/marker sizes and a defined board-to-stage transform.

1. Capture multiple board views per fixed phone with the exact production raster configuration.
2. Detect ChArUco corners using `opencv-contrib-python` and the RGB intrinsics supplied by ARKit.
3. Estimate board-to-optical pose with `solvePnP`; compose/invert it into `T_stage_from_optical` according to the named frame convention.
4. Reject frames with negative target depth, excessive reprojection error, too few/spatially clustered corners, or inconsistent metric scale.
5. Robustly aggregate accepted transforms. Do not element-wise average rotation matrices; average rotations on SO(3) or choose/refine a robust joint pose.
6. Validate against held-out board views and a separate measured target, then write one immutable calibration JSON.

Calibration file contains:

```json
{
  "schema": "hmc.rig_calibration",
  "schema_version": 1,
  "calibration_id": "f766f462-d405-4c30-89f9-f626da70547b",
  "created_at_utc": "2026-09-19T16:00:00Z",
  "stage_definition": {"unit":"meter","x":"front_camera_image_right","y":"up","z":"toward_front_camera"},
  "board": {"dictionary":"DICT_5X5_100","squares_x":7,"squares_y":10,"square_length_m":0.04,"marker_length_m":0.03},
  "cameras": [],
  "validation": {"median_reprojection_error_px":1.4,"max_reprojection_error_px":2.8,"held_out_frames":12}
}
```

Each camera entry contains the `CameraCalibration` fields from the contract plus orientation and device/session/raster constraints. Moving a phone/tripod or changing orientation/resolution invalidates it and requires a new ID.

## Reconstruction details

For each view:

1. Convert the pose segmentation mask from RGB resolution to depth resolution with a documented sampling rule. Use soft confidence before thresholding when available; do not erode away fingers/toes by default.
2. Scale RGB intrinsics to the uncropped depth raster:
   `fx_d=fx_rgb*W_d/W_rgb`, `fy_d=fy_rgb*H_d/H_rgb`, `cx_d=cx_rgb*W_d/W_rgb`, `cy_d=cy_rgb*H_d/H_rgb`.
3. Select depth pixels inside the mask, configured stage/depth crop, and confidence threshold. Exclude zero/non-finite/non-positive depth.
4. Unproject vectorized optical coordinates: `X=(u-cx_d)*Z/fx_d`, `Y=(v-cy_d)*Z/fy_d`, `Z=depth`.
5. Transform homogeneous points through `T_stage_from_optical`.
6. Sample color from corresponding RGB coordinates using the same pixel-center convention. Output actual RGB plus alpha 255.
7. Merge views, voxel-downsample at 1–2 cm, and reject isolated spatial outliers. Preserve source-camera bitsets for diagnostics.
8. If more than `max_points` remain, use deterministic spatial/reservoir selection seeded by source frame IDs; never truncate by raster order.

Do not run unconstrained ICP between the two human clouds. Calibration determines their relative frame; a moving/partially observed body is a poor unrestricted ICP target.

## Assembly and publication

`FrameAssembler` verifies before assigning the next `frame_id`:

- Pair source IDs exactly match detection/fitting inputs.
- Calibration IDs match every camera and fitted object.
- Cloud arrays satisfy count/type/finiteness limits.
- Collider IDs are unique, OBB bases are orthonormal, sizes positive, and landmark names unique.
- Quality includes explicit warnings and counts.

It creates a frozen `CharacterFrame`, serializes it once, then atomically replaces `SnapshotStore.latest`. Publication is all-or-nothing. A serialization/send failure never mutates the latest accepted object.

## Recording and replay

Each capture directory is immutable:

```text
data/recordings/<capture_id>/
├── manifest.json
├── front-phone.hmc
├── side-phone.hmc
├── calibration.json
└── expected/                  # optional reviewed outputs/metrics
```

`manifest.json` stores SHA-256, byte counts, received UTC time, pair skew, app/backend release, and consent/sensitivity note. The replayer feeds envelopes through the same decoder and queues, not directly into reconstruction. This tests the actual boundary.

## Observability

Initialize Sentry only if `HMC_SENTRY_DSN` exists. Set release/environment, tracing sample rate, and logs enabled. Carry `session_id`, `capture_id`, `frame_id`, and `calibration_id` as scalar attributes.

One snapshot transaction includes spans for decode, pair wait, pose/hands/mask, unproject/merge, registration, collider fit, serialize, and publish. Log calibration decisions, dropped frames, pair rejection, invalid extremities, point counts, and frame mismatch. Never attach RGB/depth/point buffers or log per point.

## Tests and completion gate

- Unit: envelope bounds/overlap/finiteness, clock formula, pairing edge cases, transform direction, intrinsics scaling, unprojection, voxel determinism, assembler mismatch rejection.
- Property tests: random buffer descriptors never read out of bounds; valid sphere/capsule/OBB DTOs round-trip.
- Route tests: hello timeout, duplicate device, disconnect cleanup, WebSocket binary/text flow, slow-subscriber latest-wins behavior.
- Calibration test: held-out target and reprojection metrics are stored, not just printed.
- Replay test: same recording produces matching source IDs and geometry within declared floating tolerance.
- Soak: ten minutes at intended capture rate without unbounded queue/memory growth.

This workflow passes only when it publishes one valid real `CharacterFrame` assembled from a calibrated two-device pair. Synthetic replay is an earlier gate, not hardware completion.
