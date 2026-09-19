"""Tests for transport DTOs, RGBD header parsing, and CharacterFrame codec."""

from __future__ import annotations

from uuid import uuid4

import numpy as np
import pytest
from pydantic import ValidationError

from hmc_backend.contracts.arrays import ArrayInvariantError
from hmc_backend.contracts.character_codec import encode_character_frame, unpack_points
from hmc_backend.contracts.control import ClientHello, HealthResponse, LiveRequest, LiveState
from hmc_backend.contracts.enums import HealthStatus
from hmc_backend.contracts.internal import (
    CharacterFrame,
    Collider,
    ColoredPointCloud,
    FrameQuality,
    Landmark3D,
    SourceFrameRef,
    TraceContext,
)
from hmc_backend.contracts.rgbd import parse_rgbd_header
from hmc_backend.protocol import decode_envelope, load_buffers


def _rgbd_header_dict() -> dict:
    return {
        "schema": "hmc.rgbd_frame",
        "schema_version": 1,
        "device_id": "front-phone",
        "session_id": str(uuid4()),
        "capture_id": str(uuid4()),
        "sequence": 184,
        "capture_timestamp_s": 9922.107184,
        "image_orientation": "landscape_right",
        "mirrored": False,
        "tracking_state": "normal",
        "rgb": {
            "width": 1920,
            "height": 1440,
            "intrinsics_row_major": [1412.3, 0, 959.5, 0, 1411.9, 719.5, 0, 0, 1],
        },
        "depth": {
            "width": 256,
            "height": 192,
            "unit": "meter",
            "confidence_encoding": "arkit_0_1_2",
        },
        "rgb_depth_mapping": {
            "method": "normalized_uncropped_scale",
            "rgb_crop": None,
            "depth_crop": None,
        },
        "T_arkit_world_from_camera_row_major": [1, 0, 0, 0, 0, 1, 0, 1.2, 0, 0, 1, 0, 0, 0, 0, 1],
        "buffers": [
            {"name": "rgb", "encoding": "jpeg", "offset": 0, "length": 100},
            {
                "name": "depth",
                "encoding": "float32_le",
                "offset": 100,
                "length": 196608,
                "shape": [192, 256],
            },
        ],
    }


def test_client_hello_rejects_unknown_field():
    with pytest.raises(ValidationError):
        ClientHello.model_validate(
            {
                "type": "client_hello",
                "protocol_version": 1,
                "device_id": "front-phone",
                "session_id": str(uuid4()),
                "app_version": "0.1.0",
                "platform": "ios",
                "supports_scene_depth": True,
                "image_orientation": "landscape_right",
                "surprise": "field",
            }
        )


def test_rgbd_header_parses_and_remaps_keys():
    header = parse_rgbd_header(_rgbd_header_dict())
    assert header.schema_name == "hmc.rgbd_frame"
    assert header.mirrored is False
    assert header.rgb_depth_mapping.method == "normalized_uncropped_scale"
    assert header.rgb.width == 1920
    assert len(header.t_arkit_world_from_camera_row_major) == 16


def test_rgbd_header_rejects_bad_intrinsics_length():
    bad = _rgbd_header_dict()
    bad["rgb"]["intrinsics_row_major"] = [1, 2, 3]
    with pytest.raises(ValidationError):
        parse_rgbd_header(bad)


def test_rgbd_header_rejects_nonfinite_pose():
    bad = _rgbd_header_dict()
    bad["T_arkit_world_from_camera_row_major"][3] = float("inf")
    with pytest.raises(ValidationError):
        parse_rgbd_header(bad)


def test_health_response_roundtrip():
    resp = HealthResponse.model_validate(
        {
            "status": "ready",
            "protocol_version": 1,
            "calibration": {"loaded": True, "calibration_id": str(uuid4())},
            "models": {"pose": "ready", "hands": "ready"},
            "devices": {"front-phone": {"connected": True, "clock_ready": True, "queue_depth": 0}},
            "latest_frame_id": 42,
        }
    )
    assert resp.status is HealthStatus.READY


