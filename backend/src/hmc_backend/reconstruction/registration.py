"""Bring two hand-placed phones into one stage frame without a printed board.

Two corrections stack on top of the rig calibration's nominal camera poses:

1. **Gravity alignment.** ARKit runs with gravity world alignment, so every
   frame's ``T_arkit_world_from_camera`` encodes the phone's true pitch and
   roll. A phone propped a few degrees off upright would otherwise tilt the
   whole person. The nominal pose keeps its yaw and position; only "which way
   is up" is taken from the phone.

2. **Person-as-target registration.** Both cameras see the same body at the
   same instant. A trimmed, yaw-plus-translation ICP aligns one view's cloud
   onto the reference view's cloud and the result is stored as a stage->stage
   correction for that camera. Restricting the rotation to yaw keeps the
   gravity alignment intact and makes the fit well conditioned even though the
   two views overlap only partially.

Everything here is NumPy/SciPy on a few thousand points and runs in
milliseconds, so it is safe on the live path.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from numpy.typing import NDArray
from scipy.spatial import cKDTree

from hmc_backend.contracts.internal import CameraCalibration, CapturedFrame

# ARKit camera axes are x-right, y-up, z-backward; the optical (OpenCV) raster
# is x-right, y-down, z-forward. Both are attached to the landscape sensor.
_OPTICAL_FROM_ARKIT_CAMERA = np.diag([1.0, -1.0, -1.0])


def _unit(v: NDArray[np.float64]) -> NDArray[np.float64]:
    n = float(np.linalg.norm(v))
    if n < 1e-9:
        raise ValueError("degenerate vector")
    return v / n


def has_usable_pose(frame: CapturedFrame) -> bool:
    """True when the frame carries a real (non-identity, finite) ARKit pose."""
    return arkit_pose_is_usable(frame.arkit_pose)


def arkit_pose_is_usable(pose: NDArray[np.float64] | None) -> bool:
    """True for a real (non-identity, finite, rigid) ARKit camera pose."""
    if pose is None or pose.shape != (4, 4) or not np.all(np.isfinite(pose)):
        return False
    r = pose[:3, :3]
    if not np.allclose(r @ r.T, np.eye(3), atol=1e-3):
        return False
    return not np.allclose(r, np.eye(3), atol=1e-6)


def optical_up_from_arkit_pose(arkit_pose: NDArray[np.float64] | None) -> NDArray[np.float64] | None:
    """Gravity "up" expressed in the optical frame, or ``None`` if unusable.

    ARKit runs with gravity world alignment, so ``T_arkit_world_from_camera``
    tells us which optical-frame direction is physically up — an independent
    measurement of the camera's pitch/roll that no board detection can give.
    """
    if not arkit_pose_is_usable(arkit_pose):
        return None
    r_world_from_cam = np.asarray(arkit_pose, dtype=np.float64)[:3, :3]
    up_cam = r_world_from_cam.T @ np.array([0.0, 1.0, 0.0])
    return _unit(_OPTICAL_FROM_ARKIT_CAMERA @ up_cam)


def gravity_aligned(calibration: CameraCalibration, frame: CapturedFrame) -> CameraCalibration:
    """Replace the nominal pose's pitch/roll with the phone's measured ones.

    Keeps the nominal yaw (horizontal viewing direction) and position.
    Returns the calibration unchanged when the frame has no usable pose.
    """
    up_opt = optical_up_from_arkit_pose(frame.arkit_pose)
    if up_opt is None:
        return calibration

    t_nominal = calibration.T_stage_from_optical
    r_nominal = t_nominal[:3, :3]
    # Nominal horizontal viewing direction in stage space.
    fwd_stage = r_nominal @ np.array([0.0, 0.0, 1.0])
    fwd_stage[1] = 0.0
    if np.linalg.norm(fwd_stage) < 1e-6:
        return calibration
    fwd_stage = _unit(fwd_stage)
    up_stage = np.array([0.0, 1.0, 0.0])

    # Optical basis: forward made orthogonal to measured up.
    fwd_opt = np.array([0.0, 0.0, 1.0])
    fwd_opt = fwd_opt - up_opt * float(fwd_opt @ up_opt)
    if np.linalg.norm(fwd_opt) < 1e-6:
        return calibration
    fwd_opt = _unit(fwd_opt)
    right_opt = np.cross(fwd_opt, up_opt)
    right_stage = np.cross(fwd_stage, up_stage)

    basis_opt = np.column_stack([right_opt, up_opt, fwd_opt])
    basis_stage = np.column_stack([right_stage, up_stage, fwd_stage])
    r_stage_from_optical = basis_stage @ basis_opt.T

    t = np.eye(4)
    t[:3, :3] = r_stage_from_optical
    t[:3, 3] = t_nominal[:3, 3]
    return replace(calibration, T_stage_from_optical=t)


def with_stage_correction(calibration: CameraCalibration, t_correction: NDArray[np.float64] | None) -> CameraCalibration:
    """Apply a stage->stage rigid correction on top of a camera pose."""
    if t_correction is None:
        return calibration
    return replace(calibration, T_stage_from_optical=t_correction @ calibration.T_stage_from_optical)


def _voxel_downsample(points: NDArray[np.float64], voxel_m: float) -> NDArray[np.float64]:
    if points.shape[0] == 0:
        return points
    keys = np.floor(points / voxel_m).astype(np.int64)
    _, idx = np.unique(keys, axis=0, return_index=True)
    return points[np.sort(idx)]


def _yaw_matrix(theta: float) -> NDArray[np.float64]:
    c, s = np.cos(theta), np.sin(theta)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def _icp_yaw_translation(
    source: NDArray[np.floating],
    target: NDArray[np.floating],
    *,
    voxel_m: float = 0.02,
    iterations: int = 40,
    keep_fraction: float = 0.7,
    max_pair_distance_m: float = 0.35,
) -> tuple[NDArray[np.float64], float, int]:
    """Rigid (yaw + translation) ICP aligning ``source`` onto ``target``.

    Returns ``(T_target_from_source, rms_m, inlier_count)``. Correspondences
    farther than ``max_pair_distance_m`` are dropped and only the closest
    ``keep_fraction`` of the rest are used each iteration, which tolerates the
    partial overlap of two views of one body.
    """
    src = _voxel_downsample(np.asarray(source, np.float64), voxel_m)
    tgt = _voxel_downsample(np.asarray(target, np.float64), voxel_m)
    if src.shape[0] < 50 or tgt.shape[0] < 50:
        raise ValueError("not enough points to register")

    tree = cKDTree(tgt)
    # Start from centroid alignment: the two views are of one person.
    t_total = np.eye(4)
    t_total[:3, 3] = tgt.mean(axis=0) - src.mean(axis=0)
    cur = src + t_total[:3, 3]

    rms = float("inf")
    inliers = 0
    for _ in range(iterations):
        dist, idx = tree.query(cur, k=1)
        keep = dist <= max_pair_distance_m
        if keep.sum() < 30:
            break
        cutoff = np.quantile(dist[keep], keep_fraction)
        keep &= dist <= cutoff
        p = cur[keep]
        q = tgt[idx[keep]]
        inliers = int(keep.sum())

        pc, qc = p.mean(axis=0), q.mean(axis=0)
        p0, q0 = p - pc, q - qc
        # Yaw about +Y from the horizontal (x, z) components.
        h = p0[:, [0, 2]].T @ q0[:, [0, 2]]
        u, _, vt = np.linalg.svd(h)
        d = np.sign(np.linalg.det(vt.T @ u.T))
        r2 = vt.T @ np.diag([1.0, d]) @ u.T
        theta = float(np.arctan2(r2[1, 0], r2[0, 0]))
        # r2 acts on (x, z); the stage yaw matrix acts on (x, z) as
        # [[c, s], [-s, c]], so theta maps with a sign flip.
        r = _yaw_matrix(-theta)
        t = qc - r @ pc

        step = np.eye(4)
        step[:3, :3] = r
        step[:3, 3] = t
        t_total = step @ t_total
        cur = (r @ cur.T).T + t

        new_rms = float(np.sqrt(np.mean(np.sum(((r @ p.T).T + t - q) ** 2, axis=1))))
        if abs(rms - new_rms) < 1e-5:
            rms = new_rms
            break
        rms = new_rms

    if not np.isfinite(rms) or inliers < 30 or rms > max_pair_distance_m:
        raise ValueError("insufficient valid correspondences")
    return t_total, rms, inliers


def register_yaw_translation(
    source: NDArray[np.floating],
    target: NDArray[np.floating],
    *,
    initial_yaws_deg: tuple[float, ...] = (-45.0, -30.0, -15.0, 0.0, 15.0, 30.0, 45.0),
    **kwargs,
) -> tuple[NDArray[np.float64], float, int]:
    """Multi-start wrapper: ICP alone finds only a nearby minimum, so try
    several initial yaws about the source centroid and keep the best fit."""
    src = np.asarray(source, np.float64)
    if src.shape[0] < 50 or not np.isfinite(src).all():
        raise ValueError("not enough finite points to register")
    centroid = src.mean(axis=0)
    best: tuple[NDArray[np.float64], float, int] | None = None
    for yaw in initial_yaws_deg:
        r = _yaw_matrix(np.radians(yaw))
        pre = np.eye(4)
        pre[:3, :3] = r
        pre[:3, 3] = centroid - r @ centroid
        seeded = (r @ (src - centroid).T).T + centroid
        try:
            t, rms, inliers = _icp_yaw_translation(seeded, target, **kwargs)
        except ValueError:
            continue
        # Score by fit quality but require a reasonable share of inliers.
        if best is None or (inliers >= 0.6 * best[2] and rms < best[1]):
            best = (t @ pre, rms, inliers)
    if best is None:
        raise ValueError("not enough points to register")
    return best
