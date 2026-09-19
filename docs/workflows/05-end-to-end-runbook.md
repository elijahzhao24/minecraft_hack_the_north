# End-to-end build and demo runbook

This runbook joins the four workflow documents. Workflow 4 is implemented under `minecraft-mod/`; other commands become runnable as their owning workflows land.

## 1. One-time prerequisites

- Mac/Xcode and two physical LiDAR-capable iPhones for the hardware gate.
- Python 3.12 and `uv` on the processing laptop.
- JDK 21 for Minecraft 1.21.1/Fabric development. Homebrew installs it *off*
  `PATH`, so `java -version` can fail while the JDK is present. Export it before
  any Gradle command:
  `export JAVA_HOME=/opt/homebrew/opt/openjdk@21 PATH="$JAVA_HOME/bin:$PATH"`.
- Printed, measured ChArUco board; tape/marks for the stage origin and phone tripods.
- Minecraft Java Edition account/profile and a test world.
- All devices on a trusted local network that permits device-to-laptop connections.

Record the laptop LAN IP. Permit inbound TCP 8000 in the local firewall for the demo network. Do not expose the unauthenticated MVP capture endpoint to the public internet.

## 2. Build order with hard gates

### Gate 1 — Contract fixture

1. Implement Python `HMC1` encoder/decoder and DTO validation.
2. Generate a tiny synthetic RGBD packet and a character packet containing sphere, capsule, and OBB.
3. Freeze packet bytes, decoded JSON, and SHA-256 under `contracts/fixtures/`.
4. Implement Swift and Java decoder tests against those same bytes.

Pass: all languages agree on lengths, little-endian values, matrices, points, IDs, and rejection fixtures.

### Gate 2 — Synthetic backend to Minecraft

1. Start the backend with a replay source.
2. Run the Fabric client and connect to `/ws/character`.
3. Confirm decoded → server-installed → acknowledged → active state.
4. Verify all analytic geometry tests and probe/contact behavior.

Pass: a synthetic person renders; server results match Python golden geometry.

### Gate 3 — One real phone

1. Start backend on the laptop LAN address.
2. Connect one phone and inspect `/health`.
3. Capture/record RGBD with a measured target.
4. Replay it through mask, single-view reconstruction, and overlays.

Pass: RGB/depth/mask align and metric scale is plausible. A simulator does not pass.

### Gate 4 — Anatomy on held poses

Process neutral, bent-arm/open-hand, and lifted/turned-foot captures. Validate hand association, depth support, 3D landmarks, hand/foot volumes, and explicit invalidity under occlusion.

Pass: debug overlays and measured error report satisfy the criteria in workflow 3.

### Gate 5 — Two-camera calibration and merge

Calibrate both fixed phones and validate on held-out board/target frames. Capture one still pair, merge without double torso, and confirm left/right using the asymmetric pose.

Pass: saved calibration metrics, pair skew, and merged geometry are within the declared budgets.

### Gate 6 — Real Minecraft snapshot

Publish the exact real cloud/landmark/collider snapshot. Probe hands/feet, ray through a gap, put a wall in front, and move the contact target against a turned foot.

Pass: active client/server frame IDs agree and all results use the visible snapshot.

### Gate 7 — Observability evidence

Use traces/logs to identify one real bottleneck or geometry/integration defect, change the implementation, and rerun the same saved input. Preserve before/after trace/log evidence and measured difference. Do not claim a candidate improvement as an observed result.

## 3. Calibration-day procedure

1. Fix both tripods so both cameras see the full subject and a common board over useful angles.
2. Mark `front-phone`, `side-phone`, stage origin, front direction, and floor plane physically.
3. Launch both apps with landscape-right and confirm actual RGB/depth dimensions.
4. Capture multiple board frames spanning the shared field, not one central view.
5. Generate calibration; inspect per-camera and held-out reprojection residuals.
6. Place a separate measured target at several stage locations and verify scale/axis signs.
7. Save calibration with its ID and photos/notes of tripod/board layout.
8. Do not move tripods afterward. If one moves, stop and recalibrate rather than editing a transform until it looks right.

