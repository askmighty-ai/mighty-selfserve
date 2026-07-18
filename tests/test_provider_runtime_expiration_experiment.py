"""Tests for the one-command Amex expiration experiment runner."""

from __future__ import annotations

import inspect
import json
import threading
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from mighty.provider_runtime import (
    EXPIRATION_EXPERIMENT_BOOTSTRAP_HINT,
    EXPIRATION_EXPERIMENT_SERVE_HINT,
    create_expiration_experiment_zip,
    find_latest_expiration_experiment_dir,
    open_latest_expiration_experiment,
    parse_args,
    print_expiration_experiment_result,
    run_amex_expiration_experiment,
    verify_amex_signed_in_for_experiment,
    wait_for_keepalive_convergence,
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


def _keepalive_start_ok() -> dict:
    return {
        "ok": True,
        "trial_id": "trial-1",
        "keepalive_trial_running": True,
        "keepalive_strategy": "NONE",
        "keepalive_final_reason": None,
        "keepalive_logged_out": False,
        "keepalive_final_authentication_state": None,
    }


def _keepalive_status(
    *,
    running: bool = False,
    reason: str | None = "logged_out",
    auth: str | None = "SIGNED_OUT",
) -> dict:
    return {
        "ok": True,
        "keepalive_trial_running": running,
        "keepalive_trial_id": "trial-1",
        "keepalive_strategy": "NONE",
        "keepalive_final_reason": reason,
        "keepalive_logged_out": reason == "logged_out",
        "keepalive_final_authentication_state": auth,
    }


def _recorder_payload(tmp_path: Path, *, outcome: str = "logged_out") -> dict:
    recorder_dir = tmp_path / "recorder"
    recorder_dir.mkdir(parents=True, exist_ok=True)
    recording = recorder_dir / "recording.json"
    recording.write_text(json.dumps({"ok": True, "outcome": outcome}) + "\n", encoding="utf-8")
    (recorder_dir / "screenshots").mkdir(exist_ok=True)
    (recorder_dir / "screenshots" / "0001.png").write_bytes(b"png")
    return {
        "ok": True,
        "outcome": outcome,
        "output_dir": str(recorder_dir),
        "recording_json": str(recording),
        "final_canonical_authentication_state": (
            "SIGNED_OUT" if outcome == "logged_out" else "SIGNED_IN"
        ),
        "final_authentication_state": (
            "SIGNED_OUT" if outcome == "logged_out" else "SIGNED_IN"
        ),
    }


class _FakeHttp:
    """Route-based request_json double for orchestration tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.verify_states: list[str] = ["SIGNED_IN"]
        self.recorder_block = threading.Event()
        self.recorder_release = threading.Event()
        self.recorder_started = threading.Event()
        self.keepalive_started = threading.Event()
        self.keepalive_stopped = threading.Event()
        self.keepalive_naturally_finished = threading.Event()
        self.keepalive_status_during_recorder: list[dict] = []
        self.recorder_outcome = "logged_out"
        self.recorder_raises: Exception | None = None
        self.health_raises: Exception | None = None
        self.lock_probe: MagicMock | None = None
        self._verify_index = 0
        # After recorder release, converge on this many post-recorder status polls.
        # 1 = first post-recorder status already finished (immediate convergence).
        self.keepalive_converge_after_status_polls = 1
        self.keepalive_never_converge = False
        self._post_recorder_status_polls = 0

    def _keepalive_running(self) -> bool:
        if self.keepalive_naturally_finished.is_set():
            return False
        return self.keepalive_started.is_set() and not self.keepalive_stopped.is_set()

    def _maybe_converge_keepalive(self) -> None:
        if self.keepalive_never_converge:
            return
        if not self.recorder_release.is_set() or self.recorder_raises is not None:
            return
        if self.keepalive_stopped.is_set() or self.keepalive_naturally_finished.is_set():
            return
        self._post_recorder_status_polls += 1
        if self._post_recorder_status_polls >= self.keepalive_converge_after_status_polls:
            self.keepalive_naturally_finished.set()

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
        if self.lock_probe is not None:
            # Orchestration must never touch the runtime lock.
            assert self.lock_probe.acquire.call_count == 0
            assert self.lock_probe.__enter__.call_count == 0

        if path in {"/health", "/status", "/"}:
            if self.health_raises is not None and path == "/health":
                raise self.health_raises
            return {
                "ok": True,
                "runtime_pid": 1,
                "chrome_running": True,
                "cdp_url": "http://127.0.0.1:9223",
                "keepalive_trial_running": self._keepalive_running(),
            }

        if path == "/providers/amex/verify":
            state = self.verify_states[
                min(self._verify_index, len(self.verify_states) - 1)
            ]
            self._verify_index += 1
            return _verify_payload(state)

        if path == "/providers/amex/keepalive/start":
            assert payload is not None
            assert payload.get("strategy") == "NONE"
            self.keepalive_started.set()
            return _keepalive_start_ok()

        if path == "/providers/amex/keepalive/status":
            if self.recorder_release.is_set() and self.recorder_raises is None:
                self._maybe_converge_keepalive()
            running = self._keepalive_running()
            naturally_done = self.keepalive_naturally_finished.is_set()
            status = _keepalive_status(
                running=running,
                reason=(
                    "manually_stopped"
                    if self.keepalive_stopped.is_set()
                    else ("logged_out" if naturally_done else None)
                ),
                auth="SIGNED_OUT" if naturally_done else "SIGNED_IN",
            )
            if self.recorder_started.is_set() and running:
                self.keepalive_status_during_recorder.append(status)
            return status

        if path == "/providers/amex/keepalive/stop":
            self.keepalive_stopped.set()
            return _keepalive_status(running=False, reason="manually_stopped", auth="SIGNED_IN")

        if path == "/providers/amex/diagnostics/browser-record-expiration":
            self.recorder_started.set()
            assert self.keepalive_started.is_set(), "keepalive must start before recorder"
            assert payload is not None
            out = Path(str(payload["output_dir"]))
            out.mkdir(parents=True, exist_ok=True)
            # Allow the test to observe concurrency while the recorder "runs".
            self.recorder_block.set()
            self.recorder_release.wait(timeout=5)
            if self.recorder_raises is not None:
                raise self.recorder_raises
            return _recorder_payload(
                out.parent if out.name == "recorder" else out,
                outcome=self.recorder_outcome,
            )

        raise AssertionError(f"unexpected request {method} {path}")


def _release_recorder(http: _FakeHttp) -> threading.Thread:
    def release_when_ready() -> None:
        assert http.recorder_block.wait(timeout=2)
        http.recorder_release.set()

    helper = threading.Thread(target=release_when_ready, daemon=True)
    helper.start()
    return helper


def test_runtime_unavailable(tmp_path: Path, capsys):
    http = _FakeHttp()
    http.health_raises = URLError("connection refused")
    result = run_amex_expiration_experiment(
        diagnostics_dir=tmp_path,
        request_json_fn=http,
    )
    assert result["outcome"] == "runtime_unavailable"
    assert result["exit_code"] == 1
    assert result["zip_path"] is None
    print_expiration_experiment_result(result)
    err = capsys.readouterr().err
    assert EXPIRATION_EXPERIMENT_SERVE_HINT in err
    assert ("POST", "/providers/amex/keepalive/start") not in http.calls


def test_initial_signed_out_instructs_bootstrap(tmp_path: Path, capsys):
    http = _FakeHttp()
    http.verify_states = ["SIGNED_OUT"]
    result = run_amex_expiration_experiment(
        diagnostics_dir=tmp_path,
        request_json_fn=http,
        sleep_fn=lambda _s: None,
    )
    assert result["outcome"] == "initial_not_signed_in"
    assert result["final_authentication_state"] == "SIGNED_OUT"
    assert result["zip_path"] is None
    print_expiration_experiment_result(result)
    assert EXPIRATION_EXPERIMENT_BOOTSTRAP_HINT in capsys.readouterr().err
    assert ("POST", "/providers/amex/keepalive/start") not in http.calls


def test_initial_login_unknown_retries_then_signed_in(tmp_path: Path):
    http = _FakeHttp()
    http.verify_states = ["LOGIN_UNKNOWN", "LOGIN_UNKNOWN", "SIGNED_IN"]
    sleeps: list[float] = []
    clock = {"t": 0.0}

    def mono() -> float:
        return clock["t"]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["t"] += float(seconds)

    def release_when_ready() -> None:
        assert http.recorder_block.wait(timeout=2)
        http.recorder_release.set()

    helper = threading.Thread(target=release_when_ready, daemon=True)
    helper.start()
    result = run_amex_expiration_experiment(
        diagnostics_dir=tmp_path,
        output_dir=tmp_path / "exp",
        request_json_fn=http,
        sleep_fn=sleep,
        monotonic_fn=mono,
        wait_poll_seconds=0.01,
    )
    helper.join(timeout=2)
    assert result["outcome"] == "logged_out"
    assert len(sleeps) >= 2
    verify_calls = [c for c in http.calls if c == ("POST", "/providers/amex/verify")]
    assert len(verify_calls) >= 3


def test_initial_login_unknown_exhausts_retry(tmp_path: Path):
    http = _FakeHttp()
    http.verify_states = ["LOGIN_UNKNOWN"]
    clock = {"t": 0.0}

    def mono() -> float:
        return clock["t"]

    def sleep(seconds: float) -> None:
        clock["t"] += max(float(seconds), 1.0)

    result = verify_amex_signed_in_for_experiment(
        base_url="http://127.0.0.1:8765",
        request_json_fn=http,
        sleep_fn=sleep,
        monotonic_fn=mono,
        retry_seconds=10,
        retry_interval_seconds=1,
    )
    assert result["ok"] is False
    assert result["outcome"] == "initial_authentication_unknown"
    assert result["authentication_state"] == "LOGIN_UNKNOWN"


def test_successful_orchestration_creates_zip(tmp_path: Path, capsys):
    http = _FakeHttp()
    exp = tmp_path / "amex-expiration-experiment-test"

    def release_when_ready() -> None:
        assert http.recorder_block.wait(timeout=2)
        # Prove keepalive is active while recorder runs.
        status = http(
            "GET",
            "http://127.0.0.1:8765/providers/amex/keepalive/status",
        )
        assert status["keepalive_trial_running"] is True
        http.recorder_release.set()

    helper = threading.Thread(target=release_when_ready, daemon=True)
    helper.start()
    result = run_amex_expiration_experiment(
        diagnostics_dir=tmp_path,
        output_dir=exp,
        request_json_fn=http,
        sleep_fn=lambda _s: None,
        wait_poll_seconds=0.01,
    )
    helper.join(timeout=2)
    assert result["outcome"] == "logged_out"
    assert result["keepalive_outcome"] == "logged_out"
    assert result["final_authentication_state"] == "SIGNED_OUT"
    zip_path = Path(result["zip_path"])
    assert zip_path.is_file()
    assert (exp / "experiment-summary.json").is_file()
    assert (exp / "keepalive-status.json").is_file()
    assert (exp / "recorder" / "recording.json").is_file()
    print_expiration_experiment_result(result)
    out = capsys.readouterr().out
    assert "Recorder:" in out
    assert "logged_out" in out
    assert "Waiting for keepalive convergence..." in out
    assert "Keepalive:" in out
    assert "Evidence ZIP:" in out
    assert str(zip_path) in out
    assert result["keepalive_completion_timeout"] is False
    assert result["summary"]["recorder_completed_at"]
    assert result["summary"]["keepalive_completed_at"]
    assert result["summary"]["recorder_duration_seconds"] is not None
    assert result["summary"]["experiment_duration_seconds"] is not None


def test_keepalive_and_recorder_start_concurrently(tmp_path: Path):
    http = _FakeHttp()
    exp = tmp_path / "concurrent"
    saw_concurrent = threading.Event()

    def release_when_ready() -> None:
        assert http.recorder_block.wait(timeout=2)
        assert http.keepalive_started.is_set()
        assert http.recorder_started.is_set()
        saw_concurrent.set()
        http.recorder_release.set()

    helper = threading.Thread(target=release_when_ready, daemon=True)
    helper.start()
    result = run_amex_expiration_experiment(
        diagnostics_dir=tmp_path,
        output_dir=exp,
        request_json_fn=http,
        sleep_fn=lambda _s: None,
        wait_poll_seconds=0.01,
    )
    helper.join(timeout=2)
    assert saw_concurrent.is_set()
    assert result["outcome"] == "logged_out"
    paths = [path for _method, path in http.calls]
    assert paths.index("/providers/amex/keepalive/start") < paths.index(
        "/providers/amex/diagnostics/browser-record-expiration"
    )


def test_recorder_completion_fetches_keepalive_status(tmp_path: Path):
    http = _FakeHttp()
    exp = tmp_path / "after-recorder"

    def release_when_ready() -> None:
        assert http.recorder_block.wait(timeout=2)
        http.recorder_release.set()

    helper = threading.Thread(target=release_when_ready, daemon=True)
    helper.start()
    result = run_amex_expiration_experiment(
        diagnostics_dir=tmp_path,
        output_dir=exp,
        request_json_fn=http,
        sleep_fn=lambda _s: None,
        wait_poll_seconds=0.01,
    )
    helper.join(timeout=2)
    assert result["outcome"] == "logged_out"
    status_calls = [
        idx
        for idx, call in enumerate(http.calls)
        if call == ("GET", "/providers/amex/keepalive/status")
    ]
    recorder_idx = http.calls.index(
        ("POST", "/providers/amex/diagnostics/browser-record-expiration")
    )
    assert any(idx > recorder_idx for idx in status_calls)
    saved = json.loads((exp / "keepalive-status.json").read_text(encoding="utf-8"))
    assert saved["ok"] is True


def test_early_recorder_failure_still_creates_zip(tmp_path: Path):
    http = _FakeHttp()
    http.recorder_raises = RuntimeError("recorder boom")
    exp = tmp_path / "early-fail"

    def release_when_ready() -> None:
        assert http.recorder_block.wait(timeout=2)
        http.recorder_release.set()

    helper = threading.Thread(target=release_when_ready, daemon=True)
    helper.start()
    result = run_amex_expiration_experiment(
        diagnostics_dir=tmp_path,
        output_dir=exp,
        request_json_fn=http,
        sleep_fn=lambda _s: None,
        wait_poll_seconds=0.01,
    )
    helper.join(timeout=2)
    assert result["outcome"] == "fatal_error"
    assert Path(result["zip_path"]).is_file()
    assert (exp / "experiment-summary.json").is_file()
    assert (exp / "keepalive-status.json").is_file()
    # Early failure should stop the still-running NONE trial via safe API.
    assert ("POST", "/providers/amex/keepalive/stop") in http.calls


def test_ctrl_c_creates_partial_zip(tmp_path: Path):
    http = _FakeHttp()
    exp = tmp_path / "interrupted"
    (exp / "recorder" / "screenshots").mkdir(parents=True)
    (exp / "recorder" / "screenshots" / "partial.png").write_bytes(b"png")

    def hold_recorder() -> None:
        assert http.recorder_block.wait(timeout=2)

    helper = threading.Thread(target=hold_recorder, daemon=True)
    helper.start()
    real_event_wait = threading.Event.wait

    def selective_wait(self, timeout=None):  # noqa: ANN001
        # Interrupt only the orchestration poll (short timeout) once recorder runs.
        if http.recorder_started.is_set() and timeout is not None and timeout <= 0.05:
            raise KeyboardInterrupt
        return real_event_wait(self, timeout)

    with patch.object(threading.Event, "wait", selective_wait):
        result = run_amex_expiration_experiment(
            diagnostics_dir=tmp_path,
            output_dir=exp,
            request_json_fn=http,
            sleep_fn=lambda _s: None,
            wait_poll_seconds=0.01,
        )
    http.recorder_release.set()
    helper.join(timeout=2)
    assert result["outcome"] == "interrupted"
    assert result["exit_code"] == 130
    assert Path(result["zip_path"]).is_file()
    summary = json.loads((exp / "experiment-summary.json").read_text(encoding="utf-8"))
    assert summary["outcome"] == "interrupted"
    assert summary["interrupted"] is True
    with zipfile.ZipFile(result["zip_path"]) as zf:
        names = set(zf.namelist())
    assert "experiment-summary.json" in names
    assert "keepalive-status.json" in names
    assert any(name.startswith("recorder/") for name in names)


def test_zip_contains_expected_files(tmp_path: Path):
    exp = tmp_path / "amex-expiration-experiment-zipcheck"
    (exp / "recorder" / "screenshots").mkdir(parents=True)
    (exp / "experiment-summary.json").write_text("{}", encoding="utf-8")
    (exp / "keepalive-status.json").write_text("{}", encoding="utf-8")
    (exp / "runtime-status.json").write_text("{}", encoding="utf-8")
    (exp / "recorder" / "recording.json").write_text("{}", encoding="utf-8")
    (exp / "recorder" / "screenshots" / "0001.png").write_bytes(b"png")
    zip_path = create_expiration_experiment_zip(exp)
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert "experiment-summary.json" in names
    assert "keepalive-status.json" in names
    assert "runtime-status.json" in names
    assert "recorder/recording.json" in names
    assert "recorder/screenshots/0001.png" in names
    assert zip_path.name not in names


def test_no_runtime_lock_held_while_waiting(tmp_path: Path):
    http = _FakeHttp()
    lock = MagicMock()
    lock.acquire.return_value = True
    http.lock_probe = lock
    exp = tmp_path / "no-lock"

    def release_when_ready() -> None:
        assert http.recorder_block.wait(timeout=2)
        http.recorder_release.set()

    helper = threading.Thread(target=release_when_ready, daemon=True)
    helper.start()
    result = run_amex_expiration_experiment(
        diagnostics_dir=tmp_path,
        output_dir=exp,
        request_json_fn=http,
        sleep_fn=lambda _s: None,
        wait_poll_seconds=0.01,
    )
    helper.join(timeout=2)
    assert result["outcome"] == "logged_out"
    source = inspect.getsource(run_amex_expiration_experiment)
    assert "self.lock" not in source
    assert "runtime.lock" not in source


def test_no_page_mutation_or_evaluate_in_orchestration():
    source = inspect.getsource(run_amex_expiration_experiment)
    for banned in (
        "page.click",
        "page.goto",
        "page.reload",
        "page.type",
        "page.fill",
        "page.evaluate",
        "frame.evaluate",
        "perform_keepalive_action",
        "sync_playwright",
    ):
        assert banned not in source


def test_finder_open_selects_latest_experiment(tmp_path: Path):
    older = tmp_path / "amex-expiration-experiment-20260101T000000Z"
    newer = tmp_path / "amex-expiration-experiment-20260102T000000Z"
    older.mkdir()
    newer.mkdir()
    opened: list[Path] = []
    result = open_latest_expiration_experiment(
        tmp_path,
        open_fn=lambda path: opened.append(Path(path)),
    )
    assert result["ok"] is True
    assert opened == [newer]
    assert find_latest_expiration_experiment_dir(tmp_path) == newer


def test_finder_open_when_none_exist(tmp_path: Path, capsys):
    result = open_latest_expiration_experiment(tmp_path)
    assert result["ok"] is False
    assert "No Amex expiration experiment" in result["message"]


def test_cli_registers_expiration_experiment_commands():
    with patch("sys.argv", ["provider_runtime.py", "browser-run-expiration-experiment", "amex"]):
        args = parse_args()
    assert args.command == "browser-run-expiration-experiment"
    assert args.provider == "amex"
    assert args.trial_duration_seconds == 600
    assert args.keepalive_interval_seconds == 30
    assert args.recording_timeout_seconds == 900
    assert args.evidence_interval_seconds == 1
    assert args.verification_interval_seconds == 5
    assert args.rolling_window_seconds == 90
    assert args.screenshot_every_seconds == 1

    with patch(
        "sys.argv",
        ["provider_runtime.py", "browser-open-latest-expiration-experiment", "amex"],
    ):
        args = parse_args()
    assert args.command == "browser-open-latest-expiration-experiment"


def test_login_unknown_retry_then_fails_before_keepalive(tmp_path: Path):
    http = _FakeHttp()
    http.verify_states = ["LOGIN_UNKNOWN"]
    clock = {"t": 0.0}

    result = run_amex_expiration_experiment(
        diagnostics_dir=tmp_path,
        request_json_fn=http,
        sleep_fn=lambda s: clock.__setitem__("t", clock["t"] + max(float(s), 1.0)),
        monotonic_fn=lambda: clock["t"],
    )
    assert result["outcome"] == "initial_authentication_unknown"
    assert ("POST", "/providers/amex/keepalive/start") not in http.calls
    assert result["zip_path"] is None


def test_recorder_logs_out_immediately_waits_for_keepalive(tmp_path: Path):
    http = _FakeHttp()
    http.keepalive_converge_after_status_polls = 1
    exp = tmp_path / "immediate-logout"
    helper = _release_recorder(http)
    result = run_amex_expiration_experiment(
        diagnostics_dir=tmp_path,
        output_dir=exp,
        request_json_fn=http,
        sleep_fn=lambda _s: None,
        wait_poll_seconds=0.01,
        keepalive_convergence_poll_seconds=1,
    )
    helper.join(timeout=2)
    assert result["outcome"] == "logged_out"
    assert result["keepalive_outcome"] == "logged_out"
    assert result["keepalive_completion_timeout"] is False
    assert result["keepalive_completed_at"] is not None
    assert http.keepalive_naturally_finished.is_set()


def test_keepalive_finishes_within_wait_window(tmp_path: Path, capsys):
    http = _FakeHttp()
    # Finish on the 4th post-recorder status poll → ~3s with 1s poll spacing.
    http.keepalive_converge_after_status_polls = 4
    exp = tmp_path / "converge-window"
    clock = {"t": 0.0}
    helper = _release_recorder(http)

    def sleep(seconds: float) -> None:
        clock["t"] += float(seconds)

    result = run_amex_expiration_experiment(
        diagnostics_dir=tmp_path,
        output_dir=exp,
        request_json_fn=http,
        sleep_fn=sleep,
        monotonic_fn=lambda: clock["t"],
        wait_poll_seconds=0.01,
        keepalive_convergence_timeout_seconds=15,
        keepalive_convergence_poll_seconds=1,
    )
    helper.join(timeout=2)
    assert result["outcome"] == "logged_out"
    assert result["keepalive_outcome"] == "logged_out"
    assert result["keepalive_completion_timeout"] is False
    wait_seconds = float(result["keepalive_wait_seconds"])
    assert 2.9 <= wait_seconds <= 3.2
    print_expiration_experiment_result(result)
    out = capsys.readouterr().out
    assert "finished after 3.0 seconds" in out
    # Expose measured wait for the run report.
    result["_measured_keepalive_wait_seconds"] = wait_seconds


def test_keepalive_timeout_after_15_seconds(tmp_path: Path, capsys):
    http = _FakeHttp()
    http.keepalive_never_converge = True
    exp = tmp_path / "keepalive-timeout"
    clock = {"t": 0.0}
    helper = _release_recorder(http)

    def sleep(seconds: float) -> None:
        clock["t"] += float(seconds)

    result = run_amex_expiration_experiment(
        diagnostics_dir=tmp_path,
        output_dir=exp,
        request_json_fn=http,
        sleep_fn=sleep,
        monotonic_fn=lambda: clock["t"],
        wait_poll_seconds=0.01,
        keepalive_convergence_timeout_seconds=15,
        keepalive_convergence_poll_seconds=1,
    )
    helper.join(timeout=2)
    assert result["outcome"] == "logged_out"
    assert result["keepalive_outcome"] == "still_running"
    assert result["keepalive_completion_timeout"] is True
    assert result["keepalive_completed_at"] is None
    assert float(result["keepalive_wait_seconds"]) >= 15.0
    saved = json.loads((exp / "keepalive-status.json").read_text(encoding="utf-8"))
    assert saved["keepalive_trial_running"] is True
    summary = json.loads((exp / "experiment-summary.json").read_text(encoding="utf-8"))
    assert summary["keepalive_completion_timeout"] is True
    print_expiration_experiment_result(result)
    out = capsys.readouterr().out
    assert "timed out after 15.0 seconds" in out
    assert "still_running" in out


def test_final_zip_contains_converged_keepalive_status(tmp_path: Path):
    http = _FakeHttp()
    http.keepalive_converge_after_status_polls = 2
    exp = tmp_path / "zip-converged"
    clock = {"t": 0.0}
    helper = _release_recorder(http)
    result = run_amex_expiration_experiment(
        diagnostics_dir=tmp_path,
        output_dir=exp,
        request_json_fn=http,
        sleep_fn=lambda s: clock.__setitem__("t", clock["t"] + float(s)),
        monotonic_fn=lambda: clock["t"],
        wait_poll_seconds=0.01,
        keepalive_convergence_poll_seconds=1,
    )
    helper.join(timeout=2)
    assert result["keepalive_outcome"] == "logged_out"
    with zipfile.ZipFile(result["zip_path"]) as zf:
        status = json.loads(zf.read("keepalive-status.json"))
        summary = json.loads(zf.read("experiment-summary.json"))
    assert status["keepalive_trial_running"] is False
    assert status["keepalive_final_reason"] == "logged_out"
    assert summary["keepalive_outcome"] == "logged_out"
    assert summary["keepalive_completion_timeout"] is False


def test_summary_timestamps_are_populated(tmp_path: Path):
    http = _FakeHttp()
    exp = tmp_path / "timestamps"
    helper = _release_recorder(http)
    result = run_amex_expiration_experiment(
        diagnostics_dir=tmp_path,
        output_dir=exp,
        request_json_fn=http,
        sleep_fn=lambda _s: None,
        wait_poll_seconds=0.01,
    )
    helper.join(timeout=2)
    summary = json.loads((exp / "experiment-summary.json").read_text(encoding="utf-8"))
    for key in (
        "recorder_completed_at",
        "keepalive_completed_at",
        "keepalive_wait_seconds",
        "keepalive_completion_timeout",
        "recorder_duration_seconds",
        "experiment_duration_seconds",
        "started_at",
        "completed_at",
    ):
        assert key in summary
        assert summary[key] is not None or key == "keepalive_completion_timeout"
    assert summary["keepalive_completion_timeout"] is False
    assert isinstance(summary["keepalive_wait_seconds"], (int, float))
    assert isinstance(summary["recorder_duration_seconds"], (int, float))
    assert isinstance(summary["experiment_duration_seconds"], (int, float))
    assert result["summary"]["recorder_completed_at"] == summary["recorder_completed_at"]


def test_recorder_outcome_timeout_skips_keepalive_wait(tmp_path: Path):
    http = _FakeHttp()
    http.recorder_outcome = "timeout"
    http.keepalive_never_converge = True
    exp = tmp_path / "recorder-timeout"
    clock = {"t": 0.0}
    helper = _release_recorder(http)

    def sleep(seconds: float) -> None:
        clock["t"] += float(seconds)

    result = run_amex_expiration_experiment(
        diagnostics_dir=tmp_path,
        output_dir=exp,
        request_json_fn=http,
        sleep_fn=sleep,
        monotonic_fn=lambda: clock["t"],
        wait_poll_seconds=0.01,
        keepalive_convergence_timeout_seconds=15,
        keepalive_convergence_poll_seconds=1,
    )
    helper.join(timeout=2)
    assert result["outcome"] == "timeout"
    assert result["keepalive_completion_timeout"] is False
    assert float(result["keepalive_wait_seconds"]) == 0.0
    assert result["keepalive_completed_at"] is None
    # Must not burn the 15s convergence budget when recorder times out.
    assert clock["t"] < 2.0
    status_calls = [
        call for call in http.calls if call == ("GET", "/providers/amex/keepalive/status")
    ]
    assert len(status_calls) == 1


def test_recorder_initial_not_signed_in_skips_wait(tmp_path: Path):
    # initial_not_signed_in is a pre-trial verify failure; no keepalive/recorder.
    http = _FakeHttp()
    http.verify_states = ["SIGNED_OUT"]
    result = run_amex_expiration_experiment(
        diagnostics_dir=tmp_path,
        request_json_fn=http,
        sleep_fn=lambda _s: None,
    )
    assert result["outcome"] == "initial_not_signed_in"
    assert result["zip_path"] is None
    assert ("POST", "/providers/amex/keepalive/start") not in http.calls
    assert ("GET", "/providers/amex/keepalive/status") not in http.calls


def test_wait_for_keepalive_convergence_helper_timeout():
    calls = {"n": 0}
    clock = {"t": 0.0}

    def request_json(method: str, url: str, payload=None, **kwargs):  # noqa: ANN001
        calls["n"] += 1
        return _keepalive_status(running=True, reason=None, auth="SIGNED_IN")

    result = wait_for_keepalive_convergence(
        base_url="http://127.0.0.1:8765",
        request_json_fn=request_json,
        sleep_fn=lambda s: clock.__setitem__("t", clock["t"] + float(s)),
        monotonic_fn=lambda: clock["t"],
        timeout_seconds=15,
        poll_seconds=1,
    )
    assert result["timed_out"] is True
    assert result["completed_at"] is None
    assert float(result["wait_seconds"]) >= 15.0
    assert calls["n"] >= 15
