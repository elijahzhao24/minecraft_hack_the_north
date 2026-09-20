"""Serialize a :class:`CharacterFrame` to an HMC1 ``CHARACTER_FRAME`` message.

Landmarks and colliders stay in the JSON header (small and inspectable); only
the colored points are binary, packed as the 16-byte ``xyzrgba16_le`` record.
"""

from __future__ import annotations

import numpy as np

from hmc_backend.contracts.arrays import check_cloud
from hmc_backend.contracts.internal import (
    CharacterFrame,
    Collider,
    Landmark3D,
    SourceFrameRef,
)
from hmc_backend.protocol.envelope import MessageType, encode_envelope

CHARACTER_SCHEMA = "hmc.character_frame"
CHARACTER_SCHEMA_VERSION = 2

# Packed record: 3x float32 LE (xyz) then r,g,b,a uint8 = 16 bytes.
_POINT_DTYPE = np.dtype(
    [("xyz", "<f4", (3,)), ("rgba", "u1", (4,))]
)


def pack_points(xyz: np.ndarray, rgba: np.ndarray) -> bytes:
    """Pack N points into contiguous ``xyzrgba16_le`` records."""
    n = xyz.shape[0]
    records = np.empty(n, dtype=_POINT_DTYPE)
    records["xyz"] = xyz.astype("<f4", copy=False)
    records["rgba"] = rgba.astype(np.uint8, copy=False)
    return records.tobytes()


def unpack_points(raw: bytes, count: int) -> tuple[np.ndarray, np.ndarray]:
    """Inverse of :func:`pack_points` (used by tests and the replayer)."""
    records = np.frombuffer(raw, dtype=_POINT_DTYPE, count=count)
    xyz = np.ascontiguousarray(records["xyz"]).astype(np.float32)
    rgba = np.ascontiguousarray(records["rgba"]).astype(np.uint8)
    return xyz, rgba


def _source_ref_json(ref: SourceFrameRef) -> dict:
    return {
        "device_id": ref.device_id,
        "session_id": str(ref.session_id),
        "capture_id": str(ref.capture_id),
        "sequence": ref.sequence,
        "source_frame_id": str(ref.source_frame_id) if ref.source_frame_id else None,
    }


def _landmark_json(lm: Landmark3D) -> dict:
    if not lm.valid or lm.position_stage_m is None:
        return {
            "name": lm.name,
            "position_stage_m": None,
            "valid": False,
            "source": "unavailable",
            "confidence": None,
            "visibility": None,
            "observed_by": list(lm.observed_by),
            "reprojection_error_px": None,
        }
    return {
        "name": lm.name,
        "position_stage_m": list(lm.position_stage_m),
        "valid": True,
        "source": lm.source,
        "confidence": lm.confidence,
        "visibility": lm.visibility,
        "observed_by": list(lm.observed_by),
        "reprojection_error_px": lm.reprojection_error_px,
    }


def _collider_json(c: Collider) -> dict:
    base = {
        "id": c.id,
        "body_part": c.body_part,
        "type": c.type,
        "valid": c.valid,
        "fit_source": c.fit_source,
        "quality": c.quality,
    }
    if not c.valid:
        # Invalid colliders keep identity but carry no geometry.
        return base
    if c.type == "sphere":
        base["center_stage_m"] = list(c.center_stage_m)  # type: ignore[arg-type]
        base["radius_m"] = c.radius_m
    elif c.type == "capsule":
        base["a_stage_m"] = list(c.a_stage_m)  # type: ignore[arg-type]
        base["b_stage_m"] = list(c.b_stage_m)  # type: ignore[arg-type]
        base["radius_m"] = c.radius_m
    elif c.type == "obb":
        base["center_stage_m"] = list(c.center_stage_m)  # type: ignore[arg-type]
        base["axes_row_major"] = list(c.axes_row_major)  # type: ignore[arg-type]
        base["half_extents_m"] = list(c.half_extents_m)  # type: ignore[arg-type]
    return base


def build_character_header(frame: CharacterFrame, payload_length: int) -> dict:
    """Build the JSON header for a CharacterFrame (points already packed)."""
    return {
        "schema": CHARACTER_SCHEMA,
        "schema_version": CHARACTER_SCHEMA_VERSION,
        "session_id": str(frame.session_id),
        "calibration_id": str(frame.calibration_id),
        "frame_id": frame.frame_id,
        "fusion_id": str(frame.fusion_id) if frame.fusion_id else None,
        "source_frames": [_source_ref_json(r) for r in frame.source_frames],
        "normalized_capture_time_s": frame.normalized_capture_time_s,
        "pair_skew_ms": frame.pair_skew_ms,
        "mode": frame.mode,
        "quality": {
            "valid": frame.quality.valid,
            "point_count": frame.quality.point_count,
            "valid_landmark_count": frame.quality.valid_landmark_count,
            "valid_collider_count": frame.quality.valid_collider_count,
            "warnings": list(frame.quality.warnings),
        },
        "landmarks": [_landmark_json(lm) for lm in frame.landmarks],
        "colliders": [_collider_json(c) for c in frame.colliders],
        "trace": {
            "sentry_trace": frame.trace.sentry_trace,
            "baggage": frame.trace.baggage,
        },
        "buffers": [
            {
                "name": "points",
                "encoding": "xyzrgba16_le",
                "offset": 0,
                "length": payload_length,
                "shape": [frame.cloud.count],
            }
        ],
    }


def encode_character_frame(frame: CharacterFrame) -> bytes:
    """Serialize a CharacterFrame to one HMC1 ``CHARACTER_FRAME`` message."""
    cloud = frame.cloud
    check_cloud(cloud.xyz_stage_m, cloud.rgba, cloud.source_mask)
    payload = pack_points(cloud.xyz_stage_m, cloud.rgba)
    header = build_character_header(frame, len(payload))
    if cloud.count:
        header["buffers"].append({"name": "point_sources", "encoding": "uint8",
                                  "offset": len(payload), "length": cloud.count, "shape": [cloud.count]})
        payload += cloud.source_mask.astype(np.uint8, copy=False).tobytes()
    return encode_envelope(MessageType.CHARACTER_FRAME, header, payload)
