"""Port of the config repository's headless-review bats suite, plus subscription-auth cases.

``claude`` is a stub executable on PATH, the snapshot refresh is a stub unless a test needs
the real collector, and Telegram is a recorder: no real model call or notification can
happen.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
import time
from pathlib import Path

import pytest

from cc_session_tools.lib.self_improvement import prompt, review, state

from .conftest import Env, days_ago, iso_days_ago, tree_checksums

STUB_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
log = os.environ["STUB_LOG"]
seen_env = sorted(k for k in (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_OAUTH_TOKEN",
) if k in os.environ)
if args[:2] == ["auth", "status"]:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write("claude-auth-env: " + json.dumps(seen_env) + "\n")
    method = os.environ.get("STUB_AUTH", "claude.ai")
    if method == "logged-out":
        print(json.dumps({"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"}))
    elif method == "garbage":
        print("not json")
    else:
        provider = "bedrock" if method == "third_party" else "firstParty"
        print(json.dumps({"loggedIn": True, "authMethod": method, "apiProvider": provider}))
    sys.exit(0)
with open(log, "a", encoding="utf-8") as fh:
    fh.write("claude-args: " + json.dumps(args) + "\n")
    fh.write("claude-env: " + json.dumps(seen_env) + "\n")
    fh.write("claude-cwd: " + os.getcwd() + "\n")
with open(os.environ["CAPTURE"], "w", encoding="utf-8") as fh:
    fh.write(sys.stdin.read())
mode = os.environ.get("STUB_CLAUDE", "ok")
today = os.environ["TODAY"]
def line(lever, text):
    return f"- [{today}] [auto-review] {lever}: {text}"
if mode == "new_transcript":
    path = os.path.join(os.environ["HOME"], ".claude/projects/-example-proj/during-run.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user", "entrypoint": "cli", "message": {"role": "user", "content": "DURING-RUN"}}) + "\n")
if mode == "timeout":
    time.sleep(30)
if mode == "ignore_term":
    import signal
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(30)
if mode == "exit1":
    sys.exit(1)
if mode == "garbage":
    print("this is not json")
    sys.exit(0)
if mode == "none":
    result, cost, err = "no findings", 0.05, False
elif mode == "iserror":
    result, cost, err = "boom", 0.01, True
elif mode == "iserror_overcap":
    result, cost, err = "budget exceeded", 0.52, True
elif mode == "overbudget":
    result, cost, err = line("skill", "x keeps failing -> fix skills/x/SKILL.md"), 0.60, False
else:
    out = [
        "Here are my findings:",
        line("skill", "manual shop steps repeated -> extend skills/shop/SKILL.md"),
        line("bogus", "unknown lever -> ignored"),
        line("none", "one-off noise -> dropped"),
        line("hook", "no arrow in this line"),
        line("skill", "manual shop steps repeated -> extend skills/shop/SKILL.md"),
        line("permission", "rg prompts every session -> allow rg in settings.json"),
        "  " + line("hook", "indented line -> ignored"),
    ]
    out += [line("settings", f"friction number {i} -> change setting {i}") for i in range(12)]
    result, cost, err = "\r\n".join(out), 0.12, False
print(json.dumps({"type": "result", "is_error": err, "result": result, "total_cost_usd": cost}))
'''

EXISTING_LINE = (
    f"- [{days_ago(30)}] [auto-review] permission: rg prompts every session -> "
    "allow rg in settings.json"
)


class Harness:
    def __init__(self, si: Env, monkeypatch: pytest.MonkeyPatch) -> None:
        self.si = si
        self.monkeypatch = monkeypatch
        self.bin = si.root / "bin"
        self.bin.mkdir()
        self.log = si.root / "stub.log"
        self.log.write_text("")
        self.capture = si.root / "claude-stdin.txt"
        self.signal_total = 3
        self.refresh_calls = 0
        stub = self.bin / "claude"
        stub.write_text(STUB_CLAUDE.replace("#!/usr/bin/env python3", f"#!{sys.executable}"))
        stub.chmod(0o755)
        monkeypatch.setenv("PATH", f"{self.bin}:{os.environ['PATH']}")
        monkeypatch.setenv("STUB_LOG", str(self.log))
        monkeypatch.setenv("CAPTURE", str(self.capture))
        monkeypatch.setenv("TODAY", days_ago(0))
        monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
        si.write_backlog(1, 2, 1)
        si.backlog.write_text(si.backlog.read_text().replace("## Inbox\n", f"## Inbox\n{EXISTING_LINE}\n"))
        (si.projects / "-example-proj").mkdir(parents=True)
        (si.home / ".claude" / "usage-data" / "facets").mkdir(parents=True)

    def refresh(self) -> None:
        self.refresh_calls += 1
        self.si.state.mkdir(parents=True, exist_ok=True)
        (self.si.state / "signals.env").write_text(
            f"COLLECTED_AT=2026-01-01T00:00:00Z\nSIGNAL_TOTAL={self.signal_total}\nINBOX_COUNT=1\n"
        )

    def run(self, *, force: bool = False, real_refresh: bool = False) -> str:
        return review.run(
            force=force, notify=self.si.notify, refresh=None if real_refresh else self.refresh,
        )

    def stub_lines(self, prefix: str) -> list[str]:
        return [line[len(prefix):] for line in self.log.read_text().splitlines() if line.startswith(prefix)]

    def claude_calls(self) -> int:
        return len(self.stub_lines("claude-args: "))

    def auto_lines(self) -> int:
        return sum(
            1 for line in self.si.backlog.read_text().splitlines()
            if re.match(r"^- \[[0-9-]*\] \[auto-review\]", line)
        )

    def captured(self) -> str:
        return self.capture.read_text(encoding="utf-8")


@pytest.fixture
def h(si: Env, monkeypatch: pytest.MonkeyPatch) -> Harness:
    return Harness(si, monkeypatch)


def write_transcript(path: Path, entry: str = "cli", text: str = "please fix the build") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "user", "entrypoint": entry, "message": {"role": "user", "content": text}},
        {"type": "assistant", "entrypoint": entry, "message": {"role": "assistant", "content": [{"type": "text", "text": "ASSISTANT-TEXT"}]}},
        {"type": "user", "entrypoint": entry, "message": {"role": "user", "content": [{"type": "tool_result", "is_error": True, "content": "Permission denied by hook"}]}},
        {"type": "user", "entrypoint": entry, "message": {"role": "user", "content": [{"type": "tool_result", "is_error": False, "content": "FINE-RESULT"}]}},
        {"type": "user", "isMeta": True, "entrypoint": entry, "message": {"role": "user", "content": "META-TEXT"}},
        {"type": "user", "entrypoint": entry, "message": {"role": "user", "content": "<system-reminder>INJECTED</system-reminder>"}},
    ]
    # Compact separators, as Claude Code writes transcripts: the headless marker is matched
    # textually.
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records))


@pytest.fixture
def transcript(h: Harness) -> Path:
    path = h.si.projects / "-example-proj" / "a.jsonl"
    write_transcript(path)
    return path


def set_env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)


# --- prompt -------------------------------------------------------------------------------


def test_prompt_levers_are_the_review_taxonomy() -> None:
    text = prompt.prompt_text()
    assert prompt.levers(text) == (
        "claude-md", "skill", "hook", "permission", "settings", "mcp-plugin", "none",
    )
    assert "none" not in prompt.actionable_levers(text)


# --- run limit and skip -------------------------------------------------------------------


def test_a_run_within_7_days_of_a_ran_line_is_skipped(h: Harness, transcript: Path) -> None:
    h.si.review_line(f"{iso_days_ago(3)} mode=auto status=ran sessions=2 proposals=1 cost_usd=0.10 duration_s=20")
    assert h.run() == "self-improvement auto-review: skipped (last run 3d ago)"
    assert h.claude_calls() == 0
    assert re.fullmatch(r"\S+ mode=auto status=skipped sessions=0 proposals=0 cost_usd=0 duration_s=\d+", h.si.last_log())


def test_force_bypasses_the_run_limit(h: Harness, transcript: Path) -> None:
    h.si.review_line(f"{iso_days_ago(3)} mode=auto status=ran sessions=2 proposals=1 cost_usd=0.10 duration_s=20")
    h.run(force=True)
    assert h.claude_calls() == 1


def test_a_ran_line_7_days_old_does_not_block(h: Harness, transcript: Path) -> None:
    h.si.review_line(f"{iso_days_ago(7)} mode=auto status=ran sessions=2 proposals=1 cost_usd=0.10 duration_s=20")
    h.run()
    assert h.claude_calls() == 1


def test_zero_signals_and_no_new_transcripts_is_skipped(h: Harness) -> None:
    h.signal_total = 0
    h.si.state.mkdir(parents=True, exist_ok=True)
    (h.si.state / "last-auto-review").touch()
    assert h.run() == "self-improvement auto-review: skipped (no signals, no new sessions)"
    assert h.claude_calls() == 0
    assert h.refresh_calls == 1
    assert " mode=auto status=skipped sessions=0 proposals=0 cost_usd=0 " in h.si.last_log()


def test_zero_signals_but_a_newer_transcript_still_runs(h: Harness, transcript: Path) -> None:
    h.signal_total = 0
    h.si.state.mkdir(parents=True, exist_ok=True)
    stamp = h.si.state / "last-auto-review"
    stamp.touch()
    os.utime(stamp, (1577836800, 1577836800))  # 2020-01-01
    h.run()
    assert h.claude_calls() == 1


@pytest.mark.parametrize(("cost", "blocked"), [("0.30", True), ("unknown", True), ("0", False)])
def test_failed_runs_and_the_run_limit(h: Harness, transcript: Path, cost: str, blocked: bool) -> None:
    h.si.review_line(f"{iso_days_ago(2)} mode=auto status=failed sessions=2 proposals=0 cost_usd={cost} duration_s=20")
    h.run()
    assert h.claude_calls() == (0 if blocked else 1)


def test_an_over_budget_run_counts_towards_the_run_limit(h: Harness, transcript: Path) -> None:
    h.si.review_line(f"{iso_days_ago(2)} mode=auto status=over-budget sessions=2 proposals=1 cost_usd=0.60 duration_s=20")
    h.run()
    assert h.claude_calls() == 0


# --- digest -------------------------------------------------------------------------------


def test_digest_bounds_and_exclusions(h: Harness) -> None:
    big = "x" * 30000
    for i in range(20):
        write_transcript(h.si.projects / "-example-proj" / f"s{i}.jsonl", "cli", f"SESSION-{i} {big}")
    write_transcript(h.si.projects / "-example-proj" / "headless.jsonl", "sdk-cli", "HEADLESS-MARKER")
    write_transcript(h.si.projects / "-example-proj" / "u1" / "subagents" / "agent-1.jsonl", "cli", "SUBAGENT-MARKER")
    h.run()
    captured = h.captured()
    prompt_bytes = len(prompt.prompt_text().encode())
    assert len(captured.encode()) - prompt_bytes - 1 <= review.DIGEST_MAX_BYTES
    sessions = captured.count("\n### Session ")
    assert 1 <= sessions <= 15
    for absent in ("HEADLESS-MARKER", "SUBAGENT-MARKER", "ASSISTANT-TEXT", "FINE-RESULT", "META-TEXT", "INJECTED"):
        assert absent not in captured, absent
    assert "TOOL ERROR: Permission denied by hook" in captured
    assert f"Today's date: {days_ago(0)}" in captured
    assert " sessions=15 " in h.si.last_log()


def test_user_text_is_truncated_per_message_and_session(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(path, "cli", "y" * 2000)
    extract = review.session_extract(path)
    assert extract.splitlines()[0] == "USER: " + "y" * 500
    many = tmp_path / "many.jsonl"
    many.write_text("".join(
        json.dumps({"type": "user", "message": {"content": "z" * 600}}) + "\n" for _ in range(40)
    ))
    assert len(review.session_extract(many).encode()) == review.SESSION_MAX_BYTES


def test_a_symlinked_projects_directory_is_followed(h: Harness) -> None:
    real = h.si.root / "real-projects"
    write_transcript(real / "-example-proj" / "a.jsonl")
    for child in h.si.projects.iterdir():
        child.rmdir()
    h.si.projects.rmdir()
    h.si.projects.symlink_to(real)
    h.run()
    assert " status=ran sessions=1 " in h.si.last_log()


def test_digest_carries_prompt_signals_backlog_and_deny_summary(h: Harness, transcript: Path) -> None:
    from hooks.telemetry import TelemetryEntry, log_event

    for _ in range(2):
        log_event(TelemetryEntry(
            hook="bash-hard-deny", event="PreToolUse", tool="Bash", session_id="", cwd_short="",
            decision="deny", cache="none", verdict="blocked", input_hash="",
        ))
    h.run()
    captured = h.captured()
    assert captured.startswith("# Headless self-improvement review\n")
    assert "SIGNAL_TOTAL=3" in captured
    assert "friction note 0" in captured
    assert "open item" in captured
    assert "done item" not in captured
    assert re.search(r"^ *2 bash-hard-deny$", captured, re.M)


def test_deny_summary_reports_unavailable_telemetry(h: Harness, transcript: Path) -> None:
    h.run()
    assert "## Hook denies in the last 7 days, by hook\n(telemetry unavailable)\n" in h.captured()


@pytest.mark.parametrize("facets", ["empty", "missing"])
def test_an_empty_or_missing_facets_directory_proceeds(h: Harness, transcript: Path, facets: str) -> None:
    if facets == "missing":
        (h.si.home / ".claude" / "usage-data" / "facets").rmdir()
    h.run()
    assert " status=ran " in h.si.last_log()


def test_facet_friction_and_outcome_only(h: Harness, transcript: Path) -> None:
    (h.si.home / ".claude" / "usage-data" / "facets" / "x.json").write_text(json.dumps(
        {"session_id": "x", "friction_detail": "FACET-MARKER", "outcome": "partial", "brief_summary": "SUMMARY-SECRET"}
    ))
    h.run()
    assert '{"friction_detail":"FACET-MARKER","outcome":"partial"}' in h.captured()
    assert "SUMMARY-SECRET" not in h.captured()


# --- validation and append ----------------------------------------------------------------


def test_only_valid_new_lines_are_appended_under_inbox(h: Harness, transcript: Path) -> None:
    before = h.si.backlog.read_text()
    h.run()
    after = h.si.backlog.read_text()
    assert h.auto_lines() == 11
    for absent in ("bogus", "] none:", "no arrow", "indented line", "\r"):
        assert absent not in after
    assert after.count("manual shop steps") == 1
    assert after.count("rg prompts every session") == 1
    after_lines = after.splitlines()
    entries_at = after_lines.index("## Entries")
    last_auto = max(i for i, line in enumerate(after_lines) if "[auto-review]" in line)
    assert last_auto < entries_at
    new_lines = [line for line in after_lines if line not in before.splitlines()]
    assert len(new_lines) == 10
    assert "\n".join(line for line in after_lines if line not in new_lines) + "\n" == before
    # Inserted after the last non-blank Inbox line: the blank separator stays before Entries.
    assert after_lines[entries_at - 1] == ""
    assert after_lines[entries_at - 2] == new_lines[-1]


def test_the_auto_log_line_has_the_right_fields_and_cost(h: Harness, transcript: Path) -> None:
    summary = h.run()
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z mode=auto status=ran sessions=1 proposals=10 cost_usd=0\.12 duration_s=\d+",
        h.si.last_log(),
    )
    assert summary == "self-improvement auto-review: ran, sessions 1, proposals 10, cost USD 0.12"
    assert (h.si.state / "last-auto-review").exists()
    assert h.si.notify.messages == []


def test_no_findings_appends_nothing(h: Harness, transcript: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUB_CLAUDE", "none")
    before = h.si.backlog.read_bytes()
    h.run()
    assert h.si.backlog.read_bytes() == before
    assert " status=ran sessions=1 proposals=0 cost_usd=0.05 " in h.si.last_log()


def test_the_model_call_flags(h: Harness, transcript: Path) -> None:
    h.run()
    (args,) = h.stub_lines("claude-args: ")
    assert json.loads(args) == [
        "-p", "--model", "sonnet", "--max-budget-usd", "0.50", "--output-format", "json",
        "--no-session-persistence", "--restricted", "--strict-mcp-config", "--tools", "",
    ]
    (cwd,) = h.stub_lines("claude-cwd: ")
    assert Path(cwd).resolve().parent == (h.si.root / "tmp").resolve()


# --- subscription-only auth ---------------------------------------------------------------


def test_api_billing_variables_never_reach_claude(h: Harness, transcript: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(
        monkeypatch, ANTHROPIC_API_KEY="sk-ant-x", ANTHROPIC_AUTH_TOKEN="t",
        CLAUDE_CODE_USE_BEDROCK="1", CLAUDE_CODE_USE_VERTEX="1", CLAUDE_CODE_USE_FOUNDRY="1",
        CLAUDE_CODE_OAUTH_TOKEN="subscription-token",
    )
    h.run()
    assert h.stub_lines("claude-auth-env: ") == ['["CLAUDE_CODE_OAUTH_TOKEN"]']
    assert h.stub_lines("claude-env: ") == ['["CLAUDE_CODE_OAUTH_TOKEN"]']
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-x"  # the job's own environment is untouched


@pytest.mark.parametrize("method", ["claude.ai", "oauth_token"])
def test_subscription_logins_are_accepted(h: Harness, transcript: Path, monkeypatch: pytest.MonkeyPatch, method: str) -> None:
    monkeypatch.setenv("STUB_AUTH", method)
    h.run()
    assert h.claude_calls() == 1


@pytest.mark.parametrize("method", ["logged-out", "api_key", "third_party", "garbage"])
def test_no_subscription_login_fails_with_cost_0(h: Harness, transcript: Path, monkeypatch: pytest.MonkeyPatch, method: str) -> None:
    monkeypatch.setenv("STUB_AUTH", method)
    summary = h.run()
    assert h.claude_calls() == 0
    assert " status=failed sessions=1 proposals=0 cost_usd=0 " in h.si.last_log()
    assert h.si.notify.messages == ["Self-improvement auto-review failed: no subscription login (cost USD 0)"]
    assert "no subscription login" in summary
    monkeypatch.setenv("STUB_AUTH", "claude.ai")
    h.run()
    assert h.claude_calls() == 1


# --- failure paths ------------------------------------------------------------------------


def assert_failed_run(h: Harness) -> None:
    assert " mode=auto status=failed " in h.si.last_log()
    assert len(h.si.notify.messages) == 1
    assert "auto-review failed" in h.si.notify.messages[0]
    assert h.auto_lines() == 1
    assert not (h.si.state / "last-auto-review").exists()


@pytest.mark.parametrize("mode", ["exit1", "iserror", "garbage"])
def test_failed_model_calls(h: Harness, transcript: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setenv("STUB_CLAUDE", mode)
    h.run()
    assert_failed_run(h)
    if mode == "iserror":
        assert " cost_usd=0.01 " in h.si.last_log()
    else:
        assert " cost_usd=unknown " in h.si.last_log()


def test_a_timeout_is_failed_with_unknown_cost(h: Harness, transcript: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, STUB_CLAUDE="timeout", SELF_IMPROVEMENT_MODEL_TIMEOUT_S="2")
    h.run()
    assert_failed_run(h)
    assert " status=failed sessions=1 proposals=0 cost_usd=unknown " in h.si.last_log()


def test_a_failing_notification_does_not_change_the_status(h: Harness, transcript: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUB_CLAUDE", "exit1")
    h.si.notify.fail = True
    h.run()
    assert " status=failed " in h.si.last_log()


def test_cost_over_the_cap_is_over_budget(h: Harness, transcript: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUB_CLAUDE", "overbudget")
    h.run()
    assert " mode=auto status=over-budget sessions=1 proposals=1 cost_usd=0.6 " in h.si.last_log()
    assert h.si.notify.messages == ["Self-improvement auto-review over-budget (cost USD 0.6)"]
    assert (h.si.state / "last-auto-review").exists()


def test_is_error_with_a_cost_over_the_cap_is_over_budget(h: Harness, transcript: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUB_CLAUDE", "iserror_overcap")
    h.run()
    assert " status=over-budget sessions=1 proposals=0 cost_usd=0.52 " in h.si.last_log()
    assert len(h.si.notify.messages) == 1


def test_an_unwritable_backlog_fails_and_leaves_it_untouched(h: Harness, transcript: Path) -> None:
    before = h.si.backlog.read_bytes()
    claude_dir = h.si.home / ".claude"
    claude_dir.chmod(0o555)
    try:
        h.run()
    finally:
        claude_dir.chmod(0o755)
    assert " status=failed sessions=1 proposals=0 cost_usd=0.12 " in h.si.last_log()
    assert len(h.si.notify.messages) == 1
    assert h.si.backlog.read_bytes() == before
    assert sorted(p.name for p in claude_dir.iterdir()) == [
        "projects", "self-improvement-backlog.md", "usage-data",
    ]


def test_a_failed_replace_leaves_the_backlog_untouched(h: Harness, transcript: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = h.si.backlog.read_bytes()
    real_replace = Path.replace

    def failing_replace(self: Path, target: str | os.PathLike[str]) -> Path:
        if Path(target).name == "self-improvement-backlog.md":
            raise OSError("disk full")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", failing_replace)
    h.run()
    assert " status=failed " in h.si.last_log()
    assert h.si.backlog.read_bytes() == before
    assert [p.name for p in h.si.backlog.parent.iterdir() if p.name.startswith(".")] == []


def test_the_model_timeout_is_capped_by_the_job_budget(h: Harness, transcript: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, STUB_CLAUDE="timeout", SELF_IMPROVEMENT_JOB_BUDGET_S="3")
    start = time.monotonic()
    h.run()
    assert time.monotonic() - start < 15
    assert " status=failed " in h.si.last_log()


def test_a_run_with_no_time_left_never_calls_claude(h: Harness, transcript: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SELF_IMPROVEMENT_JOB_BUDGET_S", "0")
    h.run()
    assert " status=failed sessions=1 proposals=0 cost_usd=0 " in h.si.last_log()
    assert h.log.read_text() == ""
    assert len(h.si.notify.messages) == 1


def test_a_missing_claude_logs_cost_0_and_does_not_block(h: Harness, transcript: Path) -> None:
    stub = h.bin / "claude"
    stub.rename(h.bin / "claude.off")
    h.run()
    assert " status=failed sessions=1 proposals=0 cost_usd=0 " in h.si.last_log()
    (h.bin / "claude.off").rename(stub)
    h.run()
    assert " status=ran " in h.si.last_log()


def test_a_model_that_ignores_sigterm_is_killed_and_logged(h: Harness, transcript: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, STUB_CLAUDE="ignore_term", SELF_IMPROVEMENT_MODEL_TIMEOUT_S="2")
    start = time.monotonic()
    h.run()
    assert time.monotonic() - start < 15
    assert " status=failed sessions=1 proposals=0 cost_usd=unknown " in h.si.last_log()
    assert len(h.si.notify.messages) == 1


def test_a_missing_backlog_never_calls_the_model(h: Harness, transcript: Path) -> None:
    h.si.backlog.unlink()
    summary = h.run()
    assert h.claude_calls() == 0
    assert " status=failed sessions=1 proposals=0 cost_usd=0 " in h.si.last_log()
    assert h.si.notify.messages == ["Self-improvement auto-review failed: no backlog Inbox (cost USD 0)"]
    assert "no backlog Inbox" in summary


def test_a_symlinked_backlog_is_updated_at_its_target(h: Harness, transcript: Path) -> None:
    target = h.si.root / "repo" / "self-improvement-backlog.md"
    target.parent.mkdir()
    h.si.backlog.rename(target)
    h.si.backlog.symlink_to(target)
    h.run()
    assert h.si.backlog.is_symlink()
    assert target.read_text().count("[auto-review]") == 11


# --- cleanup and blast radius -------------------------------------------------------------


@pytest.mark.parametrize("mode", ["ok", "exit1"])
def test_the_temp_digest_directory_is_removed(h: Harness, transcript: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setenv("STUB_CLAUDE", mode)
    h.run()
    assert list((h.si.root / "tmp").iterdir()) == []


def test_the_digest_directory_is_private(h: Harness, transcript: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    modes: list[int] = []
    real = review.call_model

    def spy(claude: str, work_dir: Path, *args: object, **kwargs: object) -> int:
        modes.append(stat.S_IMODE(work_dir.stat().st_mode))
        return real(claude, work_dir, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(review, "call_model", spy)
    h.run()
    assert modes == [0o700]


def test_no_file_other_than_backlog_and_state_changes(h: Harness, transcript: Path) -> None:
    exclude = [h.si.state, h.si.backlog, h.log, h.capture, h.si.stores]
    before = tree_checksums(h.si.root, exclude)
    h.run()
    assert " status=ran " in h.si.last_log()
    assert tree_checksums(h.si.root, exclude) == before


def test_the_review_module_has_no_write_path_to_instruction_files() -> None:
    source = Path(review.__file__).read_text()
    assert not re.search(r"CLAUDE\.md|claude-md-specifics", source)


def test_the_real_refresh_runs_with_escalation_off(h: Harness, transcript: Path) -> None:
    h.run(real_refresh=True)
    assert state.parse_env((h.si.state / "signals.env").read_text())["LOOP_HEALTH"] == "FAIL"
    assert h.si.notify.messages == []
    assert not (h.si.state / "last-escalation").exists()


def test_sessions_are_newest_first_and_older_than_stamp_excluded(h: Harness) -> None:
    proj = h.si.projects / "-example-proj"
    write_transcript(proj / "old.jsonl", "cli", "OLDER-SESSION")
    os.utime(proj / "old.jsonl", (1577836800, 1577836800))  # 2020-01-01
    write_transcript(proj / "ancient.jsonl", "cli", "ANCIENT-SESSION")
    os.utime(proj / "ancient.jsonl", (1514764800, 1514764800))  # 2018-01-01
    h.si.state.mkdir(parents=True, exist_ok=True)
    stamp = h.si.state / "last-auto-review"
    stamp.touch()
    os.utime(stamp, (1546300800, 1546300800))  # 2019-01-01
    write_transcript(proj / "new.jsonl", "cli", "NEWER-SESSION")
    h.run()
    assert " status=ran sessions=2 " in h.si.last_log()
    captured = h.captured()
    assert "ANCIENT-SESSION" not in captured
    assert captured.index("NEWER-SESSION") < captured.index("OLDER-SESSION")


def test_first_run_looks_back_7_days(h: Harness) -> None:
    proj = h.si.projects / "-example-proj"
    write_transcript(proj / "recent.jsonl", "cli", "RECENT")
    write_transcript(proj / "stale.jsonl", "cli", "STALE")
    eight_days = time.time() - 8 * 86400
    os.utime(proj / "stale.jsonl", (eight_days, eight_days))
    h.run()
    assert "RECENT" in h.captured() and "STALE" not in h.captured()


def test_a_transcript_written_during_a_run_is_picked_up_next_time(h: Harness, transcript: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STUB_CLAUDE", "new_transcript")
    h.run()
    assert " status=ran sessions=1 " in h.si.last_log()
    monkeypatch.setenv("STUB_CLAUDE", "none")
    h.run(force=True)
    assert " status=ran sessions=1 " in h.si.last_log()
    assert "DURING-RUN" in h.captured()


# --- pure helpers -------------------------------------------------------------------------


@pytest.mark.parametrize(("value", "text"), [(0.12, "0.12"), (0.6, "0.6"), (0, "0"), (1.0, "1"), (0.0999709, "0.0999709")])
def test_format_cost_matches_jq(value: float, text: str) -> None:
    assert review.format_cost(value) == text


def test_insert_into_an_inbox_at_end_of_file() -> None:
    assert review.insert_into_inbox("# B\n\n## Inbox\n- [2026-01-01] x\n\n", ["NEW"]) == (
        "# B\n\n## Inbox\n- [2026-01-01] x\nNEW\n\n"
    )


def test_has_inbox_needs_the_exact_heading() -> None:
    assert review.has_inbox("## Inbox\n")
    assert not review.has_inbox("## Inbox notes\n")


