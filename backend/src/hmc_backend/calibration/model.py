"""Rig calibration file model, loader, and validation.

The calibration file is the immutable source of camera-to-stage geometry. It is
loaded once at startup and its ``calibration_id`` is stamped on every pair and
published frame so a consumer can never join a cloud to a mismatched
calibration. Moving a phone/tripod or changing orientation/resolution requires a
new file with a new ``calibration_id``.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from hmc_backend.contracts.internal import CameraCalibration

RIG_SCHEMA = "hmc.rig_calibration"
RIG_SCHEMA_VERSION = 1


class CalibrationError(ValueError):
    """Raised when a calibration file is missing or invalid."""


@dataclass(frozen=True, slots=True)
class RigCalibration:
    """A whole-rig calibration: shared IDs plus per-device camera calibrations."""

    calibration_id: UUID
    created_at_utc: datetime
    stage_definition: dict
    board: dict | None
    cameras: dict[str, CameraCalibration]
    validation: dict

    def camera(self, device_id: str) -> CameraCalibration:
        try:
            return self.cameras[device_id]
        except KeyError as exc:
            raise CalibrationError(f"no calibration for device {device_id!r}") from exc

    def device_ids(self) -> tuple[str, ...]:
        return tuple(self.cameras.keys())


def _matrix(values: object, n: int, label: str) -> NDArray[np.float64]:
    if not isinstance(values, list) or len(values) != n:
        raise CalibrationError(f"{label} must be a length-{n} row-major array")
    arr = np.array(values, dtype=np.float64)
    if not np.isfinite(arr).all():
        raise CalibrationError(f"{label} contains non-finite values")
    side = round(n**0.5)
    return arr.reshape(side, side)


def _parse_camera(entry: dict, calibration_id: UUID, created_at: datetime) -> CameraCalibration:
    required = {
        "device_id",
        "image_orientation",
        "rgb",
        "depth",
        "K_rgb_row_major",
        "T_stage_from_optical_row_major",
        "reprojection_error_px",
    }
    missing = required - set(entry)
    if missing:
        raise CalibrationError(f"camera entry missing fields: {sorted(missing)}")

    rgb = entry["rgb"]
    depth = entry["depth"]
    k = _matrix(entry["K_rgb_row_major"], 9, "K_rgb")
    t = _matrix(entry["T_stage_from_optical_row_major"], 16, "T_stage_from_optical")
    if not valid_rigid_transform(t):
        raise CalibrationError("T_stage_from_optical must be a proper rigid transform")
    if k[0, 0] <= 0 or k[1, 1] <= 0:
        raise CalibrationError("focal lengths must be positive")

    return CameraCalibration(
        calibration_id=calibration_id,
        device_id=str(entry["device_id"]),
        rgb_size=(int(rgb["width"]), int(rgb["height"])),
        depth_size=(int(depth["width"]), int(depth["height"])),
        K_rgb=k,
        T_stage_from_optical=t,
        reprojection_error_px=float(entry["reprojection_error_px"]),
        created_at_utc=created_at,
    )


def parse_rig_calibration(raw: dict) -> RigCalibration:
    """Validate a decoded calibration dict into a :class:`RigCalibration`."""
    if raw.get("schema") != RIG_SCHEMA:
        raise CalibrationError(f"unexpected schema {raw.get('schema')!r}")
    if raw.get("schema_version") != RIG_SCHEMA_VERSION:
        raise CalibrationError(f"unsupported schema_version {raw.get('schema_version')!r}")

    try:
        calibration_id = UUID(str(raw["calibration_id"]))
        created_at = datetime.fromisoformat(str(raw["created_at_utc"]))
    except (KeyError, ValueError) as exc:
        raise CalibrationError("invalid calibration_id or created_at_utc") from exc

    cameras_raw = raw.get("cameras")
    if not isinstance(cameras_raw, list) or not cameras_raw:
        raise CalibrationError("calibration must list at least one camera")

    cameras: dict[str, CameraCalibration] = {}
    for entry in cameras_raw:
        cam = _parse_camera(entry, calibration_id, created_at)
        if cam.device_id in cameras:
            raise CalibrationError(f"duplicate camera device_id {cam.device_id!r}")
        cameras[cam.device_id] = cam

    return RigCalibration(
        calibration_id=calibration_id,
        created_at_utc=created_at,
        stage_definition=raw.get("stage_definition", {}),
        board=raw.get("board"),
        cameras=cameras,
        validation=raw.get("validation", {}),
    )


def load_rig_calibration(path: str | Path) -> RigCalibration:
    """Load and validate a calibration file from disk."""
    p = Path(path)
    if not p.exists():
        raise CalibrationError(f"calibration file not found: {p}")
    try:
        raw = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise CalibrationError(f"calibration file is not valid JSON: {p}") from exc
    return parse_rig_calibration(raw)


def rig_to_json(rig: RigCalibration) -> dict:
    """Serialize a rig calibration back to its file form (round-trippable)."""
    cameras = []
    for cam in rig.cameras.values():
        cameras.append(
            {
                "device_id": cam.device_id,
                "image_orientation": rig.validation.get(cam.device_id, {}).get("baseline", {}).get("image_orientation", "landscape_right"),
                "rgb": {"width": cam.rgb_size[0], "height": cam.rgb_size[1]},
                "depth": {"width": cam.depth_size[0], "height": cam.depth_size[1]},
                "K_rgb_row_major": cam.K_rgb.reshape(-1).tolist(),
                "T_stage_from_optical_row_major": cam.T_stage_from_optical.reshape(-1).tolist(),
                "reprojection_error_px": cam.reprojection_error_px,
            }
        )
    return {
        "schema": RIG_SCHEMA,
        "schema_version": RIG_SCHEMA_VERSION,
        "calibration_id": str(rig.calibration_id),
        "created_at_utc": rig.created_at_utc.isoformat(),
        "stage_definition": rig.stage_definition,
        "board": rig.board,
        "cameras": cameras,
        "validation": rig.validation,
    }


def save_rig_calibration(rig: RigCalibration, path: str | Path) -> None:
    """Write a rig calibration to disk as JSON."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = rig_to_json(rig)
    parse_rig_calibration(raw)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=p.parent, delete=False) as f:
            name = f.name
            json.dump(raw, f, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, p)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def valid_rigid_transform(t: NDArray) -> bool:
    return bool(t.shape == (4, 4) and np.isfinite(t).all()
                and np.allclose(t[3], [0, 0, 0, 1], atol=1e-6)
                and np.allclose(t[:3, :3].T @ t[:3, :3], np.eye(3), atol=1e-3)
                and abs(np.linalg.det(t[:3, :3]) - 1) < 1e-3)
