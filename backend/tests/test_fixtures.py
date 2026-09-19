"""Round-trip tests: synthetic packets -> decode -> reconstruct."""

from __future__ import annotations

import numpy as np

from hmc_backend.calibration.synthetic import build_synthetic_rig
from hmc_backend.capture.rgbd_ingest import decode_rgbd_frame
from hmc_backend.fixtures.scene import build_capture_packets, make_person_points
from hmc_backend.protocol.envelope import decode_envelope


def test_person_points_span_expected_height():
    xyz, rgb = make_person_points()
    assert xyz.shape[0] == rgb.shape[0]
    # Head near ~1.77 m, feet near 0.
    assert xyz[:, 1].max() > 1.6
    assert xyz[:, 1].min() < 0.2


def test_packets_decode_to_matching_arrays():
    rig = build_synthetic_rig(rgb_size=(160, 120), depth_size=(160, 120))
    packets = build_capture_packets(rig, seed=1, splat=2)
    assert set(packets) == set(rig.device_ids())

    for device_id, raw in packets.items():
        env = decode_envelope(raw)
        decoded = decode_rgbd_frame(env)
        calib = rig.camera(device_id)
        assert decoded.rgb.shape == (120, 160, 3)
        assert decoded.depth_m.shape == (120, 160)
        # Header intrinsics match the calibration used to render.
        np.testing.assert_allclose(decoded.k_rgb, calib.K_rgb, rtol=1e-5)
        # Some depth was actually rendered.
        assert (decoded.depth_m > 0).sum() > 200


def test_front_view_depth_is_plausible():
    rig = build_synthetic_rig(rgb_size=(160, 120), depth_size=(160, 120), distance_m=2.5)
    packets = build_capture_packets(rig, seed=2, splat=2)
    env = decode_envelope(packets["front-phone"])
    decoded = decode_rgbd_frame(env)
    valid = decoded.depth_m[decoded.depth_m > 0]
    # The person stands ~2.5 m from the front camera.
    assert 1.5 < float(np.median(valid)) < 3.5
