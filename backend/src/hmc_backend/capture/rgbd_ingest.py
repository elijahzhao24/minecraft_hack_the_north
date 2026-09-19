"""Decode an HMC1 ``RGBD_FRAME`` envelope into typed arrays.

This is the ingress boundary: it validates the header, extracts and shape-checks
the RGB/depth/confidence buffers, and decodes the JPEG. The result still lacks a
normalized capture time and clock uncertainty; those are stamped by the capture
loop once the device's clock estimate is applied.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

from hmc_backend.contracts.rgbd import RgbdFrameHeader, parse_rgbd_header
from hmc_backend.protocol.buffers import BufferEncoding, load_buffers
from hmc_backend.protocol.envelope import Envelope, EnvelopeError, MessageType


@dataclass(frozen=True, slots=True)
class DecodedRgbd:
    """Decoded RGBD arrays plus validated header metadata."""

    header: RgbdFrameHeader
    rgb: NDArray[np.uint8]        # H_rgb x W_rgb x 3, RGB order
    depth_m: NDArray[np.float32]  # H_depth x W_depth
    confidence: NDArray[np.uint8]  # H_depth x W_depth
    k_rgb: NDArray[np.float64]    # 3 x 3
    arkit_pose: NDArray[np.float64]  # 4 x 4


def decode_rgbd_frame(envelope: Envelope) -> DecodedRgbd:
    """Validate and decode one RGBD envelope into arrays."""
    if envelope.message_type is not MessageType.RGBD_FRAME:
        raise EnvelopeError("invalid_message", "expected an RGBD_FRAME envelope")

    header = parse_rgbd_header(envelope.header)
    buffers = load_buffers(envelope.header, envelope.payload)

    # --- RGB (JPEG) ---
    rgb_desc = buffers.descriptor("rgb")
    if rgb_desc.encoding is not BufferEncoding.JPEG:
        raise EnvelopeError("invalid_message", "rgb buffer must be jpeg")
    rgb_bgr = cv2.imdecode(np.frombuffer(buffers.raw("rgb"), np.uint8), cv2.IMREAD_COLOR)
    if rgb_bgr is None:
        raise EnvelopeError("invalid_message", "rgb JPEG failed to decode")
    if (rgb_bgr.shape[1], rgb_bgr.shape[0]) != (header.rgb.width, header.rgb.height):
        raise EnvelopeError("invalid_message", "decoded RGB size does not match header")
    rgb = cv2.cvtColor(rgb_bgr, cv2.COLOR_BGR2RGB)

    # --- Depth (float32) ---
    depth = buffers.array("depth").astype(np.float32, copy=False)
    if depth.shape != (header.depth.height, header.depth.width):
        raise EnvelopeError("invalid_message", "depth shape does not match header")

    # --- Confidence (uint8) ---
    if "confidence" in buffers:
        confidence = buffers.array("confidence").astype(np.uint8, copy=False)
        if confidence.shape != (header.depth.height, header.depth.width):
            raise EnvelopeError("invalid_message", "confidence shape does not match header")
    else:
        # Absent confidence: treat everything as maximally confident.
        confidence = np.full((header.depth.height, header.depth.width), 2, np.uint8)

    k_rgb = np.array(header.rgb.intrinsics_row_major, np.float64).reshape(3, 3)
    pose = np.array(header.t_arkit_world_from_camera_row_major, np.float64).reshape(4, 4)

    return DecodedRgbd(
        header=header,
        rgb=np.ascontiguousarray(rgb),
        depth_m=np.ascontiguousarray(depth),
        confidence=np.ascontiguousarray(confidence),
        k_rgb=k_rgb,
        arkit_pose=pose,
    )
