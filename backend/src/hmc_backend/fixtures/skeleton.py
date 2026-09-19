"""A synthetic articulated person with known joints, hands, and surface.

Where :mod:`hmc_backend.fixtures.scene` renders a generic humanoid cloud, this
module builds a *skeleton-consistent* fixture: every MediaPipe pose landmark
and both 21-point hands are placed in stage meters, the surface point cloud is
generated around those bones with known radii, and
:class:`SkeletonViewDetector` projects the joints into each calibrated camera
exactly the way a real detector would report them (2D pixels, visibility,
hip-centred pose prior, hand-centred hand priors, person mask).

Because the ground truth is known, tests can measure landmark and collider
error in meters instead of checking only self-consistency.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
from numpy.typing import NDArray

from hmc_backend.calibration.model import RigCalibration
from hmc_backend.contracts.internal import (
    CameraCalibration,
    CapturedFrame,
    Landmark2DObservation,
    ViewDetection,
)
from hmc_backend.fixtures.scene import _cylinder, _sphere, project_to_view
from hmc_backend.vision.model_mapping import (
    HAND_LANDMARK_NAMES,
    POSE_LANDMARK_NAMES,
    SIDES,
    Side,
    hand_index,
    pose_index,
)
from hmc_backend.vision.triangulation import project_to_pixel

Vec3 = tuple[float, float, float]

# Side with its sign along stage +X (subject's right is +X in the front view).
_SIDE_SIGNS: tuple[tuple[Side, float], ...] = (("left", -1.0), ("right", 1.0))


@dataclass(frozen=True, slots=True)
class LimbRadii:
    upper_arm: float = 0.05
    forearm: float = 0.045
    thigh: float = 0.075
    shin: float = 0.055
    torso_half_extents: Vec3 = (0.26, 0.17, 0.11)  # up, lateral, forward (matches torso_frames axes)
    pelvis_half_extents: Vec3 = (0.10, 0.16, 0.11)
    head: float = 0.11
    hand_half_extents: Vec3 = (0.09, 0.045, 0.015)  # long, wide, thick
    foot_half_extents: Vec3 = (0.125, 0.045, 0.04)


@dataclass(frozen=True, slots=True)
class Skeleton:
    """Stage-frame joints (short pose names) and hands (short hand names per side)."""

    body: dict[str, Vec3]
    hands: dict[Side, dict[str, Vec3]]
    radii: LimbRadii = field(default_factory=LimbRadii)
    planted_feet: tuple[Side, ...] = ("left", "right")

    def body_array(self) -> NDArray[np.float64]:
        return np.array([self.body[n] for n in POSE_LANDMARK_NAMES], dtype=np.float64)

    def hand_array(self, side: Side) -> NDArray[np.float64]:
        return np.array([self.hands[side][n] for n in HAND_LANDMARK_NAMES], dtype=np.float64)


# --- construction ------------------------------------------------------------


def _hand_points(wrist: Vec3, longitudinal: Vec3, transverse: Vec3, *, spread: float = 1.0) -> dict[str, Vec3]:
    """A flat open hand: wrist at ``wrist``, fingers along ``longitudinal``, width along ``transverse``."""
    w = np.asarray(wrist, float)
    lo = np.asarray(longitudinal, float)
    lo /= np.linalg.norm(lo)
    tr = np.asarray(transverse, float)
    tr -= lo * np.dot(tr, lo)
    tr /= np.linalg.norm(tr)
    pts: dict[str, np.ndarray] = {"wrist": w}
    # MCP row ~9 cm from the wrist; index..pinky spread across ~7 cm.
    offsets = {"index": 0.03, "middle": 0.01, "ring": -0.01, "pinky": -0.03}
    lengths = {"index": (0.04, 0.025, 0.02), "middle": (0.045, 0.03, 0.02), "ring": (0.04, 0.025, 0.02), "pinky": (0.03, 0.02, 0.018)}
    for finger, off in offsets.items():
        mcp = w + lo * 0.09 + tr * (off * spread)
        pts[f"{finger}_mcp"] = mcp
        pip = mcp + lo * lengths[finger][0]
        dip = pip + lo * lengths[finger][1]
        tip = dip + lo * lengths[finger][2]
        pts[f"{finger}_pip"], pts[f"{finger}_dip"], pts[f"{finger}_tip"] = pip, dip, tip
    thumb_dir = (lo * 0.5 + tr * 0.85)
    thumb_dir /= np.linalg.norm(thumb_dir)
    cmc = w + lo * 0.03 + tr * 0.03 * spread
    pts["thumb_cmc"] = cmc
    pts["thumb_mcp"] = cmc + thumb_dir * 0.035
    pts["thumb_ip"] = pts["thumb_mcp"] + thumb_dir * 0.03
    pts["thumb_tip"] = pts["thumb_ip"] + thumb_dir * 0.025
    return {k: (float(v[0]), float(v[1]), float(v[2])) for k, v in pts.items()}


def neutral_skeleton() -> Skeleton:
    """Standing, facing the front camera (+Z), arms slightly out, palms facing -Z."""
    b: dict[str, Vec3] = {
        "nose": (0.0, 1.66, 0.10),
        "left_eye_inner": (-0.015, 1.69, 0.09),
        "left_eye": (-0.03, 1.69, 0.085),
        "left_eye_outer": (-0.045, 1.69, 0.075),
        "right_eye_inner": (0.015, 1.69, 0.09),
        "right_eye": (0.03, 1.69, 0.085),
        "right_eye_outer": (0.045, 1.69, 0.075),
        "left_ear": (-0.08, 1.67, 0.0),
        "right_ear": (0.08, 1.67, 0.0),
        "mouth_left": (-0.02, 1.62, 0.09),
        "mouth_right": (0.02, 1.62, 0.09),
        "left_shoulder": (-0.19, 1.45, 0.0),
        "right_shoulder": (0.19, 1.45, 0.0),
        "left_elbow": (-0.38, 1.18, 0.03),
        "right_elbow": (0.38, 1.18, 0.03),
        "left_wrist": (-0.52, 0.93, 0.08),
        "right_wrist": (0.52, 0.93, 0.08),
        "left_hip": (-0.10, 0.96, 0.0),
        "right_hip": (0.10, 0.96, 0.0),
        "left_knee": (-0.11, 0.52, 0.01),
        "right_knee": (0.11, 0.52, 0.01),
        "left_ankle": (-0.11, 0.08, 0.0),
        "right_ankle": (0.11, 0.08, 0.0),
        "left_heel": (-0.11, 0.03, -0.05),
        "right_heel": (0.11, 0.03, -0.05),
        "left_foot_index": (-0.11, 0.03, 0.17),
        "right_foot_index": (0.11, 0.03, 0.17),
    }
    hands: dict[Side, dict[str, Vec3]] = {}
    for side, sx in _SIDE_SIGNS:
        wrist = b[f"{side}_wrist"]
        elbow = b[f"{side}_elbow"]
        longitudinal = np.subtract(wrist, elbow)
        transverse = np.array([sx, 0.0, 0.0])  # width across the body's X axis
        hands[side] = _hand_points(wrist, tuple(longitudinal), tuple(transverse))
        b[f"{side}_pinky"] = hands[side]["pinky_mcp"]
        b[f"{side}_index"] = hands[side]["index_mcp"]
        b[f"{side}_thumb"] = hands[side]["thumb_mcp"]
    return Skeleton(body=b, hands=hands)


def right_arm_raised_skeleton() -> Skeleton:
    """Asymmetric handedness regression pose: right arm straight up, left hangs."""
    base = neutral_skeleton()
    b = dict(base.body)
    b["right_elbow"] = (0.24, 1.72, 0.02)
    b["right_wrist"] = (0.26, 1.98, 0.04)
    b["left_elbow"] = (-0.22, 1.18, 0.02)
    b["left_wrist"] = (-0.23, 0.92, 0.06)
    hands = dict(base.hands)
    for side, sx in _SIDE_SIGNS:
        longitudinal = np.subtract(b[f"{side}_wrist"], b[f"{side}_elbow"])
        hands[side] = _hand_points(b[f"{side}_wrist"], tuple(longitudinal), (sx, 0.0, 0.0))
        b[f"{side}_pinky"] = hands[side]["pinky_mcp"]
        b[f"{side}_index"] = hands[side]["index_mcp"]
        b[f"{side}_thumb"] = hands[side]["thumb_mcp"]
    return replace(base, body=b, hands=hands)


def lifted_turned_foot_skeleton() -> Skeleton:
    """Left foot lifted and turned outward ~40 degrees; only the right foot is planted."""
    base = neutral_skeleton()
    b = dict(base.body)
    ankle = np.array([-0.16, 0.28, 0.02])
    b["left_knee"] = (-0.13, 0.55, 0.12)
    b["left_ankle"] = tuple(ankle)
    a = np.radians(40.0)
    fwd = np.array([-np.sin(a), 0.0, np.cos(a)])
    b["left_heel"] = tuple(ankle + np.array([0.0, -0.05, 0.0]) - fwd * 0.05)
    b["left_foot_index"] = tuple(ankle + np.array([0.0, -0.06, 0.0]) + fwd * 0.17)
    return replace(base, body=b, planted_feet=("right",))


# --- surface -----------------------------------------------------------------


def _obb_points(center, axes, half, color, n, rng):
    c = np.asarray(center, float)
    r = np.asarray(axes, float)
    h = np.asarray(half, float)
    # Sample the six faces.
    face = rng.integers(0, 3, n)
    sign = rng.choice([-1.0, 1.0], n)
    local = rng.uniform(-1.0, 1.0, (n, 3)) * h
    local[np.arange(n), face] = sign * h[face]
    pts = c + local @ r
    cols = np.tile(np.asarray(color, np.uint8), (n, 1))
    return pts.astype(np.float32), cols


def _orthonormal(longitudinal, hint):
    lo = np.array(longitudinal, dtype=float)  # copy: callers reuse their vectors
    lo /= np.linalg.norm(lo)
    tr = np.array(hint, dtype=float)
    tr -= lo * np.dot(tr, lo)
    tr /= np.linalg.norm(tr)
    return np.stack([lo, tr, np.cross(lo, tr)])


def hand_frame(sk: Skeleton, side: Side) -> tuple[np.ndarray, np.ndarray]:
    """Ground-truth hand OBB centre and row axes (longitudinal, transverse, normal)."""
    h = sk.hands[side]
    lo = np.subtract(h["middle_mcp"], h["wrist"])
    tr = np.subtract(h["index_mcp"], h["pinky_mcp"])
    axes = _orthonormal(lo, tr)
    centre = np.asarray(h["wrist"], float) + axes[0] * sk.radii.hand_half_extents[0]
    return centre, axes


def foot_frame(sk: Skeleton, side: Side) -> tuple[np.ndarray, np.ndarray]:
    heel = np.asarray(sk.body[f"{side}_heel"], float)
    toe = np.asarray(sk.body[f"{side}_foot_index"], float)
    axes = _orthonormal(toe - heel, [0.0, 1.0, 0.0])
    axes = np.stack([axes[0], axes[2], axes[1]])  # longitudinal, lateral, vertical
    centre = (heel + toe) / 2.0 + axes[2] * sk.radii.foot_half_extents[2] * 0.5
    return centre, axes


def torso_frames(sk: Skeleton) -> tuple[tuple[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]:
    b = sk.body
    sh = (np.asarray(b["left_shoulder"]) + np.asarray(b["right_shoulder"])) / 2.0
    hp = (np.asarray(b["left_hip"]) + np.asarray(b["right_hip"])) / 2.0
    up = sh - hp
    lateral = np.asarray(b["left_shoulder"]) - np.asarray(b["right_shoulder"])
    axes = _orthonormal(up, lateral)  # up, lateral, forward(-ish)
    chest_c = hp + up * 0.62
    pelvis_c = hp + up * 0.12
    return (chest_c, axes), (pelvis_c, axes)


def make_skeleton_points(sk: Skeleton, seed: int = 0) -> tuple[NDArray[np.float32], NDArray[np.uint8]]:
    """Surface samples around the bones with the skeleton's known radii."""
    rng = np.random.default_rng(seed)
    b = sk.body
    r = sk.radii
    skin, shirt, pants = (240, 200, 170), (60, 120, 200), (40, 40, 60)
    parts = []
    head_c = (np.asarray(b["left_ear"]) + np.asarray(b["right_ear"])) / 2.0
    parts.append(_sphere(head_c, r.head, skin, 1800, rng))
    (chest_c, axes), (pelvis_c, _) = torso_frames(sk)
    parts.append(_obb_points(chest_c, axes, r.torso_half_extents, shirt, 4000, rng))
    parts.append(_obb_points(pelvis_c, axes, r.pelvis_half_extents, pants, 2000, rng))
    for s in SIDES:
        parts.append(_cylinder(b[f"{s}_shoulder"], b[f"{s}_elbow"], r.upper_arm, shirt, 1500, rng))
        parts.append(_cylinder(b[f"{s}_elbow"], b[f"{s}_wrist"], r.forearm, skin, 1500, rng))
        parts.append(_cylinder(b[f"{s}_hip"], b[f"{s}_knee"], r.thigh, pants, 2000, rng))
        parts.append(_cylinder(b[f"{s}_knee"], b[f"{s}_ankle"], r.shin, pants, 2000, rng))
        hc, ha = hand_frame(sk, s)
        parts.append(_obb_points(hc, ha, r.hand_half_extents, skin, 900, rng))
        fc, fa = foot_frame(sk, s)
        parts.append(_obb_points(fc, fa, r.foot_half_extents, pants, 900, rng))
    xyz = np.concatenate([p[0] for p in parts], axis=0)
    rgb = np.concatenate([p[1] for p in parts], axis=0)
    return xyz, rgb


