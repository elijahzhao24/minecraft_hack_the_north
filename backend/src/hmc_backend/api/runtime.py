"""In-memory capture runtime with bounded coordinated-live processing."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from hmc_backend.api.hub import CharacterHub
from hmc_backend.calibration.model import RigCalibration, rig_to_json
from hmc_backend.capture.clock import ClockEstimator
from hmc_backend.capture.pairing import Pairer
from hmc_backend.capture.recording import save_capture_packet
from hmc_backend.capture.replay import captured_frame_from_decoded
from hmc_backend.capture.rgbd_ingest import decode_rgbd_frame
from hmc_backend.contracts.character_codec import encode_character_frame
from hmc_backend.contracts.control import (
    CalibrationHealth,
    CaptureRequest,
    ClockPing,
    DeviceHealth,
    HealthResponse,
    LiveHealth,
    LiveState,
)
from hmc_backend.contracts.enums import HealthStatus, Mode
from hmc_backend.contracts.internal import CaptureGroup, CharacterFrame
from hmc_backend.observability import log_event, span, transaction
from hmc_backend.pipeline.factory import build_pairer, build_processor
from hmc_backend.pipeline.processor import CharacterProcessor
from hmc_backend.pipeline.snapshot_store import SnapshotStore
from hmc_backend.protocol.envelope import EnvelopeError, decode_envelope
from hmc_backend.settings import Settings


@dataclass
class DeviceState:
    device_id: str
    connected: bool = False
    token: object | None = None
    clock: ClockEstimator = field(default_factory=ClockEstimator)
    send_text: Callable[[str], Awaitable[None]] | None = None
    clock_task: asyncio.Task[None] | None = None
    pending_clock: dict[UUID, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PendingCapture:
    mode: Mode
    created_at: float
    device_ids: tuple[str, ...]


class AppRuntime:
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
        self._devices = {dev: DeviceState(dev) for dev in settings.expected_device_ids}
        self._pairer = (pairer or build_pairer(settings)) if calibration is not None else None
        self._processor = (
            (processor or build_processor(settings, calibration))
            if calibration is not None
            else None
        )
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hmc-processor")
        self._process_lock = asyncio.Lock()
        self._pending_captures: dict[UUID, PendingCapture] = {}
        self._live_desired = False
        self._live_state = "stopped"
        self._live_reason: str | None = None
        self._live_session_id: UUID | None = None
        self._live_outstanding: UUID | None = None
        self._live_task: asyncio.Task[None] | None = None
        self._live_worker: asyncio.Task[None] | None = None
        self._pending_live_group: CaptureGroup | None = None
        self._publish_times: deque[float] = deque(maxlen=30)
        self._last_publish_at: float | None = None
        self._missed_captures = 0
        self._dropped_pairs = 0

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

    async def shutdown(self) -> None:
        self._live_desired = False
        tasks = [
            self._live_task,
            self._live_worker,
            *(d.clock_task for d in self._devices.values()),
        ]
        for task in tasks:
            if task is not None:
                task.cancel()
        await asyncio.gather(*(t for t in tasks if t is not None), return_exceptions=True)
        if self._processor is not None and hasattr(self._processor, "close"):
            self._processor.close()
        self._executor.shutdown(wait=True, cancel_futures=True)

    def register_capture(
        self, device_id: str, send_text: Callable[[str], Awaitable[None]] | None = None
    ) -> object:
        state = self._devices[device_id]
        if state.clock_task is not None:
            state.clock_task.cancel()
        token = object()
        state.connected, state.token, state.send_text = True, token, send_text
        state.clock, state.pending_clock = ClockEstimator(), {}
        if self._pairer is not None:
            self._pairer.clear_device(device_id)
        if send_text is not None:
            state.clock_task = asyncio.create_task(self._clock_loop(device_id, token))
        return token

    def unregister_capture(self, device_id: str, token: object) -> None:
        state = self._devices.get(device_id)
        if state is None or state.token is not token:
            return
        state.connected, state.token, state.send_text = False, None, None
        if state.clock_task is not None:
            state.clock_task.cancel()
        state.clock_task = None
        if self._pairer is not None:
            self._pairer.clear_device(device_id)
        if self._live_desired:
            asyncio.create_task(self._set_live_state("paused", "device_disconnected"))

    async def _clock_loop(self, device_id: str, token: object) -> None:
        state = self._devices[device_id]
        while state.connected and state.token is token and state.send_text is not None:
            request_id, sent_at = uuid4(), time.monotonic()
            state.pending_clock[request_id] = sent_at
            if len(state.pending_clock) > 64:
                state.pending_clock.pop(next(iter(state.pending_clock)))
            try:
                await state.send_text(
                    ClockPing(request_id=request_id, backend_send_time_s=sent_at).model_dump_json()
                )
            except Exception:  # noqa: BLE001 - receive loop owns socket teardown
                return
            delay = (
                0.25
                if state.clock.sample_count < self._settings.live_clock_samples
                else self._settings.clock_probe_interval_s
            )
            await asyncio.sleep(delay)

    def on_clock_pong(
        self,
        device_id: str,
        request_id: UUID,
        phone_receive_time_s: float,
        phone_send_time_s: float,
        backend_receive_time_s: float,
    ) -> bool:
        state = self._devices.get(device_id)
        if state is None:
            return False
        sent_at = state.pending_clock.pop(request_id, None)
        if sent_at is None:
            return False
        return (
            state.clock.record_pong(
                str(request_id),
                sent_at,
                phone_receive_time_s,
                phone_send_time_s,
                backend_receive_time_s,
            )
            is not None
        )

    def _clock_ready(self, state: DeviceState) -> bool:
        return state.clock.ready_with(
            self._settings.live_clock_samples, self._settings.clock_uncertainty_limit_ms
        )

    def _capture_device_ids(self, *, require_clock: bool) -> tuple[str, ...]:
        if self._calibration is None:
            return ()
        selected = []
        for device_id, state in self._devices.items():
            if device_id not in self._calibration.cameras:
                continue
            if not state.connected or state.send_text is None:
                continue
            if require_clock and not self._clock_ready(state):
                continue
            selected.append(device_id)
        return tuple(selected)

    async def dispatch_capture(self, capture_id: UUID, mode: str) -> tuple[bool, str]:
        if not self.is_ready():
            return False, "not_ready"
        capture_mode = Mode(mode)
        device_ids = self._capture_device_ids(require_clock=capture_mode is Mode.LIVE)
        if len(device_ids) < self._settings.min_capture_devices:
            return False, "devices_unavailable"
        self._pending_captures[capture_id] = PendingCapture(
            capture_mode, time.monotonic(), device_ids
        )
        await self._send_capture_requests(
            capture_id,
            capture_mode,
            device_ids=device_ids,
            scheduled=capture_mode is Mode.LIVE,
        )
        return True, "capture_dispatched"

    async def _send_capture_requests(
        self,
        capture_id: UUID,
        mode: Mode,
        *,
        device_ids: tuple[str, ...],
        scheduled: bool,
    ) -> None:
        backend_target = time.monotonic() + self._settings.live_capture_lead_ms / 1000.0
        log_event(
            "info",
            "capture_requests_scheduled",
            capture_id=str(capture_id),
            mode=mode.value,
            device_count=len(device_ids),
            device_ids=",".join(device_ids),
            scheduled=scheduled,
        )
        for device_id in device_ids:
            state = self._devices[device_id]
            if state.send_text is None:
                raise RuntimeError("capture device disconnected")
            phone_target = None
            if scheduled:
                offset = state.clock.offset_s()
                if offset is None:
                    raise RuntimeError("capture device clock is not ready")
                phone_target = backend_target + offset
            request = CaptureRequest(
                request_id=uuid4(),
                capture_id=capture_id,
                mode=mode,
                not_before_phone_time_s=phone_target,
            )
            await state.send_text(request.model_dump_json())

    async def request_live(self, request_id: UUID, enabled: bool) -> LiveState:
        if enabled:
            self._live_desired = True
            self._live_session_id = self._live_session_id or uuid4()
            await self._set_live_state("starting", None, request_id=request_id)
            if self._live_task is None or self._live_task.done():
                self._live_task = asyncio.create_task(self._live_loop())
        else:
            self._live_desired, self._live_outstanding, self._pending_live_group = (
                False,
                None,
                None,
            )
            self._pending_captures = {
                cid: p for cid, p in self._pending_captures.items() if p.mode is Mode.SNAPSHOT
            }
            self._live_session_id = None
            await self._set_live_state("stopped", None, request_id=request_id)
        return self.live_state_message(request_id=request_id)

    def _live_prerequisite_error(self) -> str | None:
        if not self.is_ready():
            return "backend_not_ready"
        connected = self._capture_device_ids(require_clock=False)
        if len(connected) < self._settings.min_capture_devices:
            return "devices_unavailable"
        ready = self._capture_device_ids(require_clock=True)
        if len(ready) < self._settings.min_capture_devices:
            return "clock_not_ready"
        return None

    async def _live_loop(self) -> None:
        period = 1.0 / self._settings.live_target_fps
        while self._live_desired:
            reason = self._live_prerequisite_error()
            if reason is not None:
                await self._set_live_state("paused", reason)
                await asyncio.sleep(0.1)
                continue
            await self._set_live_state("running", None)
            now = time.monotonic()
            if self._live_outstanding is not None:
                pending = self._pending_captures.get(self._live_outstanding)
                if (
                    pending is not None
                    and (now - pending.created_at) * 1000 < self._settings.live_pair_timeout_ms
                ):
                    await asyncio.sleep(0.02)
                    continue
                self._pending_captures.pop(self._live_outstanding, None)
                self._live_outstanding = None
                self._missed_captures += 1
                if self._pairer is not None:
                    for device_id in self._devices:
                        self._pairer.clear_device(device_id)
            device_ids = self._capture_device_ids(require_clock=True)
            capture_id = uuid4()
            self._pending_captures[capture_id] = PendingCapture(Mode.LIVE, now, device_ids)
            self._live_outstanding = capture_id
            try:
                await self._send_capture_requests(
                    capture_id, Mode.LIVE, device_ids=device_ids, scheduled=True
                )
            except Exception as exc:  # noqa: BLE001 - transition live state to paused
                self._pending_captures.pop(capture_id, None)
                self._live_outstanding = None
                await self._set_live_state("paused", str(exc))
            await asyncio.sleep(period)

    def live_state_message(self, *, request_id: UUID | None = None) -> LiveState:
        return LiveState(
            request_id=request_id,
            live_session_id=self._live_session_id,
            state=self._live_state,
            target_fps=self._settings.live_target_fps,
            reason=self._live_reason,
        )  # type: ignore[arg-type]

    async def _set_live_state(
        self, state: str, reason: str | None, *, request_id: UUID | None = None
    ) -> None:
        changed = state != self._live_state or reason != self._live_reason or request_id is not None
        self._live_state, self._live_reason = state, reason
        if not changed:
            return
        log_event(
            "info" if state != "paused" else "warning",
            "live_state_changed",
            state=state,
            reason=reason,
            live_session_id=str(self._live_session_id) if self._live_session_id else None,
            connected_devices=sum(device.connected for device in self._devices.values()),
            expected_devices=len(self._devices),
        )
        payload = self.live_state_message(request_id=request_id).model_dump_json()
        await asyncio.gather(
            *(d.send_text(payload) for d in self._devices.values() if d.send_text is not None),
            return_exceptions=True,
        )

    def is_expected_device(self, device_id: str) -> bool:
        return device_id in self._devices

    async def handle_rgbd(
        self, raw: bytes, *, expected_device_id: str, mode: str | None = None
    ) -> CharacterFrame | None:
        env = decode_envelope(raw)
        decoded = decode_rgbd_frame(env)
        device_id = decoded.header.device_id
        if device_id != expected_device_id:
            raise EnvelopeError(
                "invalid_message", "RGBD device_id does not match authenticated socket"
            )
        state = self._devices.get(device_id)
        if state is None:
            raise EnvelopeError("unauthorized_device", "device not configured")
        pending = self._pending_captures.get(decoded.header.capture_id)
        capture_mode = (
            Mode(mode) if mode is not None else pending.mode if pending else Mode.SNAPSHOT
        )
        log_event(
            "info",
            "rgbd_packet_decoded",
            device_id=device_id,
            session_id=str(decoded.header.session_id),
            capture_id=str(decoded.header.capture_id),
            sequence=decoded.header.sequence,
            mode=capture_mode.value,
            packet_bytes=len(raw),
            rgb_width=decoded.header.rgb.width,
            rgb_height=decoded.header.rgb.height,
            depth_width=decoded.header.depth.width,
            depth_height=decoded.header.depth.height,
            tracking_state=decoded.header.tracking_state.value,
            buffer_count=len(decoded.header.buffers),
        )

        if capture_mode is Mode.SNAPSHOT:
            recording_dir = save_capture_packet(
                self._settings.recording_root,
                decoded.header.capture_id,
                device_id,
                raw,
                calibration_json=rig_to_json(self._calibration) if self._calibration else None,
                app_release="ios-capture",
                backend_release=self._settings.release,
            )
            log_event(
                "info",
                "capture_packet_recorded",
                device_id=device_id,
                capture_id=str(decoded.header.capture_id),
                sequence=decoded.header.sequence,
                packet_bytes=len(raw),
                recording_dir=str(recording_dir),
            )

        if self._pairer is None or self._processor is None or self._calibration is None:
            return None
        frame = captured_frame_from_decoded(
            decoded,
            clock_offset_s=state.clock.offset_s() or 0.0,
            clock_uncertainty_ms=state.clock.uncertainty_ms() or 0.0,
        )
        required_device_ids = pending.device_ids if pending else (device_id,)
        outcome = self._pairer.offer(
            frame,
            self._calibration.calibration_id,
            required_device_ids=required_device_ids,
        )
        if outcome.paired is None:
            if outcome.rejected_reason:
                log_event(
                    "warning",
                    "pair_rejected",
                    device_id=device_id,
                    capture_id=str(decoded.header.capture_id),
                    reason=outcome.rejected_reason,
                )
            else:
                log_event(
                    "info",
                    "rgbd_packet_waiting_for_pair",
                    device_id=device_id,
                    capture_id=str(decoded.header.capture_id),
                    mode=capture_mode.value,
                )
            return None
        log_event(
            "info",
            "capture_group_ready",
            capture_id=str(decoded.header.capture_id),
            mode=capture_mode.value,
            pair_skew_ms=round(outcome.paired.pair_skew_ms, 2),
            source_count=len(outcome.paired.frames),
            device_ids=",".join(frame.device_id for frame in outcome.paired.frames),
        )
        self._pending_captures.pop(decoded.header.capture_id, None)
        if self._live_outstanding == decoded.header.capture_id:
            self._live_outstanding = None
        if capture_mode is Mode.LIVE:
            self._enqueue_live_group(outcome.paired)
            return None
        return await self._process_group(outcome.paired, Mode.SNAPSHOT)

    def _enqueue_live_group(self, group: CaptureGroup) -> None:
        if self._pending_live_group is not None:
            self._dropped_pairs += 1
        self._pending_live_group = group
        if self._live_worker is None or self._live_worker.done():
            self._live_worker = asyncio.create_task(self._drain_live_groups())

    async def _drain_live_groups(self) -> None:
        while self._pending_live_group is not None:
            group, self._pending_live_group = self._pending_live_group, None
            await self._process_group(group, Mode.LIVE)

    async def _process_group(self, group: CaptureGroup, mode: Mode) -> CharacterFrame:
        assert self._processor is not None and self._calibration is not None
        processor = self._processor
        async with self._process_lock:
            with transaction(
                "character.snapshot",
                op="capture.process",
                capture_id=str(group.first.capture_id),
                calibration_id=str(self._calibration.calibration_id),
                pair_skew_ms=round(group.pair_skew_ms, 2),
                source_count=len(group.frames),
            ):
                loop = asyncio.get_running_loop()
                with span("collider.fit_and_reconstruct", "detect+reconstruct+fit"):
                    character = await loop.run_in_executor(
                        self._executor, lambda: processor.process(group, mode=mode.value)
                    )
                with span("character.serialize", "encode CHARACTER_FRAME"):
                    encoded = await loop.run_in_executor(
                        self._executor, encode_character_frame, character
                    )
                self._store.publish(character, encoded)
                self._hub.broadcast(encoded)
        now = time.monotonic()
        self._last_publish_at = now
        self._publish_times.append(now)
        log_event(
            "info",
            "character_published",
            frame_id=character.frame_id,
            mode=mode.value,
            point_count=character.quality.point_count,
            pair_skew_ms=round(group.pair_skew_ms, 2),
            source_count=len(group.frames),
        )
        return character

    def _effective_fps(self) -> float:
        if len(self._publish_times) < 2:
            return 0.0
        elapsed = self._publish_times[-1] - self._publish_times[0]
        return 0.0 if elapsed <= 0 else (len(self._publish_times) - 1) / elapsed

    def health(self) -> tuple[HealthResponse, int]:
        calib = self._calibration
        devices = {
            dev: DeviceHealth(
                connected=s.connected,
                clock_ready=self._clock_ready(s),
                queue_depth=self._pairer.pending_depth(dev) if self._pairer else 0,
            )
            for dev, s in self._devices.items()
        }
        models = (
            {"pose": "ready", "hands": "not_loaded"}
            if self.is_ready()
            else {"pose": "not_loaded", "hands": "not_loaded"}
        )
        status = (
            HealthStatus.READY
            if self.is_ready()
            else HealthStatus.STARTING
            if calib is None
            else HealthStatus.DEGRADED
        )
        age = (
            None
            if self._last_publish_at is None
            else (time.monotonic() - self._last_publish_at) * 1000
        )
        response = HealthResponse(
            status=status,
            calibration=CalibrationHealth(
                loaded=calib is not None, calibration_id=calib.calibration_id if calib else None
            ),
            models=models,
            devices=devices,
            latest_frame_id=self._store.latest_frame_id,
            live=LiveHealth(
                state=self._live_state,
                live_session_id=self._live_session_id,
                target_fps=self._settings.live_target_fps,
                effective_fps=self._effective_fps(),
                last_publish_age_ms=age,
                missed_captures=self._missed_captures,
                dropped_pairs=self._dropped_pairs,
            ),
        )  # type: ignore[arg-type]
        return response, 200 if status is HealthStatus.READY else 503
