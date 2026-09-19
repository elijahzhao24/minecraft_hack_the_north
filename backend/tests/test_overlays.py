"""Debug artifacts: overlays draw at the right pixels, wireframes are complete, report is JSON."""

from __future__ import annotations

import json
from uuid import uuid4

import numpy as np
import pytest

from hmc_backend.calibration.synthetic import build_synthetic_rig
from hmc_backend.colliders.fitter import AnatomicalCharacterFitter
from hmc_backend.colliders.models import DisabledCollider, make_capsule, make_obb, make_sphere
from hmc_backend.contracts.enums import BodyPart, FitSource
from hmc_backend.contracts.internal import ColoredPointCloud, PairedFrames
from hmc_backend.fixtures import skeleton as skf
from hmc_backend.reconstruction.reconstruct import CropBounds, merge_clouds, reconstruct_view
from hmc_backend.vision import overlays as ov
from hmc_backend.vision.model_mapping import body_name

CROP = CropBounds(-1.5, 1.5, -0.1, 2.5, -1.5, 1.5, 0.2, 5.0)


@pytest.fixture(scope="module")
def fitted_scene():
    rig = build_synthetic_rig(rgb_size=(480, 360), depth_size=(480, 360))
    sk = skf.neutral_skeleton()
    frames = skf.render_frames(rig, sk)
    det = skf.SkeletonViewDetector(rig, sk)
    ids = list(frames)
    detections = {d: det.detect_view(frames[d]) for d in ids}
    clouds = [
        reconstruct_view(frames[d], detections[d], rig.camera(d), CROP, confidence_min=1, source_bit=1 << i)
        for i, d in enumerate(ids)
    ]
    cloud = merge_clouds(clouds, voxel_size_m=0.01, max_points=60_000, seed=1)
    pair = PairedFrames(uuid4(), frames[ids[0]], frames[ids[1]], 0.0, 5.0, rig.calibration_id)
    fitter = AnatomicalCharacterFitter(rig)
    fitted = fitter.fit_character(pair, detections, cloud, rig.camera(ids[0]))
    return rig, sk, frames, detections, cloud, fitter, fitted


def test_projection_matches_fixture_detector_pixels(fitted_scene):
    rig, sk, _frames, detections, *_ = fitted_scene
    for d, det in detections.items():
        obs = {o.name: o for o in det.body}
        for short in ("left_shoulder", "right_knee", "nose"):
            uv, front = ov.project_stage_to_rgb(np.asarray(sk.body[short])[None], rig.camera(d))
            assert front[0]
            assert np.allclose(uv[0], obs[short].xy_px, atol=1.0), (d, short, uv[0], obs[short].xy_px)


def test_overlay_draws_landmarks_and_depth_samples(fitted_scene):
    rig, _sk, frames, detections, _cloud, fitter, fitted = fitted_scene
    d = next(iter(frames))
    frame = frames[d]
    by_name = {lm.name: lm for lm in fitted.landmarks}
    img = ov.draw_view_overlay(
        frame, detections[d], rig.camera(d),
        per_landmark_report=fitter.last_report.fusion.per_landmark,
        landmarks=by_name, colliders=fitter.last_typed_colliders,
    )
    assert img.shape == frame.rgb.shape and img.dtype == np.uint8
    changed = np.any(img != frame.rgb, axis=2)
    assert changed.mean() > 0.005  # something was drawn
    # A valid body landmark is marked in the validity colour near its pixel.
    o = next(o for o in detections[d].body if o.name == "left_shoulder")
    x, y = round(o.xy_px[0]), round(o.xy_px[1])
    patch = img[max(0, y - 4) : y + 5, max(0, x - 4) : x + 5].reshape(-1, 3)
    assert any(tuple(p) == ov.COLOR_VALID for p in patch)
    # Depth-sample markers exist for this device.
    depth_px = [
        dd["pixel_rgb"]
        for rec in fitter.last_report.fusion.per_landmark.values()
        for dd in rec.get("depth", [])
        if dd["device"] == d
    ]
    assert depth_px
    x, y = round(depth_px[0][0]), round(depth_px[0][1])
    patch = img[max(0, y - 5) : y + 6, max(0, x - 5) : x + 6].reshape(-1, 3)
    assert any(tuple(p) == ov.COLOR_DEPTH for p in patch)
    # The image is unchanged where nothing lives (top-left corner is background).
    assert np.array_equal(img[:5, :5], frame.rgb[:5, :5])


