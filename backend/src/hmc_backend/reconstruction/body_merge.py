"""Forced front/back body assembly, independent of surface correspondences.

This is a visual body-space correction, NOT a camera calibration. The front
view defines the body profile. The rear view is yaw-locked opposite it, then
its torso/lower-body silhouette is tethered to that profile by height. The
inner depth envelopes meet; a small thickness prior keeps near-planar scans
from collapsing into the same sheet. No ICP and no invented surface points.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from numpy.typing import NDArray

from hmc_backend.contracts.internal import CameraCalibration, ColoredPointCloud


class BodyMergeRejected(ValueError):
    pass


def opposed_calibrations(calibrations: dict[str, CameraCalibration], reference: str):
    """Enforce the operator's 180-degree assumption, keeping measured pitch/roll."""
    front = calibrations[reference].T_stage_from_optical[:3, 2].copy()
    front[1] = 0
    if np.linalg.norm(front) < 0.2:
        raise BodyMergeRejected("camera_looks_vertical")
    front /= np.linalg.norm(front)
    out = dict(calibrations)
    for dev, calib in calibrations.items():
        if dev == reference:
            continue
        forward = calib.T_stage_from_optical[:3, 2].copy()
        forward[1] = 0
        if np.linalg.norm(forward) < 0.2:
            raise BodyMergeRejected("camera_looks_vertical")
        yaw = np.arctan2(-front[0], -front[2]) - np.arctan2(forward[0], forward[2])
        c, s = np.cos(yaw), np.sin(yaw)
        rotation = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
        t = calib.T_stage_from_optical.copy()
        t[:3, :3] = rotation @ t[:3, :3]
        out[dev] = replace(calib, T_stage_from_optical=t)
    return out


@dataclass(frozen=True)
class BodyWarp:
    """Invertible, height-dependent body-space map; also used for landmark samples.

    Basis columns are lateral, up, toward-front. Width changes are limited to
    the body core and become a translation outside it, so arms retain detail.
    """

    basis: NDArray[np.float64]
    heights: NDArray[np.float64]
    source_x: NDArray[np.float64]
    target_x: NDArray[np.float64]
    source_half_width: NDArray[np.float64]
    target_half_width: NDArray[np.float64]
    depth_shift: NDArray[np.float64]
    y_shift: float

    def apply(self, points: NDArray, *, inverse: bool = False) -> NDArray[np.float64]:
        p = np.asarray(points, np.float64).reshape(-1, 3) @ self.basis
        y = p[:, 1] if inverse else p[:, 1] + self.y_shift
        sx, tx, sw, tw, dz = [
            np.interp(y, self.heights, values)
            for values in (
                self.source_x,
                self.target_x,
                self.source_half_width,
                self.target_half_width,
                self.depth_shift,
            )
        ]
        if inverse:
            sx, tx, sw, tw = tx, sx, tw, sw
        x = p[:, 0] - sx
        # Continuous, monotone mapping. Hands outside the trunk are translated,
        # not scaled into it. All points at a height share the depth correction.
        p[:, 0] = tx + np.where(np.abs(x) <= sw, x * tw / sw, x + np.sign(x) * (tw - sw))
        p[:, 1] += -self.y_shift if inverse else self.y_shift
        p[:, 2] += -dz if inverse else dz
        return p @ self.basis.T

    def cloud(self, cloud: ColoredPointCloud) -> ColoredPointCloud:
        return replace(
            cloud, xyz_stage_m=np.ascontiguousarray(self.apply(cloud.xyz_stage_m), np.float32)
        )


def _core(points: NDArray, anchors: dict[str, NDArray], basis: NDArray):
    p = np.asarray(points, np.float64) @ basis
    if len(p) < 100 or not np.isfinite(p).all():
        raise BodyMergeRejected("insufficient_person_depth")
    a = {name.removeprefix("body."): np.asarray(pos) @ basis for name, pos in anchors.items()}
    hips = [a[n] for n in ("left_hip", "right_hip") if n in a]
    shoulders = [a[n] for n in ("left_shoulder", "right_shoulder") if n in a]
    # Arms cannot establish body height or pull the body center sideways.
    floor = float(np.quantile(p[:, 1], 0.02))
    top = float(np.quantile(p[:, 1], 0.98))
    center = (
        float(np.mean(hips, axis=0)[0])
        if len(hips) == 2
        else float(np.median(p[p[:, 1] < floor + 0.65 * (top - floor), 0]))
    )
    width = abs(shoulders[0][0] - shoulders[1][0]) if len(shoulders) == 2 else 0.5
    half_width = float(np.clip(width * 0.65, 0.18, 0.4))
    core = p[np.abs(p[:, 0] - center) <= half_width]
    if len(core) < 100:
        raise BodyMergeRejected("torso_not_visible")
    floor, top = np.quantile(core[:, 1], [0.02, 0.98])
    if top - floor < 0.6:
        raise BodyMergeRejected("body_height_too_small")
    shoulder_y = (
        float(np.mean(shoulders, axis=0)[1])
        if len(shoulders) == 2
        else float(floor + 0.80 * (top - floor))
    )
    # Use both legs through chest; exclude head and raised arms from the fit.
    return core, float(floor), shoulder_y, a


