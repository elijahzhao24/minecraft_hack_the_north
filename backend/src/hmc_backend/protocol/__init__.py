"""HMC1 protocol framing and buffer-descriptor decoding."""

from hmc_backend.protocol.buffers import (
    BufferDescriptor,
    BufferEncoding,
    BufferSet,
    load_buffers,
    parse_descriptors,
    validate_descriptors,
)
from hmc_backend.protocol.envelope import (
    ENVELOPE_VERSION,
    MAGIC,
    MAX_CHARACTER_PAYLOAD_BYTES,
    MAX_HEADER_BYTES,
    MAX_RGBD_PAYLOAD_BYTES,
    Envelope,
    EnvelopeError,
    MessageType,
    decode_envelope,
    encode_envelope,
)

__all__ = [
    "ENVELOPE_VERSION",
    "MAGIC",
    "MAX_CHARACTER_PAYLOAD_BYTES",
    "MAX_HEADER_BYTES",
    "MAX_RGBD_PAYLOAD_BYTES",
    "BufferDescriptor",
    "BufferEncoding",
    "BufferSet",
    "Envelope",
    "EnvelopeError",
    "MessageType",
    "decode_envelope",
    "encode_envelope",
    "load_buffers",
    "parse_descriptors",
    "validate_descriptors",
]
