"""Typed pipeline boundaries between Workflow 2 and Workflow 3.

These Protocols let fixtures substitute for hardware and MediaPipe models.
Workflow 3 supplies the real ``ViewDetector`` and ``CharacterFitter``; Workflow
2 owns the ``ViewReconstructor``. Keeping them as protocols means the processing
worker depends on interfaces, not concrete model objects.
"""

from __future__ import annotations

from typing import Protocol

from hmc_backend.contracts.internal import (
    CameraCalibration,
    CapturedFrame,
    ColoredPointCloud,
    FittedCharacter,
    PairedFrames,
    ViewDetection,
)


class ViewDetector(Protocol):
    """Person mask + 2D body/hand observations for a single view (Workflow 3)."""

    def detect_view(self, frame: CapturedFrame) -> ViewDetection: ...


class ViewReconstructor(Protocol):
    """Masked depth -> colored stage-frame point cloud for one view (Workflow 2)."""

    def reconstruct_view(
        self,
        frame: CapturedFrame,
        detection: ViewDetection,
        calibration: CameraCalibration,
    ) -> ColoredPointCloud: ...


class CharacterFitter(Protocol):
    """Registered 3D landmarks + fitted colliders for the pair (Workflow 3)."""

    def fit_character(
        self,
        pair: PairedFrames,
        detections: dict[str, ViewDetection],
        cloud: ColoredPointCloud,
        calibration: CameraCalibration,
    ) -> FittedCharacter: ...
