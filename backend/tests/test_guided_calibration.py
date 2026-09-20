from __future__ import annotations

import json
from dataclasses import replace
from uuid import uuid4

import numpy as np
import pytest

from hmc_backend.api.runtime import AppRuntime
from hmc_backend.calibration.charuco import BoardSpec, CharucoError, build_board, stage_from_board
from hmc_backend.calibration.model import load_rig_calibration, save_rig_calibration
from hmc_backend.calibration.session import BoardCalibrationSession, RigFreshness
from hmc_backend.calibration.synthetic import build_synthetic_rig, look_at_optical
from hmc_backend.contracts.character_codec import encode_character_frame
from hmc_backend.contracts.character_decode import decode_character_frame
from hmc_backend.fixtures.scene import encode_rgbd_packet
from hmc_backend.pipeline.factory import build_processor
from hmc_backend.reconstruction.reconstruct import CropBounds, reconstruct_view
from hmc_backend.reconstruction.registration import register_yaw_translation
from hmc_backend.settings import Settings
from tests.test_charuco import _intrinsics, _render_board_view
from tests.test_reconstruction import _synthetic_view
from tests.test_rig_registration import _pair


# ARKit camera axes are x-right, y-up, z-back; the optical raster is x-right,
# y-down, z-forward. A real phone reports its pose in the ARKit convention.
_OPTICAL_FROM_ARKIT = np.diag([1., -1., -1.])


def _optical_pose_from_arkit(pose):
    """Convert an ARKit-convention camera pose into an optical-convention one."""
    out = pose.copy()
    out[:3, :3] = pose[:3, :3] @ _OPTICAL_FROM_ARKIT
    return out


@pytest.fixture(scope="module")
def board_frames():
    board, detector = build_board(BoardSpec())
    k = _intrinsics(1600, 1200)
    frames = []
    base, _, _ = _synthetic_view()
    for dev, eye in (("front-phone", [.14, .8, 1.0]), ("side-phone", [.14, .8, -.6])):
        pose = look_at_optical(np.array(eye), np.array([.14, 0., .2]), np.array([0., 1., 0.]))
        cam_board = np.linalg.inv(pose) @ stage_from_board()
        gray = _render_board_view(board, detector, k, cam_board[:3, :3], cam_board[:3, 3])
        rgb = np.repeat(gray[:, :, None], 3, axis=2)
        frames.append(replace(base, device_id=dev, session_id=uuid4(), rgb=rgb, K_rgb=k,
                              arkit_pose=_optical_pose_from_arkit(pose)))
    return frames


def collect_job(frames):
    job = BoardCalibrationSession(("front-phone", "side-phone"))
    for i in range(12):
        for frame in frames:
            job.offer(replace(frame, sequence=i))
    return job


def test_opposing_board_views_solve_in_shared_frame(board_frames):
    job = collect_job(board_frames)
    assert job.state == "validating", job.status()
    rig = job.solve()
    for frame in board_frames:
        expected = _optical_pose_from_arkit(frame.arkit_pose)
        np.testing.assert_allclose(rig.camera(frame.device_id).T_stage_from_optical,
                                   expected, atol=.008)
        report = rig.validation[frame.device_id]
        assert report["held_out_frames"] == 3
        # The phone's own gravity measurement agrees with the solved pose.
        assert report["gravity_error_deg"] < 2
        assert report["camera_position_stage_m"][1] > 0
    assert rig.camera("front-phone").T_stage_from_optical[2, 3] > .9
    assert rig.camera("side-phone").T_stage_from_optical[2, 3] < -.5


def test_solve_rejects_pose_contradicting_gravity(board_frames):
    # A pose solve that silently picked the wrong planar candidate reads the
    # camera's measured "up" as pointing sideways; the aggregate check must
    # refuse to install it rather than emit a second displaced person.
    roll90 = np.eye(4)
    roll90[:3, :3] = np.array([[1., 0., 0.], [0., 0., -1.], [0., 1., 0.]])
    frames = [replace(f, arkit_pose=f.arkit_pose @ roll90) for f in board_frames]
    job = collect_job(frames)
    with pytest.raises(CharucoError, match="gravity"):
        job.solve()


