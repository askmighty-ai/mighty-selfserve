"""Build a sideload-ready Mighty in Chrome zip targeted at a given app URL."""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

PROD_URL = "https://mighty-selfserve-production.up.railway.app"
EXTENSION_DIRNAME = "mighty-in-chrome"


def extension_source_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "extension"


def rewrite_extension_bytes(raw: bytes, target_url: str) -> bytes:
    """Rewrite known production hosts to the target environment URL."""
    target = target_url.rstrip("/")
    text = raw.decode("utf-8")
    text = text.replace(PROD_URL, target)
    # Staging leftovers if present in local edits
    text = text.replace(
        "https://mighty-selfserve-staging.up.railway.app", target
    )
    return text.encode("utf-8")


def build_extension_zip(target_url: str) -> bytes:
    """Return a zip containing a single folder with URL-rewritten extension files."""
    src = extension_source_dir()
    if not src.is_dir():
        raise FileNotFoundError(f"Extension source missing: {src}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src.rglob("*")):
            if not path.is_file():
                continue
            if path.name.startswith(".") or path.suffix in {".map", ".DS_Store"}:
                continue
            rel = path.relative_to(src).as_posix()
            data = path.read_bytes()
            if path.suffix.lower() in {".js", ".html", ".json", ".css"}:
                data = rewrite_extension_bytes(data, target_url)
            zf.writestr(f"{EXTENSION_DIRNAME}/{rel}", data)
    return buf.getvalue()
