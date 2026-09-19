"""Array-invariant checks applied at pipeline module boundaries.

Bulk NumPy arrays crossing a boundary must be C-contiguous, of the expected
rank/dtype, mutually consistent in point count, finite where required, and
read-only before publication.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


class ArrayInvariantError(ValueError):
    """Raised when a bulk array violates an expected invariant."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ArrayInvariantError(message)


def check_cloud(xyz: NDArray, rgba: NDArray, source_mask: NDArray) -> None:
    """Validate a colored point cloud's arrays before it is packed/published."""
    require(xyz.ndim == 2 and xyz.shape[1] == 3, "xyz must be N x 3")
    require(xyz.dtype == np.float32, "xyz must be float32")
    require(rgba.ndim == 2 and rgba.shape[1] == 4, "rgba must be N x 4")
    require(rgba.dtype == np.uint8, "rgba must be uint8")
    require(source_mask.ndim == 1, "source_mask must be 1-D")
    require(source_mask.dtype == np.uint8, "source_mask must be uint8")

    n = xyz.shape[0]
    require(rgba.shape[0] == n, "rgba count must match xyz")
    require(source_mask.shape[0] == n, "source_mask count must match xyz")
    require(np.isfinite(xyz).all(), "xyz contains non-finite values")
    require(bool((rgba[:, 3] == 255).all()), "cloud alpha must all be 255")


def freeze(arr: NDArray) -> NDArray:
    """Return a C-contiguous, read-only view of ``arr`` for publication."""
    out = np.ascontiguousarray(arr)
    out.setflags(write=False)
    return out
