"""Internal typed in-process data structures.

These are trusted values that carry bulk NumPy data between pipeline stages.
They are frozen ``dataclass(slots=True)`` objects, not extra network formats.
Array invariants (C-contiguity, dtype, rank, equal counts, finiteness) are
checked at the module boundaries that construct them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True, slots=True)
class CapturedFrame:
    """One decoded, clock-normalized RGBD frame from a single device."""

    device_id: str
    session_id: UUID
    capture_id: UUID
    sequence: int
    capture_timestamp_s: float
    normalized_capture_time_s: float
    clock_uncertainty_ms: float
    rgb: NDArray[np.uint8]  # H_rgb x W_rgb x 3, RGB order
    depth_m: NDArray[np.float32]  # H_depth x W_depth
    confidence: NDArray[np.uint8]  # H_depth x W_depth
    K_rgb: NDArray[np.float64]  # 3 x 3
    arkit_pose: NDArray[np.float64]  # 4 x 4, diagnostic only


@dataclass(frozen=True, slots=True)
class CameraCalibration:
    """Per-camera calibration into the shared stage frame."""

    calibration_id: UUID
    device_id: str
    rgb_size: tuple[int, int]  # (width, height)
    depth_size: tuple[int, int]  # (width, height)
    K_rgb: NDArray[np.float64]  # 3 x 3
    T_stage_from_optical: NDArray[np.float64]  # 4 x 4
    reprojection_error_px: float
    created_at_utc: datetime


@dataclass(frozen=True, slots=True)
class CaptureGroup:
    """One or more device frames belonging to the same coordinated capture."""

    group_id: UUID
    frames: tuple[CapturedFrame, ...]
    normalized_capture_time_s: float
    pair_skew_ms: float
    calibration_id: UUID

    def __post_init__(self) -> None:
        if not 1 <= len(self.frames) <= 2:
            raise ValueError("capture group must contain one or two frames")
        device_ids = [frame.device_id for frame in self.frames]
        if len(set(device_ids)) != len(device_ids):
            raise ValueError("capture group frames must come from distinct devices")

    @property
    def first(self) -> CapturedFrame:
        """The deterministic primary view, retained for reference selection."""
        return self.frames[0]


@dataclass(frozen=True, slots=True)
class Landmark2DObservation:
    """One 2D landmark in the transmitted, unrotated image raster."""

    name: str
    xy_px: tuple[float, float]
    z_model: float | None
    visibility: float | None
    presence: float | None
    valid: bool


@dataclass(frozen=True, slots=True)
class ViewDetection:
    """Workflow-3 detection output for one view."""

    device_id: str
    capture_id: UUID
    person_mask: NDArray[np.bool_]
    body: tuple[Landmark2DObservation, ...]
    left_hand: tuple[Landmark2DObservation, ...]
    right_hand: tuple[Landmark2DObservation, ...]
    pose_world_prior_m: NDArray[np.float32] | None
    hand_world_priors_m: Mapping[str, NDArray[np.float32]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ColoredPointCloud:
    """Merged person-only colored cloud in stage meters."""

    xyz_stage_m: NDArray[np.float32]  # N x 3
    rgba: NDArray[np.uint8]  # N x 4
    source_mask: NDArray[np.uint8]  # N; camera bitset, debug only

    @property
    def count(self) -> int:
        return int(self.xyz_stage_m.shape[0])


@dataclass(frozen=True, slots=True)
class Landmark3D:
    """A named anatomical landmark registered into stage meters."""

    name: str
    position_stage_m: tuple[float, float, float] | None
    valid: bool
    source: str
    confidence: float | None = None
    visibility: float | None = None
    observed_by: tuple[str, ...] = ()
    reprojection_error_px: float | None = None


@dataclass(frozen=True, slots=True)
class Collider:
    """An analytic collider fitted to the subject.

    Exactly one geometry field group is populated according to ``type``.
    Invalid colliders retain identity fields with all geometry ``None``.
    """

    id: str
    body_part: str
    type: Literal["sphere", "capsule", "obb"]
    valid: bool
    fit_source: str
    quality: float | None
    # sphere
    center_stage_m: tuple[float, float, float] | None = None
    radius_m: float | None = None
    # capsule
    a_stage_m: tuple[float, float, float] | None = None
    b_stage_m: tuple[float, float, float] | None = None
    # obb
    axes_row_major: tuple[float, ...] | None = None  # length 9
    half_extents_m: tuple[float, float, float] | None = None


@dataclass(frozen=True, slots=True)
class FittedCharacter:
    """Workflow-3 fitting output: registered landmarks and colliders."""

    landmarks: tuple[Landmark3D, ...]
    colliders: tuple[Collider, ...]


@dataclass(frozen=True, slots=True)
class SourceFrameRef:
    device_id: str
    session_id: UUID
    capture_id: UUID
    sequence: int


@dataclass(frozen=True, slots=True)
class FrameQuality:
    valid: bool
    point_count: int
    valid_landmark_count: int
    valid_collider_count: int
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TraceContext:
    sentry_trace: str | None = None
    baggage: str | None = None


@dataclass(frozen=True, slots=True)
class CharacterFrame:
    """The immutable published snapshot. Cloud, landmarks, and colliders here
    always share the same source capture group and calibration."""

    session_id: UUID  # backend character-stream session
    calibration_id: UUID
    frame_id: int
    source_frames: tuple[SourceFrameRef, ...]
    normalized_capture_time_s: float
    pair_skew_ms: float
    mode: Literal["snapshot", "live"]
    quality: FrameQuality
    cloud: ColoredPointCloud
    landmarks: tuple[Landmark3D, ...]
    colliders: tuple[Collider, ...]
    trace: TraceContext = field(default_factory=TraceContext)
