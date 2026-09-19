from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import pytest

from hmc_backend.api.runtime import AppRuntime
from hmc_backend.calibration.synthetic import build_synthetic_rig
from hmc_backend.fixtures.scene import build_capture_packets
from hmc_backend.settings import Settings


@pytest.mark.asyncio
async def test_phone_starts_coordinated_live_capture_and_live_frames_are_not_recorded(tmp_path):
    settings = Settings(
        recording_root=str(tmp_path),
        live_target_fps=20.0,
        live_capture_lead_ms=1.0,
        live_pair_timeout_ms=100.0,
    )
    rig = build_synthetic_rig(rgb_size=(160, 120), depth_size=(160, 120))
    runtime = AppRuntime(settings, rig)
    messages: dict[str, list[dict]] = {"front-phone": [], "side-phone": []}

    async def front_send(raw: str) -> None:
        messages["front-phone"].append(json.loads(raw))

    async def side_send(raw: str) -> None:
        messages["side-phone"].append(json.loads(raw))

    runtime.register_capture("front-phone", front_send)
    runtime.register_capture("side-phone", side_send)
    for (
        state
    ) in runtime._devices.values():  # exercise coordinator independently of wall-clock probes
        for index in range(settings.live_clock_samples):
            base = 100.0 + index
            state.clock.record_pong(str(index), base, base, base, base)

    await runtime.request_live(uuid4(), True)
    await asyncio.sleep(0.03)
    front_request = next(m for m in messages["front-phone"] if m["type"] == "capture_request")
    side_request = next(m for m in messages["side-phone"] if m["type"] == "capture_request")
    assert front_request["capture_id"] == side_request["capture_id"]
    assert front_request["mode"] == side_request["mode"] == "live"
    assert front_request["not_before_phone_time_s"] is not None

    # Use the coordinator-issued identity so mode classification is authoritative.
    capture_id = UUID(front_request["capture_id"])
    packets = build_capture_packets(rig, capture_id=capture_id)
    await runtime.handle_rgbd(packets["front-phone"], expected_device_id="front-phone")
    await runtime.handle_rgbd(packets["side-phone"], expected_device_id="side-phone")
    for _ in range(100):
        if runtime.store.latest_frame_id is not None:
            break
        await asyncio.sleep(0.01)

    assert runtime.store.latest_frame_id == 1
    assert not list(tmp_path.rglob("*.hmc"))
    assert runtime.health()[0].live.state == "running"
    await runtime.request_live(uuid4(), False)
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_live_request_pauses_until_both_devices_are_clock_ready(tmp_path):
    runtime = AppRuntime(Settings(recording_root=str(tmp_path)), build_synthetic_rig())
    sent: list[dict] = []

    async def send(raw: str) -> None:
        sent.append(json.loads(raw))

    runtime.register_capture("front-phone", send)
    await runtime.request_live(uuid4(), True)
    await asyncio.sleep(0.02)
    assert runtime.health()[0].live.state == "paused"
    assert runtime.health()[0].live.last_publish_age_ms is None
    await runtime.request_live(uuid4(), False)
    await runtime.shutdown()
