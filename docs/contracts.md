# Wire contracts and data structures

This is the normative cross-service contract for protocol version 1. The product brief explains intent; this file defines bytes, DTO fields, validation, and ownership. Examples use JSON, but bulk data is never encoded as JSON arrays or base64.

## 1. Contract rules

- Wire field names use `snake_case` in Swift, Python, and Java adapters.
- UUID values are lowercase canonical strings such as `018f...`; generate UUIDv4 for sessions/captures.
- All numeric wire data is little-endian unless it is JSON text.
- JSON numbers carrying counters must be non-negative integers no greater than signed 64-bit max. Java stores them in `long`; Python stores them in `int`; Swift stores them in `UInt64` after range checking.
- All floating values must be finite. `NaN` and infinity are rejected even if a JSON parser accepts them.
- Timestamps ending in `_s` are float64 seconds. Phone capture/clock values use the phone monotonic clock. `normalized_capture_time_s` uses the backend monotonic clock after offset correction. Never compare raw clocks from different devices.
- Positions and lengths before the Minecraft boundary are meters. Pixel coordinates are in the transmitted, unrotated image raster.
- Optional unknown values are JSON `null`; `0` is a real value and is not a missing sentinel.
- Unknown JSON fields are rejected for protocol v1. That prevents misspellings from silently changing geometry.
- Every published collider and landmark repeats or inherits the containing `frame_id` and `calibration_id`; a consumer must never join objects by arrival time.

Closed v1 enums:

| Type | Values |
|---|---|
| `Mode` | `snapshot`, `live` |
| `ImageOrientation` | `landscape_right`, `landscape_left`, `portrait`, `portrait_upside_down` (the MVP calibration accepts only `landscape_right`) |
| `TrackingState` | `normal`, `limited_initializing`, `limited_excessive_motion`, `limited_insufficient_features`, `limited_relocalizing`, `not_available` |
| `LandmarkSource` | `depth_neighborhood`, `triangulated`, `registered_model_prior`, `derived`, `unavailable` |
| `ColliderType` | `sphere`, `capsule`, `obb` |
| `FitSource` | `observed`, `subject_default`, `global_default`, `disabled` |
| `BodyPart` | `head`, `torso`, `pelvis`, `left_upper_arm`, `right_upper_arm`, `left_forearm`, `right_forearm`, `left_hand`, `right_hand`, `left_thigh`, `right_thigh`, `left_shin`, `right_shin`, `left_foot`, `right_foot` |
| `ProbeResultCode` | `HIT`, `MISS`, `BLOCK_OCCLUDED`, `NO_ACTIVE_SNAPSHOT`, `FRAME_MISMATCH`, `OUT_OF_REACH` |

Mapping from native framework enum values into these values occurs at the producing boundary. Unknown native values cause a versioned validation error; they are not serialized with their platform spelling.

## 2. `HMC1` binary envelope

Every binary WebSocket message is exactly one envelope:

| Offset | Size | Type | Field |
|---:|---:|---|---|
| 0 | 4 | ASCII | magic, exactly `HMC1` |
| 4 | 2 | `uint16_le` | envelope version, exactly `1` |
| 6 | 2 | `uint16_le` | message type |
| 8 | 4 | `uint32_le` | JSON header byte length |
| 12 | 4 | `uint32_le` | binary payload byte length |
| 16 | variable | UTF-8 | JSON header, no BOM |
| `16 + header_length` | variable | bytes | concatenated buffers |

Message types:

| Value | Name | Producer → consumer |
|---:|---|---|
| 1 | `RGBD_FRAME` | iPhone → backend |
| 2 | `CHARACTER_FRAME` | backend → Minecraft client |

Complete boundary inventory:

| Boundary | DTO/message | Encoding |
|---|---|---|
| Phone → backend | `ClientHello`, `ClockPong`, `Ack`, `RGBD_FRAME` | JSON text except HMC1 binary frame |
| Backend → phone | `ServerHello`, `ClockPing`, `CaptureRequest`, `Ack`, `Error` | JSON text |
| Minecraft → backend | `CharacterHello`, `RequestCapture`, `CharacterAck` | JSON text |
| Backend → Minecraft | `CharacterServerHello`, `Ack`, `Error`, `CHARACTER_FRAME` | JSON text except HMC1 binary frame |
| HTTP caller ← backend | `HealthResponse` | JSON response |
| Backend internal | `CapturedFrame`, `CameraCalibration`, `PairedFrames`, `ViewDetection`, `ColoredPointCloud`, `FittedCharacter`, `CharacterFrame` | typed Python/NumPy objects |
| Minecraft client → logical server | `InstallSnapshotPayload`, `ProbeRequestPayload` | Fabric custom payload |
| Logical server → Minecraft client | `SnapshotAckPayload`, `ProbeResultPayload`, `ContactStatePayload` | Fabric custom payload |