## 4. Normal demo startup

### Backend

```bash
cd backend
uv sync --frozen
uv run uvicorn hmc_backend.api.app:app --host 0.0.0.0 --port 8000 --workers 1
```

Verify `http://<laptop-ip>:8000/health` reports calibration/models ready.

### Phones

On each phone:

1. Select its fixed device ID.
2. Enter `ws://<laptop-ip>:8000/ws/capture`.
3. Connect and start AR capture.
4. Confirm dimensions/orientation/tracking and clock-ready state in backend health.
5. Frame the subject so hands and feet are visible in both cameras.

### Minecraft

During development:

```bash
cd minecraft-mod
./gradlew runClient
```

In the test world, confirm backend connected and decoded/pending/active IDs. Place the contact target and ensure the anchor has open space around it.

## 5. Capture-to-interaction demo sequence

1. Subject stands in the marked area in a neutral pose and holds still.
2. Trigger snapshot capture. Narrate/observe both phone acknowledgements, pair skew, processing spans, and the new backend frame ID.
3. Wait for Minecraft logical-server acknowledgement; confirm cloud, skeleton, and collider overlays show the same frame/calibration IDs.
4. Toggle debug layers to explain appearance vs interaction representations.
5. Probe torso, left/right hands, and both feet; show the part-specific result.
6. Aim through an arm/torso gap and show miss.
7. Put a wall between player and human and show block-occluded result.
8. Move/place the contact cube at a hand/turned foot and show oriented contact.
9. Capture bent-arm/open-hand pose. Confirm atomic replacement—no old/new mixed limbs.
10. Capture lifted/turned-foot pose and repeat orientation/contact proof.
11. Review local diagnostics and saved measurements for the real before/after engineering finding.

## 6. Invariant checklist during integration

For every suspect frame, compare:

| Layer | IDs/measurements to match |
|---|---|
| Phone | device, session, capture, sequence, raster sizes, orientation |
| Pairer | two source IDs, normalized times, skew, clock uncertainty |
| Calibration | calibration ID, device/raster constraints, transform direction |
| Vision | source capture IDs, landmark provenance/validity |
| Character | session, calibration, frame, source frames, point/collider counts |
| Minecraft client | decoded, pending, active frame and calibration |
| Logical server | active frame, collider count, anchor, scale |
| Result | request, frame, collider/body part, world hit/contact |

If any identity differs, stop debugging geometry and fix snapshot/data association first.

## 7. Failure isolation

| Symptom | First checks |
|---|---|
| Phone cannot connect | laptop IP/port, same LAN, local-network permission, firewall, backend bind host |
| Backend rejects immediately | hello DTO, device ID allowlist, protocol version, duplicate active session |
| Rotated/mirrored person | encoded JPEG raster vs orientation, intrinsics resize, Swift matrix flattening; do not patch Minecraft axes |
| Cloud has two torsos | calibration ID/rig movement, pair skew, optical-axis conversion, source-color overlay |
| Floor/background present | person mask mapping, stage crop, depth confidence; avoid broad arbitrary point deletion |
| Hand floats on background | masked depth-neighborhood support and discontinuity rejection |
| Wrong left/right hand | unmirrored input, anatomical mapping, association margin, asymmetric fixture |
| Cloud correct but hits offset | active frame IDs, anchor/scale mapping, server payload coordinates |
| Wireframe correct but hit wrong | exact narrow-phase implementation, shape axis rows, block-distance comparison |
| Render stalls | decode/model work on render/event loop, repeated GPU uploads, too many draw calls |
| Growing latency | phone/backend/subscriber queue depth; verify latest-wins limits |
| Works in client but not singleplayer server | payload registration/handler side, server-thread scheduling, rejected ack |

## 8. Demo reset and shutdown

To reset without recalibration: clear active Minecraft snapshot, start new phone sessions if needed, keep tripods fixed, reconnect, and recapture. Do not delete recordings during the demo; mark bad captures in their manifest.

