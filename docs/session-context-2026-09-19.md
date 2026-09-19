# Session context — Humans in Minecraft

**Date:** 2026-09-19 · **Event:** Hack the North
**Repo:** https://github.com/elijahzhao24/minecraft_hack_the_north
**Working dir:** `/Users/adrianshahnazari/Hackthenorth`
**Git identity:** `AdrianShah <152305520+AdrianShah@users.noreply.github.com>`

This is a written-out record of everything established in the session, for handoff
or for reloading into a fresh context.

---

## 1. What we're building

Capture a real person with **two LiDAR iPhones** and render them inside
**Minecraft Java Edition** as a colored point cloud with **body-part-specific hit
volumes** — so you can probe a hand, a foot, or the torso and the server tells you
which part you hit.

Four workflows:

| # | Scope | Status |
|---|---|---|
| 1 | iOS capture app (Swift/ARKit) | exists, unsigned for Adrian until today |
| 2 | Python backend (ingest → reconstruct → publish) | **built, merged as PR #1** |
| 3 | Vision / anatomy (MediaPipe landmarks, colliders) | PR #5, open |
| 4 | Fabric Minecraft mod | built, on `main` |

---

## 2. Commit conventions (IMPORTANT)

- **Never** add Claude / Anthropic co-author or attribution trailers to commits
  or PR descriptions.
- **Do** add the **Devin AI** trailers — the team is competing for the *best use of
  Devin AI* prize track. The exact format is on the `elijah/fabric-interface`
  branch; copy it verbatim.
- Author must be the GitHub username `AdrianShah`, not the macOS account name.
- One commit per logical item. History has been rewritten with
  `git filter-branch --msg-filter` more than once to correct trailers — force-push
  is expected on these branches.

---

## 3. Branch and PR state

| Branch | State |
|---|---|
| `main` | has Workflow 4 (Fabric mod) + merged backend |
| `workflow-2-python-backend` | **merged** as PR #1 |
| `feat/charuco-calibration` | PR #6, **open**, awaiting merge |
| `docs/hardware-integration-plan` | pushed, **no PR** |
| `feat/preflight-integration` | **current branch**, 8 commits pushed, **no PR yet** |
| PR #3 | duplicate iOS implementation — needs a decision |
| PR #5 | Workflow 3 (vision) — awaiting merge |

Recent commits on `feat/preflight-integration`:

```
1670520 Rebind default keys to avoid vanilla conflicts and laptop-hostile keys
a4b43b4 Fix snapshot anchored below the world on join
b5b0a46 Document the hardware-free rehearsal and fix the runbook startup command
9b067d4 Add fake-phone harness driving /ws/capture over real WebSockets
55a4cc0 Add RGBD golden fixtures for the phone-to-backend direction
```

---

## 4. Wire protocol — `HMC1`

Binary envelope, little-endian throughout:

```
magic       "HMC1"        4 bytes
version     uint16_le
msg_type    uint16_le     1 = RGBD_FRAME, 2 = CHARACTER_FRAME
header_len  uint32_le
payload_len uint32_le
header      UTF-8 JSON
payload     concatenated binary buffers (described by the header)
```

Point records are packed **`xyzrgba16_le`** — 16 bytes each.

### Coordinate frames

- **Stage frame** — metres; origin on the floor at the capture mark;
  `+Y` up; `+X` front-camera image-right; `+Z` toward the front camera.
- **Optical frame** — OpenCV convention: `+X` image-right, `+Y` image-down,
  `+Z` forward.
- Transforms are named `T_destination_from_source` and are **row-major 4×4**.

---

## 5. Stack / pinned versions