Validation order is important:

1. Require at least 16 bytes.
2. Validate magic, version, message type, and size limits before copying or allocating.
3. Require actual message length to equal `16 + header_length + payload_length`.
4. Decode strict UTF-8 and validate the message-specific JSON model.
5. Validate all buffer descriptors: known encoding, non-negative range, `offset + length <= payload_length`, no overlap, and exact expected byte count for fixed-size rasters.
6. Decode buffers only after all descriptors pass.

Limits for v1:

| Limit | Value |
|---|---:|
| JSON header | 65,536 bytes |
| RGBD payload | 16 MiB |
| Character payload | 8 MiB |
| RGB/depth width or height | 1–8192 |
| Character points | 100,000 |
| Landmarks | 256 |
| Colliders | 128 |

### `BufferDescriptor`

```json
{
  "name": "depth",
  "encoding": "float32_le",
  "offset": 412381,
  "length": 196608,
  "shape": [192, 256]
}
```

| Field | Type | Meaning |
|---|---|---|
| `name` | non-empty string | Unique within this payload |
| `encoding` | enum | `jpeg`, `float32_le`, `uint8`, or `xyzrgba16_le` |
| `offset` | uint32 | Byte offset from start of payload, not envelope |
| `length` | uint32 | Byte length |
| `shape` | array of positive uint32 | Logical dimensions; absent only for JPEG |

Descriptors are ordered by offset. Buffers are tightly concatenated, so the first starts at 0, every next offset equals the previous end, and the final end equals `payload_length`.

## 3. Text control protocol

Small commands are one UTF-8 JSON object per WebSocket text message. Every object has `type`, `protocol_version: 1`, and `request_id` (UUID) when it participates in request/reply.

### Phone connection messages

`ClientHello` — phone → backend, first message after connect:

```json
{
  "type": "client_hello",
  "protocol_version": 1,
  "device_id": "front-phone",
  "session_id": "44487d7c-b847-49db-aa37-cf326ad76078",
  "app_version": "0.1.0",
  "platform": "ios",
  "supports_scene_depth": true,
  "image_orientation": "landscape_right"
}
```

`ServerHello` — backend → phone:

```json
{
  "type": "server_hello",
  "protocol_version": 1,
  "server_session_id": "18434a87-0ea1-4088-a120-b44823d1c8a8",
  "accepted_device_id": "front-phone",
  "max_binary_bytes": 16777216,
  "clock_probe_interval_s": 2.0
}
```

Reject a binary frame before a successful hello. One active connection is allowed per `(device_id, session_id)`; a reconnect with a new session replaces the old device session and invalidates pending pair candidates.

### Clock synchronization

```json
{
  "type": "clock_ping",
  "protocol_version": 1,
  "request_id": "456a353c-c2e0-4f95-97c7-68bf38cfef47",
  "backend_send_time_s": 61420.230115
}
```

```json
{
  "type": "clock_pong",
  "protocol_version": 1,
  "request_id": "456a353c-c2e0-4f95-97c7-68bf38cfef47",
  "backend_send_time_s": 61420.230115,
  "phone_receive_time_s": 9921.620552,
  "phone_send_time_s": 9921.620734
}
```

The backend records its own receive time. It computes phone-minus-backend offset as `((t1-t0) + (t2-t3)) / 2`, round-trip delay as `(t3-t0) - (t2-t1)`, retains a bounded set of low-delay samples, and reports uncertainty. Echoing `backend_send_time_s` is diagnostic; `request_id` is the join key.

### Capture and generic result messages

```json
{
  "type": "capture_request",
  "protocol_version": 1,
  "request_id": "bd36780c-37ac-47ad-8cbc-dba00734859f",
  "capture_id": "6ee77aca-80b0-43e5-be8e-bb61c17eb8a4",
  "mode": "snapshot",
  "not_before_phone_time_s": null
}
```

