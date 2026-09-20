"""Opposing half-shells have no corresponding skin points: fit body profiles instead."""

from dataclasses import replace
from uuid import uuid4

import numpy as np
import pytest

from hmc_backend.calibration.synthetic import build_synthetic_rig, look_at_optical
from hmc_backend.contracts.internal import ColoredPointCloud, Landmark2DObservation, ViewDetection
from hmc_backend.pipeline.factory import build_processor
from hmc_backend.reconstruction.body_merge import (
    BodyMergeRejected,
    BodyWarp,
    assemble_opposing_body,
    opposed_calibrations,
)
from hmc_backend.reconstruction.reconstruct import reconstruct_view
from hmc_backend.settings import Settings
from hmc_backend.vision.landmarks import (
    FusionConfig,
    ViewInput,
    _depth_observations,
    fuse_landmarks,
)
from hmc_backend.vision.overlays import project_stage_to_rgb
from tests.test_rig_registration import _pair


def rig():
    r = build_synthetic_rig(orientation="landscape_right")
    rear = replace(
        r.camera("side-phone"),
        T_stage_from_optical=look_at_optical(
            np.array([0.0, 1.1, -2.5]), np.array([0.0, 1.1, 0.0]), np.array([0.0, 1.0, 0.0])
        ),
    )
    return replace(r, cameras={**r.cameras, "side-phone": rear})


def shell(front=True, shift=(0.0, 0.0, 0.0), *, lean=0.0, flat=False):
    points = []
    # No shared samples at the silhouette edge: independently sampled opposite arcs.
    theta = np.linspace(0.025, np.pi - 0.025, 90) + (0 if front else 0.006)
    for y in np.linspace(0.04, 1.7, 130):
        if y < 0.87:
            sections = [(-0.12, 0.085, 0.075), (0.12, 0.085, 0.075)]
        else:
            sections = [(0.0, 0.23, 0.13)]
        for center, radius, depth in sections:
            for t in theta:
                x = center + radius * np.cos(t) + lean * y
                z = depth if flat else depth * np.sin(t)
                points.append((x, y, z if front else -z))
    p = (np.array(points) + shift).astype(np.float32)
    return ColoredPointCloud(
        p, np.full((len(p), 4), 255, np.uint8), np.full(len(p), 1 if front else 2, np.uint8)
    )


def assemble(front, back, **kwargs):
    r = rig()
    return assemble_opposing_body(
        {"front-phone": front, "side-phone": back}, r.cameras, "front-phone", {}, **kwargs
    )


def test_meter_apart_shells_align_torso_and_each_leg_without_collapsing_depth():
    front, back = shell(), shell(False, (1.1, 0.18, -0.8), lean=0.07)
    out, warps, status = assemble(front, back)
    assert status["state"] == "applied"
    assert status["max_lateral_shift_m"] > 1
    a, b = out["front-phone"].xyz_stage_m, out["side-phone"].xyz_stage_m
    for lo, hi in ((0.2, 0.4), (0.5, 0.7), (1.05, 1.25), (1.35, 1.55)):
        f, r = a[(a[:, 1] > lo) & (a[:, 1] < hi)], b[(b[:, 1] > lo) & (b[:, 1] < hi)]
        assert abs(np.median(f[:, 0]) - np.median(r[:, 0])) < 0.025
        if hi < 0.8:
            for sign in (-1, 1):
                assert (
                    abs(np.median(f[f[:, 0] * sign > 0, 0]) - np.median(r[r[:, 0] * sign > 0, 0]))
                    < 0.025
                )
        assert 0.115 <= np.median(f[:, 2]) - np.median(r[:, 2]) < 0.27
        assert np.quantile(f[:, 2], 0.05) - np.quantile(r[:, 2], 0.95) < 0.055
    assert set(warps) == {"side-phone"}
    assert out["side-phone"].count == back.count
    np.testing.assert_array_equal(out["side-phone"].rgba, back.rgba)
    np.testing.assert_array_equal(out["side-phone"].source_mask, back.source_mask)
    np.testing.assert_array_equal(
        back.xyz_stage_m, shell(False, (1.1, 0.18, -0.8), lean=0.07).xyz_stage_m
    )


def test_aligned_shells_keep_front_and_back_order_and_real_thickness():
    out, _, _ = assemble(shell(), shell(False))
    for y in (0.4, 1.2):
        slices = [c.xyz_stage_m[np.abs(c.xyz_stage_m[:, 1] - y) < 0.04] for c in out.values()]
        assert np.median(slices[0][:, 2]) > np.median(slices[1][:, 2]) + 0.115
        assert max(np.ptp(p[:, 2]) for p in slices) > 0.06


