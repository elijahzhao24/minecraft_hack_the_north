"""Build a session-local single-camera calibration from an incoming ARKit frame."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import numpy as np

from hmc_backend.calibration.model import RigCalibration
from hmc_backend.capture.rgbd_ingest import DecodedRgbd
from hmc_backend.contracts.internal import CameraCalibration


def build_provisional_rig(decoded: DecodedRgbd) -> RigCalibration:
    """Use packet intrinsics and a camera-relative temporary stage frame.

    ARKit camera coordinates use +Y up and look along -Z, while the optical
    frame used by reconstruction has +Y down and +Z forward. The diagonal
    conversion maps between them. The provisional camera is placed at a typical
    standing-phone height and distance so points land inside the configured
    stage crop. This is deliberately session-local and assumes a stationary
    phone; it is not a substitute for real multi-camera calibration.
    """
    calibration_id = uuid4()
    created_at = datetime.now(UTC)
    stage_from_optical = np.diag([1.0, -1.0, -1.0, 1.0])
    stage_from_optical[:3, 3] = (0.0, 1.2, 2.5)
    header = decoded.header
    camera = CameraCalibration(
        calibration_id=calibration_id,
        device_id=header.device_id,
        rgb_size=(header.rgb.width, header.rgb.height),
        depth_size=(header.depth.width, header.depth.height),
        K_rgb=decoded.k_rgb.copy(),
        T_stage_from_optical=stage_from_optical,
        reprojection_error_px=0.0,
        created_at_utc=created_at,
    )
    return RigCalibration(
        calibration_id=calibration_id,
        created_at_utc=created_at,
        stage_definition={
            "unit": "meter",
            "origin": "provisional_camera_relative_stage",
            "x": "camera_image_right",
            "y": "up",
            "z": "toward_camera",
            "provisional": True,
        },
        board=None,
        cameras={header.device_id: camera},
        validation={"provisional_single_view": True},
    )
