"""Shared-marker anchoring and fixed-rig runtime invariants."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import pytest

from hmc_backend.api.runtime import AppRuntime
from hmc_backend.calibration.synthetic import build_synthetic_rig
from hmc_backend.fixtures.scene import build_capture_packets
from hmc_backend.protocol.envelope import EnvelopeError
from hmc_backend.settings import Settings


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        frame_tree_path=str(tmp_path / "frame-tree.json"),
        anchor_sample_count=8,
    )


async def _connected_runtime(tmp_path):
    runtime = AppRuntime(_settings(tmp_path), None)
    sent: dict[str, list[dict]] = {"front-phone": [], "side-phone": []}
    for device_id in sent:
        async def send(payload: str, *, target=device_id) -> None:
            sent[target].append(json.loads(payload))
        runtime.register_capture(device_id, send)
    return runtime, sent


@pytest.mark.asyncio
async def test_anchor_starts_without_existing_frame_tree(tmp_path):
    runtime, sent = await _connected_runtime(tmp_path)
    accepted, reason = await runtime.start_anchor(uuid4())
    assert (accepted, reason) == (True, "anchor_started")
    await asyncio.sleep(0.02)
    assert runtime.anchor_active
    front = next(m for m in sent["front-phone"] if m["type"] == "capture_request")
    side = next(m for m in sent["side-phone"] if m["type"] == "capture_request")
    assert front["capture_id"] == side["capture_id"]
    assert "not_before_phone_time_s" in front
    assert runtime._anchor_task is not None
    runtime._anchor_task.cancel()
    await runtime._anchor_task


@pytest.mark.asyncio
async def test_anchor_requires_both_devices(tmp_path):
    runtime = AppRuntime(_settings(tmp_path), None)
    runtime.register_capture("front-phone", lambda _: asyncio.sleep(0))
    assert await runtime.start_anchor(uuid4()) == (False, "devices_unavailable")


@pytest.mark.asyncio
async def test_runtime_accepts_legacy_rig_and_rejects_orientation_mismatch(tmp_path):
    rig = build_synthetic_rig(rgb_size=(160, 120), depth_size=(160, 120))
    cameras = {
        device_id: replace(camera, image_orientation="portrait")
        for device_id, camera in rig.cameras.items()
    }
    runtime = AppRuntime(_settings(tmp_path), replace(rig, cameras=cameras))
    packets = build_capture_packets(rig)
    with pytest.raises(EnvelopeError, match="orientation") as exc:
        await runtime.handle_rgbd(packets["front-phone"], connected_device_id="front-phone")
    assert exc.value.code == "calibration_mismatch"
