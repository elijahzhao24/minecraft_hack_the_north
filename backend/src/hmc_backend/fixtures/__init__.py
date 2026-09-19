"""Synthetic scene generation for golden fixtures and the vertical slice."""

from hmc_backend.fixtures.scene import (
    build_capture_packets,
    encode_rgbd_packet,
    make_person_points,
    project_to_view,
)

__all__ = [
    "build_capture_packets",
    "encode_rgbd_packet",
    "make_person_points",
    "project_to_view",
]
