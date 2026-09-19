"""Drive `/ws/capture` as if two LiDAR iPhones were connected.

Nothing else in the repo exercises the capture ingress over a real WebSocket:
the route tests use an in-process client, and the replayer bypasses the socket
entirely. This harness connects as real clients, so the whole chain —
capture ingress, clock sync, pairing, reconstruction, publication, and the
Minecraft subscriber — can be rehearsed with **no hardware at all**.

It honours `capture_request`, so pressing **F7 in Minecraft** triggers a
synchronized capture end to end, exactly as the real rig will.

Typical hardware-free rehearsal, from ``backend/``::

    # 1. Write a rig both the backend and these fake phones agree on.
    uv run python scripts/fake_phone.py --write-calibration data/calibration.json

    # 2. Start the backend (it loads that calibration at startup).
    uv run uvicorn hmc_backend.api.app:app --host 0.0.0.0 --port 8000 --workers 1

    # 3. Connect both fake phones and wait for capture requests.
    uv run python scripts/fake_phone.py --calibration data/calibration.json

    # 4. Run the Fabric client, point it at the backend, press F7.

Add ``--once`` to fire one capture immediately without Minecraft, or
``--interval 2`` to stream live frames.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import time
from pathlib import Path
from uuid import UUID, uuid4

import numpy as np
import websockets

from hmc_backend.calibration.model import (
    CalibrationError,
    RigCalibration,
    load_rig_calibration,
    rig_to_json,
    save_rig_calibration,
)
from hmc_backend.calibration.synthetic import build_synthetic_rig
from hmc_backend.fixtures.scene import (
    encode_rgbd_packet,
    make_person_points,
    project_to_view,
)


class FakePhone:
    """One simulated capture device on its own WebSocket connection."""

    def __init__(
        self,
        device_id: str,
        url: str,
        rig: RigCalibration,
        *,
        splat: int = 2,
        verbose: bool = True,
        blank: bool = False,
    ) -> None:
        self.device_id = device_id
        self.url = url.rstrip("/") + "/ws/capture"
        self.rig = rig
        self.calib = rig.camera(device_id)
        self.session_id = uuid4()
        self.sequence = 0
        self.splat = splat
        self.verbose = verbose
        self.blank = blank
        self._views: dict[int, tuple] = {}
        self._ws: websockets.WebSocketClientProtocol | None = None
        self.captures_sent = 0

    # --- rendering --------------------------------------------------------

    def _view_for(self, pose_seed: int):
        """Render (and cache) this device's view of the synthetic person."""
        if self.blank:
            w, h = self.calib.rgb_size
            return (
                np.zeros((h, w, 3), np.uint8),
                np.zeros((h, w), np.float32),
                np.zeros((h, w), np.uint8),
            )
        if pose_seed not in self._views:
            xyz, rgb = make_person_points(pose_seed)
            self._views[pose_seed] = project_to_view(xyz, rgb, self.calib, splat=self.splat)
        return self._views[pose_seed]

    async def warm(self, pose_seed: int) -> None:
        """Pre-render this pose off the event loop.

        Rendering is slow Python. If it ran between the two devices' capture
        timestamps they would look hundreds of milliseconds apart and the pairer
        would (correctly) reject them, so it must happen *before* timestamping.
        """
        if pose_seed not in self._views:
            await asyncio.to_thread(self._view_for, pose_seed)

    def _packet(self, capture_id: UUID, pose_seed: int, capture_timestamp_s: float) -> bytes:
        colour, depth, confidence = self._view_for(pose_seed)
        self.sequence += 1
        return encode_rgbd_packet(
            device_id=self.device_id,
            session_id=self.session_id,
            capture_id=capture_id,
            sequence=self.sequence,
            # Stamped at "capture" time, in the same monotonic clock domain as
            # the clock-pong timestamps. Encoding happens after, as on a phone.
            capture_timestamp_s=capture_timestamp_s,
            calib=self.calib,
            rgb=colour,
            depth=depth,
            confidence=confidence,
        )

    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"[{self.device_id}] {message}")

    # --- protocol ---------------------------------------------------------

    async def _hello(self, ws) -> None:
        await ws.send(
            json.dumps(
                {
                    "type": "client_hello",
                    "protocol_version": 1,
                    "device_id": self.device_id,
                    "session_id": str(self.session_id),
                    "app_version": "fake-phone/0.1.0",
                    "platform": "ios",
                    "supports_scene_depth": True,
                    "image_orientation": "landscape_right",
                }
            )
        )
        reply = json.loads(await ws.recv())
        if reply.get("type") != "server_hello":
            raise RuntimeError(f"{self.device_id}: expected server_hello, got {reply}")
        self._log(f"connected (session {self.session_id})")

    async def send_capture(self, capture_id: UUID, pose_seed: int, capture_timestamp_s: float) -> None:
        if self._ws is None:
            self._log("not connected; skipping capture")
            return
        packet = self._packet(capture_id, pose_seed, capture_timestamp_s)
        await self._ws.send(packet)
        self.captures_sent += 1
        self._log(f"sent capture {capture_id} seq={self.sequence} ({len(packet)} bytes)")

    async def _handle_text(self, ws, text: str, on_capture_request) -> None:
        message = json.loads(text)
        kind = message.get("type")

        if kind == "clock_ping":
            # Reply in the phone's monotonic domain, as the real app does.
            receive = time.monotonic()
            await ws.send(
                json.dumps(
                    {
                        "type": "clock_pong",
                        "protocol_version": 1,
                        "request_id": message["request_id"],
                        "backend_send_time_s": message["backend_send_time_s"],
                        "phone_receive_time_s": receive,
                        "phone_send_time_s": time.monotonic(),
                    }
                )
            )
        elif kind == "capture_request":
            capture_id = UUID(message["capture_id"])
            self._log(f"capture_request {capture_id}")
            await on_capture_request(capture_id)
        elif kind == "error":
            self._log(f"ERROR from backend: {message.get('code')} - {message.get('message')}")
        elif kind == "ack":
            self._log(f"ack {message.get('code')} accepted={message.get('accepted')}")

    async def run(self, on_capture_request) -> None:
        async with websockets.connect(self.url, max_size=None) as ws:
            self._ws = ws
            await self._hello(ws)
            try:
                async for raw in ws:
                    if isinstance(raw, str):
                        await self._handle_text(ws, raw, on_capture_request)
            finally:
                self._ws = None


