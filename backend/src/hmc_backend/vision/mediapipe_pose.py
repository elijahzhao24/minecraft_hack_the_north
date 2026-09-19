"""MediaPipe Pose Landmarker adapter used for person-only RGBD reconstruction."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from hmc_backend.contracts.internal import (
    CapturedFrame,
    CaptureGroup,
    FittedCharacter,
    ViewDetection,
)


class MediaPipePoseMaskDetector:
    """One VIDEO-mode landmarker per camera so tracking state never crosses views."""

    def __init__(
        self,
        model_path: str,
        device_ids: tuple[str, ...],
        *,
        threshold: float,
        expected_sha256: str | None = None,
    ) -> None:
        if not Path(model_path).is_file():
            raise ValueError(f"pose model not found: {model_path}")
        if expected_sha256 is not None:
            actual = hashlib.sha256(Path(model_path).read_bytes()).hexdigest()
            if actual != expected_sha256:
                raise ValueError("pose model SHA-256 does not match models/manifest.json")
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision

        self._mp = mp
        self._threshold = threshold
        self._last_ms = {device_id: -1 for device_id in device_ids}
        self._landmarkers = {
            device_id: vision.PoseLandmarker.create_from_options(
                vision.PoseLandmarkerOptions(
                    base_options=python.BaseOptions(
                        model_asset_path=model_path,
                        delegate=python.BaseOptions.Delegate.CPU,
                    ),
                    running_mode=vision.RunningMode.VIDEO,
                    num_poses=1,
                    min_pose_detection_confidence=0.5,
                    min_pose_presence_confidence=0.5,
                    min_tracking_confidence=0.5,
                    output_segmentation_masks=True,
                )
            )
            for device_id in device_ids
        }

    def detect_view(self, frame: CapturedFrame) -> ViewDetection:
        timestamp_ms = max(
            self._last_ms[frame.device_id] + 1,
            round(frame.normalized_capture_time_s * 1000),
        )
        self._last_ms[frame.device_id] = timestamp_ms
        image = self._mp.Image(
            image_format=self._mp.ImageFormat.SRGB,
            data=np.ascontiguousarray(frame.rgb, np.uint8),
        )
        result = self._landmarkers[frame.device_id].detect_for_video(image, timestamp_ms)
        if result.segmentation_masks:
            confidence = np.asarray(result.segmentation_masks[0].numpy_view())
            mask = np.squeeze(confidence) >= self._threshold
        else:
            mask = np.zeros(frame.rgb.shape[:2], dtype=np.bool_)
        return ViewDetection(
            device_id=frame.device_id,
            capture_id=frame.capture_id,
            person_mask=np.ascontiguousarray(mask, np.bool_),
            body=(),
            left_hand=(),
            right_hand=(),
            pose_world_prior_m=None,
        )

    def close(self) -> None:
        for landmarker in self._landmarkers.values():
            landmarker.close()


class EmptyCharacterFitter:
    """Mask-only milestone: never invent landmarks or interaction colliders."""

    def fit_character(self, group: CaptureGroup, detections, cloud, calibration) -> FittedCharacter:
        return FittedCharacter(landmarks=(), colliders=())
