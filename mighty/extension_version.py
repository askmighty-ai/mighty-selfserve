"""Extension version reporting and comparison helpers.

The running version is whatever the extension last reported from
``chrome.runtime.getManifest().version``. The server never invents it.
Expected version is read from the repository ``extension/manifest.json``.

When multiple extension instances/devices report for the same user, this
module keeps the most recently seen report (by ``extension_last_seen_at``).
That is intentional for the initial PR and may under-represent older
parallel instances.

Whenever any file under ``extension/`` changes, ``manifest.json`` version
must strictly increase vs the merge base. Otherwise the reported version
cannot distinguish a stale loaded build from the current repo build.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from mighty.admin_local_time import parse_admin_timestamp, to_utc_iso_z

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "extension" / "manifest.json"
EXTENSION_PREFIX = "extension/"

_VERSION_RE = re.compile(r"^\d+(?:\.\d+){0,3}$")


def read_expected_extension_version(manifest_path: Path | None = None) -> str:
    """Return the Chrome extension version shipped in this repository."""
    path = manifest_path or MANIFEST_PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    version = str(data.get("version") or "").strip()
    if not version:
        raise ValueError(f"manifest missing version: {path}")
    return version


def parse_chrome_version(value: str | None) -> tuple[int, ...] | None:
    """Parse a Chrome dotted-numeric version into comparable ints."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or not _VERSION_RE.match(text):
        return None
    parts = [int(p) for p in text.split(".")]
    # Normalize to 4 components for stable compare (Chrome max).
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def compare_chrome_versions(left: str | None, right: str | None) -> int | None:
    """Compare two Chrome versions.

    Returns -1 / 0 / 1 when both parse, else None.
    """
    a = parse_chrome_version(left)
    b = parse_chrome_version(right)
    if a is None or b is None:
        return None
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def extension_update_required(
    reported: str | None,
    expected: str | None,
) -> bool:
    """True only when a reported version is strictly older than expected."""
    cmp = compare_chrome_versions(reported, expected)
    return cmp is not None and cmp < 0


def _utc_now_iso() -> str:
    return to_utc_iso_z(datetime.now(timezone.utc))


def normalize_seen_at(value: Any | None = None) -> str:
    """Normalize a last-seen timestamp to UTC ISO-8601 Z."""
    if value is None or value == "":
        return _utc_now_iso()
    dt = parse_admin_timestamp(value)
    if dt is None:
        return _utc_now_iso()
    return to_utc_iso_z(dt)


def should_accept_extension_report(
    *,
    existing_version: str | None,
    existing_last_seen_at: str | None,
    reported_version: str | None,
    reported_last_seen_at: str | None,
) -> bool:
    """Accept a heartbeat only when it is not older than stored last-seen.

    Out-of-order older heartbeats must not overwrite newer version/last-seen.
    Equal last-seen timestamps still accept (idempotent refresh).
    """
    if not reported_version or not str(reported_version).strip():
        return False
    if existing_last_seen_at is None or existing_last_seen_at == "":
        return True
    existing_dt = parse_admin_timestamp(existing_last_seen_at)
    reported_dt = parse_admin_timestamp(reported_last_seen_at)
    if existing_dt is None:
        return True
    if reported_dt is None:
        return False
    return reported_dt >= existing_dt


def record_extension_version(
    db: Any,
    user_id: str,
    version: str | None,
    *,
    seen_at: Any | None = None,
) -> bool:
    """Persist reported extension version for a user if the report is fresh.

    Returns True when the stored row was updated.
    """
    if not version or not str(version).strip():
        return False
    version = str(version).strip()[:40]
    seen_iso = normalize_seen_at(seen_at)

    row = db.execute(
        "SELECT extension_version, extension_last_seen_at FROM users WHERE id=?",
        (user_id,),
    ).fetchone()
    if row is None:
        return False

    existing_version = row["extension_version"] if row["extension_version"] else None
    existing_seen = (
        row["extension_last_seen_at"] if row["extension_last_seen_at"] else None
    )
    if not should_accept_extension_report(
        existing_version=existing_version,
        existing_last_seen_at=existing_seen,
        reported_version=version,
        reported_last_seen_at=seen_iso,
    ):
        return False

    db.execute(
        "UPDATE users SET extension_version=?, extension_last_seen_at=? WHERE id=?",
        (version, seen_iso, user_id),
    )
    db.commit()
    return True


def get_extension_version_status(
    db: Any,
    user_id: str,
    *,
    expected_version: str | None = None,
) -> dict[str, Any]:
    """Safe customer/debug payload for extension version diagnostics."""
    expected = expected_version or read_expected_extension_version()
    row = db.execute(
        "SELECT extension_version, extension_last_seen_at FROM users WHERE id=?",
        (user_id,),
    ).fetchone()
    reported = None
    last_seen = None
    if row is not None:
        reported = row["extension_version"] or None
        last_seen = row["extension_last_seen_at"] or None
        if last_seen:
            dt = parse_admin_timestamp(last_seen)
            last_seen = to_utc_iso_z(dt) if dt else last_seen

    update_required = extension_update_required(reported, expected)
    return {
        "extension_version": reported,
        "extension_expected_version": expected,
        "extension_last_seen_at": last_seen,
        "extension_update_required": update_required,
        # Documented semantics: most recently seen instance for this user.
        "extension_instance_semantics": "most_recently_seen",
    }


