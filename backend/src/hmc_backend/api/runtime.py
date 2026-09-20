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
from uuid import UUID, uuid4

import numpy as np

from hmc_backend.api.hub import CharacterHub
from hmc_backend.calibration.model import RigCalibration, save_rig_calibration
from hmc_backend.calibration.session import BoardCalibrationSession, RigFreshness
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
    Error,
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
    last_frame_s: float | None = None
    tracking_state: str | None = None
    valid_depth_count: int = 0


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

        self._board_session: BoardCalibrationSession | None = None
        self._board_task: asyncio.Task | None = None
        self._board_pairer = build_pairer(settings)
        self._board_pair_id = uuid4()
        self._board_packets: OrderedDict = OrderedDict()
        self._freshness = RigFreshness(calibration) if calibration else None
        self._last_published_s: float | None = None
        self._last_merge_rejection: str | None = None
        self._last_merge_notice_s = 0.0
        self._closing = False
        if calibration is not None:
            self._pairer = pairer or build_pairer(settings)
            self._processor = processor or build_processor(settings, calibration)
            # Person-target corrections are deliberately never loaded in the physical workflow.
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
        return (self._calibration is not None and self._processor is not None
                and (self._calibration.board is not None or self._settings.simulation_mode)
                and set(self._calibration.device_ids()) == set(self._settings.expected_device_ids)
                and not (self._freshness and self._freshness.invalid_reason))

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

    async def dispatch_capture(self, capture_id: UUID, mode: str, *, board: bool = False) -> tuple[bool, str]:
        """Forward a capture_request to both phones if the rig can accept one."""
        if not board and self.calibrating:
            return False, "calibration_in_progress"
        if not board and not self.is_ready():
            return False, "not_ready"
        if not all(s.connected and s.send_text is not None for s in self._devices.values()):
            return False, "devices_unavailable"
        if not self._settings.simulation_mode and not all(s.clock.ready for s in self._devices.values()):
            return False, "clocks_not_ready"
        req = CaptureRequest(request_id=uuid4(), capture_id=capture_id, mode=Mode(mode))
        self._capture_modes[capture_id] = "calibration" if board else mode
        while len(self._capture_modes) > 256:
            self._capture_modes.popitem(last=False)
        payload = req.model_dump_json()
        for state in self._devices.values():
            assert state.send_text is not None
            token = state.token
            try:
                await state.send_text(payload)
            except Exception:  # A disconnected phone must not kill live/calibration capture.
                self.unregister_capture(state.device_id, token)
                log_event("warning", "capture_send_failed", device_id=state.device_id)
                return False, "devices_unavailable"
        return True, "capture_dispatched"

    async def _announce_capture_blocked(self, code: str) -> None:
        details = {
            "not_ready": "Camera calibration required. Press N in Minecraft with the board visible to both phones.",
            "calibration_in_progress": "Camera calibration is still running; keep the board visible and phones still.",
            "devices_unavailable": "Connect both phones using distinct IDs: " + ", ".join(self._devices),
            "clocks_not_ready": "Waiting for clock sync from both phones; keep both capture apps open.",
        }
        detail = details.get(code, code)
        if code == "not_ready" and self._freshness and self._freshness.invalid_reason:
            detail += " Reason: " + self._freshness.invalid_reason
        payload = Error(code=code, message=detail, retryable=True).model_dump_json()
        self._hub.broadcast(payload)
        log_event("warning", "live_capture_blocked", code=code, detail=detail)
        for state in self._devices.values():
            if state.connected and state.send_text is not None:
                token = state.token
                try:
                    await state.send_text(payload)
                except Exception:
                    self.unregister_capture(state.device_id, token)

    # --- rig registration -------------------------------------------------

    @property
    def calibrating(self) -> bool:
        return self._board_task is not None and not self._board_task.done()

    def request_registration(self) -> bool:
        if self.calibrating:
            return False
        self._board_session = BoardCalibrationSession(self._settings.expected_device_ids)
        self._board_pairer = build_pairer(self._settings)
        self._board_pair_id = uuid4()
        self._board_packets.clear()
        self._board_task = asyncio.create_task(self._calibration_loop())
        return True

    def clear_registration(self) -> None:
        # DELETE cancels setup; it never deletes a previously validated rig.
        if self._board_task is not None:
            self._board_task.cancel()
        if self._board_session is not None:
            self._board_session.fail("calibration_cancelled")
        self._announce_calibration()

    async def shutdown(self) -> None:
        self._closing = True
        self.stop_live()
        if self._board_task is not None:
            self._board_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._board_task

    def registration_status(self) -> dict:
        result = self._board_session.status() if self._board_session else {
            "state": "ready" if self.is_ready() else "failed",
            "error": None if self.is_ready() else "calibration_required", "devices": {},
        }
        result["active_calibration_id"] = str(self._calibration.calibration_id) if self._calibration else None
        result["invalid_reason"] = self._freshness.invalid_reason if self._freshness else None
        if result["invalid_reason"] and result["state"] not in ("collecting", "validating"):
            result["state"] = "failed"
            result["error"] = result["error"] or result["invalid_reason"]
        result["max_range_m"] = self._settings.max_range_m
        result["body_merge"] = getattr(self._processor, "body_merge_status", {"state": "waiting"})
        # Where the active rig thinks each phone sits, and how far apart — the
        # numbers to tape-measure against the real rig when the render splits.
        if self._calibration is not None:
            result["synthetic"] = self._calibration.board is None
            positions = {
                dev: self._calibration.camera(dev).T_stage_from_optical[:3, 3].tolist()
                for dev in self._calibration.device_ids()
            }
            result["camera_positions_m"] = positions
            if len(positions) == 2:
                a, b = (np.asarray(p) for p in positions.values())
                result["baseline_m"] = round(float(np.linalg.norm(a - b)), 3)
        return result

    def _announce_calibration(self) -> None:
        status = self.registration_status()
        details = []
        for dev, progress in status["devices"].items():
            rejected = progress["rejections"]
            reason = max(rejected, key=rejected.get) if rejected else ""
            details.append(f"{dev}: {progress['accepted']}/{progress['required']} {reason}")
        detail = status.get("error") or "; ".join(details)
        if status["state"] == "collecting":
            detail = "Board face up on floor, phones still, step out. " + detail
        if status["state"] == "ready" and status.get("baseline_m") is not None:
            # The baseline is a tape-measure check: if the announced distance
            # does not match the phones on the floor, the solve was biased.
            detail = f"Solved baseline {status['baseline_m']:.2f} m — tape-measure it"
        self._hub.broadcast(json.dumps({"type": "ack", "protocol_version": 1,
            "accepted": status["state"] != "failed",
            "code": "calibration_" + status["state"],
            "detail": detail or "Both cameras calibrated"}))

    async def _calibration_loop(self) -> None:
        was_live = self.live_active
        self.stop_live()
        job = self._board_session
        assert job is not None
        deadline = time.monotonic() + self._settings.calibration_timeout_s
        self._board_deadline = deadline
        try:
            while job.state in ("collecting", "validating") and time.monotonic() < deadline:
                if job.state == "collecting":
                    # Unknown clocks must not masquerade as synchronized board captures.
                    if all(s.connected and s.clock.ready for s in self._devices.values()):
                        await self.dispatch_capture(uuid4(), "snapshot", board=True)
                    self._announce_calibration()
                await asyncio.sleep(1 / self._settings.calibration_capture_hz)
            if job.state in ("collecting", "validating"):
                job.fail("timeout: both phones need 12 stable board observations; check connection, clocks and board visibility")
        except asyncio.CancelledError:
            job.fail("calibration_cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - report setup failure without losing the active rig
            job.fail(f"calibration_failed: {exc}")
        finally:
            self._announce_calibration()
            if was_live and not self._closing:
                self.start_live()

    async def _offer_board_frame(self, frame, raw: bytes) -> None:
        job = self._board_session
        if job is None or job.state != "collecting":
            return
        packets = self._board_packets.setdefault(frame.capture_id, {})
        packets[frame.device_id] = raw
        while len(self._board_packets) > 8:
            self._board_packets.popitem(last=False)
        outcome = self._board_pairer.offer(frame, self._board_pair_id)
        if outcome.paired is None:
            if outcome.rejected_reason:
                job.rejections[frame.device_id][outcome.rejected_reason] += 1
            return
        pair = outcome.paired
        raw_pair = self._board_packets.pop(frame.capture_id, {})
        try:
            await asyncio.to_thread(save_recording, self._settings.recording_root, frame.capture_id,
                                    raw_pair, pair_skew_ms=pair.pair_skew_ms,
                                    consent_note="board calibration capture")
        except (OSError, ValueError) as exc:
            job.fail(f"recording_failed: {exc}")
            self._announce_calibration()
            return
        await asyncio.to_thread(job.offer, pair.first)
        await asyncio.to_thread(job.offer, pair.second)
        self._announce_calibration()
        if job.state != "validating":
            return
        try:
            rig = await asyncio.to_thread(job.solve)
            if job.state != "validating" or time.monotonic() > self._board_deadline:
                job.fail("calibration_cancelled_or_timed_out")
                return
            processor = build_processor(self._settings, rig,
                assembler=self._processor.assembler if self._processor else None,
                session_id=self._server_session_id)
            # Small atomic file commit and memory swap share one event-loop turn.
            # Cancellation cannot install a file without installing its matching rig.
            save_rig_calibration(rig, self._settings.calibration_path)
            # Called with the ingest lock: no old frame can publish after this swap.
            self._calibration, self._processor = rig, processor
            self._pairer = build_pairer(self._settings)
            self._freshness = RigFreshness(rig)
            job.result, job.state = rig, "ready"
        except Exception as exc:  # noqa: BLE001 - report setup failure without losing the active rig
            job.fail(f"validation_failed: {exc}")
        self._announce_calibration()

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
        last_blocker = None
        last_notice = 0.0
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
                accepted, code = await self.dispatch_capture(capture_id, "live")
                if accepted:
                    self._live_inflight[capture_id] = time.monotonic()
                    last_blocker = None
                elif code != last_blocker or now - last_notice >= 5.0:
                    await self._announce_capture_blocked(code)
                    last_blocker, last_notice = code, now
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

    async def handle_rgbd(self, raw: bytes, *, mode: str = "snapshot") -> CharacterFrame | None:
        async with self._lock:
            return await self._handle_rgbd_locked(raw, mode=mode)

    async def _handle_rgbd_locked(self, raw: bytes, *, mode: str = "snapshot") -> CharacterFrame | None:
        """Decode a packet, pair it, and (on a completed pair) publish a frame."""
        env = decode_envelope(raw)
        decoded = decode_rgbd_frame(env)
        device_id = decoded.header.device_id
        mode = self._capture_modes.get(decoded.header.capture_id, mode)
        state = self._devices.get(device_id)
        if state is None:
            return None

        state.last_frame_s = time.monotonic()
        state.tracking_state = decoded.header.tracking_state.value
        state.valid_depth_count = int(np.count_nonzero(np.isfinite(decoded.depth_m) & (decoded.depth_m > 0)))
        offset = state.clock.offset_s() or 0.0
        uncertainty = state.clock.uncertainty_ms()
        if uncertainty is None:
            if not self._settings.simulation_mode:
                return None
            uncertainty = 0.0  # explicit simulation mode only
        frame = captured_frame_from_decoded(
            decoded, clock_offset_s=offset, clock_uncertainty_ms=uncertainty
        )

        if mode == "calibration":
            await self._offer_board_frame(frame, raw)
            return None
        if self.calibrating or self._pairer is None or self._processor is None or self._calibration is None:
            return None
        if self._freshness and self._freshness.observe(frame):
            self._announce_calibration()
            return None
        if not self.is_ready():
            self._announce_calibration()
            return None

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
            # A failed forced merge still contains the original two clouds for
            # diagnostics. Never promote that unaligned geometry to a playable
            # snapshot. Retain the previous publication and its original age;
            # live interactions expire normally rather than renewing stale hits.
            merge_rejection = next((warning for warning in character.quality.warnings
                                    if warning.startswith("body_merge_unavailable:")), None)
            if self._settings.opposing_body_merge and merge_rejection is not None:
                now_s = time.monotonic()
                if (merge_rejection != self._last_merge_rejection
                        or now_s - self._last_merge_notice_s >= 5.0):
                    message = ("Body alignment unavailable; holding the last aligned scan. "
                               "Keep both phones still and torso and legs visible in both views. "
                               + merge_rejection.split(":", 1)[1])
                    self._hub.broadcast(Error(code="body_merge_unavailable", message=message,
                                              retryable=True).model_dump_json())
                    log_event("warning", "unaligned_frame_withheld", reason=merge_rejection)
                    self._last_merge_notice_s = now_s
                self._last_merge_rejection = merge_rejection
                return None
            self._last_merge_rejection = None
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
                self._last_published_s = time.monotonic()
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
                frame_age_ms=(round((time.monotonic() - state.last_frame_s) * 1000) if state.last_frame_s else None),
                tracking_state=state.tracking_state,
                valid_depth_count=state.valid_depth_count,
                reconstruction=(self._processor.view_diagnostics.get(dev, {}) if self._processor else {}),
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
                state=self.registration_status(),
                calibration_id=calib.calibration_id if calib else None,
            ),
            models=models,
            devices=devices,
            latest_frame_id=self._store.latest_frame_id,
            max_range_m=self._settings.max_range_m,
            frame_age_ms=(round((time.monotonic() - self._last_published_s) * 1000) if self._last_published_s else None),
        )
        http_status = 200 if status is HealthStatus.READY else 503
        return response, http_status
