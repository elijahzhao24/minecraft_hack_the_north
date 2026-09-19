"""Interoperability gate for the phone -> backend (RGBD) direction.

The shared fixtures previously covered only CHARACTER_FRAME (backend ->
Minecraft), so the Swift encoder and the Python decoder had no bytes in common.
These tests pin the Python decoder against the golden RGBD packet and the
malformed set in ``contracts/fixtures/rgbd/``.

Swift should assert the same digests and error codes; the buffer comparisons use
raw SHA-256 rather than decoded RGB pixels because JPEG decoding differs
slightly between libjpeg builds.

Regenerate with ``uv run python scripts/write_rgbd_fixtures.py`` (the fixtures
are frozen in version control — review any diff).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hmc_backend.capture.rgbd_ingest import decode_rgbd_frame
from hmc_backend.protocol.buffers import load_buffers
from hmc_backend.protocol.envelope import EnvelopeError, decode_envelope

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures" / "rgbd"
NAME = "rgbd_frame_neutral"

pytestmark = pytest.mark.skipif(
    not FIXTURES.exists(), reason="contracts/fixtures/rgbd not present in this checkout"
)


def _raw() -> bytes:
    return (FIXTURES / f"{NAME}.hmc").read_bytes()


def _expected() -> dict:
    return json.loads((FIXTURES / f"{NAME}.expected.json").read_text())


# --- integrity -------------------------------------------------------------

def test_sha256sums_match_files():
    listed = 0
    for line in (FIXTURES / "SHA256SUMS").read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        digest, _, filename = line.partition("  ")
        path = FIXTURES / filename.strip()
        assert path.exists(), f"SHA256SUMS lists a missing file: {filename}"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, (
            f"{filename} digest changed (fixtures are frozen)"
        )
        listed += 1
    assert listed >= 5


def test_golden_digest_and_size():
    exp = _expected()
    raw = _raw()
    assert hashlib.sha256(raw).hexdigest() == exp["sha256"]
    assert len(raw) == exp["total_bytes"]


def test_envelope_framing_matches_expected():
    exp = _expected()["envelope"]
    envelope = decode_envelope(_raw())
    assert int(envelope.message_type) == exp["message_type"]
    assert _raw()[:4].decode() == exp["magic"]


# --- golden decode ---------------------------------------------------------

def test_header_fields_match_expected():
    exp = _expected()
    decoded = decode_rgbd_frame(decode_envelope(_raw()))
    h = decoded.header

    assert h.device_id == exp["device_id"]
    assert str(h.session_id) == exp["session_id"]
    assert str(h.capture_id) == exp["capture_id"]
    assert h.sequence == exp["sequence"]
    assert h.capture_timestamp_s == pytest.approx(exp["capture_timestamp_s"])
    assert h.image_orientation.value == exp["image_orientation"]
    assert h.tracking_state.value == exp["tracking_state"]
    assert (h.rgb.width, h.rgb.height) == (exp["rgb"]["width"], exp["rgb"]["height"])
    assert (h.depth.width, h.depth.height) == (exp["depth"]["width"], exp["depth"]["height"])


def test_intrinsics_match_expected():
    exp = _expected()
    decoded = decode_rgbd_frame(decode_envelope(_raw()))
    flat = decoded.k_rgb.reshape(-1).tolist()
    assert flat == pytest.approx(exp["rgb"]["intrinsics_row_major"])


def test_buffer_digests_match_expected():
    """Byte-exact buffer comparison, independent of any image decoder."""
    exp = _expected()
    envelope = decode_envelope(_raw())
    buffers = load_buffers(envelope.header, envelope.payload)

    for entry in exp["buffers"]:
        descriptor = buffers.descriptor(entry["name"])
        assert descriptor.encoding.value == entry["encoding"]
        assert descriptor.offset == entry["offset"]
        assert descriptor.length == entry["length"]
        if entry["shape"] is not None:
            assert list(descriptor.shape) == entry["shape"]
        assert hashlib.sha256(buffers.raw(entry["name"])).hexdigest() == entry["sha256"]


def test_sample_depth_values_match_expected():
    """float32 depth is exact on the wire, so these compare exactly."""
    exp = _expected()
    decoded = decode_rgbd_frame(decode_envelope(_raw()))
    flat = decoded.depth_m.reshape(-1)
    for sample in exp["sample_depth_m"]:
        assert float(flat[sample["index"]]) == pytest.approx(sample["value"], abs=1e-7)
        assert float(decoded.depth_m[sample["row"], sample["col"]]) == pytest.approx(
            sample["value"], abs=1e-7
        )


def test_depth_stats_match_expected():
    exp = _expected()["depth_stats"]
    decoded = decode_rgbd_frame(decode_envelope(_raw()))
    valid = decoded.depth_m[decoded.depth_m > 0.0]
    assert int(valid.size) == exp["valid_count"]
    assert float(valid.min()) == pytest.approx(exp["min_m"], abs=1e-6)
    assert float(valid.max()) == pytest.approx(exp["max_m"], abs=1e-6)
    assert float(valid.mean()) == pytest.approx(exp["mean_m"], abs=1e-5)


def test_confidence_histogram_matches_expected():
    exp = _expected()["confidence_histogram"]
    decoded = decode_rgbd_frame(decode_envelope(_raw()))
    for value, count in exp.items():
        assert int((decoded.confidence == int(value)).sum()) == count


def test_rgb_decodes_to_declared_raster():
    exp = _expected()
    decoded = decode_rgbd_frame(decode_envelope(_raw()))
    assert decoded.rgb.shape == (exp["rgb"]["height"], exp["rgb"]["width"], 3)


# --- malformed -------------------------------------------------------------

def _malformed_cases() -> list[tuple[str, str]]:
    codes = json.loads((FIXTURES / "malformed_expected_codes.json").read_text())
    return sorted(codes.items())


@pytest.mark.parametrize(("name", "expected_code"), _malformed_cases())
def test_malformed_rejected_with_expected_code(name, expected_code):
    path = FIXTURES / "malformed" / f"{name}.hmc"
    assert path.exists(), f"missing malformed fixture: {name}.hmc"

    with pytest.raises(EnvelopeError) as exc:
        decode_rgbd_frame(decode_envelope(path.read_bytes()))

    assert exc.value.code == expected_code, (
        f"{name}: decoder raised {exc.value.code!r} but the fixture declares "
        f"{expected_code!r} -- implementations disagree"
    )


def test_every_malformed_fixture_has_a_declared_code():
    codes = json.loads((FIXTURES / "malformed_expected_codes.json").read_text())
    on_disk = {p.stem for p in (FIXTURES / "malformed").glob("*.hmc")}
    assert on_disk == set(codes)


def test_malformed_failures_are_all_coded():
    """No malformed fixture may surface as an uncoded/internal error."""
    codes = json.loads((FIXTURES / "malformed_expected_codes.json").read_text())
    for name in codes:
        raw = (FIXTURES / "malformed" / f"{name}.hmc").read_bytes()
        try:
            decode_rgbd_frame(decode_envelope(raw))
        except EnvelopeError as exc:
            assert exc.code, f"{name} raised an EnvelopeError with an empty code"
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"{name} raised uncoded {type(exc).__name__}: {exc}")
        else:
            pytest.fail(f"{name} was accepted but should have been rejected")
