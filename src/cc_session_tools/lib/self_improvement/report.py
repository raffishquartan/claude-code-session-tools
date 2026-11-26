"""``ccst self-improvement status``: what the state directory says, without writing to it."""
from __future__ import annotations

from pathlib import Path

from cc_session_tools.lib.self_improvement import signals, state


def setup_line() -> str:
    """Whether the loop is set up, and how to set it up when it is not."""
    backlog = state.backlog_path()
    if state.loop_set_up():
        return f"loop: set up (backlog {backlog})"
    return (
        f"loop: not set up - run `ccst self-improvement init` to create {backlog} with a "
        "`## Inbox` section; until then the nudge, the headless review and the FAIL "
        "notification stay quiet"
    )


def review_line() -> str:
    """How to run the interactive review, which records the review the loop health reads."""
    return f"interactive review: run {signals.PERIODIC_REVIEW_COMMAND} in a session"


def status_lines(state_dir: Path) -> list[str]:
    out = [setup_line(), review_line(), f"state directory: {state_dir}"]
    snapshot = state.read_text(state_dir / state.SIGNALS_FILE)
    if snapshot is None:
        out.append("snapshot: none (run `ccst self-improvement collect-signals`)")
    else:
        out.append("snapshot:")
        out.extend(f"  {key}={value}" for key, value in state.parse_env(snapshot).items())
    lines = state.read_log_lines(state_dir)
    out.append(f"last interactive review: {state.last_interactive_review_line(lines) or 'none'}")
    out.append(f"last headless run: {state.last_auto_line(lines) or 'none'}")
    attempts, known, unknown = state.headless_cost_since_last_review(lines)
    out.append(
        f"headless cost since last interactive review: {attempts} attempts, "
        f"USD {known:.2f} known, {unknown} with unknown cost"
    )
    return out
