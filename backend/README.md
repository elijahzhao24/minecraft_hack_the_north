# hmc-backend — Workflow 2

One Python process for the "Humans in Minecraft" MVP: it ingests HMC1 RGBD
frames from two LiDAR iPhones, estimates clock offsets, pairs frames, calibrates
the cameras into a shared metric **stage frame**, reconstructs a person-only
colored point cloud, assembles one immutable `CharacterFrame`, and publishes it
to the Minecraft subscriber.

See [`docs/contracts.md`](../docs/contracts.md) for the normative wire contract
and [`docs/workflows/02-python-backend.md`](../docs/workflows/02-python-backend.md)
for the workflow specification.

## Requirements

- Python 3.12
- [`uv`](https://docs.astral.sh/uv/)

## Setup

```bash
cd backend
uv sync
```

## Run

For person-only clouds, download the pinned Pose Landmarker Lite artifact and verify it before startup:

```bash
curl -L --fail \
  -o models/pose_landmarker_lite.task \
  https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task
echo "59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a  models/pose_landmarker_lite.task" | shasum -a 256 -c -
```

```bash
HMC_POSE_MODEL_PATH=models/pose_landmarker_lite.task \
HMC_REQUIRE_REAL_VISION=true \
uv run uvicorn hmc_backend.api.app:app --host 0.0.0.0 --port 8000 --workers 1
```

The backend must run as a **single Uvicorn worker** — device connections,
calibration, queues, and subscribers are in-memory shared state.

Once at least one phone reports clock-ready, pressing **Start live recapture** starts a backend-paced 3 FPS session. With one connected phone the backend publishes a single-view LiDAR cloud and adds the `single_view` quality warning. If `data/calibration.json` is missing, the first RGBD frame creates a session-local provisional camera-relative calibration for debugging. When a real multi-camera calibration is loaded, a second calibrated phone is incorporated automatically. Live frames are not stored under `data/recordings`; explicit snapshots are.

Provisional mode is enabled by default with `HMC_ALLOW_UNCALIBRATED_SINGLE_VIEW=true`. It assumes a stationary phone and is only for single-view debugging; it cannot align independent ARKit worlds for multi-phone merging. Set it to `false` when a real stage calibration must be mandatory.

The default allows one or two configured devices and requires at least one for capture. Override this when strict two-phone operation is desired:

```bash
HMC_MIN_CAPTURE_DEVICES=2
```

## Test

```bash
uv run pytest
```

## Layout

| Package | Responsibility |
|---|---|
| `protocol/` | HMC1 envelope framing and buffer-descriptor decoding |
| `contracts/` | Pydantic transport models and internal typed data structures |
| `capture/` | clock sync, frame queues, pairing, recording/replay |
| `calibration/` | ChArUco camera→stage calibration and calibration IO |
| `reconstruction/` | mask resample, depth unprojection, cloud merge/downsample |
| `vision/` | Workflow-3 protocol boundary + a deterministic fake for fixtures |
| `colliders/` | collider DTO validation (fitting itself belongs to Workflow 3) |
| `pipeline.py` | `CharacterProcessor` and `FrameAssembler` |
| `api/` | FastAPI routes, socket registries, `/health` |