def test_wireframes_have_expected_edge_counts():
    sphere = make_sphere("head", BodyPart.HEAD, (0, 1.6, 0), 0.1, FitSource.OBSERVED, 0.9)
    capsule = make_capsule("arm.left.upper", BodyPart.LEFT_UPPER_ARM, (0, 1, 0), (0, 0.7, 0), 0.05, FitSource.OBSERVED, 0.9)
    obb = make_obb("hand.left", BodyPart.LEFT_HAND, (0, 0.5, 0), np.eye(3), (0.09, 0.05, 0.02), FitSource.OBSERVED, 0.9)
    assert len(ov.collider_wireframe(sphere)) == 72  # three 24-segment circles
    assert len(ov.collider_wireframe(obb)) == 12
    caps = ov.collider_wireframe(capsule)
    assert len(caps) >= 24 * 2 + 4
    # OBB corners lie at the correct half-extent distances.
    corners = np.unique(np.round(np.concatenate([np.stack([a for a, _ in ov.collider_wireframe(obb)]), np.stack([b for _, b in ov.collider_wireframe(obb)])]), 6), axis=0)
    assert corners.shape[0] == 8
    assert np.allclose(np.abs(corners - np.array([0, 0.5, 0])).max(axis=0), [0.09, 0.05, 0.02])
    assert ov.collider_wireframe(DisabledCollider("head", BodyPart.HEAD, sphere.type, "x")) == []


def test_skeleton_edges_use_only_valid_landmarks(fitted_scene):
    *_, fitted = fitted_scene
    by_name = {lm.name: lm for lm in fitted.landmarks}
    edges = ov.skeleton_edges(by_name)
    assert len(edges) >= len(ov.BODY_EDGES) + 2 * len(ov.HAND_EDGES) - 2
    from dataclasses import replace

    by_name[body_name("left_knee")] = replace(by_name[body_name("left_knee")], valid=False, position_stage_m=None)
    fewer = ov.skeleton_edges(by_name)
    assert len(fewer) == len(edges) - 2  # hip-knee and knee-ankle dropped


def test_cloud_source_colors_follow_bitset():
    xyz = np.zeros((3, 3), np.float32)
    cloud = ColoredPointCloud(xyz, np.zeros((3, 4), np.uint8), np.array([1, 2, 3], np.uint8))
    cols = ov.cloud_source_colors(cloud)
    assert tuple(cols[0]) == ov.SOURCE_PALETTE[0]
    assert tuple(cols[1]) == ov.SOURCE_PALETTE[1]
    blend = (np.asarray(ov.SOURCE_PALETTE[0]) + np.asarray(ov.SOURCE_PALETTE[1])) / 2
    assert np.allclose(cols[2], blend, atol=1)


def test_write_debug_artifacts_and_json_report(fitted_scene, tmp_path):
    rig, _sk, frames, detections, cloud, fitter, fitted = fitted_scene
    written = ov.write_debug_artifacts(
        tmp_path / "dbg",
        frames=frames,
        detections=detections,
        calibrations={d: rig.camera(d) for d in frames},
        cloud=cloud,
        landmarks=fitted.landmarks,
        colliders=fitter.last_typed_colliders,
        fit_report=fitter.last_report,
        extra={"capture": "unit-test"},
    )
    for key in ("cloud", "wireframe", "report", *(f"overlay_{d}" for d in frames)):
        assert written[key].exists() and written[key].stat().st_size > 0, key
    doc = json.loads(written["report"].read_text())
    assert doc["capture"] == "unit-test"
    assert doc["coverage"]["enabled"] == 15
    assert len(doc["landmarks"]) == len(fitted.landmarks)
    assert {c["id"] for c in doc["colliders"]} == {c.id for c in fitter.last_typed_colliders}
    heads = next(c for c in doc["colliders"] if c["id"] == "head")
    assert heads["fit_source"] == "observed" and "radius_m" in heads
    assert "per_landmark" in doc["fusion"] and doc["fusion"]["pose_registration"] is not None
    assert all("support" in r for r in doc["collider_reports"].values())
    # PLY headers are well formed.
    head = written["wireframe"].read_text().splitlines()[:10]
    assert head[0] == "ply" and any(line.startswith("element edge") for line in head)
