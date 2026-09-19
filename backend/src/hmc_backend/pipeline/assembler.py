"""Assemble an immutable, self-consistent CharacterFrame.

The assembler is the correctness gate: it assigns the next monotonically
increasing ``frame_id`` only after verifying that the cloud, landmarks, and
colliders all refer to the same source capture group and calibration, that array
invariants hold, and that collider/landmark geometry is valid. Assembly is
all-or-nothing; a rejected group never advances the frame counter.
"""

from __future__ import annotations

from uuid import UUID

from hmc_backend.colliders.validate import validate_colliders
from hmc_backend.contracts.arrays import check_cloud, freeze
from hmc_backend.contracts.internal import (
    CaptureGroup,
    CharacterFrame,
    ColoredPointCloud,
    FittedCharacter,
    FrameQuality,
    SourceFrameRef,
    TraceContext,
)


class AssemblyError(ValueError):
    """Raised when a pair cannot be assembled into a valid CharacterFrame."""


class FrameAssembler:
    """Builds validated CharacterFrames with a monotonic frame_id."""

    def __init__(self, session_id: UUID) -> None:
        self._session_id = session_id
        self._next_frame_id = 1

    @property
    def next_frame_id(self) -> int:
        return self._next_frame_id

    def assemble(
        self,
        group: CaptureGroup,
        cloud: ColoredPointCloud,
        fitted: FittedCharacter,
        *,
        mode: str,
        calibration_id: UUID,
        trace: TraceContext | None = None,
        extra_warnings: tuple[str, ...] = (),
    ) -> CharacterFrame:
        # 1. Calibration consistency across pair and inputs.
        if group.calibration_id != calibration_id:
            raise AssemblyError("capture group calibration_id does not match active calibration")
        if not group.frames:
            raise AssemblyError("capture group must contain at least one frame")

        # 2. Array invariants (raises ArrayInvariantError -> caller treats as failure).
        check_cloud(cloud.xyz_stage_m, cloud.rgba, cloud.source_mask)

        # 3. Collider geometry + unique IDs.
        validate_colliders(fitted.colliders)

        # 4. Unique landmark names.
        names = [lm.name for lm in fitted.landmarks]
        if len(names) != len(set(names)):
            raise AssemblyError("landmark names are not unique")

        # 5. Source refs come straight from the consumed pair.
        source_frames = tuple(_source_ref(frame) for frame in group.frames)

        valid_landmarks = sum(1 for lm in fitted.landmarks if lm.valid)
        valid_colliders = sum(1 for c in fitted.colliders if c.valid)
        warnings = tuple(extra_warnings)
        quality = FrameQuality(
            valid=cloud.count > 0,
            point_count=cloud.count,
            valid_landmark_count=valid_landmarks,
            valid_collider_count=valid_colliders,
            warnings=warnings,
        )

        frozen_cloud = ColoredPointCloud(
            xyz_stage_m=freeze(cloud.xyz_stage_m),
            rgba=freeze(cloud.rgba),
            source_mask=freeze(cloud.source_mask),
        )

        frame = CharacterFrame(
            session_id=self._session_id,
            calibration_id=calibration_id,
            frame_id=self._next_frame_id,
            source_frames=source_frames,
            normalized_capture_time_s=group.normalized_capture_time_s,
            pair_skew_ms=group.pair_skew_ms,
            mode="live" if mode == "live" else "snapshot",
            quality=quality,
            cloud=frozen_cloud,
            landmarks=fitted.landmarks,
            colliders=fitted.colliders,
            trace=trace or TraceContext(),
        )
        # Only advance the counter once assembly fully succeeds.
        self._next_frame_id += 1
        return frame


def _source_ref(frame) -> SourceFrameRef:
    return SourceFrameRef(
        device_id=frame.device_id,
        session_id=frame.session_id,
        capture_id=frame.capture_id,
        sequence=frame.sequence,
    )
