"""Pinned MediaPipe model assets.

``backend/models/manifest.json`` names each ``.task`` file with its source URL
and SHA-256. A model filename without its hash is not a reproducible
dependency, so :func:`resolve_models` refuses to hand back a path whose hash is
missing or mismatched. Nothing here downloads anything: fetching happens once,
offline from a demo, via ``scripts/pin_models.py``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

MANIFEST_SCHEMA = "hmc.model_manifest"
MANIFEST_SCHEMA_VERSION = 1
REQUIRED_MODELS = ("pose", "hands")


class ModelManifestError(RuntimeError):
    """Manifest missing, malformed, or a model file failing its pin."""


@dataclass(frozen=True, slots=True)
class ModelEntry:
    key: str
    filename: str
    url: str | None
    sha256: str | None
    license: str | None


@dataclass(frozen=True, slots=True)
class ResolvedModels:
    pose_path: Path
    hand_path: Path
    pose_sha256: str
    hand_sha256: str


def sha256_of(path: Path, *, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def load_manifest(path: str | Path) -> dict[str, ModelEntry]:
    p = Path(path)
    if not p.exists():
        raise ModelManifestError(f"model manifest not found: {p}")
    try:
        raw = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise ModelManifestError(f"model manifest is not valid JSON: {p}") from exc
    if raw.get("schema") != MANIFEST_SCHEMA or raw.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ModelManifestError("unexpected model manifest schema/version")
    models = raw.get("models")
    if not isinstance(models, dict):
        raise ModelManifestError("manifest 'models' must be an object")
    out: dict[str, ModelEntry] = {}
    for key, entry in models.items():
        if not isinstance(entry, dict) or not entry.get("filename"):
            raise ModelManifestError(f"model {key!r} must declare a filename")
        out[key] = ModelEntry(
            key=key,
            filename=str(entry["filename"]),
            url=entry.get("url"),
            sha256=(str(entry["sha256"]).lower() if entry.get("sha256") else None),
            license=entry.get("license"),
        )
    missing = [k for k in REQUIRED_MODELS if k not in out]
    if missing:
        raise ModelManifestError(f"manifest missing required models: {missing}")
    return out


def verify_model(entry: ModelEntry, models_dir: Path) -> tuple[Path, str]:
    """Return ``(path, sha256)`` for a model that exists and matches its pin."""
    path = models_dir / entry.filename
    if entry.sha256 is None:
        raise ModelManifestError(
            f"model {entry.key!r} has no sha256 pin in the manifest; run scripts/pin_models.py"
        )
    if not path.exists():
        raise ModelManifestError(f"model file missing: {path} (fetch it before startup; no runtime downloads)")
    digest = sha256_of(path)
    if digest != entry.sha256:
        raise ModelManifestError(f"model {entry.key!r} sha256 mismatch: manifest {entry.sha256[:12]}..., file {digest[:12]}...")
    return path, digest


def resolve_models(
    manifest_path: str | Path,
    *,
    models_dir: str | Path | None = None,
    pose_override: str | None = None,
    hand_override: str | None = None,
) -> ResolvedModels:
    """Verify both required models against the manifest and return their paths.

    Explicit ``*_override`` paths (from settings) bypass the directory lookup but
    are still hashed and must match the manifest pin.
    """
    manifest_path = Path(manifest_path)
    entries = load_manifest(manifest_path)
    base = Path(models_dir) if models_dir is not None else manifest_path.parent

    def _resolve(key: str, override: str | None) -> tuple[Path, str]:
        entry = entries[key]
        if override:
            entry = ModelEntry(entry.key, Path(override).name, entry.url, entry.sha256, entry.license)
            return verify_model(entry, Path(override).parent)
        return verify_model(entry, base)

    pose_path, pose_sha = _resolve("pose", pose_override)
    hand_path, hand_sha = _resolve("hands", hand_override)
    return ResolvedModels(pose_path, hand_path, pose_sha, hand_sha)

