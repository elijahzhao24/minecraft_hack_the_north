# Hardware bring-up and Minecraft integration plan

Written after a full audit of `main` and all open PRs. This is the execution
plan to get from "four workflows that pass tests" to "a real person standing in
Minecraft with working part-specific hit volumes".

Read [`05-end-to-end-runbook.md`](05-end-to-end-runbook.md) for the demo-day
procedure; this document covers what must be **built and decided** first.

---

## 1. Where the code actually is

### Merged into `main`

| Area | Files | State |
|---|---:|---|
| `ios-capture/` (Workflow 1) | 30 | Xcode project, ARKit scene depth, RGB/depth encoders, bounded WebSocket transport, unit tests |
| `backend/` (Workflow 2) | 63 | HMC1 codec, DTOs, clock sync, pairing, reconstruction, recording/replay, FastAPI, Sentry |
| `minecraft-mod/` (Workflow 4) | 76 | Fabric 1.21.1 mod: renderer, HUD, keybinds, client socket, server snapshot store, raycaster, probe + contact services, geometry, tests |
| `contracts/` | 31 | Golden fixtures and schemas |
| `docs/` | 7 | Architecture, contracts, four workflow docs, runbook |

### Not merged

| PR | Branch | Size | State | Action |
|---|---|---:|---|---|
| **#5** | `vision-and-colliders` | +8128 | **MERGEABLE / CLEAN**, 24 commits, **225 tests pass** | **Merge now** |
| **#3** | `jonathan/ios-capture` | +3691 / −2676 | mergeable UNKNOWN | **Decide — see §2.2** |

`main` currently contains only `vision/fake.py` + `colliders/validate.py`. Every
real Workflow‑3 capability — MediaPipe detector, hand association, depth
sampling, two-view triangulation, prior registration, landmark fusion, and all
collider fitters (capsule/hand/foot/torso) — exists **only on PR #5**.

### Missing from every branch

> **ChArUco calibration is not implemented.** `backend/src/hmc_backend/calibration/`
> contains only `model.py` (file IO) and `synthetic.py` (a simulated rig). There is
> no board detection, no `solvePnP`, no rotation averaging, no validation, and no
> way to get board images from the phones into a calibration.
> The words "ChArUco"/"ArUco" appear only in docstrings.

**This is the single blocker between the current system and real hardware.**
Everything downstream — two-view merge, landmark registration, collider fitting,
and Minecraft placement — consumes `T_stage_from_optical`. Without a real one,
two physical phones cannot be fused into one stage frame.

### Bug found during the audit

`05-end-to-end-runbook.md` §4 says:

```bash
uv run uvicorn hmc_backend.main:app ...   # WRONG — main has no `app` attribute
```

The ASGI app lives at `hmc_backend.api.app:app`. Fix before demo day; this fails
instantly at the worst possible moment.

---

## 2. Step 0 — land the code (do this first, ~30 min)

### 2.1 Merge PR #5

It is clean, mergeable, and green. Nothing else should start until Workflow 3 is
on `main`, because the hardware gates below exercise exactly that code.

```bash
gh pr merge 5 --squash   # or --merge to preserve the 24-commit history
```

### 2.2 Decide PR #3 (duplicate iOS implementation)

PR #4 (`feat/workflow-1-ios-capture`) is **already merged**. PR #3 is a *second,
parallel* iOS implementation that restructures the app into a `HumansCaptureCore`
SwiftPM package with its own test suite, and deletes `ios-capture/README.md` and
`ios-capture/scripts/inspect_hmc.py`.

Two implementations of Workflow 1 cannot both live in `ios-capture/`. Pick one:

- **Recommended: close #3.** `main` already has a working app with an `.xcodeproj`
  you can run on device today. Late-stage restructuring risks the one component
  that needs physical hardware to validate.
- If #3's `HumansCaptureCore` tests are genuinely better, cherry-pick *only* the
  test targets onto `main` rather than taking the 2676-line deletion.

Whatever you choose, do it **before** touching phones — you do not want to
discover mid-calibration that you flashed the wrong build.

