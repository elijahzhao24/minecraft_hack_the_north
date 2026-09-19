"""Generate the phone->backend RGBD golden fixtures.

`contracts/fixtures` had golden CHARACTER_FRAME packets (backend -> Minecraft)
but nothing for the RGBD direction, so the Swift encoder and the Python decoder
shared no bytes. These fixtures close that half of the interoperability gate:
Swift must produce bytes the Python decoder accepts, and must decode these.

Output goes to ``contracts/fixtures/rgbd/`` so the Java-authored CharacterFrame
fixtures are left untouched.

Regenerate (fixtures are frozen in version control, so review any diff)::

    uv run python scripts/write_rgbd_fixtures.py

Assertions are made against **raw buffer digests** rather than decoded RGB
pixels: JPEG decoding differs by a bit or two between libjpeg builds, so
byte-level digests are the decoder-independent thing to compare.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path
from uuid import UUID

import numpy as np

from hmc_backend.calibration.synthetic import build_synthetic_rig
from hmc_backend.fixtures.scene import (
    encode_rgbd_packet,
    make_person_points,
    project_to_view,
)
from hmc_backend.protocol.buffers import load_buffers
from hmc_backend.protocol.envelope import MAGIC, decode_envelope

# Fixed identities so regeneration is byte-stable.
SESSION_ID = UUID("44487d7c-b847-49db-aa37-cf326ad76078")
CAPTURE_ID = UUID("6ee77aca-80b0-43e5-be8e-bb61c17eb8a4")
CALIBRATION_ID = UUID("f766f462-d405-4c30-89f9-f626da70547b")
DEVICE_ID = "front-phone"
SEQUENCE = 184
CAPTURE_TIMESTAMP_S = 9922.107184
RASTER = (64, 48)
POSE_SEED = 7

NAME = "rgbd_frame_neutral"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_golden() -> tuple[bytes, dict]:
    """Render one deterministic RGBD packet and summarise it."""
    rig = build_synthetic_rig(rgb_size=RASTER, depth_size=RASTER, calibration_id=CALIBRATION_ID)
    calib = rig.camera(DEVICE_ID)

    xyz, rgb = make_person_points(POSE_SEED)
    colour, depth, confidence = project_to_view(xyz, rgb, calib, splat=2)

    raw = encode_rgbd_packet(
        device_id=DEVICE_ID,
        session_id=SESSION_ID,
        capture_id=CAPTURE_ID,
        sequence=SEQUENCE,
        capture_timestamp_s=CAPTURE_TIMESTAMP_S,
        calib=calib,
        rgb=colour,
        depth=depth,
        confidence=confidence,
    )

    envelope = decode_envelope(raw)
    buffers = load_buffers(envelope.header, envelope.payload)

    valid = depth[depth > 0.0]
    # A deterministic scatter of depth samples for other decoders to check.
    flat = depth.reshape(-1)
    sample_idx = [i for i in range(0, flat.size, max(1, flat.size // 12))][:12]

    expected = {
        "sha256": _sha(raw),
        "total_bytes": len(raw),
        "envelope": {
            "magic": MAGIC.decode(),
            "version": 1,
            "message_type": 1,
            "header_bytes": struct.unpack_from("<I", raw, 8)[0],
            "payload_bytes": struct.unpack_from("<I", raw, 12)[0],
        },
        "device_id": DEVICE_ID,
        "session_id": str(SESSION_ID),
        "capture_id": str(CAPTURE_ID),
        "sequence": SEQUENCE,
        "capture_timestamp_s": CAPTURE_TIMESTAMP_S,
        "image_orientation": "landscape_right",
        "tracking_state": "normal",
        "rgb": {
            "width": RASTER[0],
            "height": RASTER[1],
            "intrinsics_row_major": calib.K_rgb.reshape(-1).tolist(),
        },
        "depth": {
            "width": RASTER[0],
            "height": RASTER[1],
            "unit": "meter",
            "confidence_encoding": "arkit_0_1_2",
        },
        "buffers": [
            {
                "name": d.name,
                "encoding": d.encoding.value,
                "offset": d.offset,
                "length": d.length,
                "shape": list(d.shape) if d.shape else None,
                "sha256": _sha(buffers.raw(d.name)),
            }
            for d in (buffers.descriptor(n) for n in ("rgb", "depth", "confidence"))
        ],
        "depth_stats": {
            "valid_count": int(valid.size),
            "min_m": float(valid.min()),
            "max_m": float(valid.max()),
            "mean_m": float(valid.mean()),
        },
        "confidence_histogram": {
            str(v): int((confidence == v).sum()) for v in sorted(np.unique(confidence).tolist())
        },
        # float32 values are exact on the wire, so these are safe to compare.
        "sample_depth_m": [
            {"index": int(i), "row": int(i // RASTER[0]), "col": int(i % RASTER[0]), "value": float(flat[i])}
            for i in sample_idx
        ],
    }
    return raw, expected


def _mutate(raw: bytes, header: dict, payload: bytes) -> bytes:
    """Re-encode an envelope from an explicit header/payload (no validation)."""
    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    prefix = struct.pack("<4sHHII", MAGIC, 1, 1, len(header_bytes), len(payload))
    return prefix + header_bytes + payload


def build_malformed(raw: bytes) -> dict[str, tuple[bytes, str]]:
    """Malformed RGBD packets mapped to the code a decoder must raise."""
    envelope = decode_envelope(raw)
    header = envelope.header
    payload = envelope.payload
    header_len = struct.unpack_from("<I", raw, 8)[0]
    body = raw[16:]

    cases: dict[str, tuple[bytes, str]] = {}

    cases["wrong_magic"] = (b"XXXX" + raw[4:], "invalid_message")

    cases["unsupported_envelope_version"] = (
        struct.pack("<4sHHII", MAGIC, 2, 1, header_len, len(payload)) + body,
        "unsupported_version",
    )
    cases["character_message_type"] = (
        struct.pack("<4sHHII", MAGIC, 1, 2, header_len, len(payload)) + body,
        "invalid_message",
    )
    cases["truncated_payload"] = (raw[:-64], "invalid_message")
    cases["short_header"] = (raw[:12], "invalid_message")
    cases["payload_over_limit"] = (
        struct.pack("<4sHHII", MAGIC, 1, 1, header_len, 64 * 1024 * 1024) + body,
        "frame_too_large",
    )

    overlapping = json.loads(json.dumps(header))
    overlapping["buffers"][1]["offset"] = overlapping["buffers"][0]["offset"]
    cases["overlapping_buffers"] = (_mutate(raw, overlapping, payload), "invalid_buffer_range")

    past = json.loads(json.dumps(header))
    past["buffers"][-1]["length"] = past["buffers"][-1]["length"] + 4096
    cases["buffer_past_payload"] = (_mutate(raw, past, payload), "invalid_buffer_range")

    bad_utf8 = b"\xff\xfe not utf-8"
    cases["invalid_utf8_header"] = (
        struct.pack("<4sHHII", MAGIC, 1, 1, len(bad_utf8), 0) + bad_utf8,
        "invalid_message",
    )

    nan_header = json.dumps(header, separators=(",", ":")).replace(
        '"capture_timestamp_s":9922.107184', '"capture_timestamp_s":NaN'
    ).encode("utf-8")
    cases["nan_in_header"] = (
        struct.pack("<4sHHII", MAGIC, 1, 1, len(nan_header), len(payload)) + nan_header + payload,
        "invalid_message",
    )

    future = json.loads(json.dumps(header))
    future["schema_version"] = 2
    cases["future_schema_version"] = (_mutate(raw, future, payload), "unsupported_version")

    unknown_field = json.loads(json.dumps(header))
    unknown_field["surprise"] = "field"
    cases["unknown_header_field"] = (_mutate(raw, unknown_field, payload), "invalid_message")

    bad_orientation = json.loads(json.dumps(header))
    bad_orientation["image_orientation"] = "diagonal"
    cases["unknown_orientation_enum"] = (_mutate(raw, bad_orientation, payload), "invalid_message")

    bad_tracking = json.loads(json.dumps(header))
    bad_tracking["tracking_state"] = "vibes"
    cases["unknown_tracking_state_enum"] = (_mutate(raw, bad_tracking, payload), "invalid_message")

    # The declared depth raster disagrees with the buffer's shape. The buffer
    # range itself is fine, so this is a header/message consistency failure
    # rather than invalid_buffer_range.
    bad_dims = json.loads(json.dumps(header))
    bad_dims["depth"]["width"] = bad_dims["depth"]["width"] + 1
    cases["depth_dims_mismatch"] = (_mutate(raw, bad_dims, payload), "invalid_message")

    bad_intrinsics = json.loads(json.dumps(header))
    bad_intrinsics["rgb"]["intrinsics_row_major"] = [1.0, 2.0, 3.0]
    cases["intrinsics_wrong_length"] = (_mutate(raw, bad_intrinsics, payload), "invalid_message")

    negative_sequence = json.loads(json.dumps(header))
    negative_sequence["sequence"] = -1
    cases["negative_sequence"] = (_mutate(raw, negative_sequence, payload), "invalid_message")

    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "rgbd"),
    )
    args = parser.parse_args()

    out = Path(args.out)
    (out / "malformed").mkdir(parents=True, exist_ok=True)

    raw, expected = build_golden()
    (out / f"{NAME}.hmc").write_bytes(raw)
    envelope = decode_envelope(raw)
    (out / f"{NAME}.header.json").write_text(json.dumps(envelope.header, indent=2) + "\n")
    (out / f"{NAME}.expected.json").write_text(json.dumps(expected, indent=2) + "\n")

    malformed = build_malformed(raw)
    codes = {}
    for name, (data, code) in sorted(malformed.items()):
        (out / "malformed" / f"{name}.hmc").write_bytes(data)
        codes[name] = code
    (out / "malformed_expected_codes.json").write_text(json.dumps(codes, indent=2) + "\n")

    lines = []
    for path in [
        out / f"{NAME}.hmc",
        out / f"{NAME}.header.json",
        out / f"{NAME}.expected.json",
        out / "malformed_expected_codes.json",
        *sorted((out / "malformed").glob("*.hmc")),
    ]:
        rel = path.relative_to(out)
        lines.append(f"{_sha(path.read_bytes())}  {rel}")
    (out / "SHA256SUMS").write_text("\n".join(lines) + "\n")

    print(f"wrote RGBD fixtures to {out}")
    print(f"  {NAME}.hmc  {len(raw)} bytes  sha256={expected['sha256'][:16]}...")
    print(f"  raster {RASTER[0]}x{RASTER[1]}, {expected['depth_stats']['valid_count']} valid depth px")
    print(f"  {len(codes)} malformed fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
