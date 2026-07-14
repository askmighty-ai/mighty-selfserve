"""
mighty.customer_capability_presentation
───────────────────────────────────────
Presentation-layer gate for the Truth Dashboard.

Capability resolution (resolve_capability_state / build_capability_view) still
computes live pipeline truth. This module decides *when* that truth becomes
customer-visible:

  • While verification is in flight and a prior stable card exists → hold it
    and show only a subtle refresh indicator.
  • When verification reaches a terminal conclusion → atomic swap to the new
    card (or keep the prior card visually identical if the result is unchanged).
  • Login Unknown is exceptional during refresh — never the intermediate face
    of a normal re-check.
  • First-ever in-flight shows a neutral checking face (not a completed
    Login Unknown conclusion).

Persistence is monotonic: an older verification/access cycle must never
overwrite a newer published presentation. Ordering uses canonical completion
time (and cycle ids for idempotency), not formatted display strings.

Does not change capability precedence, verification FSM, extraction, snapshots,
provider adapters, or truth-validation scoring.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from mighty.admin_local_time import parse_admin_timestamp, to_utc_iso_z
from mighty.capability_state import (
    CapabilityState,
    CapabilityView,
    EvidenceItem,
    ExtractedField,
    PipelineStage,
)
from mighty.session_verification import (
    ACTIVE_VERIFICATION_LIFECYCLES,
    TERMINAL_VERIFICATION_LIFECYCLES,
)

REFRESH_LABEL = "Refreshing…"
REFRESH_LABEL_VERBOSE = "Verifying account access…"
FIRST_EVER_CHECKING_HEADLINE = "Verifying account access…"
FIRST_EVER_CHECKING_EVIDENCE = "Mighty is checking your current account state."

_LIVE_CHECKING = "Checking"


# ── Ordering metadata ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PresentationOrderMeta:
    """First-class ordering keys for monotonic presentation persistence.

    Ordering rule (documented):
      1. Compare ``verification_completed_at`` as aware UTC datetimes.
         Incoming without a completed_at cannot replace an existing row that has one.
      2. When completed_at is strictly newer → accept; strictly older → reject.
      3. When completed_at is equal (same instant) or both missing:
         - Same ``verification_id`` (or both empty and same ``access_cycle_id``)
           → accept (idempotent / duplicate write).
         - Otherwise → reject (conservative; prevents out-of-order UUID races).
      4. First write (no existing row) always accepts.
    """

    verification_id: str | None = None
    access_cycle_id: str | None = None
    verification_completed_at: str | None = None
    lifecycle: str | None = None
    terminal_reason: str | None = None
    account_identity: str | None = None

    def to_row_dict(self) -> dict[str, str | None]:
        return {
            "verification_id": self.verification_id,
            "access_cycle_id": self.access_cycle_id,
            "verification_completed_at": self.verification_completed_at,
            "lifecycle": self.lifecycle,
            "terminal_reason": self.terminal_reason,
            "account_identity": self.account_identity,
        }


def _norm_id(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _completed_dt(meta: PresentationOrderMeta) -> datetime | None:
    return parse_admin_timestamp(meta.verification_completed_at)


def is_newer_presentation(
    incoming: PresentationOrderMeta,
    existing: PresentationOrderMeta | None,
) -> bool:
    """Return True when ``incoming`` may replace ``existing`` under the ordering rule."""
    if existing is None:
        return True

    inc_at = _completed_dt(incoming)
    ex_at = _completed_dt(existing)

    if inc_at is not None and ex_at is not None:
        if inc_at > ex_at:
            return True
        if inc_at < ex_at:
            return False
        # Equal instants → idempotency check below.
    elif inc_at is not None and ex_at is None:
        return True
    elif inc_at is None and ex_at is not None:
        return False

    inc_vid = _norm_id(incoming.verification_id)
    ex_vid = _norm_id(existing.verification_id)
    if inc_vid and ex_vid:
        return inc_vid == ex_vid

    inc_cid = _norm_id(incoming.access_cycle_id)
    ex_cid = _norm_id(existing.access_cycle_id)
    if inc_cid and ex_cid:
        return inc_cid == ex_cid

    # Both sides lack comparable cycle identity — reject to avoid silent regression.
    return False


def order_meta_from_capability(
    view: CapabilityView,
    *,
    access_view: Any | None = None,
    verification_lifecycle: str | None = None,
    terminal_reason: str | None = None,
    verification_completed_at: str | None = None,
    account_identity: str | None = None,
) -> PresentationOrderMeta:
    """Extract ordering metadata from a presented CapabilityView + access signals."""
    ids: dict[str, Any] = {}
    if view.truth_validation is not None:
        ids = dict(view.truth_validation.developer_ids or {})

    verification_id = _norm_id(
        ids.get("verification_id")
        or (getattr(access_view, "verification_id", None) if access_view else None)
    )
    access_cycle_id = _norm_id(
        ids.get("access_cycle_id")
        or (getattr(access_view, "access_cycle_id", None) if access_view else None)
        or (
            getattr(access_view, "last_confirmed_access_cycle_id", None)
            if access_view
            else None
        )
        or verification_id
    )
    lifecycle = _norm_id(
        verification_lifecycle
        or (
            getattr(access_view, "active_verification_lifecycle", None)
            if access_view
            else None
        )
    )
    completed = verification_completed_at
    if completed is None:
        completed = view.last_verified
    if completed:
        dt = parse_admin_timestamp(completed)
        completed = to_utc_iso_z(dt) if dt is not None else str(completed).strip() or None
    return PresentationOrderMeta(
        verification_id=verification_id,
        access_cycle_id=access_cycle_id,
        verification_completed_at=completed,
        lifecycle=lifecycle,
        terminal_reason=_norm_id(terminal_reason),
        account_identity=_norm_id(account_identity),
    )


def lookup_verification_order_fields(
    db: Any,
    user_id: str,
    *,
    verification_id: str | None = None,
    provider: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Return (completed_at, lifecycle, terminal_reason) from session verification."""
    if not verification_id and not provider:
        return None, None, None
    try:
        if verification_id:
            row = db.execute(
                """
                SELECT completed_at, lifecycle, terminal_reason
                FROM provider_session_verification
                WHERE verification_id = ? AND user_id = ?
                """,
                (verification_id, user_id),
            ).fetchone()
        else:
            row = db.execute(
                """
                SELECT completed_at, lifecycle, terminal_reason
                FROM provider_session_verification
                WHERE user_id = ? AND provider = ?
                ORDER BY COALESCE(completed_at, requested_at) DESC
                LIMIT 1
                """,
                (user_id, provider),
            ).fetchone()
    except Exception:  # noqa: BLE001 — table may not exist in unit tests
        return None, None, None
    if row is None:
        return None, None, None
    if hasattr(row, "keys"):
        return row["completed_at"], row["lifecycle"], row["terminal_reason"]
    return row[0], row[1], row[2]


