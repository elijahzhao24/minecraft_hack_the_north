"""Model manifest pins and the pure MediaPipe-result conversion (no weights needed)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from hmc_backend.vision import detector as det
from hmc_backend.vision import models as vm
from hmc_backend.vision.hand_association import HandCrop
from hmc_backend.vision.model_mapping import hand_index, pose_index

BACKEND_ROOT = Path(__file__).resolve().parents[1]


# --- manifest ----------------------------------------------------------------


def _write_manifest(tmp_path: Path, *, pose_sha, hand_sha) -> Path:
    doc = {
        "schema": "hmc.model_manifest",
        "schema_version": 1,
        "models": {
            "pose": {"filename": "pose.task", "url": "https://example.invalid/pose.task", "sha256": pose_sha},
            "hands": {"filename": "hand.task", "url": "https://example.invalid/hand.task", "sha256": hand_sha},
        },
    }
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(doc))
    return p


def test_checked_in_manifest_parses_and_names_both_models():
    entries = vm.load_manifest(BACKEND_ROOT / "models" / "manifest.json")
    assert set(entries) >= {"pose", "hands"}
    assert entries["pose"].url and entries["hands"].url


def test_resolve_models_verifies_sha256(tmp_path: Path):
    pose = tmp_path / "pose.task"
    hand = tmp_path / "hand.task"
    pose.write_bytes(b"pose-bytes")
    hand.write_bytes(b"hand-bytes")
    ps, hs = hashlib.sha256(b"pose-bytes").hexdigest(), hashlib.sha256(b"hand-bytes").hexdigest()
    manifest = _write_manifest(tmp_path, pose_sha=ps.upper(), hand_sha=hs)
    resolved = vm.resolve_models(manifest)
    assert resolved.pose_path == pose and resolved.pose_sha256 == ps
    assert resolved.hand_path == hand

    # Mismatch -> refused.
    hand.write_bytes(b"tampered")
    with pytest.raises(vm.ModelManifestError, match="mismatch"):
        vm.resolve_models(manifest)


def test_resolve_models_refuses_unpinned_or_missing(tmp_path: Path):
    (tmp_path / "pose.task").write_bytes(b"x")
    (tmp_path / "hand.task").write_bytes(b"y")
    unpinned = _write_manifest(tmp_path, pose_sha=None, hand_sha=hashlib.sha256(b"y").hexdigest())
    with pytest.raises(vm.ModelManifestError, match="no sha256"):
        vm.resolve_models(unpinned)
    (tmp_path / "pose.task").unlink()
    pinned = _write_manifest(
        tmp_path, pose_sha=hashlib.sha256(b"x").hexdigest(), hand_sha=hashlib.sha256(b"y").hexdigest()
    )
    with pytest.raises(vm.ModelManifestError, match="missing"):
        vm.resolve_models(pinned)


def test_manifest_schema_and_required_models_enforced(tmp_path: Path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"schema": "wrong", "schema_version": 1, "models": {}}))
    with pytest.raises(vm.ModelManifestError):
        vm.load_manifest(p)
    p.write_text(json.dumps({"schema": "hmc.model_manifest", "schema_version": 1, "models": {"pose": {"filename": "a"}}}))
    with pytest.raises(vm.ModelManifestError, match="required"):
        vm.load_manifest(p)


# --- pure conversion ---------------------------------------------------------

CFG = det.DetectorConfig()


def _frame(w=64, h=48):
    from hmc_backend.contracts.internal import CapturedFrame

    return CapturedFrame(
        "front-phone", uuid4(), uuid4(), 1, 0.0, 0.0, 1.0,
        np.zeros((h, w, 3), np.uint8), np.ones((h // 2, w // 2), np.float32),
        np.full((h // 2, w // 2), 2, np.uint8), np.eye(3), np.eye(4),
    )


def _raw_pose(w=64, h=48, *, hide: set[str] = frozenset()) -> det.RawPose:
    arr = np.zeros((33, 5))
    arr[:, 0] = 0.5
    arr[:, 1] = 0.5
    arr[:, 3] = 0.95
    arr[:, 4] = 0.95
    arr[pose_index("left_wrist"), :2] = (0.75, 0.6)
    arr[pose_index("left_elbow"), :2] = (0.7, 0.4)
    arr[pose_index("right_wrist"), :2] = (0.25, 0.6)
    arr[pose_index("right_elbow"), :2] = (0.3, 0.4)
    for n in hide:
        arr[pose_index(n), 3] = 0.1
    arr[pose_index("nose"), :2] = (1.2, 0.5)  # outside the image
    return det.RawPose(arr, np.zeros((33, 3), np.float32), np.ones((h, w), np.float32))


def test_pose_to_observations_preserves_visibility_and_validity():
    obs = det.pose_to_observations(_raw_pose(hide={"left_knee"}), (64, 48), CFG)
    assert len(obs) == 33
    by = {o.name: o for o in obs}
    assert by["left_wrist"].xy_px == pytest.approx((48.0, 28.8))
    assert by["left_wrist"].visibility == pytest.approx(0.95)
    assert by["left_wrist"].valid
    assert not by["left_knee"].valid and by["left_knee"].visibility == pytest.approx(0.1)
    assert not by["nose"].valid  # outside raster


def test_hand_to_candidate_maps_through_crop_affine():
    norm = np.zeros((21, 3))
    norm[hand_index("wrist"), :2] = (0.5, 0.5)
    norm[hand_index("index_tip"), :2] = (0.25, 0.0)
    crop = HandCrop(x0=100, y0=200, size=64, image_wh=(640, 480))
    cand = det.hand_to_candidate(det.RawHand(norm, "Left", 0.9, None, crop), (640, 480))
    assert cand.wrist_xy == pytest.approx((132.0, 232.0))
    assert cand.landmarks[hand_index("index_tip")].xy_px == pytest.approx((116.0, 200.0))
    full = det.hand_to_candidate(det.RawHand(norm, "Left", 0.9, None, None), (640, 480))
    assert full.wrist_xy == pytest.approx((320.0, 240.0))


def test_build_view_detection_assigns_hands_and_priors():
    frame = _frame()
    body = det.pose_to_observations(_raw_pose(), (64, 48), CFG)

    def hand(cx, cy, label):
        norm = np.zeros((21, 3))
        norm[:, 0] = cx / 64.0
        norm[:, 1] = cy / 48.0
        norm[1:, 0] += np.linspace(-0.02, 0.02, 20)
        norm[1:, 1] += 0.05
        return det.hand_to_candidate(det.RawHand(norm, label, 0.9, np.full((21, 3), 0.01, np.float32), None), (64, 48))

    # Anatomical left wrist is at image x=48 (front view); the model labels it "Right".
    cands = [hand(48.0, 30.0, "Right"), hand(16.0, 30.0, "Left")]
    view = det.build_view_detection(
        frame, person_mask=np.ones((48, 64), np.bool_), body=body, candidates=cands,
        pose_world_prior_m=np.zeros((33, 3), np.float32), cfg=CFG,
    )
    assert len(view.left_hand) == 21 and len(view.right_hand) == 21
    assert view.left_hand[0].xy_px[0] > view.right_hand[0].xy_px[0]
    assert set(view.hand_world_priors_m) == {"left", "right"}
    assert view.person_mask.dtype == np.bool_ and view.pose_world_prior_m.shape == (33, 3)


def test_hands_need_crops_lists_visible_wrists_without_a_hand():
    body = det.pose_to_observations(_raw_pose(hide={"right_wrist"}), (64, 48), CFG)
    arms = det.body_arms(body)
    assert arms["right"].wrist_xy is None
    assert det.hands_need_crops([], arms, CFG) == ["left"]
    assert det.hands_need_crops([], arms, det.DetectorConfig(crop_fallback=False)) == []


def test_empty_detection_has_false_mask_and_no_landmarks():
    view = det.empty_detection(_frame())
    assert view.person_mask.shape == (48, 64) and not view.person_mask.any()
    assert view.body == () and view.left_hand == () and view.pose_world_prior_m is None


def test_mediapipe_detector_is_lazy_and_construction_needs_no_weights(tmp_path: Path):
    models = vm.ResolvedModels(tmp_path / "p", tmp_path / "h", "0" * 64, "0" * 64)
    d = det.MediaPipeViewDetector(models)
    assert d._pose is None  # nothing loaded until first detect_view
    d.close()

