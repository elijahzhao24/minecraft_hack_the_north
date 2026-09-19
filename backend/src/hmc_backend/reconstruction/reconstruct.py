"""Depth unprojection, per-view reconstruction, and multi-view merge.

Each view's masked depth is unprojected in the optical frame using intrinsics
scaled to the depth raster, transformed into stage meters with the calibrated
``T_stage_from_optical``, and colored by sampling the RGB image. The two views
are then merged, voxel-downsampled, and reduced to the renderer budget.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

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
    """Nearest-neighbour color sampling from the RGB image for depth pixels."""
    w_d, h_d = depth_wh
    w_r, h_r = rgb_wh
    u_r = np.clip((u_depth * (w_r / w_d)).astype(np.int64), 0, w_r - 1)
    v_r = np.clip((v_depth * (h_r / h_d)).astype(np.int64), 0, h_r - 1)
    return rgb[v_r, u_r]  # M x 3


def reconstruct_view(
    frame: CapturedFrame,
    detection: ViewDetection,
    calibration: CameraCalibration,
    crop: CropBounds,
    *,
    confidence_min: int,
    source_bit: int,
) -> ColoredPointCloud:
    """Reconstruct one view's masked person cloud in stage meters."""
    depth = frame.depth_m
    conf = frame.confidence
    h_d, w_d = depth.shape

    mask_depth = _resample_mask_to_depth(detection.person_mask, (h_d, w_d))

    valid = (
        mask_depth
        & np.isfinite(depth)
        & (depth > 0.0)
        & (depth >= crop.depth_min)
        & (depth <= crop.depth_max)
        & (conf >= confidence_min)
    )
    vs, us = np.nonzero(valid)
    if us.size == 0:
        empty_xyz = np.zeros((0, 3), np.float32)
        return ColoredPointCloud(empty_xyz, np.zeros((0, 4), np.uint8), np.zeros((0,), np.uint8))

    z = depth[vs, us].astype(np.float64)
    k_depth = scale_intrinsics(calibration.K_rgb, calibration.rgb_size, calibration.depth_size)
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
    stage = stage[in_stage]
    us_k, vs_k = us[in_stage], vs[in_stage]

    rgb = _sample_rgb(frame.rgb, us_k, vs_k, calibration.depth_size, calibration.rgb_size)
    rgba = np.empty((stage.shape[0], 4), np.uint8)
    rgba[:, :3] = rgb
    rgba[:, 3] = 255
    source_mask = np.full((stage.shape[0],), source_bit, np.uint8)

    return ColoredPointCloud(
        xyz_stage_m=np.ascontiguousarray(stage, np.float32),
        rgba=np.ascontiguousarray(rgba),
        source_mask=source_mask,
    )


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
