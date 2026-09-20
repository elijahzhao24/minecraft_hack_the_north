"""AppRuntime: in-memory state and the capture->publish orchestration.

Holds the calibration, pipeline, per-device connection/clock state, the snapshot
store, and the character hub. WebSocket handlers call into this; the CPU-heavy
processing runs off the event loop via ``asyncio.to_thread``. All mutable state
here is touched on the event loop only, matching the single-worker model.
"""

from __future__ import annotations

import asyncio
import time
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
from hmc_backend.contracts.internal import CharacterFrame, TraceContext
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
        self._published_since_aggregate = 0
        self._aggregate_point_total = 0
        self._last_aggregate_s = time.perf_counter()

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

        with transaction(
            "character.snapshot",
            op="capture.process",
            sentry_trace=frame.trace.sentry_trace,
            baggage=frame.trace.baggage,
            source_frame_id=str(frame.source_frame_id or frame.capture_id),
            camera_id=device_id,
            payload_size=len(raw),
        ) as txn:
            queue_depth = self._pairer.pending_depth(device_id)
            with span("hmc.pair", "offer frame to bounded pairing queue", queue_depth=queue_depth):
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
            now = time.perf_counter()
            waits = [
                max(0.0, now - source.received_monotonic_s) * 1000
                for source in (pair.first, pair.second)
                if source.received_monotonic_s is not None
            ]
            txn.set_data("fusion_id", str(pair.pair_id))
            txn.set_data("calibration_version", str(self._calibration.calibration_id))
            txn.set_data("pair_skew_ms", round(pair.pair_skew_ms, 2))
            if waits:
                # Derived only from this process's monotonic clock.
                txn.set_data("pair_queue_wait_ms_max", round(max(waits), 3))
            propagated = txn.propagation_headers()
            output_trace = TraceContext(
                sentry_trace=propagated.get("sentry-trace"),
                baggage=propagated.get("baggage"),
            )
            # Run the CPU-heavy stages off the event loop.
            character = await asyncio.to_thread(
                self._processor.process, pair, mode=mode, trace=output_trace
            )
            txn.set_data("frame_id", character.frame_id)
            txn.set_data("point_count", character.quality.point_count)
            with span("hmc.character_serialize", "encode CHARACTER_FRAME") as serialize_span:
                encoded = await asyncio.to_thread(encode_character_frame, character)
                serialize_span.set_data("payload_size", len(encoded))
            # Publish only after successful serialization (all-or-nothing).
            with span("hmc.character_publish", "store + latest-wins broadcast"):
                self._store.publish(character, encoded)
                self._hub.broadcast(encoded)

        self._log_publication(character, len(encoded))
        return character

    def _log_publication(self, character: CharacterFrame, payload_size: int) -> None:
        self._published_since_aggregate += 1
        self._aggregate_point_total += character.quality.point_count
        now = time.perf_counter()
        if character.mode != "live" or now - self._last_aggregate_s >= 30:
            count = self._published_since_aggregate
            log_event(
                "info",
                "character_publish_aggregate",
                frame_id=character.frame_id,
                fusion_id=str(character.fusion_id),
                calibration_version=str(character.calibration_id),
                frames=count,
                mean_point_count=round(self._aggregate_point_total / count),
                payload_size=payload_size,
                aggregate_period_s=round(now - self._last_aggregate_s, 3),
            )
            self._published_since_aggregate = 0
            self._aggregate_point_total = 0
            self._last_aggregate_s = now

    # --- health -----------------------------------------------------------

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
        models = {"pose": "ready", "hands": "ready"} if self.is_ready() else {"pose": "not_loaded", "hands": "not_loaded"}

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
