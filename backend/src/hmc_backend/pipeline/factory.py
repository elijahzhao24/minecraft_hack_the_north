"""Wire the processing pipeline from a calibration and settings.

Central place that constructs the crop bounds, processor (with the injected
vision stages), assembler, and snapshot store, so both the replayer and the live
capture loop build the pipeline the same way.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from hmc_backend.calibration.model import RigCalibration
from hmc_backend.capture.pairing import Pairer
from hmc_backend.contracts.internal import (
    ColoredPointCloud,
    FittedCharacter,
    PairedFrames,
    ViewDetection,
)
from hmc_backend.observability import log_event
from hmc_backend.pipeline.assembler import FrameAssembler
from hmc_backend.pipeline.processor import CharacterProcessor, PostFitHook
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


def build_detector(settings: Settings) -> ViewDetector:
    if settings.vision_backend == "fake":
        return FakePersonMaskDetector()
    if settings.vision_backend == "mediapipe":
        from hmc_backend.vision.detector import MediaPipeViewDetector
        from hmc_backend.vision.models import resolve_models

        models = resolve_models(
            settings.model_manifest_path,
            models_dir=settings.models_dir,
            pose_override=settings.pose_model_path,
            hand_override=settings.hand_model_path,
        )
        log_event("info", "vision_models_verified", pose_sha256=models.pose_sha256[:12], hand_sha256=models.hand_sha256[:12])
        return MediaPipeViewDetector(models)
    raise ValueError(f"unknown vision_backend {settings.vision_backend!r}")


def build_fitter(settings: Settings, calibration: RigCalibration) -> CharacterFitter:
    if settings.collider_backend == "fake":
        return FakeCharacterFitter()
    if settings.collider_backend == "anatomical":
        from hmc_backend.colliders.fitter import AnatomicalCharacterFitter

        return AnatomicalCharacterFitter(calibration, learn_subject=settings.learn_subject_dimensions)
    raise ValueError(f"unknown collider_backend {settings.collider_backend!r}")


def build_debug_hook(settings: Settings, calibration: RigCalibration, fitter: CharacterFitter) -> PostFitHook | None:
    if not settings.debug_artifacts_dir:
        return None
    from hmc_backend.vision.overlays import write_debug_artifacts

    root = Path(settings.debug_artifacts_dir)

    def hook(pair: PairedFrames, detections: dict[str, ViewDetection], cloud: ColoredPointCloud, fitted: FittedCharacter) -> None:
        frames = {pair.first.device_id: pair.first, pair.second.device_id: pair.second}
        write_debug_artifacts(
            root / str(pair.pair_id), frames=frames, detections=detections,
            calibrations={d: calibration.camera(d) for d in frames}, cloud=cloud,
            landmarks=fitted.landmarks, colliders=getattr(fitter, "last_typed_colliders", ()),
            fit_report=getattr(fitter, "last_report", None),
            extra={"pair_id": str(pair.pair_id), "calibration_id": str(calibration.calibration_id)},
        )
    return hook


def build_processor(
    settings: Settings,
    calibration: RigCalibration,
    *,
    detector: ViewDetector | None = None,
    fitter: CharacterFitter | None = None,
    session_id: UUID | None = None,
) -> CharacterProcessor:
    """Build a processor using the configured production or fixture stages."""
    detector = detector or build_detector(settings)
    fitter = fitter or build_fitter(settings, calibration)
    return CharacterProcessor(
        detector,
        fitter,
        calibration,
        FrameAssembler(session_id or uuid4()),
        crop_from_settings(settings),
        voxel_size_m=settings.voxel_size_m,
        max_points=settings.max_points,
        confidence_min=settings.confidence_min,
        post_fit_hook=build_debug_hook(settings, calibration, fitter),
        reference_device=settings.expected_device_ids[0],
        gravity_align=settings.gravity_align,
        use_frame_intrinsics=settings.use_frame_intrinsics,
    )


def new_snapshot_store() -> SnapshotStore:
    return SnapshotStore()