def test_live_control_contracts_are_strict():
    request_id = uuid4()
    request = LiveRequest.model_validate(
        {
            "type": "live_request",
            "protocol_version": 1,
            "request_id": str(request_id),
            "enabled": True,
        }
    )
    state = LiveState(state="running", live_session_id=uuid4(), target_fps=3.0)
    assert request.enabled is True
    assert state.state == "running"


def _make_character_frame() -> CharacterFrame:
    xyz = np.array([[0.0, 1.0, 0.2], [0.1, 1.1, 0.25]], dtype=np.float32)
    rgba = np.array([[10, 20, 30, 255], [40, 50, 60, 255]], dtype=np.uint8)
    source_mask = np.array([1, 2], dtype=np.uint8)
    cloud = ColoredPointCloud(xyz_stage_m=xyz, rgba=rgba, source_mask=source_mask)
    sid = uuid4()
    cid = uuid4()
    refs = (
        SourceFrameRef("front-phone", sid, cid, 184),
        SourceFrameRef("side-phone", sid, cid, 203),
    )
    landmarks = (
        Landmark3D(
            "hand.left.index_tip",
            (-0.4, 1.1, 0.08),
            True,
            "triangulated",
            0.87,
            0.92,
            ("front-phone",),
            1.8,
        ),
        Landmark3D("hand.right.wrist", None, False, "unavailable"),
    )
    colliders = (
        Collider(
            "head",
            "head",
            "sphere",
            True,
            "observed",
            0.9,
            center_stage_m=(0.0, 1.7, 0.02),
            radius_m=0.11,
        ),
        Collider(
            "hand.left",
            "left_hand",
            "obb",
            True,
            "observed",
            0.78,
            center_stage_m=(-0.48, 1.12, 0.09),
            axes_row_major=(1, 0, 0, 0, 1, 0, 0, 0, 1),
            half_extents_m=(0.105, 0.045, 0.025),
        ),
        Collider("hand.right", "right_hand", "capsule", False, "disabled", None),
    )
    quality = FrameQuality(True, 2, 1, 2, ("right_hand_low_depth_support",))
    return CharacterFrame(
        session_id=uuid4(),
        calibration_id=cid,
        frame_id=42,
        source_frames=refs,
        normalized_capture_time_s=61420.716991,
        pair_skew_ms=18.4,
        mode="snapshot",
        quality=quality,
        cloud=cloud,
        landmarks=landmarks,
        colliders=colliders,
        trace=TraceContext(),
    )


def test_character_frame_encodes_and_decodes():
    frame = _make_character_frame()
    raw = encode_character_frame(frame)

    env = decode_envelope(raw)
    assert env.header["schema"] == "hmc.character_frame"
    assert env.header["frame_id"] == 42
    assert env.header["quality"]["point_count"] == 2

    # Invalid collider carries identity but no geometry.
    invalid = next(c for c in env.header["colliders"] if c["id"] == "hand.right")
    assert invalid["valid"] is False
    assert "radius_m" not in invalid

    # Invalid landmark null position.
    invalid_lm = next(lm for lm in env.header["landmarks"] if lm["name"] == "hand.right.wrist")
    assert invalid_lm["position_stage_m"] is None

    buffers = load_buffers(env.header, env.payload)
    raw_points = buffers.raw("points")
    xyz, rgba = unpack_points(raw_points, frame.cloud.count)
    np.testing.assert_allclose(xyz, frame.cloud.xyz_stage_m)
    np.testing.assert_array_equal(rgba, frame.cloud.rgba)


def test_character_frame_rejects_bad_alpha():
    frame = _make_character_frame()
    bad_rgba = frame.cloud.rgba.copy()
    bad_rgba[0, 3] = 128
    object.__setattr__(frame.cloud, "rgba", bad_rgba)
    with pytest.raises(ArrayInvariantError):
        encode_character_frame(frame)
