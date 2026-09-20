"""Regenerate contracts/fixtures/collider_geometry_golden.json from the Python reference.

    uv run python scripts/write_collider_golden.py
"""

from __future__ import annotations

import json
from pathlib import Path

from hmc_backend.colliders.golden import golden_document

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT / "contracts" / "fixtures" / "collider_geometry_golden.json"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(golden_document(), indent=2) + "\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

