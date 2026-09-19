"""CharacterProcessor: run the CPU-heavy stages for one capture group.

``process(group)`` runs detect -> reconstruct -> fit -> assemble synchronously.
In the live backend this executes on one dedicated worker thread (MediaPipe
objects are created and used only there); the async event loop never calls
OpenCV/MediaPipe directly. Here the stages are injected as protocol objects so a
fake substitutes for hardware.
"""

from __future__ import annotations

from uuid import UUID

from hmc_backend.calibration.model import RigCalibration
from hmc_backend.contracts.internal import CaptureGroup, CharacterFrame, TraceContext
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
        live_voxel_size_m: float | None = None,
        live_max_points: int | None = None,
    ) -> None:
        self._detector = detector
        self._fitter = fitter
        self._calibration = calibration
        self._assembler = assembler
        self._crop = crop
        self._voxel_size_m = voxel_size_m
        self._max_points = max_points
        self._confidence_min = confidence_min
        self._live_voxel_size_m = live_voxel_size_m or voxel_size_m
        self._live_max_points = live_max_points or max_points

    def process(self, group: CaptureGroup, *, mode: str = "snapshot") -> CharacterFrame:
        """Run all stages and return one validated CharacterFrame."""
        detections = {}
        clouds = []
        source_bit = 1
        for frame in group.frames:
            device_id = frame.device_id
            calib = self._calibration.camera(device_id)
            detection = self._detector.detect_view(frame)
            detections[device_id] = detection
            cloud = reconstruct_view(
                frame,
                detection,
                calib,
                self._crop,
                confidence_min=self._confidence_min,
                source_bit=source_bit,
            )
            clouds.append(cloud)
            source_bit <<= 1

        seed = _seed_from_group(group)
        is_live = mode == "live"
        merged = merge_clouds(
            clouds,
            voxel_size_m=self._live_voxel_size_m if is_live else self._voxel_size_m,
            max_points=self._live_max_points if is_live else self._max_points,
            seed=seed,
        )

        # Fit against the front camera's calibration (registration reference).
        front_calib = self._calibration.camera(group.first.device_id)
        fitted = self._fitter.fit_character(group, detections, merged, front_calib)

        warnings: tuple[str, ...] = ()
        if merged.count == 0:
            warnings = ("empty_cloud",)
        if is_live and not fitted.landmarks and not fitted.colliders:
            warnings += ("landmarks_and_colliders_not_implemented",)
        if len(group.frames) == 1:
            warnings += ("single_view",)

        return self._assembler.assemble(
            group,
            merged,
            fitted,
            mode=mode,
            calibration_id=self._calibration.calibration_id,
            trace=TraceContext(),
            extra_warnings=warnings,
        )

    def close(self) -> None:
        close = getattr(self._detector, "close", None)
        if close is not None:
            close()


def _seed_from_group(group: CaptureGroup) -> int:
    """Derive a deterministic subsample seed from the source frame IDs."""
    seed = _uuid_low(group.group_id)
    for index, frame in enumerate(group.frames):
        seed ^= int(frame.sequence) << (index * 8)
    return seed


def _uuid_low(u: UUID) -> int:
    return u.int & 0xFFFF_FFFF