Shutdown order: stop phone capture, disconnect Minecraft/backend socket, exit the world/client, then stop Uvicorn. Confirm recording manifests are complete. Calibration and recordings are retained; any material deletion is a separate deliberate operation.

## 9. Final evidence bundle

Keep a reproducible directory or release artifact containing:

- Exact Git commit and dependency locks/pins.
- Model manifest/checksums.
- Active calibration and held-out validation report.
- One consented real two-phone recording and decoded headers.
- Generated character JSON summary and point-buffer hash.
- Python/Java geometry test results.
- Physical hand/foot error measurements.
- Ten-minute soak notes.
- Local diagnostic/measurement before-and-after evidence.
- Known limitations and which acceptance gates are verified vs unverified.

## 10. Hardware-free rehearsal

Everything below runs with **no phones, no tripods, and no calibration board**.
Do it before the rig exists — it exercises the same sockets, decoders, pairing,
and publication path the real capture uses, so what it proves keeps holding.

### 10.1 Full capture-to-subscriber smoke test

```bash
cd backend

# 1. Write a rig the backend and the fake phones both agree on.
uv run python scripts/fake_phone.py --write-calibration data/calibration.json

# 2. Start the backend (single worker; it loads that calibration at startup).
uv run uvicorn hmc_backend.api.app:app --host 0.0.0.0 --port 8000 --workers 1

# 3. In another shell: connect two fake phones, fire one capture, and verify
#    a CharacterFrame comes back on /ws/character. Exits non-zero on failure.
uv run python scripts/fake_phone.py --once --expect-frame
```

The harness connects as real WebSocket clients, answers `clock_ping`, and
honours `capture_request` — so once Minecraft is attached, **F7 drives a real
synchronized capture**. Use `--interval 2` for live streaming, and `--skew-ms`
to push the pairer toward its budget.

Received frames are validated with the strict decoder, which applies the same
rules as the mod's `CharacterFrameDecoder`. A clean run means the backend's
bytes are acceptable to the Java consumer.

### 10.2 Minecraft without a backend

`fixtureOnStart=true` renders a synthetic human immediately, so the whole
Minecraft half can be checked on its own:

```bash
export JAVA_HOME=/opt/homebrew/opt/openjdk@21 PATH="$JAVA_HOME/bin:$PATH"
cd minecraft-mod
./gradlew test      # 60 tests: geometry, probe, contact, snapshot store, payloads
./gradlew runClient
```

Confirm the HUD shows decoded/pending/**active** IDs, then:

| Key | Action |
|---|---|
| `G` | request a capture |
| `R` | probe (server reports the body part hit) |
| `C` | clear the active human |
| `J` | reconnect to the backend |
| `B` | re-anchor in front of you |
| `O` / `I` / `U` / `Y` / `H` | cloud / skeleton / colliders / source colours / HUD |
| arrow keys, `[` / `]` | move the anchor |
| `=` / `-` | scale up / down |

Defaults deliberately avoid vanilla bindings (`P` is Social Interactions) and
anything needing `Fn` or a numeric keypad, so they work on a laptop. All are
rebindable in Options -> Controls -> HumanCraft.

### 10.3 Contract gates

```bash
cd backend
uv run pytest tests/test_cross_language_fixtures.py tests/test_rgbd_fixtures.py
```

Python decodes the Java-authored CHARACTER_FRAME goldens and the RGBD goldens,
and rejects every malformed fixture with the declared code. Run this after any
change to `contracts/`, and regenerate RGBD fixtures only deliberately:
`uv run python scripts/write_rgbd_fixtures.py` (review the diff — they are
frozen).

### 10.4 What this cannot prove

A clean rehearsal says the *software* agrees with itself end to end. It says
nothing about sensor accuracy, real lens distortion, motion blur, lighting,
clock behaviour across two physical devices, or whether the tripods see the
subject. Gates 3–6 still require the rig.
