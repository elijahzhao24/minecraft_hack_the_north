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
4. Swing either hand; detection starts after a baseline frame. Default trigger speed is
   **1.5 meters/second relative to its shoulder**. Fast movement can trigger again after the cooldown.
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
shoulder and wrist and does not itself trigger a hit. Observed body wrists, hand wrists, and elbows are checked independently for each arm.
Implausible arm lengths/speeds, low confidence, occluded model-prior landmarks,
and gaps over 500 ms are rejected. Adding another contributing camera does not
disarm detection; a large jump to a completely different camera is ignored for
one sample. There is no slow-down reset or fixed per-frame displacement minimum. Very brief gestures between
capture frames can be missed; use a deliberate visible swing.

## Alignment and physical checks

Scan points, landmarks, and colliders share one transform anchored on the dense
body region. A smoothed horizontal density grid finds that region; its middle
height band sets the center and its trimmed vertical bounds set the floor and
initial scale. Sparse distant points and extended arms cannot pull that center
away. Feet refine the floor only when they agree with the visible cloud.

The cloud draws inside Minecraft's player renderer. Its VBO shader receives
**camera model-view × entity pose**: Minecraft stores the camera transform
separately from the entity's pose stack. Omitting that factor made the scan
appear displaced from the correctly rendered shadow as the camera turned.
Press **U** to see a cyan ground cross at the actual entity origin, and **H**
to see how many points contributed to the dense-body anchor. Use F3+B to compare
with Minecraft's ordinary entity bounding box. Install the updated JAR and
restart Minecraft before checking the change.

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


## Mouse movement and capture rate

With a scan active and the game focused, hold **mouse button 4** to turn left
or **button 5** to turn right at 30°/second. Normal mouse looking still works.
Left/right clicks are handled by Minecraft's normal bindings. In Options →
Controls, bind **Walk Forwards** to left click and **Walk Backwards** to right
click, and move **Attack/Destroy** and **Use Item/Place Block** to other bindings
to avoid conflicts. In controlled separate mode, those movement bindings and
side-button turns steer HumanScan. Menus retain their normal clicks.

Defaults in `config/humancraft.json` are `mouseMovementEnabled: true`,
`mouseTurnDegreesPerSecond: 30`, and `liveRateHz: 15`. Set
`HUMANCRAFT_MOUSE_MOVEMENT_ENABLED=false` to disable side-button turning.
The previous 8 FPS config default is migrated once to 15 FPS. The backend also
defaults to 15 capture requests/second; actual delivered FPS depends on phone,
network, and inference throughput. Restart both backend and Minecraft after
installing the updated mod.


## Survival and PvP status

In `/humancraft mode self`, the scan replaces your existing player visually.
Survival damage, armor, health, death, and ordinary PvP still belong to that
Minecraft player. The cloud does not make the player invulnerable.

In separate mode the internal `HumanScan` player is currently explicitly
invulnerable (`AvatarService.spawnSeparate`), despite being assigned Survival
mode. It can deliver detected punches, but does not currently take normal damage
or die from PvP. Changing your observer's game mode does not remove that flag.

Physical punches use five-block 3D distance between entity positions and the
forward horizontal 180° half-space, plus line of sight. Every eligible living
target in that region receives 1 damage (half a heart before armor) and knockback,
with a shared 500 ms cooldown. PvP/team and ordinary damage-immunity rules still
apply. These checks use the server's player position/yaw, not the displayed
cloud's apparent position or individual points. Ordinary mouse/weapon attacks
continue to use Minecraft's normal reach and damage rules.