class CharacterSubscriber:
    """Stands in for the Minecraft client on ``/ws/character``.

    Decodes published frames with the strict decoder, so a run of this harness
    also proves the bytes the backend emits satisfy the same validation the
    Fabric mod applies.
    """

    def __init__(self, url: str, client_id: str = "fake-phone-verifier") -> None:
        self.url = url.rstrip("/") + "/ws/character"
        self.client_id = client_id
        self.baseline_frame_id: int | None = None
        self.received: list = []

    async def connect(self, stack: contextlib.AsyncExitStack):
        ws = await stack.enter_async_context(websockets.connect(self.url, max_size=None))
        await ws.send(
            json.dumps(
                {
                    "type": "character_hello",
                    "protocol_version": 1,
                    "client_id": self.client_id,
                }
            )
        )
        hello = json.loads(await ws.recv())
        if hello.get("type") != "character_server_hello":
            raise RuntimeError(f"expected character_server_hello, got {hello}")
        self.baseline_frame_id = hello.get("latest_frame_id")
        print(f"[subscriber] connected (latest_frame_id={self.baseline_frame_id})")
        self._ws = ws
        return ws

    async def wait_for_new_frame(self, timeout: float = 30.0):
        """Wait for a frame newer than the baseline, and validate it."""
        from hmc_backend.contracts.character_decode import decode_character_frame

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.1, deadline - time.monotonic())
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=remaining)
            except TimeoutError:
                break
            if not isinstance(raw, bytes):
                continue
            frame = decode_character_frame(raw)
            if self.baseline_frame_id is not None and frame.frame_id <= self.baseline_frame_id:
                continue  # stale snapshot sent on connect
            self.received.append(frame)
            return frame
        return None


class Rig:
    """Coordinates several fake phones so captures stay synchronized."""

    def __init__(self, phones: list[FakePhone], *, skew_ms: float = 10.0) -> None:
        self.phones = phones
        self._pose_seed = 7
        self._skew_ms = skew_ms
        self._rng = np.random.default_rng(0)

    async def capture(self, capture_id: UUID | None = None) -> UUID:
        """Fire one synchronized capture across every phone.

        Rendering is warmed first so it cannot inflate the apparent skew, then
        each device is stamped near a common instant with a small deliberate
        jitter, so the pairer is still exercised rather than handed an
        unrealistic zero-skew pair.
        """
        capture_id = capture_id or uuid4()
        await asyncio.gather(*(p.warm(self._pose_seed) for p in self.phones))

        base = time.monotonic()
        half = (self._skew_ms / 1000.0) / 2.0
        stamps = [base + float(self._rng.uniform(-half, half)) for _ in self.phones]

        await asyncio.gather(
            *(
                p.send_capture(capture_id, self._pose_seed, t)
                for p, t in zip(self.phones, stamps, strict=True)
            )
        )
        spread_ms = (max(stamps) - min(stamps)) * 1000.0
        print(f"  capture {capture_id} dispatched (simulated skew {spread_ms:.1f} ms)")
        return capture_id

    def next_pose(self) -> None:
        """Vary the pose so a recapture is visibly different."""
        self._pose_seed += 1


def _load_or_build_rig(path: str, devices: tuple[str, ...]) -> RigCalibration:
    try:
        rig = load_rig_calibration(path)
    except CalibrationError as exc:
        raise SystemExit(
            f"could not load calibration {path}: {exc}\n"
            f"Create one first:  uv run python scripts/fake_phone.py --write-calibration {path}"
        ) from exc

    missing = [d for d in devices if d not in rig.cameras]
    if missing:
        raise SystemExit(f"calibration {path} has no camera for: {', '.join(missing)}")
    return rig


