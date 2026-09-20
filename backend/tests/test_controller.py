from __future__ import annotations

import json
import struct

import pytest
from fastapi.testclient import TestClient

from hmc_backend.api.app import app
from hmc_backend.controller.protocol import ControllerPacketError, decode_state_packet
from hmc_backend.controller.state import ControllerStore


def packet(
    *, sequence: int = 7, uptime: int = 1234, buttons: int = 0x41,
    accel: tuple[int, int, int] = (12, -20, 1001), punches: int = 3,
) -> bytes:
    return struct.pack("<2sBBHIHhhhH", b"HB", 1, 1, sequence, uptime, buttons, *accel, punches)


def test_state_packet_golden_decode() -> None:
    decoded = decode_state_packet(packet())
    assert decoded.sequence == 7
    assert decoded.badge_uptime_ms == 1234
    assert decoded.buttons == 0x41
    assert decoded.accel_mg == (12, -20, 1001)
    assert decoded.punch_counter == 3


@pytest.mark.parametrize(
    "bad",
    [b"", packet()[:-1], b"XX" + packet()[2:], packet()[:2] + b"\x02" + packet()[3:]],
)
def test_state_packet_rejects_invalid_framing(bad: bytes) -> None:
    with pytest.raises(ControllerPacketError):
        decode_state_packet(bad)


def test_store_latest_wins_rollover_and_stale_neutral() -> None:
    store = ControllerStore(stale_timeout_ms=250)
    assert store.accept_bytes(packet(sequence=0xFFFF, buttons=1, punches=8), now_s=1.0)
    assert store.accept_bytes(packet(sequence=0, buttons=2, punches=9), now_s=1.1)
    assert not store.accept_bytes(packet(sequence=0, buttons=4), now_s=1.2)
    assert store.state.buttons == 2
    assert not store.expire_if_stale(now_s=1.34)
    assert store.expire_if_stale(now_s=1.36)
    assert not store.state.connected
    assert store.state.buttons == 0
    assert store.state.punch_counter == 9


def test_store_counts_gap_and_malformed_packet() -> None:
    store = ControllerStore()
    assert store.accept_bytes(packet(sequence=10))
    assert store.accept_bytes(packet(sequence=13))
    assert not store.accept_bytes(b"bad")
    assert store.sequence_gaps == 2
    assert store.malformed_packets == 1


def test_controller_websocket_starts_with_current_state() -> None:
    with TestClient(app) as client:
        app.state.controller.accept_bytes(packet(sequence=9, buttons=0x41, punches=2))
        with client.websocket_connect("/ws/controller") as ws:
            ws.send_text(json.dumps({
                "type": "controller_hello",
                "protocol_version": 1,
                "client_id": "minecraft-test",
            }))
            state = json.loads(ws.receive_text())
            assert state == {
                "type": "controller_state",
                "protocol_version": 1,
                "connected": True,
                "sequence": 9,
                "badge_uptime_ms": 1234,
                "buttons": 0x41,
                "accel_mg": [12, -20, 1001],
                "punch_counter": 2,
            }


def test_health_includes_controller_diagnostics() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert "controller" in response.json()
    assert response.json()["controller"]["enabled"] is False
