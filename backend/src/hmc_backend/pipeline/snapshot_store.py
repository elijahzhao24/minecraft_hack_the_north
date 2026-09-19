"""SnapshotStore: the single latest immutable CharacterFrame.

Publication is all-or-nothing. The store holds one reference that is replaced
atomically only after a frame is fully assembled, validated, and serialized. A
serialization or send failure never mutates the latest accepted object.
"""

from __future__ import annotations

import threading

from hmc_backend.contracts.internal import CharacterFrame


class SnapshotStore:
    """Holds the latest published CharacterFrame and its serialized bytes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: CharacterFrame | None = None
        self._encoded: bytes | None = None

    def publish(self, frame: CharacterFrame, encoded: bytes) -> None:
        """Atomically replace the latest snapshot (frame + its serialized form)."""
        with self._lock:
            self._frame = frame
            self._encoded = encoded

    @property
    def latest(self) -> CharacterFrame | None:
        with self._lock:
            return self._frame

    @property
    def latest_encoded(self) -> bytes | None:
        with self._lock:
            return self._encoded

    @property
    def latest_frame_id(self) -> int | None:
        with self._lock:
            return self._frame.frame_id if self._frame is not None else None
