"""Debug artifacts for validation captures (non-production, behind a flag).

* Per-camera RGB overlay: person-mask contour, body/hand landmark IDs with
  validity colour, sampled-depth pixel locations, and (optionally) the fused
  3D skeleton and collider wireframes projected into the view.
* Stage cloud coloured by source camera (PLY).
* Skeleton plus collider wireframe with stable IDs (PLY edge list + JSON).
* JSON report: observation sources, support counts, reprojection errors,
  fitted dimensions, disabled reasons, warnings.

These images may contain a real person. They are written only to the local
debug directory and must never be attached to Sentry events or uploaded; the
recording's consent/storage policy applies to this directory.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

from hmc_backend.colliders.models import (
    CapsuleCollider,
    DisabledCollider,
    FittedCollider,
    ObbCollider,
    SphereCollider,
)
from hmc_backend.contracts.internal import (
    CameraCalibration,
    CapturedFrame,
    ColoredPointCloud,
    Landmark3D,
    ViewDetection,
)
from hmc_backend.vision.model_mapping import body_name, hand_name

# Skeleton edges (body short names) for the wireframe.
BODY_EDGES: tuple[tuple[str, str], ...] = (
    ("left_shoulder", "right_shoulder"), ("left_hip", "right_hip"),
    ("left_shoulder", "left_hip"), ("right_shoulder", "right_hip"),
    ("left_shoulder", "left_elbow"), ("left_elbow", "left_wrist"),
    ("right_shoulder", "right_elbow"), ("right_elbow", "right_wrist"),
    ("left_hip", "left_knee"), ("left_knee", "left_ankle"), ("left_ankle", "left_heel"), ("left_heel", "left_foot_index"),
    ("right_hip", "right_knee"), ("right_knee", "right_ankle"), ("right_ankle", "right_heel"), ("right_heel", "right_foot_index"),
    ("left_ear", "right_ear"), ("nose", "left_ear"), ("nose", "right_ear"),
)
def _finger_chain(finger: str) -> tuple[str, ...]:
    if finger == "thumb":
        return ("wrist", "thumb_cmc", "thumb_mcp", "thumb_ip", "thumb_tip")
    return ("wrist", f"{finger}_mcp", f"{finger}_pip", f"{finger}_dip", f"{finger}_tip")


HAND_EDGES: tuple[tuple[str, str], ...] = tuple(
    (a, b)
    for finger in ("thumb", "index", "middle", "ring", "pinky")
    for a, b in zip(_finger_chain(finger)[:-1], _finger_chain(finger)[1:], strict=True)
)

# Colours are RGB (the overlay is returned in the frame's RGB order).
COLOR_MASK = (0, 255, 255)
COLOR_VALID = (0, 220, 0)
COLOR_INVALID = (230, 40, 40)
COLOR_DEPTH = (255, 200, 0)
COLOR_SKELETON = (255, 255, 255)
COLOR_COLLIDER = {"observed": (60, 200, 255), "subject_default": (255, 160, 60), "global_default": (200, 60, 255)}
SOURCE_PALETTE = ((230, 80, 80), (80, 160, 255), (80, 220, 120), (240, 200, 60), (200, 100, 240), (90, 230, 230))


# --- projection ---------------------------------------------------------------


def project_stage_to_rgb(points: NDArray, calib: CameraCalibration) -> tuple[NDArray[np.float64], NDArray[np.bool_]]:
    """Pixel coordinates (N x 2) in the RGB raster and an in-front-of-camera mask."""
    p = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    r = calib.T_stage_from_optical[:3, :3]
    eye = calib.T_stage_from_optical[:3, 3]
    opt = (p - eye) @ r
    z = opt[:, 2]
    front = z > 1e-3
    zs = np.where(front, z, 1.0)
    k = calib.K_rgb
    u = k[0, 0] * opt[:, 0] / zs + k[0, 2]
    v = k[1, 1] * opt[:, 1] / zs + k[1, 2]
    return np.stack([u, v], axis=1), front


# --- collider wireframes -------------------------------------------------------


def _circle(centre, u, v, radius, n=24):
    t = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    pts = centre + np.outer(np.cos(t), u) * radius + np.outer(np.sin(t), v) * radius
    return [(pts[i], pts[(i + 1) % n]) for i in range(n)]


def _basis_perp(axis):
    a = np.asarray(axis, float)
    a = a / (np.linalg.norm(a) or 1.0)
    tmp = np.array([1.0, 0.0, 0.0]) if abs(a[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(a, tmp)
    u /= np.linalg.norm(u)
    return a, u, np.cross(a, u)


def collider_wireframe(c: FittedCollider) -> list[tuple[NDArray, NDArray]]:
    """3D line segments (pairs of stage points) outlining one collider."""
    if isinstance(c, DisabledCollider):
        return []
    if isinstance(c, SphereCollider):
        ctr = np.asarray(c.center)
        e = np.eye(3)
        return _circle(ctr, e[0], e[1], c.radius) + _circle(ctr, e[1], e[2], c.radius) + _circle(ctr, e[0], e[2], c.radius)
    if isinstance(c, CapsuleCollider):
        a, b = np.asarray(c.a), np.asarray(c.b)
        axis, u, v = _basis_perp(b - a)
        segs = _circle(a, u, v, c.radius) + _circle(b, u, v, c.radius)
        for d in (u, -u, v, -v):
            segs.append((a + d * c.radius, b + d * c.radius))
        # End caps: half circles in the (u, axis) plane bulging outward.
        segs += [(p, q) for p, q in _circle(a, u, -axis, c.radius) if np.dot((p + q) / 2 - a, -axis) >= 0]
        segs += [(p, q) for p, q in _circle(b, u, axis, c.radius) if np.dot((p + q) / 2 - b, axis) >= 0]
        return segs
    if isinstance(c, ObbCollider):
        ctr = np.asarray(c.center)
        axes = c.axes_array()
        h = np.asarray(c.half_extents)
        corners = np.array([ctr + axes.T @ (np.array(s) * h) for s in np.array(np.meshgrid([-1, 1], [-1, 1], [-1, 1])).T.reshape(-1, 3)])
        edges = []
        for i in range(8):
            for j in range(i + 1, 8):
                if (i ^ j).bit_count() == 1:
                    edges.append((corners[i], corners[j]))
        return edges
    return []


def skeleton_edges(landmarks: dict[str, Landmark3D]) -> list[tuple[NDArray, NDArray, str]]:
    """Valid 3D skeleton segments as (a, b, label)."""
    out = []

    def p(n):
        lm = landmarks.get(n)
        return None if lm is None or not lm.valid or lm.position_stage_m is None else np.asarray(lm.position_stage_m)

    for a, b in BODY_EDGES:
        pa, pb = p(body_name(a)), p(body_name(b))
        if pa is not None and pb is not None:
            out.append((pa, pb, f"{a}-{b}"))
    for side in ("left", "right"):
        for a, b in HAND_EDGES:
            pa, pb = p(hand_name(side, a)), p(hand_name(side, b))
            if pa is not None and pb is not None:
                out.append((pa, pb, f"{side}.{a}-{b}"))
    return out


# --- 2D overlay ---------------------------------------------------------------


def _draw_segments_2d(img, segs, calib, color, thickness=1):
    if not segs:
        return
    a = np.stack([s[0] for s in segs])
    b = np.stack([s[1] for s in segs])
    ua, fa = project_stage_to_rgb(a, calib)
    ub, fb = project_stage_to_rgb(b, calib)
    h, w = img.shape[:2]
    for i in range(len(segs)):
        if not (fa[i] and fb[i]):
            continue
        p0 = (round(ua[i, 0]), round(ua[i, 1]))
        p1 = (round(ub[i, 0]), round(ub[i, 1]))
        if max(abs(p0[0]), abs(p1[0])) > 4 * w or max(abs(p0[1]), abs(p1[1])) > 4 * h:
            continue
        cv2.line(img, p0, p1, color, thickness, cv2.LINE_AA)


def draw_view_overlay(
    frame: CapturedFrame,
    detection: ViewDetection,
    calib: CameraCalibration,
    *,
    per_landmark_report: dict[str, dict] | None = None,
    landmarks: dict[str, Landmark3D] | None = None,
    colliders: tuple[FittedCollider, ...] = (),
    label_landmarks: bool = True,
) -> NDArray[np.uint8]:
    """Return an RGB overlay image for one camera."""
    img = np.ascontiguousarray(frame.rgb.copy())
    h, w = img.shape[:2]

    # Mask contour (mask may be at depth resolution).
    mask: NDArray[np.uint8] = detection.person_mask.astype(np.uint8)
    if mask.shape != (h, w):
        mask = np.asarray(cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST), dtype=np.uint8)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(img, contours, -1, COLOR_MASK, 1)

    # Fused skeleton and colliders projected into this view (drawn first; the
    # per-view observations go on top so their validity colour stays readable).
    if landmarks:
        _draw_segments_2d(img, [(a, b) for a, b, _ in skeleton_edges(landmarks)], calib, COLOR_SKELETON, 1)
    for c in colliders:
        if isinstance(c, DisabledCollider):
            continue
        color = COLOR_COLLIDER.get(c.fit_source.value, (255, 255, 255))
        _draw_segments_2d(img, collider_wireframe(c), calib, color, 1)
        centre = _collider_centre(c)
        uv, front = project_stage_to_rgb(centre[None], calib)
        if front[0] and 0 <= uv[0, 0] < w and 0 <= uv[0, 1] < h:
            cv2.putText(img, c.id, (int(uv[0, 0]) + 3, int(uv[0, 1])), cv2.FONT_HERSHEY_PLAIN, 0.8, color, 1, cv2.LINE_AA)

    # 2D observations with validity (solid, non-antialiased so the colour is exact).
    for prefix, obs in (("", detection.body), ("L.", detection.left_hand), ("R.", detection.right_hand)):
        for o in obs:
            x, y = round(o.xy_px[0]), round(o.xy_px[1])
            if not (0 <= x < w and 0 <= y < h):
                continue
            color = COLOR_VALID if o.valid else COLOR_INVALID
            cv2.circle(img, (x, y), 3, color, -1, cv2.LINE_8)
            if label_landmarks and (prefix == "" or o.name in ("wrist", "index_tip", "thumb_tip")):
                cv2.putText(img, prefix + o.name, (x + 4, y - 3), cv2.FONT_HERSHEY_PLAIN, 0.7, color, 1, cv2.LINE_AA)

    # Sampled-depth locations from the fusion report for this device.
    if per_landmark_report:
        for rec in per_landmark_report.values():
            for d in rec.get("depth", []):
                if d.get("device") == frame.device_id and "pixel_rgb" in d:
                    x, y = round(d["pixel_rgb"][0]), round(d["pixel_rgb"][1])
                    cv2.drawMarker(img, (x, y), COLOR_DEPTH, cv2.MARKER_TILTED_CROSS, 7, 1, cv2.LINE_8)
    return img


def _collider_centre(c: FittedCollider) -> NDArray:
    if isinstance(c, CapsuleCollider):
        return (np.asarray(c.a) + np.asarray(c.b)) / 2.0
    return np.asarray(c.center)  # type: ignore[union-attr]


# --- cloud and wireframe exports ------------------------------------------------


def cloud_source_colors(cloud: ColoredPointCloud) -> NDArray[np.uint8]:
    """RGB per point coloured by source-camera bitset (mixed sources blend)."""
    n = cloud.count
    out = np.zeros((n, 3), np.float64)
    hits = np.zeros(n, np.float64)
    bits = np.asarray(cloud.source_mask, dtype=np.uint16)
    for i in range(len(SOURCE_PALETTE)):
        sel = (bits & (1 << i)) != 0
        out[sel] += np.asarray(SOURCE_PALETTE[i], float)
        hits[sel] += 1.0
    hits[hits == 0] = 1.0
    return np.clip(out / hits[:, None], 0, 255).astype(np.uint8)


def write_ply_points(path: Path, xyz: NDArray, rgb: NDArray) -> None:
    xyz = np.asarray(xyz, np.float32).reshape(-1, 3)
    rgb = np.asarray(rgb, np.uint8).reshape(-1, 3)
    with path.open("w") as f:
        f.write("ply\nformat ascii 1.0\n")
        f.write(f"element vertex {xyz.shape[0]}\n")
        f.write("property float x\nproperty float y\nproperty float z\n")
        f.write("property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")
        for p, c in zip(xyz, rgb, strict=True):
            f.write(f"{p[0]:.4f} {p[1]:.4f} {p[2]:.4f} {int(c[0])} {int(c[1])} {int(c[2])}\n")


def write_ply_edges(path: Path, segments: list[tuple[NDArray, NDArray, tuple[int, int, int]]]) -> None:
    """Edge-list PLY (vertices + edges with per-edge colour)."""
    verts: list[NDArray] = []
    edges: list[tuple[int, int, tuple[int, int, int]]] = []
    for a, b, color in segments:
        verts.append(np.asarray(a, np.float32))
        verts.append(np.asarray(b, np.float32))
        edges.append((len(verts) - 2, len(verts) - 1, color))
    with path.open("w") as f:
        f.write("ply\nformat ascii 1.0\n")
        f.write(f"element vertex {len(verts)}\nproperty float x\nproperty float y\nproperty float z\n")
        f.write(f"element edge {len(edges)}\nproperty int vertex1\nproperty int vertex2\n")
        f.write("property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")
        for v in verts:
            f.write(f"{v[0]:.4f} {v[1]:.4f} {v[2]:.4f}\n")
        for i, j, c in edges:
            f.write(f"{i} {j} {c[0]} {c[1]} {c[2]}\n")


def wireframe_segments(
    landmarks: dict[str, Landmark3D], colliders: tuple[FittedCollider, ...]
) -> list[tuple[NDArray, NDArray, tuple[int, int, int]]]:
    segs = [(a, b, COLOR_SKELETON) for a, b, _ in skeleton_edges(landmarks)]
    for c in colliders:
        color = COLOR_COLLIDER.get(c.fit_source.value, (255, 255, 255))
        segs += [(a, b, color) for a, b in collider_wireframe(c)]
    return segs


# --- JSON report ----------------------------------------------------------------


def _jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_jsonable(v) for v in x]
    if isinstance(x, np.ndarray):
        return _jsonable(x.tolist())
    if isinstance(x, np.generic):
        return _jsonable(x.item())
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if hasattr(x, "value") and not isinstance(x, str | int | bool):
        return x.value
    if hasattr(x, "__dataclass_fields__"):
        return _jsonable({k: getattr(x, k) for k in x.__dataclass_fields__})
    return x


def build_json_report(
    *,
    landmarks: tuple[Landmark3D, ...],
    colliders: tuple[FittedCollider, ...],
    fit_report: Any,
    extra: dict | None = None,
) -> dict:
    """Serializable report: sources, support counts, reprojection errors, dimensions, disabled reasons, warnings."""
    lm_doc = [
        {
            "name": lm.name,
            "valid": lm.valid,
            "source": lm.source,
            "confidence": lm.confidence,
            "visibility": lm.visibility,
            "observed_by": list(lm.observed_by),
            "reprojection_error_px": lm.reprojection_error_px,
            "position_stage_m": lm.position_stage_m,
        }
        for lm in landmarks
    ]
    col_doc = []
    for c in colliders:
        d: dict[str, Any] = {"id": c.id, "body_part": c.body_part.value, "type": c.type.value, "fit_source": c.fit_source.value}
        if isinstance(c, DisabledCollider):
            d["disabled_reason"] = c.reason
        else:
            d["quality"] = c.quality
            if isinstance(c, SphereCollider):
                d.update(center=c.center, radius_m=c.radius)
            elif isinstance(c, CapsuleCollider):
                d.update(a=c.a, b=c.b, radius_m=c.radius)
            elif isinstance(c, ObbCollider):
                d.update(center=c.center, axes=c.axes, half_extents_m=c.half_extents)
        col_doc.append(d)
    doc = {
        "landmarks": lm_doc,
        "colliders": col_doc,
        "fusion": None if getattr(fit_report, "fusion", None) is None else _jsonable(fit_report.fusion),
        "collider_reports": _jsonable(getattr(fit_report, "colliders", {})),
        "coverage": _jsonable(getattr(fit_report, "coverage", {})),
        "subject": _jsonable(getattr(fit_report, "subject", {})),
        "point_count": getattr(fit_report, "point_count", None),
        "warnings": list(getattr(fit_report, "warnings", [])),
    }
    if extra:
        doc.update(_jsonable(extra))
    return _jsonable(doc)


# --- entry point -----------------------------------------------------------------


def write_debug_artifacts(
    out_dir: Path,
    *,
    frames: dict[str, CapturedFrame],
    detections: dict[str, ViewDetection],
    calibrations: dict[str, CameraCalibration],
    cloud: ColoredPointCloud,
    landmarks: tuple[Landmark3D, ...],
    colliders: tuple[FittedCollider, ...],
    fit_report: Any,
    extra: dict | None = None,
) -> dict[str, Path]:
    """Write all debug artifacts for one capture; returns the written paths.

    Never call this from a Sentry hook or an upload path: the images may show a person.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_name = {lm.name: lm for lm in landmarks}
    per_lm = getattr(getattr(fit_report, "fusion", None), "per_landmark", None) or {}
    written: dict[str, Path] = {}
    for device_id, frame in frames.items():
        img = draw_view_overlay(
            frame, detections[device_id], calibrations[device_id],
            per_landmark_report=per_lm, landmarks=by_name, colliders=colliders,
        )
        p = out_dir / f"overlay_{device_id}.png"
        cv2.imwrite(str(p), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
        written[f"overlay_{device_id}"] = p
    p = out_dir / "cloud_by_source.ply"
    write_ply_points(p, cloud.xyz_stage_m, cloud_source_colors(cloud))
    written["cloud"] = p
    p = out_dir / "skeleton_colliders.ply"
    write_ply_edges(p, wireframe_segments(by_name, colliders))
    written["wireframe"] = p
    p = out_dir / "report.json"
    p.write_text(json.dumps(build_json_report(landmarks=landmarks, colliders=colliders, fit_report=fit_report, extra=extra), indent=1))
    written["report"] = p
    return written

