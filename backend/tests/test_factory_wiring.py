"""Settings-driven selection of the Workflow-3 vision and collider stages."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from pydantic import ValidationError

from hmc_backend.calibration.synthetic import build_synthetic_rig
from hmc_backend.colliders.fitter import AnatomicalCharacterFitter
from hmc_backend.contracts.internal import PairedFrames
from hmc_backend.fixtures import skeleton as skf
from hmc_backend.pipeline.factory import build_detector, build_fitter, build_processor
from hmc_backend.settings import Settings
from hmc_backend.vision.detector import MediaPipeViewDetector
from hmc_backend.vision.fake import FakeCharacterFitter, FakePersonMaskDetector
from hmc_backend.vision.models import ModelManifestError

MANIFEST = Path(__file__).resolve().parents[1] / "models" / "manifest.json"


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_defaults_select_fake_stages():
    s = _settings()
    rig = build_synthetic_rig()
    assert isinstance(build_detector(s), FakePersonMaskDetector)
    assert isinstance(build_fitter(s, rig), FakeCharacterFitter)
    proc = build_processor(s, rig)
    assert isinstance(proc.detector, FakePersonMaskDetector)
    assert isinstance(proc.fitter, FakeCharacterFitter)


def test_anatomical_fitter_selected_and_learns_flag_forwarded():
    rig = build_synthetic_rig()
    fitter = build_fitter(_settings(collider_backend="anatomical", learn_subject_dimensions=False), rig)
    assert isinstance(fitter, AnatomicalCharacterFitter)
    assert fitter._learn_subject is False


def test_unknown_backend_rejected_by_settings():
    with pytest.raises(ValidationError):
        _settings(vision_backend="opencv")
    with pytest.raises(ValidationError):
        _settings(collider_backend="magic")


def test_mediapipe_backend_fails_fast_without_pinned_files(tmp_path: Path):
    """No downloads at startup: a manifest without files is a hard error."""
    manifest = json.loads(MANIFEST.read_text())
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    s = _settings(vision_backend="mediapipe", model_manifest_path=str(tmp_path / "manifest.json"))
    with pytest.raises(ModelManifestError):
        build_detector(s)


def test_mediapipe_backend_rejects_hash_mismatch(tmp_path: Path):
    manifest = json.loads(MANIFEST.read_text())
    for entry in manifest["models"].values():
        entry["sha256"] = "0" * 64
        (tmp_path / entry["filename"]).write_bytes(b"not a model")
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    s = _settings(vision_backend="mediapipe", model_manifest_path=str(tmp_path / "manifest.json"))
    with pytest.raises(ModelManifestError, match="sha256 mismatch"):
        build_detector(s)


@pytest.mark.skipif(
    not all((MANIFEST.parent / f).exists() for f in ("pose_landmarker_full.task", "hand_landmarker.task")),
    reason="pinned MediaPipe model files not present; run scripts/pin_models.py",
)
def test_mediapipe_backend_constructs_without_importing_mediapipe(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "mediapipe", None)  # import would raise
    s = _settings(vision_backend="mediapipe", model_manifest_path=str(MANIFEST))
    det = build_detector(s)
    assert isinstance(det, MediaPipeViewDetector)
    status = det.model_status()
    assert status["pose"].startswith("mediapipe:") and status["hands"].startswith("mediapipe:")


@pytest.mark.skipif(
    not all((MANIFEST.parent / f).exists() for f in ("pose_landmarker_full.task", "hand_landmarker.task")),
    reason="pinned MediaPipe model files not present; run scripts/pin_models.py",
)
def test_mediapipe_detector_runs_on_rendered_frame():
    """Live check that the pinned mediapipe wheel + .task files actually infer.

    Guards against wheel regressions (mediapipe 1.0.x aborts the process on
    macOS inside TensorsToDetectionsCalculator) that unit tests with fakes
    cannot see. The synthetic box-person is not a detection-quality fixture;
    real accuracy is measured with consented RGB-D captures.
    """
    rig = build_synthetic_rig(rgb_size=(640, 480), depth_size=(320, 240))
    frame = next(iter(skf.render_frames(rig, skf.neutral_skeleton()).values()))
    det = build_detector(_settings(vision_backend="mediapipe", model_manifest_path=str(MANIFEST)))
    d = det.detect_view(frame)
    assert d.person_mask.shape == frame.depth_m.shape
    assert d.person_mask.dtype == np.bool_
    assert len(d.body) in (0, 33)


def test_debug_hook_writes_per_pair_artifacts(tmp_path: Path):
    rig = build_synthetic_rig()
    sk = skf.neutral_skeleton()
    frames = skf.render_frames(rig, sk)
    ids = list(frames)
    s = _settings(
        collider_backend="anatomical",
        debug_artifacts_dir=str(tmp_path / "dbg"),
        stage_min_y_m=-0.5,
        stage_max_y_m=2.5,
    )
    proc = build_processor(s, rig, detector=skf.SkeletonViewDetector(rig, sk))
    pair = PairedFrames(uuid4(), frames[ids[0]], frames[ids[1]], 0.0, 5.0, rig.calibration_id)
    frame = proc.process(pair)
    assert frame.frame_id >= 1

    out = tmp_path / "dbg" / str(pair.pair_id)
    assert (out / "report.json").exists()
    assert (out / "cloud_by_source.ply").exists()
    for d in ids:
        assert (out / f"overlay_{d}.png").exists()
    report = json.loads((out / "report.json").read_text())
    assert report["pair_id"] == str(pair.pair_id)
    assert report["calibration_id"] == str(rig.calibration_id)
    assert report["colliders"]


def test_debug_hook_failure_does_not_break_publish(tmp_path: Path, monkeypatch):
    rig = build_synthetic_rig()
    sk = skf.neutral_skeleton()
    frames = skf.render_frames(rig, sk)
    ids = list(frames)
    s = _settings(debug_artifacts_dir=str(tmp_path / "dbg"))
    proc = build_processor(s, rig, detector=skf.SkeletonViewDetector(rig, sk))

    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(proc, "_post_fit_hook", boom)
    pair = PairedFrames(uuid4(), frames[ids[0]], frames[ids[1]], 0.0, 5.0, rig.calibration_id)
    assert proc.process(pair).frame_id >= 1
