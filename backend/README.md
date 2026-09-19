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
