# hmc-backend — Workflows 2 and 3

One Python process for the "Humans in Minecraft" MVP: it ingests HMC1 RGBD
frames from two LiDAR iPhones, estimates clock offsets, pairs frames, calibrates
the cameras into a shared metric **stage frame**, reconstructs a person-only
colored point cloud, detects the person and their landmarks in each view, fuses
them into registered 3D landmarks, fits typed colliders, assembles one immutable
`CharacterFrame`, and publishes it to the Minecraft subscriber.

See [`docs/contracts.md`](../docs/contracts.md) for the normative wire contract,
[`docs/workflows/02-python-backend.md`](../docs/workflows/02-python-backend.md)
for the capture/backend workflow, and
[`docs/workflows/03-vision-and-colliders.md`](../docs/workflows/03-vision-and-colliders.md)
for the vision, landmark, and collider workflow.

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

## Vision and collider backends (Workflow 3)

Both Workflow-3 stages are selected by `HMC_`-prefixed environment variables
(see `settings.py`). Defaults keep fixtures and CI free of model weights.

| Variable | Values | Default | Effect |
|---|---|---|---|
| `HMC_VISION_BACKEND` | `fake`, `mediapipe` | `fake` | per-view person mask + 2D body/hand landmarks |
| `HMC_COLLIDER_BACKEND` | `fake`, `anatomical` | `fake` | 3D landmark fusion + typed collider fitting |
| `HMC_LEARN_SUBJECT_DIMENSIONS` | bool | `true` | let valid observed fits update per-subject limb dimensions |
| `HMC_DEBUG_ARTIFACTS_DIR` | path | unset | write per-pair overlays, PLYs, and `report.json` (may show a person — never upload) |
| `HMC_MODEL_MANIFEST_PATH` | path | `models/manifest.json` | pinned model manifest |
| `HMC_MODELS_DIR` | path | manifest directory | where the `.task` files live |
| `HMC_POSE_MODEL_PATH` / `HMC_HAND_MODEL_PATH` | path | unset | explicit file overrides (still hash-verified) |

### Model assets

MediaPipe `.task` files are pinned by SHA-256 in `models/manifest.json` and are
**never downloaded at runtime**. Fetch them once:

```bash
uv run python scripts/pin_models.py            # download + record sha256
uv run python scripts/pin_models.py --verify   # check files against the pins
```

With `HMC_VISION_BACKEND=mediapipe` the process refuses to start if a file is
missing or its hash differs from the manifest. `/health` reports the backend
and the pinned hash prefix per model.

`mediapipe` is pinned to `0.10.14`: the 1.0.x macOS wheels abort the process
inside the landmarker graph (`Check failed: service_ Service is unavailable`).

### Live demo configuration

```bash
HMC_VISION_BACKEND=mediapipe HMC_COLLIDER_BACKEND=anatomical \
uv run uvicorn hmc_backend.api.app:app --host 0.0.0.0 --port 8000 --workers 1
```

## Test

```bash
uv run pytest
```

Tests run against a deterministic synthetic skeleton fixture (`fixtures/skeleton.py`)
with a projecting fake detector, so they need no weights. When the pinned
`.task` files are present, one additional live test exercises the real
MediaPipe landmarkers end to end; it is skipped otherwise.

Collider ray/cube geometry is cross-checked against
`contracts/fixtures/collider_geometry_golden.json`, regenerated with
`uv run python scripts/write_collider_golden.py`, so the Java side can assert
the same numbers.

## Layout

| Package | Responsibility |
|---|---|
| `protocol/` | HMC1 envelope framing and buffer-descriptor decoding |
| `contracts/` | Pydantic transport models and internal typed data structures |
| `capture/` | clock sync, frame queues, pairing, recording/replay |
| `calibration/` | ChArUco camera→stage calibration and calibration IO |
| `reconstruction/` | mask resample, depth unprojection, cloud merge/downsample |
| `vision/` | Workflow-3 detection and landmark stages: model manifest, MediaPipe `ViewDetector`, hand crops/association, depth sampling, two-view triangulation, prior registration, landmark fusion, debug overlays; plus the deterministic fake |
| `colliders/` | typed collider models (sphere/capsule/OBB/disabled), pure ray/cube geometry + golden cases, subject dimensions, capsule/OBB/sphere fitters, typed validation, `AnatomicalCharacterFitter` |
| `fixtures/` | synthetic scene and known-skeleton fixtures with a projecting fake detector |
| `pipeline/` | `CharacterProcessor`, `FrameAssembler`, settings-driven factory, snapshot store |
| `api/` | FastAPI routes, socket registries, `/health` |
| `models/` | pinned MediaPipe `.task` manifest (files are git-ignored) |
| `scripts/` | `pin_models.py`, `write_collider_golden.py` |
