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
    ClockPong,
    Error,
    LiveRequest,
    ServerHello,
)
from hmc_backend.observability import (
    configure_console_logging,
    configure_sentry,
    flush_sentry,
    log_event,
)
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
    configure_console_logging()
    configure_sentry(
        settings.sentry_dsn,
        environment=settings.environment,
        release=settings.release,
        traces_sample_rate=settings.traces_sample_rate,
    )
    app.state.runtime = build_runtime(settings)
    log_event(
        "info",
        "backend_started",
        bind_host=settings.bind_host,
        port=settings.port,
        expected_device_ids=",".join(settings.expected_device_ids),
        calibration_loaded=app.state.runtime.is_ready(),
    )
    yield
    await app.state.runtime.shutdown()
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
    log_event(
        "info",
        "capture_socket_opened",
        client_host=ws.client.host if ws.client else "unknown",
    )

    # 1. Hello handshake with deadline.
    try:
        first = await asyncio.wait_for(ws.receive_text(), timeout=runtime.settings.hello_deadline_s)
    except (TimeoutError, WebSocketDisconnect):
        log_event("warning", "capture_hello_missing")
        await ws.close()
        return

    try:
        hello = ClientHello.model_validate_json(first)
    except ValueError:
        log_event("warning", "capture_hello_rejected", reason="invalid_client_hello")
        await _send_model(ws, Error(code="invalid_message", message="expected client_hello"))
        await ws.close()
        return

    if not runtime.is_expected_device(hello.device_id):
        log_event(
            "warning",
            "capture_hello_rejected",
            device_id=hello.device_id,
            reason="unauthorized_device",
        )
        await _send_model(ws, Error(code="unauthorized_device", message="device not configured"))
        await ws.close()
        return

    token = runtime.register_capture(hello.device_id, ws.send_text)
    log_event(
        "info",
        "capture_device_connected",
        device_id=hello.device_id,
        session_id=str(hello.session_id),
        app_version=hello.app_version,
    )
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
                await _handle_capture_binary(runtime, ws, hello.device_id, data)
    except WebSocketDisconnect:
        pass
    finally:
        runtime.unregister_capture(hello.device_id, token)
        log_event("info", "capture_device_disconnected", device_id=hello.device_id)


async def _handle_capture_text(
    runtime: AppRuntime, ws: WebSocket, device_id: str, text: str
) -> None:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        await _send_model(ws, Error(code="invalid_message", message="control frame not JSON"))
        return
    if obj.get("type") == "clock_pong":
        import time

        try:
            pong = ClockPong.model_validate(obj)
            runtime.on_clock_pong(
                device_id,
                pong.request_id,
                pong.phone_receive_time_s,
                pong.phone_send_time_s,
                time.monotonic(),
            )
        except ValueError:
            await _send_model(ws, Error(code="invalid_message", message="invalid clock_pong"))
    elif obj.get("type") == "live_request":
        try:
            request = LiveRequest.model_validate(obj)
            log_event(
                "info",
                "live_request_received",
                device_id=device_id,
                request_id=str(request.request_id),
                enabled=request.enabled,
            )
            await runtime.request_live(request.request_id, request.enabled)
        except ValueError:
            log_event(
                "warning",
                "live_request_rejected",
                device_id=device_id,
                reason="invalid_live_request",
            )
            await _send_model(ws, Error(code="invalid_message", message="invalid live_request"))
    elif obj.get("type") == "ack":
        # Capture-request acknowledgements are diagnostic; frame arrival is authoritative.
        try:
            Ack.model_validate(obj)
        except ValueError:
            await _send_model(ws, Error(code="invalid_message", message="invalid ack"))
    else:
        await _send_model(ws, Error(code="invalid_message", message="unknown phone control type"))


async def _handle_capture_binary(
    runtime: AppRuntime, ws: WebSocket, device_id: str, data: bytes
) -> None:
    log_event(
        "info",
        "rgbd_packet_arrived",
        device_id=device_id,
        packet_bytes=len(data),
    )
    try:
        await runtime.handle_rgbd(data, expected_device_id=device_id)
    except EnvelopeError as exc:
        log_event(
            "warning",
            "rgbd_packet_rejected",
            device_id=device_id,
            packet_bytes=len(data),
            code=exc.code,
            reason=exc.message,
        )
        await _send_model(ws, Error(code=exc.code, message=exc.message))
    except Exception as exc:  # noqa: BLE001 - never let one frame kill the socket
        log_event(
            "error",
            "rgbd_packet_failed",
            device_id=device_id,
            packet_bytes=len(data),
            error_type=type(exc).__name__,
            reason=str(exc),
        )
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
