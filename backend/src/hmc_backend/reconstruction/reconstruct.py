"""Depth unprojection, per-view reconstruction, and multi-view merge.

Each view's masked depth is unprojected in the optical frame using intrinsics
scaled to the depth raster, transformed into stage meters with the calibrated
``T_stage_from_optical``, and colored by sampling the RGB image. The two views
are then merged, voxel-downsampled, and reduced to the renderer budget.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from hmc_backend.contracts.internal import (
    CameraCalibration,
    CapturedFrame,
    ColoredPointCloud,
    ViewDetection,
)
from hmc_backend.reconstruction.geometry import (
    apply_transform,
    deterministic_subsample,
    scale_intrinsics,
    unproject,
    voxel_downsample,
)


@dataclass(frozen=True, slots=True)
class CropBounds:
    """Hard spatial crop in stage meters plus a depth range."""

    min_x: float
    max_x: float
    min_y: float
    max_y: float
    min_z: float
    max_z: float
    depth_min: float
    depth_max: float
    max_range_m: float = 5.0


def _resample_mask_to_depth(mask_rgb: np.ndarray, depth_hw: tuple[int, int]) -> np.ndarray:
    """Nearest-neighbour resample a boolean RGB-resolution mask to depth size."""
    h_d, w_d = depth_hw
    h_r, w_r = mask_rgb.shape
    vv = (np.arange(h_d) * (h_r / h_d)).astype(np.int64)
    uu = (np.arange(w_d) * (w_r / w_d)).astype(np.int64)
    vv = np.clip(vv, 0, h_r - 1)
    uu = np.clip(uu, 0, w_r - 1)
    return mask_rgb[np.ix_(vv, uu)]


def _sample_rgb(rgb: np.ndarray, u_depth: np.ndarray, v_depth: np.ndarray, depth_wh, rgb_wh) -> np.ndarray:
    """Centered color sampling from the RGB image for depth pixels."""
    w_d, h_d = depth_wh
    w_r, h_r = rgb_wh
    u_r = np.clip(np.round((u_depth + 0.5) * (w_r / w_d) - 0.5).astype(np.int64), 0, w_r - 1)
    v_r = np.clip(np.round((v_depth + 0.5) * (h_r / h_d) - 0.5).astype(np.int64), 0, h_r - 1)
    return rgb[v_r, u_r]  # M x 3


def _plausible_intrinsics(k: NDArray[np.float64] | None, rgb_wh: tuple[int, int]) -> bool:
    if k is None or k.shape != (3, 3) or not np.all(np.isfinite(k)):
        return False
    w, h = rgb_wh
    # Focal lengths positive and principal point inside the raster.
    return bool(k[0, 0] > 0 and k[1, 1] > 0 and 0 < k[0, 2] < w and 0 < k[1, 2] < h)


def reconstruct_view(
    frame: CapturedFrame,
    detection: ViewDetection,
    calibration: CameraCalibration,
    crop: CropBounds,
    *,
    confidence_min: int,
    source_bit: int,
    use_frame_intrinsics: bool = False,
    diagnostics: dict | None = None,
    apply_stage_crop: bool = True,
) -> ColoredPointCloud:
    """Reconstruct one view's masked person cloud in stage meters.

    With ``use_frame_intrinsics`` the phone's own K (reported per frame for its
    RGB raster) is used for unprojection; invalid live intrinsics are rejected. A real lens rarely matches the synthetic 60 degree K, and the
    mismatch scales the whole body.
    """
    depth = frame.depth_m
    conf = frame.confidence
    h_d, w_d = depth.shape

    mask_depth = _resample_mask_to_depth(detection.person_mask, (h_d, w_d))

    # Range is measured in each camera's optical frame, never from stage origin.
    depth_wh = (w_d, h_d)
    rgb_wh = (frame.rgb.shape[1], frame.rgb.shape[0])
    k_src, k_wh = calibration.K_rgb, calibration.rgb_size
    if use_frame_intrinsics:
        if not _plausible_intrinsics(frame.K_rgb, rgb_wh):
            raise ValueError("invalid frame intrinsics")
        k_src, k_wh = frame.K_rgb, rgb_wh
    k_depth = scale_intrinsics(k_src, k_wh, depth_wh)
    vv, uu = np.indices(depth.shape)
    ray_sq = 1 + ((uu - k_depth[0, 2]) / k_depth[0, 0]) ** 2 + ((vv - k_depth[1, 2]) / k_depth[1, 1]) ** 2
    valid = np.isfinite(depth) & (depth > 0)
    counts = {"valid_depth": int(valid.sum())}
    valid &= (depth >= crop.depth_min) & (depth <= crop.depth_max)
    valid &= depth.astype(np.float64) ** 2 * ray_sq <= crop.max_range_m ** 2
    counts["after_range"] = int(valid.sum())
    valid &= conf >= confidence_min
    counts["after_confidence"] = int(valid.sum())
    valid &= mask_depth
    counts["after_mask"] = int(valid.sum())
    counts["after_stage"] = 0
    if diagnostics is not None:
        diagnostics.update(counts)
    vs, us = np.nonzero(valid)
    z = depth[vs, us].astype(np.float64)
    optical = unproject(us.astype(np.float64), vs.astype(np.float64), z, k_depth)
    stage = apply_transform(calibration.T_stage_from_optical, optical)

    # Hard stage crop.
    in_stage = (
        (stage[:, 0] >= crop.min_x)
        & (stage[:, 0] <= crop.max_x)
        & (stage[:, 1] >= crop.min_y)
        & (stage[:, 1] <= crop.max_y)
        & (stage[:, 2] >= crop.min_z)
        & (stage[:, 2] <= crop.max_z)
    )
    if not apply_stage_crop:
        in_stage = np.ones(len(stage), dtype=bool)
    stage = stage[in_stage]
    if diagnostics is not None:
        diagnostics["after_stage"] = int(stage.shape[0])
    us_k, vs_k = us[in_stage], vs[in_stage]

    rgb = _sample_rgb(frame.rgb, us_k, vs_k, depth_wh, rgb_wh)
    rgba = np.empty((stage.shape[0], 4), np.uint8)
    rgba[:, :3] = rgb
    rgba[:, 3] = 255
    source_mask = np.full((stage.shape[0],), source_bit, np.uint8)

    return ColoredPointCloud(
        xyz_stage_m=np.ascontiguousarray(stage, np.float32),
        rgba=np.ascontiguousarray(rgba),
        source_mask=source_mask,
    )


def crop_cloud(cloud: ColoredPointCloud, crop: CropBounds) -> ColoredPointCloud:
    """Apply stage bounds after body assembly; camera-space range was already gated."""
    p = cloud.xyz_stage_m
    keep = ((p[:, 0] >= crop.min_x) & (p[:, 0] <= crop.max_x)
            & (p[:, 1] >= crop.min_y) & (p[:, 1] <= crop.max_y)
            & (p[:, 2] >= crop.min_z) & (p[:, 2] <= crop.max_z))
    return ColoredPointCloud(np.ascontiguousarray(p[keep]), np.ascontiguousarray(cloud.rgba[keep]),
                             np.ascontiguousarray(cloud.source_mask[keep]))


def merge_clouds(
    clouds: list[ColoredPointCloud],
    *,
    voxel_size_m: float,
    max_points: int,
    seed: int,
) -> ColoredPointCloud:
    """Merge per-view clouds, voxel-downsample, and cap at the renderer budget."""
    non_empty = [c for c in clouds if c.count > 0]
    if not non_empty:
        return ColoredPointCloud(
            np.zeros((0, 3), np.float32), np.zeros((0, 4), np.uint8), np.zeros((0,), np.uint8)
        )

    xyz = np.concatenate([c.xyz_stage_m for c in non_empty], axis=0)
    rgba = np.concatenate([c.rgba for c in non_empty], axis=0)
    source = np.concatenate([c.source_mask for c in non_empty], axis=0)

    xyz, rgba, source = voxel_downsample(xyz, rgba, source, voxel_size_m)
    xyz, rgba, source = deterministic_subsample(xyz, rgba, source, max_points, seed)

    return ColoredPointCloud(
        xyz_stage_m=np.ascontiguousarray(xyz, np.float32),
        rgba=np.ascontiguousarray(rgba),
        source_mask=np.ascontiguousarray(source),
    )
