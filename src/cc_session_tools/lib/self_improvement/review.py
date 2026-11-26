"""Weekly headless, proposal-only self-improvement review
(``ccst self-improvement auto-review``).

Builds a size-capped digest of the signal snapshot, the backlog, hook denies, any facet files
and recent session extracts in a private temporary directory, runs one cost-capped
``claude -p`` call with no tools over it using the subscription login only, and appends the
validated proposal lines to the backlog Inbox.

It never edits configuration: the only files it writes are its state files and new lines in
the ``## Inbox`` section of the backlog. Every attempt is logged to ``reviews.log`` with its
cost; a failed or over-budget run sends one Telegram message.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cc_session_tools.lib import api_keys, paths
from cc_session_tools.lib.self_improvement import prompt, signals, state

logger = logging.getLogger(__name__)

BUDGET_USD = 0.50
MODEL = "sonnet"
RUN_LIMIT_DAYS = 7
FIRST_RUN_WINDOW = dt.timedelta(days=7)
MAX_SESSIONS = 15
MAX_PROPOSALS = 10
DIGEST_MAX_BYTES = 122880  # 120 KB
SESSION_MAX_BYTES = 8000  # 15 sessions fit inside the digest cap
USER_TEXT_MAX_CHARS = 500
TOOL_ERROR_MAX_CHARS = 300
HEADER_SCAN_LINES = 20
TERM_GRACE_S = 3
AUTH_CHECK_MAX_S = 30
MODEL_TIMEOUT_ENV = "SELF_IMPROVEMENT_MODEL_TIMEOUT_S"
JOB_BUDGET_ENV = "SELF_IMPROVEMENT_JOB_BUDGET_S"
DEFAULT_MODEL_TIMEOUT_S = 240
# Wall time the job allows itself, under the scheduler's 300 s job timeout, so a slow run is
# still logged and notified here instead of being killed silently.
DEFAULT_JOB_BUDGET_S = 270
# `claude auth status` values that mean a first-party subscription login: an interactive
# claude.ai login, or a CLAUDE_CODE_OAUTH_TOKEN subscription token.
SUBSCRIPTION_AUTH_METHODS = frozenset({"claude.ai", "oauth_token"})
HEADLESS_MARKER = '"entrypoint":"sdk-cli"'
NOT_STARTED_RC = 127
TIMEOUT_RC = 124

_BODY_PREFIX_RE = re.compile(r"^- \[[0-9-]+\] ")
_BACKLOG_AUTO_RE = re.compile(r"^- \[[0-9-]+\] \[auto-review\] ")
Refresher = Callable[[], None]


@dataclass(frozen=True, slots=True)
class Outcome:
    status: str
    sessions: int
    proposals: int
    cost: str


# --- helpers -----------------------------------------------------------------------------


def _env_seconds(name: str, default: int) -> int:
    """An integer number of seconds from the environment; anything else means the default."""
    try:
        return int(os.environ.get(name, ""))
    except ValueError:
        return default


def subscription_env() -> dict[str, str]:
    """The current environment without API-billed credentials or provider switches."""
    env = dict(os.environ)
    api_keys.without_api_auth(env)
    return env


def format_cost(value: int | float) -> str:
    """The cost as ``jq -r`` prints it, so log lines match the bash implementation's."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _body(line: str) -> str:
    return _BODY_PREFIX_RE.sub("", line, count=1)


# --- sessions and digest -----------------------------------------------------------------