# --- frames and detector -----------------------------------------------------


def render_frames(
    rig: RigCalibration,
    sk: Skeleton,
    *,
    seed: int = 0,
    splat: int = 2,
    capture_id=None,
) -> dict[str, CapturedFrame]:
    """Render the skeleton's surface into every rig camera as CapturedFrames."""
    from uuid import uuid4

    capture_id = capture_id or uuid4()
    session = uuid4()
    xyz, rgb = make_skeleton_points(sk, seed)
    out: dict[str, CapturedFrame] = {}
    for i, (device_id, calib) in enumerate(rig.cameras.items()):
        color, depth, conf = project_to_view(xyz, rgb, calib, splat=splat)
        out[device_id] = CapturedFrame(
            device_id=device_id,
            session_id=session,
            capture_id=capture_id,
            sequence=100 + i,
            capture_timestamp_s=1000.0 + i * 0.01,
            normalized_capture_time_s=1000.0,
            clock_uncertainty_ms=1.0,
            rgb=color,
            depth_m=depth,
            confidence=conf,
            K_rgb=calib.K_rgb.copy(),
            arkit_pose=np.eye(4),
        )
    return out


def _random_rotation(rng: np.random.Generator) -> np.ndarray:
    q = rng.normal(size=4)
    q /= np.linalg.norm(q)
    w, x, y, z = q
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


