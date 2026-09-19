"""Cross-language interoperability gate: Python against the shared fixtures.

`docs/architecture.md` calls `contracts/fixtures` "the interoperability gate:
Swift, Python, and Java must decode the same golden packets, and each encoder
must produce bytes accepted by the other two decoders."

Those fixtures are authored and consumed by Java. Until this file existed,
Python never touched them, so Java was only checking its own output and a
Python/Java disagreement on the wire format would not surface until a real
frame failed to decode in the mod.

These tests decode every golden and malformed fixture with the Python decoder
and assert the values and error codes the fixtures declare.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from hmc_backend.contracts.character_decode import (
    CharacterDecodeError,
    decode_character_frame,
)

FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"
GOLDEN = ["character_frame_neutral", "character_frame_bent_left_arm", "character_frame_lifted_right_foot"]

pytestmark = pytest.mark.skipif(
    not FIXTURES.exists(), reason="contracts/fixtures not present in this checkout"
)


def _expected(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.expected.json").read_text())


def _raw(name: str) -> bytes:
    return (FIXTURES / f"{name}.hmc").read_bytes()


# --- integrity -------------------------------------------------------------

def test_sha256sums_match_files():
    """Every fixture listed in SHA256SUMS still hashes to its recorded digest."""
    listed = 0
    for line in (FIXTURES / "SHA256SUMS").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, _, filename = line.partition("  ")
        path = FIXTURES / filename.strip()
        if not path.exists():
            pytest.fail(f"SHA256SUMS lists a missing file: {filename}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == digest, f"{filename} digest changed (fixtures are frozen)"
        listed += 1
    assert listed >= 3


# --- golden frames ---------------------------------------------------------

@pytest.mark.parametrize("name", GOLDEN)
def test_python_decodes_java_golden_frame(name):
    raw = _raw(name)
    exp = _expected(name)

    assert hashlib.sha256(raw).hexdigest() == exp["sha256"]
    assert len(raw) == exp["total_bytes"]

    frame = decode_character_frame(raw)

    assert frame.frame_id == exp["frame_id"]
    assert str(frame.session_id) == exp["session_id"]
    assert str(frame.calibration_id) == exp["calibration_id"]
    assert frame.mode == exp["mode"]
    assert frame.point_count == exp["point_count"]
    assert len(frame.landmarks) == exp["landmark_count"]
    assert len(frame.colliders) == exp["collider_count"]


@pytest.mark.parametrize("name", GOLDEN)
def test_sample_points_match_expected(name):
    """Spot-check the packed xyzrgba16_le records Java recorded."""
    frame = decode_character_frame(_raw(name))
    for sample in _expected(name)["sample_points"]:
        i = sample["index"]
        xyz = frame.xyz_stage_m[i]
        assert xyz[0] == pytest.approx(sample["x"], abs=1e-6)
        assert xyz[1] == pytest.approx(sample["y"], abs=1e-6)
        assert xyz[2] == pytest.approx(sample["z"], abs=1e-6)
        assert bytes(frame.rgba[i]).hex() == sample["rgba"]


@pytest.mark.parametrize("name", GOLDEN)
def test_cloud_bounds_match_expected(name):
    frame = decode_character_frame(_raw(name))
    exp = _expected(name)
    np.testing.assert_allclose(frame.xyz_stage_m.min(axis=0), exp["cloud_min_stage_m"], atol=1e-6)
    np.testing.assert_allclose(frame.xyz_stage_m.max(axis=0), exp["cloud_max_stage_m"], atol=1e-6)


@pytest.mark.parametrize("name", GOLDEN)
def test_colliders_match_expected(name):
    frame = decode_character_frame(_raw(name))
    decoded = {c.id: c for c in frame.colliders}
    for exp_c in _expected(name)["colliders"]:
        got = decoded[exp_c["id"]]
        assert got.body_part == exp_c["body_part"]
        assert got.type == exp_c["type"]
        assert got.valid == exp_c["valid"]


def test_all_three_collider_types_are_exercised():
    """The fixture set must cover sphere, capsule, and OBB."""
    seen = set()
    for name in GOLDEN:
        seen |= {c.type for c in decode_character_frame(_raw(name)).colliders if c.valid}
    assert {"sphere", "capsule", "obb"} <= seen


# --- malformed fixtures ----------------------------------------------------

def _malformed_cases() -> list[tuple[str, str]]:
    codes = json.loads((FIXTURES / "malformed_expected_codes.json").read_text())
    return sorted(codes.items())


@pytest.mark.parametrize(("name", "expected_code"), _malformed_cases())
def test_malformed_fixture_rejected_with_expected_code(name, expected_code):
    path = FIXTURES / "malformed" / f"{name}.hmc"
    if not path.exists():
        pytest.fail(f"missing malformed fixture: {name}.hmc")

    with pytest.raises(CharacterDecodeError) as exc:
        decode_character_frame(path.read_bytes())

    assert exc.value.code == expected_code, (
        f"{name}: Python raised {exc.value.code!r} but the shared fixture "
        f"declares {expected_code!r} -- Python and Java disagree"
    )


def test_every_malformed_fixture_has_a_declared_code():
    """No malformed fixture may sit on disk without an expected code."""
    codes = json.loads((FIXTURES / "malformed_expected_codes.json").read_text())
    on_disk = {p.stem for p in (FIXTURES / "malformed").glob("*.hmc")}
    assert on_disk == set(codes), (
        f"undeclared: {sorted(on_disk - set(codes))}, missing files: {sorted(set(codes) - on_disk)}"
    )


# --- encoder/decoder agreement ---------------------------------------------

def test_python_encoder_output_is_accepted_by_python_decoder():
    """Round-trip Python's own encoder through the strict decoder.

    This is the half of the gate Python can check alone: bytes it produces must
    satisfy the same validation Java applies.
    """
    from hmc_backend.contracts.character_codec import encode_character_frame

    original = decode_character_frame(_raw(GOLDEN[0]))

    # Rebuild an internal CharacterFrame from the decoded fixture and re-encode.
    from hmc_backend.contracts.internal import (
        CharacterFrame,
        ColoredPointCloud,
        FrameQuality,
        SourceFrameRef,
        TraceContext,
    )

    refs = tuple(
        SourceFrameRef(
            device_id=r["device_id"],
            session_id=__import__("uuid").UUID(r["session_id"]),
            capture_id=__import__("uuid").UUID(r["capture_id"]),
            sequence=r["sequence"],
        )
        for r in original.source_frames[:2]
    )
    rebuilt = CharacterFrame(
        session_id=original.session_id,
        calibration_id=original.calibration_id,
        frame_id=original.frame_id,
        source_frames=refs,  # type: ignore[arg-type]
        normalized_capture_time_s=original.normalized_capture_time_s,
        pair_skew_ms=original.pair_skew_ms,
        mode=original.mode,  # type: ignore[arg-type]
        quality=FrameQuality(
            valid=True,
            point_count=original.point_count,
            valid_landmark_count=sum(1 for lm in original.landmarks if lm.valid),
            valid_collider_count=sum(1 for c in original.colliders if c.valid),
            warnings=(),
        ),
        cloud=ColoredPointCloud(
            xyz_stage_m=original.xyz_stage_m,
            rgba=original.rgba,
            source_mask=np.ones(original.point_count, np.uint8),
        ),
        landmarks=original.landmarks,
        colliders=original.colliders,
        trace=TraceContext(),
    )

    reencoded = encode_character_frame(rebuilt)
    roundtripped = decode_character_frame(reencoded)

    assert roundtripped.frame_id == original.frame_id
    assert roundtripped.point_count == original.point_count
    assert len(roundtripped.colliders) == len(original.colliders)
    np.testing.assert_allclose(roundtripped.xyz_stage_m, original.xyz_stage_m)
    np.testing.assert_array_equal(roundtripped.rgba, original.rgba)