async def _run(args) -> int:
    devices = tuple(d.strip() for d in args.devices.split(",") if d.strip())
    rig = _load_or_build_rig(args.calibration, devices)

    phones = [FakePhone(d, args.url, rig, splat=args.splat, blank=args.blank) for d in devices]
    control = Rig(phones, skew_ms=args.skew_ms)

    async def on_capture_request(capture_id: UUID) -> None:
        # One phone's request drives the whole rig; ignore the duplicate from
        # the second phone so a capture is not sent twice.
        if capture_id in _seen:
            return
        _seen.add(capture_id)
        await control.capture(capture_id)
        control.next_pose()

    _seen: set[UUID] = set()
    exit_code = 0

    async with contextlib.AsyncExitStack() as stack:
        subscriber: CharacterSubscriber | None = None
        if args.expect_frame:
            subscriber = CharacterSubscriber(args.url)
            await subscriber.connect(stack)

        tasks = [asyncio.create_task(p.run(on_capture_request)) for p in phones]
        # Give both sockets a moment to complete their hello handshake.
        await asyncio.sleep(0.75)

        async def driver() -> None:
            if args.once:
                await control.capture()
                control.next_pose()
                if subscriber is None:
                    await asyncio.sleep(args.linger)
            elif args.interval > 0:
                while True:
                    await control.capture()
                    control.next_pose()
                    await asyncio.sleep(args.interval)
            else:
                print("\nwaiting for capture_request (press F7 in Minecraft)... Ctrl-C to stop\n")

        drive = asyncio.create_task(driver())

        if subscriber is not None:
            await drive
            frame = await subscriber.wait_for_new_frame(timeout=args.timeout)
            if frame is None:
                print("\nFAIL: no CharacterFrame arrived on /ws/character")
                exit_code = 1
            else:
                valid = sum(1 for c in frame.colliders if c.valid)
                print("\nreceived CharacterFrame on /ws/character:")
                print(f"  frame_id        = {frame.frame_id}")
                print(f"  calibration_id  = {frame.calibration_id}")
                print(f"  mode            = {frame.mode}")
                print(f"  points          = {frame.point_count}")
                print(f"  landmarks       = {len(frame.landmarks)}")
                print(f"  colliders       = {len(frame.colliders)} ({valid} valid)")
                print(f"  pair_skew_ms    = {frame.pair_skew_ms:.2f}")
                print("  decoded with the strict decoder (same validation as the mod)")
            for t in tasks:
                t.cancel()
        else:
            if args.once:
                for t in tasks:
                    t.cancel()
            try:
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                pass

        drive.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await drive
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    total = sum(p.captures_sent for p in phones)
    print(f"\nsent {total} packet(s) across {len(phones)} device(s)")
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", default="ws://127.0.0.1:8000", help="backend base URL")
    parser.add_argument("--devices", default="front-phone,side-phone")
    parser.add_argument("--calibration", default="data/calibration.json")
    parser.add_argument("--splat", type=int, default=2, help="point splat radius when rendering")
    parser.add_argument("--blank", action="store_true", help="send blank/empty frames (0 depth) so real phone is unpolluted")
    parser.add_argument("--once", action="store_true", help="fire one capture then exit")
    parser.add_argument("--interval", type=float, default=0.0, help="seconds between live captures")
    parser.add_argument("--linger", type=float, default=2.0, help="seconds to wait after --once")
    parser.add_argument("--skew-ms", type=float, default=10.0, help="simulated inter-device capture skew")
    parser.add_argument(
        "--expect-frame",
        action="store_true",
        help="also subscribe to /ws/character and verify a CharacterFrame arrives (exit 1 if not)",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for a frame")
    parser.add_argument(
        "--write-calibration",
        metavar="PATH",
        help="write a synthetic rig calibration to PATH and exit",
    )
    parser.add_argument("--raster", type=int, nargs=2, default=(320, 240), metavar=("W", "H"))
    args = parser.parse_args()

    if args.write_calibration:
        w, h = args.raster
        rig = build_synthetic_rig(
            tuple(d.strip() for d in args.devices.split(",")),  # type: ignore[arg-type]
            rgb_size=(w, h),
            depth_size=(w, h),
        )
        save_rig_calibration(rig, args.write_calibration)
        print(f"wrote {args.write_calibration}")
        print(f"  calibration_id = {rig.calibration_id}")
        for device_id in rig.device_ids():
            pos = np.round(rig.camera(device_id).T_stage_from_optical[:3, 3], 3)
            print(f"  {device_id}: camera at stage {pos}")
        print("\nStart the backend so it loads this file, then run this script again.")
        _ = rig_to_json(rig)  # validates round-trip before anyone depends on it
        return 0

    if not Path(args.calibration).exists():
        raise SystemExit(
            f"no calibration at {args.calibration}\n"
            f"Create one:  uv run python scripts/fake_phone.py --write-calibration {args.calibration}"
        )

    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
