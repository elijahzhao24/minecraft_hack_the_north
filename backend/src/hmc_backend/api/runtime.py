"""AppRuntime: in-memory state and the capture->publish orchestration.

Holds the frame tree, pipeline, per-device connection/clock state, the snapshot
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

from hmc_backend.api.hub import CharacterHub
from hmc_backend.calibration.anchor import (
    DeviceAccumulator,
    anchor_status_payload,
    build_board_detector,
    observe_decoded_frame,
    solve_frame_tree,
)
from hmc_backend.calibration.frame_tree import RigFrameTree, save_frame_tree
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
class AnchorCaptureState:
    """One synchronized anchor sample waiting for both device packets."""

    capture_id: UUID
    packets: dict[str, bytes] = field(default_factory=dict)


@dataclass
class AnchorSession:
    """Shared-marker anchoring in progress."""

    request_id: UUID
    started_monotonic_s: float
    deadline_monotonic_s: float
    required_samples: int
    accepted_samples: int = 0
    pending: dict[UUID, AnchorCaptureState] = field(default_factory=dict)
    accumulators: dict[str, DeviceAccumulator] = field(default_factory=dict)
    state: str = "collecting"
    failure_code: str | None = None
    resume_live: bool = False
    board_spec: object | None = None
    board: object | None = None
    detector: object | None = None


class AppRuntime:
    """Owns the pipeline and connection state for the process lifetime."""

    def __init__(
        self,
        settings: Settings,
        frame_tree: RigFrameTree | RigCalibration | None,
        *,
        processor: CharacterProcessor | None = None,
        pairer: Pairer | None = None,
        store: SnapshotStore | None = None,
    ) -> None:
        if isinstance(frame_tree, RigCalibration):
            frame_tree = RigFrameTree.from_legacy_rig(frame_tree)
        self._settings = settings
        self._frame_tree = frame_tree
        self._server_session_id = uuid4()
        self._store = store or SnapshotStore()
        self._hub = CharacterHub()
        self._lock = asyncio.Lock()
        self._published_since_aggregate = 0
        self._aggregate_point_total = 0
        self._last_aggregate_s = time.perf_counter()

        self._devices = {dev: DeviceState(dev) for dev in settings.expected_device_ids}
        self._capture_modes: OrderedDict[UUID, str] = OrderedDict()
        self._live_task: asyncio.Task[None] | None = None
        self._live_rate_hz: float = settings.live_rate_hz
        self._live_inflight: dict[UUID, float] = {}
        self._live_paired = asyncio.Event()
        self._anchor: AnchorSession | None = None
        self._anchor_task: asyncio.Task[None] | None = None

        self._pairer: Pairer | None
        self._processor: CharacterProcessor | None
        if frame_tree is not None:
            self._pairer = pairer or build_pairer(settings)
            self._processor = processor or build_processor(settings, frame_tree)
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

    @property
    def frame_tree(self) -> RigFrameTree | None:
        return self._frame_tree

    @property
    def calibration(self) -> RigCalibration | None:
        return self._frame_tree.rig if self._frame_tree is not None else None

    def is_ready(self) -> bool:
        return self._frame_tree is not None and self._processor is not None

    def _emit_anchor_status(self, session: AnchorSession) -> None:
        rig_id = self._frame_tree.rig.calibration_id if session.state == "complete" and self._frame_tree else None
        payload = anchor_status_payload(
            request_id=session.request_id,
            state=session.state,
            accepted_sample_count=session.accepted_samples,
            required_sample_count=session.required_samples,
            rig_id=rig_id,
            failure_code=session.failure_code,
        )
        self._hub.broadcast_text(json.dumps(payload))

    def _install_frame_tree(self, tree: RigFrameTree) -> None:
        self._frame_tree = tree
        self._pairer = build_pairer(self._settings)
        self._processor = build_processor(self._settings, tree)
        log_event("info", "frame_tree_installed", rig_id=str(tree.rig.calibration_id))

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

    async def dispatch_capture(
        self,
        capture_id: UUID,
        mode: str,
        *,
        sync_lead_s: float = 0.05,
    ) -> tuple[bool, str]:
        """Forward a capture_request to both phones if the rig can accept one."""
        if not self.is_ready() and self._anchor is None:
            return False, "not_ready"
        if not all(s.connected and s.send_text is not None for s in self._devices.values()):
            return False, "devices_unavailable"
        target_backend_s = time.monotonic() + sync_lead_s
        self._capture_modes[capture_id] = mode
        while len(self._capture_modes) > 256:
            self._capture_modes.popitem(last=False)
        for state in self._devices.values():
            assert state.send_text is not None
            offset = state.clock.offset_s() or 0.0
            req = CaptureRequest(
                request_id=uuid4(),
                capture_id=capture_id,
                mode=Mode(mode),
                not_before_phone_time_s=target_backend_s + offset,
            )
            await state.send_text(req.model_dump_json())
        return True, "capture_dispatched"

    # --- shared-marker anchoring ------------------------------------------

    @property
    def anchor_active(self) -> bool:
        return self._anchor is not None and self._anchor.state == "collecting"

    async def start_anchor(self, request_id: UUID) -> tuple[bool, str]:
        """Begin collecting synchronized board samples from both phones."""
        if self._anchor is not None:
            return False, "anchor_in_progress"
        if not all(s.connected and s.send_text is not None for s in self._devices.values()):
            return False, "devices_unavailable"
        resume_live = self.live_active
        if resume_live:
            self.stop_live()
        spec, board, detector = build_board_detector()
        now = time.monotonic()
        session = AnchorSession(
            request_id=request_id,
            started_monotonic_s=now,
            deadline_monotonic_s=now + self._settings.anchor_timeout_s,
            required_samples=self._settings.anchor_sample_count,
            resume_live=resume_live,
            board_spec=spec,
            board=board,
            detector=detector,
            accumulators={
                dev: DeviceAccumulator(dev) for dev in self._settings.expected_device_ids
            },
        )
        self._anchor = session
        self._emit_anchor_status(session)
        self._anchor_task = asyncio.create_task(self._anchor_loop())
        log_event("info", "anchor_started", request_id=str(request_id))
        return True, "anchor_started"

    def _anchor_timed_out(self, session: AnchorSession) -> bool:
        return time.monotonic() > session.deadline_monotonic_s

    async def _anchor_loop(self) -> None:
        """Request anchor captures until enough valid board pairs are collected."""
        try:
            while True:
                session = self._anchor
                if session is None or session.state != "collecting":
                    return
                if self._anchor_timed_out(session):
                    await self._finish_anchor(session, "anchor_timeout")
                    return
                if session.accepted_samples >= session.required_samples:
                    await self._finalize_anchor(session)
                    return
                if len(session.pending) >= 2:
                    await asyncio.sleep(0.05)
                    continue
                capture_id = uuid4()
                session.pending[capture_id] = AnchorCaptureState(capture_id=capture_id)
                accepted, _ = await self.dispatch_capture(capture_id, "snapshot", sync_lead_s=0.08)
                if not accepted:
                    await self._finish_anchor(session, "devices_unavailable")
                    return
                await asyncio.sleep(0.12)
        except asyncio.CancelledError:
            pass

    async def _finish_anchor(self, session: AnchorSession, failure_code: str) -> None:
        session.state = "failed"
        session.failure_code = failure_code
        self._emit_anchor_status(session)
        log_event("warning", "anchor_failed", request_id=str(session.request_id), code=failure_code)
        if session.resume_live:
            self.start_live()
        self._anchor = None

    async def _finalize_anchor(self, session: AnchorSession) -> None:
        session.state = "solving"
        self._emit_anchor_status(session)
        try:
            tree = await asyncio.to_thread(solve_frame_tree, session.accumulators, self._settings)
        except Exception as exc:  # noqa: BLE001 - anchor errors are reported to the client
            await self._finish_anchor(session, str(exc).replace(" ", "_")[:64])
            return
        try:
            await asyncio.to_thread(save_frame_tree, tree, self._settings.frame_tree_path)
        except Exception as exc:  # noqa: BLE001 - preserve the active rig on disk failure
            await self._finish_anchor(session, f"persist_failed_{type(exc).__name__}")
            return
        self._install_frame_tree(tree)
        session.state = "complete"
        self._emit_anchor_status(session)
        log_event(
            "info",
            "anchor_complete",
            request_id=str(session.request_id),
            rig_id=str(tree.rig.calibration_id),
        )
        if session.resume_live:
            self.start_live()
        self._anchor = None

    async def _handle_anchor_packet(self, device_id: str, capture_id: UUID, raw: bytes) -> None:
        session = self._anchor
        if session is None or session.state != "collecting":
            return
        pending = session.pending.get(capture_id)
        if pending is None:
            return
        if device_id in pending.packets:
            await self._finish_anchor(session, "duplicate_device_packet")
            return
        pending.packets[device_id] = bytes(raw)
        if set(pending.packets) != set(self._devices):
            return

        assert session.board is not None and session.detector is not None
        accepted_pair = True
        for dev_id, packet in pending.packets.items():
            try:
                decoded = decode_rgbd_frame(decode_envelope(packet))
            except EnvelopeError:
                accepted_pair = False
                session.accumulators[dev_id].note_rejection("undecodable_packet")
                continue
            observed = await asyncio.to_thread(
                observe_decoded_frame,
                decoded,
                board=session.board,
                detector=session.detector,
                accumulator=session.accumulators[dev_id],
            )
            if not observed:
                accepted_pair = False
        del session.pending[capture_id]
        if accepted_pair:
            session.accepted_samples += 1
            self._emit_anchor_status(session)
            if session.accepted_samples >= session.required_samples:
                await self._finalize_anchor(session)

    # --- live mode --------------------------------------------------------

    @property
    def live_active(self) -> bool:
        return self._live_task is not None and not self._live_task.done()

    def start_live(self, rate_hz: float | None = None) -> None:
        """Drive both phones with live capture_requests at ``rate_hz``."""
        if self.anchor_active:
            return
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
        if self._anchor is not None:
            await self._handle_anchor_packet(device_id, decoded.header.capture_id, raw)
            return None
        if self._pairer is None or self._processor is None or self._frame_tree is None:
            return None
        camera = self._frame_tree.rig.camera(device_id)
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
            uncertainty = 0.0
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
                outcome = self._pairer.offer(frame, self._frame_tree.rig.calibration_id)
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
            txn.set_data("calibration_version", str(self._frame_tree.rig.calibration_id))
            txn.set_data("pair_skew_ms", round(pair.pair_skew_ms, 2))
            if waits:
                txn.set_data("pair_queue_wait_ms_max", round(max(waits), 3))
            propagated = txn.propagation_headers()
            output_trace = TraceContext(
                sentry_trace=propagated.get("sentry-trace"),
                baggage=propagated.get("baggage"),
            )
            with span("collider.fit_and_reconstruct", "detect+reconstruct+fit"):
                character = await asyncio.to_thread(
                    self._processor.process, pair, mode=mode, trace=output_trace
                )
            if mode == "live" and character.quality.point_count == 0:
                log_event("info", "live_frame_empty", capture_id=str(pair.first.capture_id))
                return None
            txn.set_data("frame_id", character.frame_id)
            txn.set_data("point_count", character.quality.point_count)
            with span("hmc.character_serialize", "encode CHARACTER_FRAME") as serialize_span:
                encoded = await asyncio.to_thread(encode_character_frame, character)
                serialize_span.set_data("payload_size", len(encoded))
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
        calib = self.calibration
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
