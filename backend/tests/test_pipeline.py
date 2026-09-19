"""Tests for collider validation, frame assembly, and the processor."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import numpy as np
import pytest

from hmc_backend.calibration.model import RigCalibration
from hmc_backend.colliders.validate import ColliderValidationError, validate_colliders
from hmc_backend.contracts.character_codec import encode_character_frame
from hmc_backend.contracts.internal import (
    CameraCalibration,
    CapturedFrame,
    CaptureGroup,
    Collider,
    ColoredPointCloud,
    FittedCharacter,
    Landmark3D,
)
from hmc_backend.pipeline.assembler import AssemblyError, FrameAssembler
from hmc_backend.pipeline.processor import CharacterProcessor
from hmc_backend.pipeline.snapshot_store import SnapshotStore
from hmc_backend.reconstruction.reconstruct import CropBounds
from hmc_backend.vision.fake import FakeCharacterFitter, FakePersonMaskDetector

# --- collider validation ---------------------------------------------------


def test_validate_rejects_non_orthonormal_obb():
    bad = Collider(
        "hand.left",
        "left_hand",
        "obb",
        True,
        "observed",
        0.5,
        center_stage_m=(0, 1, 0),
        axes_row_major=(1, 0, 0, 1, 0, 0, 0, 0, 1),  # first two rows parallel
        half_extents_m=(0.1, 0.1, 0.1),
    )
    with pytest.raises(ColliderValidationError):
        validate_colliders((bad,))


def test_validate_rejects_oversize_radius():
    bad = Collider(
        "head", "head", "sphere", True, "observed", 0.5, center_stage_m=(0, 1, 0), radius_m=2.0
    )
    with pytest.raises(ColliderValidationError):
        validate_colliders((bad,))


def test_validate_rejects_invalid_with_geometry():
    bad = Collider(
        "head", "head", "sphere", False, "disabled", None, center_stage_m=(0, 1, 0), radius_m=0.1
    )
    with pytest.raises(ColliderValidationError):
        validate_colliders((bad,))


def test_validate_rejects_duplicate_ids():
    a = Collider(
        "head", "head", "sphere", True, "observed", 0.5, center_stage_m=(0, 1, 0), radius_m=0.1
    )
    with pytest.raises(ColliderValidationError):
        validate_colliders((a, a))


# --- assembler -------------------------------------------------------------


def _cloud(n=10) -> ColoredPointCloud:
    xyz = np.random.default_rng(0).random((n, 3)).astype(np.float32)
    rgba = np.zeros((n, 4), np.uint8)
    rgba[:, 3] = 255
    return ColoredPointCloud(xyz, rgba, np.ones(n, np.uint8))


def _pair(calibration_id) -> CaptureGroup:
    f = CapturedFrame(
        "front-phone",
        uuid4(),
        uuid4(),
        1,
        0.0,
        0.0,
        1.0,
        np.zeros((2, 2, 3), np.uint8),
        np.ones((2, 2), np.float32),
        np.full((2, 2), 2, np.uint8),
        np.eye(3),
        np.eye(4),
    )
    s = CapturedFrame(
        "side-phone",
        f.session_id,
        f.capture_id,
        2,
        0.0,
        0.0,
        1.0,
        np.zeros((2, 2, 3), np.uint8),
        np.ones((2, 2), np.float32),
        np.full((2, 2), 2, np.uint8),
        np.eye(3),
        np.eye(4),
    )
    return CaptureGroup(uuid4(), (f, s), 0.0, 10.0, calibration_id)


def test_assembler_monotonic_frame_id():
    cid = uuid4()
    asm = FrameAssembler(uuid4())
    fitted = FittedCharacter((), ())
    f1 = asm.assemble(_pair(cid), _cloud(), fitted, mode="snapshot", calibration_id=cid)
    f2 = asm.assemble(_pair(cid), _cloud(), fitted, mode="snapshot", calibration_id=cid)
    assert f1.frame_id == 1
    assert f2.frame_id == 2


def test_assembler_rejects_calibration_mismatch():
    asm = FrameAssembler(uuid4())
    pair = _pair(uuid4())
    with pytest.raises(AssemblyError):
        asm.assemble(
            pair, _cloud(), FittedCharacter((), ()), mode="snapshot", calibration_id=uuid4()
        )
    # A rejected assembly does not advance the frame counter.
    assert asm.next_frame_id == 1


def test_assembler_rejects_duplicate_landmarks():
    cid = uuid4()
    asm = FrameAssembler(uuid4())
    dup = (
        Landmark3D("body.head_center", (0, 1, 0), True, "derived"),
        Landmark3D("body.head_center", (0, 1, 0), True, "derived"),
    )
    with pytest.raises(AssemblyError):
        asm.assemble(
            _pair(cid), _cloud(), FittedCharacter(dup, ()), mode="snapshot", calibration_id=cid
        )


# --- processor end to end --------------------------------------------------


def _identity_rig(cid):
    k = np.array([[100.0, 0, 8], [0, 100, 6], [0, 0, 1]])

    def cam(dev):
        return CameraCalibration(
            cid, dev, (16, 12), (16, 12), k.copy(), np.eye(4), 1.0, datetime.now(UTC)
        )

    cameras = {"front-phone": cam("front-phone"), "side-phone": cam("side-phone")}
    return RigCalibration(cid, datetime.now(UTC), {}, None, cameras, {})


def _plane_frame(device_id, cid):
    depth = np.full((12, 16), 2.0, np.float32)
    return CapturedFrame(
        device_id,
        uuid4(),
        cid,
        1 if device_id == "front-phone" else 2,
        0.0,
        0.0,
        1.0,
        np.full((12, 16, 3), 100, np.uint8),
        depth,
        np.full((12, 16), 2, np.uint8),
        np.eye(3),
        np.eye(4),
    )


def test_processor_end_to_end_publishes_valid_frame():
    cid = uuid4()
    rig = _identity_rig(cid)
    capture_id = uuid4()
    f = _plane_frame("front-phone", capture_id)
    s = _plane_frame("side-phone", capture_id)
    pair = CaptureGroup(uuid4(), (f, s), 0.0, 12.0, cid)

    crop = CropBounds(-5, 5, -5, 5, -5, 5, 0.1, 10.0)
    proc = CharacterProcessor(
        FakePersonMaskDetector(),
        FakeCharacterFitter(),
        rig,
        FrameAssembler(uuid4()),
        crop,
        voxel_size_m=0.02,
        max_points=50_000,
        confidence_min=1,
    )
    frame = proc.process(pair, mode="snapshot")

    assert frame.frame_id == 1
    assert frame.quality.point_count > 0
    assert frame.calibration_id == cid
    # Source refs come straight from the consumed pair.
    assert {r.device_id for r in frame.source_frames} == {"front-phone", "side-phone"}

    # The assembled frame serializes to a valid CHARACTER_FRAME.
    raw = encode_character_frame(frame)
    assert raw[:4] == b"HMC1"

    # Snapshot store swaps atomically.
    store = SnapshotStore()
    store.publish(frame, raw)
    assert store.latest_frame_id == 1
    assert store.latest_encoded == raw
