"""Calibration capture collection and fixed-rig runtime invariants."""

from __future__ import annotations

import json
from dataclasses import replace
from uuid import UUID

import pytest

from hmc_backend.api.runtime import AppRuntime
from hmc_backend.calibration.synthetic import build_synthetic_rig
from hmc_backend.fixtures.scene import build_capture_packets
from hmc_backend.protocol.envelope import EnvelopeError
from hmc_backend.settings import Settings


def _settings(tmp_path, **overrides) -> Settings:
    return Settings(
        _env_file=None,
        recording_root=str(tmp_path / "recordings"),
        registration_path=str(tmp_path / "registration.json"),
        **overrides,
    )


async def _connected_runtime(tmp_path, **overrides):
    runtime = AppRuntime(_settings(tmp_path, **overrides), None)
    sent: dict[str, list[dict]] = {"front-phone": [], "side-phone": []}

    for device_id in sent:
        async def send(payload: str, *, target=device_id) -> None:
            sent[target].append(json.loads(payload))

        runtime.register_capture(device_id, send)
    return runtime, sent


@pytest.mark.asyncio
async def test_collects_pair_without_existing_calibration(tmp_path):
    runtime, sent = await _connected_runtime(tmp_path)
    accepted, requested = await runtime.request_calibration_capture()
    assert accepted is True
    assert requested["state"] == "pending"
    capture_uuid = UUID(requested["capture_id"])
    capture_id = next(
        message["capture_id"]
        for message in sent["front-phone"]
        if message["type"] == "capture_request"
    )

    rig = build_synthetic_rig(rgb_size=(160, 120), depth_size=(160, 120))
    packets = build_capture_packets(rig, capture_id=capture_uuid)
    await runtime.handle_rgbd(packets["front-phone"], connected_device_id="front-phone")
    assert runtime.calibration_capture_status(capture_uuid)["state"] == "pending"
    await runtime.handle_rgbd(packets["side-phone"], connected_device_id="side-phone")

    status = runtime.calibration_capture_status(capture_uuid)
    assert status["state"] == "complete"
    capture_dir = tmp_path / "recordings" / capture_id
    assert (capture_dir / "front-phone.hmc").read_bytes() == packets["front-phone"]
    assert (capture_dir / "side-phone.hmc").read_bytes() == packets["side-phone"]
    assert (capture_dir / "manifest.json").exists()


@pytest.mark.asyncio
async def test_duplicate_device_packet_fails_capture(tmp_path):
    runtime, _ = await _connected_runtime(tmp_path)
    _, requested = await runtime.request_calibration_capture()
    rig = build_synthetic_rig(rgb_size=(160, 120), depth_size=(160, 120))
    capture_uuid = UUID(requested["capture_id"])
    packets = build_capture_packets(rig, capture_id=capture_uuid)
    await runtime.handle_rgbd(packets["front-phone"], connected_device_id="front-phone")
    await runtime.handle_rgbd(packets["front-phone"], connected_device_id="front-phone")
    status = runtime.calibration_capture_status(capture_uuid)
    assert status["state"] == "failed"
    assert status["failure_code"] == "duplicate_device_packet"


@pytest.mark.asyncio
async def test_calibration_capture_times_out(tmp_path):
    runtime, _ = await _connected_runtime(tmp_path)
    _, requested = await runtime.request_calibration_capture()
    capture_id = UUID(requested["capture_id"])
    runtime._calibration_captures[capture_id].deadline_monotonic_s = 0.0
    status = runtime.calibration_capture_status(capture_id)
    assert status["state"] == "failed"
    assert status["failure_code"] == "capture_timeout"


@pytest.mark.asyncio
async def test_runtime_rejects_frame_orientation_mismatch(tmp_path):
    rig = build_synthetic_rig(rgb_size=(160, 120), depth_size=(160, 120))
    cameras = {
        device_id: replace(camera, image_orientation="portrait")
        for device_id, camera in rig.cameras.items()
    }
    portrait_rig = replace(rig, cameras=cameras)
    runtime = AppRuntime(_settings(tmp_path), portrait_rig)
    packets = build_capture_packets(rig)
    with pytest.raises(EnvelopeError, match="orientation") as exc:
        await runtime.handle_rgbd(packets["front-phone"], connected_device_id="front-phone")
    assert exc.value.code == "calibration_mismatch"


def test_stale_registration_is_not_loaded(tmp_path):
    rig = build_synthetic_rig()
    path = tmp_path / "registration.json"
    path.write_text(json.dumps({
        "calibration_id": "00000000-0000-0000-0000-000000000000",
        "corrections": {"side-phone": [1, 0, 0, 1, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]},
    }))
    runtime = AppRuntime(
        _settings(tmp_path, enable_person_registration=True),
        rig,
    )
    assert runtime.registration_status()["corrections"] == {}
