"""The known-skeleton fixture is self-consistent with the rig it is rendered into."""

from __future__ import annotations

import numpy as np

from hmc_backend.calibration.synthetic import build_synthetic_rig
from hmc_backend.fixtures import skeleton as skf
from hmc_backend.vision.model_mapping import HAND_LANDMARK_NAMES, POSE_LANDMARK_NAMES, pose_index
from hmc_backend.vision.triangulation import project_to_pixel


def test_skeletons_cover_every_model_landmark():
    for sk in (skf.neutral_skeleton(), skf.right_arm_raised_skeleton(), skf.lifted_turned_foot_skeleton()):
        assert set(sk.body) == set(POSE_LANDMARK_NAMES)
        for side in ("left", "right"):
            assert set(sk.hands[side]) == set(HAND_LANDMARK_NAMES)
            # The pose model's wrist and the hand model's wrist coincide.
            assert sk.hands[side]["wrist"] == sk.body[f"{side}_wrist"]


def test_asymmetric_pose_is_actually_asymmetric():
    sk = skf.right_arm_raised_skeleton()
    assert sk.body["right_wrist"][1] > 1.8 and sk.body["left_wrist"][1] < 1.0


def test_lifted_foot_is_off_the_floor_and_turned():
    sk = skf.lifted_turned_foot_skeleton()
    assert sk.planted_feet == ("right",)
    assert sk.body["left_heel"][1] > 0.15
    heel, toe = np.asarray(sk.body["left_heel"]), np.asarray(sk.body["left_foot_index"])
    d = toe - heel
    assert abs(d[0]) > 0.05  # rotated out of the sagittal plane


def test_rendered_frames_and_detector_agree_with_projection():
    rig = build_synthetic_rig(rgb_size=(320, 240), depth_size=(320, 240))
    sk = skf.neutral_skeleton()
    frames = skf.render_frames(rig, sk)
    assert set(frames) == set(rig.cameras)
    det = skf.SkeletonViewDetector(rig, sk)
    for device_id, frame in frames.items():
        assert frame.depth_m.shape == (240, 320) and (frame.depth_m > 0).sum() > 2000
        view = det.detect_view(frame)
        assert view.person_mask.shape == frame.rgb.shape[:2]
        assert view.person_mask.sum() == (frame.depth_m > 0).sum()
        calib = rig.camera(device_id)
        knee = next(lm for lm in view.body if lm.name == "left_knee")
        assert knee.valid and knee.xy_px == project_to_pixel(sk.body["left_knee"], calib)
        # Prior is hip-centred (hips average to ~0), not stage coordinates.
        prior = view.pose_world_prior_m
        hips = (prior[pose_index("left_hip")] + prior[pose_index("right_hip")]) / 2.0
        assert np.linalg.norm(hips) < 1e-5
        assert len(view.left_hand) == 21 and set(view.hand_world_priors_m) == {"left", "right"}


def test_ground_truth_frames_are_orthonormal():
    sk = skf.neutral_skeleton()
    for centre, axes in (skf.hand_frame(sk, "left"), skf.foot_frame(sk, "right"), *skf.torso_frames(sk)):
        assert np.allclose(axes @ axes.T, np.eye(3), atol=1e-9)
        assert np.isfinite(centre).all()

