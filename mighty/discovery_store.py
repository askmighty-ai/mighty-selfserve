"""Discovery Store — durable discovery facts (Milestone 7).

One owner for discovered provider relationships. Does not enroll accounts,
write session evidence, or rank Attention.

See docs/ACCOUNT_DISCOVERY.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from mighty.discovery_policy import (
    DISPOSITION_DISMISSED,
    DISPOSITION_ENROLLED,
    DISPOSITION_IGNORED,
    DiscoveryDecision,
)


@dataclass(frozen=True)
class DiscoveryFact:
    user_id: str
    provider: str
    source_type: str
    source_ref: str | None
    matched_domain: str | None
    match_method: str | None
    confidence: float
    email_count: int
    disposition: str
    first_seen_at: str
    last_seen_at: str
    evidence_summary: str | None
    enrolled_at: str | None
    display_name: str | None
    category: str | None


def ensure_discovery_tables(db: Any, *, commit: bool = True) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS account_discovery (
            user_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_ref TEXT,
            matched_domain TEXT,
            match_method TEXT,
            confidence REAL NOT NULL DEFAULT 0,
            email_count INTEGER NOT NULL DEFAULT 0,
            disposition TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            evidence_summary TEXT,
            enrolled_at TEXT,
            display_name TEXT,
            category TEXT,
            PRIMARY KEY (user_id, provider)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_account_discovery_user "
        "ON account_discovery(user_id, disposition)"
    )
    if commit:
        db.commit()


def get_discovery_fact(
    db: Any, user_id: str, provider: str
) -> DiscoveryFact | None:
    ensure_discovery_tables(db, commit=False)
    row = db.execute(
        """
        SELECT user_id, provider, source_type, source_ref, matched_domain,
               match_method, confidence, email_count, disposition,
               first_seen_at, last_seen_at, evidence_summary, enrolled_at,
               display_name, category
        FROM account_discovery
        WHERE user_id = ? AND provider = ?
        """,
        (str(user_id).strip(), str(provider).strip().lower()),
    ).fetchone()
    return _fact_from_row(row) if row else None


def list_discovery_facts(db: Any, user_id: str) -> list[DiscoveryFact]:
    ensure_discovery_tables(db, commit=False)
    rows = db.execute(
        """
        SELECT user_id, provider, source_type, source_ref, matched_domain,
               match_method, confidence, email_count, disposition,
               first_seen_at, last_seen_at, evidence_summary, enrolled_at,
               display_name, category
        FROM account_discovery
        WHERE user_id = ?
        ORDER BY confidence DESC, last_seen_at DESC, provider ASC
        """,
        (str(user_id).strip(),),
    ).fetchall()
    return [_fact_from_row(r) for r in rows if r]


def is_dismissed(db: Any, user_id: str, provider: str) -> bool:
    fact = get_discovery_fact(db, user_id, provider)
    if fact and fact.disposition == DISPOSITION_DISMISSED:
        return True
    # Compatibility with legacy email_suggestions.dismissed
    try:
        row = db.execute(
            "SELECT dismissed FROM email_suggestions WHERE user_id=? AND site_key=?",
            (str(user_id).strip(), str(provider).strip().lower()),
        ).fetchone()
        if row is not None:
            return bool(row["dismissed"] if hasattr(row, "keys") else row[0])
    except Exception:
        pass
    return False


def mark_dismissed(db: Any, user_id: str, provider: str, *, now: datetime) -> None:
    ensure_discovery_tables(db, commit=False)
    stamp = _iso(now)
    uid = str(user_id).strip()
    prov = str(provider).strip().lower()
    existing = get_discovery_fact(db, uid, prov)
    if existing is None:
        db.execute(
            """
            INSERT INTO account_discovery (
                user_id, provider, source_type, source_ref, matched_domain,
                match_method, confidence, email_count, disposition,
                first_seen_at, last_seen_at, evidence_summary, enrolled_at,
                display_name, category
            ) VALUES (?, ?, 'manual', NULL, NULL, NULL, 0, 0, ?, ?, ?, NULL, NULL, NULL, NULL)
            """,
            (uid, prov, DISPOSITION_DISMISSED, stamp, stamp),
        )
    else:
        db.execute(
            """
            UPDATE account_discovery
            SET disposition = ?, last_seen_at = ?
            WHERE user_id = ? AND provider = ?
            """,
            (DISPOSITION_DISMISSED, stamp, uid, prov),
        )
    try:
        db.execute(
            "UPDATE email_suggestions SET dismissed=1 WHERE user_id=? AND site_key=?",
            (uid, prov),
        )
    except Exception:
        pass
    db.commit()


def mark_enrolled(
    db: Any, user_id: str, provider: str, *, now: datetime
) -> None:
    ensure_discovery_tables(db, commit=False)
    stamp = _iso(now)
    uid = str(user_id).strip()
    prov = str(provider).strip().lower()
    existing = get_discovery_fact(db, uid, prov)
    if existing is None:
        db.execute(
            """
            INSERT INTO account_discovery (
                user_id, provider, source_type, source_ref, matched_domain,
                match_method, confidence, email_count, disposition,
                first_seen_at, last_seen_at, evidence_summary, enrolled_at,
                display_name, category
            ) VALUES (?, ?, 'enrollment', NULL, NULL, NULL, 1.0, 0, ?, ?, ?, NULL, ?, NULL, NULL)
            """,
            (uid, prov, DISPOSITION_ENROLLED, stamp, stamp, stamp),
        )
    else:
        db.execute(
            """
            UPDATE account_discovery
            SET disposition = ?, enrolled_at = COALESCE(enrolled_at, ?), last_seen_at = ?
            WHERE user_id = ? AND provider = ?
            """,
            (DISPOSITION_ENROLLED, stamp, stamp, uid, prov),
        )
    try:
        db.execute(
            "UPDATE email_suggestions SET added=1, dismissed=0 "
            "WHERE user_id=? AND site_key=?",
            (uid, prov),
        )
    except Exception:
        pass
    db.commit()


def reconcile_discovery_hits(
    db: Any,
    user_id: str,
    decisions: list[DiscoveryDecision],
    *,
    source_type: str,
    source_ref: str | None,
    now: datetime,
) -> dict[str, int]:
    """Upsert scan decisions without clearing dismiss/enroll intent.

    Returns counts: seen, inserted, updated, preserved_dismissed, ignored.
    """
    ensure_discovery_tables(db, commit=False)
    now = _ensure_aware(now)
    stamp = _iso(now)
    uid = str(user_id).strip()
    seen_providers = {d.provider for d in decisions}
    counts = {
        "seen": len(decisions),
        "inserted": 0,
        "updated": 0,
        "preserved_dismissed": 0,
        "ignored": 0,
    }

    for decision in decisions:
        existing = get_discovery_fact(db, uid, decision.provider)
        disposition = decision.disposition
        enrolled_at = None
        if existing and existing.disposition == DISPOSITION_DISMISSED:
            disposition = DISPOSITION_DISMISSED
            counts["preserved_dismissed"] += 1
        elif existing and existing.disposition == DISPOSITION_ENROLLED:
            disposition = DISPOSITION_ENROLLED
            enrolled_at = existing.enrolled_at
        elif existing and existing.enrolled_at:
            disposition = DISPOSITION_ENROLLED
            enrolled_at = existing.enrolled_at

        if existing is None:
            db.execute(
                """
                INSERT INTO account_discovery (
                    user_id, provider, source_type, source_ref, matched_domain,
                    match_method, confidence, email_count, disposition,
                    first_seen_at, last_seen_at, evidence_summary, enrolled_at,
                    display_name, category
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uid,
                    decision.provider,
                    source_type,
                    source_ref,
                    decision.matched_domain,
                    decision.match_method,
                    decision.confidence,
                    decision.email_count,
                    disposition,
                    stamp,
                    stamp,
                    decision.evidence_summary,
                    enrolled_at,
                    decision.display_name,
                    decision.category,
                ),
            )
            counts["inserted"] += 1
        else:
            db.execute(
                """
                UPDATE account_discovery SET
                    source_type = ?,
                    source_ref = ?,
                    matched_domain = ?,
                    match_method = ?,
                    confidence = ?,
                    email_count = ?,
                    disposition = ?,
                    last_seen_at = ?,
                    evidence_summary = ?,
                    enrolled_at = COALESCE(?, enrolled_at),
                    display_name = ?,
                    category = ?
                WHERE user_id = ? AND provider = ?
                """,
                (
                    source_type,
                    source_ref,
                    decision.matched_domain,
                    decision.match_method,
                    decision.confidence,
                    decision.email_count,
                    disposition,
                    stamp,
                    decision.evidence_summary,
                    enrolled_at,
                    decision.display_name,
                    decision.category,
                    uid,
                    decision.provider,
                ),
            )
            counts["updated"] += 1

        _sync_email_suggestion(db, uid, decision, disposition=disposition, now=stamp)

    # Mark previously seen facts absent from this scan as ignored (not deleted).
    for fact in list_discovery_facts(db, uid):
        if fact.provider in seen_providers:
            continue
        if fact.disposition in {
            DISPOSITION_DISMISSED,
            DISPOSITION_ENROLLED,
            DISPOSITION_IGNORED,
        }:
            continue
        if fact.disposition == "already_enrolled":
            continue
        db.execute(
            """
            UPDATE account_discovery
            SET disposition = ?
            WHERE user_id = ? AND provider = ?
            """,
            (DISPOSITION_IGNORED, uid, fact.provider),
        )
        counts["ignored"] += 1

    db.commit()
    return counts


