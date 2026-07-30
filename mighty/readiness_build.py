"""Founder-facing readiness build identity (content fingerprint).

Evidence standard for gate 1: the Founder-facing host's public health payload
must report ``readiness_content_sha`` equal to the validated working tree's
fingerprint of Amex-readiness-critical files.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# Files whose behavior gates the Founder Decision Card Amex path.
READINESS_CRITICAL_FILES = (
    "mighty/natural_session.py",
    "mighty/home_state.py",
    "mighty/journey_narrative.py",
    "mighty/home_projection.py",
    "extension/background.js",
    "extension/manifest.json",
    "app.py",  # Amex open URL + enable-monitoring heartbeat ready
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def compute_readiness_content_sha(*, root: Path | None = None) -> str:
    """Stable fingerprint of readiness-critical source files."""
    base = root or ROOT
    h = hashlib.sha256()
    for rel in READINESS_CRITICAL_FILES:
        path = base / rel
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        if path.is_file():
            h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def compute_extension_content_sha(*, root: Path | None = None) -> str:
    """Fingerprint of the unpacked extension's service worker + manifest."""
    base = root or ROOT
    h = hashlib.sha256()
    for rel in ("extension/background.js", "extension/manifest.json"):
        path = base / rel
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def git_head(*, root: Path | None = None) -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root or ROOT),
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return out.strip() or None
    except Exception:
        return None


def deployment_sha_from_env() -> str:
    for key in ("RAILWAY_GIT_COMMIT_SHA", "SOURCE_VERSION", "COMMIT_SHA"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value[:40]
    return "unknown"


def local_readiness_identity(*, root: Path | None = None) -> dict[str, Any]:
    base = root or ROOT
    return {
        "readiness_content_sha": compute_readiness_content_sha(root=base),
        "extension_content_sha": compute_extension_content_sha(root=base),
        "git_head": git_head(root=base),
        "deployment_sha_env": deployment_sha_from_env(),
        "critical_files": list(READINESS_CRITICAL_FILES),
    }


def write_extension_build_identity(*, root: Path | None = None) -> Path:
    """Write extension/build_identity.json for the service worker to report."""
    base = root or ROOT
    out = base / "extension" / "build_identity.json"
    manifest = json.loads((base / "extension" / "manifest.json").read_text(encoding="utf-8"))
    payload = {
        "extension_content_sha": compute_extension_content_sha(root=base),
        "background_js_sha256": file_sha256(base / "extension" / "background.js"),
        "manifest_version": str(manifest.get("version") or ""),
        "readiness_content_sha": compute_readiness_content_sha(root=base),
    }
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out