def test_planar_observations_use_explicit_thickness_prior_not_zero_error_flattening():
    out, _, status = assemble(shell(flat=True), shell(False, (1, 0.1, 1), flat=True))
    assert status["depth_prior_used"]
    a, b = [c.xyz_stage_m for c in out.values()]
    a, b = a[a[:, 1] > 1], b[b[:, 1] > 1]
    assert np.median(a[:, 2]) - np.median(b[:, 2]) == pytest.approx(0.12, abs=0.002)


def test_moving_arms_do_not_move_body_profile():
    front, back = shell(), shell(False, (1, 0, 0.5))
    baseline, _, _ = assemble(front, back)
    arms = (
        np.random.default_rng(4)
        .uniform([1.7, 1.25, 0.45], [2.4, 1.5, 0.6], (2000, 3))
        .astype(np.float32)
    )
    back_with_arm = ColoredPointCloud(
        np.vstack([back.xyz_stage_m, arms]),
        np.full((back.count + len(arms), 4), 255, np.uint8),
        np.full(back.count + len(arms), 2, np.uint8),
    )
    result, _, _ = assemble(front, back_with_arm)
    np.testing.assert_allclose(
        result["side-phone"].xyz_stage_m[: back.count],
        baseline["side-phone"].xyz_stage_m,
        atol=0.003,
    )
    assert np.ptp(result["side-phone"].xyz_stage_m[-len(arms) :, 0]) > 0.65


@pytest.mark.parametrize("bad", ["empty", "only_head", "incompatible_width"])
def test_missing_or_incompatible_person_is_not_claimed_as_success(bad):
    front, back = shell(), shell(False)
    if bad == "empty":
        back = replace(
            back,
            xyz_stage_m=np.empty((0, 3), np.float32),
            rgba=np.empty((0, 4), np.uint8),
            source_mask=np.empty(0, np.uint8),
        )
    elif bad == "only_head":
        keep = back.xyz_stage_m[:, 1] > 1.5
        back = ColoredPointCloud(back.xyz_stage_m[keep], back.rgba[keep], back.source_mask[keep])
    else:
        p = back.xyz_stage_m.copy()
        p[:, 0] *= 0.2
        back = replace(back, xyz_stage_m=p)
    with pytest.raises(BodyMergeRejected):
        assemble(front, back)


def test_yaw_lock_is_rigid_and_does_not_edit_saved_camera_calibration():
    r = build_synthetic_rig(orientation="portrait")
    old = r.camera("side-phone").T_stage_from_optical.copy()
    fixed = opposed_calibrations(r.cameras, "front-phone")
    a, b = [fixed[d].T_stage_from_optical[:3, 2] for d in ("front-phone", "side-phone")]
    np.testing.assert_allclose(a, -b, atol=1e-10)
    np.testing.assert_allclose(
        fixed["side-phone"].T_stage_from_optical[:3, :3].T
        @ fixed["side-phone"].T_stage_from_optical[:3, :3],
        np.eye(3),
        atol=1e-10,
    )
    np.testing.assert_array_equal(r.camera("side-phone").T_stage_from_optical, old)


def test_warp_inverse_preserves_projection_and_cloud_landmark_mapping():
    _, warps, _ = assemble(shell(), shell(False, (1, 0.2, 0.8)))
    w = warps["side-phone"]
    p = np.random.default_rng(0).uniform([-0.7, 0.1, -0.2], [0.7, 1.7, 0.2], (100, 3)) + [
        1,
        0.2,
        0.8,
    ]
    mapped = w.apply(p)
    np.testing.assert_allclose(w.apply(mapped, inverse=True), p, atol=1e-10)
    c = rig().camera("side-phone")
    expected, ef = project_stage_to_rgb(p, c)
    actual, af = project_stage_to_rgb(mapped, c, w)
    np.testing.assert_allclose(actual, expected, atol=1e-9)
    np.testing.assert_array_equal(af, ef)


def test_pipeline_recovers_rear_outside_stage_crop_and_keeps_source_bits():
    truth = rig()
    pair = _pair(truth)
    camera = truth.camera("side-phone")
    moved = camera.T_stage_from_optical.copy()
    moved[:3, 3] += [3.5, 0.15, -0.8]
    wrong = replace(
        truth, cameras={**truth.cameras, "side-phone": replace(camera, T_stage_from_optical=moved)}
    )
    processor = build_processor(Settings(gravity_align=False), wrong)
    before = reconstruct_view(
        pair.second,
        processor.detector.detect_view(pair.second),
        wrong.camera("side-phone"),
        processor._crop,
        confidence_min=1,
        source_bit=2,
    )
    assert before.count == 0
    result = processor.process(pair)
    assert processor.body_merge_status["state"] == "applied", processor.body_merge_status
    assert "forced_opposing_body_merge" in result.quality.warnings
    assert np.count_nonzero(result.cloud.source_mask & 2) > 100
    assert not any(w.startswith("missing_view") for w in result.quality.warnings)
    assert not processor.corrections  # Never persisted as a rig extrinsic.


