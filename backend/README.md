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

## Physical camera calibration

See [the guided setup and printable A3 board](../docs/camera-calibration.md).
The backend can calibrate without an existing rig file. Legacy person-ICP
corrections are ignored; synthetic demos require `HMC_SIMULATION_MODE=true`.
`HMC_MAX_RANGE_M` defaults to 5 meters of actual camera-to-sample distance.
