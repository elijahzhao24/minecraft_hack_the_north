"""FastAPI application: lifespan wiring, /health, and the capture/character WS.

One Uvicorn worker owns all state. Startup loads the frame tree (if present) and
builds the runtime; the capture socket ingests HMC1 RGBD frames and the
character socket streams the latest published snapshot to Minecraft.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from hmc_backend.api.discovery import start_discovery, stop_discovery
from hmc_backend.api.runtime import AppRuntime
from hmc_backend.calibration.frame_tree import RigFrameTree, load_frame_tree
from hmc_backend.calibration.model import CalibrationError, load_rig_calibration
from hmc_backend.contracts.control import (
    Ack,
    CharacterHello,
    CharacterServerHello,
    ClientHello,
    ClockPing,
    ControllerHello,
    Error,
    ServerHello,
)
from hmc_backend.controller.ble import BadgeBleReceiver
from hmc_backend.controller.state import ControllerState, ControllerStore
from hmc_backend.observability import configure_sentry, flush_sentry, log_event
from hmc_backend.protocol.envelope import EnvelopeError
from hmc_backend.settings import Settings, load_settings


def build_runtime(settings: Settings) -> AppRuntime:
    """Load the frame tree (if any) and construct the runtime."""
    frame_tree: RigFrameTree | None = None
    try:
        frame_tree = load_frame_tree(settings.frame_tree_path)
        if set(frame_tree.rig.device_ids()) != set(settings.expected_device_ids):
            raise CalibrationError("frame tree devices do not match expected_device_ids")
    except CalibrationError:
        try:
            rig = load_rig_calibration(settings.calibration_path)
            if set(rig.device_ids()) != set(settings.expected_device_ids):
                raise CalibrationError("calibration devices do not match expected_device_ids")
            frame_tree = RigFrameTree.from_legacy_rig(rig)
            log_event("info", "frame_tree_loaded_from_legacy_calibration")
        except CalibrationError:
            frame_tree = None
    return AppRuntime(settings, frame_tree)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    configure_sentry(
        settings.sentry_dsn,
        environment=settings.environment,
        release=settings.release,
        traces_sample_rate=settings.traces_sample_rate,
        send_default_pii=settings.sentry_send_default_pii,
    )
    app.state.runtime = build_runtime(settings)
    app.state.controller = ControllerStore(stale_timeout_ms=settings.badge_stale_timeout_ms)
    app.state.badge_receiver = BadgeBleReceiver(settings, app.state.controller)
    badge_task = asyncio.create_task(app.state.badge_receiver.run())
    start_discovery(port=settings.port)
    yield
    app.state.badge_receiver.stop()
    badge_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await badge_task
    app.state.runtime.stop_live()
    stop_discovery()
    flush_sentry()


app = FastAPI(title="hmc-backend", lifespan=lifespan)


@app.get("/health")
async def health() -> JSONResponse:
    runtime: AppRuntime = app.state.runtime
    response, status_code = runtime.health()
    content = json.loads(response.model_dump_json())
    controller: ControllerStore = app.state.controller
    content["controller"] = {
        "enabled": runtime.settings.badge_controller_enabled,
        **controller.health(),
    }
    return JSONResponse(content=content, status_code=status_code)


@app.websocket("/ws/controller")
async def ws_controller(ws: WebSocket) -> None:
    runtime: AppRuntime = app.state.runtime
    controller: ControllerStore = app.state.controller
    await ws.accept()
    try:
        first = await asyncio.wait_for(ws.receive_text(), timeout=runtime.settings.hello_deadline_s)
        ControllerHello.model_validate_json(first)
    except (TimeoutError, WebSocketDisconnect, ValueError):
        await ws.close(code=1008)
        return

    queue = controller.subscribe()
    sender = asyncio.create_task(_controller_sender(ws, queue))
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        controller.unsubscribe(queue)
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender


async def _controller_sender(ws: WebSocket, queue: asyncio.Queue[ControllerState]) -> None:
    while True:
        state = await queue.get()
        await ws.send_text(json.dumps(state.wire(), separators=(",", ":")))


@app.websocket("/ws/capture")
async def ws_capture(ws: WebSocket) -> None:
    runtime: AppRuntime = app.state.runtime
    await ws.accept()

    try:
        first = await asyncio.wait_for(ws.receive_text(), timeout=runtime.settings.hello_deadline_s)
    except (TimeoutError, WebSocketDisconnect):
        await ws.close()
        return

    try:
        hello = ClientHello.model_validate_json(first)
    except ValueError:
        await _send_model(ws, Error(code="invalid_message", message="expected client_hello"))
        await ws.close()
        return

    if not runtime.is_expected_device(hello.device_id):
        await _send_model(ws, Error(code="unauthorized_device", message="device not configured"))
        await ws.close()
        return

    token = runtime.register_capture(hello.device_id, ws.send_text)
    await _send_model(
        ws,
        ServerHello(
            server_session_id=runtime.server_session_id,
            accepted_device_id=hello.device_id,
            max_binary_bytes=runtime.settings.max_rgbd_bytes,
            clock_probe_interval_s=runtime.settings.clock_probe_interval_s,
        ),
    )

    probe_task = asyncio.create_task(
        _clock_probe_loop(ws, runtime.settings.clock_probe_interval_s)
    )

    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if (text := message.get("text")) is not None:
                await _handle_capture_text(runtime, ws, hello.device_id, text)
            elif (data := message.get("bytes")) is not None:
                await _handle_capture_binary(runtime, ws, hello.device_id, data)
    except WebSocketDisconnect:
        pass
    finally:
        probe_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await probe_task
        runtime.unregister_capture(hello.device_id, token)


async def _clock_probe_loop(ws: WebSocket, interval_s: float) -> None:
    import time

    for _ in range(4):
        try:
            ping = ClockPing(request_id=uuid4(), backend_send_time_s=time.monotonic())
            await _send_model(ws, ping)
            await asyncio.sleep(0.05)
        except Exception:  # noqa: BLE001
            return

    while True:
        try:
            await asyncio.sleep(interval_s)
            ping = ClockPing(request_id=uuid4(), backend_send_time_s=time.monotonic())
            await _send_model(ws, ping)
        except Exception:  # noqa: BLE001
            break


async def _handle_capture_text(runtime: AppRuntime, ws: WebSocket, device_id: str, text: str) -> None:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        await _send_model(ws, Error(code="invalid_message", message="control frame not JSON"))
        return
    if _handle_live_control(runtime, obj):
        return
    if obj.get("type") == "ack" and not obj.get("accepted", True):
        log_event("warning", "capture_declined", device_id=device_id, code=obj.get("code"), detail=obj.get("detail"))
        return
    if obj.get("type") == "clock_pong":
        import time

        t3 = time.monotonic()
        runtime.on_clock_pong(
            device_id,
            float(obj["backend_send_time_s"]),
            float(obj["phone_receive_time_s"]),
            float(obj["phone_send_time_s"]),
            t3,
        )


async def _handle_capture_binary(runtime: AppRuntime, ws: WebSocket, device_id: str, data: bytes) -> None:
    try:
        await runtime.handle_rgbd(data, connected_device_id=device_id)
    except EnvelopeError as exc:
        log_event("warning", "rgbd_rejected", device_id=device_id, code=exc.code, detail=exc.message)
        await _send_model(ws, Error(code=exc.code, message=exc.message))
    except Exception:  # noqa: BLE001
        log_event("error", "rgbd_processing_failed", device_id=device_id, bytes=len(data))
        await _send_model(ws, Error(code="internal_error", message="frame processing failed"))


@app.websocket("/ws/character")
async def ws_character(ws: WebSocket) -> None:
    runtime: AppRuntime = app.state.runtime
    await ws.accept()

    try:
        first = await asyncio.wait_for(ws.receive_text(), timeout=runtime.settings.hello_deadline_s)
    except (TimeoutError, WebSocketDisconnect):
        await ws.close()
        return

    try:
        CharacterHello.model_validate_json(first)
    except ValueError:
        await _send_model(ws, Error(code="invalid_message", message="expected character_hello"))
        await ws.close()
        return

    await _send_model(
        ws,
        CharacterServerHello(
            server_session_id=runtime.server_session_id,
            max_binary_bytes=runtime.settings.max_character_bytes,
            latest_frame_id=runtime.store.latest_frame_id,
        ),
    )

    latest = runtime.store.latest_encoded
    if latest is not None:
        await ws.send_bytes(latest)

    queue = runtime.hub.subscribe()
    text_queue = runtime.hub.subscribe_text()
    sender = asyncio.create_task(_character_sender(ws, queue))
    text_sender = asyncio.create_task(_character_text_sender(ws, text_queue))
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if (text := message.get("text")) is not None:
                await _handle_character_text(runtime, ws, text)
    except WebSocketDisconnect:
        pass
    finally:
        runtime.hub.unsubscribe(queue)
        runtime.hub.unsubscribe_text(text_queue)
        sender.cancel()
        text_sender.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender
            await text_sender


async def _character_sender(ws: WebSocket, queue: asyncio.Queue[bytes]) -> None:
    while True:
        encoded = await queue.get()
        await ws.send_bytes(encoded)


async def _character_text_sender(ws: WebSocket, queue: asyncio.Queue[str]) -> None:
    while True:
        payload = await queue.get()
        await ws.send_text(payload)


async def _handle_character_text(runtime: AppRuntime, ws: WebSocket, text: str) -> None:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        await _send_model(ws, Error(code="invalid_message", message="control frame not JSON"))
        return
    if await _handle_live_control(runtime, ws, obj):
        return
    if obj.get("type") == "request_capture":
        capture_id = UUID(obj["capture_id"]) if "capture_id" in obj else uuid4()
        request_id = UUID(obj["request_id"]) if "request_id" in obj else uuid4()
        accepted, code = await runtime.dispatch_capture(capture_id, obj.get("mode", "snapshot"))
        await _send_model(
            ws,
            Ack(request_id=request_id, accepted=accepted, code=code),
        )


async def _handle_live_control(runtime: AppRuntime, ws: WebSocket, obj: dict) -> bool:
    """Apply live/anchor control messages; True if handled."""
    kind = obj.get("type")
    request_id = UUID(obj["request_id"]) if "request_id" in obj else uuid4()
    if kind == "live_start":
        rate = obj.get("rate_hz")
        runtime.start_live(float(rate) if isinstance(rate, (int, float)) else None)
        await _send_model(
            ws,
            Ack(request_id=request_id, accepted=True, code="live_started" if runtime.live_active else "live_stopped"),
        )
        return True
    if kind == "live_stop":
        runtime.stop_live()
        await _send_model(ws, Ack(request_id=request_id, accepted=True, code="live_stopped"))
        return True
    if kind == "anchor_rig":
        accepted, code = await runtime.start_anchor(request_id)
        await _send_model(ws, Ack(request_id=request_id, accepted=accepted, code=code))
        return True
    return False


async def _send_model(ws: WebSocket, model) -> None:
    await ws.send_text(model.model_dump_json())
