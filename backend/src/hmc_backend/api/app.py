"""FastAPI application: lifespan wiring, /health, and the capture/character WS.

One Uvicorn worker owns all state. Startup loads calibration (if present) and
builds the runtime; the capture socket ingests HMC1 RGBD frames and the
character socket streams the latest published snapshot to Minecraft.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from hmc_backend.api.runtime import AppRuntime
from hmc_backend.calibration.model import CalibrationError, load_rig_calibration
from hmc_backend.contracts.control import (
    Ack,
    CharacterHello,
    CharacterServerHello,
    ClientHello,
    Error,
    ServerHello,
)
from hmc_backend.observability import configure_sentry, flush_sentry
from hmc_backend.protocol.envelope import EnvelopeError
from hmc_backend.settings import Settings, load_settings


def build_runtime(settings: Settings) -> AppRuntime:
    """Load calibration (if any) and construct the runtime."""
    calibration = None
    try:
        calibration = load_rig_calibration(settings.calibration_path)
    except CalibrationError:
        calibration = None
    return AppRuntime(settings, calibration)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_settings()
    configure_sentry(
        settings.sentry_dsn,
        environment=settings.environment,
        release=settings.release,
        traces_sample_rate=settings.traces_sample_rate,
    )
    app.state.runtime = build_runtime(settings)
    yield
    # Shutdown: flush any pending Sentry events with a short timeout.
    flush_sentry()


app = FastAPI(title="hmc-backend", lifespan=lifespan)


@app.get("/health")
async def health() -> JSONResponse:
    runtime: AppRuntime = app.state.runtime
    response, status_code = runtime.health()
    return JSONResponse(content=json.loads(response.model_dump_json()), status_code=status_code)


@app.websocket("/ws/capture")
async def ws_capture(ws: WebSocket) -> None:
    runtime: AppRuntime = app.state.runtime
    await ws.accept()

    # 1. Hello handshake with deadline.
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

    # 2. Receive loop.
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if (text := message.get("text")) is not None:
                await _handle_capture_text(runtime, ws, hello.device_id, text)
            elif (data := message.get("bytes")) is not None:
                await _handle_capture_binary(runtime, ws, data)
    except WebSocketDisconnect:
        pass
    finally:
        runtime.unregister_capture(hello.device_id, token)


async def _handle_capture_text(runtime: AppRuntime, ws: WebSocket, device_id: str, text: str) -> None:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        await _send_model(ws, Error(code="invalid_message", message="control frame not JSON"))
        return
    if obj.get("type") == "clock_pong":
        # Backend records its own receive time on arrival.
        import time

        t3 = time.monotonic()
        runtime.on_clock_pong(
            device_id,
            float(obj["backend_send_time_s"]),
            float(obj["phone_receive_time_s"]),
            float(obj["phone_send_time_s"]),
            t3,
        )


async def _handle_capture_binary(runtime: AppRuntime, ws: WebSocket, data: bytes) -> None:
    try:
        await runtime.handle_rgbd(data)
    except EnvelopeError as exc:
        await _send_model(ws, Error(code=exc.code, message=exc.message))
    except Exception:  # noqa: BLE001 - never let one frame kill the socket
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

    # Immediately send the latest snapshot, if any.
    latest = runtime.store.latest_encoded
    if latest is not None:
        await ws.send_bytes(latest)

    queue = runtime.hub.subscribe()
    sender = asyncio.create_task(_character_sender(ws, queue))
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
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender


async def _character_sender(ws: WebSocket, queue: asyncio.Queue[bytes]) -> None:
    while True:
        encoded = await queue.get()
        await ws.send_bytes(encoded)


async def _handle_character_text(runtime: AppRuntime, ws: WebSocket, text: str) -> None:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        await _send_model(ws, Error(code="invalid_message", message="control frame not JSON"))
        return
    if obj.get("type") == "request_capture":
        from uuid import UUID

        capture_id = UUID(obj["capture_id"])
        accepted, code = await runtime.dispatch_capture(capture_id, obj.get("mode", "snapshot"))
        await _send_model(
            ws,
            Ack(request_id=UUID(obj["request_id"]), accepted=accepted, code=code),
        )


async def _send_model(ws: WebSocket, model) -> None:
    await ws.send_text(model.model_dump_json())