```json
{
  "type": "ack",
  "protocol_version": 1,
  "request_id": "bd36780c-37ac-47ad-8cbc-dba00734859f",
  "accepted": true,
  "code": "capture_queued",
  "detail": null
}
```

```json
{
  "type": "error",
  "protocol_version": 1,
  "request_id": null,
  "code": "invalid_buffer_range",
  "message": "depth ends after payload",
  "retryable": false
}
```

Error `code` is a stable machine value. `message` is for people and may change. Expected codes include `unsupported_version`, `unauthorized_device`, `invalid_message`, `invalid_buffer_range`, `frame_too_large`, `sequence_replayed`, `calibration_missing`, `pair_skew_exceeded`, `processor_busy`, and `internal_error`.

### Minecraft subscriber messages

Minecraft sends `character_hello` first:

```json
{
  "type": "character_hello",
  "protocol_version": 1,
  "client_id": "demo-laptop",
  "last_frame_id": 41
}
```

The backend responds with a subscriber-specific hello and immediately sends its latest complete snapshot when available:

```json
{
  "type": "character_server_hello",
  "protocol_version": 1,
  "server_session_id": "18434a87-0ea1-4088-a120-b44823d1c8a8",
  "max_binary_bytes": 8388608,
  "latest_frame_id": 42
}
```

Minecraft can then send:

```json
{
  "type": "character_ack",
  "protocol_version": 1,
  "frame_id": 42,
  "accepted": true,
  "code": "decoded",
  "detail": null
}
```

This acknowledges backend-to-client decode only. It is distinct from the Fabric logical-server acknowledgement that makes interaction active.

Minecraft may initiate one synchronized capture:

```json
{
  "type": "request_capture",
  "protocol_version": 1,
  "request_id": "04b17d28-d361-4ea5-a196-6ed1f7578f17",
  "capture_id": "ec256792-0ea8-41d4-af25-8b06b71f2987",
  "mode": "snapshot"
}
```

The backend accepts it only when both configured phones are connected, clock-ready, and the processor can accept a snapshot. It forwards `capture_request` carrying the same IDs to both phones and replies to Minecraft with `ack`. That acknowledgement means the request was dispatched, not that a `CharacterFrame` exists; completion is the later binary frame whose two source records carry that `capture_id`.

### HTTP health response

`GET /health` returns `HealthResponse` with `status` (`starting`, `ready`, or `degraded`), protocol version, calibration readiness/ID, named model states, each expected device's connected/clock-ready/queue-depth state, and nullable latest frame ID. HTTP status is 200 only for `ready`, otherwise 503. The concrete example is in [workflows/02-python-backend.md](workflows/02-python-backend.md).

## 4. `RGBD_FRAME` header

```json
{
  "schema": "hmc.rgbd_frame",
  "schema_version": 1,
  "device_id": "front-phone",
  "session_id": "44487d7c-b847-49db-aa37-cf326ad76078",
  "capture_id": "6ee77aca-80b0-43e5-be8e-bb61c17eb8a4",
  "sequence": 184,
  "capture_timestamp_s": 9922.107184,
  "image_orientation": "landscape_right",
  "mirrored": false,
  "tracking_state": "normal",
  "rgb": {
    "width": 1920,
    "height": 1440,
    "intrinsics_row_major": [1412.3, 0.0, 959.5, 0.0, 1411.9, 719.5, 0.0, 0.0, 1.0]
  },
  "depth": {
    "width": 256,
    "height": 192,
    "unit": "meter",
    "confidence_encoding": "arkit_0_1_2"
  },
  "rgb_depth_mapping": {
    "method": "normalized_uncropped_scale",
    "rgb_crop": null,
    "depth_crop": null
  },
  "T_arkit_world_from_camera_row_major": [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 1.2, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0],
  "trace": {"sentry_trace": null, "baggage": null},
  "buffers": [
    {"name": "rgb", "encoding": "jpeg", "offset": 0, "length": 412381},
    {"name": "depth", "encoding": "float32_le", "offset": 412381, "length": 196608, "shape": [192, 256]},
    {"name": "confidence", "encoding": "uint8", "offset": 608989, "length": 49152, "shape": [192, 256]}
  ]
}
```

Invariants:

- RGB JPEG decodes to the stated RGB width/height and has no relied-upon EXIF rotation.
- `mirrored` is `false` for the locked v1 capture path. `rgb_depth_mapping.method` is `normalized_uncropped_scale`: both rasters are uncropped/unmirrored and corresponding coordinates are related by normalized raster position. Any later crop/rotation requires a new versioned mapping method.
- Intrinsics describe that transmitted RGB raster after any resize.
- Depth has exactly `width * height * 4` bytes and positive finite values for valid samples. Invalid samples are `NaN` in memory but are normalized to `0.0` on the wire and rejected by confidence/mask; consumers must not unproject zero.
- Confidence has exactly `width * height` bytes. ARKit values are 0/1/2; unknown values are rejected in v1.
- Raster orientation is locked per session. A change requires a new session and calibration.
- The ARKit pose is diagnostic and not calibration truth.
- `trace` is optional observability context. Its absence does not invalidate capture. When present, receivers may extract `sentry_trace` and `baggage`; they must not treat frame-ID correlation alone as a connected distributed trace.

## 5. Python internal data structures

These are typed in-process values, not extra network formats. Pydantic models validate untrusted boundaries; frozen `dataclass(slots=True)` objects and NumPy arrays carry trusted bulk data internally.

```python
@dataclass(frozen=True, slots=True)
class CapturedFrame:
    device_id: str
    session_id: UUID
    capture_id: UUID
    sequence: int
    capture_timestamp_s: float
    normalized_capture_time_s: float
    clock_uncertainty_ms: float
    rgb: NDArray[np.uint8]          # H_rgb × W_rgb × 3, RGB order
    depth_m: NDArray[np.float32]    # H_depth × W_depth
    confidence: NDArray[np.uint8]   # H_depth × W_depth
    K_rgb: NDArray[np.float64]      # 3 × 3
    arkit_pose: NDArray[np.float64] # 4 × 4, diagnostic

@dataclass(frozen=True, slots=True)
class CameraCalibration:
    calibration_id: UUID
    device_id: str
    rgb_size: tuple[int, int]
    depth_size: tuple[int, int]
    K_rgb: NDArray[np.float64]
    T_stage_from_optical: NDArray[np.float64]
    reprojection_error_px: float
    created_at_utc: datetime

@dataclass(frozen=True, slots=True)
class PairedFrames:
    pair_id: UUID
    first: CapturedFrame
    second: CapturedFrame
    normalized_capture_time_s: float
    pair_skew_ms: float
    calibration_id: UUID

@dataclass(frozen=True, slots=True)
class Landmark2DObservation:
    name: str
    xy_px: tuple[float, float]
    z_model: float | None
    visibility: float | None
    presence: float | None
    valid: bool

@dataclass(frozen=True, slots=True)
class ViewDetection:
    device_id: str
    capture_id: UUID
    person_mask: NDArray[np.bool_]
    body: tuple[Landmark2DObservation, ...]
    left_hand: tuple[Landmark2DObservation, ...]
    right_hand: tuple[Landmark2DObservation, ...]
    pose_world_prior_m: NDArray[np.float32] | None
    hand_world_priors_m: Mapping[str, NDArray[np.float32]]

@dataclass(frozen=True, slots=True)
class ColoredPointCloud:
    xyz_stage_m: NDArray[np.float32] # N × 3
    rgba: NDArray[np.uint8]          # N × 4
    source_mask: NDArray[np.uint8]   # N; camera bitset, debug only

@dataclass(frozen=True, slots=True)
class SourceFrameRef:
    device_id: str
    session_id: UUID
    capture_id: UUID
    sequence: int

@dataclass(frozen=True, slots=True)
class FrameQuality:
    valid: bool
    point_count: int
    valid_landmark_count: int
    valid_collider_count: int
    warnings: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class TraceContext:
    sentry_trace: str | None
    baggage: str | None

@dataclass(frozen=True, slots=True)
class CharacterFrame:
    session_id: UUID                 # backend character-stream session
    calibration_id: UUID
    frame_id: int
    source_frames: tuple[SourceFrameRef, SourceFrameRef]
    normalized_capture_time_s: float
    pair_skew_ms: float
    mode: Literal["snapshot", "live"]
    quality: FrameQuality
    cloud: ColoredPointCloud
    landmarks: tuple[Landmark3D, ...]
    colliders: tuple[Collider, ...]
    trace: TraceContext
```

Array invariants are checked at module boundaries: C-contiguous, expected rank/shape/dtype, same point count, finite XYZ, alpha 255, and arrays set read-only before publication.