def _sync_email_suggestion(
    db: Any,
    user_id: str,
    decision: DiscoveryDecision,
    *,
    disposition: str,
    now: str,
) -> None:
    """Keep legacy email_suggestions projection for existing UI."""
    dismissed = 1 if disposition == DISPOSITION_DISMISSED else 0
    added = 1 if disposition in {DISPOSITION_ENROLLED, "already_enrolled"} else 0
    try:
        db.execute(
            """
            INSERT INTO email_suggestions(
                user_id, site_key, display_name, category, email_count,
                sender_domain, dismissed, added, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, site_key) DO UPDATE SET
                email_count = excluded.email_count,
                sender_domain = excluded.sender_domain,
                display_name = excluded.display_name,
                category = excluded.category,
                dismissed = CASE
                    WHEN email_suggestions.dismissed = 1 THEN 1
                    ELSE excluded.dismissed
                END,
                added = CASE
                    WHEN email_suggestions.added = 1 THEN 1
                    WHEN excluded.added = 1 THEN 1
                    ELSE email_suggestions.added
                END
            """,
            (
                user_id,
                decision.provider,
                decision.display_name,
                decision.category,
                decision.email_count,
                decision.matched_domain,
                dismissed,
                added,
                now,
            ),
        )
    except Exception:
        # Table may be absent in minimal fixtures.
        pass