def is_headless_transcript(path: Path) -> bool:
    """True for a ``claude -p`` transcript (hook sub-sessions and this job's own kind)."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for _ in range(HEADER_SCAN_LINES):
                line = fh.readline()
                if not line:
                    return False
                if HEADLESS_MARKER in line:
                    return True
    except OSError:
        return True
    return False


def list_sessions(projects_dir: Path, since_epoch: float) -> list[Path]:
    """Transcripts modified after ``since_epoch``, newest first, at most MAX_SESSIONS.
    Sub-agent and headless transcripts are excluded. A symlinked projects directory is
    followed; symlinks below it are not."""
    if not projects_dir.is_dir():
        return []
    found: list[tuple[float, Path]] = []
    for root, dirs, files in os.walk(projects_dir):
        rel_parts = Path(root).relative_to(projects_dir).parts
        if "subagents" in rel_parts:
            dirs[:] = []
            continue
        for name in files:
            if not name.endswith(".jsonl"):
                continue
            path = Path(root) / name
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime > since_epoch and not is_headless_transcript(path):
                found.append((mtime, path))
    found.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in found[:MAX_SESSIONS]]


def backlog_extract(text: str) -> str:
    """The ``## Inbox`` section, plus ``### `` entries whose status is open or proposed."""
    out: list[str] = []
    section = ""
    block: list[str] = []
    keep = False

    def flush() -> None:
        nonlocal block, keep
        if keep:
            out.extend(block)
            out.append("")
        block, keep = [], False

    for line in text.splitlines():
        if line.startswith("## "):
            flush()
            section = line
            if section == "## Inbox":
                out.append(line)
            continue
        if section == "## Inbox":
            out.append(line)
        elif section == "## Entries":
            if line.startswith("### "):
                flush()
                block = [line]
            elif block:
                block.append(line)
                if signals.OPEN_STATUS_RE.match(line):
                    keep = True
    flush()
    return "\n".join(out)


def deny_summary(now: dt.datetime) -> str:
    rows = signals.deny_rows(now)
    if rows is None:
        return "(telemetry unavailable)"
    counts = Counter(str(row["hook"]) for row in rows)
    return "\n".join(f"{n:>4} {hook}" for hook, n in sorted(counts.items()))


def facet_extract(facets_dir: Path) -> str:
    """``friction_detail`` and ``outcome`` of each facet file; nothing else from them."""
    if not facets_dir.is_dir():
        return ""
    out: list[str] = []
    for path in sorted(facets_dir.glob("*.json")):
        try:
            data = json.loads(state.read_text(path) or "null")
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            out.append(json.dumps(
                {"friction_detail": data.get("friction_detail"), "outcome": data.get("outcome")},
                separators=(",", ":"),
            ))
    return "\n".join(out)


def _tool_error_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(item.get("text") or "") if isinstance(item, dict) else "" for item in content
        )
    return ""


def session_extract(path: Path) -> str:
    """User-message text, tool errors and permission denials only. Assistant text, successful
    tool output and system-injected text (anything starting ``<``) are left out."""
    out: list[str] = []
    for raw in (state.read_text(path) or "").splitlines():
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or record.get("type") != "user" or record.get("isMeta"):
            continue
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            if not content.startswith("<"):
                out.append("USER: " + content[:USER_TEXT_MAX_CHARS])
        elif isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text":
                    text = str(item.get("text") or "")
                    if not text.startswith("<"):
                        out.append("USER: " + text[:USER_TEXT_MAX_CHARS])
                elif item.get("type") == "tool_result" and item.get("is_error") is True:
                    out.append(
                        "TOOL ERROR: "
                        + _tool_error_text(item.get("content"))[:TOOL_ERROR_MAX_CHARS]
                    )
    return _cut_bytes("".join(line + "\n" for line in out), SESSION_MAX_BYTES)


def _cut_bytes(text: str, limit: int) -> str:
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def build_digest(sessions: list[Path], backlog_text: str, now: dt.datetime) -> str:
    snapshot = state.read_text(state.state_dir() / state.SIGNALS_FILE) or "(no snapshot)\n"
    parts = [
        f"Today's date: {now.date().isoformat()}\n\n",
        "## Signal snapshot\n", snapshot, "\n",
        "## Backlog (Inbox and open or proposed entries)\n", backlog_extract(backlog_text), "\n\n",
        "## Hook denies in the last 7 days, by hook\n", deny_summary(now), "\n\n",
        "## Facets\n",
    ]
    facets = facet_extract(Path.home() / ".claude" / "usage-data" / "facets")
    if facets:
        parts.append(facets + "\n")
    parts.append("\n## Session extracts (newest first)\n")
    for n, path in enumerate(sessions, start=1):
        parts.append(f"\n### Session {n}\n{session_extract(path)}\n")
    return _cut_bytes("".join(parts), DIGEST_MAX_BYTES)


