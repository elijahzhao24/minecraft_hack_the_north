"""Static transform-tree composition and validation."""

import numpy as np
import pytest

from hmc_backend.transforms import StaticTransform, TransformBuffer, TransformError


def _translation(x, y, z):
    transform = np.eye(4)
    transform[:3, 3] = [x, y, z]
    return transform


def test_direct_inverse_and_composed_lookup():
    buffer = TransformBuffer([
        StaticTransform("stage", "phone/world", _translation(1, 0, 0), "test"),
        StaticTransform("phone/world", "phone/optical", _translation(0, 2, 0), "test"),
    ])
    got = buffer.lookup_transform("stage", "phone/optical", 12.0)
    np.testing.assert_allclose(got, _translation(1, 2, 0))
    np.testing.assert_allclose(
        buffer.lookup_transform("phone/optical", "stage", 12.0), np.linalg.inv(got)
    )


def test_unknown_and_disconnected_frames_are_unavailable():
    buffer = TransformBuffer([
        StaticTransform("a", "b", np.eye(4)),
        StaticTransform("x", "y", np.eye(4)),
    ])
    assert not buffer.can_transform("a", "missing", 0.0)
    with pytest.raises(TransformError, match="disconnected"):
        buffer.lookup_transform("a", "y", 0.0)


def test_rejects_duplicate_parent_cycle_and_reflection():
    with pytest.raises(TransformError, match="multiple parents"):
        TransformBuffer([
            StaticTransform("a", "child", np.eye(4)),
            StaticTransform("b", "child", np.eye(4)),
        ])
    with pytest.raises(TransformError, match="cycle"):
        TransformBuffer([
            StaticTransform("a", "b", np.eye(4)),
            StaticTransform("b", "a", np.eye(4)),
        ])
    reflection = np.eye(4)
    reflection[0, 0] = -1
    with pytest.raises(TransformError, match="right-handed"):
        StaticTransform("a", "b", reflection)
