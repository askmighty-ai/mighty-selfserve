"""Tests for unattended campaign mode, notifications, and sleep prevention."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from mighty.provider_runtime import (
    BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
    CAMPAIGN_STATUS_WAITING_FOR_AUTHENTICATION,
    MIN_AUTH_REMINDER_MINUTES,
    ensure_expiration_campaign_signed_in,
    format_elapsed_duration,
    notify_macos_desktop,
    parse_args,
    resolve_auth_reminder_seconds,
    run_amex_expiration_campaign,
    run_amex_provider_campaign,
    start_owned_caffeinate,
    stop_owned_caffeinate,
    wait_for_enter_with_reminders,
)


def _verify_payload(state: str) -> dict:
    return {
        "ok": state == "SIGNED_IN",
        "result": {
            "provider": "amex",
            "authentication_state": state,
            "reason": f"state:{state}",
        },
    }


class _Http:
    def __init__(self, verify_states: list[str]) -> None:
        self.verify_states = list(verify_states)
        self._verify_index = 0

    def __call__(self, method: str, url: str, payload=None, *, timeout: float = 60):
        from urllib.parse import urlsplit

        path = urlsplit(url).path or url
        if path == "/health":
            return {"ok": True}
        if path == "/providers/amex/verify":
            state = self.verify_states[
                min(self._verify_index, len(self.verify_states) - 1)
            ]
            self._verify_index += 1
            return _verify_payload(state)
        if path == "/providers/amex/keepalive/probe":
            strategy = str((payload or {}).get("strategy") or "NONE")
            return {
                "ok": True,
                "success": True,
                "strategy": strategy,
                "reason": "success",
                "attempt": {
                    "attempted_at": "2026-01-01T00:00:00+00:00",
                    "strategy": strategy,
                    "action": "probe",
                    "success": True,
                    "result": "success",
                },
            }
        raise AssertionError(f"unexpected {method} {path}")


def _fake_experiment(**kwargs):
    output_dir = Path(kwargs["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "recorder").mkdir(parents=True, exist_ok=True)
    (output_dir / "experiment-summary.json").write_text("{}", encoding="utf-8")
    return {
        "ok": True,
        "outcome": "logged_out",
        "keepalive_outcome": "logged_out",
        "final_authentication_state": "SIGNED_OUT",
        "experiment_dir": str(output_dir),
        "zip_path": str(output_dir / "trial.zip"),
        "summary": {
            "outcome": "logged_out",
            "recorder_outcome": "logged_out",
            "keepalive_outcome": "logged_out",
            "keepalive_strategy": kwargs["strategy"],
            "keepalive_interval_seconds": kwargs["keepalive_interval_seconds"],
            "keepalive_wait_seconds": 1.0,
            "keepalive_completion_timeout": False,
            "final_authentication_state": "SIGNED_OUT",
        },
        "recorder": {
            "outcome": "logged_out",
            "logout_detected_at": "2026-01-01T00:10:00+00:00",
            "initial_canonical_authentication_state": "SIGNED_IN",
            "final_canonical_authentication_state": "SIGNED_OUT",
            "observations": [],
        },
        "keepalive_status": {
            "ok": True,
            "keepalive_logged_out": True,
            "keepalive_expiration_dialog_seen": False,
            "keepalive_events": [],
        },
        "exit_code": 0,
    }


def _browser_ensure(*, preexisting: bool = False, launched: bool = True):
    return lambda **_k: {
        "ok": True,
        "managed_browser_preexisting": preexisting,
        "managed_browser_launched_by_campaign": launched,
        "managed_browser_restarted_by_campaign": False,
        "cdp_url": "http://127.0.0.1:9223",
    }


def test_cli_registers_unattended_notify_prevent_sleep_flags():
    with patch(
        "sys.argv",
        [
            "provider_runtime.py",
            "campaign",
            "amex",
            "--trial",
            "SESSION_API:30",
            "--unattended",
            "--notify",
            "--prevent-sleep",
            "--auth-reminder-minutes",
            "20",
            "--analyze",
        ],
    ):
        args = parse_args()
    assert args.command == "campaign"
    assert args.unattended is True
    assert args.notify is True
    assert args.prevent_sleep is True
    assert args.auth_reminder_minutes == 20.0
    assert args.analyze is True


def test_resolve_auth_reminder_defaults_and_minimum():
    assert resolve_auth_reminder_seconds(unattended=False, auth_reminder_minutes=15) is None
    assert (
        resolve_auth_reminder_seconds(unattended=True, auth_reminder_minutes=None)
        == 15 * 60
    )
    assert (
        resolve_auth_reminder_seconds(unattended=True, auth_reminder_minutes=1)
        == MIN_AUTH_REMINDER_MINUTES * 60
    )


def test_unattended_campaign_starts_trials_without_confirmation(tmp_path: Path):
    prints: list[str] = []
    http = _Http(["SIGNED_IN", "SIGNED_IN"])
    result = run_amex_expiration_campaign(
        root=tmp_path,
        output_dir=tmp_path / "campaign",
        trials=["SESSION_API:30", "PAGE_ACTIVITY:30"],
        request_json_fn=http,
        run_experiment_fn=_fake_experiment,
        ensure_managed_browser_fn=_browser_ensure(),
        classify_managed_browser_fn=lambda: {
            "state": "HEALTHY",
            "cdp_url": "http://127.0.0.1:9223",
        },
        input_fn=lambda: (_ for _ in ()).throw(AssertionError("no confirmation expected")),
        print_fn=lambda msg: prints.append(str(msg)),
        unattended=True,
        notify=False,
        browser_cleanup=BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
        close_managed_browser_fn=MagicMock(return_value={"closed": True}),
    )
    assert result["outcome"] == "completed"
    assert any("Starting trial 1 of 2" in p for p in prints)
    assert any("No interaction is required." in p for p in prints)
    assert any("Trial 1 completed:" in p for p in prints)


def test_authentication_waits_indefinitely_and_does_not_fail_trial(tmp_path: Path):
    notifies: list[tuple[str, str]] = []
    states = {
        "n": 0,
        "waiting": [],
    }

    def verify(**_k):
        states["n"] += 1
        if states["n"] == 1:
            return {
                "ok": False,
                "authentication_state": "SIGNED_OUT",
                "outcome": "initial_not_signed_in",
                "message": "signed out",
            }
        return {"ok": True, "authentication_state": "SIGNED_IN", "verify_payload": {}}

    def on_waiting(state):
        states["waiting"].append(dict(state))

    with patch(
        "mighty.provider_runtime.verify_amex_signed_in_for_experiment",
        side_effect=verify,
    ):
        result = ensure_expiration_campaign_signed_in(
            trial_number=2,
            trial_total=3,
            base_url="http://127.0.0.1:8765",
            request_json_fn=MagicMock(),
            input_fn=lambda: None,
            print_fn=lambda *_a, **_k: None,
            bring_to_foreground_fn=MagicMock(),
            unattended=True,
            notify=True,
            notify_fn=lambda title, body: notifies.append((title, body)),
            auth_reminder_seconds=None,
            on_waiting_state_fn=on_waiting,
        )
    assert result["ok"] is True
    assert result["prompt_count"] == 1
    assert result.get("interrupted") is not True
    assert any("authentication required" in t.lower() for t, _ in notifies)
    assert states["waiting"]
    assert states["waiting"][0]["status"] == CAMPAIGN_STATUS_WAITING_FOR_AUTHENTICATION


def test_notification_failure_is_nonfatal(tmp_path: Path):
    def boom(_title, _body):
        raise RuntimeError("osascript failed")

    with patch(
        "mighty.provider_runtime.verify_amex_signed_in_for_experiment",
        side_effect=[
            {
                "ok": False,
                "authentication_state": "SIGNED_OUT",
                "outcome": "initial_not_signed_in",
                "message": "signed out",
            },
            {"ok": True, "authentication_state": "SIGNED_IN", "verify_payload": {}},
        ],
    ):
        result = ensure_expiration_campaign_signed_in(
            trial_number=1,
            trial_total=1,
            base_url="http://127.0.0.1:8765",
            request_json_fn=MagicMock(),
            input_fn=lambda: None,
            print_fn=lambda *_a, **_k: None,
            unattended=True,
            notify=True,
            notify_fn=boom,
            auth_reminder_seconds=None,
        )
    assert result["ok"] is True


def test_reminder_notification_cadence_and_stop_after_auth():
    reminders: list[int] = []
    clock = {"t": 0.0}
    waits = {"n": 0}

    def stdin_wait(timeout):
        waits["n"] += 1
        clock["t"] += float(timeout)
        # Allow two reminder cycles, then Enter.
        return waits["n"] >= 3

    wait_for_enter_with_reminders(
        input_fn=lambda: None,
        sleep_fn=lambda _s: None,
        monotonic_fn=lambda: clock["t"],
        reminder_seconds=10.0,
        on_reminder_fn=lambda: reminders.append(waits["n"]),
        stdin_wait_fn=stdin_wait,
    )
    assert len(reminders) == 2


def test_successful_authentication_stops_reminders():
    reminders = {"n": 0}
    clock = {"t": 0.0}
    waits = {"n": 0}

    def stdin_wait(timeout):
        waits["n"] += 1
        clock["t"] += float(timeout)
        return waits["n"] >= 2

    wait_for_enter_with_reminders(
        input_fn=lambda: None,
        sleep_fn=lambda _s: None,
        monotonic_fn=lambda: clock["t"],
        reminder_seconds=10.0,
        on_reminder_fn=lambda: reminders.__setitem__("n", reminders["n"] + 1),
        stdin_wait_fn=stdin_wait,
    )
    assert reminders["n"] == 1


def test_notification_on_campaign_completion(tmp_path: Path):
    notifies: list[tuple[str, str]] = []
    http = _Http(["SIGNED_IN"])
    result = run_amex_expiration_campaign(
        root=tmp_path,
        output_dir=tmp_path / "done",
        trials=["SESSION_API:30"],
        request_json_fn=http,
        run_experiment_fn=_fake_experiment,
        ensure_managed_browser_fn=_browser_ensure(),
        input_fn=lambda: None,
        print_fn=lambda *_a, **_k: None,
        unattended=True,
        notify=True,
        notify_fn=lambda title, body: notifies.append((title, body)),
        browser_cleanup=BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
        close_managed_browser_fn=MagicMock(return_value={"closed": True}),
    )
    assert result["outcome"] == "completed"
    assert any(title == "Mighty Amex campaign completed" for title, _ in notifies)


def test_trial_progress_heartbeat(tmp_path: Path):
    prints: list[str] = []
    http = _Http(["SIGNED_IN"])
    stop_events = []

    def fake_heartbeat(**kwargs):
        stop_events.append(kwargs["stop_event"])
        kwargs["print_fn"]("Trial 1 running — elapsed 1m 0s.")
        return MagicMock()

    result = run_amex_expiration_campaign(
        root=tmp_path,
        output_dir=tmp_path / "hb",
        trials=["SESSION_API:30"],
        request_json_fn=http,
        run_experiment_fn=_fake_experiment,
        ensure_managed_browser_fn=_browser_ensure(),
        input_fn=lambda: None,
        print_fn=lambda msg: prints.append(str(msg)),
        unattended=True,
        notify=False,
        start_heartbeat_fn=fake_heartbeat,
        trial_heartbeat_seconds=60,
        browser_cleanup=BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
        close_managed_browser_fn=MagicMock(return_value={"closed": True}),
    )
    assert result["ok"] is True
    assert any("elapsed 1m 0s" in p for p in prints)
    assert stop_events and stop_events[0].is_set()


def test_owned_caffeinate_starts_and_stops(tmp_path: Path):
    process = MagicMock()
    process.poll.return_value = None
    start = MagicMock(return_value=process)
    stop = MagicMock(return_value={"ok": True, "stopped": True})
    result = run_amex_provider_campaign(
        root=tmp_path,
        trials=["SESSION_API:30"],
        prevent_sleep=True,
        ensure_runtime_fn=lambda **_k: {
            "ok": True,
            "runtime_preexisting": True,
            "runtime_started_by_campaign": False,
            "process": None,
        },
        stop_runtime_fn=MagicMock(),
        run_campaign_fn=lambda **_k: {
            "ok": True,
            "outcome": "completed",
            "exit_code": 0,
            "trial_summaries": [],
            "zip_path": str(tmp_path / "x.zip"),
            "campaign_dir": str(tmp_path),
        },
        start_caffeinate_fn=start,
        stop_caffeinate_fn=stop,
        print_fn=lambda *_a, **_k: None,
    )
    assert result["caffeinate_started_by_campaign"] is True
    start.assert_called_once()
    stop.assert_called_once_with(process)


def test_unrelated_caffeinate_is_never_terminated():
    source = "\n".join(
        [
            inspect.getsource(start_owned_caffeinate),
            inspect.getsource(stop_owned_caffeinate),
            inspect.getsource(run_amex_provider_campaign),
        ]
    )
    assert "pkill" not in source
    assert "killall" not in source
    assert "pgrep" not in source
    assert "caffeinate" in source


def test_ctrl_c_during_auth_preserves_waiting_state(tmp_path: Path):
    http = _Http(["SIGNED_OUT"])

    def boom_input():
        raise KeyboardInterrupt

    result = run_amex_expiration_campaign(
        root=tmp_path,
        output_dir=tmp_path / "wait",
        trials=["SESSION_API:30", "PAGE_ACTIVITY:30"],
        request_json_fn=http,
        run_experiment_fn=_fake_experiment,
        ensure_managed_browser_fn=_browser_ensure(preexisting=False, launched=True),
        input_fn=boom_input,
        print_fn=lambda *_a, **_k: None,
        unattended=True,
        notify=True,
        notify_fn=lambda *_a, **_k: None,
        browser_cleanup=BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
        close_managed_browser_fn=MagicMock(
            return_value={"closed": False, "reason": "interrupted_leave_open"}
        ),
    )
    assert result["interrupted"] is True
    assert result["exit_code"] == 130
    assert Path(result["zip_path"]).is_file()
    manifest = json.loads(
        (tmp_path / "wait" / "campaign-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["waiting_for_authentication"] is True
    assert manifest["pending_trial_number"] == 1
    assert manifest["pending_strategy"] == "SESSION_API"
    assert result["trial_summaries"] == []


def test_resume_from_waiting_for_authentication(tmp_path: Path):
    campaign_dir = tmp_path / "resume"
    campaign_dir.mkdir(parents=True)
    (campaign_dir / "trials").mkdir()
    manifest = {
        "provider": "amex",
        "started_at": "2026-01-01T00:00:00+00:00",
        "waiting_for_authentication": True,
        "pending_trial_number": 2,
        "pending_strategy": "PAGE_ACTIVITY",
        "waiting_since": "2026-01-01T00:05:00+00:00",
        "authentication_attempt_count": 1,
        "trials": [
            {
                "trial_number": 1,
                "strategy": "SESSION_API",
                "keepalive_interval_seconds": 30,
                "status": "completed",
                "error": None,
                "recorder_outcome": "logged_out",
            }
        ],
    }
    (campaign_dir / "campaign-manifest.json").write_text(
        json.dumps(manifest) + "\n", encoding="utf-8"
    )
    prints: list[str] = []
    http = _Http(["SIGNED_IN"])
    result = run_amex_expiration_campaign(
        root=tmp_path,
        output_dir=campaign_dir,
        trials=["SESSION_API:30", "PAGE_ACTIVITY:30"],
        request_json_fn=http,
        run_experiment_fn=_fake_experiment,
        ensure_managed_browser_fn=_browser_ensure(preexisting=True, launched=False),
        classify_managed_browser_fn=lambda: {
            "state": "HEALTHY",
            "cdp_url": "http://127.0.0.1:9223",
        },
        input_fn=lambda: None,
        print_fn=lambda msg: prints.append(str(msg)),
        skip_completed=True,
        unattended=True,
        notify=False,
        browser_cleanup=BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
        close_managed_browser_fn=MagicMock(
            return_value={"closed": False, "reason": "preexisting_never_closed"}
        ),
    )
    assert any("Resuming from waiting_for_authentication" in p for p in prints)
    assert result["outcome"] == "completed"
    strategies = [row["strategy"] for row in result["trial_summaries"]]
    assert strategies.count("SESSION_API") == 1
    assert "PAGE_ACTIVITY" in strategies


def test_ordinary_chrome_remains_untouched_in_unattended_helpers():
    source = "\n".join(
        [
            inspect.getsource(run_amex_expiration_campaign),
            inspect.getsource(ensure_expiration_campaign_signed_in),
            inspect.getsource(notify_macos_desktop),
            inspect.getsource(start_owned_caffeinate),
        ]
    )
    assert "Library/Application Support/Google/Chrome" not in source


def test_cleanup_happens_only_after_terminal_outcome(tmp_path: Path):
    closer = MagicMock(return_value={"closed": True})
    order: list[str] = []

    def run_experiment(**kwargs):
        order.append("experiment")
        return _fake_experiment(**kwargs)

    def close(**kwargs):
        order.append("cleanup")
        return closer(**kwargs)

    http = _Http(["SIGNED_IN"])
    run_amex_expiration_campaign(
        root=tmp_path,
        output_dir=tmp_path / "order",
        trials=["SESSION_API:30"],
        request_json_fn=http,
        run_experiment_fn=run_experiment,
        ensure_managed_browser_fn=_browser_ensure(),
        input_fn=lambda: None,
        print_fn=lambda *_a, **_k: None,
        unattended=True,
        close_managed_browser_fn=close,
        browser_cleanup=BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
    )
    assert order == ["experiment", "cleanup"]


def test_notify_macos_desktop_nonfatal_on_failure():
    result = notify_macos_desktop(
        "title",
        "body",
        subprocess_run_fn=MagicMock(side_effect=RuntimeError("boom")),
    )
    assert result["ok"] is False
    assert "RuntimeError" in (result.get("error") or "")


def test_format_elapsed_duration():
    assert format_elapsed_duration(0) == "0m 0s"
    assert format_elapsed_duration(65) == "1m 5s"
    assert format_elapsed_duration(3661) == "1h 1m 1s"


def test_auth_required_notification_copy():
    notifies: list[tuple[str, str]] = []
    with patch(
        "mighty.provider_runtime.verify_amex_signed_in_for_experiment",
        side_effect=[
            {
                "ok": False,
                "authentication_state": "SIGNED_OUT",
                "outcome": "initial_not_signed_in",
                "message": "signed out",
            },
            {"ok": True, "authentication_state": "SIGNED_IN", "verify_payload": {}},
        ],
    ):
        ensure_expiration_campaign_signed_in(
            trial_number=2,
            trial_total=3,
            base_url="http://127.0.0.1:8765",
            request_json_fn=MagicMock(),
            input_fn=lambda: None,
            print_fn=lambda *_a, **_k: None,
            unattended=True,
            notify=True,
            notify_fn=lambda title, body: notifies.append((title, body)),
        )
    assert notifies
    assert notifies[0][0] == "Mighty Amex authentication required"
    assert "Trial 2 of 3 is waiting" in notifies[0][1]
