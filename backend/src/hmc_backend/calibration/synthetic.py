"""Build a synthetic two-camera rig calibration for fixtures and tests.

Real calibration comes from a ChArUco board (see the workflow spec). For the
fixture-driven vertical slice we construct a rig with two cameras at known
oblique poses looking at the stage origin, so a synthetic scene can be projected
into each camera and reconstructed back with the same transforms.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import numpy as np
from numpy.typing import NDArray

from hmc_backend.calibration.model import RigCalibration
from hmc_backend.contracts.internal import CameraCalibration


def look_at_optical(
    eye: NDArray[np.float64], target: NDArray[np.float64], world_up: NDArray[np.float64]
) -> NDArray[np.float64]:
    """Return ``T_stage_from_optical`` for a camera at ``eye`` looking at ``target``.

    Optical axes: +Z forward (toward target), +X image-right, +Y image-down.
    A point at the optical origin maps to ``eye`` in stage coordinates.
    """
    z = target - eye
    z = z / np.linalg.norm(z)
    # image-right = forward x up  (so that image-down = forward x right points
    # against world_up and the raster is upright).
    x = np.cross(z, world_up)
    nx = np.linalg.norm(x)
    if nx < 1e-8:
        # Degenerate: view parallel to up; pick an arbitrary right axis.
        x = np.array([1.0, 0.0, 0.0])
    else:
        x = x / nx
    y = np.cross(z, x)  # image-down

    r = np.column_stack([x, y, z])  # optical axes in stage frame
    t = np.eye(4)
    t[:3, :3] = r
    t[:3, 3] = eye
    return t


def intrinsics(width: int, height: int, fov_x_deg: float = 60.0) -> NDArray[np.float64]:
    """A simple pinhole intrinsic matrix for a given raster and horizontal FOV."""
    fx = (width / 2.0) / np.tan(np.radians(fov_x_deg) / 2.0)
    fy = fx  # square pixels
    cx = width / 2.0
    cy = height / 2.0
    return np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], dtype=np.float64)


def build_synthetic_rig(
    device_ids: tuple[str, str] = ("front-phone", "side-phone"),
    *,
    rgb_size: tuple[int, int] = (320, 240),
    depth_size: tuple[int, int] = (320, 240),
    distance_m: float = 2.5,
    height_m: float = 1.1,
    calibration_id: UUID | None = None,
) -> RigCalibration:
    """Two cameras at oblique views of the stage origin (front and side)."""
    calibration_id = calibration_id or uuid4()
    created = datetime.now(UTC)
    target = np.array([0.0, height_m, 0.0])
    up = np.array([0.0, 1.0, 0.0])

    # Front camera on +Z axis; side camera offset ~40 degrees around +Y.
    angle = np.radians(40.0)
    eyes = {
        device_ids[0]: np.array([0.0, height_m, distance_m]),
        device_ids[1]: np.array([distance_m * np.sin(angle), height_m, distance_m * np.cos(angle)]),
    }

    k = intrinsics(*depth_size)
    cameras: dict[str, CameraCalibration] = {}
    for dev, eye in eyes.items():
        cameras[dev] = CameraCalibration(
            calibration_id=calibration_id,
            device_id=dev,
            rgb_size=rgb_size,
            depth_size=depth_size,
            K_rgb=k.copy(),
            T_stage_from_optical=look_at_optical(eye, target, up),
            reprojection_error_px=1.0,
            created_at_utc=created,
        )

    return RigCalibration(
        calibration_id=calibration_id,
        created_at_utc=created,
        stage_definition={
            "unit": "meter",
            "x": "front_camera_image_right",
            "y": "up",
            "z": "toward_front_camera",
        },
        board=None,
        cameras=cameras,
        validation={"synthetic": True},
    )