### 2.3 Fix the runbook command

One-line fix to `05-end-to-end-runbook.md` §4.

---

## 3. The critical path — build calibration

### 3.1 Design decision: record → solve offline

**Do not add a new endpoint or protocol message.** The cheapest correct path
reuses machinery that already exists and is tested:

1. Phones send board frames through the **existing** `/ws/capture` RGBD path.
2. Backend records them with the **existing** `save_recording()` (SHA‑256 manifest).
3. A **new offline CLI** reads the recording, solves each camera's pose, and
   writes `data/calibration.json`.
4. Restart the backend; `load_rig_calibration()` (already tested) picks it up.

This adds roughly one module and one script. No contract change, no schema
version bump, no new failure mode in the live path. It also means calibration is
reproducible from saved bytes — you can re-solve without the person or the
tripods present.

### 3.2 What to build

**`backend/src/hmc_backend/calibration/charuco.py`**

```python
def detect_board(gray, board) -> tuple[corners, ids] | None
def estimate_pose(corners, ids, board, K, dist) -> tuple[R, t, reproj_px] | None
def average_rotations_so3(rotations) -> np.ndarray    # NOT element-wise
def solve_camera(frames, board, K) -> CameraSolution  # robust aggregate + residuals
```

**`backend/scripts/calibrate.py`** — reads one or more recording directories,
runs `solve_camera` per device, holds out ~25% of frames for validation, and
writes a `RigCalibration` via the existing `save_rig_calibration()`.

### 3.3 OpenCV API trap

The project pins `opencv-contrib-python` **5.x**. Most ChArUco tutorials online
use the pre‑4.7 API (`cv2.aruco.interpolateCornersCharuco`,
`estimatePoseCharucoBoard`) which **no longer exists**. Use the modern flow:

```python
dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_5X5_100)
board = cv2.aruco.CharucoBoard((squares_x, squares_y), square_len_m, marker_len_m, dictionary)
detector = cv2.aruco.CharucoDetector(board)

charuco_corners, charuco_ids, _, _ = detector.detectBoard(gray)
obj_pts, img_pts = board.matchImagePoints(charuco_corners, charuco_ids)
ok, rvec, tvec = cv2.solvePnP(obj_pts, img_pts, K_rgb, dist)   # dist = zeros for ARKit
```

`solvePnP` returns **`T_camera_from_board`**. You need the inverse.

### 3.4 The transform chain (and the handedness trap)

```
T_stage_from_optical  =  T_stage_from_board  @  inverse(T_camera_from_board)
```

Place the board **flat on the floor**, its origin corner on the stage mark, with
its **+Y axis pointing away from the front camera**. Then:

```
R_stage_from_board = [[1,  0, 0],
                      [0,  0, 1],
                      [0, -1, 0]]
```

Verify: board +X → stage +X; board +Y → stage −Z (away from front camera);
board +Z (up out of the board) → stage +Y. **det = +1.**

> The obvious-looking swap `[[1,0,0],[0,0,1],[0,1,0]]` has **det = −1**. It is a
> reflection and it will mirror your person — the exact failure the workflow docs
> warn about. Make `T_stage_from_board` a named constant with a unit test
> asserting `det ≈ +1`, and confirm with the asymmetric-pose left/right check.

### 3.5 Aggregation and validation

- **Never element-wise average rotation matrices.** Average on SO(3): convert to
  quaternions, take the eigenvector of the outer-product sum (or SVD-project the
  mean matrix back onto SO(3)). Median the translations.
- Reject frames with negative target depth, too few corners, spatially clustered
  corners, or reprojection error above threshold.
- Hold out ~25% of frames. Store **median and max reprojection error** and the
  held-out count in the calibration file's `validation` block — the schema
  already has the field.
- Target: **2–3 px median** reprojection at the recorded RGB resolution.
- Independently verify metric scale with a tape-measured target at several stage
  positions. Reprojection agreement alone does not prove correct depth.

### 3.6 Fallbacks if calibration fights you

