"""Route tests for /health and the capture/character WebSockets."""

from __future__ import annotations

import json
from uuid import uuid4

from fastapi.testclient import TestClient

from hmc_backend.api.app import app
from hmc_backend.api.runtime import AppRuntime
from hmc_backend.calibration.synthetic import build_synthetic_rig
from hmc_backend.fixtures.scene import build_capture_packets
from hmc_backend.settings import Settings


def _install_runtime(*, with_calibration: bool = True) -> tuple[AppRuntime, object]:
    settings = Settings(simulation_mode=True)
    rig = build_synthetic_rig(rgb_size=(160, 120), depth_size=(160, 120)) if with_calibration else None
    runtime = AppRuntime(settings, rig)
    app.state.runtime = runtime
    return runtime, rig


def test_health_ready_when_calibrated():
    _install_runtime(with_calibration=True)
    with TestClient(app) as client:
        # Override the lifespan-installed runtime with our calibrated one.
        _install_runtime(with_calibration=True)
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["calibration"]["loaded"] is True
    assert set(body["devices"]) == {"front-phone", "side-phone"}


def test_health_503_without_calibration():
    with TestClient(app) as client:
        _install_runtime(with_calibration=False)
        resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json()["status"] in {"starting", "degraded"}


def test_capture_hello_rejects_unknown_device():
    with TestClient(app) as client:
        _install_runtime(with_calibration=True)
        with client.websocket_connect("/ws/capture") as sock:
            sock.send_text(
                json.dumps(
                    {
                        "type": "client_hello",
                        "protocol_version": 1,
                        "device_id": "rogue-phone",
                        "session_id": str(uuid4()),
                        "app_version": "0.1.0",
                        "platform": "ios",
                        "supports_scene_depth": True,
                        "image_orientation": "landscape_right",
                    }
                )
            )
            reply = json.loads(sock.receive_text())
            assert reply["type"] == "error"
            assert reply["code"] == "unauthorized_device"


def _client_hello(device_id: str) -> str:
    return json.dumps(
        {
            "type": "client_hello",
            "protocol_version": 1,
            "device_id": device_id,
            "session_id": str(uuid4()),
            "app_version": "0.1.0",
            "platform": "ios",
            "supports_scene_depth": True,
            "image_orientation": "landscape_right",
        }
    )


def test_capture_to_character_end_to_end():
    with TestClient(app) as client:
        runtime, rig = _install_runtime(with_calibration=True)
        capture_id = uuid4()
        packets = build_capture_packets(rig, capture_id=capture_id, seed=5, splat=2)

        # Subscribe a character client first.
        with client.websocket_connect("/ws/character") as char:
            char.send_text(
                json.dumps({"type": "character_hello", "protocol_version": 1, "client_id": "demo"})
            )
            server_hello = json.loads(char.receive_text())
            assert server_hello["type"] == "character_server_hello"

            # Two phones connect and each send their RGBD frame.
            with client.websocket_connect("/ws/capture") as front:
                front.send_text(_client_hello("front-phone"))
                assert json.loads(front.receive_text())["type"] == "server_hello"
                front.send_bytes(packets["front-phone"])

                with client.websocket_connect("/ws/capture") as side:
                    side.send_text(_client_hello("side-phone"))
                    assert json.loads(side.receive_text())["type"] == "server_hello"
                    side.send_bytes(packets["side-phone"])

                    # The pair completes and a CHARACTER_FRAME is pushed.
                    frame_bytes = char.receive_bytes()
                    assert frame_bytes[:4] == b"HMC1"

        assert runtime.store.latest_frame_id == 1


def test_character_request_capture_forwards_to_phones():
    with TestClient(app) as client:
        _install_runtime(with_calibration=True)
        with client.websocket_connect("/ws/capture") as front:
            front.send_text(_client_hello("front-phone"))
            front.receive_text()  # server_hello
            with client.websocket_connect("/ws/capture") as side:
                side.send_text(_client_hello("side-phone"))
                side.receive_text()

                with client.websocket_connect("/ws/character") as char:
                    char.send_text(
                        json.dumps({"type": "character_hello", "protocol_version": 1, "client_id": "demo"})
                    )
                    char.receive_text()  # character_server_hello
                    req_id = str(uuid4())
                    char.send_text(
                        json.dumps(
                            {
                                "type": "request_capture",
                                "protocol_version": 1,
                                "request_id": req_id,
                                "capture_id": str(uuid4()),
                                "mode": "snapshot",
                            }
                        )
                    )
                    def _receive_capture_request(sock):
                        while True:
                            msg = json.loads(sock.receive_text())
                            if msg.get("type") == "capture_request":
                                return msg

                    # Both phones receive a capture_request.
                    front_msg = _receive_capture_request(front)
                    side_msg = _receive_capture_request(side)
                    assert front_msg["type"] == "capture_request"
                    assert side_msg["type"] == "capture_request"
                    # And the character client is acked.
                    ack = json.loads(char.receive_text())
                    assert ack["type"] == "ack"
                    assert ack["accepted"] is True


def test_single_device_mode_publishes_from_one_phone():
    """With HMC_SINGLE_DEVICE the other device is virtual: one phone's frame pairs at once."""
    with TestClient(app) as client:
        settings = Settings(simulation_mode=True, single_device="front-phone")
        rig = build_synthetic_rig()
        runtime = AppRuntime(settings, rig)
        app.state.runtime = runtime
        packets = build_capture_packets(rig, capture_id=uuid4(), seed=5, splat=2)

        with client.websocket_connect("/ws/character") as char:
            char.send_text(json.dumps({"type": "character_hello", "protocol_version": 1, "client_id": "demo"}))
            assert json.loads(char.receive_text())["type"] == "character_server_hello"

            # The virtual device refuses a real phone.
            with client.websocket_connect("/ws/capture") as impostor:
                impostor.send_text(_client_hello("side-phone"))
                assert json.loads(impostor.receive_text())["code"] == "unauthorized_device"

            with client.websocket_connect("/ws/capture") as front:
                front.send_text(_client_hello("front-phone"))
                assert json.loads(front.receive_text())["type"] == "server_hello"
                front.send_bytes(packets["front-phone"])
                frame_bytes = char.receive_bytes()
                assert frame_bytes[:4] == b"HMC1"

        assert runtime.store.latest_frame_id == 1
        health, status = runtime.health()
        assert status == 200
        assert health.devices["side-phone"].connected and health.devices["side-phone"].clock_ready
