"""Solve a rig calibration from recorded ChArUco board captures.

Board frames are captured through the normal `/ws/capture` path and recorded
with `save_recording()`. This script replays those recordings offline, solves
each camera's pose against the board, validates on held-out frames, and writes
`calibration.json`. Nothing new goes on the wire, and the solve is reproducible
from saved bytes without the rig or the subject present.

Run from ``backend/``::

    # one recording directory per board pose (more poses = better)
    uv run python scripts/calibrate.py --recordings data/recordings --out data/calibration.json

Then restart the backend; it loads the calibration at startup.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

import numpy as np

from hmc_backend.calibration.charuco import (
    BoardObservation,
    BoardSpec,
    CharucoError,
    build_board,
    observe_frame,
    solve_camera,
    stage_from_board,
    validate_solution,
)
from hmc_backend.calibration.model import RigCalibration, save_rig_calibration
from hmc_backend.capture.recording import load_recording
from hmc_backend.capture.rgbd_ingest import decode_rgbd_frame
from hmc_backend.contracts.internal import CameraCalibration
from hmc_backend.protocol.envelope import decode_envelope


class DeviceFrames:
    """Accumulated board observations and raster metadata for one device."""

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.observations: list[BoardObservation] = []
        self.rejections: dict[str, int] = {}
        self.k_rgb: np.ndarray | None = None
        self.rgb_size: tuple[int, int] | None = None
        self.depth_size: tuple[int, int] | None = None
        self.image_orientation: str | None = None

    def note_rejection(self, reason: str) -> None:
        self.rejections[reason] = self.rejections.get(reason, 0) + 1


def collect(recording_dirs: list[Path], spec: BoardSpec) -> dict[str, DeviceFrames]:
    """Decode every recorded packet and detect the board in each RGB frame."""
    board, detector = build_board(spec)
    devices: dict[str, DeviceFrames] = {}

    for capture_dir in recording_dirs:
        try:
            recording = load_recording(capture_dir)
        except (OSError, ValueError) as exc:
            print(f"  ! skipping {capture_dir.name}: {exc}")
            continue

        for device_id, raw in recording.packets.items():
            state = devices.setdefault(device_id, DeviceFrames(device_id))
            try:
                decoded = decode_rgbd_frame(decode_envelope(raw))
            except Exception as exc:  # noqa: BLE001 - one bad packet must not abort
                state.note_rejection("undecodable_packet")
                print(f"  ! {device_id} @ {capture_dir.name}: {exc}")
                continue

            header = decoded.header
            rgb_size = (header.rgb.width, header.rgb.height)
            depth_size = (header.depth.width, header.depth.height)
            orientation = header.image_orientation.value
            if state.rgb_size is not None and state.rgb_size != rgb_size:
                state.note_rejection("rgb_size_changed")
                continue
            if state.depth_size is not None and state.depth_size != depth_size:
                state.note_rejection("depth_size_changed")
                continue
            if state.image_orientation is not None and state.image_orientation != orientation:
                state.note_rejection("image_orientation_changed")
                continue
            state.k_rgb = decoded.k_rgb
            state.rgb_size = rgb_size
            state.depth_size = depth_size
            state.image_orientation = orientation

            obs, reason = observe_frame(
                decoded.rgb,
                decoded.k_rgb,
                board,
                detector,
                device_id=device_id,
                sequence=header.sequence,
            )
            if obs is None:
                state.note_rejection(reason)
            else:
                state.observations.append(obs)

    return devices


def capture_from_backend(base_url: str, count: int, timeout_s: float) -> list[Path]:
    """Request ``count`` synchronized raw pairs and return their saved paths."""
    base = base_url.rstrip("/")
    paths: list[Path] = []
    for index in range(count):
        try:
            with urlopen(Request(f"{base}/calibration/captures", method="POST"), timeout=5) as response:
                requested = json.load(response)
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RuntimeError(f"could not request calibration capture: {exc}") from exc
        capture_id = requested["capture_id"]
        deadline = time.monotonic() + timeout_s
        while True:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"capture {capture_id} did not complete within {timeout_s:g}s")
            try:
                with urlopen(f"{base}/calibration/captures/{capture_id}", timeout=5) as response:
                    status = json.load(response)
            except (HTTPError, URLError, TimeoutError) as exc:
                raise RuntimeError(f"could not query calibration capture {capture_id}: {exc}") from exc
            if status["state"] == "complete":
                paths.append(Path(status["saved_recording_path"]))
                print(f"captured {index + 1}/{count}: {capture_id}")
                break
            if status["state"] == "failed":
                raise RuntimeError(f"capture {capture_id} failed: {status['failure_code']}")
            time.sleep(0.1)
    return paths


def split_held_out(
    observations: list[BoardObservation], fraction: float
) -> tuple[list[BoardObservation], list[BoardObservation]]:
    """Deterministically hold out a fraction of frames for validation."""
    if fraction <= 0 or len(observations) < 4:
        return observations, []
    ordered = sorted(observations, key=lambda o: o.sequence)
    step = max(2, round(1.0 / fraction))
    held = [o for i, o in enumerate(ordered) if i % step == step - 1]
    train = [o for o in ordered if o not in held]
    if len(train) < 2:
        return ordered, []
    return train, held


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--recordings", default="data/recordings", help="root of capture dirs")
    parser.add_argument("--out", default="data/calibration.json")
    parser.add_argument("--url", help="running backend base URL; collect synchronized pairs before solving")
    parser.add_argument("--capture-count", type=int, default=8)
    parser.add_argument("--capture-timeout-s", type=float, default=10.0)
    parser.add_argument("--devices", default="front-phone,side-phone")
    parser.add_argument("--dictionary", default="DICT_5X5_100")
    parser.add_argument("--squares-x", type=int, default=7)
    parser.add_argument("--squares-y", type=int, default=10)
    parser.add_argument("--square-length-m", type=float, default=0.04)
    parser.add_argument("--marker-length-m", type=float, default=0.03)
    parser.add_argument(
        "--board-origin-stage-m",
        nargs=3,
        type=float,
        default=(0.0, 0.0, 0.0),
        metavar=("X", "Y", "Z"),
        help="where the board's origin corner sits in stage coordinates",
    )
    parser.add_argument("--held-out-fraction", type=float, default=0.25)
    parser.add_argument("--min-frames", type=int, default=4)
    parser.add_argument("--max-angle-deg", type=float, default=5.0)
    parser.add_argument("--max-position-error-m", type=float, default=0.03)
    parser.add_argument("--max-reprojection-error-px", type=float, default=3.0)
    parser.add_argument("--min-held-out", type=int, default=2)
    args = parser.parse_args()

    spec = BoardSpec(
        dictionary=args.dictionary,
        squares_x=args.squares_x,
        squares_y=args.squares_y,
        square_length_m=args.square_length_m,
        marker_length_m=args.marker_length_m,
    )

    root = Path(args.recordings)
    try:
        capture_dirs = (
            capture_from_backend(args.url, args.capture_count, args.capture_timeout_s)
            if args.url
            else sorted(d for d in root.iterdir() if (d / "manifest.json").exists())
        )
    except (OSError, RuntimeError) as exc:
        print(f"calibration capture failed: {exc}")
        return 1
    if not capture_dirs:
        print(f"no recordings with a manifest under {root}")
        return 1
    print(f"scanning {len(capture_dirs)} recording(s) under {root}")

    devices = collect(capture_dirs, spec)
    expected_devices = tuple(part.strip() for part in args.devices.split(",") if part.strip())
    if len(expected_devices) != 2 or len(set(expected_devices)) != 2:
        print("--devices must contain exactly two unique IDs")
        return 1
    if set(devices) != set(expected_devices):
        print(f"device mismatch: expected {sorted(expected_devices)}, found {sorted(devices)}")
        print("no device packets found")
        return 1

    t_stage_from_board = stage_from_board(tuple(args.board_origin_stage_m))
    calibration_id = uuid4()
    created = datetime.now(UTC)

    cameras: dict[str, CameraCalibration] = {}
    validation: dict[str, dict] = {}
    failed = False

    for device_id, state in sorted(devices.items()):
        n = len(state.observations)
        print(f"\n{device_id}: {n} accepted board frame(s)")
        for reason, count in sorted(state.rejections.items()):
            print(f"    rejected {count:3d}  {reason}")

        if n < args.min_frames:
            print(f"    !! need at least {args.min_frames} accepted frames")
            failed = True
            continue
        if state.k_rgb is None or state.rgb_size is None or state.depth_size is None:
            print("    !! missing raster metadata")
            failed = True
            continue

        train, held = split_held_out(state.observations, args.held_out_fraction)
        try:
            solution = solve_camera(
                train,
                device_id=device_id,
                t_stage_from_board=t_stage_from_board,
                max_angle_deg=args.max_angle_deg,
                rejection_reasons=state.rejections,
            )
        except CharucoError as exc:
            print(f"    !! solve failed: {exc}")
            failed = True
            continue

        report = validate_solution(solution, held, t_stage_from_board=t_stage_from_board)
        pos = solution.camera_position_stage_m
        print(f"    camera at stage  x={pos[0]:+.3f} y={pos[1]:+.3f} z={pos[2]:+.3f} m")
        print(
            f"    reprojection     median={solution.median_reprojection_error_px:.2f}px"
            f"  max={solution.max_reprojection_error_px:.2f}px"
        )
        if report.get("held_out_frames"):
            print(
                f"    held-out ({report['held_out_frames']}) position error"
                f"  median={report['median_position_error_m']*100:.1f}cm"
                f"  max={report['max_position_error_m']*100:.1f}cm"
            )
        else:
            print("    held-out         none (too few frames) - capture more poses")

        if solution.max_reprojection_error_px > args.max_reprojection_error_px:
            print(
                f"    !! reprojection error {solution.max_reprojection_error_px:.2f}px exceeds "
                f"{args.max_reprojection_error_px:.2f}px"
            )
            failed = True
        if report.get("held_out_frames", 0) < args.min_held_out:
            print(f"    !! need at least {args.min_held_out} held-out frames")
            failed = True
        if report.get("max_position_error_m", float("inf")) > args.max_position_error_m:
            print(
                f"    !! held-out position error {report.get('max_position_error_m', float('inf')):.3f}m "
                f"exceeds {args.max_position_error_m:.3f}m"
            )
            failed = True

        if pos[1] <= 0:
            print("    !! camera solved to at/below floor level - check board placement")
            failed = True

        cameras[device_id] = CameraCalibration(
            calibration_id=calibration_id,
            device_id=device_id,
            rgb_size=state.rgb_size,
            depth_size=state.depth_size,
            K_rgb=state.k_rgb,
            T_stage_from_optical=solution.T_stage_from_optical,
            reprojection_error_px=solution.median_reprojection_error_px,
            created_at_utc=created,
            image_orientation=state.image_orientation or "landscape_right",
        )
        validation[device_id] = {
            "accepted_frames": solution.accepted_count,
            "median_reprojection_error_px": solution.median_reprojection_error_px,
            "max_reprojection_error_px": solution.max_reprojection_error_px,
            "rejection_reasons": solution.rejection_reasons,
            **report,
        }

    if failed or not cameras:
        print("\ncalibration NOT written - resolve the problems above")
        return 1

    rig = RigCalibration(
        calibration_id=calibration_id,
        created_at_utc=created,
        stage_definition={
            "unit": "meter",
            "x": "front_camera_image_right",
            "y": "up",
            "z": "toward_front_camera",
        },
        board=spec.to_json(),
        cameras=cameras,
        validation=validation,
    )
    output = Path(args.out)
    temporary = output.with_suffix(output.suffix + ".tmp")
    save_rig_calibration(rig, temporary)
    temporary.replace(output)

    print(f"\nwrote {args.out}")
    print(f"  calibration_id = {calibration_id}")
    print(f"  cameras        = {', '.join(sorted(cameras))}")
    if len(cameras) >= 2:
        ids = sorted(cameras)
        a = cameras[ids[0]].T_stage_from_optical[:3, 3]
        b = cameras[ids[1]].T_stage_from_optical[:3, 3]
        print(f"  baseline       = {np.linalg.norm(a - b):.3f} m between cameras")
    print("\nSanity-check these numbers against a tape measure before trusting them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