def test_misaligned_views_warn_and_report_gap():
    rig = build_synthetic_rig(orientation="landscape_right")
    pair = _pair(rig)  # rendered by the true rig
    # The backend believes a side pose shifted a metre: the person's back
    # shell renders displaced from the front shell — the classic "two ghosts".
    cam = rig.camera("side-phone")
    shifted = cam.T_stage_from_optical.copy()
    shifted[0, 3] += 1.0
    wrong = replace(rig, cameras={**rig.cameras, "side-phone": replace(cam, T_stage_from_optical=shifted)})
    processor = build_processor(Settings(gravity_align=False), wrong)
    result = processor.process(pair)
    assert result.cloud.count > 0
    assert any(w.startswith("views_misaligned") for w in result.quality.warnings)
    assert processor.view_diagnostics["front-phone"]["cross_view_nn_m"] > 0.4


def test_aligned_views_do_not_warn():
    rig = build_synthetic_rig(orientation="landscape_right")
    processor = build_processor(Settings(gravity_align=False), rig)
    result = processor.process(_pair(rig))
    assert result.cloud.count > 0
    assert not any(w.startswith("views_misaligned") for w in result.quality.warnings)


def test_missing_board_and_tracking_are_actionable(board_frames):
    job = BoardCalibrationSession(("front-phone", "side-phone"))
    job.offer(replace(board_frames[0], rgb=np.zeros_like(board_frames[0].rgb)))
    job.offer(replace(board_frames[1], tracking_state="limited_initializing"))
    assert job.rejections["front-phone"]["no_board_detected"] == 1
    assert job.rejections["side-phone"]["tracking_not_normal"] == 1
    with pytest.raises(CharucoError):
        job.solve()


def test_moved_board_rejected_even_with_good_reprojection(board_frames):
    job = collect_job(board_frames)
    obs = job.observations["side-phone"][-1]
    job.observations["side-phone"][-1] = replace(obs, t_camera_from_board=obs.t_camera_from_board + [.15, 0, 0])
    with pytest.raises(CharucoError, match="moved|disagree"):
        job.solve()


def test_phone_motion_and_session_changes_invalidate(board_frames):
    rig = collect_job(board_frames).solve()
    fresh = RigFreshness(rig)
    frame = board_frames[0]
    moved_pose = frame.arkit_pose.copy()
    moved_pose[0, 3] += .03
    moved = replace(frame, arkit_pose=moved_pose, sequence=frame.sequence + 1)
    assert fresh.observe(moved) is None
    assert fresh.observe(moved) is None
    assert "camera_moved" in fresh.observe(moved)
    assert "camera_moved" in fresh.observe(frame)  # latched until recalibrated
    assert "session_id_changed" in RigFreshness(rig).observe(replace(frame, session_id=uuid4()))
    job = BoardCalibrationSession(("front-phone", "side-phone"))
    job.offer(frame)
    job.offer(moved)
    assert job.state == "failed"


def test_failed_atomic_write_preserves_previous_rig(tmp_path, monkeypatch):
    path = tmp_path / "calibration.json"
    rig = build_synthetic_rig()
    save_rig_calibration(rig, path)
    before = path.read_bytes()
    def fail(*args):
        raise OSError("disk failure")
    monkeypatch.setattr("hmc_backend.calibration.model.os.replace", fail)
    with pytest.raises(OSError):
        save_rig_calibration(build_synthetic_rig(), path)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("depth,expected", [(4.99, True), (5., True), (5.01, False)])
def test_true_range_center_boundaries(depth, expected):
    frame, detection, calib = _synthetic_view()
    values = np.zeros_like(frame.depth_m)
    values[6, 8] = depth
    frame = replace(frame, depth_m=values)
    crop = CropBounds(-10, 10, -10, 10, -10, 10, .2, 10, 5)
    cloud = reconstruct_view(frame, detection, calib, crop, confidence_min=1, source_bit=1)
    assert cloud.count == int(expected)


