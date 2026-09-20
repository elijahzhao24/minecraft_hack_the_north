"""Strict Pydantic model for the ``RGBD_FRAME`` (message type 1) JSON header.

This validates the untrusted header a phone sends alongside its binary payload.
Buffer descriptors are validated separately by
:mod:`hmc_backend.protocol.buffers`; here we validate the metadata and cross-
check declared raster dimensions.
"""

from __future__ import annotations

import math
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from hmc_backend.contracts.enums import ImageOrientation, TrackingState

_DIM_MIN, _DIM_MAX = 1, 8192


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _all_finite(values: list[float], label: str) -> list[float]:
    for v in values:
        if not math.isfinite(v):
            raise ValueError(f"{label} contains a non-finite value")
    return values


class RgbInfo(_Strict):
    width: int
    height: int
    intrinsics_row_major: list[float]

    @field_validator("width", "height")
    @classmethod
    def _dim_range(cls, v: int) -> int:
        if not (_DIM_MIN <= v <= _DIM_MAX):
            raise ValueError(f"dimension {v} out of range [1, 8192]")
        return v

    @field_validator("intrinsics_row_major")
    @classmethod
    def _intrinsics_shape(cls, v: list[float]) -> list[float]:
        if len(v) != 9:
            raise ValueError("intrinsics_row_major must have exactly 9 elements (3x3 row-major)")
        return _all_finite(v, "intrinsics")


class DepthInfo(_Strict):
    width: int
    height: int
    unit: Literal["meter"]
    confidence_encoding: str | None = None

    @field_validator("width", "height")
    @classmethod
    def _dim_range(cls, v: int) -> int:
        if not (_DIM_MIN <= v <= _DIM_MAX):
            raise ValueError(f"dimension {v} out of range [1, 8192]")
        return v


class RGBDepthMappingModel(_Strict):
    method: str = "normalized_uncropped_scale"
    rgb_crop: list[int] | None = None
    depth_crop: list[int] | None = None


class BufferDescriptorModel(_Strict):
    name: str
    encoding: Literal["jpeg", "float32_le", "uint8", "xyzrgba16_le"]
    offset: int
    length: int
    shape: list[int] | None = None


class TraceContextModel(_Strict):
    sentry_trace: str | None = None
    baggage: str | None = None


class RgbdFrameHeader(_Strict):
    """The ``hmc.rgbd_frame`` header carried in an HMC1 RGBD envelope."""

    schema_name: str
    schema_version: Literal[1, 2]
    device_id: str
    session_id: UUID
    capture_id: UUID
    sequence: int
    capture_timestamp_s: float
    image_orientation: ImageOrientation
    mirrored: bool = False
    tracking_state: TrackingState
    rgb: RgbInfo
    depth: DepthInfo
    rgb_depth_mapping: RGBDepthMappingModel | None = None
    t_arkit_world_from_camera_row_major: list[float]
    buffers: list[BufferDescriptorModel]
    source_frame_id: UUID | None = None
    trace: TraceContextModel | None = None

    # The wire uses `schema` and `T_arkit_world_from_camera_row_major`; map them.
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    @field_validator("sequence")
    @classmethod
    def _non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("sequence must be non-negative")
        return v

    @field_validator("capture_timestamp_s")
    @classmethod
    def _finite_time(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("capture_timestamp_s must be finite")
        return v

    @field_validator("t_arkit_world_from_camera_row_major")
    @classmethod
    def _pose_shape(cls, v: list[float]) -> list[float]:
        if len(v) != 16:
            raise ValueError("ARKit pose must have exactly 16 elements (4x4 row-major)")
        return _all_finite(v, "arkit_pose")


def parse_rgbd_header(raw: dict) -> RgbdFrameHeader:
    """Validate a decoded RGBD header dict.

    The wire spells the schema key ``schema`` and the pose key with a leading
    capital ``T_``; these are remapped to model field names before validation.
    """
    data = dict(raw)
    if "schema" in data:
        data["schema_name"] = data.pop("schema")
    if "T_arkit_world_from_camera_row_major" in data:
        data["t_arkit_world_from_camera_row_major"] = data.pop(
            "T_arkit_world_from_camera_row_major"
        )
    return RgbdFrameHeader.model_validate(data)
