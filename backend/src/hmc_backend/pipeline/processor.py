"""CharacterProcessor: run the CPU-heavy stages for one paired capture.

``process(pair)`` runs detect -> reconstruct -> fit -> assemble synchronously.
In the live backend this executes on one dedicated worker thread (MediaPipe
objects are created and used only there); the async event loop never calls
OpenCV/MediaPipe directly. Here the stages are injected as protocol objects so a
fake substitutes for hardware.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from uuid import UUID

from hmc_backend.calibration.frame_tree import RigFrameTree
from hmc_backend.calibration.model import RigCalibration
from hmc_backend.contracts.internal import (
    CharacterFrame,
    ColoredPointCloud,
    FittedCharacter,
    PairedFrames,
    TraceContext,
    ViewDetection,
)
from hmc_backend.observability import log_event, span
from hmc_backend.observability.quality import QualityMonitor, measure_view_quality
from hmc_backend.pipeline.assembler import FrameAssembler
from hmc_backend.reconstruction.reconstruct import CropBounds, merge_clouds, reconstruct_view
from hmc_backend.reconstruction.registration import gravity_aligned
from hmc_backend.transforms import TransformError, optical_frame
from hmc_backend.vision.protocols import CharacterFitter, ViewDetector

PostFitHook = Callable[
    [PairedFrames, dict[str, ViewDetection], ColoredPointCloud, FittedCharacter], None
]


class CharacterProcessor:
    """Owns the vision/reconstruction stages and the frame assembler."""

    def __init__(
        self,
        detector: ViewDetector,
        fitter: CharacterFitter,
        frame_tree: RigFrameTree | RigCalibration,
        assembler: FrameAssembler,
        crop: CropBounds,
        *,
        voxel_size_m: float,
        max_points: int,
        confidence_min: int,
        observability_delay_ms: int = 0,
        quality_monitor: QualityMonitor | None = None,
        post_fit_hook: PostFitHook | None = None,
        gravity_align: bool = False,
        use_frame_intrinsics: bool = False,
    ) -> None:
        self._detector = detector
        self._fitter = fitter
        self._frame_tree = (
            frame_tree
            if isinstance(frame_tree, RigFrameTree)
            else RigFrameTree.from_legacy_rig(frame_tree)
        )
        self._assembler = assembler
        self._crop = crop
        self._voxel_size_m = voxel_size_m
        self._max_points = max_points
        self._confidence_min = confidence_min
        self._observability_delay_ms = max(0, observability_delay_ms)
        self._quality_monitor = quality_monitor or QualityMonitor()
        self._post_fit_hook = post_fit_hook
        self._gravity_align = gravity_align
        self._use_frame_intrinsics = use_frame_intrinsics

    @property
    def frame_tree(self) -> RigFrameTree:
        return self._frame_tree

    @property
    def calibration(self):
        return self._frame_tree.rig

    def _effective_calibration(self, device_id: str, frame):
        calib = self._frame_tree.rig.camera(device_id)
        if self._gravity_align:
            calib = gravity_aligned(calib, frame)
        try:
            t_stage = self._frame_tree.transforms.lookup_transform(
                "stage",
                optical_frame(device_id),
                frame.normalized_capture_time_s,
            )
        except TransformError as exc:
            raise ValueError(f"transform lookup failed for {device_id!r}: {exc}") from exc
        return replace(calib, T_stage_from_optical=t_stage)

    @property
    def detector(self) -> ViewDetector:
        return self._detector

    @property
    def fitter(self) -> CharacterFitter:
        return self._fitter

    def process(
        self,
        pair: PairedFrames,
        *,
        mode: str = "snapshot",
        trace: TraceContext | None = None,
    ) -> CharacterFrame:
        """Run all stages and return one validated CharacterFrame."""
        frames = {pair.first.device_id: pair.first, pair.second.device_id: pair.second}

        detections = {}
        clouds = []
        source_bit = 1
        view_quality = []
        source_ids = ",".join(
            str(frame.source_frame_id or frame.capture_id) for frame in (pair.first, pair.second)
        )
        with span(
            "hmc.calibration_fusion",
            "detect, calibrate, reconstruct, fuse and fit",
            fusion_id=str(pair.pair_id),
            source_frame_ids=source_ids,
        ) as fusion_span:
            if self._observability_delay_ms:
                # Explicit, opt-in demo hook. Never enabled by default.
                time.sleep(self._observability_delay_ms / 1000)

            for device_id in (pair.first.device_id, pair.second.device_id):
                frame = frames[device_id]
                calib = self._effective_calibration(device_id, frame)
                with span("hmc.detect", "existing person mask + landmarks", camera_id=device_id):
                    detection = self._detector.detect_view(frame)
                detections[device_id] = detection
                view_quality.append(
                    measure_view_quality(
                        frame, detection, confidence_min=self._confidence_min
                    )
                )
                with span("hmc.reconstruct", "masked depth -> calibrated points", camera_id=device_id) as reconstruct_span:
                    cloud = reconstruct_view(
                        frame,
                        detection,
                        calib,
                        self._crop,
                        confidence_min=self._confidence_min,
                        source_bit=source_bit,
                        use_frame_intrinsics=self._use_frame_intrinsics,
                    )
                    reconstruct_span.set_data("point_count", cloud.count)
                clouds.append(cloud)
                source_bit <<= 1

            seed = _seed_from_pair(pair)
            with span("hmc.fuse", "merge, voxel downsample and cap") as merge_span:
                merged = merge_clouds(
                    clouds, voxel_size_m=self._voxel_size_m, max_points=self._max_points, seed=seed
                )
                merge_span.set_data("point_count", merged.count)

            front_calib = self._effective_calibration(pair.first.device_id, pair.first)
            with span("hmc.fit", "landmarks and colliders"):
                fitted = self._fitter.fit_character(pair, detections, merged, front_calib)

            if self._post_fit_hook is not None:
                try:
                    self._post_fit_hook(pair, detections, merged, fitted)
                except Exception as exc:  # noqa: BLE001 - diagnostics must never block publishing
                    log_event(
                        "warning",
                        "post_fit_hook_failed",
                        pair_id=str(pair.pair_id),
                        error=type(exc).__name__,
                    )

            warnings = ("empty_cloud",) if merged.count == 0 else ()
            with span("hmc.assemble", "immutable CharacterFrame"):
                result = self._assembler.assemble(
                    pair,
                    merged,
                    fitted,
                    mode=mode,
                    calibration_id=self._frame_tree.rig.calibration_id,
                    trace=trace or TraceContext(),
                    extra_warnings=warnings,
                )
            fusion_span.set_data("frame_id", result.frame_id)
            fusion_span.set_data("point_count", result.quality.point_count)

        for quality in view_quality:
            self._quality_monitor.observe(
                frame_id=result.frame_id,
                fusion_id=pair.pair_id,
                calibration_id=self._frame_tree.rig.calibration_id,
                quality=quality,
            )
        return result


def _seed_from_pair(pair: PairedFrames) -> int:
    """Derive a deterministic subsample seed from the source frame IDs."""
    return (int(pair.first.sequence) << 16) ^ int(pair.second.sequence) ^ _uuid_low(pair.pair_id)


def _uuid_low(u: UUID) -> int:
    return u.int & 0xFFFF_FFFF
