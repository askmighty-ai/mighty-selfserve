#!/usr/bin/env python3
"""Fail when extension/ changes without a strict manifest version bump.

Usage:
  python scripts/check_extension_version_bump.py
  MIGHTY_EXTENSION_VERSION_BASE=origin/main python scripts/check_extension_version_bump.py

Exit codes:
  0 — ok (no extension changes, or version strictly increased)
  1 — rule violated or git/manifest error
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mighty.extension_version import (  # noqa: E402
    check_repo_extension_version_bump,
    resolve_extension_version_base_ref,
)


def main() -> int:
    try:
        base = resolve_extension_version_base_ref()
        err = check_repo_extension_version_bump(base_ref=base)
    except Exception as exc:  # noqa: BLE001 — CLI surface
        print(f"extension version bump check error: {exc}", file=sys.stderr)
        return 1
    if err:
        print(err, file=sys.stderr)
        return 1
    print(f"extension version bump check ok (base={base})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
