"""Small, tf2-inspired static transform tree for the fixed two-phone rig.

Transforms are named ``T_parent_from_child``: they map points expressed in the
child frame into the parent frame.  The public lookup follows tf2 semantics and
returns ``T_target_from_source``.  Timestamps are accepted at the boundary so
dynamic history can be added later without changing reconstruction callers.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


class TransformError(ValueError):
    """The requested transform is invalid or unavailable."""


def optical_frame(device_id: str) -> str:
    return f"{device_id}/optical"


def _validated_matrix(value: NDArray[np.floating] | list[float]) -> NDArray[np.float64]:
    matrix = np.asarray(value, dtype=np.float64)
    if matrix.size != 16:
        raise TransformError("transform must contain 16 values")
    matrix = matrix.reshape(4, 4)
    if not np.isfinite(matrix).all():
        raise TransformError("transform contains non-finite values")
    rotation = matrix[:3, :3]
    if not np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-4):
        raise TransformError("transform rotation is not orthonormal")
    if not np.isclose(np.linalg.det(rotation), 1.0, atol=1e-4):
        raise TransformError("transform rotation is not right-handed")
    if not np.allclose(matrix[3], [0.0, 0.0, 0.0, 1.0], atol=1e-8):
        raise TransformError("transform has an invalid homogeneous final row")
    return matrix.copy()


@dataclass(frozen=True, slots=True)
class StaticTransform:
    parent_frame: str
    child_frame: str
    matrix: NDArray[np.float64]
    authority: str = "unknown"

    def __post_init__(self) -> None:
        if not self.parent_frame or not self.child_frame:
            raise TransformError("frame names must be non-empty")
        if self.parent_frame == self.child_frame:
            raise TransformError("a frame cannot be its own parent")
        object.__setattr__(self, "matrix", _validated_matrix(self.matrix))


class TransformBuffer:
    """Immutable-after-construction static tree with cached composed lookups."""

    def __init__(self, transforms: list[StaticTransform] | tuple[StaticTransform, ...]) -> None:
        self._by_child: dict[str, StaticTransform] = {}
        self._frames: set[str] = set()
        self._cache: dict[tuple[str, str], NDArray[np.float64]] = {}
        for transform in transforms:
            if transform.child_frame in self._by_child:
                raise TransformError(f"frame {transform.child_frame!r} has multiple parents")
            self._by_child[transform.child_frame] = transform
            self._frames.update((transform.parent_frame, transform.child_frame))
        self._validate_acyclic()

    def _validate_acyclic(self) -> None:
        for frame in self._frames:
            seen: set[str] = set()
            current = frame
            while current in self._by_child:
                if current in seen:
                    raise TransformError(f"cycle detected at frame {current!r}")
                seen.add(current)
                current = self._by_child[current].parent_frame

    @property
    def frames(self) -> tuple[str, ...]:
        return tuple(sorted(self._frames))

    @property
    def transforms(self) -> tuple[StaticTransform, ...]:
        return tuple(self._by_child[name] for name in sorted(self._by_child))

    def can_transform(self, target_frame: str, source_frame: str, timestamp_s: float) -> bool:
        try:
            self.lookup_transform(target_frame, source_frame, timestamp_s)
        except TransformError:
            return False
        return True

    def lookup_transform(
        self, target_frame: str, source_frame: str, timestamp_s: float
    ) -> NDArray[np.float64]:
        if not np.isfinite(timestamp_s):
            raise TransformError("lookup timestamp must be finite")
        if target_frame == source_frame:
            if target_frame not in self._frames:
                raise TransformError(f"unknown frame {target_frame!r}")
            return np.eye(4, dtype=np.float64)
        key = (target_frame, source_frame)
        cached = self._cache.get(key)
        if cached is not None:
            return cached.copy()

        root_source, root_from_source = self._root_from(source_frame)
        root_target, root_from_target = self._root_from(target_frame)
        if root_source != root_target:
            raise TransformError(
                f"frames {source_frame!r} and {target_frame!r} are disconnected"
            )
        target_from_source = np.linalg.inv(root_from_target) @ root_from_source
        self._cache[key] = target_from_source
        return target_from_source.copy()

    def _root_from(self, frame: str) -> tuple[str, NDArray[np.float64]]:
        if frame not in self._frames:
            raise TransformError(f"unknown frame {frame!r}")
        root_from_frame = np.eye(4, dtype=np.float64)
        current = frame
        while current in self._by_child:
            edge = self._by_child[current]
            root_from_frame = edge.matrix @ root_from_frame
            current = edge.parent_frame
        return current, root_from_frame

