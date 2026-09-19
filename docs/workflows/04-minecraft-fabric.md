# Workflow 4 — Minecraft Fabric rendering and interaction

## Outcome and boundary

The Fabric mod receives a processed `CharacterFrame`, renders its point cloud and debug geometry on the physical client, and sends the small collider snapshot to Minecraft's logical server. The logical server owns active hit/contact state and makes every authoritative result.

This is true in singleplayer: Fabric's networking documentation notes that an integrated singleplayer game still has separate logical client/server sides. See [Fabric networking](https://docs.fabricmc.net/develop/networking).

## Version baseline

Pin this set together in `minecraft-mod/gradle.properties`:

```properties
minecraft_version=26.2
loader_version=0.19.5
loom_version=1.17-SNAPSHOT
fabric_api_version=0.160.0+26.2
```

Use JDK 25 and Gradle/toolchain settings from the matching official Fabric 26.2 example template. These values were checked against the [official example mod](https://github.com/FabricMC/fabric-example-mod/tree/26.2) on 2026-09-19. Fabric/Minecraft APIs change frequently; an upgrade means updating the whole set, `fabric.mod.json`, source APIs, fixtures, and this document after a clean build.

## Bootstrap and run

1. Install a JDK 25 distribution and set the IDE project SDK/toolchain to it.
2. Generate/copy the Fabric example project for Minecraft 26.2 into `minecraft-mod/`.
3. Set mod ID `hmc`, Maven group `dev.hmc`, Java package `dev.hmc`, and environment `*` because common logical-server code and client code are both required.
4. Keep separate `main` and `client` entrypoints in `fabric.mod.json`.
5. Add the Sentry Java dependency only after verifying its version supports Logs; keep DSN/config external. Prefer JDK `java.net.http.WebSocket` and the JSON library already selected by the project rather than adding a second networking stack.
6. Confirm the untouched skeleton works:

```bash
cd minecraft-mod
./gradlew runClient
./gradlew test
./gradlew build
```

The distributable JAR is the shortest normal JAR in `build/libs/`. For manual play, create a Minecraft 26.2 Fabric profile with compatible Loader and Fabric API, then place both the mod JAR and matching Fabric API JAR in the profile's `mods/` directory. The Gradle `runClient` task handles the development classpath automatically.

Suggested source layout:

```text
src/
├── main/java/dev/hmc/
│   ├── HmcMod.java
│   ├── config/HmcConfig.java
│   ├── geometry/               # immutable world-space shapes and queries
│   ├── payload/                # Fabric CustomPacketPayload records/codecs
│   ├── server/ActiveSnapshotStore.java
│   ├── server/ProbeService.java
│   └── server/ContactService.java
├── client/java/dev/hmc/client/
│   ├── HmcClient.java
│   ├── backend/CharacterWebSocket.java
│   ├── backend/HmcDecoder.java
│   ├── state/ClientSnapshotCoordinator.java
│   ├── render/PointCloudRenderer.java
│   ├── render/DebugGeometryRenderer.java
│   └── input/HmcKeybindings.java
└── main/resources/
    ├── fabric.mod.json
    └── assets/hmc/lang/en_us.json
```

## Mod configuration

Use a small JSON config under the Minecraft config directory:

```json
{
  "backend_ws_url": "ws://127.0.0.1:8000/ws/character",
  "client_id": "demo-laptop",
  "anchor_blocks": [0.5, 64.0, 0.5],
  "blocks_per_meter": 1.0,
  "max_message_bytes": 8388608,
  "snapshot_live_ttl_ms": 500,
  "probe_reach_blocks": 5.0,
  "render_cloud": true,
  "render_skeleton": true,
  "render_colliders": true
}
```

Validate config before joining a world. Anchor and scale changes create a newly transformed/installable snapshot; they do not independently move the visible cloud while leaving server colliders behind.

## Backend WebSocket client

`CharacterWebSocket` runs on its own single-thread executor and uses JDK `HttpClient.newWebSocketBuilder()`. Its listener:

1. Sends `character_hello` after open.
2. Accumulates fragmented binary callbacks into one bounded buffer until `last=true`.
3. Rejects a message beyond 8 MiB before growth, closes on invalid framing, and never passes partial bytes onward.
4. Sends completed bytes to a decode executor; no decode or JSON parsing happens on the render thread.
5. Handles text `character_server_hello`, `ack`, `error`, and control responses separately.
6. Reconnects with a capped delay while preserving the last server-accepted snapshot.

The recapture key sends the contract's `request_capture` text DTO with a new request/capture UUID. UI moves from dispatched to complete only when a later `CharacterFrame.source_frames` contains that capture ID; the immediate backend acknowledgement is not completion.

`HmcDecoder` follows the validation order in [../contracts.md](../contracts.md). It uses a little-endian duplicate/slice of the input buffer, strict DTO adapters, and checked arithmetic. Points remain in a direct/native buffer appropriate for GPU upload; colliders/landmarks become immutable Java records. It rejects unknown schema versions rather than guessing.

## Snapshot state machine

The client must not show a new cloud as interactive until the logical server accepts its exact colliders:

```text
RECEIVED -> DECODED -> TRANSFORMED -> SENT_TO_SERVER -> ACKNOWLEDGED -> ACTIVE
                    \-> REJECTED (last ACTIVE remains)
```

`ClientSnapshotCoordinator`:

1. Takes one decoded immutable frame.
2. Applies `world = anchor + scale * stage` to points, landmarks, and colliders in one function.
3. Builds `InstallSnapshotPayload` from the transformed colliders.
4. Sends it with `ClientPlayNetworking.send` on the client thread.
5. Retains a pending render snapshot keyed by `frame_id`.
6. On matching accepted `SnapshotAckPayload`, atomically swaps the active render snapshot and schedules one GPU upload.
7. On rejection/mismatched ack, drops pending and reports the code.

The client sends `character_ack` to the backend after WebSocket decode, but that does not activate gameplay. Only the logical-server ack does.

## Fabric custom payload registration

Implement the records from [../contracts.md](../contracts.md) with `CustomPacketPayload` and `StreamCodec`. Register client-to-server types using `PayloadTypeRegistry.serverboundPlay().register` and server-to-client types using `PayloadTypeRegistry.clientboundPlay().register` in the common initializer. Then register handlers with `ServerPlayNetworking.registerGlobalReceiver` and `ClientPlayNetworking.registerGlobalReceiver` on the correct sides.

The current official Fabric guide uses those APIs and explicitly requires server-side content validation. Network callbacks schedule state changes onto the relevant game/server context; they do not mutate renderer/server collections from an arbitrary socket thread.

## Server snapshot installation

`ActiveSnapshotStore` is owned by the logical server thread and scoped to the world/player session. On `InstallSnapshotPayload`:

- Require the sender to be the configured local owner for the MVP.
- Require increasing non-negative frame ID, at most 128 colliders, unique IDs, finite coordinates, scale within configured bounds, and geometry within a bounded region around anchor.
- Validate radii/extents, capsule length, and OBB orthonormality again even though the client already did.
- Construct immutable broad-phase AABBs from the exact narrow-phase shapes.
- Atomically replace the active state and send accepted ack. A failed installation keeps the previous state.

On world unload, logout, config ownership change, or explicit clear, remove active state. In live mode, also clear after TTL; snapshot mode intentionally persists.

## Rendering

### Point cloud

Do not create a block/entity per point. `PointCloudRenderer` owns one GPU vertex buffer for the active frame:

- Vertex: world position float32 plus RGBA8 (16 bytes if packed similarly).
- Upload only when active frame changes, on the render thread.
- Draw all points in a batch using the supported Fabric 26.2 world-render event and a shader/render pipeline with normal depth testing.
- Start with small camera-facing quads/point sprites. If point size portability is poor, instanced tiny cubes are a fallback; never issue tens of thousands of independent draw calls.
- Release the old GPU buffer on replace/world unload.

Rendering code is version-specific; implement against the [Fabric rendering guide](https://docs.fabricmc.net/develop/rendering/world) for the pinned version. Do not copy an older matrix stack/buffer builder snippet without compiling it against 26.2.

### Debug geometry

Draw skeleton lines and collider wireframes from the same active immutable records used to create the server payload. Color invalid landmarks/colliders differently or omit disabled interaction geometry. HUD text shows connection state, decoded/pending/active frame IDs, calibration ID, point/collider counts, last ack/rejection, and live age.

## Server-authoritative probe

Bind a probe key/item on the client. It sends only `(request_id, expected_frame_id)`.

On the server thread:

1. Require an active snapshot matching `expected_frame_id`.
2. Derive origin from the sending player's eye position and normalized direction from server-known rotation.
3. Use `min(configured_probe_reach, allowed_game_reach)`.
4. Raycast blocks over the same segment and record nearest blocking distance.
5. Test active collider AABBs for broad phase.
6. Run exact ray intersections for surviving sphere, finite capsule, and OBB shapes.
7. Keep the nearest valid `t >= 0` within reach. Deterministically break equal-distance ties by collider ID.
8. If the nearest block is closer than/equal to the human hit (within epsilon), return `BLOCK_OCCLUDED`; otherwise return the exact collider/body part/hit point.

The server ignores any client-rendered crosshair target. A large bookkeeping entity AABB never counts as a body hit.

### Geometry requirements

- Sphere: stable quadratic ray intersection, including origin inside.
- Capsule: finite segment swept sphere; test cylinder plus hemispherical caps, including degenerate/parallel cases.
- OBB: transform ray into OBB local coordinates using the transpose of its orthonormal axes, then slab-test `[-halfExtent,+halfExtent]`.
- Use one declared epsilon and test tangency. Reject negative/NaN distances.

## Contact target

Register or designate one full-cube test block. At a low server tick rate or when snapshot/target changes:

- Test only valid left/right hand and foot colliders.
- Sphere vs AABB: closest-point distance.
- Capsule vs AABB: exact/robust segment-to-box squared distance compared with radius squared.
- OBB vs AABB: separating-axis theorem, not enclosing-AABB overlap.
- Update the indicator block/state and send `ContactStatePayload` only when the touching part set changes (plus optional heartbeat).

This is a query/indicator, not physical collision response.

## Keybindings/debug controls

Provide bindings for reconnect, recapture request, clear, probe, toggle cloud, toggle skeleton, toggle colliders, toggle source colors, and move/reset anchor. Any anchor/scale change must reinstall and wait for ack before becoming visible/active.

## Sentry

Initialize Sentry Java with release/environment, trace sampling, and Logs enabled only when DSN is configured. Instrument WebSocket decode, GPU upload CPU duration, snapshot install/ack, broad/narrow phase, block occlusion, and contact query. Never label draw submission as GPU duration unless an actual GPU timer query exists.

Log one structured event per deliberate demo probe with frame ID, body part, result code, distance, and collider ID. Rate-limit connection/rejection repeats. The mod remains fully usable with no Sentry network access.

## Tests and completion gate

Pure JVM tests:

- `HMC1` golden snapshot decode and every malformed fixture.
- Stage→world mapping applied equally to point and all collider geometries.
- Sphere/capsule/OBB ray hits: direct, miss, tangent, inside origin, transformed/scaled, and nearest selection.
- OBB/capsule cube overlap, including a turned foot whose AABB overlaps but OBB does not.
- Block distance wins over farther human hit.
- Install rejects non-finite/oversized/non-orthonormal/stale data.

In-game acceptance:

- Synthetic fixture renders and receives logical-server ack.
- Probe identifies each hand and foot by exact part.
- A ray through a visible gap misses.
- A nearer wall blocks a human hit.
- Contact target follows oriented hand/foot geometry.
- Changing anchor/scale moves points, debug volumes, and authoritative queries together.
- Ten-minute demo does not leak GPU buffers, socket messages, or stale live interaction.

A visible wireframe without the logical-server results does not pass this workflow.
