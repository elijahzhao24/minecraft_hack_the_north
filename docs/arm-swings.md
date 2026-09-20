# Physical arm swings

The Minecraft client detects swings from the backend's MediaPipe 3D shoulder and
wrist landmarks. No mouse click is needed. Each swing applies an ordinary fist
hit (1 damage, half a heart before armor) to every eligible living target in a
180-degree arc ahead of the scanned player, up to 5 blocks away. Both arms work.
The server determines targets, reach, facing, obstruction, and damage. Blocks,
dropped items, allies, spectators, and the controlling observer are not targets;
normal PvP rules and damage immunity still apply. Held weapons do not increase
the damage. Solid walls block hits.

## Start and use

1. Start the real vision backend, following [backend setup](../backend/README.md).
   Fixture mode is the default and does not track your physical arms:

   ```bash
   cd backend
   uv run python scripts/pin_models.py
   HMC_VISION_BACKEND=mediapipe HMC_COLLIDER_BACKEND=anatomical \
     uv run uvicorn hmc_backend.api.app:app --host 0.0.0.0 --port 8000 --workers 1
   ```

2. Complete [camera calibration](camera-calibration.md), then step into view.
   Keep both shoulders and wrists visible. Press **V** to enable live capture.
   A still snapshot cannot detect motion.
3. Use `/humancraft mode self` to attach the scan to your own player. Press **F5**
   for third-person inspection and `/humancraft normalize` while standing upright
   with your whole body visible to reset its scale.
4. Briefly hold your arms still, then swing either hand. Default trigger speed is
   **1.5 meters/second relative to its shoulder**. Slow down before swinging again.
   At most one attack is allowed every **500 ms**, shared across both arms.
5. Press **H** to show the HUD. It reports left/right speeds, the trigger threshold,
   and the last detected swing (this records detection, not a guaranteed hit).
   `untracked / warming up` means more reliable observations are needed.
   **I** toggles the arm skeleton; **U** toggles collider outlines.

In separate mode (`/humancraft mode separate`), the scan belongs to `HumanScan`.
Your own vanilla player remains visible; this is intentional. Use
`/humancraft control` to steer HumanScan's position and facing. Physical swings
attack from HumanScan even after you release keyboard control. Switch back to
`/humancraft mode self` if you want the scan to replace your own body.

## Tuning

Edit `config/humancraft.json` in your Minecraft game directory and restart:

```json
{
  "armSwingEnabled": true,
  "armSwingSpeedMps": 1.5,
  "armSwingCooldownMs": 500
}
```

These are fields to merge into the existing config. Under `./gradlew runClient`,
the file is `minecraft-mod/run/config/humancraft.json`. Environment overrides are
`HUMANCRAFT_ARM_SWING_ENABLED`, `HUMANCRAFT_ARM_SWING_SPEED_MPS`, and
`HUMANCRAFT_ARM_SWING_COOLDOWN_MS`. Allowed speed range is 0.3–10 m/s;
cooldown is clamped to 500–5000 ms. Reach remains fixed at five blocks, measured
between entity positions in 3D; facing uses Minecraft yaw, so looking down does
not rotate the horizontal arc.

Speed uses physical meters before avatar scaling. Walking translates both the
shoulder and wrist and does not itself trigger a hit. The wrist must move at
least 8 cm between samples; implausible arm lengths/speeds, low confidence,
occluded model-prior landmarks, and gaps over 350 ms are rejected. Detection
rearms below 40% of the trigger speed. Camera-source changes and reacquisition
need a fresh baseline followed by a slow sample. Very brief gestures between
capture frames can be missed; use a deliberate visible swing.

## Alignment and physical checks

Scan points, landmarks, and colliders share the same hip-centered transform.
Scale stays fixed until normalization or a new session/calibration; missing hips
retain the last root instead of centering on whichever surface remains visible.
Feet provide the floor when tracked; otherwise the previous floor is retained.
The renderer uses Minecraft's interpolated player position. The vanilla skin is
hidden only while its replacement cloud is available and enabled.

Automated tests cover velocity, frame/session guards, cooldown, arc boundaries,
and normalization. Verify these remaining checks on the real rig:

- Standing still and walking should not produce attacks. Swing each arm separately.
- Place mobs in front, to the sides, behind, and beyond five blocks; only eligible
  targets in the forward half-sphere should take damage. Test a wall and both hands
  moving together.
- Cover a wrist, stop the stream, or change calibration: no ghost punches on
  reacquisition. Let tracking settle before testing another swing.
- In self mode, use F5 and inspect the hips over the player/shadow while walking,
  turning, and extending each arm. Use O to compare the scan with the vanilla skin.
  In separate mode, verify the scan follows HumanScan, not the observing player.