def enrich_order_meta_from_db(
    db: Any,
    user_id: str,
    meta: PresentationOrderMeta,
    *,
    provider: str | None = None,
) -> PresentationOrderMeta:
    """Fill completed_at / lifecycle / terminal_reason from the verification row."""
    completed, lifecycle, reason = lookup_verification_order_fields(
        db,
        user_id,
        verification_id=meta.verification_id,
        provider=provider if not meta.verification_id else None,
    )
    completed_iso = meta.verification_completed_at
    if completed:
        dt = parse_admin_timestamp(completed)
        completed_iso = to_utc_iso_z(dt) if dt is not None else str(completed).strip()
    return PresentationOrderMeta(
        verification_id=meta.verification_id,
        access_cycle_id=meta.access_cycle_id or meta.verification_id,
        verification_completed_at=completed_iso or meta.verification_completed_at,
        lifecycle=_norm_id(lifecycle) or meta.lifecycle,
        terminal_reason=_norm_id(reason) or meta.terminal_reason,
        account_identity=meta.account_identity,
    )


def fingerprint_account_identity(username_material: str | None) -> str | None:
    """Stable non-reversible identity fingerprint for a provider credential."""
    text = (username_material or "").strip()
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def resolve_account_identity(
    db: Any,
    user_id: str,
    provider: str,
    *,
    decrypt_username: Any | None = None,
) -> str | None:
    """Fingerprint the current credential identity for ``user_id`` + ``provider``."""
    try:
        row = db.execute(
            """
            SELECT username_enc, created_at FROM account_credentials
            WHERE user_id = ? AND source = ?
            """,
            (user_id, provider),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if row is None:
        return None
    username_enc = row["username_enc"] if hasattr(row, "keys") else row[0]
    created_at = row["created_at"] if hasattr(row, "keys") else row[1]
    material = None
    if username_enc and decrypt_username is not None:
        try:
            material = decrypt_username(user_id, username_enc) or None
        except Exception:  # noqa: BLE001
            material = None
    if not material:
        # Fall back to ciphertext / created_at so empty usernames still isolate rows.
        material = (username_enc or "") + "|" + (created_at or "")
    return fingerprint_account_identity(material)


# ── In-flight / present ──────────────────────────────────────────────────────


def is_customer_refresh_in_flight(
    access_view: Any | None,
    *,
    verification_lifecycle: str | None = None,
    background_verification: bool | None = None,
) -> bool:
    """True while a verification/extraction cycle has not reached a terminal outcome."""
    lifecycle = (
        verification_lifecycle
        if verification_lifecycle is not None
        else (
            getattr(access_view, "active_verification_lifecycle", None)
            if access_view
            else None
        )
    )
    lifecycle = (lifecycle or "").strip()
    if lifecycle in TERMINAL_VERIFICATION_LIFECYCLES:
        return False

    bg = (
        background_verification
        if background_verification is not None
        else bool(
            getattr(access_view, "background_verification", False) if access_view else False
        )
    )
    if bg:
        return True
    if lifecycle in ACTIVE_VERIFICATION_LIFECYCLES:
        return True
    if access_view is None:
        return False

    readiness = (getattr(access_view, "readiness", None) or "").strip()
    live_access = (getattr(access_view, "live_access", None) or "").strip()
    session_state = (getattr(access_view, "session_state", None) or "").strip()
    if readiness == "checking" or live_access == _LIVE_CHECKING or session_state == "checking":
        return True
    return False


def customer_visible_signature(view: CapabilityView) -> tuple[Any, ...]:
    """Identity of customer-visible card content (excludes timestamps / IDs)."""
    return (
        view.state.value,
        view.headline,
        view.explanations,
        tuple((e.text, e.ok) for e in view.evidence),
        view.confidence,
        tuple((f.label, f.value) for f in view.extracted_fields),
        view.action_required,
        view.action_label,
        view.action_url,
    )


def customer_visible_same(left: CapabilityView, right: CapabilityView) -> bool:
    return customer_visible_signature(left) == customer_visible_signature(right)


def _with_refresh(view: CapabilityView, *, first_ever: bool) -> CapabilityView:
    return replace(
        view,
        is_refreshing=True,
        refresh_label=REFRESH_LABEL_VERBOSE if first_ever else REFRESH_LABEL,
    )


def _as_stable(view: CapabilityView) -> CapabilityView:
    return replace(view, is_refreshing=False, refresh_label=None)


def _first_ever_checking_view(live: CapabilityView) -> CapabilityView:
    """Neutral checking face — not a completed Login Unknown conclusion."""
    return replace(
        live,
        state=CapabilityState.LOGIN_UNKNOWN,
        headline=FIRST_EVER_CHECKING_HEADLINE,
        explanations=(),
        evidence=(EvidenceItem(FIRST_EVER_CHECKING_EVIDENCE, None),),
        confidence=None,
        action_required=False,
        action_label=None,
        action_url=None,
        extracted_fields=(),
        is_refreshing=True,
        refresh_label=REFRESH_LABEL_VERBOSE,
    )


def merge_unchanged_presentation(
    previous: CapabilityView,
    live: CapabilityView,
) -> CapabilityView:
    """Keep prior visual card; refresh only last-verified / ID-bearing meta."""
    prev_tv = previous.truth_validation
    live_tv = live.truth_validation
    truth = prev_tv
    if prev_tv is not None and live_tv is not None:
        truth = replace(
            prev_tv,
            generated_at=live_tv.generated_at,
            developer_ids=dict(live_tv.developer_ids),
            transition=live_tv.transition,
        )
    elif live_tv is not None:
        truth = live_tv

    if len(previous.pipeline) == len(live.pipeline) and all(
        a.name == b.name and a.verdict == b.verdict
        for a, b in zip(previous.pipeline, live.pipeline)
    ):
        pipeline = live.pipeline
    else:
        pipeline = previous.pipeline

    return replace(
        previous,
        last_verified=live.last_verified or previous.last_verified,
        pipeline=pipeline,
        truth_validation=truth,
        is_refreshing=False,
        refresh_label=None,
    )


def present_customer_capability(
    live: CapabilityView,
    *,
    previous_stable: CapabilityView | None = None,
    access_view: Any | None = None,
    verification_lifecycle: str | None = None,
    background_verification: bool | None = None,
    force_unknown: bool = False,
) -> CapabilityView:
    """Gate live capability into the customer-visible Truth card.

    force_unknown: developer override — show live immediately. Never persisted
    by callers that honor ``persist=False`` / ``force_unknown`` on save.
    """
    if force_unknown:
        return _as_stable(live)

    refreshing = is_customer_refresh_in_flight(
        access_view,
        verification_lifecycle=verification_lifecycle,
        background_verification=background_verification,
    )

    if refreshing:
        if previous_stable is not None:
            return _with_refresh(previous_stable, first_ever=False)
        # No prior published card. If live already resolved to a non-unknown
        # face (e.g. readiness SWR kept EXTRACTION_SUCCESS), keep it with a
        # refresh indicator — do not invent first-ever checking.
        if live.state != CapabilityState.LOGIN_UNKNOWN:
            return _with_refresh(live, first_ever=False)
        return _first_ever_checking_view(live)

    if previous_stable is not None and customer_visible_same(previous_stable, live):
        return merge_unchanged_presentation(previous_stable, live)
    return _as_stable(live)


# ── Persistence ──────────────────────────────────────────────────────────────


def _utc_now_iso() -> str:
    return to_utc_iso_z(datetime.now(timezone.utc))


_SCHEMA_COLUMNS = (
    ("verification_id", "TEXT"),
    ("access_cycle_id", "TEXT"),
    ("verification_completed_at", "TEXT"),
    ("lifecycle", "TEXT"),
    ("terminal_reason", "TEXT"),
    ("account_identity", "TEXT"),
)


def ensure_customer_capability_presentation_tables(db: Any) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_capability_presentation (
            user_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            capability_state TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            verification_id TEXT,
            access_cycle_id TEXT,
            verification_completed_at TEXT,
            lifecycle TEXT,
            terminal_reason TEXT,
            account_identity TEXT,
            PRIMARY KEY (user_id, provider)
        )
        """
    )
    # Migrate older installs that only had the original columns.
    existing = {
        row[1]
        for row in db.execute(
            "PRAGMA table_info(customer_capability_presentation)"
        ).fetchall()
    }
    for name, col_type in _SCHEMA_COLUMNS:
        if name not in existing:
            try:
                db.execute(
                    f"ALTER TABLE customer_capability_presentation "
                    f"ADD COLUMN {name} {col_type}"
                )
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                if "duplicate column" not in msg and "already exists" not in msg:
                    raise
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_ccp_user "
        "ON customer_capability_presentation(user_id, updated_at DESC)"
    )
    db.commit()


def capability_view_to_payload(view: CapabilityView) -> dict[str, Any]:
    payload = view.to_dict()
    payload["is_refreshing"] = False
    payload["refresh_label"] = None
    return payload


def capability_view_from_payload(payload: dict[str, Any]) -> CapabilityView:
    """Rehydrate a CapabilityView from a stored payload (presentation only)."""
    from mighty.truth_validation import (
        ConfidenceLevel,
        EvidenceCategory,
        EvidenceOutcome,
        TruthEvidence,
        TruthPipelineStage,
        TruthTransition,
        TruthValidation,
    )

    state_raw = payload.get("capability_state") or payload.get("state") or "login_unknown"
    try:
        state = CapabilityState(state_raw)
    except ValueError:
        state = CapabilityState.LOGIN_UNKNOWN

    evidence = tuple(
        EvidenceItem(text=str(e.get("text") or ""), ok=e.get("ok"))
        for e in (payload.get("evidence") or [])
        if isinstance(e, dict)
    )
    extracted = tuple(
        ExtractedField(label=str(f.get("label") or ""), value=str(f.get("value") or ""))
        for f in (payload.get("extracted_fields") or [])
        if isinstance(f, dict) and f.get("label")
    )
    pipeline_stages: list[PipelineStage] = []
    for s in payload.get("pipeline") or []:
        if not isinstance(s, dict):
            continue
        verdict = str(s.get("verdict") or "UNKNOWN")
        if verdict not in ("PASS", "FAIL", "UNKNOWN", "NOT_RUN"):
            verdict = "UNKNOWN"
        pipeline_stages.append(
            PipelineStage(
                name=str(s.get("name") or ""),
                verdict=verdict,  # type: ignore[arg-type]
                timestamp=s.get("timestamp"),
                detail=s.get("detail"),
                id_label=s.get("id_label"),
            )
        )

    truth = None
    tv_raw = payload.get("truth_validation")
    if isinstance(tv_raw, dict):
        def _ev(item: dict[str, Any]) -> TruthEvidence:
            try:
                cat = EvidenceCategory(str(item.get("category") or "session"))
            except ValueError:
                cat = EvidenceCategory.SESSION
            try:
                outcome = EvidenceOutcome(str(item.get("outcome") or "UNKNOWN"))
            except ValueError:
                outcome = EvidenceOutcome.UNKNOWN
            return TruthEvidence(
                id=str(item.get("id") or ""),
                timestamp=item.get("timestamp"),
                category=cat,
                description=str(item.get("description") or ""),
                outcome=outcome,
                confidence_contribution=int(item.get("confidence_contribution") or 0),
                metadata=dict(item.get("metadata") or {}),
            )

        transition = None
        tr = tv_raw.get("transition")
        if isinstance(tr, dict) and tr.get("current_state"):
            transition = TruthTransition(
                previous_state=tr.get("previous_state"),
                current_state=str(tr["current_state"]),
                reason=str(tr.get("reason") or ""),
                timestamp=tr.get("timestamp"),
            )

        pipe: list[TruthPipelineStage] = []
        for s in tv_raw.get("pipeline") or []:
            if not isinstance(s, dict):
                continue
            try:
                verdict = EvidenceOutcome(str(s.get("verdict") or "UNKNOWN"))
            except ValueError:
                verdict = EvidenceOutcome.UNKNOWN
            pipe.append(
                TruthPipelineStage(
                    name=str(s.get("name") or ""),
                    verdict=verdict,
                    timestamp=s.get("timestamp"),
                    duration_ms=s.get("duration_ms"),
                    evidence_ids=tuple(s.get("evidence_ids") or ()),
                    detail=s.get("detail"),
                )
            )

        conf = str(tv_raw.get("confidence") or "Low")
        if conf not in {c.value for c in ConfidenceLevel}:
            conf = "Low"

        truth = TruthValidation(
            capability_state=str(tv_raw.get("capability_state") or state.value),
            confidence=conf,
            confidence_score=int(tv_raw.get("confidence_score") or 0),
            generated_at=str(tv_raw.get("generated_at") or ""),
            explanation=str(tv_raw.get("explanation") or ""),
            evidence=tuple(
                _ev(e) for e in (tv_raw.get("evidence") or []) if isinstance(e, dict)
            ),
            pipeline=tuple(pipe),
            timeline=tuple(
                _ev(e) for e in (tv_raw.get("timeline") or []) if isinstance(e, dict)
            ),
            transition=transition,
            developer_ids={
                str(k): (None if v is None else str(v))
                for k, v in dict(tv_raw.get("developer_ids") or {}).items()
            },
        )

    explanations = payload.get("explanations") or payload.get("explanation") or ()
    if isinstance(explanations, str):
        explanations = (explanations,)
    explanations = tuple(str(x) for x in explanations)
    headline = str(payload.get("headline") or payload.get("title") or "")

    return CapabilityView(
        provider=str(payload.get("provider") or "amex"),
        display_name=str(payload.get("display_name") or "American Express"),
        state=state,
        headline=headline,
        explanations=explanations,
        evidence=evidence,
        last_verified=payload.get("last_verified"),
        confidence=payload.get("confidence"),
        action_label=payload.get("action_label"),
        action_url=payload.get("action_url"),
        action_required=bool(payload.get("action_required")),
        extracted_fields=extracted,
        pipeline=tuple(pipeline_stages),
        truth_validation=truth,
        is_refreshing=False,
        refresh_label=None,
    )


def _row_order_meta(row: Any) -> PresentationOrderMeta:
    if hasattr(row, "keys"):
        return PresentationOrderMeta(
            verification_id=row["verification_id"] if "verification_id" in row.keys() else None,
            access_cycle_id=row["access_cycle_id"] if "access_cycle_id" in row.keys() else None,
            verification_completed_at=(
                row["verification_completed_at"]
                if "verification_completed_at" in row.keys()
                else None
            ),
            lifecycle=row["lifecycle"] if "lifecycle" in row.keys() else None,
            terminal_reason=(
                row["terminal_reason"] if "terminal_reason" in row.keys() else None
            ),
            account_identity=(
                row["account_identity"] if "account_identity" in row.keys() else None
            ),
        )
    # Positional fallback unused in practice.
    return PresentationOrderMeta()


def load_stable_capability(
    db: Any,
    user_id: str,
    provider: str,
    *,
    account_identity: str | None = None,
) -> CapabilityView | None:
    ensure_customer_capability_presentation_tables(db)
    row = db.execute(
        """
        SELECT payload_json, verification_id, access_cycle_id,
               verification_completed_at, lifecycle, terminal_reason,
               account_identity
        FROM customer_capability_presentation
        WHERE user_id = ? AND provider = ?
        """,
        (user_id, provider),
    ).fetchone()
    if row is None:
        return None

    stored_identity = None
    if hasattr(row, "keys") and "account_identity" in row.keys():
        stored_identity = row["account_identity"]
    # Identity mismatch → treat as no prior stable card (do not leak across accounts).
    if account_identity is not None and stored_identity and stored_identity != account_identity:
        return None
    if account_identity is not None and stored_identity is None and account_identity:
        # Legacy row without identity: invalidate rather than risk cross-account reuse.
        clear_stable_capability(db, user_id, provider)
        return None

    raw = row["payload_json"] if hasattr(row, "keys") else row[0]
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return capability_view_from_payload(payload)
    except Exception:  # noqa: BLE001 — corrupt row must not break dashboard
        return None


def load_stable_order_meta(
    db: Any,
    user_id: str,
    provider: str,
    *,
    ensure_schema: bool = True,
) -> PresentationOrderMeta | None:
    if ensure_schema:
        ensure_customer_capability_presentation_tables(db)
    row = db.execute(
        """
        SELECT verification_id, access_cycle_id, verification_completed_at,
               lifecycle, terminal_reason, account_identity
        FROM customer_capability_presentation
        WHERE user_id = ? AND provider = ?
        """,
        (user_id, provider),
    ).fetchone()
    if row is None:
        return None
    return _row_order_meta(row)


def save_stable_capability(
    db: Any,
    user_id: str,
    view: CapabilityView,
    *,
    order_meta: PresentationOrderMeta | None = None,
    access_view: Any | None = None,
    force_unknown: bool = False,
) -> bool:
    """Persist a terminal customer-visible card if newer than the stored one.

    Returns True when the row was written. Never stores in-flight holds or
    debug force_unknown overrides.

    Uses BEGIN IMMEDIATE so concurrent writers serialize the compare-and-swap
    against first-class ordering metadata.
    """
    if force_unknown or view.is_refreshing:
        return False

    ensure_customer_capability_presentation_tables(db)
    stable = _as_stable(view)
    meta = order_meta or order_meta_from_capability(stable, access_view=access_view)
    if not meta.verification_completed_at:
        meta = PresentationOrderMeta(
            verification_id=meta.verification_id,
            access_cycle_id=meta.access_cycle_id,
            verification_completed_at=_utc_now_iso(),
            lifecycle=meta.lifecycle,
            terminal_reason=meta.terminal_reason,
            account_identity=meta.account_identity,
        )

    payload = capability_view_to_payload(stable)
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    updated_at = _utc_now_iso()

    try:
        db.execute("BEGIN IMMEDIATE")
    except Exception:  # noqa: BLE001 — some test doubles lack transactions
        pass

    try:
        existing = load_stable_order_meta(
            db, user_id, stable.provider, ensure_schema=False,
        )
        if not is_newer_presentation(meta, existing):
            try:
                db.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            return False

        db.execute(
            """
            INSERT INTO customer_capability_presentation (
                user_id, provider, capability_state, payload_json, updated_at,
                verification_id, access_cycle_id, verification_completed_at,
                lifecycle, terminal_reason, account_identity
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, provider) DO UPDATE SET
                capability_state = excluded.capability_state,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at,
                verification_id = excluded.verification_id,
                access_cycle_id = excluded.access_cycle_id,
                verification_completed_at = excluded.verification_completed_at,
                lifecycle = excluded.lifecycle,
                terminal_reason = excluded.terminal_reason,
                account_identity = excluded.account_identity
            """,
            (
                user_id,
                stable.provider,
                stable.state.value,
                payload_json,
                updated_at,
                meta.verification_id,
                meta.access_cycle_id,
                meta.verification_completed_at,
                meta.lifecycle,
                meta.terminal_reason,
                meta.account_identity,
            ),
        )
        db.commit()
        return True
    except Exception:
        try:
            db.execute("ROLLBACK")
        except Exception:  # noqa: BLE001
            pass
        raise


def clear_stable_capability(db: Any, user_id: str, provider: str) -> None:
    """Invalidate persisted presentation for one user/provider (disconnect / identity)."""
    ensure_customer_capability_presentation_tables(db)
    db.execute(
        """
        DELETE FROM customer_capability_presentation
        WHERE user_id = ? AND provider = ?
        """,
        (user_id, provider),
    )
    db.commit()


def clear_all_stable_capabilities_for_user(db: Any, user_id: str) -> None:
    ensure_customer_capability_presentation_tables(db)
    db.execute(
        "DELETE FROM customer_capability_presentation WHERE user_id = ?",
        (user_id,),
    )
    db.commit()


def build_presented_capability_view(
    access_view: Any | None,
    *,
    previous_stable: CapabilityView | None = None,
    force_unknown: bool = False,
    persist_db: Any | None = None,
    persist_user_id: str | None = None,
    account_identity: str | None = None,
    order_meta: PresentationOrderMeta | None = None,
    **build_kwargs: Any,
) -> CapabilityView:
    """Build live capability, apply presentation gate, optionally persist terminal."""
    from mighty.capability_state import build_capability_view

    live = build_capability_view(access_view, **build_kwargs)

    previous = previous_stable
    if (
        previous is None
        and persist_db is not None
        and persist_user_id
        and not force_unknown
    ):
        provider = build_kwargs.get("provider") or (
            getattr(access_view, "provider", None) if access_view else "amex"
        )
        identity = account_identity
        if identity is None:
            identity = resolve_account_identity(persist_db, persist_user_id, provider)
        previous = load_stable_capability(
            persist_db,
            persist_user_id,
            provider,
            account_identity=identity,
        )

    presented = present_customer_capability(
        live,
        previous_stable=previous,
        access_view=access_view,
        force_unknown=force_unknown,
    )
    if (
        persist_db is not None
        and persist_user_id
        and not presented.is_refreshing
        and not force_unknown
    ):
        meta = order_meta or order_meta_from_capability(
            presented,
            access_view=access_view,
            account_identity=account_identity,
        )
        if account_identity and not meta.account_identity:
            meta = PresentationOrderMeta(
                verification_id=meta.verification_id,
                access_cycle_id=meta.access_cycle_id,
                verification_completed_at=meta.verification_completed_at,
                lifecycle=meta.lifecycle,
                terminal_reason=meta.terminal_reason,
                account_identity=account_identity,
            )
        meta = enrich_order_meta_from_db(
            persist_db,
            persist_user_id,
            meta,
            provider=presented.provider,
        )
        save_stable_capability(
            persist_db,
            persist_user_id,
            presented,
            order_meta=meta,
            access_view=access_view,
            force_unknown=False,
        )
    return presented
