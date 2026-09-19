"""Build a synthetic person and project it into calibrated camera views.

This produces *real* HMC1 RGBD packets (JPEG RGB, float32 depth, uint8
confidence) from a known 3D scene, so the whole backend spine can be exercised
without hardware. Because the same rig calibration is used to both project and
reconstruct, the recovered cloud should match the synthetic person.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import cv2
import numpy as np
from numpy.typing import NDArray

from hmc_backend.contracts.internal import CameraCalibration
from hmc_backend.protocol.envelope import MessageType, encode_envelope


def _cylinder(a, b, radius, color, n, rng) -> tuple[np.ndarray, np.ndarray]:
    """Sample points on a capsule-ish cylinder between a and b."""
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    axis = b - a
    length = np.linalg.norm(axis)
    axis = axis / length
    # Build an orthonormal basis around the axis.
    tmp = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(axis, tmp)
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    t = rng.random(n)
    theta = rng.random(n) * 2 * np.pi
    centers = a[None, :] + np.outer(t, axis * length)
    ring = radius * (np.cos(theta)[:, None] * u[None, :] + np.sin(theta)[:, None] * v[None, :])
    pts = centers + ring
    cols = np.tile(np.asarray(color, np.uint8), (n, 1))
    return pts.astype(np.float32), cols


def _sphere(center, radius, color, n, rng) -> tuple[np.ndarray, np.ndarray]:
    center = np.asarray(center, float)
    dirs = rng.normal(size=(n, 3))
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    pts = center[None, :] + radius * dirs
    cols = np.tile(np.asarray(color, np.uint8), (n, 1))
    return pts.astype(np.float32), cols


def make_person_points(seed: int = 0) -> tuple[NDArray[np.float32], NDArray[np.uint8]]:
    """A simple standing humanoid of colored primitives in the stage frame."""
    rng = np.random.default_rng(seed)
    parts = [
        _sphere((0.0, 1.65, 0.0), 0.12, (240, 200, 170), 1500, rng),          # head
        _cylinder((0.0, 0.95, 0.0), (0.0, 1.5, 0.0), 0.16, (60, 120, 200), 5000, rng),  # torso
        _cylinder((-0.18, 1.45, 0.0), (-0.45, 1.1, 0.05), 0.05, (60, 120, 200), 1500, rng),  # L upper arm
        _cylinder((-0.45, 1.1, 0.05), (-0.6, 0.8, 0.1), 0.045, (240, 200, 170), 1500, rng),  # L forearm
        _cylinder((0.18, 1.45, 0.0), (0.45, 1.1, 0.05), 0.05, (60, 120, 200), 1500, rng),    # R upper arm
        _cylinder((0.45, 1.1, 0.05), (0.6, 0.8, 0.1), 0.045, (240, 200, 170), 1500, rng),    # R forearm
        _cylinder((-0.08, 0.95, 0.0), (-0.1, 0.5, 0.0), 0.07, (40, 40, 60), 2000, rng),      # L thigh
        _cylinder((-0.1, 0.5, 0.0), (-0.1, 0.05, 0.03), 0.055, (40, 40, 60), 2000, rng),     # L shin
        _cylinder((0.08, 0.95, 0.0), (0.1, 0.5, 0.0), 0.07, (40, 40, 60), 2000, rng),        # R thigh
        _cylinder((0.1, 0.5, 0.0), (0.1, 0.05, 0.03), 0.055, (40, 40, 60), 2000, rng),       # R shin
    ]
    xyz = np.concatenate([p[0] for p in parts], axis=0)
    rgb = np.concatenate([p[1] for p in parts], axis=0)
    return xyz, rgb


def project_to_view(
    xyz_stage: NDArray[np.float32],
    rgb: NDArray[np.uint8],
    calib: CameraCalibration,
    *,
    splat: int = 1,
) -> tuple[NDArray[np.uint8], NDArray[np.float32], NDArray[np.uint8]]:
    """Z-buffer project stage points into one camera's RGB/depth/confidence."""
    w, h = calib.depth_size
    r = calib.T_stage_from_optical[:3, :3]
    eye = calib.T_stage_from_optical[:3, 3]
    # Stage -> optical: p_opt = R^T (p_stage - eye).
    opt = (xyz_stage.astype(np.float64) - eye) @ r
    z = opt[:, 2]
    front = z > 1e-3
    opt = opt[front]
    z = z[front]
    cols = rgb[front]

    k = calib.K_rgb  # depth_size == rgb_size for synthetic rig
    u = (k[0, 0] * opt[:, 0] / z + k[0, 2]).round().astype(np.int64)
    v = (k[1, 1] * opt[:, 1] / z + k[1, 2]).round().astype(np.int64)

    depth = np.zeros((h, w), np.float32)
    color = np.zeros((h, w, 3), np.uint8)
    zbuf = np.full((h, w), np.inf)

    # Painter's order: nearest last so it wins the z-buffer via explicit test.
    order = np.argsort(-z)
    for i in order:
        for dv in range(-splat + 1, splat):
            for du in range(-splat + 1, splat):
                uu, vv = u[i] + du, v[i] + dv
                if 0 <= uu < w and 0 <= vv < h and z[i] < zbuf[vv, uu]:
                    zbuf[vv, uu] = z[i]
                    depth[vv, uu] = np.float32(z[i])
                    color[vv, uu] = cols[i]

    confidence = np.where(depth > 0.0, 2, 0).astype(np.uint8)
    return color, depth, confidence


