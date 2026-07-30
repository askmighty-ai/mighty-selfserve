#!/usr/bin/env python3
"""Report local / committed / remote / production deployment alignment.

Future implementation reports must distinguish these four states and must not
claim "shipped" or "working in production" until all four align.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _health(host: str) -> dict:
    req = urllib.request.Request(
        host.rstrip("/") + "/health",
        headers={"User-Agent": "MightyDeploymentGuard/1.0"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    from mighty.readiness_build import compute_readiness_content_sha

    host = (os.environ.get("FOUNDER_HOST") or "").strip()
    if not host:
        print("FAIL: set FOUNDER_HOST", file=sys.stderr)
        return 2

    dirty = bool(_git("status", "--porcelain"))
    head = _git("rev-parse", "HEAD")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    try:
        upstream = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        remote_sha = _git("rev-parse", "@{u}")
        ahead_behind = _git("rev-list", "--left-right", "--count", f"HEAD...@{{u}}")
    except subprocess.CalledProcessError:
        upstream = None
        remote_sha = None
        ahead_behind = None

    local_sha = compute_readiness_content_sha()
    remote_health = _health(host)
    prod_sha = (remote_health.get("readiness_content_sha") or "").strip()
    prod_git = (remote_health.get("deployment_sha") or remote_health.get("git_sha") or "").strip()

    states = {
        "local_working_tree": {
            "dirty": dirty,
            "readiness_content_sha": local_sha,
            "note": "Uncommitted edits change this SHA relative to HEAD checkout",
        },
        "committed_HEAD": {
            "git_sha": head,
            "branch": branch,
        },
        "pushed_remote": {
            "upstream": upstream,
            "remote_sha": remote_sha,
            "ahead_behind_left_right": ahead_behind,
        },
        "deployed_production": {
            "host": host,
            "readiness_content_sha": prod_sha,
            "deployment_sha": prod_git,
            "health_ok": bool(remote_health.get("ok")),
        },
    }

    aligned = (
        not dirty
        and upstream
        and remote_sha == head
        and ahead_behind == "0\t0"
        and prod_sha == local_sha
        and remote_health.get("ok") is True
    )
    report = {
        "aligned": aligned,
        "states": states,
        "rule": (
            "Do not report shipped/working-in-production until local working tree, "
            "committed HEAD, pushed remote, and deployed production SHA all align."
        ),
    }
    print(json.dumps(report, indent=2))
    return 0 if aligned else 1


if __name__ == "__main__":
    raise SystemExit(main())