def ensure_extension_version_columns(db: Any) -> None:
    """Add users.extension_version / extension_last_seen_at if missing."""
    for ddl in (
        "ALTER TABLE users ADD COLUMN extension_version TEXT",
        "ALTER TABLE users ADD COLUMN extension_last_seen_at TEXT",
    ):
        try:
            db.execute(ddl)
            db.commit()
        except Exception as exc:  # noqa: BLE001 — sqlite duplicate column
            msg = str(exc).lower()
            if "duplicate column" not in msg and "already exists" not in msg:
                raise


def is_extension_path(path: str | Path) -> bool:
    """True when *path* is under the shipped Chrome extension tree."""
    text = str(path).replace("\\", "/").lstrip("./")
    return text == "extension" or text.startswith(EXTENSION_PREFIX)


def extension_paths_changed(changed_paths: Iterable[str | Path]) -> list[str]:
    """Return changed paths that live under ``extension/`` (sorted unique)."""
    found: set[str] = set()
    for path in changed_paths:
        text = str(path).replace("\\", "/").lstrip("./")
        if is_extension_path(text):
            found.add(text)
    return sorted(found)


def check_extension_version_bump(
    *,
    changed_paths: Iterable[str | Path],
    base_version: str | None,
    head_version: str | None,
) -> str | None:
    """Require a strict manifest version increase when extension files change.

    Returns an error message when the rule is violated, else None.
    """
    touched = extension_paths_changed(changed_paths)
    if not touched:
        return None

    if not head_version or not str(head_version).strip():
        return (
            "extension/ files changed but extension/manifest.json has no version. "
            f"Changed: {', '.join(touched)}"
        )
    if not base_version or not str(base_version).strip():
        return (
            "extension/ files changed but the base branch manifest has no version. "
            "Cannot verify a bump."
        )

    cmp = compare_chrome_versions(head_version, base_version)
    if cmp is None:
        return (
            f"Cannot compare extension versions "
            f"(base={base_version!r}, head={head_version!r}). "
            "Use Chrome dotted-numeric versions (e.g. 1.3.16)."
        )
    if cmp <= 0:
        return (
            "extension/ files changed but extension/manifest.json version was not "
            f"increased (base={base_version}, head={head_version}). "
            "Bump the manifest version whenever extension code ships so the "
            "version diagnostic can tell stale builds from current ones. "
            f"Changed: {', '.join(touched)}"
        )
    return None


def _git_output(args: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd or ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr}")
    return result.stdout


def resolve_extension_version_base_ref(
    *,
    cwd: Path | None = None,
    preferred: str | None = None,
) -> str:
    """Pick a git ref to diff extension changes against."""
    if preferred:
        return preferred
    import os

    env_ref = (os.environ.get("MIGHTY_EXTENSION_VERSION_BASE") or "").strip()
    if env_ref:
        return env_ref
    # Prefer origin/main when available; fall back to main / master.
    for candidate in ("origin/main", "main", "origin/master", "master"):
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", candidate],
            cwd=str(cwd or ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0:
            return candidate
    raise RuntimeError(
        "No base ref found for extension version bump check "
        "(tried origin/main, main, origin/master, master)."
    )


def changed_paths_since_base(
    base_ref: str,
    *,
    cwd: Path | None = None,
    include_working_tree: bool = True,
) -> list[str]:
    """List paths changed between merge-base(base_ref, HEAD) and HEAD (+ WT)."""
    root = cwd or ROOT
    merge_base = _git_output(["merge-base", "HEAD", base_ref], cwd=root).strip()
    names: set[str] = set()
    committed = _git_output(
        ["diff", "--name-only", f"{merge_base}...HEAD"],
        cwd=root,
    )
    names.update(line.strip() for line in committed.splitlines() if line.strip())
    if include_working_tree:
        dirty = _git_output(["diff", "--name-only", merge_base], cwd=root)
        names.update(line.strip() for line in dirty.splitlines() if line.strip())
        untracked = _git_output(
            ["ls-files", "--others", "--exclude-standard", "--", "extension"],
            cwd=root,
        )
        names.update(line.strip() for line in untracked.splitlines() if line.strip())
    return sorted(names)


def read_manifest_version_at_ref(
    ref: str,
    *,
    cwd: Path | None = None,
    manifest_git_path: str = "extension/manifest.json",
) -> str:
    """Read ``extension/manifest.json`` version from a git ref."""
    raw = _git_output(["show", f"{ref}:{manifest_git_path}"], cwd=cwd or ROOT)
    data = json.loads(raw)
    version = str(data.get("version") or "").strip()
    if not version:
        raise ValueError(f"manifest at {ref}:{manifest_git_path} missing version")
    return version


def check_repo_extension_version_bump(
    *,
    cwd: Path | None = None,
    base_ref: str | None = None,
    include_working_tree: bool = True,
) -> str | None:
    """Run the extension version-bump rule against the current git checkout."""
    root = cwd or ROOT
    resolved_base = resolve_extension_version_base_ref(cwd=root, preferred=base_ref)
    merge_base = _git_output(["merge-base", "HEAD", resolved_base], cwd=root).strip()
    changed = changed_paths_since_base(
        resolved_base,
        cwd=root,
        include_working_tree=include_working_tree,
    )
    base_version = read_manifest_version_at_ref(merge_base, cwd=root)
    head_version = read_expected_extension_version(root / "extension" / "manifest.json")
    return check_extension_version_bump(
        changed_paths=changed,
        base_version=base_version,
        head_version=head_version,
    )
