"""AppRuntime: in-memory state and the capture->publish orchestration.

Holds the calibration, pipeline, per-device connection/clock state, the snapshot
store, and the character hub. WebSocket handlers call into this; the CPU-heavy
processing runs off the event loop via ``asyncio.to_thread``. All mutable state
here is touched on the event loop only, matching the single-worker model.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

import numpy as np

from hmc_backend.api.hub import CharacterHub
from hmc_backend.calibration.model import RigCalibration
from hmc_backend.capture.clock import ClockEstimator
from hmc_backend.capture.pairing import Pairer
from hmc_backend.capture.recording import save_recording
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
from hmc_backend.protocol.envelope import EnvelopeError, decode_envelope
from hmc_backend.settings import Settings


@dataclass
class DeviceState:
    """Per-device connection and clock state (event-loop only)."""

    device_id: str
    connected: bool = False
    token: object | None = None
    clock: ClockEstimator = field(default_factory=ClockEstimator)
    send_text: Callable[[str], Awaitable[None]] | None = None


@dataclass
class CalibrationCaptureState:
    """Raw synchronized packets collected before a rig calibration exists."""

    capture_id: UUID
    created_monotonic_s: float
    deadline_monotonic_s: float
    packets: dict[str, bytes] = field(default_factory=dict)
    state: str = "pending"
    saved_recording_path: str | None = None
    failure_code: str | None = None


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
        # capture_id -> mode for requests this backend dispatched, so a frame
        # can be tagged live/snapshot when it comes back (the RGBD header has
        # no mode field). Bounded: live mode issues several per second.
        self._capture_modes: OrderedDict[UUID, str] = OrderedDict()
        self._live_task: asyncio.Task[None] | None = None
        self._live_rate_hz: float = settings.live_rate_hz
        # Live requests dispatched but not yet paired: capture_id -> send time.
        self._live_inflight: dict[UUID, float] = {}
        self._live_paired = asyncio.Event()
        self._calibration_captures: OrderedDict[UUID, CalibrationCaptureState] = OrderedDict()

        self._pairer: Pairer | None
        self._processor: CharacterProcessor | None
        if calibration is not None:
            self._pairer = pairer or build_pairer(settings)
            self._processor = processor or build_processor(settings, calibration)
            self._load_registration()
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
        self._capture_modes[capture_id] = mode
        while len(self._capture_modes) > 256:
            self._capture_modes.popitem(last=False)
        payload = req.model_dump_json()
        for state in self._devices.values():
            assert state.send_text is not None
            await state.send_text(payload)
        return True, "capture_dispatched"

    async def request_calibration_capture(self) -> tuple[bool, dict]:
        """Request one raw synchronized pair, even when no calibration is loaded."""
        if not all(s.connected and s.send_text is not None for s in self._devices.values()):
            return False, {"code": "devices_unavailable"}
        capture_id = uuid4()
        now = time.monotonic()
        state = CalibrationCaptureState(
            capture_id=capture_id,
            created_monotonic_s=now,
            deadline_monotonic_s=now + self._settings.calibration_capture_timeout_s,
        )
        self._calibration_captures[capture_id] = state
        while len(self._calibration_captures) > 64:
            self._calibration_captures.popitem(last=False)
        request = CaptureRequest(request_id=uuid4(), capture_id=capture_id, mode=Mode.SNAPSHOT)
        payload = request.model_dump_json()
        for device in self._devices.values():
            assert device.send_text is not None
            await device.send_text(payload)
        return True, self.calibration_capture_status(capture_id) or {}

    def calibration_capture_status(self, capture_id: UUID) -> dict | None:
        state = self._calibration_captures.get(capture_id)
        if state is None:
            return None
        if state.state == "pending" and time.monotonic() > state.deadline_monotonic_s:
            state.state = "failed"
            state.failure_code = "capture_timeout"
        return {
            "capture_id": str(state.capture_id),
            "device_ids": sorted(state.packets),
            "state": state.state,
            "saved_recording_path": state.saved_recording_path,
            "failure_code": state.failure_code,
        }

    async def _record_calibration_packet(self, device_id: str, capture_id: UUID, raw: bytes) -> None:
        state = self._calibration_captures[capture_id]
        self.calibration_capture_status(capture_id)
        if state.state != "pending":
            return
        if device_id in state.packets:
            state.state = "failed"
            state.failure_code = "duplicate_device_packet"
            return
        state.packets[device_id] = bytes(raw)
        if set(state.packets) != set(self._devices):
            return
        try:
            path = await asyncio.to_thread(
                save_recording,
                self._settings.recording_root,
                capture_id,
                state.packets,
                backend_release=self._settings.release,
                consent_note="calibration board capture; may contain surrounding RGB/depth data",
            )
        except OSError:
            state.state = "failed"
            state.failure_code = "recording_write_failed"
            return
        state.state = "complete"
        state.saved_recording_path = str(path.resolve())

    # --- rig registration -------------------------------------------------

    def request_registration(self) -> bool:
        """Align the side camera onto the front one using the next paired frame."""
        if not self._settings.enable_person_registration or self._processor is None:
            return False
        self._processor.request_registration()
        self._registration_dirty = True
        return True

    def clear_registration(self) -> None:
        if self._processor is not None:
            self._processor.set_corrections({})
            self._processor.last_registration = None
        path = Path(self._settings.registration_path)
        if path.exists():
            path.unlink()
        log_event("info", "rig_registration_cleared")

    def registration_status(self) -> dict:
        if self._processor is None:
            return {"corrections": {}, "last": None}
        return {
            "corrections": {k: v.reshape(-1).tolist() for k, v in self._processor.corrections.items()},
            "last": self._processor.last_registration,
        }

    def _load_registration(self) -> None:
        self._registration_dirty = False
        if not self._settings.enable_person_registration:
            return
        path = Path(self._settings.registration_path)
        if not path.exists() or self._processor is None:
            return
        try:
            data = json.loads(path.read_text())
            if self._calibration is None or data.get("calibration_id") != str(self._calibration.calibration_id):
                log_event("warning", "rig_registration_ignored", reason="calibration_id_mismatch")
                return
            self._processor.set_corrections(
                {k: np.array(v, np.float64).reshape(4, 4) for k, v in data.get("corrections", {}).items()}
            )
            log_event("info", "rig_registration_loaded", devices=list(data.get("corrections", {})))
        except (ValueError, OSError) as exc:
            log_event("warning", "rig_registration_load_failed", error=str(exc))

    def _save_registration_if_dirty(self) -> None:
        if not getattr(self, "_registration_dirty", False) or self._processor is None:
            return
        last = self._processor.last_registration
        if last is None or not last.get("ok"):
            return
        self._registration_dirty = False
        path = Path(self._settings.registration_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "calibration_id": str(self._calibration.calibration_id) if self._calibration else None,
            "corrections": {k: v.reshape(-1).tolist() for k, v in self._processor.corrections.items()},
            "last": last,
        }, indent=2))

    # --- live mode --------------------------------------------------------

    @property
    def live_active(self) -> bool:
        return self._live_task is not None and not self._live_task.done()

    def start_live(self, rate_hz: float | None = None) -> None:
        """Drive both phones with live capture_requests at ``rate_hz``.

        Both devices answer with the same capture_id, so pairing is unchanged
        from snapshot mode; the phones never need to invent their own ids.
        """
        if rate_hz is not None and rate_hz > 0:
            self._live_rate_hz = min(rate_hz, 30.0)
        if self.live_active:
            return
        self._live_task = asyncio.create_task(self._live_loop())
        log_event("info", "live_started", rate_hz=self._live_rate_hz)

    def stop_live(self) -> None:
        task = self._live_task
        self._live_task = None
        if task is not None and not task.done():
            task.cancel()
            log_event("info", "live_stopped")

    async def _live_loop(self) -> None:
        """Self-pacing request loop.

        ``live_rate_hz`` is a ceiling. At most two requests are in flight; the
        next goes out when one pairs (or is written off after a second), so a
        slow phone is never asked for more than it can deliver and its queue
        never overflows. That overflow is what stalled the first live attempt.
        """
        max_inflight = 2
        self._live_inflight.clear()
        try:
            while True:
                interval = 1.0 / self._live_rate_hz
                now = time.monotonic()
                for cid, sent in list(self._live_inflight.items()):
                    if now - sent > 1.0:
                        del self._live_inflight[cid]
                if len(self._live_inflight) >= max_inflight:
                    self._live_paired.clear()
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(self._live_paired.wait(), timeout=0.25)
                    continue
                capture_id = uuid4()
                accepted, _ = await self.dispatch_capture(capture_id, "live")
                if accepted:
                    self._live_inflight[capture_id] = time.monotonic()
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
        finally:
            self._live_inflight.clear()

    def is_expected_device(self, device_id: str) -> bool:
        return device_id in self._devices

    def on_clock_pong(self, device_id: str, t0: float, t1: float, t2: float, t3: float) -> None:
        state = self._devices.get(device_id)
        if state is not None:
            state.clock.record_pong("pong", t0, t1, t2, t3)

    # --- capture ingest ---------------------------------------------------

    async def handle_rgbd(
        self,
        raw: bytes,
        *,
        mode: str = "snapshot",
        connected_device_id: str | None = None,
    ) -> CharacterFrame | None:
        """Decode a packet, pair it, and (on a completed pair) publish a frame."""
        env = decode_envelope(raw)
        decoded = decode_rgbd_frame(env)
        device_id = decoded.header.device_id
        if connected_device_id is not None and connected_device_id != device_id:
            raise EnvelopeError("unauthorized_device", "packet device_id does not match connection")
        calibration_capture = self._calibration_captures.get(decoded.header.capture_id)
        if calibration_capture is not None:
            await self._record_calibration_packet(device_id, decoded.header.capture_id, raw)
            return None
        if self._pairer is None or self._processor is None or self._calibration is None:
            return None
        camera = self._calibration.camera(device_id)
        header = decoded.header
        if header.image_orientation.value != camera.image_orientation:
            raise EnvelopeError("calibration_mismatch", "image orientation differs from calibration")
        if (header.rgb.width, header.rgb.height) != camera.rgb_size:
            raise EnvelopeError("calibration_mismatch", "RGB dimensions differ from calibration")
        if (header.depth.width, header.depth.height) != camera.depth_size:
            raise EnvelopeError("calibration_mismatch", "depth dimensions differ from calibration")
        mode = self._capture_modes.get(decoded.header.capture_id, mode)
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
            log_event(
                "info",
                "rgbd_received",
                device_id=device_id,
                capture_id=str(decoded.header.capture_id)[:8],
                mode=mode,
                paired=outcome.paired is not None,
                reason=outcome.rejected_reason,
            )
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
            if self._live_inflight.pop(pair.first.capture_id, None) is not None:
                self._live_paired.set()
            started = time.perf_counter()
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
            with span("collider.fit_and_reconstruct", "detect+reconstruct+fit"):
                character = await asyncio.to_thread(
                    self._processor.process, pair, mode=mode, trace=output_trace
                )
            self._save_registration_if_dirty()
            if mode == "live" and character.quality.point_count == 0:
                # Nobody in frame; publishing an empty live frame only makes
                # the client log a decode error and blank the figure.
                log_event("info", "live_frame_empty", capture_id=str(pair.first.capture_id))
                return None
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
        log_event(
            "info",
            "character_published",
            frame_id=character.frame_id,
            calibration_id=str(character.calibration_id),
            point_count=character.quality.point_count,
            valid_collider_count=character.quality.valid_collider_count,
            pair_skew_ms=round(pair.pair_skew_ms, 2),
            mode=mode,
            processing_ms=round((time.perf_counter() - started) * 1000.0, 1),
        )
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
        if self.is_ready():
            models = {
                "pose": f"ready:{self._settings.vision_backend}",
                "hands": f"ready:{self._settings.vision_backend}",
                "colliders": f"ready:{self._settings.collider_backend}",
            }
        else:
            models = {"pose": "not_loaded", "hands": "not_loaded", "colliders": "not_loaded"}

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
