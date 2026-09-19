"""HMC1 binary envelope framing.

Every binary WebSocket message is exactly one envelope::

    | 0  | 4 | ASCII      | magic, exactly b"HMC1"          |
    | 4  | 2 | uint16_le  | envelope version, exactly 1     |
    | 6  | 2 | uint16_le  | message type                    |
    | 8  | 4 | uint32_le  | JSON header byte length         |
    | 12 | 4 | uint32_le  | binary payload byte length      |
    | 16 | .. | UTF-8     | JSON header (no BOM)            |
    | .. | .. | bytes     | concatenated binary buffers    |

Decoding follows the strict order mandated by ``docs/contracts.md`` §2: sizes
and magic are validated *before* any allocation, the total length must match
exactly, the header is decoded as strict UTF-8, and buffer descriptors are
validated for range/overlap/contiguity before any buffer bytes are read.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from enum import IntEnum

MAGIC = b"HMC1"
ENVELOPE_VERSION = 1
HEADER_STRUCT = struct.Struct("<4sHHII")  # magic, version, type, header_len, payload_len
FIXED_PREFIX_LEN = HEADER_STRUCT.size  # 16

# v1 limits (docs/contracts.md §2).
MAX_HEADER_BYTES = 65_536
MAX_RGBD_PAYLOAD_BYTES = 16 * 1024 * 1024
MAX_CHARACTER_PAYLOAD_BYTES = 8 * 1024 * 1024


class MessageType(IntEnum):
    """HMC1 message type discriminator."""

    RGBD_FRAME = 1
    CHARACTER_FRAME = 2


class EnvelopeError(ValueError):
    """Raised when an HMC1 envelope is malformed.

    ``code`` is a stable machine-readable value suitable for an ``error``
    control message; the message text is for humans and may change.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _max_payload_for_type(message_type: int) -> int:
    if message_type == MessageType.RGBD_FRAME:
        return MAX_RGBD_PAYLOAD_BYTES
    if message_type == MessageType.CHARACTER_FRAME:
        return MAX_CHARACTER_PAYLOAD_BYTES
    # Unknown types are rejected before this is consulted.
    return MAX_RGBD_PAYLOAD_BYTES


@dataclass(frozen=True, slots=True)
class Envelope:
    """A decoded HMC1 envelope: message type, parsed header, and raw payload.

    ``payload`` is the concatenated buffer region as raw bytes. Buffer
    descriptors in ``header["buffers"]`` describe how to slice it; use
    :mod:`hmc_backend.protocol.buffers` to validate and extract them.
    """

    message_type: MessageType
    header: dict
    payload: bytes

    @property
    def raw(self) -> bytes:
        """Re-encode this envelope to its canonical byte form."""
        return encode_envelope(self.message_type, self.header, self.payload)


def encode_envelope(message_type: MessageType | int, header: dict, payload: bytes) -> bytes:
    """Encode a header dict and payload bytes into one HMC1 message.

    The header is serialized as compact UTF-8 JSON. Size limits are enforced so
    the encoder cannot emit a message its own decoder would reject.
    """
    header_bytes = json.dumps(header, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(header_bytes) > MAX_HEADER_BYTES:
        raise EnvelopeError("frame_too_large", "JSON header exceeds 65536 bytes")

    max_payload = _max_payload_for_type(int(message_type))
    if len(payload) > max_payload:
        raise EnvelopeError("frame_too_large", "binary payload exceeds limit for message type")

    prefix = HEADER_STRUCT.pack(
        MAGIC,
        ENVELOPE_VERSION,
        int(message_type),
        len(header_bytes),
        len(payload),
    )
    return prefix + header_bytes + payload


def decode_envelope(data: bytes) -> Envelope:
    """Decode one HMC1 message, validating framing before allocation.

    Raises :class:`EnvelopeError` with a stable ``code`` on any violation.
    """
    # 1. Require at least the fixed prefix.
    if len(data) < FIXED_PREFIX_LEN:
        raise EnvelopeError("invalid_message", "message shorter than 16-byte prefix")

    magic, version, message_type, header_len, payload_len = HEADER_STRUCT.unpack_from(data, 0)

    # 2. Validate magic / version / type / size limits BEFORE allocating.
    if magic != MAGIC:
        raise EnvelopeError("invalid_message", "bad magic; expected HMC1")
    if version != ENVELOPE_VERSION:
        raise EnvelopeError("unsupported_version", f"unsupported envelope version {version}")
    if message_type not in (MessageType.RGBD_FRAME, MessageType.CHARACTER_FRAME):
        raise EnvelopeError("invalid_message", f"unknown message type {message_type}")
    if header_len > MAX_HEADER_BYTES:
        raise EnvelopeError("frame_too_large", "declared header length exceeds 65536 bytes")
    if payload_len > _max_payload_for_type(message_type):
        raise EnvelopeError("frame_too_large", "declared payload length exceeds limit")

    # 3. Total length must match exactly.
    expected = FIXED_PREFIX_LEN + header_len + payload_len
    if len(data) != expected:
        raise EnvelopeError(
            "invalid_message",
            f"length mismatch: have {len(data)}, declared {expected}",
        )

    # 4. Strict UTF-8 + JSON object for the header.
    header_start = FIXED_PREFIX_LEN
    header_end = header_start + header_len
    header_slice = data[header_start:header_end]
    try:
        header_text = header_slice.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EnvelopeError("invalid_message", "header is not valid UTF-8") from exc
    try:
        header = json.loads(header_text)
    except json.JSONDecodeError as exc:
        raise EnvelopeError("invalid_message", "header is not valid JSON") from exc
    if not isinstance(header, dict):
        raise EnvelopeError("invalid_message", "header JSON must be an object")

    # 5. Payload region (buffer-descriptor validation happens in buffers.py).
    payload = bytes(data[header_end:])

    return Envelope(
        message_type=MessageType(message_type),
        header=header,
        payload=payload,
    )
