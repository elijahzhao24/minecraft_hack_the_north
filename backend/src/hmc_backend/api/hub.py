"""CharacterHub: fan out the latest snapshot to Minecraft subscribers.

Each subscriber has an async queue of size one. When a new frame is published a
pending older frame is discarded so a slow subscriber only ever falls behind by
one, never accumulating a backlog of stale snapshots.
"""

from __future__ import annotations

import asyncio


class CharacterHub:
    """Latest-wins broadcast of serialized CHARACTER_FRAME bytes and control text."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[bytes]] = set()
        self._text_subscribers: set[asyncio.Queue[str]] = set()

    def subscribe(self) -> asyncio.Queue[bytes]:
        q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)
        self._subscribers.add(q)
        return q

    def subscribe_text(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=8)
        self._text_subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[bytes]) -> None:
        self._subscribers.discard(q)

    def unsubscribe_text(self, q: asyncio.Queue[str]) -> None:
        self._text_subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def broadcast(self, encoded: bytes) -> None:
        """Deliver ``encoded`` to every subscriber, evicting stale pending frames."""
        for q in self._subscribers:
            _put_latest(q, encoded)

    def broadcast_text(self, payload: str) -> None:
        """Deliver a JSON control message to every text subscriber."""
        for q in self._text_subscribers:
            _put_latest(q, payload)


def _put_latest[T](q: asyncio.Queue[T], item: T) -> None:
    if q.full():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        pass