# --- validation and append ---------------------------------------------------------------


def valid_proposals(result: str, levers: tuple[str, ...], backlog_text: str) -> list[str]:
    """Lines of the exact proposal form with an actionable lever, not already in the backlog
    (whatever their date) or earlier in this output, at most MAX_PROPOSALS."""
    line_re = re.compile(
        r"- \[[0-9]{4}-[0-9]{2}-[0-9]{2}\] \[auto-review\] (?:"
        + "|".join(re.escape(lever) for lever in levers)
        + r"): .+ -> .+"
    )
    seen = {_body(line) for line in backlog_text.splitlines() if _BACKLOG_AUTO_RE.match(line)}
    kept: list[str] = []
    # splitlines, as every reader of the backlog uses: a line kept here must read back as the
    # same single line, or the duplicate check would miss it next week.
    for line in result.splitlines():
        if not line_re.fullmatch(line):
            continue
        body = _body(line)
        if body in seen:
            continue
        seen.add(body)
        kept.append(line)
        if len(kept) == MAX_PROPOSALS:
            break
    return kept


def has_inbox(backlog_text: str) -> bool:
    return "## Inbox" in backlog_text.split("\n")


def insert_into_inbox(backlog_text: str, new_lines: list[str]) -> str:
    """Insert after the last non-blank line of the Inbox section, so blank separator lines
    before the next heading stay where they are. Every other byte is unchanged."""
    lines = backlog_text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    out: list[str] = []
    in_inbox = False
    done = False
    blanks: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if in_inbox and not done:
                out.extend(new_lines)
                done = True
            if in_inbox:
                out.extend(blanks)
                blanks = []
            in_inbox = line == "## Inbox"
            out.append(line)
        elif in_inbox and not line.strip():
            blanks.append(line)
        elif in_inbox:
            out.extend(blanks)
            blanks = []
            out.append(line)
        else:
            out.append(line)
    if in_inbox and not done:
        out.extend(new_lines)
    out.extend(blanks)
    return "".join(line + "\n" for line in out)


# --- model call --------------------------------------------------------------------------


