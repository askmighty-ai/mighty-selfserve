"""Tests for Amex session-expiration maintenance in Provider Runtime."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mighty.provider_runtime import (
    MAINTENANCE_RESULT_IN_PROGRESS,
    MAINTENANCE_RESULT_NO_DIALOG,
    MAINTENANCE_RESULT_SESSION_EXTENDED,
    MAINTENANCE_RESULT_SESSION_NOT_CONFIRMED,
    MAINTENANCE_RESULT_WATCHER_ERROR,
    MaintenanceOutcome,
    ProviderRuntime,
    confirm_session_extended,
    dismiss_amex_expiration_dialog,
    expiration_dialog_criteria_met,
    extend_amex_session_on_page,
    inspect_amex_expiration_dialog,
)


GENUINE_DIALOG_TEXT = (
    "Your session is about to expire. "
    "You will be signed out due to inactivity. "
    "Select Continue to stay signed in."
)


def test_genuine_expiration_dialog_criteria_detected():
    assert expiration_dialog_criteria_met(
        GENUINE_DIALOG_TEXT,
        has_continue_button=True,
    )


def test_unrelated_continue_button_is_ignored():
    # Continue exists elsewhere, but dialog text is not an expiration warning.
    assert not expiration_dialog_criteria_met(
        "Continue to view your card benefits and offers.",
        has_continue_button=True,
    )


def test_missing_expiration_language_is_ignored():
    # Inactivity wording without the required expiration headline.
    assert not expiration_dialog_criteria_met(
        "Due to inactivity, please respond soon or you may be signed out.",
        has_continue_button=True,
    )
    # Missing Continue inside the dialog must always fail.
    assert not expiration_dialog_criteria_met(
        GENUINE_DIALOG_TEXT,
        has_continue_button=False,
    )
    # Unrelated modal with Continue and no expiration headline.
    assert not expiration_dialog_criteria_met(
        "Please confirm your mailing address to continue.",
        has_continue_button=True,
    )


def test_inspect_requires_dialog_continue_and_expiration_text():
    page = MagicMock()
    page.evaluate.return_value = {
        "detected": True,
        "continue_token": "tok-1",
        "dialog_text": GENUINE_DIALOG_TEXT.lower(),
    }
    info = inspect_amex_expiration_dialog(page)
    assert info["detected"] is True
    assert info["continue_token"] == "tok-1"

    page.evaluate.return_value = {
        "detected": True,
        "continue_token": "tok-2",
        "dialog_text": "click continue for rewards",
    }
    assert inspect_amex_expiration_dialog(page)["detected"] is False


def test_successful_click_plus_signed_in_records_session_extended():
    page = MagicMock()
    with patch(
        "mighty.provider_runtime.dismiss_amex_expiration_dialog",
        return_value=None,
    ):
        verification = SimpleNamespace(authentication_state="SIGNED_IN")
        outcome = extend_amex_session_on_page(page, verify_fn=lambda: verification)
    assert outcome.result == MAINTENANCE_RESULT_SESSION_EXTENDED
    assert outcome.verification_state == "SIGNED_IN"


def test_failed_confirmation_does_not_claim_success():
    outcome = confirm_session_extended(
        SimpleNamespace(authentication_state="SIGNED_OUT")
    )
    assert outcome.result == MAINTENANCE_RESULT_SESSION_NOT_CONFIRMED
    assert outcome.result != MAINTENANCE_RESULT_SESSION_EXTENDED

    outcome_unknown = confirm_session_extended(
        SimpleNamespace(authentication_state="LOGIN_UNKNOWN")
    )
    assert outcome_unknown.result == MAINTENANCE_RESULT_SESSION_NOT_CONFIRMED


def test_watcher_errors_do_not_become_signed_out(tmp_path: Path):
    runtime = ProviderRuntime(
        root=tmp_path,
        cdp_port=9333,
        state_path=tmp_path / "state.json",
        result_path=tmp_path / "result.json",
    )
    runtime.cdp_url = "http://127.0.0.1:9333"
    with patch.object(
        runtime,
        "_inspect_and_extend_session",
        side_effect=RuntimeError("cdp blew up"),
    ):
        payload = runtime.run_maintenance_once(force=True)

    assert payload["result"] == MAINTENANCE_RESULT_WATCHER_ERROR
    assert payload["result"] != "SIGNED_OUT"
    assert runtime.last_maintenance_result == MAINTENANCE_RESULT_WATCHER_ERROR
    assert runtime.last_result is None


def test_duplicate_concurrent_attempts_are_prevented(tmp_path: Path):
    runtime = ProviderRuntime(
        root=tmp_path,
        cdp_port=9333,
        state_path=tmp_path / "state.json",
        result_path=tmp_path / "result.json",
    )
    runtime.cdp_url = "http://127.0.0.1:9333"
    started = threading.Event()
    release = threading.Event()

    def slow_inspect(_cdp_url: str) -> MaintenanceOutcome:
        started.set()
        release.wait(timeout=2)
        return MaintenanceOutcome(
            result=MAINTENANCE_RESULT_NO_DIALOG,
            observed_at="2026-01-01T00:00:00+00:00",
            dialog_detected=False,
            reason="none",
        )

    with patch.object(runtime, "_inspect_and_extend_session", side_effect=slow_inspect):
        results: list[dict] = []

        def worker() -> None:
            results.append(runtime.run_maintenance_once(force=True))

        first = threading.Thread(target=worker)
        first.start()
        assert started.wait(timeout=2)
        second_payload = runtime.run_maintenance_once(force=True)
        assert second_payload["result"] == MAINTENANCE_RESULT_IN_PROGRESS
        release.set()
        first.join(timeout=2)

    assert any(item["result"] == MAINTENANCE_RESULT_NO_DIALOG for item in results)


def test_dismiss_clicks_only_marked_continue_inside_dialog():
    page = MagicMock()
    page.evaluate.side_effect = [
        {
            "detected": True,
            "continue_token": "tok-xyz",
            "dialog_text": GENUINE_DIALOG_TEXT.lower(),
        },
        {"detected": False, "continue_token": None, "dialog_text": None},
    ]
    button = MagicMock()
    button.is_visible.return_value = True
    locator = MagicMock()
    locator.count.return_value = 1
    locator.first = button
    page.locator.return_value = locator
    page.wait_for_timeout.return_value = None

    early = dismiss_amex_expiration_dialog(page)
    assert early is None
    page.locator.assert_called_with('[data-mighty-amex-continue="tok-xyz"]')
    button.click.assert_called_once()


def test_status_includes_maintenance_fields(tmp_path: Path):
    runtime = ProviderRuntime(
        root=tmp_path,
        cdp_port=9333,
        state_path=tmp_path / "state.json",
        result_path=tmp_path / "result.json",
    )
    status = runtime.status()
    assert "maintenance_running" in status
    assert "last_maintenance_attempt_at" in status
    assert "last_maintenance_result" in status
    assert "last_session_extended_at" in status
    assert "maintenance_attempt_count" in status
    assert "maintenance_success_count" in status


def test_maintenance_success_updates_counts(tmp_path: Path):
    runtime = ProviderRuntime(
        root=tmp_path,
        cdp_port=9333,
        state_path=tmp_path / "state.json",
        result_path=tmp_path / "result.json",
    )
    runtime.cdp_url = "http://127.0.0.1:9333"
    with patch.object(
        runtime,
        "_inspect_and_extend_session",
        return_value=MaintenanceOutcome(
            result=MAINTENANCE_RESULT_SESSION_EXTENDED,
            observed_at="2026-01-01T00:00:00+00:00",
            dialog_detected=True,
            verification_state="SIGNED_IN",
        ),
    ):
        payload = runtime.run_maintenance_once(force=True)

    assert payload["result"] == MAINTENANCE_RESULT_SESSION_EXTENDED
    assert runtime.maintenance_attempt_count == 1
    assert runtime.maintenance_success_count == 1
    assert runtime.last_session_extended_at == "2026-01-01T00:00:00+00:00"
