#!/usr/bin/env python3
"""Dump Amex verification timeline vs presentation selector (investigation).

Usage:
  DATABASE_PATH=/path/to/mighty.db USER_ID=<uid> \\
    .venv/bin/python scripts/dump_amex_verification_timeline.py

Prints a sanitized JSON report to stdout. Does not print emails, credentials,
cookies, tokens, balances, account numbers, or extracted values.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

# Allow running from repo root without install.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.verification_timeline_diagnostics import (
    build_amex_verification_timeline_report,
)


def main() -> int:
    db_path = os.environ.get("DATABASE_PATH", "mighty.db")
    user_id = os.environ.get("USER_ID") or os.environ.get("MIGHTY_USER_ID")
    if not user_id:
        print("Set USER_ID to the affected account", file=sys.stderr)
        return 2
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    report = build_amex_verification_timeline_report(conn, user_id, provider="amex")
    # Never echo full filesystem path — basename only in datastore metadata.
    print(json.dumps(report, indent=2, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
