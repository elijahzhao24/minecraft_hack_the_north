"""Buffer-descriptor validation and typed extraction.

Buffer descriptors live in the envelope header and describe how to slice the
concatenated binary payload into named, typed arrays. Descriptors are ordered
by offset and tightly concatenated: the first starts at 0, every next offset
equals the previous end, and the final end equals the payload length. Nothing
is read out of the payload until every descriptor has passed validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
from numpy.typing import NDArray

from hmc_backend.protocol.envelope import EnvelopeError


class BufferEncoding(str, Enum):
    """Known buffer encodings for protocol v1."""

    JPEG = "jpeg"
    FLOAT32_LE = "float32_le"
    UINT8 = "uint8"
    XYZRGBA16_LE = "xyzrgba16_le"


# Bytes per element for fixed-size encodings (JPEG is variable-length).
_ELEMENT_SIZE = {
    BufferEncoding.FLOAT32_LE: 4,
    BufferEncoding.UINT8: 1,
    BufferEncoding.XYZRGBA16_LE: 16,
}

_UINT32_MAX = 0xFFFF_FFFF


@dataclass(frozen=True, slots=True)
class BufferDescriptor:
    """One entry in the header ``buffers`` array."""

    name: str
    encoding: BufferEncoding
    offset: int
    length: int
    shape: tuple[int, ...] | None

    @classmethod
    def from_json(cls, raw: object) -> BufferDescriptor:
        if not isinstance(raw, dict):
            raise EnvelopeError("invalid_message", "buffer descriptor must be an object")

        allowed = {"name", "encoding", "offset", "length", "shape"}
        extra = set(raw) - allowed
        if extra:
            raise EnvelopeError("invalid_message", f"unknown buffer descriptor fields: {sorted(extra)}")

        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise EnvelopeError("invalid_message", "buffer descriptor name must be a non-empty string")

        encoding_raw = raw.get("encoding")
        try:
            encoding = BufferEncoding(encoding_raw)
        except ValueError as exc:
            raise EnvelopeError("invalid_message", f"unknown buffer encoding {encoding_raw!r}") from exc

        offset = _require_uint32(raw.get("offset"), "buffer offset")
        length = _require_uint32(raw.get("length"), "buffer length")

        shape_raw = raw.get("shape")
        shape: tuple[int, ...] | None
        if shape_raw is None:
            if encoding is not BufferEncoding.JPEG:
                raise EnvelopeError("invalid_message", f"{name}: shape required for {encoding.value}")
            shape = None
        else:
            if not isinstance(shape_raw, list) or not shape_raw:
                raise EnvelopeError("invalid_message", f"{name}: shape must be a non-empty array")
            dims: list[int] = []
            for dim in shape_raw:
                dims.append(_require_uint32(dim, f"{name} shape dim", positive=True))
            shape = tuple(dims)

        return cls(name=name, encoding=encoding, offset=offset, length=length, shape=shape)

    def expected_length(self) -> int | None:
        """Byte length implied by ``encoding`` and ``shape`` (None for JPEG)."""
        if self.encoding is BufferEncoding.JPEG or self.shape is None:
            return None
        count = 1
        for dim in self.shape:
            count *= dim
        return count * _ELEMENT_SIZE[self.encoding]


def _require_uint32(value: object, label: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EnvelopeError("invalid_message", f"{label} must be an integer")
    if value < 0 or value > _UINT32_MAX:
        raise EnvelopeError("invalid_message", f"{label} out of uint32 range")
    if positive and value == 0:
        raise EnvelopeError("invalid_message", f"{label} must be positive")
    return value


def parse_descriptors(header: dict) -> tuple[BufferDescriptor, ...]:
    """Parse the header ``buffers`` array into descriptors (no range checks)."""
    raw_buffers = header.get("buffers")
    if not isinstance(raw_buffers, list) or not raw_buffers:
        raise EnvelopeError("invalid_message", "header must contain a non-empty buffers array")
    return tuple(BufferDescriptor.from_json(entry) for entry in raw_buffers)


def validate_descriptors(
    descriptors: tuple[BufferDescriptor, ...], payload_length: int
) -> None:
    """Validate range, contiguity, non-overlap, and exact fixed-raster sizes.

    Enforces that descriptors are tightly concatenated with no gaps or overlap
    and cover the payload exactly. Raises :class:`EnvelopeError` on any problem.
    """
    names: set[str] = set()
    cursor = 0
    for desc in descriptors:
        if desc.name in names:
            raise EnvelopeError("invalid_buffer_range", f"duplicate buffer name {desc.name!r}")
        names.add(desc.name)

        end = desc.offset + desc.length
        if end > payload_length:
            raise EnvelopeError(
                "invalid_buffer_range", f"{desc.name} ends after payload ({end} > {payload_length})"
            )
        if desc.offset != cursor:
            raise EnvelopeError(
                "invalid_buffer_range",
                f"{desc.name} not tightly concatenated (offset {desc.offset}, expected {cursor})",
            )

        expected = desc.expected_length()
        if expected is not None and expected != desc.length:
            raise EnvelopeError(
                "invalid_buffer_range",
                f"{desc.name} length {desc.length} does not match shape {desc.shape} ({expected})",
            )

        cursor = end

    if cursor != payload_length:
        raise EnvelopeError(
            "invalid_buffer_range",
            f"buffers cover {cursor} bytes but payload is {payload_length}",
        )


class BufferSet:
    """Validated, named access to the buffers inside one payload."""

    def __init__(self, descriptors: tuple[BufferDescriptor, ...], payload: bytes) -> None:
        self._by_name = {d.name: d for d in descriptors}
        self._payload = payload

    def __contains__(self, name: str) -> bool:
        return name in self._by_name

    def descriptor(self, name: str) -> BufferDescriptor:
        try:
            return self._by_name[name]
        except KeyError as exc:
            raise EnvelopeError("invalid_message", f"missing buffer {name!r}") from exc

    def raw(self, name: str) -> bytes:
        """Return the raw bytes for a named buffer."""
        desc = self.descriptor(name)
        return self._payload[desc.offset : desc.offset + desc.length]

    def array(self, name: str) -> NDArray:
        """Decode a fixed-size buffer into a C-contiguous NumPy array.

        JPEG buffers are returned via :meth:`raw`; decode them with an image
        codec at the reconstruction boundary, not here.
        """
        desc = self.descriptor(name)
        raw = self.raw(name)
        if desc.encoding is BufferEncoding.FLOAT32_LE:
            arr = np.frombuffer(raw, dtype="<f4").astype(np.float32, copy=True)
        elif desc.encoding is BufferEncoding.UINT8:
            arr = np.frombuffer(raw, dtype=np.uint8).copy()
        elif desc.encoding is BufferEncoding.XYZRGBA16_LE:
            # Structured 16-byte record; caller reshapes/interprets fields.
            arr = np.frombuffer(raw, dtype=np.uint8).copy()
        else:  # JPEG
            raise EnvelopeError("invalid_message", f"{name}: use raw() for jpeg buffers")

        if desc.shape is not None and desc.encoding is not BufferEncoding.XYZRGBA16_LE:
            arr = arr.reshape(desc.shape)
        return np.ascontiguousarray(arr)


def load_buffers(header: dict, payload: bytes) -> BufferSet:
    """Parse, validate, and wrap the payload buffers described by ``header``."""
    descriptors = parse_descriptors(header)
    validate_descriptors(descriptors, len(payload))
    return BufferSet(descriptors, payload)
