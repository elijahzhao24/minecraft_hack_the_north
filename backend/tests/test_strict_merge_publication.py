"""Bad body alignment must never replace a published scan, even for snapshots."""

import json
from dataclasses import replace

import pytest

from hmc_backend.api.runtime import AppRuntime
from hmc_backend.fixtures.scene import build_capture_packets
from hmc_backend.settings import Settings
from tests.test_body_merge import rig


@pytest.mark.parametrize("mode", ["live", "snapshot"])
async def test_rejected_merge_holds_original_publication_and_recovers(mode, monkeypatch):
    calibration = rig()
    runtime = AppRuntime(Settings(simulation_mode=True, gravity_align=False), calibration)
    subscriber = runtime.hub.subscribe()
    process = runtime._processor.process
    reject = True

    def controlled_process(*args, **kwargs):
        frame = process(*args, **kwargs)
        assert "forced_opposing_body_merge" in frame.quality.warnings
        if reject:
            frame = replace(frame, quality=replace(frame.quality, warnings=(
                "body_merge_unavailable:torso_and_legs_not_visible_in_both_views",)))
        return frame

    monkeypatch.setattr(runtime._processor, "process", controlled_process)

    async def capture():
        result = None
        for packet in build_capture_packets(calibration).values():
            result = await runtime.handle_rgbd(packet, mode=mode)
        return result

    try:
        assert await capture() is None
        assert runtime.store.latest is None
        assert json.loads(subscriber.get_nowait())["code"] == "body_merge_unavailable"
        reject = False
        good = await capture()
        assert good is not None
        encoded = subscriber.get_nowait()
        assert isinstance(encoded, bytes)
        published_at = runtime._last_published_s
        reject = True
        assert await capture() is None
        assert runtime.store.latest is good
        assert runtime.store.latest_encoded == encoded
        assert runtime._last_published_s == published_at
        assert json.loads(subscriber.get_nowait())["code"] == "body_merge_unavailable"
        # Repeated bad frames neither flood clients nor renew the old scan.
        assert await capture() is None
        assert subscriber.empty()
        reject = False
        recovered = await capture()
        assert recovered.frame_id > good.frame_id
        assert runtime.store.latest is recovered
        assert isinstance(subscriber.get_nowait(), bytes)
    finally:
        await runtime.shutdown()
