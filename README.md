# Humans in Minecraft

Capture a real person with two LiDAR iPhones and use the RGB point cloud as the body of a real Minecraft player. Minecraft keeps player health, inventory, movement, gravity, collision, damage, and respawn while HumanCraft supplies appearance and anatomical hit volumes.

## Repository status

Workflow 1 now includes a runnable Xcode project under [`ios-capture/`](ios-capture/README.md). The Python service, Fabric mod, model assets, physical calibration, and real-device fixtures are not yet present. The documents below define the shared contracts and the remaining implementation lanes.

Workflow 4 is implemented as the runnable `minecraft-mod/` Fabric project. It includes deterministic golden fixtures, strict `HMC1` decoding, stage-to-world conversion, a reconnecting backend client, batched point-cloud/debug rendering, integrated-server snapshot ownership, body-part ray hits, hand/foot contact queries, controls/HUD, and optional Sentry telemetry. The built-in fixture keeps this workflow independent of phones and the Python backend.

The Python backend includes selectable fixture and production vision pipelines. Production mode uses checksum-pinned MediaPipe Pose and Hand models plus anatomical collider fitting.

The original product and acceptance brief remains in [minecraft_human_mvp_agent_brief.md](minecraft_human_mvp_agent_brief.md). Start implementation from the narrower documents in `docs/`:

| Document | Purpose |
|---|---|
| [Project context and architecture](docs/architecture.md) | Boundaries, owners, runtime topology, data flow, state, and proposed repository layout |
| [Wire contracts and DTOs](docs/contracts.md) | Normative binary envelope, JSON headers, control messages, Python DTOs, and Fabric payloads |
| [Workflow 1 — iPhone capture](docs/workflows/01-ios-capture.md) | Xcode/ARKit setup, buffer handling, transport, and capture acceptance tests |
| [Workflow 2 — Python backend](docs/workflows/02-python-backend.md) | FastAPI setup, endpoints, queues, calibration, reconstruction, publication, and tests |
| [Workflow 3 — Vision and colliders](docs/workflows/03-vision-and-colliders.md) | MediaPipe models, 2D-to-3D registration, landmark DTOs, and collider fitting |
| [Workflow 4 — Minecraft integration](docs/workflows/04-minecraft-fabric.md) | Fabric setup, client rendering, client/server synchronization, hit and contact queries |
| [End-to-end runbook](docs/workflows/05-end-to-end-runbook.md) | Startup order, calibration/capture/demo sequences, failure isolation, and gate checklist |

## Chosen implementation baseline

These are implementation decisions, not evidence of existing code:

- iOS: Xcode 16.4, Swift 6, iOS 17+, SwiftUI, ARKit scene depth, `URLSessionWebSocketTask`, and physical LiDAR-capable iPhones.
- Backend: Python 3.12, FastAPI, Uvicorn, Pydantic v2, NumPy, OpenCV, and MediaPipe Tasks.
- Game: Minecraft Java 1.21.1, Fabric Loader 0.19.5, Fabric API `0.116.17+1.21.1`, Fabric Loom 1.17.21, Gradle 9.5.1 wrapper, and JDK 21. Keep these pins together and upgrade them as one tested set.
- Transport: versioned `HMC1` binary messages over WebSockets. Bulk images, depth, and points remain binary; small commands and results are JSON text messages.
- Units/frame: meters in a calibrated stage frame until the Minecraft boundary; one shared transform converts every point, landmark, and collider to blocks.

## First implementation slice

Build a vertical slice before connecting real hardware:

1. Implement the shared encoder/decoder and commit a synthetic `HMC1` fixture.
2. Make the Python service replay that fixture and publish one `CharacterFrame`.
3. Render it in Minecraft and make the integrated server accept its collider snapshot.
4. Add one server-authoritative probe hit/miss.
5. Replace the synthetic input with one real iPhone packet, then add the second camera and vision fitting.

No workflow may invent a locally convenient schema. Contract changes begin in [docs/contracts.md](docs/contracts.md), increment the relevant schema version, and update fixtures in all three languages.

## Build and run Workflow 4

