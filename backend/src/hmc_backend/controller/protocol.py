"""Strict decoder for the Hacker Badge's fixed-size BLE notification."""

from __future__ import annotations

import struct
from dataclasses import dataclass

PACKET_SIZE = 20
PACKET_MAGIC = b"HB"
PROTOCOL_VERSION = 1
STATE_MESSAGE = 1
SERVICE_UUID = "7b1e1000-6f7a-4d19-9c4b-5a2c8f1e2026"
STATE_UUID = "7b1e1001-6f7a-4d19-9c4b-5a2c8f1e2026"
_STATE = struct.Struct("<2sBBHIHhhhH")


class ControllerPacketError(ValueError):
    """A BLE notification violated the controller wire contract."""


@dataclass(frozen=True, slots=True)
class ControllerPacket:
    sequence: int
    badge_uptime_ms: int
    buttons: int
    accel_mg: tuple[int, int, int]
    punch_counter: int


def decode_state_packet(data: bytes | bytearray | memoryview) -> ControllerPacket:
    """Decode exactly one protocol-v1 state packet without accepting extensions."""
    raw = bytes(data)
    if len(raw) != PACKET_SIZE:
        raise ControllerPacketError(f"controller packet must be {PACKET_SIZE} bytes")
    magic, version, message_type, sequence, uptime, buttons, ax, ay, az, punches = (
        _STATE.unpack(raw)
    )
    if magic != PACKET_MAGIC:
        raise ControllerPacketError("invalid controller packet magic")
    if version != PROTOCOL_VERSION:
        raise ControllerPacketError(f"unsupported controller protocol version {version}")
    if message_type != STATE_MESSAGE:
        raise ControllerPacketError(f"unsupported controller message type {message_type}")
    if buttons & ~0x01FF:
        raise ControllerPacketError("controller packet contains unknown button bits")
    return ControllerPacket(sequence, uptime, buttons, (ax, ay, az), punches)