def _fact_from_row(row: Any) -> DiscoveryFact:
    mapping = dict(row) if not isinstance(row, dict) else row
    try:
        mapping = {k: mapping[k] for k in mapping.keys()}
    except Exception:
        mapping = {
            "user_id": row[0],
            "provider": row[1],
            "source_type": row[2],
            "source_ref": row[3],
            "matched_domain": row[4],
            "match_method": row[5],
            "confidence": row[6],
            "email_count": row[7],
            "disposition": row[8],
            "first_seen_at": row[9],
            "last_seen_at": row[10],
            "evidence_summary": row[11],
            "enrolled_at": row[12],
            "display_name": row[13],
            "category": row[14],
        }
    return DiscoveryFact(
        user_id=str(mapping.get("user_id") or ""),
        provider=str(mapping.get("provider") or ""),
        source_type=str(mapping.get("source_type") or ""),
        source_ref=_opt(mapping.get("source_ref")),
        matched_domain=_opt(mapping.get("matched_domain")),
        match_method=_opt(mapping.get("match_method")),
        confidence=float(mapping.get("confidence") or 0),
        email_count=int(mapping.get("email_count") or 0),
        disposition=str(mapping.get("disposition") or ""),
        first_seen_at=str(mapping.get("first_seen_at") or ""),
        last_seen_at=str(mapping.get("last_seen_at") or ""),
        evidence_summary=_opt(mapping.get("evidence_summary")),
        enrolled_at=_opt(mapping.get("enrolled_at")),
        display_name=_opt(mapping.get("display_name")),
        category=_opt(mapping.get("category")),
    )


def _opt(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso(value: datetime) -> str:
    return _ensure_aware(value).replace(microsecond=0).isoformat()
