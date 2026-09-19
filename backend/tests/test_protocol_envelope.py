"""Unit and property tests for the HMC1 envelope + buffer codec."""

from __future__ import annotations

import json
import struct

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from hmc_backend.protocol import (
    ENVELOPE_VERSION,
    MAGIC,
    EnvelopeError,
    MessageType,
    decode_envelope,
    encode_envelope,
    load_buffers,
)
from hmc_backend.protocol.buffers import parse_descriptors, validate_descriptors


def _rgbd_header(depth: bytes, confidence: bytes) -> dict:
    return {
        "schema": "hmc.rgbd_frame",
        "buffers": [
            {"name": "depth", "encoding": "float32_le", "offset": 0, "length": len(depth), "shape": [1, 1]},
            {
                "name": "confidence",
                "encoding": "uint8",
                "offset": len(depth),
                "length": len(confidence),
                "shape": [1, 1],
            },
        ],
    }


def test_roundtrip_encode_decode():
    depth = np.float32(1.5).tobytes()
    confidence = bytes([2])
    header = _rgbd_header(depth, confidence)
    payload = depth + confidence

    raw = encode_envelope(MessageType.RGBD_FRAME, header, payload)
    env = decode_envelope(raw)

    assert env.message_type is MessageType.RGBD_FRAME
    assert env.header["schema"] == "hmc.rgbd_frame"
    assert env.payload == payload

    buffers = load_buffers(env.header, env.payload)
    assert buffers.array("depth").dtype == np.float32
    assert float(buffers.array("depth")[0, 0]) == pytest.approx(1.5)
    assert int(buffers.array("confidence")[0, 0]) == 2


def test_short_message_rejected():
    with pytest.raises(EnvelopeError) as exc:
        decode_envelope(b"HMC1")
    assert exc.value.code == "invalid_message"


def test_bad_magic_rejected():
    header = json.dumps({"buffers": [{"name": "x", "encoding": "jpeg", "offset": 0, "length": 0}]}).encode()
    bad = struct.pack("<4sHHII", b"XXXX", 1, 1, len(header), 0) + header
    with pytest.raises(EnvelopeError) as exc:
        decode_envelope(bad)
    assert exc.value.code == "invalid_message"


def test_unsupported_version_rejected():
    header = json.dumps({"buffers": [{"name": "x", "encoding": "jpeg", "offset": 0, "length": 0}]}).encode()
    bad = struct.pack("<4sHHII", MAGIC, ENVELOPE_VERSION + 1, 1, len(header), 0) + header
    with pytest.raises(EnvelopeError) as exc:
        decode_envelope(bad)
    assert exc.value.code == "unsupported_version"


def test_unknown_message_type_rejected():
    header = json.dumps({"buffers": [{"name": "x", "encoding": "jpeg", "offset": 0, "length": 0}]}).encode()
    bad = struct.pack("<4sHHII", MAGIC, ENVELOPE_VERSION, 99, len(header), 0) + header
    with pytest.raises(EnvelopeError) as exc:
        decode_envelope(bad)
    assert exc.value.code == "invalid_message"


def test_length_mismatch_rejected():
    header = json.dumps({"buffers": [{"name": "x", "encoding": "jpeg", "offset": 0, "length": 0}]}).encode()
    # Declares 4 payload bytes but supplies none.
    bad = struct.pack("<4sHHII", MAGIC, ENVELOPE_VERSION, 1, len(header), 4) + header
    with pytest.raises(EnvelopeError) as exc:
        decode_envelope(bad)
    assert exc.value.code == "invalid_message"


def test_truncated_payload_rejected():
    depth = np.float32(1.0).tobytes()
    header = _rgbd_header(depth, b"\x01")
    payload = depth + b"\x01"
    raw = encode_envelope(MessageType.RGBD_FRAME, header, payload)
    with pytest.raises(EnvelopeError):
        decode_envelope(raw[:-1])


def test_invalid_utf8_header_rejected():
    header_bytes = b"\xff\xfe not utf8"
    bad = struct.pack("<4sHHII", MAGIC, ENVELOPE_VERSION, 1, len(header_bytes), 0) + header_bytes
    with pytest.raises(EnvelopeError) as exc:
        decode_envelope(bad)
    assert exc.value.code == "invalid_message"


def test_overlapping_buffers_rejected():
    header = {
        "buffers": [
            {"name": "a", "encoding": "uint8", "offset": 0, "length": 4, "shape": [4]},
            {"name": "b", "encoding": "uint8", "offset": 2, "length": 4, "shape": [4]},
        ]
    }
    descriptors = parse_descriptors(header)
    with pytest.raises(EnvelopeError) as exc:
        validate_descriptors(descriptors, 6)
    assert exc.value.code == "invalid_buffer_range"


def test_non_contiguous_buffers_rejected():
    header = {
        "buffers": [
            {"name": "a", "encoding": "uint8", "offset": 0, "length": 2, "shape": [2]},
            {"name": "b", "encoding": "uint8", "offset": 4, "length": 2, "shape": [2]},
        ]
    }
    descriptors = parse_descriptors(header)
    with pytest.raises(EnvelopeError):
        validate_descriptors(descriptors, 6)


def test_shape_length_mismatch_rejected():
    header = {
        "buffers": [
            {"name": "depth", "encoding": "float32_le", "offset": 0, "length": 8, "shape": [4]},
        ]
    }
    descriptors = parse_descriptors(header)
    with pytest.raises(EnvelopeError) as exc:
        validate_descriptors(descriptors, 8)
    assert exc.value.code == "invalid_buffer_range"


def test_encoder_rejects_nan_in_header():
    with pytest.raises(ValueError):
        encode_envelope(MessageType.RGBD_FRAME, {"x": float("nan"), "buffers": []}, b"")


# --- Property tests -------------------------------------------------------

@given(
    lengths=st.lists(st.integers(min_value=1, max_value=64), min_size=1, max_size=5),
    payload_slack=st.integers(min_value=-4, max_value=8),
)
def test_property_validation_matches_coverage(lengths, payload_slack):
    """Contiguous uint8 descriptors validate iff the payload covers them exactly."""
    offset = 0
    buffers = []
    for i, length in enumerate(lengths):
        buffers.append(
            {"name": f"b{i}", "encoding": "uint8", "offset": offset, "length": length, "shape": [length]}
        )
        offset += length
    total = offset
    descriptors = parse_descriptors({"buffers": buffers})

    payload_len = total + payload_slack
    if payload_len == total:
        validate_descriptors(descriptors, payload_len)  # exact: must pass
    else:
        with pytest.raises(EnvelopeError):
            validate_descriptors(descriptors, payload_len)


@given(data=st.binary(min_size=0, max_size=15))
def test_property_short_inputs_always_rejected(data):
    with pytest.raises(EnvelopeError):
        decode_envelope(data)
