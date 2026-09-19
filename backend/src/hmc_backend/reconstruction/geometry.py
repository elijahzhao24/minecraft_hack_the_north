"""Small, tested geometry helpers for reconstruction.

Conventions (see ``docs/architecture.md`` §5):

* Optical frame: +X image-right, +Y image-down, +Z forward from the camera.
  A depth sample is the optical Z (camera-plane distance), not a ray distance.
* Stage frame: meters, +Y up, +X front-camera image-right, +Z toward the
  front camera. Transforms are ``T_destination_from_source``, 4x4 row-major,
  applied to column vectors: ``p_dst = T @ [x, y, z, 1]``.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def scale_intrinsics(k_rgb: NDArray[np.float64], rgb_wh: tuple[int, int], depth_wh: tuple[int, int]) -> NDArray[np.float64]:
    """Scale a 3x3 RGB intrinsic matrix to an uncropped depth raster.

    ``fx_d = fx_rgb * W_d/W_rgb`` (and likewise fy, cx, cy). Valid only when the
    depth raster is the same uncropped field of view as RGB, just resampled.
    """
    w_rgb, h_rgb = rgb_wh
    w_d, h_d = depth_wh
    sx = w_d / w_rgb
    sy = h_d / h_rgb
    k = k_rgb.astype(np.float64, copy=True)
    k[0, 0] *= sx  # fx
    k[1, 1] *= sy  # fy
    k[0, 2] *= sx  # cx
    k[1, 2] *= sy  # cy
    return k


def unproject(u: NDArray, v: NDArray, z: NDArray, k_depth: NDArray[np.float64]) -> NDArray[np.float32]:
    """Unproject pixel coordinates + depth into optical-frame points (N x 3).

    ``X = (u - cx) * Z / fx``, ``Y = (v - cy) * Z / fy``, ``Z = depth``.
    Uses pixel-center coordinates (integer pixel index = its center).
    """
    fx = k_depth[0, 0]
    fy = k_depth[1, 1]
    cx = k_depth[0, 2]
    cy = k_depth[1, 2]
    x = (u - cx) * z / fx
    y = (v - cy) * z / fy
    return np.stack([x, y, z], axis=-1).astype(np.float32)


def apply_transform(t_dst_from_src: NDArray[np.float64], points: NDArray) -> NDArray[np.float32]:
    """Apply a 4x4 row-major transform to N x 3 points, returning N x 3."""
    pts = np.asarray(points, dtype=np.float64)
    homo = np.concatenate([pts, np.ones((pts.shape[0], 1))], axis=1)  # N x 4
    out = homo @ t_dst_from_src.T  # (N x 4) @ (4 x 4)
    return out[:, :3].astype(np.float32)


def voxel_downsample(
    xyz: NDArray[np.float32], rgba: NDArray[np.uint8], source_mask: NDArray[np.uint8], voxel_size_m: float
) -> tuple[NDArray[np.float32], NDArray[np.uint8], NDArray[np.uint8]]:
    """Collapse points to one representative per voxel (first-in wins, deterministic).

    Colors and source bitsets of the surviving representative are kept; source
    bitsets from all points in a voxel are OR-combined so multi-camera coverage
    is preserved for diagnostics.
    """
    if xyz.shape[0] == 0 or voxel_size_m <= 0:
        return xyz, rgba, source_mask

    keys = np.floor(xyz / voxel_size_m).astype(np.int64)
    # Deterministic order: lexsort by voxel key.
    order = np.lexsort((keys[:, 2], keys[:, 1], keys[:, 0]))
    keys_sorted = keys[order]
    unique_mask = np.ones(len(order), dtype=bool)
    unique_mask[1:] = np.any(keys_sorted[1:] != keys_sorted[:-1], axis=1)

    rep_idx = order[unique_mask]
    # OR-combine source bitsets within each voxel group.
    group_ids = np.cumsum(unique_mask) - 1
    combined_source = np.zeros(int(group_ids[-1]) + 1, dtype=np.uint8)
    np.bitwise_or.at(combined_source, group_ids, source_mask[order])

    return xyz[rep_idx], rgba[rep_idx], combined_source


def deterministic_subsample(
    xyz: NDArray[np.float32],
    rgba: NDArray[np.uint8],
    source_mask: NDArray[np.uint8],
    max_points: int,
    seed: int,
) -> tuple[NDArray[np.float32], NDArray[np.uint8], NDArray[np.uint8]]:
    """Reduce to ``max_points`` via a seeded permutation (never by raster order)."""
    n = xyz.shape[0]
    if n <= max_points:
        return xyz, rgba, source_mask
    rng = np.random.default_rng(seed)
    pick = rng.permutation(n)[:max_points]
    pick.sort()  # keep stable ordering for reproducible output
    return xyz[pick], rgba[pick], source_mask[pick]
