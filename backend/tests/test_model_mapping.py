"""Model-index maps have the expected counts, unique names, and a JSON mirror."""

from __future__ import annotations

import json
from pathlib import Path

from hmc_backend.vision import model_mapping as mm

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_pose_map_count_and_uniqueness():
    assert len(mm.POSE_LANDMARK_NAMES) == mm.POSE_LANDMARK_COUNT == 33
    assert len(set(mm.POSE_LANDMARK_NAMES)) == 33
    # Spot-check the official order.
    assert mm.pose_index("nose") == 0
    assert mm.pose_index("left_shoulder") == 11
    assert mm.pose_index("right_wrist") == 16
    assert mm.pose_index("left_hip") == 23
    assert mm.pose_index("right_foot_index") == 32


def test_hand_map_count_and_uniqueness():
    assert len(mm.HAND_LANDMARK_NAMES) == mm.HAND_LANDMARK_COUNT == 21
    assert len(set(mm.HAND_LANDMARK_NAMES)) == 21
    assert mm.hand_index("wrist") == 0
    assert mm.hand_index("thumb_tip") == 4
    assert mm.hand_index("index_mcp") == 5
    assert mm.hand_index("pinky_tip") == 20


def test_canonical_names_are_unique_and_prefixed():
    assert len(set(mm.ALL_CANONICAL_NAMES)) == len(mm.ALL_CANONICAL_NAMES)
    assert len(mm.CANONICAL_BODY_NAMES) == 35  # 33 + pelvis_center + head_center
    assert len(mm.CANONICAL_HAND_NAMES) == 42
    assert all(n.startswith("body.") for n in mm.CANONICAL_BODY_NAMES)
    assert all(n.startswith(("hand.left.", "hand.right.")) for n in mm.CANONICAL_HAND_NAMES)
    assert mm.hand_name("left", "index_tip") == "hand.left.index_tip"
    assert mm.body_name("pelvis_center") == "body.pelvis_center"


def test_side_detection():
    assert mm.side_of("left_wrist") == "left"
    assert mm.side_of("right_foot_index") == "right"
    assert mm.side_of("mouth_left") == "left"
    assert mm.side_of("nose") is None
    assert mm.mirror_side("left") == "right"


def test_json_mirror_matches_python_source():
    path = REPO_ROOT / "contracts" / "landmark_names.json"
    assert path.exists(), "contracts/landmark_names.json must be committed"
    on_disk = json.loads(path.read_text())
    assert on_disk == mm.landmark_names_document()
