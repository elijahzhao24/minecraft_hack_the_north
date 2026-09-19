"""CharacterHub: fan out the latest snapshot to Minecraft subscribers.

Each subscriber has an async queue of size one. When a new frame is published a
pending older frame is discarded so a slow subscriber only ever falls behind by
one, never accumulating a backlog of stale snapshots.
"""

from __future__ import annotations

import asyncio


class CharacterHub:
    """Latest-wins broadcast of serialized CHARACTER_FRAME bytes."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[bytes]] = set()

    def subscribe(self) -> asyncio.Queue[bytes]:
        q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[bytes]) -> None:
        self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def broadcast(self, encoded: bytes) -> None:
        """Deliver ``encoded`` to every subscriber, evicting stale pending frames."""
        for q in self._subscribers:
            _put_latest(q, encoded)


def _put_latest(q: asyncio.Queue[bytes], item: bytes) -> None:
    if q.full():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        pass