## 6. `CHARACTER_FRAME` header and payload

Landmarks and colliders stay in the JSON header because they are small and inspectable. Only points are binary.

```json
{
  "schema": "hmc.character_frame",
  "schema_version": 1,
  "session_id": "25b981d5-b9a6-4df5-8495-c95c5a9a65d9",
  "calibration_id": "f766f462-d405-4c30-89f9-f626da70547b",
  "frame_id": 42,
  "source_frames": [
    {"device_id": "front-phone", "session_id": "44487d7c-b847-49db-aa37-cf326ad76078", "capture_id": "6ee77aca-80b0-43e5-be8e-bb61c17eb8a4", "sequence": 184},
    {"device_id": "side-phone", "session_id": "18619e93-736f-4f18-afdb-7f3ea369b7d5", "capture_id": "6ee77aca-80b0-43e5-be8e-bb61c17eb8a4", "sequence": 203}
  ],
  "normalized_capture_time_s": 61420.716991,
  "pair_skew_ms": 18.4,
  "mode": "snapshot",
  "quality": {
    "valid": true,
    "point_count": 38421,
    "valid_landmark_count": 68,
    "valid_collider_count": 17,
    "warnings": ["right_hand_low_depth_support"]
  },
  "landmarks": [],
  "colliders": [],
  "trace": {"sentry_trace": null, "baggage": null},
  "buffers": [
    {"name": "points", "encoding": "xyzrgba16_le", "offset": 0, "length": 614736, "shape": [38421]}
  ]
}
```

`xyzrgba16_le` is a packed 16-byte record repeated N times:

| Offset in record | Size | Type | Meaning |
|---:|---:|---|---|
| 0 | 4 | float32 LE | stage X meters |
| 4 | 4 | float32 LE | stage Y meters |
| 8 | 4 | float32 LE | stage Z meters |
| 12 | 1 | uint8 | red |
| 13 | 1 | uint8 | green |
| 14 | 1 | uint8 | blue |
| 15 | 1 | uint8 | alpha |

### `Landmark3D`

```json
{
  "name": "hand.left.index_tip",
  "position_stage_m": [-0.411, 1.126, 0.084],
  "valid": true,
  "source": "multiview_depth",
  "confidence": 0.87,
  "visibility": 0.92,
  "observed_by": ["front-phone", "side-phone"],
  "reprojection_error_px": 1.8
}
```

`source` is `depth_neighborhood`, `triangulated`, `registered_model_prior`, `derived`, or `unavailable`. Invalid landmarks use `position_stage_m: null`, `source: unavailable`, and nullable metrics. `confidence` is present only when the producing model/estimator supplies a meaningful score.

Canonical body names are MediaPipe's 33 pose names prefixed by `body.`, plus `body.pelvis_center` and `body.head_center`. Hand names are `hand.left|right.wrist`, thumb `cmc|mcp|ip|tip`, and index/middle/ring/pinky `mcp|pip|dip|tip`. The future `contracts/landmark_names.json` is the only index mapping source.

### Collider discriminated union

Every collider has:

| Field | Type |
|---|---|
| `id` | stable string such as `arm.left.forearm` |
| `body_part` | stable enum used in results/UI |
| `type` | `sphere`, `capsule`, or `obb` discriminator |
| `valid` | bool |
| `fit_source` | `observed`, `subject_default`, `global_default`, or `disabled` |
| `quality` | float `[0,1]` or null |

Sphere:

```json
{"id":"head","body_part":"head","type":"sphere","valid":true,"fit_source":"observed","quality":0.91,"center_stage_m":[0.0,1.71,0.02],"radius_m":0.112}
```

Capsule:

```json
{"id":"arm.left.forearm","body_part":"left_forearm","type":"capsule","valid":true,"fit_source":"observed","quality":0.82,"a_stage_m":[-0.22,1.36,0.01],"b_stage_m":[-0.43,1.18,0.08],"radius_m":0.055}
```

Oriented box:

```json
{"id":"hand.left","body_part":"left_hand","type":"obb","valid":true,"fit_source":"observed","quality":0.78,"center_stage_m":[-0.48,1.12,0.09],"axes_row_major":[0.82,-0.03,0.57,0.10,0.99,-0.09,-0.56,0.13,0.82],"half_extents_m":[0.105,0.045,0.025]}
```