| Plan | Method | Expected error | When |
|---|---|---|---|
| **A** | ChArUco as above | 2–3 px / ~2–5 cm | Default |
| **B** | Single shared board pose, fewer frames, no held-out split | ~5–10 cm | Time pressure |
| **C** | **Tape-measure the tripods** and build the rig with the existing `look_at_optical()` from `synthetic.py` | ~10–20 cm | Calibration broken, demo in <2 h |

Plan C is real and already coded: `look_at_optical(eye, target, up)` plus
measured tripod positions produces a usable `RigCalibration` with no board at
all. Accuracy will miss the 5 cm goal — **say so out loud rather than widening
hitboxes to hide it.**

---

## 4. Hardware bring-up sequence

Run these in order. Each gate is cheap to verify and expensive to skip.

### Gate A — Minecraft on synthetic data (no hardware, do it today)

The mod ships `fixtureOnStart=true`, so it renders a synthetic human immediately.

```bash
cd minecraft-mod && ./gradlew runClient
```

Confirm: cloud renders, HUD shows decoded/pending/**active** frame IDs, `P`
probes report a body part, `F8` clears, `O`/`I`/`U` toggle cloud/skeleton/colliders.

**This validates the entire Minecraft half before a phone is ever involved.** If
this does not work, no amount of calibration will save the demo.

### Gate B — backend ↔ Minecraft over the socket

```bash
cd backend
HMC_VISION_BACKEND=fake HMC_COLLIDER_BACKEND=fake \
uv run uvicorn hmc_backend.api.app:app --host 0.0.0.0 --port 8000 --workers 1

# separate shell
uv run python scripts/run_vertical_slice.py
```

Point the mod at the backend (`humancraft.json` → `backendUrl`, default
`ws://127.0.0.1:8000/ws/character`). Confirm the synthetic `CharacterFrame`
arrives over the wire and reaches **active** state.

### Gate C — one real phone

Start the backend on the laptop's **LAN IP** (not `127.0.0.1`), allow inbound TCP
8000, put phone and laptop on the same trusted network.

On the phone: set device ID `front-phone`, backend `ws://<laptop-ip>:8000/ws/capture`,
connect, start AR capture. Check `/health` shows `connected` and `clock_ready`.

Capture a frame with a **tape-measured target** in view. Verify:
- RGB and depth edges align
- Depth agrees with the tape measure (metric scale sanity)
- Person mask does not erase hands or toes

A simulator does **not** pass this gate — it has no LiDAR.

### Gate D — calibration day

Follow `05-end-to-end-runbook.md` §3, plus:
- Capture board frames spanning the **shared** field of view at multiple angles,
  not one central shot.
- Both phones must see the board **without moving the tripods between captures**.
- Run `scripts/calibrate.py`; inspect held-out residuals before accepting.
- Photograph the tripod/board layout and save it with the calibration ID.
- **Tape the tripod feet to the floor.** If one moves, recalibrate — never
  hand-edit a transform until it "looks right".

### Gate E — two-camera merge

Capture one still pair. Check:
- No double torso (that means calibration or optical-axis conversion is wrong)
- Floor and background excluded, feet retained
- Left/right correct under an **asymmetric pose** (right arm raised)
- Pair skew inside the 50 ms budget

Use the per-camera source coloring toggle (`Y`) — misalignment becomes obvious
when you can see which camera contributed which points.

### Gate F — real person in Minecraft

Switch the backend to the real Workflow‑3 stages:

```bash
HMC_VISION_BACKEND=mediapipe HMC_COLLIDER_BACKEND=anatomical \
uv run uvicorn hmc_backend.api.app:app --host 0.0.0.0 --port 8000 --workers 1
```

Models must be pinned first: `uv run python scripts/pin_models.py`.

Then press **F7 in Minecraft** to trigger a synchronized capture, and run the
demo sequence from runbook §5.

---

## 5. Getting it into Minecraft

**The Minecraft half is the most complete part of the project.** The integration
is mostly configuration, not code.

### The path a frame takes

```
backend publishes HMC1 CHARACTER_FRAME
  → CharacterWebSocket decodes off the render thread
  → StageToWorld applies anchor + blocksPerMeter (once, to points AND colliders)
  → client sends InstallSnapshotPayload (colliders only) to the logical server
  → server validates, stores, returns SnapshotAckPayload
  → only the acknowledged frame_id becomes ACTIVE for render + interaction
  → P → ProbeRequestPayload → server derives eye/look/reach → ProbeResultPayload
```

The server is authoritative. The client never decides what was hit.

### Controls already implemented

| Key | Action |
|---|---|
| `F6` / `F7` / `F8` | reconnect / **recapture** / clear |
| `P` | probe (server-authoritative part-specific hit) |
| `O` / `I` / `U` / `Y` / `H` | cloud / skeleton / colliders / source colors / HUD |
| Numpad `8 2 4 6`, PgUp/PgDn, Home | move anchor / reset |
| `=` / `-` | scale up / down |

### Tuning for the demo

- **`blocksPerMeter = 1.0`** → a 1.7 m person is 1.7 blocks tall. Correct, but
  visually small. Raising it to ~2.0 reads better on a projector **but magnifies
  every measurement error proportionally.** Decide deliberately; if you scale up,
  say that error scales too.
- **`anchorAuto = true`** places the human in front of the player. Make sure the
  anchor has open space — a human intersecting terrain makes probes confusing.
- **Point budget**: `max_points` defaults to 50,000. If the renderer stalls, cut
  points before cutting hand detail — hands are the thing being demonstrated.
- **`fixtureOnStart`**: set `false` once real captures work, or the synthetic
  human may be mistaken for the real one on stage.

### Most likely integration failures

| Symptom | Cause |
|---|---|
| Cloud renders, hits are offset | Client and server disagree on active frame, or anchor/scale applied to points but not colliders |
| Wireframe right, hit wrong | Narrow-phase shape axes — OBB rows vs columns |
| Works in client, not singleplayer | Payload registered on only one side |
| Person mirrored | `T_stage_from_board` determinant (§3.4) — **fix in calibration, never by flipping Minecraft axes** |

---

## 6. Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| **Calibration not built** | Blocks everything real | Start immediately; Plan C fallback exists (§3.6) |
| PR #5 not merged | No real vision/colliders | Merge first (§2.1) |
| Two iOS implementations | Wrong build on device | Resolve PR #3 before touching phones (§2.2) |
| Only one LiDAR iPhone available | No two-view merge | Single-camera demo is still a valid gate; state the limitation |
| Network blocks device→laptop | Phones cannot connect | Test the LAN early; carry a personal hotspot |
| MediaPipe instability | No landmarks | `mediapipe` is pinned to `0.10.14` for a reason — do not upgrade |
| Demo-day failure | No demo | Keep a recorded two-phone capture and replay it (§7) |

---

## 7. Demo-day safety net

**Record a good capture the moment you get one.** `save_recording()` writes
immutable packets with checksums, and `Replayer` reproduces the exact frame
without phones, tripods, calibration, or a person.

If the rig fails on stage, replay a real recording and say clearly that it is a
recording. That is honest and it still demonstrates real sensor data through the
real pipeline. A recording that works beats a live rig that does not.

Also keep:
- The exact commit + `uv.lock` + model manifest hashes
- The active calibration and its held-out validation report
- Before/after Sentry trace/log evidence for Gate 7

---

## 8. Suggested order of work

1. Merge PR #5; resolve PR #3; fix the runbook command. *(~30 min)*
2. **Gate A** — Minecraft on synthetic data. *(~30 min, no hardware)*
3. **Gate B** — backend ↔ Minecraft over the socket. *(~30 min)*
4. **Build calibration** (§3) against *recorded synthetic board frames* first, so
   the solver is tested before the tripods are set up. *(~2–3 h)*
5. **Gate C** — one real phone. *(~1 h)*
6. **Gate D/E** — calibrate, merge, validate. *(~1–2 h)*
7. **Gate F** — real person in Minecraft, record everything. *(~1 h)*
8. Gate 7 observability evidence with a saved input. *(~1 h)*

Steps 2–3 need no hardware and de-risk the whole back half — do them while
someone else is setting up tripods.
