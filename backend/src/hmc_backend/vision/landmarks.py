"""Fuse per-view observations into registered 3D landmarks with provenance.

For every canonical landmark name the final position is chosen in priority
order based on *validated* quality:

1. ``triangulated`` — two-view ray intersection that is compatible with any
   well-supported depth observation (then fused with those observations).
2. ``depth_neighborhood`` — a strong single-view (or agreeing two-view) depth
   sample.
3. ``registered_model_prior`` — the hip-/hand-centred model prior after a
   robust similarity fit against the observed landmarks.
4. ``derived`` — explicit constructions (``pelvis_center``, ``head_center``).
5. ``unavailable`` — ``position=None, valid=False``. Never ``(0,0,0)``.

Every canonical name is always present in the output so downstream code can
index by name and reason about validity explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from hmc_backend.contracts.enums import LandmarkSource
from hmc_backend.contracts.internal import (
    CameraCalibration,
    CapturedFrame,
    Landmark2DObservation,
    Landmark3D,
    ViewDetection,
)
from hmc_backend.vision import registration as reg
from hmc_backend.vision import triangulation as tri
from hmc_backend.vision.depth_sampling import (
    DepthObservation,
    DepthSamplingConfig,
    observe_landmarks,
)
from hmc_backend.vision.model_mapping import (
    ALL_CANONICAL_NAMES,
    HAND_LANDMARK_NAMES,
    POSE_LANDMARK_NAMES,
    SIDES,
    Side,
    body_name,
    hand_index,
    hand_name,
    pose_index,
)


@dataclass(frozen=True, slots=True)
class FusionConfig:
    depth: DepthSamplingConfig = field(default_factory=DepthSamplingConfig)
    triangulation: tri.TriangulationConfig = field(default_factory=tri.TriangulationConfig)
    registration: reg.RegistrationConfig = field(default_factory=reg.RegistrationConfig)
    min_depth_quality: float = 0.25
    two_view_depth_agreement_m: float = 0.06
    head_center_back_offset_m: float = 0.07
    prior_confidence_cap: float = 0.5
    # A single-view depth sample is a surface hit at the landmark's pixel: for an
    # occluded landmark that surface belongs to whatever is in front (e.g. the
    # shin in front of a heel). When it sits farther than this from the registered
    # prior, the sample is treated as an occluder hit and the prior is used instead.
    single_view_depth_prior_gate_m: float = 0.10
    single_view_hand_depth_prior_gate_m: float = 0.06


@dataclass(frozen=True, slots=True)
class ViewInput:
    frame: CapturedFrame
    detection: ViewDetection
    calibration: CameraCalibration


@dataclass(slots=True)
class FusionReport:
    """Diagnostics for the debug JSON report; never serialized to the wire."""

    per_landmark: dict[str, dict] = field(default_factory=dict)
    pose_registration: dict | None = None
    hand_registration: dict[str, dict | None] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class FusionResult:
    landmarks: tuple[Landmark3D, ...]
    report: FusionReport

    def by_name(self) -> dict[str, Landmark3D]:
        return {lm.name: lm for lm in self.landmarks}


Vec3 = tuple[float, float, float]


def _t3(p) -> Vec3:
    a = np.asarray(p, dtype=np.float64).reshape(3)
    return (float(a[0]), float(a[1]), float(a[2]))


def _unavailable(name: str) -> Landmark3D:
    return Landmark3D(name, None, False, LandmarkSource.UNAVAILABLE.value)


# --- per-view 2D observations keyed by canonical name -----------------------


def _canonical_2d(det: ViewDetection) -> dict[str, Landmark2DObservation]:
    out: dict[str, Landmark2DObservation] = {}
    for lm in det.body:
        out[body_name(lm.name)] = lm
    for side, hand in (("left", det.left_hand), ("right", det.right_hand)):
        for lm in hand:
            out[hand_name(side, lm.name)] = lm
    return out


def _depth_observations(view: ViewInput, cfg: FusionConfig) -> dict[str, DepthObservation]:
    det = view.detection
    out = observe_landmarks(view.frame, view.calibration, det.person_mask, det.body, cfg.depth, name_prefix="body.")
    for side, hand in (("left", det.left_hand), ("right", det.right_hand)):
        out.update(
            observe_landmarks(view.frame, view.calibration, det.person_mask, hand, cfg.depth, name_prefix=f"hand.{side}.")
        )
    return out


# --- fusion core -------------------------------------------------------------


def _fuse_observed(
    name: str,
    views: list[ViewInput],
    obs2d: list[dict[str, Landmark2DObservation]],
    depth: list[dict[str, DepthObservation]],
    cfg: FusionConfig,
    report: dict,
) -> Landmark3D | None:
    """Triangulation-first, then depth. Returns ``None`` when neither qualifies."""
    depth_here = [(i, d[name]) for i, d in enumerate(depth) if name in d and d[name].quality >= cfg.min_depth_quality]
    vis = [o[name].visibility for o in obs2d if name in o and o[name].visibility is not None]
    visibility = max(vis) if vis else None

    valid2d = [(i, o[name]) for i, o in enumerate(obs2d) if name in o and o[name].valid]
    if len(valid2d) == 2 and len(views) == 2:
        (ia, a), (ib, b) = valid2d
        try:
            t = tri.triangulate(a.xy_px, views[ia].calibration, b.xy_px, views[ib].calibration, cfg.triangulation)
            samples = [(d.depth_m, d.support, views[i].calibration) for i, d in depth_here]
            if tri.compatible_with_depth(t, samples, cfg.triangulation):
                # Depth validates the ray intersection; it is a surface sample and is
                # not averaged into an interior joint centre.
                q = float(np.mean([d.quality for _, d in depth_here])) if depth_here else 0.0
                conf = min(1.0, 0.6 + 0.4 * q)
                report["triangulation"] = {"ok": True, "reprojection_px": t.reprojection_error_px, "angle_deg": t.ray_angle_deg}
                return Landmark3D(
                    name, t.position_stage_m, True, LandmarkSource.TRIANGULATED.value, conf, visibility,
                    tuple(views[i].frame.device_id for i in (ia, ib)), t.reprojection_error_px,
                )
            report["triangulation"] = {"ok": False, "reason": "depth_disagreement"}
        except tri.TriangulationRejected as exc:
            report["triangulation"] = {"ok": False, "reason": str(exc)}

    if not depth_here:
        return None
    report["depth"] = [
        {
            "device": d.device_id,
            "support": d.support,
            "spread_m": d.spread_m,
            "quality": d.quality,
            "pixel_rgb": tuple(float(x) for x in d.pixel_rgb),
        }
        for _, d in depth_here
    ]
    if len(depth_here) == 2:
        (ia, a), (ib, b) = depth_here
        if np.linalg.norm(np.subtract(a.position_stage_m, b.position_stage_m)) <= cfg.two_view_depth_agreement_m:
            p = np.average(np.stack([a.position_stage_m, b.position_stage_m]), axis=0, weights=[a.quality, b.quality])
            return Landmark3D(
                name, _t3(p), True, LandmarkSource.DEPTH_NEIGHBORHOOD.value, float(max(a.quality, b.quality)), visibility,
                (a.device_id, b.device_id), None,
            )
        report["depth_two_view"] = "disagree"
    _, best = max(depth_here, key=lambda kv: kv[1].quality)
    return Landmark3D(
        name, best.position_stage_m, True, LandmarkSource.DEPTH_NEIGHBORHOOD.value, best.quality, visibility, (best.device_id,), None
    )


def _register_pose(views: list[ViewInput], observed: dict[str, Landmark3D], cfg: FusionConfig, report: FusionReport):
    anchors = {
        n: observed[body_name(n)].position_stage_m
        for n in reg.POSE_ANCHOR_NAMES
        if body_name(n) in observed and observed[body_name(n)].valid
    }
    best: reg.Similarity | None = None
    best_prior = None
    for v in views:
        prior = v.detection.pose_world_prior_m
        if prior is None:
            continue
        try:
            sim = reg.register_pose_prior(prior, anchors, cfg.registration)
        except reg.RegistrationRejected as exc:
            report.warnings.append(f"pose_prior_rejected:{v.frame.device_id}:{exc}")
            continue
        if best is None or sim.rms_residual_m < best.rms_residual_m:
            best, best_prior = sim, prior
    if best is None:
        return None, None
    report.pose_registration = {"scale": best.scale, "rms_m": best.rms_residual_m, "anchors": best.anchor_count}
    return best, best_prior


def _register_hand(side: Side, views: list[ViewInput], observed: dict[str, Landmark3D], cfg: FusionConfig, report: FusionReport):
    anchors = {
        n: observed[hand_name(side, n)].position_stage_m
        for n in reg.HAND_ANCHOR_NAMES
        if hand_name(side, n) in observed and observed[hand_name(side, n)].valid
    }
    best = best_prior = None
    for v in views:
        prior = v.detection.hand_world_priors_m.get(side)
        if prior is None:
            continue
        try:
            sim = reg.register_hand_prior(prior, anchors, cfg.registration)
        except reg.RegistrationRejected as exc:
            report.warnings.append(f"hand_prior_rejected:{side}:{v.frame.device_id}:{exc}")
            continue
        if best is None or sim.rms_residual_m < best.rms_residual_m:
            best, best_prior = sim, prior
    report.hand_registration[side] = None if best is None else {"scale": best.scale, "rms_m": best.rms_residual_m}
    return best, best_prior


def _prior_conf(sim: reg.Similarity, max_rms: float, cap: float) -> float:
    return float(cap * max(0.0, 1.0 - sim.rms_residual_m / max_rms))


def _gate_single_view_depth(
    filled: dict[str, Landmark3D],
    stage: np.ndarray,
    names: tuple[str, ...],
    to_name,
    index_of,
    gate_m: float,
    prior_conf: float,
    report: FusionReport,
) -> None:
    """Replace single-view depth samples that sit far from the registered prior.

    Two-view depth agreement and depth-validated triangulation are left alone;
    only a lone surface sample can be an occluder hit without any cross-check.
    """
    for short in names:
        n = to_name(short)
        lm = filled.get(n)
        if lm is None or lm.source != LandmarkSource.DEPTH_NEIGHBORHOOD.value or len(lm.observed_by) != 1:
            continue
        p = stage[index_of(short)]
        dist = float(np.linalg.norm(np.subtract(lm.position_stage_m, p)))
        rec = report.per_landmark.setdefault(n, {})
        if dist > gate_m:
            filled[n] = Landmark3D(n, _t3(p), True, LandmarkSource.REGISTERED_MODEL_PRIOR.value, prior_conf, lm.visibility)
            rec["depth_prior_gate"] = {"distance_m": dist, "replaced": True, "device": lm.observed_by[0]}
        else:
            rec["depth_prior_gate"] = {"distance_m": dist, "replaced": False}


def _derive(observed: dict[str, Landmark3D], cfg: FusionConfig) -> dict[str, Landmark3D]:
    out: dict[str, Landmark3D] = {}

    def pos(short: str):
        lm = observed.get(body_name(short))
        return None if lm is None or not lm.valid else np.asarray(lm.position_stage_m)

    def conf(*shorts: str) -> float:
        vals = [observed[body_name(s)].confidence for s in shorts if observed[body_name(s)].confidence is not None]
        return float(min(vals)) if vals else 0.0

    lh, rh = pos("left_hip"), pos("right_hip")
    if lh is not None and rh is not None:
        out[body_name("pelvis_center")] = Landmark3D(
            body_name("pelvis_center"), _t3((lh + rh) / 2.0), True, LandmarkSource.DERIVED.value, conf("left_hip", "right_hip")
        )

    le, re = pos("left_ear"), pos("right_ear")
    if le is not None and re is not None:
        out[body_name("head_center")] = Landmark3D(
            body_name("head_center"), _t3((le + re) / 2.0), True, LandmarkSource.DERIVED.value, conf("left_ear", "right_ear")
        )
    else:
        face = [p for p in (pos("nose"), pos("left_eye"), pos("right_eye"), le, re) if p is not None]
        ls, rs = pos("left_shoulder"), pos("right_shoulder")
        if face and ls is not None and rs is not None:
            face_mean = np.mean(np.stack(face), axis=0)
            shoulder_c = (ls + rs) / 2.0
            facing = face_mean - shoulder_c
            facing[1] = 0.0
            n = np.linalg.norm(facing)
            if n > 1e-6:
                centre = face_mean - facing / n * cfg.head_center_back_offset_m
                out[body_name("head_center")] = Landmark3D(
                    body_name("head_center"), _t3(centre), True, LandmarkSource.DERIVED.value, 0.5 * conf("nose")
                )
    return out


def fuse_landmarks(views: list[ViewInput], cfg: FusionConfig | None = None) -> FusionResult:
    """Fuse one or two views into the full canonical landmark set."""
    cfg = cfg or FusionConfig()
    report = FusionReport()
    if not views:
        return FusionResult(tuple(_unavailable(n) for n in ALL_CANONICAL_NAMES), report)

    obs2d = [_canonical_2d(v.detection) for v in views]
    depth = [_depth_observations(v, cfg) for v in views]

    observed: dict[str, Landmark3D] = {}
    for name in ALL_CANONICAL_NAMES:
        rec: dict = {}
        lm = _fuse_observed(name, views, obs2d, depth, cfg, rec)
        if lm is not None:
            observed[name] = lm
        if rec:
            report.per_landmark[name] = rec

    # Registered priors fill what observation could not.
    filled: dict[str, Landmark3D] = dict(observed)
    sim, prior = _register_pose(views, observed, cfg, report)
    if sim is not None:
        stage = sim.apply(prior)
        c = _prior_conf(sim, cfg.registration.max_rms_residual_m, cfg.prior_confidence_cap)
        _gate_single_view_depth(
            filled, stage, POSE_LANDMARK_NAMES, body_name, pose_index, cfg.single_view_depth_prior_gate_m, c, report
        )
        for short in POSE_LANDMARK_NAMES:
            n = body_name(short)
            if n not in filled:
                filled[n] = Landmark3D(n, _t3(stage[pose_index(short)]), True, LandmarkSource.REGISTERED_MODEL_PRIOR.value, c)

    for side in SIDES:
        hsim, hprior = _register_hand(side, views, observed, cfg, report)
        if hsim is None:
            continue
        stage = hsim.apply(hprior)
        # Attach the registered hand wrist to the reconciled body wrist when available.
        body_wrist = filled.get(body_name(f"{side}_wrist"))
        if body_wrist is not None and body_wrist.valid and body_wrist.source != LandmarkSource.REGISTERED_MODEL_PRIOR.value:
            stage = stage + (np.asarray(body_wrist.position_stage_m) - stage[hand_index("wrist")])
        c = _prior_conf(hsim, cfg.registration.max_hand_rms_residual_m, cfg.prior_confidence_cap)
        _gate_single_view_depth(
            filled,
            stage,
            HAND_LANDMARK_NAMES,
            lambda s, side=side: hand_name(side, s),
            hand_index,
            cfg.single_view_hand_depth_prior_gate_m,
            c,
            report,
        )
        for short in HAND_LANDMARK_NAMES:
            n = hand_name(side, short)
            if n not in filled:
                filled[n] = Landmark3D(n, _t3(stage[hand_index(short)]), True, LandmarkSource.REGISTERED_MODEL_PRIOR.value, c)

    filled.update(_derive(filled, cfg))

    out = []
    for name in ALL_CANONICAL_NAMES:
        lm = filled.get(name) or _unavailable(name)
        if lm.valid and (lm.position_stage_m is None or not np.isfinite(lm.position_stage_m).all()):
            lm = _unavailable(name)
        out.append(lm)
    return FusionResult(tuple(out), report)
