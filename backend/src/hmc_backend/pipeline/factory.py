"""Wire the processing pipeline from a calibration and settings.

Central place that constructs the crop bounds, processor (with the injected
vision stages), assembler, and snapshot store, so both the replayer and the live
capture loop build the pipeline the same way.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from hmc_backend.calibration.model import RigCalibration
from hmc_backend.capture.pairing import Pairer
from hmc_backend.pipeline.assembler import FrameAssembler
from hmc_backend.pipeline.processor import CharacterProcessor
from hmc_backend.pipeline.snapshot_store import SnapshotStore
from hmc_backend.reconstruction.reconstruct import CropBounds
from hmc_backend.settings import Settings
from hmc_backend.vision.fake import FakeCharacterFitter, FakePersonMaskDetector
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
        tuple(settings.expected_device_ids),  # type: ignore[arg-type]
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
    return CharacterProcessor(
        detector or FakePersonMaskDetector(),
        fitter or FakeCharacterFitter(),
        calibration,
        FrameAssembler(session_id or uuid4()),
        crop_from_settings(settings),
        voxel_size_m=settings.voxel_size_m,
        max_points=settings.max_points,
        confidence_min=settings.confidence_min,
        observability_delay_ms=settings.observability_demo_delay_ms,
    )


def new_snapshot_store() -> SnapshotStore:
    return SnapshotStore()