@dataclass
class SkeletonViewDetector:
    """Projects the known skeleton into each view like a detector would.

    * 2D pixels via the calibration; landmarks outside the raster are invalid.
    * ``visibility`` is 1.0 minus a small per-landmark noise; optionally hide names.
    * Person mask is the rendered depth footprint (resampled to RGB size).
    * Pose prior is the true skeleton *hip-centred*, randomly rotated and scaled
      (as MediaPipe world landmarks are), never stage coordinates.
    * Hand priors are hand-centred likewise.
    """

    rig: RigCalibration
    skeleton: Skeleton
    hidden: dict[str, set[str]] = field(default_factory=dict)  # device_id -> set of body short names
    pixel_noise_px: float = 0.0
    seed: int = 0
    prior_scale: float = 1.0
    drop_hands: dict[str, set[Side]] = field(default_factory=dict)

    def _project(self, calib: CameraCalibration, pts: dict[str, Vec3], rng, hidden: set[str], wh):
        out = []
        for name, p in pts.items():
            uv = project_to_pixel(p, calib)
            if uv is None:
                out.append(Landmark2DObservation(name, (-1.0, -1.0), None, 0.0, 0.0, False))
                continue
            u = uv[0] + rng.normal(scale=self.pixel_noise_px) if self.pixel_noise_px else uv[0]
            v = uv[1] + rng.normal(scale=self.pixel_noise_px) if self.pixel_noise_px else uv[1]
            inside = 0 <= u < wh[0] and 0 <= v < wh[1]
            vis = 0.05 if name in hidden else 0.97
            out.append(Landmark2DObservation(name, (float(u), float(v)), None, vis, 0.97, bool(inside and name not in hidden)))
        return tuple(out)

    def detect_view(self, frame: CapturedFrame) -> ViewDetection:
        calib = self.rig.camera(frame.device_id)
        rng = np.random.default_rng(self.seed ^ hash(frame.device_id) & 0xFFFF)
        wh = calib.rgb_size
        hidden = self.hidden.get(frame.device_id, set())
        sk = self.skeleton

        body = self._project(calib, {n: sk.body[n] for n in POSE_LANDMARK_NAMES}, rng, hidden, wh)
        hands: dict[str, tuple[Landmark2DObservation, ...]] = {}
        priors: dict[str, NDArray[np.float32]] = {}
        for side in SIDES:
            if side in self.drop_hands.get(frame.device_id, set()):
                hands[side] = ()
                continue
            hands[side] = self._project(calib, {n: sk.hands[side][n] for n in HAND_LANDMARK_NAMES}, rng, set(), wh)
            arr = sk.hand_array(side)
            centred = arr - arr[[hand_index(n) for n in ("wrist", "index_mcp", "middle_mcp", "ring_mcp", "pinky_mcp")]].mean(axis=0)
            priors[side] = ((centred / self.prior_scale) @ _random_rotation(rng)).astype(np.float32)

        # Hip-centred, rotated, scaled pose prior.
        arr = sk.body_array()
        hips = (arr[pose_index("left_hip")] + arr[pose_index("right_hip")]) / 2.0
        pose_prior = (((arr - hips) / self.prior_scale) @ _random_rotation(rng)).astype(np.float32)

        # Mask from the rendered depth footprint, resampled to RGB size.
        depth_mask = frame.depth_m > 0.0
        h_r, w_r = frame.rgb.shape[:2]
        if depth_mask.shape != (h_r, w_r):
            vv = np.clip((np.arange(h_r) * depth_mask.shape[0] / h_r).astype(int), 0, depth_mask.shape[0] - 1)
            uu = np.clip((np.arange(w_r) * depth_mask.shape[1] / w_r).astype(int), 0, depth_mask.shape[1] - 1)
            depth_mask = depth_mask[np.ix_(vv, uu)]

        return ViewDetection(
            device_id=frame.device_id,
            capture_id=frame.capture_id,
            person_mask=np.ascontiguousarray(depth_mask, np.bool_),
            body=body,
            left_hand=hands["left"],
            right_hand=hands["right"],
            pose_world_prior_m=pose_prior,
            hand_world_priors_m=priors,
        )