**Backend** — Python 3.12 + `uv`, FastAPI/Uvicorn (**single worker**), Pydantic v2
with `extra='forbid'`, NumPy, OpenCV 5.0, MediaPipe pinned `0.10.14` (on PR #5).

**Mod** — Minecraft 1.21.1, Fabric Loader 0.19.5, Fabric API 0.116.17+1.21.1,
Loom 1.17.21, **JDK 21**, Mojang mappings.

> Homebrew installs JDK 21 **off `PATH`**, so `java -version` fails while the JDK is
> present. Before any Gradle command:
> ```bash
> export JAVA_HOME=/opt/homebrew/opt/openjdk@21 PATH="$JAVA_HOME/bin:$PATH"
> ```

---

## 6. Backend module map

```
backend/src/hmc_backend/
  protocol/      envelope.py  buffers.py
  contracts/     enums  control  rgbd  internal  arrays
                 character_codec.py      (encoder)
                 character_decode.py     (NEW strict decoder, mirrors Java)
  capture/       clock  pairing  rgbd_ingest  recording  replay
  calibration/   model  synthetic  charuco.py
  reconstruction/geometry  reconstruct
  vision/        protocols  fake
  colliders/     validate
  pipeline/      assembler  processor  snapshot_store  factory
  api/           app  runtime  hub
  observability/ sentry
  fixtures/      scene
backend/scripts/  run_vertical_slice.py  calibrate.py
                  fake_phone.py  write_rgbd_fixtures.py
```

### Error codes emitted by the strict decoders

`invalid_message`, `unsupported_version`, `frame_too_large`,
`invalid_buffer_range`, `non_finite_geometry`, `invalid_collider_geometry`,
`limit_exceeded`

---

## 7. ChArUco calibration (PR #6)

`backend/src/hmc_backend/calibration/charuco.py` — uses the **OpenCV 5 modern API**
(`CharucoDetector`, `matchImagePoints`). The legacy
`interpolateCornersCharuco` / `estimatePoseCharucoBoard` were **removed in 5.x**.

The single most important constant, and the one that was hardest to get right:

```python
# OpenCV's ChArUco object frame follows the *printed image* convention:
#   +X left->right across page, +Y top->bottom DOWN the page,
#   +Z = X x Y, pointing INTO the page. Printed face is -Z.
STAGE_FROM_BOARD_ROTATION = np.array([
    [1.0, 0.0,  0.0],
    [0.0, 0.0, -1.0],
    [0.0, 1.0,  0.0],
], dtype=np.float64)
```

Other pieces: `average_rotations_so3` (SVD projection — **never** average rotation
matrices element-wise), `solve_camera`, `validate_solution`, `observe_frame`,
`compose_stage_from_optical`.

`backend/scripts/calibrate.py` is the offline CLI: recordings → `calibration.json`.

**Result:** cameras recovered to within **1 mm** of ground truth, with sub-pixel
reprojection residuals. 26 tests in `test_charuco.py`, 7 in
`test_calibration_pipeline.py`.

---

## 8. Minecraft mod — key files and bindings

```
minecraft-mod/src/client/java/dev/humancraft/client/
  HumanCraftClient.java
  state/ClientSnapshotCoordinator.java
  render/HumanRenderer.java
  input/HumanCraftKeybindings.java
```

Rendering hooks: `WorldRenderEvents.LAST`, `WorldRenderContext.matrixStack()`,
`ClientPlayConnectionEvents.JOIN`.

### Keybindings (rebound this session)

| Key | Action |
|---|---|
| `G` | request a capture |
| `R` | probe (server reports the body part hit) |
| `C` | clear the active human |
| `J` | reconnect to the backend |
| `B` | re-anchor in front of you |
| `O` / `I` / `U` / `Y` / `H` | cloud / skeleton / colliders / source colours / HUD |
| arrow keys, `[` `]` | move the anchor |
| `=` / `-` | scale up / down |

Chosen to avoid vanilla bindings (`P` is Social Interactions) and anything needing
`Fn` or a numeric keypad. All rebindable in **Options → Controls → HumanCraft**.

---

## 9. Bugs found and fixed (the valuable part)

### 9.1 The invisible human — root cause

Everything reported healthy: 60 accepted installs, `matrixStack=true`,
`cloudBuf=true`, 6424 points. Nothing rendered.

Temporary `HMC-DIAG` logging revealed `firstWorld=(2.44, -53.52, -1.44)` and
`anchorY: -60.0`. **`onJoin` read `client.player.getY()` before the position was
synced**, capturing a placeholder below terrain. It was silent because the renderer
skips when buffers are null and only reports draw failures through Sentry, which was
disabled.

Fix — defer anchoring a few ticks and validate:

```java
private static final int ANCHOR_SETTLE_TICKS = 5;
private int anchorPendingTicks;

public void onJoin(Minecraft client) {
    joined = true;
    serverStatus = "ready";
    if (config.anchorAuto) {
        // player position not synced yet at JOIN; defer
        anchorPendingTicks = ANCHOR_SETTLE_TICKS;
    }
}

public void tick(Minecraft client) {
    if (anchorPendingTicks > 0) {
        anchorPendingTicks--;
        if (anchorPendingTicks == 0 && setAutomaticAnchor(client)) {
            persistAndReinstall("anchor ready");
        }
    }
}

private boolean setAutomaticAnchor(Minecraft client) {
    if (client.player == null || client.level == null) return false;
    double y = client.player.getY();
    if (!Double.isFinite(y) || y <= client.level.getMinBuildHeight()) return false;
    // floor()+0.5 for X/Z, floor(y) for Y
    return true;
}
```

**Verified:** anchor resolves to `y=72`, human renders at `y 72.07–75.56`.

### 9.2 ChArUco board orientation

The plan doc asserted `[[1,0,0],[0,0,1],[0,-1,0]]` (board `+Y` away from the front
camera, `+Z` up). Testing against a rendered board disproved it. Correct is
`[[1,0,0],[0,0,-1],[0,1,0]]`.

> **Both matrices have determinant +1, so a determinant check cannot catch this.**

The doc was corrected in a follow-up commit.

### 9.3 Test-render correspondence

Initially assumed which image corner was the board origin; detection failed and the
pose came back 180° off. Fixed by **deriving** the correspondence — detect the board
in its own `generateImage()` output and use `matchImagePoints`.

### 9.4 Others

| Problem | Fix |
|---|---|
| `float(tvec[2])` TypeError | `solvePnP` returns `(3,1)`; use `np.asarray(tvec, np.float64).reshape(3)` |
| `pair_skew_exceeded` in fake_phone | rendering ran *between* the two devices' timestamps → `async def warm(pose_seed)` with `asyncio.to_thread` before stamping |
| `depth_dims_mismatch` code mismatch | the **fixture declaration** was wrong, not the decoder — buffer range is valid, the header disagrees with the descriptor |
| "Unable to locate a Java Runtime" | JDK 21 present but off `PATH` (see §5) |
| F7 not working | macOS media key — needs `Fn`+F7; permanently solved by rebinding |
| `P` says "Social Interactions are only available in multiplayer" | vanilla binding; probe moved to `R` |
| NaN in JSON headers | `json.loads(header_text, parse_constant=_reject_json_constant)` rejects non-finite constants |

---

## 10. The interoperability gate

`docs/architecture.md` describes `contracts/fixtures` as *"the interoperability
gate: Swift, Python, and Java must decode the same golden packets."*

**It was fictional.** Java was only checking its own output. Python never touched
the fixtures, so a Python/Java disagreement would not surface until a real frame
failed to decode in the mod.

Now real:

- `backend/tests/test_cross_language_fixtures.py` (35 tests) — Python decodes all 3
  Java-authored CHARACTER_FRAME goldens and rejects all malformed fixtures with the
  declared code.
- `backend/tests/test_rgbd_fixtures.py` (29 tests) — same for the phone → backend
  RGBD direction, plus `SHA256SUMS` integrity checks. Buffer comparison uses raw
  SHA-256 rather than decoded pixels, because JPEG decoding differs between libjpeg
  builds.

It **passed on the first run**, which means Python and Java genuinely agree.

```bash
cd backend
uv run pytest tests/test_cross_language_fixtures.py tests/test_rgbd_fixtures.py
```

Regenerate RGBD fixtures only deliberately (they are frozen — review the diff):
`uv run python scripts/write_rgbd_fixtures.py`

---

## 11. Hardware-free rehearsal

`backend/scripts/fake_phone.py` provides `FakePhone`, `CharacterSubscriber`, and
`Rig`. Flags: `--write-calibration`, `--once`, `--interval`, `--expect-frame`,
`--skew-ms`, `--devices`, `--url`, `--calibration`.

```bash
cd backend
uv run python scripts/fake_phone.py --write-calibration data/calibration.json
uv run uvicorn hmc_backend.api.app:app --host 0.0.0.0 --port 8000 --workers 1
uv run python scripts/fake_phone.py --once --expect-frame   # non-zero on failure
```

The harness connects as real WebSocket clients, answers `clock_ping`, and honours
`capture_request` — so with Minecraft attached, the capture key drives a real
synchronized capture. Documented in `docs/workflows/05-end-to-end-runbook.md` §10.

**What it cannot prove:** sensor accuracy, real lens distortion, motion blur,
lighting, clock behaviour across two physical devices, or whether the tripods
actually see the subject. Gates 3–6 still require the rig.

### Measured pipeline performance

decode → pair → reconstruct → fit → publish:
**6 ms @ 320×240**, **17 ms @ 640×480**.

---

## 12. Live state at the end of this session

Three background processes running:

| What | Detail |
|---|---|
| Backend | port **8090**, calibration `a59cbaf1`, `latest_frame_id 9` |
| Fake phones | two connected via `scripts/fake_phone.py --url ws://127.0.0.1:8090` |
| Minecraft client | auto-joined "New World" via `--quickPlaySingleplayer`, anchored `y=72`, `blocksPerMeter=8.0` |

Port **8000** is occupied by an unrelated backend — hence 8090.

**Tests: 141 Python, 60 Java, all passing.**

---

## 13. iOS app — getting the real phone connected

`ios-capture/HumansCapture.xcodeproj` · targets **HumansCapture** and
**HumansCaptureTests** · `IPHONEOS_DEPLOYMENT_TARGET = 17.0`

`Info.plist` already has `NSAllowsLocalNetworking`, `NSCameraUsageDescription`,
and `NSLocalNetworkUsageDescription` — all correct.

```swift
// ios-capture/HumansCapture/State/CaptureStore.swift
@AppStorage("capture.device_id")   var deviceID = "front-phone"
@AppStorage("capture.backend_url") var backendURL = "ws://192.168.1.2:8000/ws/capture"
```

### Signing changes made (uncommitted, local only)

| Setting | Was | Now |
|---|---|---|
| `DEVELOPMENT_TEAM` | `85CXC23NDS` (Jonathan's) | `G3WH4Z3Y7C` (Adrian's) |
| `PRODUCT_BUNDLE_IDENTIFIER` | `com.jonathan.HumansCapture` | `com.adrianshahnazari.HumansCapture` |

The bundle ID **had** to change — `com.jonathan.HumansCapture` is registered as an
explicit App ID under team `85CXC23NDS`, and two teams cannot claim the same one.

> ⚠️ **Do not commit these two edits** — they break Jonathan's build.
> The `HumansCaptureTests` target still has no `DEVELOPMENT_TEAM`; it only matters
> if you build the test target.

### Xcode navigation gotcha

**Signing & Capabilities only appears when a _target_ is selected, not the project.**
`⌘1` → click the blue **HumansCapture** project icon at the top → select the
**HumansCapture** target under `TARGETS` → the tab appears. If the editor pane is
narrow, the PROJECT/TARGETS column collapses into a dropdown in the editor's
top-left corner.

### Install sequence

1. Pick the iPhone in the device dropdown, `⌘R`.
2. Expect *"Developer Mode disabled"*. **Developer Mode only appears in iOS Settings
   after you first try to install a dev app.** Then:
   Settings → Privacy & Security → Developer Mode → On → reboot → `⌘R` again.
3. Expect *"Untrusted Developer"*: Settings → General → VPN & Device Management →
   Adrian Shahnazari → Trust.

### In-app settings

- Backend URL: `ws://10.37.104.178:8090/ws/capture` (laptop LAN IP, en0)
- Device ID: **`side-phone`** — *not* `front-phone`, because a fake phone currently
  holds `front-phone` and the real one would evict it and break the pair.

Phone and laptop must be on the **same Wi-Fi**. The USB-C cable carries only the
Xcode install, not the capture stream.

> ⚠️ The loaded calibration is **synthetic**, so anything the real phone produces
> will be geometrically wrong. This run proves transport + decode (**Gate 3**) and
> nothing about accuracy.

---

## 14. Open items

- [ ] Finish getting the real iPhone installed and streaming
- [ ] Open a PR for `feat/preflight-integration` (8 commits pushed)
- [ ] Open a PR for `docs/hardware-integration-plan`
- [ ] Merge PR #5 (Workflow 3) and PR #6 (calibration)
- [ ] Decide what to do with PR #3 (duplicate iOS implementation)
- [ ] Accuracy measurement against PR #5's `fixtures/skeleton.py` ground truth
      *(blocked on merge)*
- [ ] Add a `--devices side-phone` mode to `fake_phone.py` so the real phone can
      cleanly take `front-phone`
- [ ] Real ChArUco calibration to replace the synthetic one

---

## 15. Acceptance gates (from the runbook)

| Gate | Requirement |
|---|---|
| 1 | Contract fixture — all languages agree on the same golden bytes ✅ |
| 2 | Synthetic backend → Minecraft renders a person ✅ |
| 3 | One real phone — RGB/depth/mask align, metric scale plausible. *A simulator does not pass.* |
| 4 | Anatomy on held poses — hand association, depth support, 3D landmarks |
| 5 | Two-camera calibration and merge — no double torso, correct left/right |
| 6 | Real Minecraft snapshot — client/server frame IDs agree |
| 7 | Observability evidence — find one real bottleneck, fix it, show before/after on the same saved input |

---
---

# Part 2 — Hardware bring-up session (afternoon/evening, 2026-09-19)

Chronological record of the real-iPhone session that followed Part 1.
All changes below are **uncommitted** on `feat/preflight-integration`.

## 16. Timeline

| Time | Event |
|---|---|
| 15:49 | Reviewed state; backend on 8090, fake phones on both IDs |
| 16:36 | iPhone 16 Pro Max not in Xcode destination list → **Developer Mode disabled** on phone |
| 17:00 | Developer Mode enabled, paired |
| 17:02 | Fake harness restarted with `--devices front-phone` so the real phone can take `side-phone` |
| 17:04 | Moved everything from 8090 → **8000**; Minecraft launched with `--quickPlaySingleplayer 'New World'` |
| 17:05 | "Untrusted Developer" → Settings → General → VPN & Device Management → Trust |
| 17:08 | Phone connected, `ready` |
| 17:11 | **Bug A** `invalid_message – invalid RGBD header: 2 error(s)` |
| 17:15 | **Bug B** `G` did nothing — `options.txt` still cached F7 |
| 17:18 | **Bug C** `pair_rejected: pair_skew_exceeded` — clock probe loop never implemented |
| 17:22 | **Bug D** skew 184 ms over Wi-Fi > 50 ms budget |
| 17:31 | Synthetic frame 13 injected to prove rendering; silhouette seen, 14 blocks tall (`blocksPerMeter=8`) |
| 17:40 | Real capture rendered for the first time (person close to the phone) |
| 17:47 | **Bug E** person upside down |
| 17:51 | **Bug F** whole room rendered, not just the person |
| 17:55 | Reference photo (Dream/Sapnap volumetric capture) — target look |
| 17:58 | Voxel density raised, point size raised, colliders/skeleton hidden |
| 18:01 | Portrait phone orientation support |
| 18:05 | `L` collided with vanilla Advancements → point size on `.` / `,` |
| 18:11 | Wireless brainstorm; Bonjour auto-discovery implemented |
| 18:17 | Keep-awake (`isIdleTimerDisabled`) |
| 18:24 | Live-streaming attempt (15 FPS, `V` key, `require_capture_id=False`) — **broke everything** |
| 18:27–18:30 | Rolled back live streaming twice |
| 18:34 | Still nothing visible → this diagnosis |

## 17. Bugs found and fixed with the real phone

### A. `invalid RGBD header: 2 error(s)`
`contracts/rgbd.py` has `extra="forbid"`; the phone sends `mirrored` and
`rgb_depth_mapping` which the model lacked. Added both fields.

### B. `G` not firing
`minecraft-mod/run/options.txt` had the pre-rebind `F7` cached. Rewrote the
`key_key.humancraft.*` lines and restarted the client.

### C. `pair_skew_exceeded` (thousands of seconds)
Phone timestamps are seconds-since-boot; Mac is `time.monotonic()`. The backend's
`_clock_probe_loop` that was supposed to measure the offset was never implemented.
Implemented it: 4 rapid probes on connect, then every 2 s. Result:
`[CLOCK-PONG] offset=0.00006s uncert=0.23ms`.

### D. `pair_skew_exceeded` (184 ms)
Real Wi-Fi + ARKit latency was ~184 ms against a hard 50 ms budget.
Snapshot budget raised to **1000 ms** (`capture/pairing.py`); outlier clock samples
auto-cleared on reconnect (`capture/clock.py`).

### E. Upside down
`calibration/synthetic.py::look_at_optical` mapped optical `+Y` (image-down) to
stage `+Y` (up). The synthetic fixture cancelled its own bug because it both
projected and unprojected with the same matrix. Fixed sign; regenerated
`data/calibration.json`.

### F. Background rendered
`vision/fake.py::FakePersonMaskDetector` marked every pixel as person. Replaced
with LiDAR depth segmentation: foreground depth-peak detection, adaptive
depth band-pass (~0.4–0.6 m body thickness), morphological close +
`cv2.connectedComponentsWithStats`, keep components ≥ 3 % so hands/feet survive.
**Verified working by the user.**

### G. Look tuning (settings.py / humancraft.json / reconstruct.py)
- `voxel_size_m` 0.015 → **0.006**, `max_points` 50 000 → **80 000**
- Minecraft `pointSize` 3.0 → 5.5 (user later at 10.0), `blocksPerMeter` ≈ 3.39
- centred sub-pixel RGB sampling in `reconstruction/reconstruct.py`
- `showColliders=false`, `showSkeleton=false` by default (toggle `U`/`I`)

### H. Portrait capture
`Info.plist` `UIInterfaceOrientationPortrait`; `.portrait` in `CaptureDTOs.swift`,
`FrameEncoder.swift`, `CaptureStore.swift`; portrait axes in `synthetic.py`.

### I. Keybindings added
`.` point size +0.5, `,` point size −0.5. (`L`/`K` rejected: `L` = Advancements.)

### J. Bonjour auto-discovery
- `backend/src/hmc_backend/api/discovery.py` — advertises `_hmc._tcp` on 8000 via
  `dns-sd` in the FastAPI lifespan. Verified with `dns-sd -B _hmc._tcp local`.
- `ios-capture/HumansCapture/Transport/BackendDiscovery.swift` — `NWBrowser`,
  resolves the host, one-tap Connect in `CaptureView.swift`.
- `Info.plist` gains `NSBonjourServices: _hmc._tcp`.

### K. Keep-awake
`CaptureStore.swift`: `UIApplication.shared.isIdleTimerDisabled = true` while
`socketState == .ready || isRunning`; restored on disconnect/background.

### L. Live streaming — attempted and REVERTED
15 FPS `liveIntervalSeconds=0.066`, JPEG 0.65, `V` key, `require_capture_id=False`.
Flooded Minecraft with empty live frames (500 ms TTL → flicker/vanish).
Reverted: `factory.py` `require_capture_id=True`, `V` removed, `fake_phone.py`
streaming loop removed, `BufferDescriptor.java` reverted.
`fake_phone.py` gained a **`--blank`** flag (sends empty depth so the fake
`front-phone` contributes no points and the real `side-phone` is the only source).

## 18. Diagnosis of "still not working" (18:40)

State at diagnosis — everything actually healthy end-to-end:

```
/health → status ready, calibration f69f78b1, both devices connected,
          clock_ready true, latest_frame_id 237
Minecraft log → "install frame=198…237 result=accepted"  (138 accepted total)
             → 23× "buffer 'points' shape dims must be positive" (0-point frames,
               i.e. nobody in the LiDAR foreground; harmless client-side reject)
```

**Root cause: `minecraft-mod/run/config/humancraft.json` had `anchorY: -60.0`.**
Frames were accepted and rendered 130 blocks underground. The JOIN-deferred
anchor (§9.1, `ANCHOR_SETTLE_TICKS = 5`) tried **once**; on this quick-play
launch the player position was still unsynced after 5 ticks, `setAutomaticAnchor`
returned `false`, and `tick()` never retried — leaving the stale -60 from the
persisted config.

**Fix** (`ClientSnapshotCoordinator.tick`): re-arm `anchorPendingTicks` when the
attempt fails, so it retries every 5 ticks until the position is real. Mod rebuilt,
Minecraft relaunched.

Secondary observations:
- Backend stdout goes to the tty of the previous session's terminal; it is not
  captured to a file. Use `curl :8000/health` and `minecraft-mod/run/logs/latest.log`.
- Frames arriving in bursts at ~5 FPS after the rollback means the phone still had
  "Start live recapture" on; all frames go through `handle_rgbd(mode="snapshot")`
  so they do **not** expire — that theory was ruled out.

## 19. How to run it (current, port 8000)

```bash
cd backend
uv run uvicorn hmc_backend.api.app:app --host 0.0.0.0 --port 8000 --workers 1
```
```bash
cd backend
uv run python scripts/fake_phone.py --url ws://127.0.0.1:8000 --devices front-phone --blank
```
```bash
cd minecraft-mod
export JAVA_HOME=/opt/homebrew/opt/openjdk@21 PATH="$JAVA_HOME/bin:$PATH" && ./gradlew runClient --args="--quickPlaySingleplayer 'New World'"
```

Phone: HumansCapture → Connect (Bonjour) or `ws://<mac-lan-ip>:8000/ws/capture`,
Device ID **`side-phone`**. Person 1.5–2.5 m away, phone stationary, portrait or
landscape.

In-game: `G` capture · `B` anchor in front of you (once) · `R` probe · `.`/`,`
point size · `=`/`-` scale · arrows `[` `]` nudge · `O` `I` `U` `Y` `H` toggles ·
`C` clear · `J` reconnect.

## 20. Uncommitted changes (do not lose)

```
M backend/scripts/fake_phone.py              (--blank)
M backend/src/hmc_backend/api/app.py         (discovery lifespan, clock probes)
M backend/src/hmc_backend/api/runtime.py     (clock probe loop, HANDLE-RGBD debug prints)
M backend/src/hmc_backend/calibration/synthetic.py (Y flip, portrait axes)
M backend/src/hmc_backend/capture/clock.py   (outlier clearing)
M backend/src/hmc_backend/capture/pairing.py (1000 ms snapshot budget)
M backend/src/hmc_backend/contracts/rgbd.py  (mirrored, rgb_depth_mapping)
M backend/src/hmc_backend/reconstruction/reconstruct.py (centred RGB sampling)
M backend/src/hmc_backend/settings.py        (voxel 6 mm, 80k points)
M backend/src/hmc_backend/vision/fake.py     (depth segmentation)
M backend/tests/test_api.py
?? backend/src/hmc_backend/api/discovery.py
M ios-capture/HumansCapture.xcodeproj/project.pbxproj   ← contains Adrian's signing; DO NOT COMMIT those two lines
M ios-capture/HumansCapture/{Capture/ARCaptureController,Encoding/FrameEncoder,Protocol/CaptureDTOs,State/CaptureStore,UI/CaptureView}.swift
M ios-capture/HumansCapture/Resources/Info.plist
?? ios-capture/HumansCapture/Transport/BackendDiscovery.swift
M minecraft-mod/.../input/HumanCraftKeybindings.java   (. and ,)
M minecraft-mod/.../state/ClientSnapshotCoordinator.java (anchor retry)
```

Before committing: remove the `[HANDLE-RGBD]` / `[CLOCK-PONG]` / `[PAIR-SKEW-DEBUG]`
print statements from the backend, and strip the signing lines from `project.pbxproj`.
