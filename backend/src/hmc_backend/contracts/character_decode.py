"""Strict decoder for HMC1 ``CHARACTER_FRAME`` messages.

Python previously only *encoded* CharacterFrames (backend -> Minecraft) and
never decoded them, so its wire output was never checked against the Java
consumer. This decoder closes that gap: it validates a frame the same way the
Fabric mod's ``CharacterFrameDecoder`` does and raises the same stable error
codes, so `contracts/fixtures` becomes a genuine cross-language gate rather than
Java checking its own output.

Error codes (must match ``contracts/fixtures/malformed_expected_codes.json``):

``invalid_message``            framing, schema, enum, or structural violation
``unsupported_version``        envelope version or header ``schema_version``
``frame_too_large``            declared sizes above the v1 limits
``invalid_buffer_range``       buffer descriptor range/overlap/point-count
``non_finite_geometry``        a non-finite point or landmark coordinate
``invalid_collider_geometry``  bad radius, extent, or OBB basis
``limit_exceeded``             too many landmarks or colliders
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from hmc_backend.contracts.character_codec import (
    CHARACTER_SCHEMA,
    CHARACTER_SCHEMA_VERSION,
    unpack_points,
)
from hmc_backend.contracts.internal import Collider, Landmark3D
from hmc_backend.protocol.buffers import load_buffers
from hmc_backend.protocol.envelope import (
    EnvelopeError,
    MessageType,
    decode_envelope,
)

# v1 limits, mirroring minecraft-mod ProtocolLimits.
MAX_POINTS = 100_000
MAX_LANDMARKS = 256
MAX_COLLIDERS = 128
MAX_COLLIDER_ID_BYTES = 64
MAX_COLLIDER_SIZE_M = 1.0
POINT_RECORD_BYTES = 16

_ORTHO_TOL = 1e-4
_DET_TOL = 1e-3

_HEADER_KEYS = {
    "schema",
    "schema_version",
    "session_id",
    "calibration_id",
    "frame_id",
    "fusion_id",
    "source_frames",
    "normalized_capture_time_s",
    "pair_skew_ms",
    "mode",
    "quality",
    "landmarks",
    "colliders",
    "trace",
    "buffers",
}
_MODES = {"snapshot", "live"}
_COLLIDER_TYPES = {"sphere", "capsule", "obb"}


class CharacterDecodeError(ValueError):
    """Raised when a CHARACTER_FRAME violates the contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class DecodedCharacterFrame:
    """A validated CharacterFrame decoded from the wire."""

    session_id: UUID
    calibration_id: UUID
    frame_id: int
    mode: str
    normalized_capture_time_s: float
    pair_skew_ms: float
    source_frames: tuple[dict, ...]
    quality: dict
    xyz_stage_m: NDArray[np.float32]
    rgba: NDArray[np.uint8]
    landmarks: tuple[Landmark3D, ...]
    colliders: tuple[Collider, ...]
    header: dict
    fusion_id: UUID | None = None

    @property
    def point_count(self) -> int:
        return int(self.xyz_stage_m.shape[0])


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise CharacterDecodeError(code, message)


def _finite_vec(value: object, label: str, length: int = 3) -> tuple[float, ...]:
    _require(
        isinstance(value, list) and len(value) == length,
        "invalid_message",
        f"{label} must be a {length}-element array",
    )
    out = []
    for v in value:  # type: ignore[union-attr]
        _require(isinstance(v, (int, float)) and not isinstance(v, bool), "invalid_message", f"{label} must be numeric")
        f = float(v)
        _require(math.isfinite(f), "non_finite_geometry", f"{label} contains a non-finite value")
        out.append(f)
    return tuple(out)


def _uuid(value: object, label: str) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise CharacterDecodeError("invalid_message", f"{label} is not a UUID") from exc


def _decode_landmark(raw: object) -> Landmark3D:
    _require(isinstance(raw, dict), "invalid_message", "landmark must be an object")
    assert isinstance(raw, dict)
    name = raw.get("name")
    _require(isinstance(name, str) and bool(name), "invalid_message", "landmark name must be non-empty")
    valid = raw.get("valid")
    _require(isinstance(valid, bool), "invalid_message", f"{name}: valid must be a boolean")

    position = raw.get("position_stage_m")
    if not valid or position is None:
        return Landmark3D(name=str(name), position_stage_m=None, valid=False, source="unavailable")

    xyz = _finite_vec(position, f"landmark {name} position")
    return Landmark3D(
        name=str(name),
        position_stage_m=(xyz[0], xyz[1], xyz[2]),
        valid=True,
        source=str(raw.get("source", "unavailable")),
        confidence=raw.get("confidence"),
        visibility=raw.get("visibility"),
        observed_by=tuple(raw.get("observed_by") or ()),
        reprojection_error_px=raw.get("reprojection_error_px"),
    )


