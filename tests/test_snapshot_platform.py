"""Tests for the provider-independent Snapshot Platform."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from mighty.fact_generator import (
    assert_no_advice_language,
    fact_to_bullet,
    format_facts_summary,
    format_persisted_refresh_summary,
)
from mighty.provider_connector import (
    AccountSnapshot,
    AccountType,
    Completeness,
    FinancialAccount,
    MoneyAmount,
    RewardsBalance,
)
from mighty.provider_runtime import parse_args, run_connector_refresh_with_runtime
from mighty.snapshot_diff import FactType, PRESCRIPTIVE_FACT_FRAGMENTS, diff_snapshots
from mighty.snapshot_store import (
    LocalFileSnapshotStore,
    build_extraction_summary,
    build_stored_snapshot,
    persist_refresh_snapshot,
    stored_snapshot_from_dict,
)


def _money(amount: str, currency: str = "USD") -> MoneyAmount:
    return MoneyAmount(amount=Decimal(amount), currency=currency)


def _account(
    account_id: str,
    *,
    name: str = "Gold Card",
    product: str | None = "Gold Card",
    balance: str | None = "500.00",
    available: str | None = "9500.00",
    due: str | None = "25.00",
    due_date: date | None = date(2026, 3, 1),
    last_four: str | None = "2008",
) -> FinancialAccount:
    return FinancialAccount(
        provider_account_id=account_id,
        display_name=name,
        account_type=AccountType.CREDIT_CARD,
        currency="USD",
        observed_at="2026-07-19T12:00:00+00:00",
        product_name=product,
        last_four=last_four,
        current_balance=_money(balance) if balance is not None else None,
        available_credit=_money(available) if available is not None else None,
        payment_due_amount=_money(due) if due is not None else None,
        payment_due_date=due_date,
    )


def _rewards(balance: str = "124350", program: str = "Membership Rewards") -> RewardsBalance:
    return RewardsBalance(
        program_name=program,
        balance=Decimal(balance),
        unit="points",
        observed_at="2026-07-19T12:00:00+00:00",
    )


def _snapshot(
    *,
    provider: str = "amex",
    customer: str | None = "cust_opaque_1",
    accounts: tuple[FinancialAccount, ...] | None = None,
    rewards: tuple[RewardsBalance, ...] | None = None,
    observed_at: str = "2026-07-19T12:00:00+00:00",
    verified_at: str | None = "2026-07-19T12:00:00+00:00",
) -> AccountSnapshot:
    return AccountSnapshot(
        provider=provider,
        accounts=accounts if accounts is not None else (_account("acct_gold"),),
        rewards=rewards if rewards is not None else (_rewards(),),
        observed_at=observed_at,
        verified_at=verified_at,
        completeness=Completeness.FULL,
        warnings=(),
        provider_customer_id=customer,
        provider_metadata={},
    )


def test_snapshot_persistence_and_retrieval(tmp_path: Path):
    store = LocalFileSnapshotStore(tmp_path)
    snap = _snapshot()
    record = build_stored_snapshot(
        snap,
        connector_version="amex-connector/1",
        extraction_summary=build_extraction_summary(snap, refresh_status="success"),
    )
    stored = store.append(record)
    assert stored.snapshot_id == record.snapshot_id

    loaded = store.get(record.snapshot_id)
    assert loaded is not None
    assert loaded.provider == "amex"
    assert loaded.snapshot.accounts[0].provider_account_id == "acct_gold"
    assert loaded.snapshot.rewards[0].balance == Decimal("124350")

    latest = store.get_latest(provider="amex", provider_customer_id="cust_opaque_1")
    assert latest is not None
    assert latest.snapshot_id == record.snapshot_id

    # Append-only: same id rejected.
    with pytest.raises(ValueError, match="snapshot_already_exists"):
        store.append(record)


def test_snapshot_retrieval_newest_first(tmp_path: Path):
    store = LocalFileSnapshotStore(tmp_path)
    first = build_stored_snapshot(
        _snapshot(observed_at="2026-07-19T10:00:00+00:00"),
        snapshot_id="snap-1",
    )
    second = build_stored_snapshot(
        _snapshot(observed_at="2026-07-19T11:00:00+00:00"),
        snapshot_id="snap-2",
    )
    store.append(first)
    store.append(second)
    listed = store.list_snapshots(
        provider="amex",
        provider_customer_id="cust_opaque_1",
        limit=10,
    )
    assert [item.snapshot_id for item in listed] == ["snap-2", "snap-1"]
    assert store.get_latest(provider="amex", provider_customer_id="cust_opaque_1").snapshot_id == "snap-2"


def test_diff_no_change_snapshots():
    before = _snapshot()
    after = _snapshot(observed_at="2026-07-19T13:00:00+00:00")
    # Same verified_at and balances → no material facts except possible last-verified skip
    facts = diff_snapshots(before, after, previous_id="a", after_id="b")
    assert facts == []


def test_diff_balance_changes():
    before = _snapshot(accounts=(_account("acct_gold", balance="920.18"),))
    after = _snapshot(accounts=(_account("acct_gold", balance="500.00"),))
    facts = diff_snapshots(before, after, previous_id="a", after_id="b")
    types = {fact.fact_type for fact in facts}
    assert FactType.BALANCE_CHANGED in types
    balance_fact = next(f for f in facts if f.fact_type == FactType.BALANCE_CHANGED)
    assert "920.18" in balance_fact.explanation
    assert "500.00" in balance_fact.explanation


def test_diff_rewards_changes():
    before = _snapshot(rewards=(_rewards("124350"),))
    after = _snapshot(rewards=(_rewards("125120"),))
    facts = diff_snapshots(before, after, previous_id="a", after_id="b")
    assert len(facts) == 1
    assert facts[0].fact_type == FactType.REWARDS_CHANGED
    assert "124,350" in facts[0].explanation
    assert "125,120" in facts[0].explanation


def test_diff_payment_changes():
    before = _snapshot(
        accounts=(
            _account(
                "acct_gold",
                due="520.00",
                due_date=date(2026, 2, 15),
            ),
        )
    )
    after = _snapshot(
        accounts=(
            _account(
                "acct_gold",
                due="412.00",
                due_date=date(2026, 3, 1),
            ),
        )
    )
    facts = diff_snapshots(before, after, previous_id="a", after_id="b")
    types = {fact.fact_type for fact in facts}
    assert FactType.PAYMENT_DUE_CHANGED in types
    assert FactType.PAYMENT_DATE_CHANGED in types


def test_diff_multiple_accounts():
    before = _snapshot(
        accounts=(
            _account("acct_gold", name="Gold Card", balance="500.00"),
            _account("acct_plat", name="Platinum Card", balance="1234.56", last_four="1009"),
        )
    )
    after = _snapshot(
        accounts=(
            _account("acct_gold", name="Gold Card", balance="400.00"),
            _account("acct_plat", name="Platinum Card", balance="1234.56", last_four="1009"),
        )
    )
    facts = diff_snapshots(before, after, previous_id="a", after_id="b")
    balance_facts = [f for f in facts if f.fact_type == FactType.BALANCE_CHANGED]
    assert len(balance_facts) == 1
    assert balance_facts[0].account_id == "acct_gold"


def test_diff_new_account():
    before = _snapshot(accounts=(_account("acct_gold"),))
    after = _snapshot(
        accounts=(
            _account("acct_gold"),
            _account("acct_blue", name="Blue Cash Everyday", product="Blue Cash Everyday"),
        )
    )
    facts = diff_snapshots(before, after, previous_id="a", after_id="b")
    new_facts = [f for f in facts if f.fact_type == FactType.NEW_ACCOUNT]
    assert len(new_facts) == 1
    assert new_facts[0].account_id == "acct_blue"
    assert "Blue Cash Everyday" in new_facts[0].explanation


def test_diff_removed_account():
    before = _snapshot(
        accounts=(
            _account("acct_gold"),
            _account("acct_blue", name="Blue Cash Everyday"),
        )
    )
    after = _snapshot(accounts=(_account("acct_gold"),))
    facts = diff_snapshots(before, after, previous_id="a", after_id="b")
    removed = [f for f in facts if f.fact_type == FactType.ACCOUNT_REMOVED]
    assert len(removed) == 1
    assert removed[0].account_id == "acct_blue"


def test_stable_account_matching_ignores_display_rename():
    before = _snapshot(
        accounts=(_account("acct_gold", name="Gold Card", last_four="2008"),)
    )
    after = _snapshot(
        accounts=(_account("acct_gold", name="Amex Gold", last_four="9999"),)
    )
    facts = diff_snapshots(before, after, previous_id="a", after_id="b")
    types = {fact.fact_type for fact in facts}
    assert FactType.NEW_ACCOUNT not in types
    assert FactType.ACCOUNT_REMOVED not in types
    assert FactType.ACCOUNT_RENAMED in types


def test_provider_independence_chase_snapshot():
    before = _snapshot(
        provider="chase",
        customer="chase_cust",
        accounts=(_account("chase_acct_1", name="Sapphire Preferred", balance="100.00"),),
        rewards=(_rewards("1000", program="Ultimate Rewards"),),
    )
    after = _snapshot(
        provider="chase",
        customer="chase_cust",
        accounts=(_account("chase_acct_1", name="Sapphire Preferred", balance="250.00"),),
        rewards=(_rewards("1500", program="Ultimate Rewards"),),
        observed_at="2026-07-19T14:00:00+00:00",
    )
    facts = diff_snapshots(before, after, previous_id="c1", after_id="c2")
    assert {f.provider for f in facts} == {"chase"}
    types = {f.fact_type for f in facts}
    assert FactType.BALANCE_CHANGED in types
    assert FactType.REWARDS_CHANGED in types
    summary = format_facts_summary(facts)
    assert "Ultimate Rewards" in summary
    assert "Current balance" in summary


def test_no_advice_language_in_facts_and_summary():
    before = _snapshot(rewards=(_rewards("100"),))
    after = _snapshot(rewards=(_rewards("200"),))
    facts = diff_snapshots(before, after, previous_id="a", after_id="b")
    summary = format_facts_summary(facts)
    for text in [summary, *(f.explanation for f in facts), *(fact_to_bullet(f) for f in facts)]:
        lowered = text.lower()
        for fragment in PRESCRIPTIVE_FACT_FRAGMENTS:
            assert fragment not in lowered
        assert_no_advice_language(text)


def test_available_credit_and_field_availability():
    before = _snapshot(
        accounts=(
            _account("acct_gold", available=None, balance="100.00"),
        )
    )
    after = _snapshot(
        accounts=(
            _account("acct_gold", available="1800.00", balance="100.00"),
        )
    )
    facts = diff_snapshots(before, after, previous_id="a", after_id="b")
    types = {f.fact_type for f in facts}
    assert FactType.FIELD_BECAME_AVAILABLE in types


def test_persist_first_and_second_refresh(tmp_path: Path):
    store = LocalFileSnapshotStore(tmp_path)
    first = persist_refresh_snapshot(
        _snapshot(observed_at="2026-07-19T10:00:00+00:00"),
        store=store,
        connector_version="amex-connector/1",
    )
    assert first.first_snapshot is True
    assert first.summary == "First snapshot recorded."
    assert first.telemetry.previous_snapshot_found is False
    assert first.telemetry.facts_generated == 0

    second = persist_refresh_snapshot(
        _snapshot(
            observed_at="2026-07-19T11:00:00+00:00",
            rewards=(_rewards("125120"),),
            accounts=(_account("acct_gold", balance="80.00", due="412.00"),),
        ),
        store=store,
        connector_version="amex-connector/1",
    )
    assert second.first_snapshot is False
    assert second.telemetry.previous_snapshot_found is True
    assert second.telemetry.facts_generated >= 1
    assert "Changes since previous refresh" in second.summary
    assert store.get_latest(
        provider="amex",
        provider_customer_id="cust_opaque_1",
    ).snapshot_id == second.stored.snapshot_id


def test_stored_snapshot_roundtrip_dict():
    record = build_stored_snapshot(_snapshot())
    restored = stored_snapshot_from_dict(record.to_dict())
    assert restored.snapshot_id == record.snapshot_id
    assert restored.snapshot.accounts[0].payment_due_date == date(2026, 3, 1)
    assert restored.snapshot.rewards[0].balance == Decimal("124350")


def test_cli_persist_flag_parses():
    with patch(
        "sys.argv",
        ["provider_runtime.py", "connector-refresh", "amex", "--persist"],
    ):
        args = parse_args()
    assert args.command == "connector-refresh"
    assert args.persist is True


def test_cli_persist_first_and_second_output(tmp_path: Path):
    from mighty.amex_connector import AmexConnector
    from mighty.amex_extractor import extract_from_dom_text
    from mighty.provider_runtime import VerificationResult

    overview = """
    Account Home
    Membership Rewards Points Balance 124,350
    Gold Card
    Card Ending 2008
    Current Balance $500.00
    Available Credit $9,500.00
    Minimum Payment Due $25.00
    Due Date 03/01/2026
    """
    overview_second = overview.replace("124,350", "125,120").replace("$500.00", "$80.00")

    def signed_in():
        return VerificationResult(
            provider="amex",
            authentication_state="SIGNED_IN",
            reason="ok",
            observed_at="2026-07-19T12:00:00+00:00",
            final_url="https://global.americanexpress.com/overview",
            page_title="Overview",
            login_url_detected=False,
            login_marker_count=0,
            authenticated_marker_count=2,
            session_api_200_count=1,
            session_api_denied_count=0,
        )

    def ensure_runtime(**kwargs):
        return {
            "ok": True,
            "runtime_preexisting": True,
            "runtime_started_by_campaign": False,
            "process": None,
        }

    def prepare(**kwargs):
        return {
            "ok": True,
            "managed_browser_preexisting": True,
            "managed_browser_launched": False,
            "managed_browser_restarted": False,
            "interrupted": False,
            "final_authentication_state": "SIGNED_IN",
        }

    def factory_for(text: str):
        observation = extract_from_dom_text(text)

        def factory():
            return AmexConnector(
                ensure_usable_session_fn=lambda p: {
                    "ok": True,
                    "authentication_state": "SIGNED_IN",
                },
                ensure_provider_surface_fn=lambda p, s: {"ok": True},
                execute_readonly_extraction_fn=lambda p, e: {
                    "ok": True,
                    "observation": observation,
                    "method_counts": observation.method_counts,
                },
                verify_fn=lambda p: signed_in(),
            )

        return factory

    printed_first: list[str] = []
    first = run_connector_refresh_with_runtime(
        provider="amex",
        root=tmp_path,
        persist=True,
        ensure_runtime_fn=ensure_runtime,
        prepare_session_fn=prepare,
        close_managed_browser_fn=lambda **k: {"closed": False},
        stop_runtime_fn=lambda **k: None,
        connector_factory_fn=factory_for(overview),
        print_fn=lambda *a, **k: printed_first.append(" ".join(str(x) for x in a)),
    )
    assert first["ok"] is True
    assert first["persist"] is True
    assert first["snapshot_persist"]["first_snapshot"] is True
    text_first = "\n".join(printed_first)
    assert "Amex connector refresh" in text_first
    assert "Status:" in text_first
    assert "Snapshot:" in text_first
    assert "stored" in text_first
    assert "First snapshot recorded." in text_first

    printed_second: list[str] = []
    second = run_connector_refresh_with_runtime(
        provider="amex",
        root=tmp_path,
        persist=True,
        ensure_runtime_fn=ensure_runtime,
        prepare_session_fn=prepare,
        close_managed_browser_fn=lambda **k: {"closed": False},
        stop_runtime_fn=lambda **k: None,
        connector_factory_fn=factory_for(overview_second),
        print_fn=lambda *a, **k: printed_second.append(" ".join(str(x) for x in a)),
    )
    assert second["ok"] is True
    assert second["snapshot_persist"]["first_snapshot"] is False
    assert second["snapshot_persist"]["telemetry"]["previous_snapshot_found"] is True
    assert second["snapshot_persist"]["telemetry"]["facts_generated"] >= 1
    text_second = "\n".join(printed_second)
    assert "Changes since previous refresh" in text_second
    assert "•" in text_second
    for fragment in ("you should", "we recommend", "redeem", "optimize"):
        assert fragment not in text_second.lower()

    # Snapshots landed on disk under the local store.
    store_files = list((tmp_path / "snapshots").rglob("*.json"))
    assert len([p for p in store_files if p.name != "index.json"]) >= 2


def test_format_persisted_refresh_summary_shapes():
    class _Result:
        first_snapshot = False
        summary = (
            "Changes since previous refresh\n\n"
            "• Membership Rewards increased by 770 points."
        )

    text = format_persisted_refresh_summary(
        provider_label="Amex connector refresh",
        status="success",
        persist_result=_Result(),
    )
    assert text.startswith("Amex connector refresh\n")
    assert "Status:\nsuccess\n" in text
    assert "Snapshot:\nstored\n" in text
    assert "Membership Rewards increased by 770 points." in text

    first = format_persisted_refresh_summary(
        provider_label="Amex connector refresh",
        status="success",
        persist_result=type("R", (), {"first_snapshot": True, "summary": "First snapshot recorded."})(),
    )
    assert "First snapshot recorded." in first


def test_last_verified_changed():
    before = _snapshot(verified_at="2026-07-19T10:00:00+00:00")
    after = _snapshot(verified_at="2026-07-19T11:00:00+00:00")
    facts = diff_snapshots(before, after, previous_id="a", after_id="b")
    assert any(f.fact_type == FactType.LAST_VERIFIED_CHANGED for f in facts)