On Ubuntu, install a JDK 21 distribution, then use the committed wrapper:

```bash
sudo apt-get update
sudo apt-get install -y openjdk-21-jdk
cd minecraft-mod
java -version
./gradlew clean test build
./gradlew runClient
```

The remapped distributable is `minecraft-mod/build/libs/humancraft-0.1.0.jar`. `runClient` supplies Fabric API on the development classpath. For a normal launcher profile, use Minecraft 1.21.1 with Fabric Loader 0.19.5 and install both that HumanCraft JAR and Fabric API `0.116.17+1.21.1`.

HumanCraft creates `run/config/humancraft.json` under `runClient` (or `.minecraft/config/humancraft.json` in a launcher profile). The backend defaults to `ws://127.0.0.1:8000/ws/character`; change `backendUrl` or set `HUMANCRAFT_BACKEND_URL`. Live-mode frames are hidden and made non-interactive after 500 ms; snapshot-mode frames persist.

The scan starts bound to your own player. These commands manage the two supported modes:

```text
/humancraft mode self
/humancraft mode separate
/humancraft spawn
/humancraft despawn
/humancraft control
/humancraft release
/humancraft calibrate
/humancraft debug on|off
```

Separate mode creates a server-controlled player named `HumanScan`; it does not require another Minecraft account. While controlled, WASD, look, jump, sneak, and sprint are sent as intent to the logical server.

Sentry is off by default and never gates gameplay. To enable crash/error reporting, Logs, and performance spans without putting a DSN in git or the JSON file:

```bash
export HUMANCRAFT_SENTRY_DSN='https://public-key@your-sentry-host/project-id'
export HUMANCRAFT_SENTRY_ENVIRONMENT='local-visual-test'
export HUMANCRAFT_SENTRY_TRACES_SAMPLE_RATE='1.0'
./gradlew runClient
```

The DSN environment value is deliberately blanked whenever config is saved.

## Local visual and hitbox verification

Cloud CI can load the client and validate entrypoints, but final rendering and input acceptance should be done locally with a GPU:

1. Run `cd minecraft-mod && ./gradlew runClient`. No Microsoft login is required for the development client.
2. Create a single-player Creative flat world. With `fixtureOnStart: true`, the neutral fixture replaces your player model even if no backend is running. Use third-person view to inspect it.
3. Confirm the HUD reaches `active frame 0`, reports 6,000 points, 77 landmarks, and 15 colliders. Verify the colored surface is depth-tested; skeleton lines and collider wireframes stay aligned while walking around it. Hands are magenta and feet orange in the collider overlay.
4. Aim directly at torso, each hand, and each foot and press `P`. Confirm the chat/HUD reports the nearest exact body part. Aim through the arm/torso gap and confirm `MISS`.
5. Place a solid wall between the player and human, aim through it, and press `P`; confirm `BLOCK_OCCLUDED`. Remove the wall and confirm the same aim can hit again.
6. In the neutral fixture, check the HUD contact count while both foot OBBs meet the flat floor. Place a cube against a hand and verify contact updates. This is a query indicator, not physical collision response.
7. Walk, turn, jump, collide with walls and stairs, and take knockback. The cloud and colliders must follow the player together. Run `/humancraft mode separate`, then `/humancraft control`, and repeat with the internal player.
8. Toggle cloud/skeleton/colliders with `O`/`I`/`U`, the registration-color diagnostic with `Y`, and the HUD with `H`. Use F6 to reconnect, F7 to request capture, and F8 to clear. All bindings are remappable under Options → Controls → HumanCraft.
9. Leave the world running for ten minutes while toggling overlays and reconnecting. Confirm frame age/status remains sane and `run/logs/latest.log` has no renderer, buffer, or stale-interaction error.

For real backend integration, start the backend first, set its character WebSocket URL, run the same client command, and follow [the end-to-end runbook](docs/workflows/05-end-to-end-runbook.md). The backend must emit the versioned schema in [the contracts document](docs/contracts.md); the client rejects unknown versions and malformed/oversized frames while retaining the last accepted snapshot.
