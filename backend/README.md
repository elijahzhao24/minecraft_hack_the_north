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

```bash
uv run uvicorn hmc_backend.api.app:app --host 0.0.0.0 --port 8000 --workers 1
```

The backend must run as a **single Uvicorn worker** — device connections,
calibration, queues, and subscribers are in-memory shared state.

## Calibrate and merge two fixed iPhones

Place the measured ChArUco board flat on the stage mark in the orientation
documented in `calibration/charuco.py`, connect both phones as `front-phone` and
`side-phone`, and leave both tripods fixed. The backend accepts calibration
captures even when `/health` is 503 because no calibration exists yet.

```bash
uv run python scripts/calibrate.py \
  --url http://127.0.0.1:8000 \
  --capture-count 8 \
  --out data/calibration.json
```

The command requests synchronized raw pairs, solves both
`T_stage_from_optical` transforms, and writes the file only when every camera
has at least two held-out frames, at most 3 cm held-out position error, and at
most 3 px reprojection error. Restart the backend afterward. Moving either
phone, or changing its raster size or orientation, requires recalibration.

`POST /calibration/captures` requests one raw pair and
`GET /calibration/captures/{capture_id}` reports its status. Production frames
whose device, raster, or orientation differs from the loaded calibration are
rejected instead of being merged in the wrong frame.

Fixture mode remains the default. For real Pose/Hand inference:

```bash
uv run python scripts/pin_models.py
HMC_VISION_BACKEND=mediapipe \
HMC_COLLIDER_BACKEND=anatomical \
uv run uvicorn hmc_backend.api.app:app --host 0.0.0.0 --port 8000 --workers 1
```

Model files are downloaded only by the explicit pin command and are verified against `models/manifest.json` at startup. `/health` reports the selected fixture or production detector and fitter, so fixture geometry cannot be mistaken for real inference.

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
| `vision/` | MediaPipe Pose/Hand detection, depth sampling, multiview fusion, and fixture detectors |
| `colliders/` | anatomical hand/foot/limb/torso fitting, validation, and subject dimensions |
| `pipeline.py` | `CharacterProcessor` and `FrameAssembler` |
| `api/` | FastAPI routes, socket registries, `/health` |
