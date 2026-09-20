"""Person-target rig registration: a mis-placed side camera snaps onto the front one."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import numpy as np

from hmc_backend.calibration.synthetic import build_synthetic_rig
from hmc_backend.capture.replay import captured_frame_from_decoded
from hmc_backend.capture.rgbd_ingest import decode_rgbd_frame
from hmc_backend.contracts.internal import PairedFrames
from hmc_backend.fixtures.scene import encode_rgbd_packet
from hmc_backend.pipeline.factory import build_processor
from hmc_backend.protocol.envelope import decode_envelope
from hmc_backend.reconstruction.registration import _yaw_matrix, register_yaw_translation
from hmc_backend.settings import Settings

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from fake_phone import make_person_points, project_to_view  # noqa: E402


def _pair(rig, pose_seed: int = 3) -> PairedFrames:
    xyz, rgb = make_person_points(pose_seed)
    capture_id = uuid4()
    frames = []
    for i, dev in enumerate(("front-phone", "side-phone")):
        calib = rig.cameras[dev]
        colour, depth, conf = project_to_view(xyz, rgb, calib, splat=2)
        raw = encode_rgbd_packet(
            device_id=dev, session_id=uuid4(), capture_id=capture_id, sequence=i,
            capture_timestamp_s=100.0, calib=calib, rgb=colour, depth=depth, confidence=conf,
        )
        decoded = decode_rgbd_frame(decode_envelope(raw))
        frames.append(captured_frame_from_decoded(decoded, clock_offset_s=0.0, clock_uncertainty_ms=0.0))
    return PairedFrames(
        pair_id=uuid4(), first=frames[0], second=frames[1],
        normalized_capture_time_s=100.0, pair_skew_ms=0.0, calibration_id=rig.calibration_id,
    )


def _views(processor, pair):
    """Per-device stage clouds using the processor's effective calibration."""
    from hmc_backend.reconstruction.reconstruct import reconstruct_view

    out = {}
    for f in (pair.first, pair.second):
        det = processor._detector.detect_view(f)
        calib = processor._effective_calibration(f.device_id, f)
        out[f.device_id] = reconstruct_view(
            f, det, calib, processor._crop, confidence_min=1, source_bit=1
        ).xyz_stage_m
    return out


def _cloud_gap_m(views) -> float:
    a, b = views["front-phone"], views["side-phone"]
    return float(np.linalg.norm(a.mean(axis=0) - b.mean(axis=0)))


def test_registration_recovers_misplaced_side_camera(tmp_path):
    truth = build_synthetic_rig(orientation="landscape_right")
    pair = _pair(truth)  # rendered by the real (true) rig

    # The backend believes a wrong side pose: 20 degrees of yaw and a 30 cm shift.
    wrong_cam = truth.cameras["side-phone"]
    pert = np.eye(4)
    pert[:3, :3] = _yaw_matrix(np.radians(20.0))
    pert[:3, 3] = [0.3, 0.0, -0.2]
    wrong = replace(truth, cameras={**truth.cameras, "side-phone": replace(
        wrong_cam, T_stage_from_optical=pert @ wrong_cam.T_stage_from_optical)})

    settings = Settings(registration_path=str(tmp_path / "reg.json"), gravity_align=False)
    processor = build_processor(settings, wrong)
    # Two partial views of one body have different centroids even when the rig
    # is perfect, so measure against the true rig rather than against zero.
    baseline = _cloud_gap_m(_views(build_processor(settings, truth), pair))

    before = _cloud_gap_m(_views(processor, pair))
    assert before > baseline + 0.2, "perturbation should visibly separate the two views"

    processor.request_registration()
    processor.process(pair)
    assert processor.last_registration and processor.last_registration["ok"]

    after = _cloud_gap_m(_views(processor, pair))
    assert abs(after - baseline) < 0.05, f"gap {after:.3f} m vs true-rig {baseline:.3f} m"
    # The learned yaw should undo most of the injected 20 degrees.
    assert abs(abs(processor.last_registration["yaw_deg"]) - 20.0) < 4.0


def test_registration_is_idempotent_on_aligned_rig():
    truth = build_synthetic_rig(orientation="landscape_right")
    pair = _pair(truth)
    processor = build_processor(Settings(gravity_align=False), truth)
    processor.request_registration()
    processor.process(pair)
    last = processor.last_registration
    assert last and last["ok"]
    assert abs(last["yaw_deg"]) < 3.0
    assert np.linalg.norm(last["shift_m"]) < 0.08


def test_yaw_translation_icp_recovers_synthetic_offset():
    rng = np.random.default_rng(0)
    pts = rng.uniform(-1, 1, (3000, 3))
    ax = rng.integers(0, 3, 3000)
    pts[np.arange(3000), ax] = rng.choice([-1.0, 1.0], 3000)
    body = pts * [0.2, 0.4, 0.12] + [0, 1.1, 0]
    body = np.vstack([body, rng.uniform(-1, 1, (800, 3)) * [0.05, 0.3, 0.05] + [0.35, 1.2, 0.05]])
    front = body[body[:, 2] > 0]
    side = body[body[:, 0] > 0.02]
    r = _yaw_matrix(np.radians(30.0))
    t = np.array([0.4, 0.0, -0.3])
    moved = (r @ side.T).T + t
    T, rms, inliers = register_yaw_translation(moved, front)
    r_inv = np.linalg.inv(r)
    yaw_err = np.degrees(np.arctan2(T[0, 2], T[0, 0]) - np.arctan2(r_inv[0, 2], r_inv[0, 0]))
    assert abs(yaw_err) < 3.0
    assert rms < 0.03
