"""ChArUco board detection and camera-to-stage pose estimation.

This turns board observations into the calibrated ``T_stage_from_optical`` that
every downstream stage depends on. The flow per camera is:

1. Detect ChArUco corners in each recorded RGB frame.
2. ``solvePnP`` those corners against the board's object points, giving
   ``T_camera_from_board`` (OpenCV's camera frame *is* our optical frame:
   +X image-right, +Y image-down, +Z forward).
3. Invert and compose with the known board-to-stage transform::

       T_stage_from_optical = T_stage_from_board @ inverse(T_camera_from_board)

4. Robustly aggregate across frames: rotations are averaged **on SO(3)**, never
   element-wise; translations use a component-wise median.

OpenCV API note: this project pins opencv-contrib-python 5.x, where the legacy
``interpolateCornersCharuco`` / ``estimatePoseCharucoBoard`` helpers no longer
exist. The modern ``CharucoDetector`` + ``matchImagePoints`` flow is used here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from numpy.typing import NDArray

# --- Board-to-stage orientation -------------------------------------------
#
# OpenCV's ChArUco object frame follows the *printed image* convention:
#   +X runs left->right across the printed page,
#   +Y runs top->bottom DOWN the printed page,
#   +Z = X x Y, which therefore points INTO the page.
# The printed (visible) face is the -Z side. This is verified empirically in
# tests/test_charuco.py by detecting the board in its own generated image.
#
# PHYSICAL PLACEMENT for calibration:
#   * Board lies FLAT ON THE FLOOR, printed side UP.
#   * Origin corner (printed top-left) sits on the stage mark.
#   * Printed +X (across the page) points along stage +X.
#   * Printed +Y (down the page) points along stage +Z, i.e. TOWARD the front
#     camera, so the bottom of the page is nearest that camera.
#
# Board axes then map to stage axes as:
#   +X -> +X,  +Y -> +Z (toward front camera),  +Z -> -Y (down into the floor,
#   consistent with the printed face pointing up).
#
# The matrix is right-handed (det = +1), asserted in stage_from_board().
#
# TWO DISTINCT TRAPS, both covered by tests:
#  1. [[1,0,0],[0,0,1],[0,1,0]] has det = -1. It is a reflection and silently
#     MIRRORS the subject, swapping left/right limbs.
#  2. [[1,0,0],[0,0,1],[0,-1,0]] has det = +1 but assumes board +Z points UP out
#     of the board. It does not (see above), so it flips the rig 180 degrees.
#     A determinant check cannot catch this one -- only a real board can.
STAGE_FROM_BOARD_ROTATION: NDArray[np.float64] = np.array(
    [
        [1.0, 0.0, 0.0],
        [0.0, 0.0, -1.0],
        [0.0, 1.0, 0.0],
    ],
    dtype=np.float64,
)


class CharucoError(ValueError):
    """Raised when board detection or pose estimation cannot proceed."""


@dataclass(frozen=True, slots=True)
class BoardSpec:
    """Printed ChArUco board geometry. Lengths are measured, in meters."""

    dictionary: str = "DICT_5X5_100"
    squares_x: int = 7
    squares_y: int = 10
    square_length_m: float = 0.04
    marker_length_m: float = 0.03

    def __post_init__(self) -> None:
        if self.squares_x < 2 or self.squares_y < 2:
            raise CharucoError("board must have at least 2x2 squares")
        if not 0 < self.marker_length_m < self.square_length_m:
            raise CharucoError("marker_length_m must be positive and < square_length_m")

    @property
    def width_m(self) -> float:
        return self.squares_x * self.square_length_m

    @property
    def height_m(self) -> float:
        return self.squares_y * self.square_length_m

    def to_json(self) -> dict:
        return {
            "dictionary": self.dictionary,
            "squares_x": self.squares_x,
            "squares_y": self.squares_y,
            "square_length_m": self.square_length_m,
            "marker_length_m": self.marker_length_m,
        }

    @classmethod
    def from_json(cls, raw: dict) -> BoardSpec:
        return cls(
            dictionary=str(raw.get("dictionary", "DICT_5X5_100")),
            squares_x=int(raw["squares_x"]),
            squares_y=int(raw["squares_y"]),
            square_length_m=float(raw["square_length_m"]),
            marker_length_m=float(raw["marker_length_m"]),
        )


def build_board(spec: BoardSpec):
    """Construct the OpenCV board and its detector for ``spec``."""
    try:
        dict_id = getattr(cv2.aruco, spec.dictionary)
    except AttributeError as exc:
        raise CharucoError(f"unknown ArUco dictionary {spec.dictionary!r}") from exc

    dictionary = cv2.aruco.getPredefinedDictionary(dict_id)
    board = cv2.aruco.CharucoBoard(
        (spec.squares_x, spec.squares_y),
        spec.square_length_m,
        spec.marker_length_m,
        dictionary,
    )
    detector = cv2.aruco.CharucoDetector(board)
    return board, detector


def detect_board(image: NDArray[np.uint8], detector) -> tuple[NDArray, NDArray] | None:
    """Detect ChArUco corners; returns ``(corners, ids)`` or ``None``.

    Accepts RGB, BGR, or grayscale (the pattern is colour-independent).
    """
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    corners, ids, _marker_corners, _marker_ids = detector.detectBoard(gray)
    if corners is None or ids is None or len(ids) == 0:
        return None
    return corners, ids


def corner_coverage(corners: NDArray, image_shape: tuple[int, int]) -> float:
    """Fraction of the image area spanned by the detected corners' bounding box.

    A cluster of corners in one small region gives a poorly constrained pose,
    so low coverage is a rejection criterion.
    """
    pts = corners.reshape(-1, 2)
    if pts.shape[0] < 3:
        return 0.0
    h, w = image_shape[:2]
    span_x = float(pts[:, 0].max() - pts[:, 0].min())
    span_y = float(pts[:, 1].max() - pts[:, 1].min())
    return (span_x * span_y) / float(w * h)


@dataclass(frozen=True, slots=True)
class BoardObservation:
    """One accepted board pose observation from a single frame."""

    device_id: str
    sequence: int
    corner_count: int
    coverage: float
    R_camera_from_board: NDArray[np.float64]  # 3 x 3
    t_camera_from_board: NDArray[np.float64]  # 3
    reprojection_error_px: float


def estimate_pose(
    corners: NDArray,
    ids: NDArray,
    board,
    k_rgb: NDArray[np.float64],
    dist: NDArray[np.float64] | None = None,
) -> tuple[NDArray[np.float64], NDArray[np.float64], float] | None:
    """Estimate ``T_camera_from_board`` from detected corners.

    Returns ``(R, t, reprojection_error_px)``, or ``None`` if the pose cannot be
    solved or the board would sit behind the camera.

    ``dist`` defaults to zeros: ARKit supplies intrinsics for an effectively
    rectilinear image, so no distortion model is applied.
    """
    if dist is None:
        dist = np.zeros(5, dtype=np.float64)

    obj_pts, img_pts = board.matchImagePoints(corners, ids)
    if obj_pts is None or img_pts is None or len(obj_pts) < 4:
        return None

    ok, rvec, tvec = cv2.solvePnP(
        obj_pts, img_pts, k_rgb, dist, flags=cv2.SOLVEPNP_ITERATIVE
    )
    if not ok:
        return None

    # solvePnP returns tvec as a (3, 1) column; flatten before scalar access.
    translation = np.asarray(tvec, dtype=np.float64).reshape(3)

    # The board must be in front of the camera (positive optical Z).
    if translation[2] <= 0.0:
        return None

    projected, _ = cv2.projectPoints(obj_pts, rvec, tvec, k_rgb, dist)
    residuals = np.linalg.norm(projected.reshape(-1, 2) - img_pts.reshape(-1, 2), axis=1)
    reproj_px = float(np.sqrt(np.mean(residuals**2)))  # RMS

    rotation, _ = cv2.Rodrigues(rvec)
    return rotation.astype(np.float64), translation, reproj_px


def observe_frame(
    image: NDArray[np.uint8],
    k_rgb: NDArray[np.float64],
    board,
    detector,
    *,
    device_id: str,
    sequence: int,
    min_corners: int = 8,
    min_coverage: float = 0.02,
    max_reprojection_px: float = 3.0,
) -> tuple[BoardObservation | None, str]:
    """Detect + solve one frame, applying acceptance criteria.

    Returns ``(observation_or_None, reason)`` where ``reason`` is ``"accepted"``
    or a stable rejection code suitable for logging.
    """
    found = detect_board(image, detector)
    if found is None:
        return None, "no_board_detected"
    corners, ids = found

    if len(ids) < min_corners:
        return None, "too_few_corners"

    coverage = corner_coverage(corners, image.shape[:2])
    if coverage < min_coverage:
        return None, "corners_clustered"

    solved = estimate_pose(corners, ids, board, k_rgb)
    if solved is None:
        return None, "pose_unsolvable"
    rotation, translation, reproj_px = solved

    if reproj_px > max_reprojection_px:
        return None, "reprojection_too_high"

    return (
        BoardObservation(
            device_id=device_id,
            sequence=sequence,
            corner_count=len(ids),
            coverage=coverage,
            R_camera_from_board=rotation,
            t_camera_from_board=translation,
            reprojection_error_px=reproj_px,
        ),
        "accepted",
    )


# --- Rotation aggregation --------------------------------------------------

def average_rotations_so3(
    rotations: list[NDArray[np.float64]], weights: list[float] | None = None
) -> NDArray[np.float64]:
    """Chordal L2 mean of rotations, projected back onto SO(3).

    Element-wise averaging of rotation matrices does **not** yield a rotation.
    This sums the (optionally weighted) matrices and projects the result onto
    SO(3) via SVD, forcing ``det = +1`` so no reflection can sneak in.
    """
    if not rotations:
        raise CharucoError("cannot average an empty set of rotations")
    if weights is None:
        weights = [1.0] * len(rotations)
    if len(weights) != len(rotations):
        raise CharucoError("weights and rotations length mismatch")

    acc = np.zeros((3, 3), dtype=np.float64)
    for rotation, weight in zip(rotations, weights, strict=True):
        acc += float(weight) * rotation

    u, _s, vt = np.linalg.svd(acc)
    d = np.diag([1.0, 1.0, float(np.sign(np.linalg.det(u @ vt)))])
    return u @ d @ vt


def geodesic_angle_rad(a: NDArray[np.float64], b: NDArray[np.float64]) -> float:
    """Angle of the relative rotation between two rotation matrices."""
    rel = a.T @ b
    cos = (np.trace(rel) - 1.0) / 2.0
    return float(np.arccos(np.clip(cos, -1.0, 1.0)))


# --- Stage composition -----------------------------------------------------

def stage_from_board(board_origin_stage_m: tuple[float, float, float] = (0.0, 0.0, 0.0)):
    """Build ``T_stage_from_board`` for a board flat on the floor.

    ``board_origin_stage_m`` is where the board's origin corner sits in stage
    coordinates; it is the zero vector when the corner is on the stage mark.
    """
    det = float(np.linalg.det(STAGE_FROM_BOARD_ROTATION))
    if abs(det - 1.0) > 1e-9:
        raise CharucoError(f"STAGE_FROM_BOARD_ROTATION must be right-handed, det={det}")

    t = np.eye(4, dtype=np.float64)
    t[:3, :3] = STAGE_FROM_BOARD_ROTATION
    t[:3, 3] = np.asarray(board_origin_stage_m, dtype=np.float64)
    return t


def compose_stage_from_optical(
    r_camera_from_board: NDArray[np.float64],
    t_camera_from_board: NDArray[np.float64],
    t_stage_from_board: NDArray[np.float64],
) -> NDArray[np.float64]:
    """``T_stage_from_optical = T_stage_from_board @ inverse(T_camera_from_board)``."""
    t_board_from_camera = np.eye(4, dtype=np.float64)
    t_board_from_camera[:3, :3] = r_camera_from_board.T
    t_board_from_camera[:3, 3] = -r_camera_from_board.T @ t_camera_from_board
    return t_stage_from_board @ t_board_from_camera


# --- Robust per-camera solve ----------------------------------------------

@dataclass(frozen=True, slots=True)
class CameraSolution:
    """Aggregated calibration result for one camera."""

    device_id: str
    T_stage_from_optical: NDArray[np.float64]
    accepted_count: int
    rejected_count: int
    median_reprojection_error_px: float
    max_reprojection_error_px: float
    rejection_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def camera_position_stage_m(self) -> NDArray[np.float64]:
        """Where this camera sits in the stage frame (useful sanity check)."""
        return self.T_stage_from_optical[:3, 3]


def solve_camera(
    observations: list[BoardObservation],
    *,
    device_id: str,
    t_stage_from_board: NDArray[np.float64] | None = None,
    max_angle_deg: float = 5.0,
    rejection_reasons: dict[str, int] | None = None,
) -> CameraSolution:
    """Aggregate board observations into one camera-to-stage transform.

    Outlier rejection runs in two passes: an initial SO(3) mean, then rejection
    of observations whose rotation is more than ``max_angle_deg`` from it, then
    a final mean over the survivors. Translations use a component-wise median,
    which is robust without needing the same angular treatment.
    """
    if not observations:
        raise CharucoError(f"{device_id}: no accepted board observations")

    if t_stage_from_board is None:
        t_stage_from_board = stage_from_board()

    rotations = [o.R_camera_from_board for o in observations]
    # Weight better-constrained views (more corners, lower residual) higher.
    weights = [o.corner_count / max(o.reprojection_error_px, 1e-3) for o in observations]

    coarse = average_rotations_so3(rotations, weights)
    max_angle_rad = np.radians(max_angle_deg)

    kept = [
        o for o in observations if geodesic_angle_rad(coarse, o.R_camera_from_board) <= max_angle_rad
    ]
    outliers = len(observations) - len(kept)
    if not kept:
        # Every view disagrees with the mean: the board likely moved between
        # frames. Fall back to the full set rather than inventing a pose.
        kept = observations
        outliers = 0

    kept_rotations = [o.R_camera_from_board for o in kept]
    kept_weights = [o.corner_count / max(o.reprojection_error_px, 1e-3) for o in kept]
    rotation = average_rotations_so3(kept_rotations, kept_weights)
    translation = np.median(np.stack([o.t_camera_from_board for o in kept]), axis=0)

    transform = compose_stage_from_optical(rotation, translation, t_stage_from_board)

    residuals = [o.reprojection_error_px for o in kept]
    reasons = dict(rejection_reasons or {})
    if outliers:
        reasons["rotation_outlier"] = reasons.get("rotation_outlier", 0) + outliers

    return CameraSolution(
        device_id=device_id,
        T_stage_from_optical=transform,
        accepted_count=len(kept),
        rejected_count=sum(reasons.values()),
        median_reprojection_error_px=float(np.median(residuals)),
        max_reprojection_error_px=float(np.max(residuals)),
        rejection_reasons=reasons,
    )


def validate_solution(
    solution: CameraSolution,
    held_out: list[BoardObservation],
    *,
    t_stage_from_board: NDArray[np.float64] | None = None,
) -> dict:
    """Score a solved camera against held-out observations.

    Reports how far each held-out frame's independently solved camera position
    lands from the aggregated one. Reprojection agreement alone does not prove
    correct depth, so this is a position check in meters.
    """
    if not held_out:
        return {"held_out_frames": 0}

    if t_stage_from_board is None:
        t_stage_from_board = stage_from_board()

    reference = solution.camera_position_stage_m
    errors = []
    for o in held_out:
        t = compose_stage_from_optical(
            o.R_camera_from_board, o.t_camera_from_board, t_stage_from_board
        )
        errors.append(float(np.linalg.norm(t[:3, 3] - reference)))

    return {
        "held_out_frames": len(held_out),
        "median_position_error_m": float(np.median(errors)),
        "max_position_error_m": float(np.max(errors)),
        "median_reprojection_error_px": float(
            np.median([o.reprojection_error_px for o in held_out])
        ),
    }
