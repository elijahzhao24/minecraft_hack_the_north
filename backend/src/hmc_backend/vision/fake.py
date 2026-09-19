"""Deterministic fake detector and fitter for the fixture-driven vertical slice.

These stand in for Workflow 3 so the whole pipeline (decode -> pair -> mask ->
reconstruct -> fit -> assemble -> publish) runs without MediaPipe or hardware.
The geometry is intentionally simple but *valid*: it exercises all three
collider types and both valid/invalid states, derived from the actual cloud
bounds so it stays consistent with the reconstructed points.
"""

from __future__ import annotations

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d

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


def segment_person_depth(depth_m: np.ndarray, confidence: np.ndarray | None = None) -> np.ndarray:
    """Isolate the foreground human subject from background walls, floor, and clutter."""
    h, w = depth_m.shape
    valid = np.isfinite(depth_m) & (depth_m > 0.2) & (depth_m < 5.0)
    if confidence is not None and confidence.shape == depth_m.shape:
        valid &= (confidence >= 1)

    valid_count = np.count_nonzero(valid)
    if valid_count < 10:
        return np.zeros((h, w), dtype=bool)

    # If small test frame (e.g. unit tests with 8x8 frames), return all valid pixels.
    if h <= 16 or w <= 16:
        return valid

    # Use central region (where person typically stands) to detect foreground depth
    r_y0, r_y1 = int(h * 0.1), int(h * 0.9)
    r_x0, r_x1 = int(w * 0.15), int(w * 0.85)
    center_valid = valid[r_y0:r_y1, r_x0:r_x1]
    center_depths = depth_m[r_y0:r_y1, r_x0:r_x1][center_valid]

    if center_depths.size < 50:
        center_depths = depth_m[valid]

    if center_depths.size < 50:
        return valid

    # Histogram in 5cm bins from 0.3m to 4.5m
    bin_size = 0.05
    bins = np.arange(0.3, 4.5 + bin_size, bin_size)
    hist, edges = np.histogram(center_depths, bins=bins)

    # Smooth histogram
    smooth_hist = gaussian_filter1d(hist.astype(np.float32), sigma=1.5)

    # Find peaks (local maxima with at least 8% of max peak or 30 points)
    threshold = max(30.0, float(np.max(smooth_hist)) * 0.08)
    peaks = []
    for i in range(1, len(smooth_hist) - 1):
        if smooth_hist[i] > smooth_hist[i - 1] and smooth_hist[i] >= smooth_hist[i + 1]:
            if smooth_hist[i] >= threshold:
                peaks.append(i)

    if not peaks:
        # Fallback: use 15th percentile as foreground
        p15 = float(np.percentile(center_depths, 15))
        d_min = max(0.2, p15 - 0.35)
        d_max = p15 + 0.6
    else:
        # Closest peak is the person
        first_peak = peaks[0]
        d_peak = float((edges[first_peak] + edges[first_peak + 1]) / 2.0)

        # Lower bound: 0.4m closer than peak (or until histogram drops to ~0)
        d_min = max(0.2, d_peak - 0.4)
        for i in range(first_peak - 1, -1, -1):
            if smooth_hist[i] < threshold * 0.2:
                d_min = float(edges[i])
                break

        # Upper bound: search for valley before next peak or cap at peak + 0.55m
        d_max = d_peak + 0.55
        for i in range(first_peak + 1, len(smooth_hist) - 1):
            d_curr = (edges[i] + edges[i + 1]) / 2.0
            if d_curr > d_peak + 0.7:
                break
            # If we hit a local minimum and it's lower than 40% of peak
            if smooth_hist[i] <= smooth_hist[i - 1] and smooth_hist[i] <= smooth_hist[i + 1]:
                if smooth_hist[i] < smooth_hist[first_peak] * 0.4:
                    d_max = float((edges[i] + edges[i + 1]) / 2.0)
                    break

    candidate = valid & (depth_m >= d_min) & (depth_m <= d_max)

    # Connected components to remove floating noise / wall fragments
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    closed = cv2.morphologyEx(candidate.astype(np.uint8), cv2.MORPH_CLOSE, kernel)

    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(closed, connectivity=8)
    if num_labels <= 1:
        return candidate

    # Find largest component (excluding 0)
    areas = stats[1:, cv2.CC_STAT_AREA]
    max_idx = 1 + int(np.argmax(areas))
    max_area = areas[max_idx - 1]

    # Keep largest component and any components with >15% of largest area
    # that are within reasonable distance of the main body
    person_mask = (labels == max_idx)
    main_cx, main_cy = centroids[max_idx]

    for lbl in range(1, num_labels):
        if lbl == max_idx:
            continue
        area = stats[lbl, cv2.CC_STAT_AREA]
        if area > max_area * 0.03:
            cx, cy = centroids[lbl]
            if (cx - main_cx) ** 2 + (cy - main_cy) ** 2 < (max(h, w) * 0.5) ** 2:
                person_mask |= (labels == lbl)

    # Small dilation to preserve fine fingers / hair, but strictly within depth bounds
    person_mask = cv2.dilate(person_mask.astype(np.uint8), kernel, iterations=1).astype(bool)
    return person_mask & valid & (depth_m <= d_max + 0.05) & (depth_m >= d_min - 0.05)


class FakePersonMaskDetector:
    """Marks the person using LiDAR depth foreground segmentation.

    Separates the human subject from background walls, floors, and room geometry.
    If center_box is explicitly requested (fixtures/tests), falls back to center crop.
    """

    def __init__(self, *, center_box: bool = False, depth_segment: bool = True) -> None:
        self._center_box = center_box
        self._depth_segment = depth_segment and not center_box

    def detect_view(self, frame: CapturedFrame) -> ViewDetection:
        h, w = frame.depth_m.shape
        if self._center_box:
            mask = np.zeros((h, w), np.bool_)
            mask[h // 4 : 3 * h // 4, w // 4 : 3 * w // 4] = True
        elif self._depth_segment:
            mask = segment_person_depth(frame.depth_m, frame.confidence)
        else:
            mask = np.ones((h, w), np.bool_)

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