def _positive_size(value: object, label: str) -> float:
    _require(
        isinstance(value, (int, float)) and not isinstance(value, bool),
        "invalid_collider_geometry",
        f"{label} must be numeric",
    )
    f = float(value)  # type: ignore[arg-type]
    _require(math.isfinite(f), "invalid_collider_geometry", f"{label} must be finite")
    _require(f > 0.0, "invalid_collider_geometry", f"{label} must be positive")
    _require(f <= MAX_COLLIDER_SIZE_M, "invalid_collider_geometry", f"{label} exceeds {MAX_COLLIDER_SIZE_M} m")
    return f


def _decode_collider(raw: object) -> Collider:
    _require(isinstance(raw, dict), "invalid_message", "collider must be an object")
    assert isinstance(raw, dict)

    cid = raw.get("id")
    _require(isinstance(cid, str) and bool(cid), "invalid_message", "collider id must be non-empty")
    assert isinstance(cid, str)
    _require(
        len(cid.encode("utf-8")) <= MAX_COLLIDER_ID_BYTES,
        "invalid_message",
        f"collider id {cid!r} exceeds {MAX_COLLIDER_ID_BYTES} bytes",
    )

    ctype = raw.get("type")
    _require(ctype in _COLLIDER_TYPES, "invalid_message", f"{cid}: unknown collider type {ctype!r}")
    valid = raw.get("valid")
    _require(isinstance(valid, bool), "invalid_message", f"{cid}: valid must be a boolean")

    base = {
        "id": cid,
        "body_part": str(raw.get("body_part", "")),
        "type": ctype,
        "valid": bool(valid),
        "fit_source": str(raw.get("fit_source", "disabled")),
        "quality": raw.get("quality"),
    }
    if not valid:
        # Invalid colliders keep identity only; geometry must be absent.
        return Collider(**base)  # type: ignore[arg-type]

    if ctype == "sphere":
        return Collider(
            **base,  # type: ignore[arg-type]
            center_stage_m=_finite_vec(raw.get("center_stage_m"), f"{cid} center"),
            radius_m=_positive_size(raw.get("radius_m"), f"{cid} radius"),
        )
    if ctype == "capsule":
        return Collider(
            **base,  # type: ignore[arg-type]
            a_stage_m=_finite_vec(raw.get("a_stage_m"), f"{cid} a"),
            b_stage_m=_finite_vec(raw.get("b_stage_m"), f"{cid} b"),
            radius_m=_positive_size(raw.get("radius_m"), f"{cid} radius"),
        )

    axes = _finite_vec(raw.get("axes_row_major"), f"{cid} axes", length=9)
    m = np.array(axes, dtype=np.float64).reshape(3, 3)
    _require(
        bool(np.allclose(m @ m.T, np.eye(3), atol=_ORTHO_TOL)),
        "invalid_collider_geometry",
        f"{cid}: OBB axes are not orthonormal",
    )
    _require(
        abs(abs(float(np.linalg.det(m))) - 1.0) <= _DET_TOL,
        "invalid_collider_geometry",
        f"{cid}: OBB axes determinant magnitude != 1",
    )
    half = raw.get("half_extents_m")
    _require(isinstance(half, list) and len(half) == 3, "invalid_message", f"{cid} half_extents must be a 3-vector")
    extents = tuple(_positive_size(h, f"{cid} half_extent[{i}]") for i, h in enumerate(half))  # type: ignore[union-attr]

    return Collider(
        **base,  # type: ignore[arg-type]
        center_stage_m=_finite_vec(raw.get("center_stage_m"), f"{cid} center"),
        axes_row_major=axes,
        half_extents_m=extents,  # type: ignore[arg-type]
    )


