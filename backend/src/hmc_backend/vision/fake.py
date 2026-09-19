"""Deterministic fake detector and fitter for the fixture-driven vertical slice.

These stand in for Workflow 3 so the whole pipeline (decode -> pair -> mask ->
reconstruct -> fit -> assemble -> publish) runs without MediaPipe or hardware.
The geometry is intentionally simple but *valid*: it exercises all three
collider types and both valid/invalid states, derived from the actual cloud
bounds so it stays consistent with the reconstructed points.
"""

from __future__ import annotations

import numpy as np

from hmc_backend.contracts.internal import (
    CameraCalibration,
    CapturedFrame,
    Collider,
    ColoredPointCloud,
    FittedCharacter,
    Landmark3D,
    PairedFrames,
    ViewDetection,
)


class FakePersonMaskDetector:
    """Marks the whole frame (optionally a centered box) as the person.

    Real segmentation belongs to Workflow 3; for fixtures a full-frame mask is
    sufficient to exercise reconstruction end to end.
    """

    def __init__(self, *, center_box: bool = False) -> None:
        self._center_box = center_box

    def detect_view(self, frame: CapturedFrame) -> ViewDetection:
        h, w = frame.depth_m.shape
        mask = np.ones((h, w), np.bool_)
        if self._center_box:
            mask[:] = False
            mask[h // 4 : 3 * h // 4, w // 4 : 3 * w // 4] = True
        return ViewDetection(
            device_id=frame.device_id,
            capture_id=frame.capture_id,
            person_mask=mask,
            body=(),
            left_hand=(),
            right_hand=(),
            pose_world_prior_m=None,
        )


class FakeCharacterFitter:
    """Fit trivially valid colliders/landmarks from the merged cloud's bounds."""

    def fit_character(
        self,
        pair: PairedFrames,
        detections: dict[str, ViewDetection],
        cloud: ColoredPointCloud,
        calibration: CameraCalibration,
    ) -> FittedCharacter:
        if cloud.count == 0:
            return FittedCharacter(landmarks=(), colliders=())

        xyz = cloud.xyz_stage_m
        lo = xyz.min(axis=0)
        hi = xyz.max(axis=0)
        center = (lo + hi) / 2.0
        height = float(hi[1] - lo[1])
        head_r = max(0.05, min(0.15, height * 0.08))

        head_center = (float(center[0]), float(hi[1] - head_r), float(center[2]))
        pelvis_center = (float(center[0]), float(lo[1] + height * 0.45), float(center[2]))

        landmarks = (
            Landmark3D("body.head_center", head_center, True, "derived"),
            Landmark3D("body.pelvis_center", pelvis_center, True, "derived"),
        )

        colliders = (
            # Head: sphere near the top of the cloud.
            Collider(
                id="head",
                body_part="head",
                type="sphere",
                valid=True,
                fit_source="observed",
                quality=0.5,
                center_stage_m=head_center,
                radius_m=head_r,
            ),
            # Torso: capsule pelvis -> shoulders.
            Collider(
                id="torso",
                body_part="torso",
                type="capsule",
                valid=True,
                fit_source="observed",
                quality=0.5,
                a_stage_m=pelvis_center,
                b_stage_m=(head_center[0], head_center[1] - head_r, head_center[2]),
                radius_m=max(0.08, float(hi[0] - lo[0]) * 0.25),
            ),
            # Left hand: axis-aligned OBB placeholder at the cloud's -X extent.
            Collider(
                id="hand.left",
                body_part="left_hand",
                type="obb",
                valid=True,
                fit_source="subject_default",
                quality=0.3,
                center_stage_m=(float(lo[0]), float(center[1]), float(center[2])),
                axes_row_major=(1, 0, 0, 0, 1, 0, 0, 0, 1),
                half_extents_m=(0.09, 0.05, 0.03),
            ),
            # Right hand: explicitly disabled to exercise the invalid path.
            Collider(
                id="hand.right",
                body_part="right_hand",
                type="obb",
                valid=False,
                fit_source="disabled",
                quality=None,
            ),
        )
        return FittedCharacter(landmarks=landmarks, colliders=colliders)
