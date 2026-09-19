"""CharacterProcessor: run the CPU-heavy stages for one paired capture.

``process(pair)`` runs detect -> reconstruct -> fit -> assemble synchronously.
In the live backend this executes on one dedicated worker thread (MediaPipe
objects are created and used only there); the async event loop never calls
OpenCV/MediaPipe directly. Here the stages are injected as protocol objects so a
fake substitutes for hardware.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from hmc_backend.calibration.model import RigCalibration
from hmc_backend.contracts.internal import (
    CharacterFrame,
    ColoredPointCloud,
    FittedCharacter,
    PairedFrames,
    TraceContext,
    ViewDetection,
)
from hmc_backend.observability import log_event
from hmc_backend.pipeline.assembler import FrameAssembler
from hmc_backend.reconstruction.reconstruct import CropBounds, merge_clouds, reconstruct_view
from hmc_backend.vision.protocols import CharacterFitter, ViewDetector

# Called on the processing thread after fitting, before assembly. Used for
# debug artifacts; exceptions are swallowed so diagnostics never break publish.
PostFitHook = Callable[[PairedFrames, dict[str, ViewDetection], ColoredPointCloud, FittedCharacter], None]


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
        post_fit_hook: PostFitHook | None = None,
    ) -> None:
        self._detector = detector
        self._fitter = fitter
        self._calibration = calibration
        self._assembler = assembler
        self._crop = crop
        self._voxel_size_m = voxel_size_m
        self._max_points = max_points
        self._confidence_min = confidence_min
        self._post_fit_hook = post_fit_hook

    @property
    def detector(self) -> ViewDetector:
        return self._detector

    @property
    def fitter(self) -> CharacterFitter:
        return self._fitter

    def process(self, pair: PairedFrames, *, mode: str = "snapshot") -> CharacterFrame:
        """Run all stages and return one validated CharacterFrame."""
        frames = {pair.first.device_id: pair.first, pair.second.device_id: pair.second}

        detections = {}
        clouds = []
        source_bit = 1
        for device_id in (pair.first.device_id, pair.second.device_id):
            frame = frames[device_id]
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

        seed = _seed_from_pair(pair)
        merged = merge_clouds(
            clouds, voxel_size_m=self._voxel_size_m, max_points=self._max_points, seed=seed
        )

        # Fit against the front camera's calibration (registration reference).
        front_calib = self._calibration.camera(pair.first.device_id)
        fitted = self._fitter.fit_character(pair, detections, merged, front_calib)

        if self._post_fit_hook is not None:
            try:
                self._post_fit_hook(pair, detections, merged, fitted)
            except Exception as exc:  # noqa: BLE001 - diagnostics must never block publishing
                log_event("warning", "post_fit_hook_failed", pair_id=str(pair.pair_id), error=type(exc).__name__)

        warnings = ()
        if merged.count == 0:
            warnings = ("empty_cloud",)

        return self._assembler.assemble(
            pair,
            merged,
            fitted,
            mode=mode,
            calibration_id=self._calibration.calibration_id,
            trace=TraceContext(),
            extra_warnings=warnings,
        )


def _seed_from_pair(pair: PairedFrames) -> int:
    """Derive a deterministic subsample seed from the source frame IDs."""
    return (int(pair.first.sequence) << 16) ^ int(pair.second.sequence) ^ _uuid_low(pair.pair_id)


def _uuid_low(u: UUID) -> int:
    return u.int & 0xFFFF_FFFF