def assemble_opposing_body(
    clouds: dict[str, ColoredPointCloud],
    calibrations: dict[str, CameraCalibration],
    reference: str,
    anchors: dict[str, dict[str, NDArray]],
    *,
    min_thickness_m: float = 0.12,
    seam_overlap_m: float = 0.01,
) -> tuple[dict[str, ColoredPointCloud], dict[str, BodyWarp], dict]:
    if len(clouds) != 2 or reference not in clouds:
        raise BodyMergeRejected("requires_front_and_rear")
    rear = next(dev for dev in clouds if dev != reference)
    toward_front = -calibrations[reference].T_stage_from_optical[:3, 2].copy()
    toward_front[1] = 0
    toward_front /= np.linalg.norm(toward_front)
    up = np.array([0.0, 1.0, 0.0])
    basis = np.column_stack([np.cross(up, toward_front), up, toward_front])
    f, floor_f, shoulder_f, fa = _core(
        clouds[reference].xyz_stage_m, anchors.get(reference, {}), basis
    )
    b, floor_b, shoulder_b, ba = _core(clouds[rear].xyz_stage_m, anchors.get(rear, {}), basis)
    del shoulder_b
    vertical = []
    for joint in ("shoulder", "hip", "knee", "ankle"):
        names = [f"{side}_{joint}" for side in ("left", "right")]
        if all(n in fa and n in ba for n in names):
            vertical.append(np.mean([fa[n][1] - ba[n][1] for n in names]))
    dy = float(np.median(vertical)) if vertical else floor_f - floor_b
    b[:, 1] += dy
    # Independent height slices align trunk AND lower body, not cloud centroids.
    heights, sx, tx, sw, tw, dz = [], [], [], [], [], []
    depth_prior_used = False
    for y in np.linspace(floor_f + 0.08, shoulder_f - 0.04, 12):
        pf, pb = f[np.abs(f[:, 1] - y) < 0.09], b[np.abs(b[:, 1] - y) < 0.09]
        if min(len(pf), len(pb)) < 20:
            continue
        fl, fr = np.quantile(pf[:, 0], [0.05, 0.95])
        bl, br = np.quantile(pb[:, 0], [0.05, 0.95])
        if min(fr - fl, br - bl) < 0.06:
            continue
        front_inner = float(np.quantile(pf[:, 2], 0.05))
        rear_inner = float(np.quantile(pb[:, 2], 0.95))
        seam_shift = front_inner - rear_inner + seam_overlap_m
        # Surface medians remain ordered front/back, even if LiDAR supplies
        # almost flat sheets with no observed silhouette seam at all.
        thickness_shift = float(np.median(pf[:, 2]) - np.median(pb[:, 2]) - min_thickness_m)
        shift = min(seam_shift, thickness_shift)
        depth_prior_used |= thickness_shift < seam_shift
        heights.append(float(y))
        sx.append(float((bl + br) / 2))
        tx.append(float((fl + fr) / 2))
        sw.append(float((br - bl) / 2))
        tw.append(float((fr - fl) / 2))
        dz.append(shift)
    if len(heights) < 5 or heights[-1] - heights[0] < 0.45:
        raise BodyMergeRejected("torso_and_legs_not_visible_in_both_views")
    # Both the lower and upper body must be constrained, not just several
    # tightly grouped slices of a sleeve or upper chest.
    if heights[0] > floor_f + 0.3 or heights[-1] < shoulder_f - 0.25:
        raise BodyMergeRejected("torso_and_legs_not_visible_in_both_views")
    ratios = np.asarray(tw) / np.asarray(sw)
    if np.any((ratios < 0.5) | (ratios > 2)):
        raise BodyMergeRejected("body_profiles_incompatible")
    warp = BodyWarp(basis, *[np.asarray(v, np.float64) for v in (heights, sx, tx, sw, tw, dz)], dy)
    out = dict(clouds)
    out[rear] = warp.cloud(clouds[rear])
    return (
        out,
        {rear: warp},
        {
            "state": "applied",
            "reference_device": reference,
            "rear_device": rear,
            "method": "opposing_body_profile",
            "profile_slices": len(heights),
            "max_lateral_shift_m": round(float(np.max(np.abs(np.subtract(tx, sx)))), 3),
            "vertical_shift_m": round(dy, 3),
            "max_depth_shift_m": round(float(np.max(np.abs(dz))), 3),
            "depth_prior_used": bool(depth_prior_used),
            "min_thickness_m": min_thickness_m,
            "seam_overlap_m": seam_overlap_m,
        },
    )