def decode_character_frame(raw: bytes) -> DecodedCharacterFrame:
    """Decode and fully validate one ``CHARACTER_FRAME`` message."""
    try:
        envelope = decode_envelope(raw)
    except EnvelopeError as exc:
        raise CharacterDecodeError(exc.code, exc.message) from exc

    _require(
        envelope.message_type is MessageType.CHARACTER_FRAME,
        "invalid_message",
        "expected a CHARACTER_FRAME envelope",
    )

    header = envelope.header

    # --- schema / version --------------------------------------------------
    _require(header.get("schema") == CHARACTER_SCHEMA, "invalid_message", "unexpected schema")
    version = header.get("schema_version")
    _require(isinstance(version, int) and not isinstance(version, bool), "invalid_message", "schema_version must be an integer")
    _require(version in (1, CHARACTER_SCHEMA_VERSION), "unsupported_version", f"unsupported schema_version {version}")

    # v1 rejects unknown fields so a misspelling cannot silently change geometry.
    allowed_keys = _HEADER_KEYS if version == 2 else _HEADER_KEYS - {"fusion_id"}
    unknown = set(header) - allowed_keys
    _require(not unknown, "invalid_message", f"unknown header fields: {sorted(unknown)}")

    mode = header.get("mode")
    _require(mode in _MODES, "invalid_message", f"unknown mode {mode!r}")

    frame_id = header.get("frame_id")
    _require(
        isinstance(frame_id, int) and not isinstance(frame_id, bool) and frame_id >= 0,
        "invalid_message",
        "frame_id must be a non-negative integer",
    )

    # --- landmarks / colliders --------------------------------------------
    landmarks_raw = header.get("landmarks")
    _require(isinstance(landmarks_raw, list), "invalid_message", "landmarks must be an array")
    assert isinstance(landmarks_raw, list)
    _require(len(landmarks_raw) <= MAX_LANDMARKS, "limit_exceeded", f"more than {MAX_LANDMARKS} landmarks")

    colliders_raw = header.get("colliders")
    _require(isinstance(colliders_raw, list), "invalid_message", "colliders must be an array")
    assert isinstance(colliders_raw, list)
    _require(len(colliders_raw) <= MAX_COLLIDERS, "limit_exceeded", f"more than {MAX_COLLIDERS} colliders")

    landmarks = tuple(_decode_landmark(entry) for entry in landmarks_raw)
    names = [lm.name for lm in landmarks]
    _require(len(names) == len(set(names)), "invalid_message", "landmark names are not unique")

    colliders = tuple(_decode_collider(entry) for entry in colliders_raw)
    ids = [c.id for c in colliders]
    _require(len(ids) == len(set(ids)), "invalid_message", "collider ids are not unique")

    # --- points ------------------------------------------------------------
    try:
        buffers = load_buffers(header, envelope.payload)
        descriptor = buffers.descriptor("points")
    except EnvelopeError as exc:
        raise CharacterDecodeError(exc.code, exc.message) from exc

    _require(
        descriptor.length % POINT_RECORD_BYTES == 0,
        "invalid_buffer_range",
        "points buffer length is not a multiple of 16",
    )
    count = descriptor.length // POINT_RECORD_BYTES
    _require(count <= MAX_POINTS, "invalid_buffer_range", f"more than {MAX_POINTS} points")
    if descriptor.shape is not None:
        _require(
            descriptor.shape[0] == count,
            "invalid_buffer_range",
            "points shape does not match buffer length",
        )

    xyz, rgba = unpack_points(buffers.raw("points"), count)
    _require(bool(np.isfinite(xyz).all()), "non_finite_geometry", "cloud contains a non-finite coordinate")

    quality = header.get("quality")
    _require(isinstance(quality, dict), "invalid_message", "quality must be an object")

    return DecodedCharacterFrame(
        session_id=_uuid(header.get("session_id"), "session_id"),
        calibration_id=_uuid(header.get("calibration_id"), "calibration_id"),
        frame_id=int(frame_id),  # type: ignore[arg-type]
        mode=str(mode),
        normalized_capture_time_s=float(header.get("normalized_capture_time_s", 0.0)),
        pair_skew_ms=float(header.get("pair_skew_ms", 0.0)),
        source_frames=tuple(header.get("source_frames") or ()),
        quality=dict(quality),  # type: ignore[arg-type]
        xyz_stage_m=xyz,
        rgba=rgba,
        landmarks=landmarks,
        colliders=colliders,
        header=header,
        fusion_id=_uuid(header.get("fusion_id"), "fusion_id") if header.get("fusion_id") else None,
    )
