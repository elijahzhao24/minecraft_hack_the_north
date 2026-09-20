"""Live capture must expose readiness blockers and survive phone reconnects."""

import asyncio
import json

import pytest

from hmc_backend.api.runtime import AppRuntime
from hmc_backend.calibration.synthetic import build_synthetic_rig
from hmc_backend.settings import Settings


@pytest.mark.parametrize("blocker", ["not_ready", "devices_unavailable", "clocks_not_ready"])
async def test_live_reports_blocker_to_phone_and_minecraft(blocker):
    runtime = AppRuntime(
        Settings(simulation_mode=blocker != "clocks_not_ready"),
        None if blocker == "not_ready" else build_synthetic_rig(),
    )
    # Isolate clock readiness from the physical board prerequisite.
    if blocker == "clocks_not_ready":
        runtime.is_ready = lambda: True
    phone_messages = []

    async def send(payload):
        phone_messages.append(json.loads(payload))

    runtime.register_capture("front-phone", send)
    if blocker != "devices_unavailable":
        runtime.register_capture("side-phone", send)
    subscriber = runtime.hub.subscribe()
    try:
        runtime.start_live()
        notice = json.loads(await asyncio.wait_for(subscriber.get(), 1))
        assert notice["type"] == "error"
        assert notice["code"] == blocker
        assert notice["message"]
        assert any(message["code"] == blocker for message in phone_messages)
        assert runtime.live_active
    finally:
        await runtime.shutdown()


async def test_live_survives_send_failure_and_recovers_after_reconnect():
    runtime = AppRuntime(Settings(simulation_mode=True), build_synthetic_rig())
    captures = asyncio.Queue()

    async def broken_send(payload):
        raise ConnectionError("phone disconnected")

    async def send(payload):
        message = json.loads(payload)
        if message["type"] == "capture_request":
            captures.put_nowait(message)

    runtime.register_capture("front-phone", broken_send)
    runtime.register_capture("side-phone", send)
    subscriber = runtime.hub.subscribe()
    try:
        runtime.start_live(rate_hz=30)
        notice = json.loads(await asyncio.wait_for(subscriber.get(), 1))
        assert notice["code"] == "devices_unavailable"
        assert runtime.live_active
        runtime.register_capture("front-phone", send)
        first = await asyncio.wait_for(captures.get(), 1)
        second = await asyncio.wait_for(captures.get(), 1)
        assert first["capture_id"] == second["capture_id"]
        assert runtime.live_active
    finally:
        await runtime.shutdown()
