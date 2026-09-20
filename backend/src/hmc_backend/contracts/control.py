"""Strict Pydantic models for the JSON text control protocol (protocol v1).

Every model forbids unknown fields so a misspelling is a versioned validation
error rather than a silent change. These validate untrusted boundaries; trusted
bulk data uses the frozen dataclasses in :mod:`hmc_backend.contracts.internal`.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from hmc_backend.contracts.enums import HealthStatus, ImageOrientation, Mode

PROTOCOL_VERSION = 1


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- Phone connection -----------------------------------------------------

class ClientHello(_Strict):
    type: Literal["client_hello"]
    protocol_version: Literal[1]
    device_id: str
    session_id: UUID
    app_version: str
    platform: str
    supports_scene_depth: bool
    image_orientation: ImageOrientation


class ServerHello(_Strict):
    type: Literal["server_hello"] = "server_hello"
    protocol_version: Literal[1] = 1
    server_session_id: UUID
    accepted_device_id: str
    max_binary_bytes: int
    clock_probe_interval_s: float


# --- Clock synchronization -------------------------------------------------

class ClockPing(_Strict):
    type: Literal["clock_ping"] = "clock_ping"
    protocol_version: Literal[1] = 1
    request_id: UUID
    backend_send_time_s: float


class ClockPong(_Strict):
    type: Literal["clock_pong"]
    protocol_version: Literal[1]
    request_id: UUID
    backend_send_time_s: float
    phone_receive_time_s: float
    phone_send_time_s: float


# --- Capture + generic results --------------------------------------------

class CaptureRequest(_Strict):
    type: Literal["capture_request"] = "capture_request"
    protocol_version: Literal[1] = 1
    request_id: UUID
    capture_id: UUID
    mode: Mode
    not_before_phone_time_s: float | None = None


class Ack(_Strict):
    type: Literal["ack"] = "ack"
    protocol_version: Literal[1] = 1
    request_id: UUID | None = None
    accepted: bool
    code: str
    detail: str | None = None


class Error(_Strict):
    type: Literal["error"] = "error"
    protocol_version: Literal[1] = 1
    request_id: UUID | None = None
    code: str
    message: str
    retryable: bool = False


# --- Minecraft subscriber --------------------------------------------------

class CharacterHello(_Strict):
    type: Literal["character_hello"]
    protocol_version: Literal[1]
    client_id: str
    last_frame_id: int | None = None


class CharacterServerHello(_Strict):
    type: Literal["character_server_hello"] = "character_server_hello"
    protocol_version: Literal[1] = 1
    server_session_id: UUID
    max_binary_bytes: int
    latest_frame_id: int | None = None


class CharacterAck(_Strict):
    type: Literal["character_ack"]
    protocol_version: Literal[1]
    frame_id: int
    accepted: bool
    code: str
    detail: str | None = None


class RequestCapture(_Strict):
    type: Literal["request_capture"]
    protocol_version: Literal[1]
    request_id: UUID
    capture_id: UUID
    mode: Mode


# --- Hacker Badge controller subscriber ---------------------------------

class ControllerHello(_Strict):
    type: Literal["controller_hello"]
    protocol_version: Literal[1]
    client_id: str


# --- HTTP health -----------------------------------------------------------

class CalibrationHealth(_Strict):
    loaded: bool
    calibration_id: UUID | None = None


class DeviceHealth(_Strict):
    connected: bool
    clock_ready: bool
    queue_depth: int


class HealthResponse(_Strict):
    status: HealthStatus
    protocol_version: Literal[1] = 1
    calibration: CalibrationHealth
    models: dict[str, str]
    devices: dict[str, DeviceHealth]
    latest_frame_id: int | None = None