def test_off_axis_under_five_depth_over_five_range():
    frame, detection, calib = _synthetic_view()
    k = frame.K_rgb.copy()
    k[0, 0] = k[1, 1] = 8
    depth = np.zeros_like(frame.depth_m)
    depth[6, 0] = 4  # x=-4, z=4: 5.66 m from phone
    frame = replace(frame, K_rgb=k, depth_m=depth)
    diag = {}
    cloud = reconstruct_view(frame, detection, calib, CropBounds(-10, 10, -10, 10, -10, 10, .2, 10),
                             confidence_min=1, source_bit=1, use_frame_intrinsics=True, diagnostics=diag)
    assert cloud.count == 0
    assert diag["valid_depth"] == 1 and diag["after_range"] == 0


def test_missing_view_warns_and_sources_roundtrip():
    rig = build_synthetic_rig(orientation="landscape_right")
    pair = _pair(rig)
    cam = rig.camera("side-phone")
    pose = cam.T_stage_from_optical.copy()
    pose[0, 3] += 10
    wrong = replace(rig, cameras={**rig.cameras, "side-phone": replace(cam, T_stage_from_optical=pose)})
    processor = build_processor(Settings(gravity_align=False), wrong)
    result = processor.process(pair)
    assert result.cloud.count > 0
    assert "missing_view:side-phone:after_stage" in result.quality.warnings
    decoded = decode_character_frame(encode_character_frame(result))
    np.testing.assert_array_equal(decoded.source_mask, result.cloud.source_mask)
    assert np.all(decoded.source_mask == 1)


def test_no_matching_points_does_not_return_a_transform():
    src = np.column_stack([np.linspace(-3, 3, 100), np.zeros(100), np.zeros(100)])
    tgt = np.column_stack([np.zeros(100), np.linspace(-3, 3, 100), np.ones(100)])
    with pytest.raises(ValueError):
        register_yaw_translation(src, tgt, max_pair_distance_m=.0001)


def test_board_rig_ignores_person_corrections_and_gravity(board_frames):
    rig = collect_job(board_frames).solve()
    processor = build_processor(Settings(), rig)
    correction = np.eye(4)
    correction[2, 3] = .3
    processor.set_corrections({"side-phone": correction})
    frame = board_frames[1]
    np.testing.assert_allclose(processor._effective_calibration(frame.device_id, frame).T_stage_from_optical,
                               rig.camera(frame.device_id).T_stage_from_optical)


@pytest.mark.asyncio
async def test_timeout_keeps_previous_rig_and_resumes_live(tmp_path):
    rig = build_synthetic_rig()
    runtime = AppRuntime(Settings(calibration_timeout_s=.02, calibration_capture_hz=10,
                                  calibration_path=str(tmp_path / "rig.json")), rig)
    runtime.start_live()
    assert runtime.request_registration()
    assert not runtime.request_registration()
    await runtime._board_task
    assert runtime.registration_status()["state"] == "failed"
    assert runtime._calibration is rig
    assert runtime.live_active
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_bootstrap_calibration_via_real_rgbd_packets(board_frames, tmp_path):
    settings = Settings(calibration_capture_hz=10, calibration_path=str(tmp_path / "rig.json"),
                        recording_root=str(tmp_path / "recordings"))
    runtime = AppRuntime(settings, None)
    for dev in settings.expected_device_ids:
        runtime.on_clock_pong(dev, 0., 0., 0., .002)
    assert runtime.request_registration()
    calibration = build_synthetic_rig(orientation="landscape_right", rgb_size=(1600, 1200), depth_size=(16, 12))
    for i in range(12):
        capture_id = uuid4()
        for f in board_frames:
            raw = encode_rgbd_packet(device_id=f.device_id, session_id=f.session_id, capture_id=capture_id,
                sequence=i, capture_timestamp_s=float(i),
                calib=replace(calibration.camera(f.device_id), K_rgb=f.K_rgb), rgb=f.rgb,
                depth=f.depth_m, confidence=f.confidence)
            # Fixture packet pose is identity; consistently valid for freshness.
            runtime._capture_modes[capture_id] = "calibration"
            await runtime.handle_rgbd(raw)
    await runtime._board_task
    assert runtime.registration_status()["state"] == "ready", runtime.registration_status()
    assert runtime.is_ready()
    assert load_rig_calibration(tmp_path / "rig.json").calibration_id == runtime._calibration.calibration_id
    assert len(list((tmp_path / "recordings").iterdir())) == 12
    await runtime.shutdown()


