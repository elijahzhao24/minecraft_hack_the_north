"""Latest-wins controller state and WebSocket fanout."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace

from hmc_backend.controller.protocol import (
    ControllerPacket,
    ControllerPacketError,
    decode_state_packet,
)


@dataclass(frozen=True, slots=True)
class ControllerState:
    connected: bool = False
    sequence: int = 0
    badge_uptime_ms: int = 0
    buttons: int = 0
    accel_mg: tuple[int, int, int] = (0, 0, 0)
    punch_counter: int = 0

    def wire(self) -> dict[str, object]:
        return {
            "type": "controller_state",
            "protocol_version": 1,
            "connected": self.connected,
            "sequence": self.sequence,
            "badge_uptime_ms": self.badge_uptime_ms,
            "buttons": self.buttons,
            "accel_mg": list(self.accel_mg),
            "punch_counter": self.punch_counter,
        }


class ControllerStore:
    """Own controller state on the FastAPI event loop."""

    def __init__(self, *, stale_timeout_ms: int = 250) -> None:
        self.stale_timeout_s = stale_timeout_ms / 1000.0
        self.state = ControllerState()
        self.last_packet_s: float | None = None
        self.malformed_packets = 0
        self.sequence_gaps = 0
        self.reconnects = 0
        self._have_sequence = False
        self._subscribers: set[asyncio.Queue[ControllerState]] = set()

    def subscribe(self) -> asyncio.Queue[ControllerState]:
        queue: asyncio.Queue[ControllerState] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        self._put_latest(queue, self.state)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[ControllerState]) -> None:
        self._subscribers.discard(queue)

    def accept_bytes(self, data: bytes, *, now_s: float | None = None) -> bool:
        try:
            packet = decode_state_packet(data)
        except ControllerPacketError:
            self.malformed_packets += 1
            return False
        return self.accept(packet, now_s=now_s)

    def accept(self, packet: ControllerPacket, *, now_s: float | None = None) -> bool:
        now = time.monotonic() if now_s is None else now_s
        if self._have_sequence:
            delta = (packet.sequence - self.state.sequence) & 0xFFFF
            if delta == 0 or delta >= 0x8000:
                return False
            self.sequence_gaps += max(0, delta - 1)
        self._have_sequence = True
        self.last_packet_s = now
        was_connected = self.state.connected
        self.state = ControllerState(
            connected=True,
            sequence=packet.sequence,
            badge_uptime_ms=packet.badge_uptime_ms,
            buttons=packet.buttons,
            accel_mg=packet.accel_mg,
            punch_counter=packet.punch_counter,
        )
        if not was_connected:
            self.reconnects += 1
        self._broadcast()
        return True

    def disconnect(self) -> None:
        self._have_sequence = False
        self.last_packet_s = None
        if not self.state.connected and self.state.buttons == 0:
            return
        self.state = replace(self.state, connected=False, buttons=0)
        self._broadcast()

    def expire_if_stale(self, *, now_s: float | None = None) -> bool:
        now = time.monotonic() if now_s is None else now_s
        if (
            self.state.connected
            and self.last_packet_s is not None
            and now - self.last_packet_s > self.stale_timeout_s
        ):
            self.disconnect()
            return True
        return False

    def health(self, *, now_s: float | None = None) -> dict[str, object]:
        now = time.monotonic() if now_s is None else now_s
        age_ms = None if self.last_packet_s is None else max(0, round((now - self.last_packet_s) * 1000))
        return {
            "connected": self.state.connected,
            "last_packet_age_ms": age_ms,
            "malformed_packets": self.malformed_packets,
            "sequence_gaps": self.sequence_gaps,
            "reconnects": self.reconnects,
            "punch_counter": self.state.punch_counter,
        }

    def _broadcast(self) -> None:
        for queue in self._subscribers:
            self._put_latest(queue, self.state)

    @staticmethod
    def _put_latest(queue: asyncio.Queue[ControllerState], state: ControllerState) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(state)
        except asyncio.QueueFull:
            pass
