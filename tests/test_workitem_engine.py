"""Comprehensive unit tests for the canonical WorkItem engine."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.workitem import (
    CanonicalModels,
    CoverageItem,
    HomeStatusMode,
    IllegalTransition,
    LifecycleError,
    ProofItem,
    UrgencyBand,
    WorkItem,
    WorkItemAction,
    WorkItemEvidence,
    WorkItemLifecycle,
    WorkItemOverlay,
    WorkItemPriority,
    WorkItemState,
    WorkItemType,
    WorkItemValidationError,
    can_transition,
    collapse_proof_items,
    create_proof_for_completion,
    effective_work_items,
    project_home,
    rank_work_items,
    transition,
)
from mighty.workitem.coverage import (
    AuthPosture,
    CoverageHealth,
    CoverageStatus,
    VerificationState,
)
from mighty.workitem.state_machine import TransitionContext


AS_OF = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


def _action(key: str = "act", intent: str = "Do the thing") -> WorkItemAction:
    return WorkItemAction(key=key, intent=intent)


def _item(
    *,
    id: str = "wi_1",
    type: WorkItemType = WorkItemType.INTERRUPT,
    priority: WorkItemPriority | None = None,
    state: WorkItemState = WorkItemState.VISIBLE,
    provider: str | None = "amex",
    expires_at: datetime | None = None,
    urgency_band: UrgencyBand = UrgencyBand.HARD,
    dismissible: bool | None = None,
    deferrable: bool = True,
    effort_weight: int = 100,
    blocks: tuple[str, ...] = (),
    confidence: float = 1.0,
    title: str | None = None,
    secondary: WorkItemAction | None = None,
) -> WorkItem:
    if priority is None:
        priority = {
            WorkItemType.INTERRUPT: WorkItemPriority.INTERRUPT,
            WorkItemType.APPROVAL: WorkItemPriority.APPROVAL,
            WorkItemType.OPPORTUNITY: WorkItemPriority.OPPORTUNITY,
            WorkItemType.SETUP: WorkItemPriority.SETUP_BLOCKING,
        }[type]
    if dismissible is None:
        if type is WorkItemType.APPROVAL:
            dismissible = False
        elif type is WorkItemType.INTERRUPT and urgency_band is UrgencyBand.HARD:
            dismissible = False
        elif (
            type is WorkItemType.SETUP
            and priority is WorkItemPriority.SETUP_BLOCKING
        ):
            dismissible = False
        else:
            dismissible = True
    return WorkItem(
        id=id,
        type=type,
        priority=priority,
        title=title or f"Title {id}",
        summary=f"Summary {id}",
        evidence=WorkItemEvidence.from_mapping({"account": provider or "none"}),
        primary_action=_action(),
        secondary_action=secondary,
        dismissible=dismissible,
        deferrable=deferrable,
        created_at=AS_OF - timedelta(hours=2),
        updated_at=AS_OF - timedelta(hours=1),
        expires_at=expires_at,
        proof_reference=None,
        provider=provider,
        capability="session",
        state=state,
        owner_domain="test",
        urgency_band=urgency_band,
        effort_weight=effort_weight,
        blocks=blocks,
        confidence=confidence,
    )


def _coverage(provider: str = "amex") -> CoverageItem:
    return CoverageItem(
        provider=provider,
        status=CoverageStatus.ENROLLED,
        health=CoverageHealth.HEALTHY,
        capabilities=("capture",),
        verification=VerificationState.VERIFIED,
        discovery="manual",
        authentication=AuthPosture.VALID,
        monitoring="active",
        display_name=provider.title(),
    )


# ── Model ───────────────────────────────────────────────────────────────────


class TestWorkItemModel:
    def test_frozen_and_round_trip(self):
        item = _item(secondary=_action("defer", "Not now"))
        restored = WorkItem.from_dict(item.to_dict())
        assert restored == item

    def test_type_immutable(self):
        item = _item()
        with pytest.raises(WorkItemValidationError):
            item.with_updates(type=WorkItemType.APPROVAL)

    def test_approval_not_dismissible(self):
        with pytest.raises(WorkItemValidationError):
            _item(type=WorkItemType.APPROVAL, dismissible=True)

    def test_hard_interrupt_not_dismissible(self):
        with pytest.raises(WorkItemValidationError):
            _item(
                type=WorkItemType.INTERRUPT,
                urgency_band=UrgencyBand.HARD,
                dismissible=True,
            )

    def test_secondary_must_differ_from_primary(self):
        with pytest.raises(WorkItemValidationError):
            _item(secondary=_action("act", "Same key as primary"))

    def test_priority_must_match_type(self):
        with pytest.raises(WorkItemValidationError):
            _item(
                type=WorkItemType.OPPORTUNITY,
                priority=WorkItemPriority.INTERRUPT,
            )


# ── Ranking ─────────────────────────────────────────────────────────────────


class TestRanking:
    def test_class_order(self):
        opp = _item(
            id="opp",
            type=WorkItemType.OPPORTUNITY,
            urgency_band=UrgencyBand.HIGH,
            dismissible=True,
        )
        setup_soft = _item(
            id="setup_soft",
            type=WorkItemType.SETUP,
            priority=WorkItemPriority.SETUP_NONBLOCKING,
            urgency_band=UrgencyBand.SOFT,
            dismissible=True,
        )
        setup_hard = _item(
            id="setup_hard",
            type=WorkItemType.SETUP,
            priority=WorkItemPriority.SETUP_BLOCKING,
            urgency_band=UrgencyBand.HARD,
        )
        approval = _item(
            id="apr",
            type=WorkItemType.APPROVAL,
            urgency_band=UrgencyBand.HIGH,
            dismissible=False,
            deferrable=False,
        )
        interrupt = _item(id="int", type=WorkItemType.INTERRUPT)

        # Deliberately reverse input order
        ranked = rank_work_items(
            [opp, setup_soft, setup_hard, approval, interrupt],
            as_of=AS_OF,
        )
        assert [i.id for i in ranked] == [
            "int",
            "apr",
            "setup_hard",
            "opp",
            "setup_soft",
        ]

    def test_hard_interrupt_before_soft(self):
        hard = _item(id="hard", urgency_band=UrgencyBand.HARD)
        soft = _item(
            id="soft",
            urgency_band=UrgencyBand.SOFT,
            dismissible=True,
        )
        ranked = rank_work_items([soft, hard], as_of=AS_OF)
        assert [i.id for i in ranked] == ["hard", "soft"]

    def test_earlier_expiry_wins_within_class(self):
        later = _item(
            id="later",
            type=WorkItemType.OPPORTUNITY,
            urgency_band=UrgencyBand.NORMAL,
            expires_at=AS_OF + timedelta(days=5),
            dismissible=True,
        )
        sooner = _item(
            id="sooner",
            type=WorkItemType.OPPORTUNITY,
            urgency_band=UrgencyBand.NORMAL,
            expires_at=AS_OF + timedelta(days=1),
            dismissible=True,
        )
        none = _item(
            id="none",
            type=WorkItemType.OPPORTUNITY,
            urgency_band=UrgencyBand.NORMAL,
            expires_at=None,
            dismissible=True,
        )
        ranked = rank_work_items([none, later, sooner], as_of=AS_OF)
        assert [i.id for i in ranked] == ["sooner", "later", "none"]

    def test_effort_within_band(self):
        hard = _item(id="a", effort_weight=50)
        easy = _item(id="b", effort_weight=10)
        ranked = rank_work_items([hard, easy], as_of=AS_OF)
        assert [i.id for i in ranked] == ["b", "a"]

    def test_dependency_a_blocks_b(self):
        a = _item(
            id="a",
            type=WorkItemType.OPPORTUNITY,
            urgency_band=UrgencyBand.NORMAL,
            blocks=("b",),
            dismissible=True,
        )
        b = _item(
            id="b",
            type=WorkItemType.OPPORTUNITY,
            urgency_band=UrgencyBand.NORMAL,
            dismissible=True,
        )
        ranked = rank_work_items([b, a], as_of=AS_OF)
        assert [i.id for i in ranked] == ["a", "b"]

    def test_confidence_within_band(self):
        low = _item(
            id="low",
            type=WorkItemType.OPPORTUNITY,
            confidence=0.2,
            dismissible=True,
        )
        high = _item(
            id="high",
            type=WorkItemType.OPPORTUNITY,
            confidence=0.9,
            dismissible=True,
        )
        ranked = rank_work_items([low, high], as_of=AS_OF)
        assert [i.id for i in ranked] == ["high", "low"]

    def test_expired_excluded_from_rank(self):
        live = _item(
            id="live",
            type=WorkItemType.OPPORTUNITY,
            expires_at=AS_OF + timedelta(hours=1),
            dismissible=True,
        )
        dead = _item(
            id="dead",
            type=WorkItemType.OPPORTUNITY,
            expires_at=AS_OF - timedelta(seconds=1),
            dismissible=True,
        )
        ranked = rank_work_items([dead, live], as_of=AS_OF)
        assert [i.id for i in ranked] == ["live"]


class TestTieBreaking:
    def test_provider_then_id(self):
        a = _item(
            id="z_item",
            type=WorkItemType.OPPORTUNITY,
            provider="zeta",
            dismissible=True,
        )
        b = _item(
            id="a_item",
            type=WorkItemType.OPPORTUNITY,
            provider="alpha",
            dismissible=True,
        )
        c = _item(
            id="b_item",
            type=WorkItemType.OPPORTUNITY,
            provider="alpha",
            dismissible=True,
        )
        ranked = rank_work_items([a, c, b], as_of=AS_OF)
        assert [i.id for i in ranked] == ["a_item", "b_item", "z_item"]

    def test_missing_provider_sorts_as_empty(self):
        missing = _item(
            id="m",
            type=WorkItemType.OPPORTUNITY,
            provider=None,
            dismissible=True,
        )
        named = _item(
            id="n",
            type=WorkItemType.OPPORTUNITY,
            provider="amex",
            dismissible=True,
        )
        ranked = rank_work_items([named, missing], as_of=AS_OF)
        assert [i.id for i in ranked] == ["m", "n"]

    def test_input_order_irrelevant(self):
        items = [
            _item(id="c", type=WorkItemType.OPPORTUNITY, provider="c", dismissible=True),
            _item(id="a", type=WorkItemType.OPPORTUNITY, provider="a", dismissible=True),
            _item(id="b", type=WorkItemType.OPPORTUNITY, provider="b", dismissible=True),
        ]
        forward = rank_work_items(items, as_of=AS_OF)
        reverse = rank_work_items(list(reversed(items)), as_of=AS_OF)
        assert [i.id for i in forward] == [i.id for i in reverse]


# ── State machine ────────────────────────────────────────────────────────────


class TestStateTransitions:
    def test_happy_path(self):
        life = WorkItemLifecycle()
        item = _item(state=WorkItemState.CREATED)
        item = life.make_visible(item, as_of=AS_OF)
        assert item.state is WorkItemState.VISIBLE
        item = life.expand(item, as_of=AS_OF)
        assert item.state is WorkItemState.EXPANDED
        result = life.complete(item, as_of=AS_OF, earn_proof=True)
        assert result.work_item.state is WorkItemState.PROOF
        assert result.proof is not None
        archived = life.archive(result.work_item, as_of=AS_OF)
        assert archived.state is WorkItemState.ARCHIVED

    def test_illegal_proof_to_visible(self):
        item = _item(state=WorkItemState.PROOF, dismissible=False)
        item = item.with_updates(proof_reference="proof:1")
        assert not can_transition(item, WorkItemState.VISIBLE)
        with pytest.raises(IllegalTransition):
            transition(
                item,
                WorkItemState.VISIBLE,
                context=TransitionContext(as_of=AS_OF),
            )

    def test_archived_cannot_reactivate(self):
        item = _item(state=WorkItemState.ARCHIVED)
        with pytest.raises(IllegalTransition):
            transition(
                item,
                WorkItemState.VISIBLE,
                context=TransitionContext(as_of=AS_OF),
            )

    def test_defer_requires_deferrable(self):
        item = _item(deferrable=False, dismissible=False)
        with pytest.raises(IllegalTransition):
            transition(
                item,
                WorkItemState.DEFERRED,
                context=TransitionContext(as_of=AS_OF),
            )


# ── Defer / dismiss / complete / expire ──────────────────────────────────────


class TestLifecycleDeferCompleteExpire:
    def test_defer_and_reactivate(self):
        life = WorkItemLifecycle()
        item = _item(
            type=WorkItemType.OPPORTUNITY,
            urgency_band=UrgencyBand.NORMAL,
            dismissible=True,
        )
        deferred, until = life.defer(item, as_of=AS_OF)
        assert deferred.state is WorkItemState.DEFERRED
        assert until == AS_OF + timedelta(hours=24)
        visible = life.reactivate(deferred, as_of=until)
        assert visible.state is WorkItemState.VISIBLE

    def test_defer_not_in_effective_queue(self):
        life = WorkItemLifecycle()
        item = _item(
            type=WorkItemType.OPPORTUNITY,
            dismissible=True,
            urgency_band=UrgencyBand.NORMAL,
        )
        deferred, until = life.defer(item, as_of=AS_OF)
        overlay = WorkItemOverlay(work_item_id=item.id, deferred_until=until)
        eff = effective_work_items([deferred], [overlay], as_of=AS_OF)
        assert eff == ()
        # After window ends, state is still deferred until reactivated —
        # overlay alone ending is not enough without lifecycle reactivate.
        eff_later = effective_work_items(
            [deferred],
            [WorkItemOverlay(work_item_id=item.id, deferred_until=until)],
            as_of=until + timedelta(seconds=1),
        )
        assert eff_later == ()

    def test_overlay_quiet_window_hides_visible_item(self):
        item = _item(
            type=WorkItemType.OPPORTUNITY,
            dismissible=True,
            urgency_band=UrgencyBand.NORMAL,
        )
        until = AS_OF + timedelta(hours=2)
        overlay = WorkItemOverlay(work_item_id=item.id, deferred_until=until)
        assert effective_work_items([item], [overlay], as_of=AS_OF) == ()
        assert effective_work_items(
            [item], [overlay], as_of=until + timedelta(seconds=1)
        ) == (item,)

    def test_dismiss_no_proof(self):
        life = WorkItemLifecycle()
        item = _item(
            type=WorkItemType.OPPORTUNITY,
            dismissible=True,
            urgency_band=UrgencyBand.NORMAL,
        )
        archived = life.dismiss(item, as_of=AS_OF)
        assert archived.state is WorkItemState.ARCHIVED
        assert archived.proof_reference is None

    def test_cannot_dismiss_approval(self):
        life = WorkItemLifecycle()
        item = _item(
            type=WorkItemType.APPROVAL,
            dismissible=False,
            deferrable=False,
            urgency_band=UrgencyBand.HIGH,
        )
        with pytest.raises(LifecycleError):
            life.dismiss(item, as_of=AS_OF)

    def test_complete_without_proof(self):
        life = WorkItemLifecycle()
        item = _item(
            type=WorkItemType.OPPORTUNITY,
            dismissible=True,
            urgency_band=UrgencyBand.NORMAL,
        )
        result = life.complete(item, as_of=AS_OF, earn_proof=False)
        assert result.work_item.state is WorkItemState.COMPLETED
        assert result.proof is None

    def test_approval_completion_always_earns_proof(self):
        life = WorkItemLifecycle()
        item = _item(
            type=WorkItemType.APPROVAL,
            dismissible=False,
            deferrable=False,
            urgency_band=UrgencyBand.HIGH,
        )
        result = life.complete(item, as_of=AS_OF, earn_proof=False)
        assert result.work_item.state is WorkItemState.PROOF
        assert result.proof is not None
        assert result.work_item.proof_reference == result.proof.id

    def test_expire_opportunity_archives_without_proof(self):
        life = WorkItemLifecycle()
        item = _item(
            type=WorkItemType.OPPORTUNITY,
            dismissible=True,
            urgency_band=UrgencyBand.NORMAL,
            expires_at=AS_OF - timedelta(minutes=1),
        )
        result = life.expire(item, as_of=AS_OF)
        assert result.work_item.state is WorkItemState.ARCHIVED
        assert result.proof is None

    def test_expire_approval_records_proof(self):
        life = WorkItemLifecycle()
        item = _item(
            type=WorkItemType.APPROVAL,
            dismissible=False,
            deferrable=False,
            urgency_band=UrgencyBand.HIGH,
            expires_at=AS_OF - timedelta(minutes=1),
        )
        result = life.expire(item, as_of=AS_OF)
        assert result.proof is not None
        assert result.work_item.state is WorkItemState.PROOF
        assert result.proof.outcome_class == "approval_expired"

    def test_apply_expirations_batch(self):
        life = WorkItemLifecycle()
        dead = _item(
            id="dead",
            type=WorkItemType.OPPORTUNITY,
            expires_at=AS_OF - timedelta(hours=1),
            dismissible=True,
        )
        live = _item(
            id="live",
            type=WorkItemType.OPPORTUNITY,
            expires_at=AS_OF + timedelta(hours=1),
            dismissible=True,
        )
        results = life.apply_expirations([dead, live], as_of=AS_OF)
        assert len(results) == 1
        assert results[0].work_item.id == "dead"


# ── Proof creation ───────────────────────────────────────────────────────────


class TestProofCreation:
    def test_create_proof_for_completion(self):
        item = _item(id="wi_proof")
        proof = create_proof_for_completion(
            item,
            as_of=AS_OF,
            summary="Session restored",
            outcome_class="interrupt_resolved",
        )
        assert proof.work_item_id == "wi_proof"
        assert proof.summary == "Session restored"
        assert proof.provider == "amex"

    def test_collapse_low_impact_same_day(self):
        p1 = ProofItem(
            id="p1",
            outcome_at=AS_OF,
            summary="Small change A",
            provider="amex",
            outcome_class="balance",
            impact="low",
        )
        p2 = ProofItem(
            id="p2",
            outcome_at=AS_OF + timedelta(minutes=5),
            summary="Small change B",
            provider="amex",
            outcome_class="balance",
            impact="low",
        )
        high = ProofItem(
            id="p3",
            outcome_at=AS_OF + timedelta(minutes=10),
            summary="Material win",
            provider="amex",
            outcome_class="benefit",
            impact="high",
        )
        rows = collapse_proof_items([p1, p2, high], as_of=AS_OF)
        assert any(r.count == 2 for r in rows)
        assert any(r.id == "p3" for r in rows)

    def test_proof_order_newest_first(self):
        older = ProofItem(
            id="a",
            outcome_at=AS_OF - timedelta(days=1),
            summary="Old",
            impact="normal",
        )
        newer = ProofItem(
            id="b",
            outcome_at=AS_OF,
            summary="New",
            impact="normal",
        )
        rows = collapse_proof_items([older, newer], as_of=AS_OF)
        assert rows[0].id == "b"


# ── HomeProjection ───────────────────────────────────────────────────────────


class TestHomeProjection:
    def test_calm_when_empty(self):
        state = project_home(CanonicalModels(), as_of=AS_OF)
        assert state.status is HomeStatusMode.CALM
        assert state.silence is True
        assert state.expanded_work_item_id is None
        assert state.work_queue == ()

    def test_expands_top_ranked(self):
        interrupt = _item(id="int")
        opp = _item(
            id="opp",
            type=WorkItemType.OPPORTUNITY,
            dismissible=True,
            urgency_band=UrgencyBand.NORMAL,
        )
        state = project_home(
            CanonicalModels(work_items=(opp, interrupt)),
            as_of=AS_OF,
        )
        assert state.expanded_work_item_id == "int"
        assert state.status is HomeStatusMode.NEEDS_USER
        assert state.silence is False
        assert [i.id for i in state.work_queue] == ["int", "opp"]

    def test_value_waiting_status(self):
        opp = _item(
            id="opp",
            type=WorkItemType.OPPORTUNITY,
            dismissible=True,
            urgency_band=UrgencyBand.NORMAL,
        )
        state = project_home(CanonicalModels(work_items=(opp,)), as_of=AS_OF)
        assert state.status is HomeStatusMode.VALUE_WAITING

    def test_setup_incomplete_status(self):
        setup = _item(
            id="setup",
            type=WorkItemType.SETUP,
            priority=WorkItemPriority.SETUP_BLOCKING,
        )
        state = project_home(CanonicalModels(work_items=(setup,)), as_of=AS_OF)
        assert state.status is HomeStatusMode.SETUP_INCOMPLETE

    def test_determinism(self):
        items = (
            _item(id="b", provider="b"),
            _item(
                id="a",
                type=WorkItemType.OPPORTUNITY,
                provider="a",
                dismissible=True,
            ),
            _item(
                id="c",
                type=WorkItemType.APPROVAL,
                provider="c",
                dismissible=False,
                deferrable=False,
                urgency_band=UrgencyBand.HIGH,
            ),
        )
        coverage = (_coverage("zeta"), _coverage("amex"))
        proof = (
            ProofItem(
                id="p1",
                outcome_at=AS_OF,
                summary="Win",
                provider="amex",
            ),
        )
        models = CanonicalModels(work_items=items, coverage=coverage, proof=proof)
        overlays = (
            WorkItemOverlay(work_item_id="nope", inactive=True),
        )
        s1 = project_home(models, overlays, as_of=AS_OF)
        s2 = project_home(models, overlays, as_of=AS_OF)
        assert s1.to_dict() == s2.to_dict()
        # Input order shuffle of work_items
        models_rev = CanonicalModels(
            work_items=tuple(reversed(items)),
            coverage=tuple(reversed(coverage)),
            proof=proof,
        )
        s3 = project_home(models_rev, overlays, as_of=AS_OF)
        assert [i.id for i in s1.work_queue] == [i.id for i in s3.work_queue]
        assert s1.expanded_work_item_id == s3.expanded_work_item_id
        assert [c.provider for c in s1.coverage] == [
            c.provider for c in s3.coverage
        ]

    def test_expired_leaves_queue_without_clear_ritual(self):
        dead = _item(
            id="dead",
            type=WorkItemType.OPPORTUNITY,
            expires_at=AS_OF - timedelta(seconds=1),
            dismissible=True,
        )
        state = project_home(CanonicalModels(work_items=(dead,)), as_of=AS_OF)
        assert state.silence is True
        assert state.work_queue == ()

    def test_coverage_and_proof_do_not_invent_work(self):
        state = project_home(
            CanonicalModels(
                coverage=(_coverage(),),
                proof=(
                    ProofItem(
                        id="p",
                        outcome_at=AS_OF,
                        summary="Past win",
                    ),
                ),
            ),
            as_of=AS_OF,
        )
        assert state.silence is True
        assert state.coverage[0].provider == "amex"
        assert len(state.proof) == 1
