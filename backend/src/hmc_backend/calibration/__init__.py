"""ChArUco camera-to-stage calibration and calibration file IO."""

from hmc_backend.calibration.model import (
    CalibrationError,
    RigCalibration,
    load_rig_calibration,
    parse_rig_calibration,
    rig_to_json,
    save_rig_calibration,
)
from hmc_backend.calibration.synthetic import (
    build_synthetic_rig,
    intrinsics,
    look_at_optical,
)

__all__ = [
    "CalibrationError",
    "RigCalibration",
    "build_synthetic_rig",
    "intrinsics",
    "load_rig_calibration",
    "look_at_optical",
    "parse_rig_calibration",
    "rig_to_json",
    "save_rig_calibration",
]
