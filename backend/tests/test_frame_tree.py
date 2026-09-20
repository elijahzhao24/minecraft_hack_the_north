"""Persisted rig frame-tree schema and legacy adapter."""

import numpy as np

from hmc_backend.calibration.frame_tree import (
    RigFrameTree,
    load_frame_tree,
    save_frame_tree,
)
from hmc_backend.calibration.synthetic import build_synthetic_rig
from hmc_backend.transforms import optical_frame


def test_frame_tree_round_trip(tmp_path):
    rig = build_synthetic_rig()
    tree = RigFrameTree.from_legacy_rig(rig)
    path = tmp_path / "frame_tree.json"
    save_frame_tree(tree, path)
    loaded = load_frame_tree(path)
    assert loaded.rig.calibration_id == rig.calibration_id
    for device_id in rig.device_ids():
        np.testing.assert_allclose(
            loaded.transforms.lookup_transform("stage", optical_frame(device_id), 1.0),
            rig.camera(device_id).T_stage_from_optical,
        )
