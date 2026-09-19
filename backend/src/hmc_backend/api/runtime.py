"""AppRuntime: in-memory state and the capture->publish orchestration.

Holds the calibration, pipeline, per-device connection/clock state, the snapshot
store, and the character hub. WebSocket handlers call into this; the CPU-heavy
processing runs off the event loop via ``asyncio.to_thread``. All mutable state
here is touched on the event loop only, matching the single-worker model.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from hmc_backend.api.hub import CharacterHub
from hmc_backend.calibration.model import RigCalibration
from hmc_backend.capture.clock import ClockEstimator
from hmc_backend.capture.pairing import Pairer
from hmc_backend.capture.replay import captured_frame_from_decoded
from hmc_backend.capture.rgbd_ingest import decode_rgbd_frame
from hmc_backend.contracts.character_codec import encode_character_frame
from hmc_backend.contracts.control import (
    CalibrationHealth,
    CaptureRequest,
    DeviceHealth,
    HealthResponse,
)
from hmc_backend.contracts.enums import HealthStatus, Mode
from hmc_backend.contracts.internal import CharacterFrame
from hmc_backend.observability import log_event, span, transaction
from hmc_backend.pipeline.factory import build_pairer, build_processor
from hmc_backend.pipeline.processor import CharacterProcessor
from hmc_backend.pipeline.snapshot_store import SnapshotStore
from hmc_backend.protocol.envelope import decode_envelope
from hmc_backend.settings import Settings


@dataclass
class DeviceState:
    """Per-device connection and clock state (event-loop only)."""

    device_id: str
    connected: bool = False
    token: object | None = None
    clock: ClockEstimator = field(default_factory=ClockEstimator)
    send_text: Callable[[str], Awaitable[None]] | None = None


class AppRuntime:
    """Owns the pipeline and connection state for the process lifetime."""

    def __init__(
        self,
        settings: Settings,
        calibration: RigCalibration | None,
        *,
        processor: CharacterProcessor | None = None,
        pairer: Pairer | None = None,
        store: SnapshotStore | None = None,
    ) -> None:
        self._settings = settings
        self._calibration = calibration
        self._server_session_id = uuid4()
        self._store = store or SnapshotStore()
        self._hub = CharacterHub()
        self._lock = asyncio.Lock()

        self._devices = {dev: DeviceState(dev) for dev in settings.expected_device_ids}

        if calibration is not None:
            self._pairer = pairer or build_pairer(settings)
            self._processor = processor or build_processor(settings, calibration)
        else:
            self._pairer = None
            self._processor = None

    # --- accessors --------------------------------------------------------

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def server_session_id(self) -> UUID:
        return self._server_session_id

    @property
    def store(self) -> SnapshotStore:
        return self._store

    @property
    def hub(self) -> CharacterHub:
        return self._hub

    def is_ready(self) -> bool:
        return self._calibration is not None and self._processor is not None

    # --- capture device lifecycle ----------------------------------------

    def register_capture(
        self, device_id: str, send_text: Callable[[str], Awaitable[None]] | None = None
    ) -> object:
        """Mark a device connected with a fresh token; clears stale pairing."""
        state = self._devices[device_id]
        token = object()
        state.connected = True
        state.token = token
        state.clock = ClockEstimator()
        state.send_text = send_text
        if self._pairer is not None:
            self._pairer.clear_device(device_id)
        return token

    def unregister_capture(self, device_id: str, token: object) -> None:
        """Disconnect a device only if the token still matches (reconnect-safe)."""
        state = self._devices.get(device_id)
        if state is not None and state.token is token:
            state.connected = False
            state.token = None
            state.send_text = None

    async def dispatch_capture(self, capture_id: UUID, mode: str) -> tuple[bool, str]:
        """Forward a capture_request to both phones if the rig can accept one."""
        if not self.is_ready():
            return False, "not_ready"
        if not all(s.connected and s.send_text is not None for s in self._devices.values()):
            return False, "devices_unavailable"
        req = CaptureRequest(request_id=uuid4(), capture_id=capture_id, mode=Mode(mode))
        payload = req.model_dump_json()
        for state in self._devices.values():
            assert state.send_text is not None
            await state.send_text(payload)
        return True, "capture_dispatched"

    def is_expected_device(self, device_id: str) -> bool:
        return device_id in self._devices

    def on_clock_pong(self, device_id: str, t0: float, t1: float, t2: float, t3: float) -> None:
        state = self._devices.get(device_id)
        if state is not None:
            state.clock.record_pong("pong", t0, t1, t2, t3)

    # --- capture ingest ---------------------------------------------------

    async def handle_rgbd(self, raw: bytes, *, mode: str = "snapshot") -> CharacterFrame | None:
        """Decode a packet, pair it, and (on a completed pair) publish a frame."""
        if self._pairer is None or self._processor is None or self._calibration is None:
            return None

        env = decode_envelope(raw)
        decoded = decode_rgbd_frame(env)
        device_id = decoded.header.device_id
        state = self._devices.get(device_id)
        if state is None:
            return None

        offset = state.clock.offset_s() or 0.0
        uncertainty = state.clock.uncertainty_ms()
        if uncertainty is None:
            uncertainty = 0.0  # no probes yet; treated as synchronized for the MVP
        frame = captured_frame_from_decoded(
            decoded, clock_offset_s=offset, clock_uncertainty_ms=uncertainty
        )

        outcome = self._pairer.offer(frame, self._calibration.calibration_id)
        if outcome.paired is None:
            if outcome.rejected_reason is not None:
                log_event(
                    "warning",
                    "pair_rejected",
                    device_id=device_id,
                    capture_id=str(decoded.header.capture_id),
                    reason=outcome.rejected_reason,
                )
            return None

        pair = outcome.paired
        with transaction(
            "character.snapshot",
            op="capture.process",
            capture_id=str(pair.first.capture_id),
            calibration_id=str(self._calibration.calibration_id),
            pair_skew_ms=round(pair.pair_skew_ms, 2),
        ):
            # Run the CPU-heavy stages off the event loop.
            with span("collider.fit_and_reconstruct", "detect+reconstruct+fit"):
                character = await asyncio.to_thread(self._processor.process, pair, mode=mode)
            with span("character.serialize", "encode CHARACTER_FRAME"):
                encoded = await asyncio.to_thread(encode_character_frame, character)
            # Publish only after successful serialization (all-or-nothing).
            with span("character.publish", "store + broadcast"):
                self._store.publish(character, encoded)
                self._hub.broadcast(encoded)

        log_event(
            "info",
            "character_published",
            frame_id=character.frame_id,
            calibration_id=str(character.calibration_id),
            point_count=character.quality.point_count,
            valid_collider_count=character.quality.valid_collider_count,
            pair_skew_ms=round(pair.pair_skew_ms, 2),
        )
        return character

    # --- health -----------------------------------------------------------

    def _model_status(self) -> dict[str, str]:
        """Describe the loaded vision stage without touching model objects."""
        if self._processor is None:
            return {"pose": "not_loaded", "hands": "not_loaded"}
        detector = self._processor.detector
        status = getattr(detector, "model_status", None)
        if callable(status):
            return dict(status())
        name = type(detector).__name__
        return {"pose": name, "hands": name}

    def health(self) -> tuple[HealthResponse, int]:
        """Build the health response and its HTTP status code."""
        calib = self._calibration
        devices = {
            dev: DeviceHealth(
                connected=state.connected,
                clock_ready=state.clock.ready,
                queue_depth=(self._pairer.pending_depth(dev) if self._pairer else 0),
            )
            for dev, state in self._devices.items()
        }
        models = self._model_status()

        if self.is_ready():
            status = HealthStatus.READY
        elif calib is None:
            status = HealthStatus.STARTING
        else:
            status = HealthStatus.DEGRADED

        response = HealthResponse(
            status=status,
            calibration=CalibrationHealth(
                loaded=calib is not None,
                calibration_id=calib.calibration_id if calib else None,
            ),
            models=models,
            devices=devices,
            latest_frame_id=self._store.latest_frame_id,
        )
        http_status = 200 if status is HealthStatus.READY else 503
        return response, http_status