def test_legacy_person_corrections_never_loaded(tmp_path):
    rig = build_synthetic_rig()
    path = tmp_path / "registration.json"
    path.write_text(json.dumps({"calibration_id": str(uuid4()), "corrections": {"side-phone": np.eye(4).reshape(-1).tolist()}}))
    runtime = AppRuntime(Settings(registration_path=str(path)), rig)
    assert runtime._processor.corrections == {}


@pytest.mark.parametrize("settings", [{"max_range_m": 5.01}, {"max_range_m": float("nan")},
                                      {"depth_min_m": 6}, {"depth_max_m": float("inf")}])
def test_invalid_range_configuration_rejected(settings):
    with pytest.raises(ValueError):
        Settings(**settings)


def test_board_mode_preserves_opposite_surface_thickness():
    from hmc_backend.contracts.internal import PairedFrames
    base, _, cam = _synthetic_view()
    rig = build_synthetic_rig(orientation="landscape_right")
    front_t = np.diag([1., -1., -1., 1.])
    front_t[:3, 3] = [0, 1, 2.15]
    back_t = np.diag([-1., -1., 1., 1.])
    back_t[:3, 3] = [0, 1, -2.15]
    rig = replace(rig, board=BoardSpec().to_json(), cameras={
        "front-phone": replace(cam, device_id="front-phone", calibration_id=rig.calibration_id, T_stage_from_optical=front_t),
        "side-phone": replace(cam, device_id="side-phone", calibration_id=rig.calibration_id, T_stage_from_optical=back_t),
    })
    pair = PairedFrames(uuid4(), base, replace(base, device_id="side-phone"), 0., 0., rig.calibration_id)
    processor = build_processor(Settings(), rig)
    processor.request_registration()  # legacy API must never run body ICP for a board rig
    result = processor.process(pair)
    assert result.cloud.count > 0
    assert np.ptp(result.cloud.xyz_stage_m[:, 2]) == pytest.approx(.30, abs=1e-5)
    assert set(result.cloud.source_mask) == {1, 2}
    assert processor.corrections == {}


@pytest.mark.asyncio
async def test_recalibration_preserves_stream_counter_and_clears_pair_queue(board_frames, tmp_path):
    rig = build_synthetic_rig(orientation="landscape_right")
    settings = Settings(simulation_mode=True, calibration_path=str(tmp_path / "rig.json"),
                        recording_root=str(tmp_path / "recordings"), calibration_capture_hz=10)
    runtime = AppRuntime(settings, rig)
    first = runtime._processor.process(_pair(rig))
    job = collect_job(board_frames)
    solved = job.solve()
    # Exercise the activation path with a completed solver and one final pair.
    assert runtime.request_registration()
    active_job = runtime._board_session
    active_job.observations = job.observations
    active_job.frames = job.frames
    active_job.baselines = job.baselines
    active_job.solve = lambda: solved
    active_job.offer = lambda frame: setattr(active_job, "state", "validating")
    capture = uuid4()
    # Use valid packets even though this test bypasses detection (tested above).
    for f in board_frames:
        raw = encode_rgbd_packet(device_id=f.device_id, session_id=f.session_id, capture_id=capture,
                sequence=1, capture_timestamp_s=1., calib=rig.camera(f.device_id),
                rgb=np.zeros((240, 320, 3), np.uint8), depth=np.ones((240, 320), np.float32),
                confidence=np.full((240, 320), 2, np.uint8))
        runtime._capture_modes[capture] = "calibration"
        await runtime.handle_rgbd(raw)
    await runtime._board_task
    assert runtime._processor.assembler.next_frame_id == first.frame_id + 1
    assert all(runtime._pairer.pending_depth(d) == 0 for d in settings.expected_device_ids)
    assert runtime._calibration.calibration_id == solved.calibration_id
    await runtime.shutdown()


def test_physical_runtime_rejects_nominal_synthetic_geometry():
    runtime = AppRuntime(Settings(), build_synthetic_rig())
    assert not runtime.is_ready()
    assert runtime.registration_status()["error"] == "calibration_required"
