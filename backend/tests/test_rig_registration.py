"""The legacy ICP helper stays covered but is no longer in the live workflow."""

import numpy as np

from hmc_backend.reconstruction.registration import _yaw_matrix, register_yaw_translation


def test_yaw_translation_icp_recovers_synthetic_offset():
    rng = np.random.default_rng(0)
    pts = rng.uniform(-1, 1, (3000, 3))
    ax = rng.integers(0, 3, 3000)
    pts[np.arange(3000), ax] = rng.choice([-1.0, 1.0], 3000)
    body = pts * [0.2, 0.4, 0.12] + [0, 1.1, 0]
    limb = rng.uniform(-1, 1, (800, 3)) * [0.05, 0.3, 0.05] + [0.35, 1.2, 0.05]
    body = np.vstack([body, limb])
    front = body[body[:, 2] > 0]
    side = body[body[:, 0] > 0.02]
    rotation = _yaw_matrix(np.radians(30.0))
    moved = (rotation @ side.T).T + np.array([0.4, 0.0, -0.3])
    transform, rms, _ = register_yaw_translation(moved, front)
    inverse = np.linalg.inv(rotation)
    yaw_error = np.degrees(
        np.arctan2(transform[0, 2], transform[0, 0])
        - np.arctan2(inverse[0, 2], inverse[0, 0])
    )
    assert abs(yaw_error) < 3.0
    assert rms < 0.03