def encode_rgbd_packet(
    *,
    device_id: str,
    session_id: UUID,
    capture_id: UUID,
    sequence: int,
    capture_timestamp_s: float,
    calib: CameraCalibration,
    rgb: NDArray[np.uint8],
    depth: NDArray[np.float32],
    confidence: NDArray[np.uint8],
) -> bytes:
    """Encode arrays into a real HMC1 RGBD packet."""
    h_rgb, w_rgb = rgb.shape[:2]
    h_d, w_d = depth.shape
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    ok, jpeg = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    if not ok:
        raise RuntimeError("failed to JPEG-encode synthetic RGB")
    jpeg_bytes = jpeg.tobytes()
    depth_bytes = np.ascontiguousarray(depth, "<f4").tobytes()
    conf_bytes = np.ascontiguousarray(confidence, np.uint8).tobytes()

    off_depth = len(jpeg_bytes)
    off_conf = off_depth + len(depth_bytes)
    header = {
        "schema": "hmc.rgbd_frame",
        "schema_version": 1,
        "device_id": device_id,
        "session_id": str(session_id),
        "capture_id": str(capture_id),
        "sequence": sequence,
        "capture_timestamp_s": capture_timestamp_s,
        "image_orientation": "landscape_right",
        "tracking_state": "normal",
        "rgb": {
            "width": w_rgb,
            "height": h_rgb,
            "intrinsics_row_major": calib.K_rgb.reshape(-1).tolist(),
        },
        "depth": {"width": w_d, "height": h_d, "unit": "meter", "confidence_encoding": "arkit_0_1_2"},
        "T_arkit_world_from_camera_row_major": np.eye(4).reshape(-1).tolist(),
        "buffers": [
            {"name": "rgb", "encoding": "jpeg", "offset": 0, "length": len(jpeg_bytes)},
            {"name": "depth", "encoding": "float32_le", "offset": off_depth, "length": len(depth_bytes), "shape": [h_d, w_d]},
            {"name": "confidence", "encoding": "uint8", "offset": off_conf, "length": len(conf_bytes), "shape": [h_d, w_d]},
        ],
    }
    payload = jpeg_bytes + depth_bytes + conf_bytes
    return encode_envelope(MessageType.RGBD_FRAME, header, payload)


def build_capture_packets(
    rig,
    *,
    capture_id: UUID | None = None,
    seed: int = 0,
    splat: int = 2,
) -> dict[str, bytes]:
    """Render the synthetic person into every rig camera as HMC1 packets."""
    capture_id = capture_id or uuid4()
    xyz, rgb = make_person_points(seed)
    packets: dict[str, bytes] = {}
    for i, (device_id, calib) in enumerate(rig.cameras.items()):
        color, depth, conf = project_to_view(xyz, rgb, calib, splat=splat)
        packets[device_id] = encode_rgbd_packet(
            device_id=device_id,
            session_id=uuid4(),
            capture_id=capture_id,
            sequence=100 + i,
            capture_timestamp_s=1000.0 + i * 0.01,
            calib=calib,
            rgb=color,
            depth=depth,
            confidence=conf,
        )
    return packets
