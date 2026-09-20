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

### Enable the Hacker Badge controller on macOS

Turn Bluetooth on and start the backend with the BLE receiver enabled:

```bash
HMC_BADGE_CONTROLLER_ENABLED=true uv run uvicorn hmc_backend.api.app:app \
  --host 0.0.0.0 --port 8000 --workers 1
```

The receiver scans for the custom service and a local name beginning with
`HTN-Badge`. Override the exact name with `HMC_BADGE_CONTROLLER_NAME`. The
latest validated state is available to Minecraft at `/ws/controller`, and
`/health` includes connection age, malformed packets, gaps, and reconnects.
Bluetooth loss neutralizes all buttons after 250 ms.

## Calibrate and merge two fixed iPhones

Place the measured ChArUco board at the stage origin, connect both phones as
`front-phone` and `side-phone`, then press **N** in Minecraft. The character
WebSocket sends `anchor_rig`; the backend pauses live capture, requests
clock-aligned samples from both phones, solves `stage -> <device>/optical`, and
atomically writes `data/frame-tree.json`. Progress and failure details return as
`anchor_status` messages. Live streaming resumes automatically.

Each incoming cloud is transformed through the frame tree at its normalized
capture timestamp before fusion. Frames with the wrong device, raster size, or
orientation are rejected. Moving either phone requires anchoring again. The
offline `scripts/calibrate.py --recordings ...` command remains available for
diagnostics and reproducible solves; there is no HTTP calibration endpoint.

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
| `controller/` | Hacker Badge BLE decoding, fail-safe state, and fanout |
| `calibration/` | ChArUco camera→stage calibration and calibration IO |
| `reconstruction/` | mask resample, depth unprojection, cloud merge/downsample |
| `vision/` | MediaPipe Pose/Hand detection, depth sampling, multiview fusion, and fixture detectors |
| `colliders/` | anatomical hand/foot/limb/torso fitting, validation, and subject dimensions |
| `pipeline.py` | `CharacterProcessor` and `FrameAssembler` |
| `api/` | FastAPI routes, socket registries, `/health` |
