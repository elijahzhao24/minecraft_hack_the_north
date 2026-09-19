"""Immutable recording of raw capture packets for deterministic replay.

Each capture directory stores the exact ``.hmc`` envelopes plus a manifest with
SHA-256 hashes and byte counts, so a later defect can be reproduced without a
person standing at the cameras. Recordings may contain personal RGB/depth data
and are git-ignored.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Recording:
    """A loaded recording: capture id, per-device packet bytes, calibration."""

    capture_id: UUID
    packets: dict[str, bytes]
    calibration_json: dict | None
    manifest: dict


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_recording(
    root: str | Path,
    capture_id: UUID,
    packets: dict[str, bytes],
    *,
    calibration_json: dict | None = None,
    app_release: str = "unknown",
    backend_release: str = "hmc-backend@0.1.0",
    pair_skew_ms: float | None = None,
    consent_note: str = "synthetic fixture; no personal data",
) -> Path:
    """Write an immutable recording directory and return its path."""
    capture_dir = Path(root) / str(capture_id)
    capture_dir.mkdir(parents=True, exist_ok=True)

    files = {}
    for device_id, raw in packets.items():
        (capture_dir / f"{device_id}.hmc").write_bytes(raw)
        files[device_id] = {"sha256": _sha256(raw), "bytes": len(raw)}

    if calibration_json is not None:
        (capture_dir / "calibration.json").write_text(json.dumps(calibration_json, indent=2))

    manifest = {
        "capture_id": str(capture_id),
        "received_utc": datetime.now(UTC).isoformat(),
        "files": files,
        "app_release": app_release,
        "backend_release": backend_release,
        "pair_skew_ms": pair_skew_ms,
        "consent_note": consent_note,
    }
    (capture_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return capture_dir


def load_recording(capture_dir: str | Path) -> Recording:
    """Load a recording, verifying each packet's SHA-256 against the manifest."""
    capture_dir = Path(capture_dir)
    manifest = json.loads((capture_dir / "manifest.json").read_text())

    packets: dict[str, bytes] = {}
    for device_id, meta in manifest["files"].items():
        raw = (capture_dir / f"{device_id}.hmc").read_bytes()
        actual = _sha256(raw)
        if actual != meta["sha256"]:
            raise ValueError(f"{device_id}.hmc sha256 mismatch (recording corrupted)")
        packets[device_id] = raw

    calib_path = capture_dir / "calibration.json"
    calibration_json = json.loads(calib_path.read_text()) if calib_path.exists() else None

    return Recording(
        capture_id=UUID(manifest["capture_id"]),
        packets=packets,
        calibration_json=calibration_json,
        manifest=manifest,
    )
