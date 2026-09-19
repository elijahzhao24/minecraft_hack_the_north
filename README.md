# Humans in Minecraft

Capture a real person with two LiDAR iPhones, reconstruct a colored 3D snapshot, fit body-part hit volumes, and render/interact with that snapshot inside Minecraft Java Edition.

## Repository status

Workflow 1 now includes a runnable Xcode project under [`ios-capture/`](ios-capture/README.md). The Python service, Fabric mod, model assets, physical calibration, and real-device fixtures are not yet present. The documents below define the shared contracts and the remaining implementation lanes.

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

- iOS: Xcode 16.4, Swift 6, iOS 17+, SwiftUI, ARKit scene depth, `URLSessionWebSocketTask`, Sentry Cocoa 9.24.0, and physical LiDAR-capable iPhones.
- Backend: Python 3.12, FastAPI, Uvicorn, Pydantic v2, NumPy, OpenCV, MediaPipe Tasks, and Sentry.
- Game: Minecraft Java 26.2, Fabric Loader 0.19.5, Fabric API `0.160.0+26.2`, Fabric Loom `1.17-SNAPSHOT`, and JDK 25. Those values match the official Fabric example template checked on 2026-09-19; keep all pins together and upgrade them as one tested set.
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
