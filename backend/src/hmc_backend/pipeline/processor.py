"""CharacterProcessor: run the CPU-heavy stages for one paired capture.

``process(pair)`` runs detect -> reconstruct -> fit -> assemble synchronously.
In the live backend this executes on one dedicated worker thread (MediaPipe
objects are created and used only there); the async event loop never calls
OpenCV/MediaPipe directly. Here the stages are injected as protocol objects so a
fake substitutes for hardware.
"""

from __future__ import annotations

import time
from uuid import UUID

from hmc_backend.calibration.model import RigCalibration
from hmc_backend.contracts.internal import CharacterFrame, PairedFrames, TraceContext
from hmc_backend.observability import span
from hmc_backend.observability.quality import QualityMonitor, measure_view_quality
from hmc_backend.pipeline.assembler import FrameAssembler
from hmc_backend.reconstruction.reconstruct import CropBounds, merge_clouds, reconstruct_view
from hmc_backend.vision.protocols import CharacterFitter, ViewDetector


class CharacterProcessor:
    """Owns the vision/reconstruction stages and the frame assembler."""

    def __init__(
        self,
        detector: ViewDetector,
        fitter: CharacterFitter,
        calibration: RigCalibration,
        assembler: FrameAssembler,
        crop: CropBounds,
        *,
        voxel_size_m: float,
        max_points: int,
        confidence_min: int,
        observability_delay_ms: int = 0,
        quality_monitor: QualityMonitor | None = None,
    ) -> None:
        self._detector = detector
        self._fitter = fitter
        self._calibration = calibration
        self._assembler = assembler
        self._crop = crop
        self._voxel_size_m = voxel_size_m
        self._max_points = max_points
        self._confidence_min = confidence_min
        self._observability_delay_ms = max(0, observability_delay_ms)
        self._quality_monitor = quality_monitor or QualityMonitor()

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
                calib = self._calibration.camera(device_id)
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

            # Fit against the front camera's calibration (registration reference).
            front_calib = self._calibration.camera(pair.first.device_id)
            with span("hmc.fit", "landmarks and colliders"):
                fitted = self._fitter.fit_character(pair, detections, merged, front_calib)

            warnings = ("empty_cloud",) if merged.count == 0 else ()
            with span("hmc.assemble", "immutable CharacterFrame"):
                result = self._assembler.assemble(
                    pair,
                    merged,
                    fitted,
                    mode=mode,
                    calibration_id=self._calibration.calibration_id,
                    trace=trace or TraceContext(),
                    extra_warnings=warnings,
                )
            fusion_span.set_data("frame_id", result.frame_id)
            fusion_span.set_data("point_count", result.quality.point_count)

        for quality in view_quality:
            self._quality_monitor.observe(
                frame_id=result.frame_id,
                fusion_id=pair.pair_id,
                calibration_id=self._calibration.calibration_id,
                quality=quality,
            )
        return result


def _seed_from_pair(pair: PairedFrames) -> int:
    """Derive a deterministic subsample seed from the source frame IDs."""
    return (int(pair.first.sequence) << 16) ^ int(pair.second.sequence) ^ _uuid_low(pair.pair_id)


def _uuid_low(u: UUID) -> int:
    return u.int & 0xFFFF_FFFF