def subscription_login_ok(
    claude: str, work_dir: Path, env: dict[str, str], timeout_s: float,
) -> bool | None:
    """True for a first-party subscription login, False otherwise, None when ``claude``
    cannot be run at all. Runs from ``work_dir`` with ``env``, as the model call does."""
    try:
        proc = subprocess.run(
            [claude, "auth", "status", "--json"], cwd=work_dir, env=env, capture_output=True,
            text=True, timeout=timeout_s, check=False,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return False
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False
    return (
        isinstance(data, dict)
        and data.get("loggedIn") is True
        and data.get("apiProvider") == "firstParty"
        and data.get("authMethod") in SUBSCRIPTION_AUTH_METHODS
    )


def model_command(claude: str) -> list[str]:
    return [
        claude, "-p", "--model", MODEL, "--max-budget-usd", f"{BUDGET_USD:.2f}",
        "--output-format", "json", "--no-session-persistence", "--restricted",
        "--strict-mcp-config", "--tools", "",
    ]


def call_model(
    claude: str, work_dir: Path, input_path: Path, out_path: Path, env: dict[str, str],
    timeout_s: float,
) -> int:
    """Run the model call from ``work_dir`` (so no project instructions load). Returns its exit
    code, TIMEOUT_RC when it had to be stopped, NOT_STARTED_RC when it could not start. A
    child that ignores SIGTERM is killed after TERM_GRACE_S."""
    with input_path.open("rb") as stdin, out_path.open("wb") as stdout, \
            (work_dir / "err.txt").open("wb") as stderr:
        try:
            proc = subprocess.Popen(
                model_command(claude), stdin=stdin, stdout=stdout, stderr=stderr,
                cwd=work_dir, env=env,
            )
        except FileNotFoundError:
            return NOT_STARTED_RC
        try:
            return proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=TERM_GRACE_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            return TIMEOUT_RC


@dataclass(frozen=True, slots=True)
class ModelResult:
    cost: int | float
    is_error: bool
    text: str


def parse_result(out_path: Path) -> ModelResult | None:
    """The JSON result when it has a numeric ``total_cost_usd`` and a boolean ``is_error``."""
    try:
        data = json.loads(state.read_text(out_path) or "null")
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    cost = data.get("total_cost_usd")
    is_error = data.get("is_error")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not isinstance(is_error, bool):
        return None
    text = data.get("result")
    return ModelResult(cost=cost, is_error=is_error, text=text if isinstance(text, str) else "")


# --- the run -----------------------------------------------------------------------------


class _Run:
    def __init__(self, notify: signals.Notifier) -> None:
        self.started = time.monotonic()
        self.notify = notify
        self.state_dir = state.state_dir()
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def elapsed(self) -> int:
        return int(time.monotonic() - self.started)

    def log(self, outcome: Outcome) -> None:
        line = (
            f"{state.now_iso()} mode=auto status={outcome.status} sessions={outcome.sessions} "
            f"proposals={outcome.proposals} cost_usd={outcome.cost} duration_s={self.elapsed()}\n"
        )
        with (self.state_dir / state.REVIEWS_LOG).open("a", encoding="utf-8") as fh:
            fh.write(line)

    def finish(self, outcome: Outcome, reason: str | None = None) -> str:
        """Log, notify on failed or over-budget (a failed send changes nothing), and return
        the summary line."""
        self.log(outcome)
        if outcome.status in ("failed", "over-budget"):
            detail = f": {reason}" if reason else ""
            self.notify(
                f"Self-improvement auto-review {outcome.status}{detail} (cost USD {outcome.cost})"
            )
        suffix = f" ({reason})" if reason else ""
        return (
            f"self-improvement auto-review: {outcome.status}{suffix}, sessions "
            f"{outcome.sessions}, proposals {outcome.proposals}, cost USD {outcome.cost}"
        )


def _refresh_signals(notify: signals.Notifier) -> None:
    try:
        signals.run(escalate_on_fail=False, notify=notify)
    except OSError as exc:
        logger.warning("signal refresh failed: %s", exc)


def run(
    *, force: bool, notify: signals.Notifier, claude: str = "claude",
    refresh: Refresher | None = None,
) -> str:
    """One headless review attempt. Returns the summary line; every outcome is logged."""
    job = _Run(notify)
    lines = state.read_log_lines(job.state_dir)

    # 1. Run limit: a run that may have spent money within RUN_LIMIT_DAYS blocks another model
    # call, so catch-ups can never spend more than once a week.
    if not force:
        last_paid = state.last_paid_run_date(lines)
        age = None if last_paid is None else state.days_since(last_paid)
        if age is not None and age < RUN_LIMIT_DAYS:
            job.log(Outcome("skipped", 0, 0, "0"))
            return f"self-improvement auto-review: skipped (last run {age}d ago)"

    # The next run's scan window starts here, so transcripts written while this run is in
    # progress are picked up next time.
    run_start = time.time()

    # 2. Refresh the snapshot, with escalation off: this run's own auto line is not written yet.
    if refresh is None:
        _refresh_signals(notify)
    else:
        refresh()
    snapshot = state.parse_env(state.read_text(job.state_dir / state.SIGNALS_FILE) or "")
    signal_total = snapshot.get("SIGNAL_TOTAL")

    # 3. Sessions changed since the last completed run (or in the last 7 days before the first).
    stamp = job.state_dir / state.LAST_AUTO_REVIEW
    now = dt.datetime.now(dt.timezone.utc)
    since = stamp.stat().st_mtime if stamp.exists() else (now - FIRST_RUN_WINDOW).timestamp()
    sessions = list_sessions(paths.claude_projects_dir(), since)
    n_sessions = len(sessions)

    if signal_total == "0" and not sessions:
        job.log(Outcome("skipped", 0, 0, "0"))
        return "self-improvement auto-review: skipped (no signals, no new sessions)"

    # Proposals have nowhere to go without a backlog Inbox, so do not spend on a call.
    backlog = state.backlog_path().resolve()
    backlog_text = state.read_text(backlog) or ""
    if not has_inbox(backlog_text):
        return job.finish(Outcome("failed", n_sessions, 0, "0"), "no backlog Inbox")

    budget_s = _env_seconds(JOB_BUDGET_ENV, DEFAULT_JOB_BUDGET_S)
    model_timeout_s = _env_seconds(MODEL_TIMEOUT_ENV, DEFAULT_MODEL_TIMEOUT_S)
    env = subscription_env()

    with tempfile.TemporaryDirectory(prefix="ccst-self-improvement-") as tmp:
        work = Path(tmp)
        work.chmod(0o700)

        # 4. Digest.
        prompt_text = prompt.prompt_text()
        input_path = work / "input.md"
        input_path.write_text(
            prompt_text + "\n" + build_digest(sessions, backlog_text, now), encoding="utf-8"
        )

        # 5. Subscription login, then the model call, both inside the job's own time budget.
        if budget_s - job.elapsed() < 1:
            return job.finish(Outcome("failed", n_sessions, 0, "0"), "no time left")
        login = subscription_login_ok(
            claude, work, env, min(AUTH_CHECK_MAX_S, budget_s - job.elapsed())
        )
        if login is None:
            return job.finish(Outcome("failed", n_sessions, 0, "0"), "claude not found")
        if not login:
            return job.finish(Outcome("failed", n_sessions, 0, "0"), "no subscription login")
        timeout_s = min(model_timeout_s, budget_s - job.elapsed())
        if timeout_s < 1:
            return job.finish(Outcome("failed", n_sessions, 0, "0"), "no time left")
        out_path = work / "out.json"
        rc = call_model(claude, work, input_path, out_path, env, timeout_s)

        # 6. Parse. Spend is checked before errors: a call stopped at the cap may report
        # is_error and still have cost more than the cap. Without parseable JSON the cost is
        # unknown and the run still counts towards the run limit, unless the model never
        # started.
        status = "ran"
        cost = "0" if rc == NOT_STARTED_RC else "unknown"
        result = parse_result(out_path) if rc != TIMEOUT_RC else None
        if result is None:
            status = "failed"
        else:
            cost = format_cost(result.cost)
            if result.cost > BUDGET_USD:
                status = "over-budget"
            elif rc != 0 or result.is_error:
                status = "failed"

        # 7. Validate and append, against the backlog as it is now: it may have been edited
        # while the model ran. Any failure (no Inbox heading, unwritable directory) fails the
        # run and leaves the backlog untouched.
        proposals = 0
        if result is not None and status in ("ran", "over-budget"):
            current = state.read_text(backlog) or ""
            kept = valid_proposals(result.text, prompt.actionable_levers(prompt_text), current)
            if kept and not has_inbox(current):
                status = "failed"
            elif kept:
                try:
                    state.atomic_write(backlog, insert_into_inbox(current, kept))
                    proposals = len(kept)
                except OSError as exc:
                    logger.warning("backlog write failed: %s", exc)
                    status = "failed"

    # 8. Stamp, log and escalate.
    if status in ("ran", "over-budget"):
        stamp.touch()
        os.utime(stamp, (run_start, run_start))
    return job.finish(Outcome(status, n_sessions, proposals, cost))
