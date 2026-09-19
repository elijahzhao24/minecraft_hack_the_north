"""Limb capsules from registered joint centres and the masked surface cloud.

Endpoints are the fused joint positions; they are never lengthened beyond the
joints to catch a ray. The radius is a robust percentile of the radial
distances of surface points assigned to this limb (nearest-segment
assignment keeps torso and neighbouring limbs out), with end-cap trimming,
capped to anatomical bounds. Insufficient support falls back to the saved
subject radius (labelled ``subject_default``) or the labelled global default;
missing joints disable the collider.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from hmc_backend.colliders.fit_common import (
    FitConfig,
    FitOutcome,
    LandmarkMap,
    Segment,
    assign_points_to_segments,
    landmark_conf,
    mad,
    pos,
    robust_percentile,
    source_quality,
)
from hmc_backend.colliders.geometry import point_segment_distance
from hmc_backend.colliders.models import SPEC_BY_ID, disabled, make_capsule
from hmc_backend.colliders.subject import DimensionEstimate, SubjectDimensions
from hmc_backend.contracts.enums import FitSource
from hmc_backend.vision.model_mapping import Side, body_name


@dataclass(frozen=True, slots=True)
class LimbSpec:
    collider_id: str
    side: Side
    joint_a: str  # body short name
    joint_b: str
    dimension: str  # SubjectDimensions field


def limb_specs() -> tuple[LimbSpec, ...]:
    out = []
    for s in ("left", "right"):
        out += [
            LimbSpec(f"arm.{s}.upper", s, f"{s}_shoulder", f"{s}_elbow", "upper_arm_radius_m"),
            LimbSpec(f"arm.{s}.forearm", s, f"{s}_elbow", f"{s}_wrist", "forearm_radius_m"),
            LimbSpec(f"leg.{s}.thigh", s, f"{s}_hip", f"{s}_knee", "thigh_radius_m"),
            LimbSpec(f"leg.{s}.shin", s, f"{s}_knee", f"{s}_ankle", "shin_radius_m"),
        ]
    return tuple(out)


def body_segments(lms: LandmarkMap, subject: SubjectDimensions) -> list[Segment]:
    """All limb segments plus torso/head/hand/foot pseudo-segments for point assignment."""
    segs: list[Segment] = []
    for spec in limb_specs():
        a, b = pos(lms, body_name(spec.joint_a)), pos(lms, body_name(spec.joint_b))
        if a is not None and b is not None:
            segs.append(Segment(spec.collider_id, a, b, subject.get(spec.dimension, spec.side).value_m))
    ls, rs = pos(lms, body_name("left_shoulder")), pos(lms, body_name("right_shoulder"))
    lh, rh = pos(lms, body_name("left_hip")), pos(lms, body_name("right_hip"))
    if ls is not None and rs is not None and lh is not None and rh is not None:
        sh_c, hp_c = (ls + rs) / 2.0, (lh + rh) / 2.0
        half_width = float(np.linalg.norm(ls - rs)) / 2.0
        segs.append(Segment("torso", hp_c, sh_c, max(half_width, 0.12)))
    head = pos(lms, body_name("head_center"))
    if head is not None:
        segs.append(Segment("head", head, head, subject.head_radius_m.value_m))
    for s in ("left", "right"):
        w, mcp = pos(lms, f"hand.{s}.wrist"), pos(lms, f"hand.{s}.middle_mcp")
        if w is not None and mcp is not None:
            segs.append(Segment(f"hand.{s}", w, w + (mcp - w) * 2.0, subject.get("hand_width_m", s).value_m / 2.0))
        heel, toe = pos(lms, body_name(f"{s}_heel")), pos(lms, body_name(f"{s}_foot_index"))
        if heel is not None and toe is not None:
            segs.append(Segment(f"foot.{s}", heel, toe, subject.get("foot_width_m", s).value_m / 2.0))
    return segs


def fit_limb_capsule(
    spec: LimbSpec,
    lms: LandmarkMap,
    xyz: NDArray[np.float32],
    assignment: NDArray[np.int64],
    segments: list[Segment],
    subject: SubjectDimensions,
    cfg: FitConfig,
) -> FitOutcome:
    body_part = SPEC_BY_ID[spec.collider_id].body_part
    a = pos(lms, body_name(spec.joint_a))
    b = pos(lms, body_name(spec.joint_b))
    rep: dict = {"joints": (spec.joint_a, spec.joint_b)}
    if a is None or b is None:
        rep["reason"] = "missing_joint"
        return FitOutcome(disabled(spec.collider_id, "missing_joint"), report=rep)
    length = float(np.linalg.norm(b - a))
    if length < 0.03:
        rep["reason"] = "coincident_joints"
        return FitOutcome(disabled(spec.collider_id, "coincident_joints"), report=rep)

    seg_idx = next((i for i, s in enumerate(segments) if s.key == spec.collider_id), -1)
    mine = xyz[assignment == seg_idx] if seg_idx >= 0 and xyz.shape[0] else np.zeros((0, 3), np.float32)
    support = 0
    radius: float | None = None
    spread = float("nan")
    if mine.shape[0]:
        radial, u = point_segment_distance(mine, a, b)
        keep = (u >= cfg.capsule_axial_trim) & (u <= 1.0 - cfg.capsule_axial_trim) & (radial <= cfg.capsule_search_radius_m)
        radial = radial[keep]
        support = int(radial.size)
        if support >= cfg.capsule_min_support:
            r = robust_percentile(radial, cfg.capsule_radius_percentile) + cfg.capsule_margin_m
            radius = float(np.clip(r, cfg.capsule_min_radius_m, cfg.max_limb_radius_m))
            spread = mad(radial, float(np.median(radial)))
    rep["support"] = support

    conf = landmark_conf(lms, body_name(spec.joint_a), body_name(spec.joint_b))
    if radius is not None:
        est = DimensionEstimate(radius, FitSource.OBSERVED, support, spread)
        rep.update(radius_m=radius, spread_m=spread, source="observed")
        quality = source_quality(FitSource.OBSERVED, 0.5 * conf + 0.5 * min(1.0, support / 200.0))
        return FitOutcome(
            make_capsule(spec.collider_id, body_part, a, b, radius, FitSource.OBSERVED, quality),
            subject_update=(spec.dimension, spec.side, est),
            report=rep,
        )

    fallback = subject.as_subject_default(spec.dimension, spec.side)
    rep.update(radius_m=fallback.value_m, source=fallback.source.value, reason="insufficient_support")
    quality = source_quality(fallback.source, conf)
    return FitOutcome(
        make_capsule(spec.collider_id, body_part, a, b, fallback.value_m, fallback.source, quality), report=rep
    )


def fit_all_limbs(
    lms: LandmarkMap,
    xyz: NDArray[np.float32],
    subject: SubjectDimensions,
    cfg: FitConfig,
) -> tuple[list[FitOutcome], list[Segment], NDArray[np.int64]]:
    """Fit the eight limb capsules; also returns the segment assignment for other fitters."""
    segments = body_segments(lms, subject)
    assignment = assign_points_to_segments(xyz.astype(np.float64), segments) if xyz.shape[0] else np.zeros(0, np.int64)
    outcomes = [fit_limb_capsule(spec, lms, xyz, assignment, segments, subject, cfg) for spec in limb_specs()]
    return outcomes, segments, assignment
