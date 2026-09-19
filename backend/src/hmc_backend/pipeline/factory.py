"""Wire the processing pipeline from a calibration and settings.

Central place that constructs the crop bounds, processor (with the injected
vision stages), assembler, and snapshot store, so both the replayer and the live
capture loop build the pipeline the same way.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

from hmc_backend.calibration.model import RigCalibration
from hmc_backend.capture.pairing import Pairer
from hmc_backend.pipeline.assembler import FrameAssembler
from hmc_backend.pipeline.processor import CharacterProcessor
from hmc_backend.pipeline.snapshot_store import SnapshotStore
from hmc_backend.reconstruction.reconstruct import CropBounds
from hmc_backend.settings import Settings
from hmc_backend.vision.fake import FakeCharacterFitter, FakePersonMaskDetector
from hmc_backend.vision.mediapipe_pose import EmptyCharacterFitter, MediaPipePoseMaskDetector
from hmc_backend.vision.protocols import CharacterFitter, ViewDetector


def crop_from_settings(settings: Settings) -> CropBounds:
    return CropBounds(
        min_x=settings.stage_min_x_m,
        max_x=settings.stage_max_x_m,
        min_y=settings.stage_min_y_m,
        max_y=settings.stage_max_y_m,
        min_z=settings.stage_min_z_m,
        max_z=settings.stage_max_z_m,
        depth_min=settings.depth_min_m,
        depth_max=settings.depth_max_m,
    )


def build_pairer(settings: Settings, *, require_capture_id: bool = True) -> Pairer:
    return Pairer(
        tuple(settings.expected_device_ids),
        pair_skew_limit_ms=settings.pair_skew_limit_ms,
        clock_uncertainty_limit_ms=settings.clock_uncertainty_limit_ms,
        require_capture_id=require_capture_id,
    )


def build_processor(
    settings: Settings,
    calibration: RigCalibration,
    *,
    detector: ViewDetector | None = None,
    fitter: CharacterFitter | None = None,
    session_id: UUID | None = None,
) -> CharacterProcessor:
    """Build a processor. Defaults to the fake vision stages for fixtures."""
    if detector is None and settings.pose_model_path:
        manifest = json.loads(Path(settings.model_manifest_path).read_text())
        pose_manifest = manifest["models"]["pose"]
        detector = MediaPipePoseMaskDetector(
            settings.pose_model_path,
            tuple(settings.expected_device_ids),
            threshold=settings.person_mask_threshold,
            expected_sha256=pose_manifest["sha256"],
        )
        fitter = fitter or EmptyCharacterFitter()
    elif detector is None:
        if settings.require_real_vision:
            raise ValueError("HMC_POSE_MODEL_PATH is required when real vision is enabled")
        detector = FakePersonMaskDetector()
    return CharacterProcessor(
        detector,
        fitter or FakeCharacterFitter(),
        calibration,
        FrameAssembler(session_id or uuid4()),
        crop_from_settings(settings),
        voxel_size_m=settings.voxel_size_m,
        max_points=settings.max_points,
        confidence_min=settings.confidence_min,
        live_voxel_size_m=settings.live_voxel_size_m,
        live_max_points=settings.live_max_points,
    )


def new_snapshot_store() -> SnapshotStore:
    return SnapshotStore()
