"""Fetch the MediaPipe .task files once and pin their SHA-256 in models/manifest.json.

    uv run python scripts/pin_models.py            # download + pin
    uv run python scripts/pin_models.py --verify   # only check existing files

This is a one-time developer step. The backend never downloads models at
startup; it only verifies the files against the pinned hashes.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from hmc_backend.vision.models import load_manifest, sha256_of

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = BACKEND_ROOT / "models" / "manifest.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="do not download; verify pins only")
    args = ap.parse_args()

    entries = load_manifest(MANIFEST)
    raw = json.loads(MANIFEST.read_text())
    ok = True
    for key, entry in entries.items():
        path = MANIFEST.parent / entry.filename
        if not path.exists():
            if args.verify or not entry.url:
                print(f"[{key}] missing {path}" + ("" if entry.url else " and no url"), file=sys.stderr)
                ok = False
                continue
            print(f"[{key}] downloading {entry.url}")
            urllib.request.urlretrieve(entry.url, path)  # fixed https URL from the manifest
        digest = sha256_of(path)
        if entry.sha256 is None:
            if args.verify:
                print(f"[{key}] not pinned (file sha256 {digest})", file=sys.stderr)
                ok = False
            else:
                raw["models"][key]["sha256"] = digest
                print(f"[{key}] pinned {digest}")
        elif digest != entry.sha256:
            print(f"[{key}] MISMATCH manifest {entry.sha256} file {digest}", file=sys.stderr)
            ok = False
        else:
            print(f"[{key}] ok {digest}")

    if not args.verify:
        MANIFEST.write_text(json.dumps(raw, indent=2) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
