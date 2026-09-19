"""Route tests for /health and the capture/character WebSockets."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

import hmc_backend.api.app as app_module
from hmc_backend.api.app import app
from hmc_backend.api.runtime import AppRuntime
from hmc_backend.calibration.synthetic import build_synthetic_rig
from hmc_backend.fixtures.scene import build_capture_packets
from hmc_backend.settings import Settings


def _install_runtime(*, with_calibration: bool = True) -> tuple[AppRuntime, object]:
    settings = Settings()
    rig = (
        build_synthetic_rig(rgb_size=(160, 120), depth_size=(160, 120))
        if with_calibration
        else None
    )
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


def test_missing_calibration_logs_readiness_reason(tmp_path, monkeypatch):
    events: list[tuple[str, dict]] = []

    def capture_log(level: str, message: str, **attributes) -> None:
        events.append((message, attributes))

    monkeypatch.setattr(app_module, "log_event", capture_log)
    runtime = app_module.build_runtime(
        Settings(calibration_path=str(tmp_path / "missing-calibration.json"))
    )
    assert runtime.readiness_issues() == (
        "provisional_calibration_pending",
        "processor_unavailable",
    )
    failure = next(attributes for name, attributes in events if name == "calibration_load_failed")
    assert "not found" in failure["reason"]


def test_handshake_logs_accepted_but_backend_not_ready(monkeypatch):
    events: list[tuple[str, dict]] = []

    def capture_log(level: str, message: str, **attributes) -> None:
        events.append((message, attributes))

    monkeypatch.setattr(app_module, "log_event", capture_log)
    with TestClient(app) as client:
        runtime = AppRuntime(Settings(), None)
        app.state.runtime = runtime
        events.clear()
        with client.websocket_connect("/ws/capture") as sock:
            sock.send_text(_client_hello("front-phone"))
            assert json.loads(sock.receive_text())["type"] == "server_hello"

    accepted = next(
        attributes for name, attributes in events if name == "capture_handshake_accepted"
    )
    assert accepted["backend_ready"] is False
    assert accepted["readiness_issues"] == "provisional_calibration_pending,processor_unavailable"


def test_valid_packet_is_recorded_without_calibration(tmp_path):
    rig = build_synthetic_rig(rgb_size=(160, 120), depth_size=(160, 120))
    capture_id = uuid4()
    packet = build_capture_packets(rig, capture_id=capture_id)["front-phone"]
    settings = Settings(recording_root=str(tmp_path))
    runtime = AppRuntime(settings, None)

    with TestClient(app) as client:
        app.state.runtime = runtime
        with client.websocket_connect("/ws/capture") as sock:
            sock.send_text(_client_hello("front-phone"))
            assert json.loads(sock.receive_text())["type"] == "server_hello"
            sock.send_bytes(packet)

    recorded = Path(tmp_path, str(capture_id), "front-phone.hmc")
    assert recorded.read_bytes() == packet


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


def _receive_type(sock, wanted: str) -> dict:
    while True:
        message = json.loads(sock.receive_text())
        if message["type"] == wanted:
            return message


def test_capture_to_character_end_to_end(tmp_path):
    with TestClient(app) as client:
        settings = Settings(recording_root=str(tmp_path))
        rig = build_synthetic_rig(rgb_size=(160, 120), depth_size=(160, 120))
        runtime = AppRuntime(settings, rig)
        app.state.runtime = runtime
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
                        json.dumps(
                            {"type": "character_hello", "protocol_version": 1, "client_id": "demo"}
                        )
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
                    # Both phones receive a capture_request.
                    front_msg = _receive_type(front, "capture_request")
                    side_msg = _receive_type(side, "capture_request")
                    assert front_msg["type"] == "capture_request"
                    assert side_msg["type"] == "capture_request"
                    # And the character client is acked.
                    ack = json.loads(char.receive_text())
                    assert ack["type"] == "ack"
                    assert ack["accepted"] is True
