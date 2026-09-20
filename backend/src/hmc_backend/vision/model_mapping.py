"""Canonical landmark names and their MediaPipe model-index mappings.

This module is the single Python source for the ``body.*`` and ``hand.*``
landmark vocabulary used everywhere downstream (fusion, colliders, the wire
header). ``contracts/landmark_names.json`` at the repository root is a
generated mirror of these tables for the Swift and Java lanes; a test asserts
that the two never drift apart.

Index order follows the official MediaPipe Tasks output order:

* Pose Landmarker: 33 landmarks (https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker)
* Hand Landmarker: 21 landmarks per hand (https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker)

Left/right always means the *subject's anatomical* side.
"""

from __future__ import annotations

from typing import Final, Literal

Side = Literal["left", "right"]
SIDES: Final[tuple[Side, Side]] = ("left", "right")

BODY_PREFIX: Final = "body."
HAND_PREFIX: Final = "hand."

# --- MediaPipe Pose Landmarker (33) ------------------------------------------

POSE_LANDMARK_NAMES: Final[tuple[str, ...]] = (
    "nose",
    "left_eye_inner",
    "left_eye",
    "left_eye_outer",
    "right_eye_inner",
    "right_eye",
    "right_eye_outer",
    "left_ear",
    "right_ear",
    "mouth_left",
    "mouth_right",
    "left_shoulder",
    "right_shoulder",
    "left_elbow",
    "right_elbow",
    "left_wrist",
    "right_wrist",
    "left_pinky",
    "right_pinky",
    "left_index",
    "right_index",
    "left_thumb",
    "right_thumb",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
    "left_heel",
    "right_heel",
    "left_foot_index",
    "right_foot_index",
)
POSE_LANDMARK_COUNT: Final = 33

# Explicitly derived body landmarks that are not model outputs.
DERIVED_BODY_NAMES: Final[tuple[str, ...]] = ("pelvis_center", "head_center")

# --- MediaPipe Hand Landmarker (21) ------------------------------------------

HAND_LANDMARK_NAMES: Final[tuple[str, ...]] = (
    "wrist",
    "thumb_cmc",
    "thumb_mcp",
    "thumb_ip",
    "thumb_tip",
    "index_mcp",
    "index_pip",
    "index_dip",
    "index_tip",
    "middle_mcp",
    "middle_pip",
    "middle_dip",
    "middle_tip",
    "ring_mcp",
    "ring_pip",
    "ring_dip",
    "ring_tip",
    "pinky_mcp",
    "pinky_pip",
    "pinky_dip",
    "pinky_tip",
)
HAND_LANDMARK_COUNT: Final = 21

HAND_FINGERTIPS: Final[tuple[str, ...]] = (
    "thumb_tip",
    "index_tip",
    "middle_tip",
    "ring_tip",
    "pinky_tip",
)
HAND_MCPS: Final[tuple[str, ...]] = ("index_mcp", "middle_mcp", "ring_mcp", "pinky_mcp")

# --- Canonical (prefixed) names ----------------------------------------------


def body_name(short: str) -> str:
    """``"left_wrist"`` -> ``"body.left_wrist"``."""
    return BODY_PREFIX + short


def hand_name(side: Side, short: str) -> str:
    """``("left", "index_tip")`` -> ``"hand.left.index_tip"``."""
    return f"{HAND_PREFIX}{side}.{short}"


POSE_INDEX_BY_NAME: Final[dict[str, int]] = {n: i for i, n in enumerate(POSE_LANDMARK_NAMES)}
HAND_INDEX_BY_NAME: Final[dict[str, int]] = {n: i for i, n in enumerate(HAND_LANDMARK_NAMES)}

CANONICAL_BODY_NAMES: Final[tuple[str, ...]] = tuple(
    body_name(n) for n in POSE_LANDMARK_NAMES
) + tuple(body_name(n) for n in DERIVED_BODY_NAMES)

CANONICAL_HAND_NAMES: Final[tuple[str, ...]] = tuple(
    hand_name(side, n) for side in SIDES for n in HAND_LANDMARK_NAMES
)

ALL_CANONICAL_NAMES: Final[tuple[str, ...]] = CANONICAL_BODY_NAMES + CANONICAL_HAND_NAMES


def pose_index(short: str) -> int:
    """Model output index of a pose landmark short name."""
    return POSE_INDEX_BY_NAME[short]


def hand_index(short: str) -> int:
    """Model output index of a hand landmark short name."""
    return HAND_INDEX_BY_NAME[short]


def side_of(short: str) -> Side | None:
    """Anatomical side encoded in a body short name (``None`` for midline)."""
    if short.startswith("left_") or short.endswith("_left"):
        return "left"
    if short.startswith("right_") or short.endswith("_right"):
        return "right"
    return None


def mirror_side(side: Side) -> Side:
    return "right" if side == "left" else "left"


def landmark_names_document() -> dict:
    """The JSON document mirrored at ``contracts/landmark_names.json``."""
    return {
        "schema": "hmc.landmark_names",
        "schema_version": 1,
        "pose": {
            "model": "mediapipe_pose_landmarker",
            "count": POSE_LANDMARK_COUNT,
            "prefix": BODY_PREFIX,
            "index_to_name": list(POSE_LANDMARK_NAMES),
            "derived": list(DERIVED_BODY_NAMES),
        },
        "hand": {
            "model": "mediapipe_hand_landmarker",
            "count": HAND_LANDMARK_COUNT,
            "prefix_by_side": {side: f"{HAND_PREFIX}{side}." for side in SIDES},
            "index_to_name": list(HAND_LANDMARK_NAMES),
        },
        "note": (
            "left/right are the subject's anatomical sides. MediaPipe handedness "
            "assumes a mirrored (selfie) image; the backend swaps it for unmirrored input."
        ),
    }