Axes are three unit axis vectors stored as rows, mutually orthogonal within `1e-4`; determinant magnitude must be within `1e-3` of 1. Radii and extents are positive and capped at 1 meter. Invalid colliders retain identity fields but omit geometry (`geometry: null` in the Pydantic form); serializers must not emit fake zero geometry.

## 7. Minecraft client ↔ logical-server payloads

These are Fabric `CustomPacketPayload` records, not `HMC1` messages. Register their `StreamCodec`s on both sides before play. Names use the `hmc` namespace.

### `hmc:install_snapshot` — client to server

```java
record InstallSnapshotPayload(
    long frameId,
    UUID sessionId,
    UUID calibrationId,
    Vec3 anchorBlocks,
    float blocksPerMeter,
    List<ColliderDto> colliders
) implements CustomPacketPayload {}
```

`ColliderDto` is a tagged union matching the wire collider after conversion to world blocks. It contains `id`, `bodyPart`, `valid`, and exactly one geometry. Maximum 128 colliders and 64 UTF-8 bytes per ID. The server validates finite coordinates, positive bounded sizes, orthonormal OBB axes, player/session ownership, and monotonically increasing `frameId`.

### `hmc:snapshot_ack` — server to client

```java
record SnapshotAckPayload(long frameId, boolean accepted, String code) {}
```

Only `accepted=true` allows the client to mark that `frameId` active and swap its visible cloud/collider overlays together.

### `hmc:probe_request` — client to server

```java
record ProbeRequestPayload(long requestId, long expectedFrameId) {}
```

It deliberately omits origin, direction, target part, reach, and claimed hit. The server obtains those from the sending player.

### `hmc:probe_result` — server to client

```java
record ProbeResultPayload(
    long requestId,
    long frameId,
    boolean hit,
    Optional<String> colliderId,
    Optional<BodyPart> bodyPart,
    Optional<Vec3> hitWorld,
    OptionalDouble distanceBlocks,
    ProbeResultCode code
) {}
```

Codes: `HIT`, `MISS`, `BLOCK_OCCLUDED`, `NO_ACTIVE_SNAPSHOT`, `FRAME_MISMATCH`, and `OUT_OF_REACH`.

### `hmc:contact_state` — server to client

```java
record ContactStatePayload(long frameId, BlockPos target, Set<BodyPart> touchingParts) {}
```

The server emits only on state change or at a low heartbeat rate, not every tick.

## 8. Type mapping across languages

| Concept | Swift | Python boundary | Java |
|---|---|---|---|
| strict JSON DTO | `Codable struct/enum` | Pydantic `BaseModel`, `extra='forbid'` | record + Gson adapter |
| UUID | `UUID` | `uuid.UUID` | `java.util.UUID` |
| uint16/32 envelope | `UInt16`/`UInt32` | `struct '<HHII'` → `int` | manual LE `ByteBuffer`/Netty reads |
| sequence/frame ID | checked `UInt64` | constrained `int` | non-negative `long` |
| Vec3 JSON | fixed-count `[Double]` adapter | tuple of 3 finite floats | `Vec3Dto(double x,y,z)` |
| matrix JSON | fixed-count `[Double]` adapter | NumPy float64 after Pydantic list validation | immutable `double[]` length 9/16 |
| bulk depth | copied `CVPixelBuffer` bytes | C-contiguous NumPy float32 | never received |
| bulk points | never received | NumPy float32 + uint8 | direct/native `ByteBuffer` for GPU upload |

Do not generate Java game payloads directly from the WebSocket JSON models. Decode into transport DTOs, validate, apply the stage-to-world transform, then build separate game payload records. That boundary prevents meters/stage coordinates from being confused with blocks/world coordinates.

## 9. Compatibility and fixtures

- Envelope version changes only if the 16-byte framing changes.
- Each header has an independent `schema_version` for field-level changes.
- Adding an enum value is breaking for v1 strict consumers unless they have an explicit `UNKNOWN` policy; increment the schema.
- Every schema change requires: updated examples here, Pydantic validation tests, Swift and Java decoder tests, one success golden fixture, and one malformed fixture.
- Golden fixtures include at minimum: tiny 2×2 RGBD, wrong magic, truncated payload, overlapping buffers, invalid UTF-8, non-finite geometry, one snapshot containing all three collider types, and maximum-count rejection.

The Python encoder is the initial fixture authority, but its output is not automatically correct. The exact bytes and expected decoded values are reviewed and frozen in version control.
