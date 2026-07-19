"""Tests for the first-class ``campaign amex`` Provider Runtime command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

from mighty.provider_runtime import (
    DEFAULT_AMEX_CAMPAIGN_NAME,
    DEFAULT_AMEX_CAMPAIGN_TRIALS,
    check_provider_runtime_health,
    default_amex_campaign_trials,
    ensure_provider_runtime_for_campaign,
    parse_args,
    resolve_amex_campaign_trials,
    run_amex_expiration_campaign,
    run_amex_provider_campaign,
)


class _HealthHttp:
    def __init__(self, *, healthy_after: int = 0) -> None:
        self.calls: list[tuple[str, str]] = []
        self._healthy_after = int(healthy_after)
        self._health_count = 0
        self.shutdown_calls = 0

    def __call__(
        self,
        method: str,
        url: str,
        payload: dict | None = None,
        *,
        timeout: float = 60,
    ) -> dict:
        from urllib.parse import urlsplit

        path = urlsplit(url).path or url
        self.calls.append((method, path))
        if path == "/health":
            self._health_count += 1
            if self._health_count <= self._healthy_after:
                raise URLError("connection refused")
            return {"ok": True, "runtime_pid": 99}
        if path == "/shutdown":
            self.shutdown_calls += 1
            return {"ok": True}
        raise AssertionError(f"unexpected {method} {path}")


def test_default_campaign_expansion():
    assert resolve_amex_campaign_trials(None) == list(DEFAULT_AMEX_CAMPAIGN_TRIALS)
    assert resolve_amex_campaign_trials([]) == list(DEFAULT_AMEX_CAMPAIGN_TRIALS)
    assert default_amex_campaign_trials() == [
        "NONE:30",
        "SESSION_API:30",
        "SESSION_API:5",
        "PAGE_ACTIVITY:30",
        "OVERVIEW_RELOAD:30",
    ]


def test_custom_trial_override():
    assert resolve_amex_campaign_trials(["NONE:10", "SESSION_API:5"]) == [
        "NONE:10",
        "SESSION_API:5",
    ]


def test_cli_registers_campaign_with_optional_trials():
    with patch("sys.argv", ["provider_runtime.py", "campaign", "amex"]):
        args = parse_args()
    assert args.command == "campaign"
    assert args.provider == "amex"
    assert args.trials is None

    with patch(
        "sys.argv",
        [
            "provider_runtime.py",
            "campaign",
            "amex",
            "--trial",
            "NONE:10",
            "--trial",
            "SESSION_API:5",
        ],
    ):
        args = parse_args()
    assert args.trials == ["NONE:10", "SESSION_API:5"]


def test_runtime_already_running_is_reused(tmp_path: Path):
    http = _HealthHttp(healthy_after=0)
    start = MagicMock()
    stop = MagicMock()
    campaign = MagicMock(
        return_value={
            "ok": True,
            "outcome": "completed",
            "exit_code": 0,
            "zip_path": str(tmp_path / "out.zip"),
            "trial_summaries": [{"strategy": "NONE"}],
            "campaign_name": DEFAULT_AMEX_CAMPAIGN_NAME,
            "interrupted": False,
        }
    )

    result = run_amex_provider_campaign(
        root=tmp_path,
        request_json_fn=http,
        ensure_runtime_fn=lambda **_k: {
            "ok": True,
            "runtime_preexisting": True,
            "runtime_started_by_campaign": False,
            "process": None,
        },
        stop_runtime_fn=stop,
        run_campaign_fn=campaign,
        print_fn=lambda *_a, **_k: None,
        input_fn=lambda: "",
    )
    assert result["runtime_preexisting"] is True
    assert result["runtime_started_by_campaign"] is False
    assert result["runtime_stopped_by_campaign"] is False
    start.assert_not_called()
    stop.assert_not_called()
    campaign.assert_called_once()
    assert campaign.call_args.kwargs["trials"] == list(DEFAULT_AMEX_CAMPAIGN_TRIALS)


def test_runtime_auto_start_and_auto_stop_when_owned(tmp_path: Path):
    http = _HealthHttp(healthy_after=2)
    process = MagicMock()
    start = MagicMock(return_value=process)
    stop = MagicMock(return_value={"ok": True})
    campaign = MagicMock(
        return_value={
            "ok": True,
            "outcome": "completed",
            "exit_code": 0,
            "zip_path": str(tmp_path / "owned.zip"),
            "trial_summaries": [],
            "interrupted": False,
        }
    )

    ensure = ensure_provider_runtime_for_campaign(
        root=tmp_path,
        request_json_fn=http,
        start_runtime_fn=start,
        sleep_fn=lambda _s: None,
        monotonic_fn=lambda: http._health_count * 0.1,
        print_fn=lambda *_a, **_k: None,
    )
    assert ensure["ok"] is True
    assert ensure["runtime_started_by_campaign"] is True
    start.assert_called_once()

    result = run_amex_provider_campaign(
        root=tmp_path,
        request_json_fn=http,
        ensure_runtime_fn=lambda **_k: ensure,
        stop_runtime_fn=stop,
        run_campaign_fn=campaign,
        print_fn=lambda *_a, **_k: None,
        input_fn=lambda: "",
    )
    assert result["runtime_started_by_campaign"] is True
    assert result["runtime_stopped_by_campaign"] is True
    stop.assert_called_once()
    assert stop.call_args.kwargs["process"] is process


def test_runtime_left_running_when_preexisting(tmp_path: Path, capsys):
    stop = MagicMock()
    campaign = MagicMock(
        return_value={
            "ok": True,
            "outcome": "completed",
            "exit_code": 0,
            "zip_path": str(tmp_path / "pre.zip"),
            "trial_summaries": [],
            "interrupted": False,
        }
    )
    result = run_amex_provider_campaign(
        root=tmp_path,
        ensure_runtime_fn=lambda **_k: {
            "ok": True,
            "runtime_preexisting": True,
            "runtime_started_by_campaign": False,
            "process": None,
        },
        stop_runtime_fn=stop,
        run_campaign_fn=campaign,
        print_fn=print,
        input_fn=lambda: "",
    )
    out = capsys.readouterr().out
    assert "Leaving preexisting Provider Runtime running." in out
    assert result["runtime_stopped_by_campaign"] is False
    stop.assert_not_called()


def test_failure_during_runtime_startup(tmp_path: Path):
    result = run_amex_provider_campaign(
        root=tmp_path,
        ensure_runtime_fn=lambda **_k: {
            "ok": False,
            "outcome": "runtime_start_failed",
            "message": "boom",
            "error": "boom",
            "runtime_preexisting": False,
            "runtime_started_by_campaign": False,
        },
        run_campaign_fn=MagicMock(side_effect=AssertionError("campaign must not run")),
        print_fn=lambda *_a, **_k: None,
    )
    assert result["ok"] is False
    assert result["outcome"] == "runtime_start_failed"
    assert result["exit_code"] == 1
    assert result["zip_path"] is None


def test_campaign_cancellation_stops_owned_runtime(tmp_path: Path):
    stop = MagicMock(return_value={"ok": True})

    def boom(**_kwargs):
        raise KeyboardInterrupt

    result = run_amex_provider_campaign(
        root=tmp_path,
        ensure_runtime_fn=lambda **_k: {
            "ok": True,
            "runtime_preexisting": False,
            "runtime_started_by_campaign": True,
            "process": MagicMock(),
        },
        stop_runtime_fn=stop,
        run_campaign_fn=boom,
        print_fn=lambda *_a, **_k: None,
    )
    assert result["interrupted"] is True
    assert result["exit_code"] == 130
    assert result["runtime_stopped_by_campaign"] is True
    stop.assert_called_once()


def test_partial_evidence_preserved_on_interrupt(tmp_path: Path):
    stop = MagicMock(return_value={"ok": True})
    zip_path = tmp_path / "partial.zip"
    zip_path.write_bytes(b"zip")

    def partial(**_kwargs):
        return {
            "ok": True,
            "outcome": "interrupted",
            "exit_code": 130,
            "interrupted": True,
            "zip_path": str(zip_path),
            "trial_summaries": [
                {"strategy": "NONE", "status": "completed", "error": None},
                {"strategy": "SESSION_API", "status": "partial", "error": None},
            ],
            "campaign_name": DEFAULT_AMEX_CAMPAIGN_NAME,
        }

    result = run_amex_provider_campaign(
        root=tmp_path,
        ensure_runtime_fn=lambda **_k: {
            "ok": True,
            "runtime_preexisting": False,
            "runtime_started_by_campaign": True,
            "process": MagicMock(),
        },
        stop_runtime_fn=stop,
        run_campaign_fn=partial,
        print_fn=lambda *_a, **_k: None,
    )
    assert result["zip_path"] == str(zip_path)
    assert Path(result["zip_path"]).is_file()
    assert len(result["trial_summaries"]) == 2
    assert result["runtime_stopped_by_campaign"] is True


def test_identical_campaign_output_to_existing_command(tmp_path: Path):
    shared = {
        "ok": True,
        "outcome": "completed",
        "exit_code": 0,
        "interrupted": False,
        "campaign_name": DEFAULT_AMEX_CAMPAIGN_NAME,
        "zip_path": str(tmp_path / "same.zip"),
        "trial_summaries": [
            {
                "trial_number": 1,
                "strategy": "NONE",
                "keepalive_interval_seconds": 30,
                "error": None,
            }
        ],
        "summary": {"trials": [{"strategy": "NONE"}]},
        "managed_browser_preexisting": False,
        "managed_browser_launched_by_campaign": True,
    }
    inner = MagicMock(return_value=dict(shared))

    top = run_amex_provider_campaign(
        root=tmp_path,
        trials=["NONE:30"],
        ensure_runtime_fn=lambda **_k: {
            "ok": True,
            "runtime_preexisting": True,
            "runtime_started_by_campaign": False,
            "process": None,
        },
        stop_runtime_fn=MagicMock(),
        run_campaign_fn=inner,
        print_fn=lambda *_a, **_k: None,
    )
    assert top["zip_path"] == shared["zip_path"]
    assert top["trial_summaries"] == shared["trial_summaries"]
    assert top["summary"] == shared["summary"]
    assert top["managed_browser_launched_by_campaign"] is True
    assert callable(run_amex_expiration_campaign)
    assert inner.called
    # Top-level command reuses the same campaign runner unit of execution.
    import inspect

    source = inspect.getsource(run_amex_provider_campaign)
    assert "run_amex_expiration_campaign" in source


def test_custom_trials_passed_through(tmp_path: Path):
    campaign = MagicMock(
        return_value={
            "ok": True,
            "outcome": "completed",
            "exit_code": 0,
            "zip_path": str(tmp_path / "custom.zip"),
            "trial_summaries": [],
            "interrupted": False,
        }
    )
    run_amex_provider_campaign(
        root=tmp_path,
        trials=["NONE:10"],
        ensure_runtime_fn=lambda **_k: {
            "ok": True,
            "runtime_preexisting": True,
            "runtime_started_by_campaign": False,
            "process": None,
        },
        run_campaign_fn=campaign,
        print_fn=lambda *_a, **_k: None,
    )
    assert campaign.call_args.kwargs["trials"] == ["NONE:10"]
    assert campaign.call_args.kwargs["campaign_name"] == DEFAULT_AMEX_CAMPAIGN_NAME


def test_check_provider_runtime_health_ok_and_down():
    ok_http = _HealthHttp(healthy_after=0)
    assert check_provider_runtime_health(request_json_fn=ok_http)["ok"] is True
    down = _HealthHttp(healthy_after=100)
    assert check_provider_runtime_health(request_json_fn=down)["ok"] is False


def test_ensure_runtime_start_failure_message(tmp_path: Path):
    result = ensure_provider_runtime_for_campaign(
        root=tmp_path,
        request_json_fn=_HealthHttp(healthy_after=100),
        start_runtime_fn=MagicMock(side_effect=RuntimeError("cannot spawn")),
        sleep_fn=lambda _s: None,
        print_fn=lambda *_a, **_k: None,
    )
    assert result["ok"] is False
    assert result["outcome"] == "runtime_start_failed"
    assert "cannot spawn" in result["message"]
