"""Closed v1 enumerations shared across the wire contract.

Native framework enum values (ARKit tracking states, MediaPipe outputs, ...) are
mapped into these at the producing boundary. Unknown native values raise a
versioned validation error rather than being serialized with their platform
spelling.
"""

from __future__ import annotations

from enum import Enum


class Mode(str, Enum):
    SNAPSHOT = "snapshot"
    LIVE = "live"


class ImageOrientation(str, Enum):
    LANDSCAPE_RIGHT = "landscape_right"
    LANDSCAPE_LEFT = "landscape_left"
    PORTRAIT = "portrait"
    PORTRAIT_UPSIDE_DOWN = "portrait_upside_down"


class TrackingState(str, Enum):
    NORMAL = "normal"
    LIMITED_INITIALIZING = "limited_initializing"
    LIMITED_EXCESSIVE_MOTION = "limited_excessive_motion"
    LIMITED_INSUFFICIENT_FEATURES = "limited_insufficient_features"
    LIMITED_RELOCALIZING = "limited_relocalizing"
    NOT_AVAILABLE = "not_available"


class LandmarkSource(str, Enum):
    DEPTH_NEIGHBORHOOD = "depth_neighborhood"
    TRIANGULATED = "triangulated"
    REGISTERED_MODEL_PRIOR = "registered_model_prior"
    DERIVED = "derived"
    UNAVAILABLE = "unavailable"


class ColliderType(str, Enum):
    SPHERE = "sphere"
    CAPSULE = "capsule"
    OBB = "obb"


class FitSource(str, Enum):
    OBSERVED = "observed"
    SUBJECT_DEFAULT = "subject_default"
    GLOBAL_DEFAULT = "global_default"
    DISABLED = "disabled"


class BodyPart(str, Enum):
    HEAD = "head"
    TORSO = "torso"
    PELVIS = "pelvis"
    LEFT_UPPER_ARM = "left_upper_arm"
    RIGHT_UPPER_ARM = "right_upper_arm"
    LEFT_FOREARM = "left_forearm"
    RIGHT_FOREARM = "right_forearm"
    LEFT_HAND = "left_hand"
    RIGHT_HAND = "right_hand"
    LEFT_THIGH = "left_thigh"
    RIGHT_THIGH = "right_thigh"
    LEFT_SHIN = "left_shin"
    RIGHT_SHIN = "right_shin"
    LEFT_FOOT = "left_foot"
    RIGHT_FOOT = "right_foot"


class ProbeResultCode(str, Enum):
    HIT = "HIT"
    MISS = "MISS"
    BLOCK_OCCLUDED = "BLOCK_OCCLUDED"
    NO_ACTIVE_SNAPSHOT = "NO_ACTIVE_SNAPSHOT"
    FRAME_MISMATCH = "FRAME_MISMATCH"
    OUT_OF_REACH = "OUT_OF_REACH"


class HealthStatus(str, Enum):
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