def test_failed_next_pair_does_not_reuse_previous_body_warp():
    truth = rig()
    pair = _pair(truth)
    processor = build_processor(Settings(gravity_align=False), truth)
    processor.process(pair)
    assert processor.view_warps
    empty = replace(pair.second, depth_m=np.zeros_like(pair.second.depth_m), session_id=uuid4())
    result = processor.process(replace(pair, second=empty))
    assert not processor.view_warps
    assert processor.body_merge_status["state"] == "unavailable"
    assert any(w.startswith("body_merge_unavailable") for w in result.quality.warnings)
    assert "missing_view:side-phone:valid_depth" in result.quality.warnings
    assert result.cloud.count > 0


def test_landmark_depth_uses_same_warp_and_does_not_triangulate_unwarped_rays():
    r = rig()
    pair = _pair(r)
    f = pair.first
    # Uniform test depth supplies a reliable landmark near the image center.
    f = replace(f, depth_m=np.full_like(f.depth_m, 2.0))
    det = ViewDetection(
        f.device_id,
        f.capture_id,
        np.ones(f.rgb.shape[:2], bool),
        (Landmark2DObservation("left_wrist", (160, 120), None, 1.0, 1.0, True),),
        (),
        (),
        None,
    )
    c = r.camera(f.device_id)
    warp = BodyWarp(
        np.eye(3),
        np.array([0.0, 2.0]),
        np.zeros(2),
        np.full(2, 0.3),
        np.ones(2),
        np.ones(2),
        np.full(2, -0.2),
        0.1,
    )
    original = _depth_observations(ViewInput(f, det, c), FusionConfig())["body.left_wrist"]
    view = ViewInput(f, det, c, warp)
    warped = _depth_observations(view, FusionConfig())["body.left_wrist"]
    np.testing.assert_allclose(
        warped.position_stage_m, warp.apply(np.array(original.position_stage_m)[None])[0]
    )
    fused = fuse_landmarks([view], FusionConfig())
    assert (
        fused.report.per_landmark["body.left_wrist"]["triangulation"]["reason"]
        == "forced_body_space"
    )
    np.testing.assert_allclose(
        fused.by_name()["body.left_wrist"].position_stage_m, warped.position_stage_m
    )


def test_front_reference_does_not_depend_on_pair_arrival_order():
    truth = rig()
    pair = _pair(truth)
    processor = build_processor(Settings(gravity_align=False), truth)
    result = processor.process(replace(pair, first=pair.second, second=pair.first))
    assert processor.body_merge_status["reference_device"] == "front-phone"
    assert processor.body_merge_status["rear_device"] == "side-phone"
    assert set(processor.view_warps) == {"side-phone"}
    assert result.source_frames[0].device_id == "side-phone"
    assert np.count_nonzero(result.cloud.source_mask & 1) > 100
    assert np.count_nonzero(result.cloud.source_mask & 2) > 100


def test_limited_tracking_and_disabled_mode_do_not_force_alignment():
    truth = rig()
    pair = _pair(truth)
    processor = build_processor(Settings(gravity_align=False), truth)
    result = processor.process(replace(pair, second=replace(pair.second, tracking_state="limited")))
    assert "body_merge_unavailable:tracking_not_normal" in result.quality.warnings
    assert not processor.view_warps
    original = build_processor(Settings(gravity_align=False, opposing_body_merge=False), truth)
    result = original.process(pair)
    assert original.body_merge_status["state"] == "disabled"
    assert not original.view_warps
    assert "forced_opposing_body_merge" not in result.quality.warnings


def test_debug_artifacts_use_current_body_space(tmp_path):
    import json

    truth = rig()
    pair = _pair(truth)
    processor = build_processor(
        Settings(gravity_align=False, debug_artifacts_dir=str(tmp_path)), truth
    )
    processor.process(pair)
    report = json.loads((tmp_path / str(pair.pair_id) / "report.json").read_text())
    assert report["body_merge"]["state"] == "applied"
    assert (tmp_path / str(pair.pair_id) / "overlay_side-phone.png").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("body_merge_min_thickness_m", 0),
        ("body_merge_min_thickness_m", float("nan")),
        ("body_merge_seam_overlap_m", -0.01),
        ("body_merge_seam_overlap_m", 0.04),
    ],
)
def test_body_merge_settings_reject_invalid_limits(field, value):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(**{field: value})
