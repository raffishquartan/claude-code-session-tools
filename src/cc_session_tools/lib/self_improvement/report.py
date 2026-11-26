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
    """How an interactive review is recorded on this install. Without the review skill
    nothing writes one, so the nudge and the FAIL message keep pointing here: say why and how
    to stop them."""
    if signals.review_skill_installed():
        return f"interactive review: run {signals.PERIODIC_REVIEW_COMMAND} in a session"
    return (
        f"interactive review: the {signals.REVIEW_SKILL} skill is not installed, so nothing "
        "records an interactive review and, once the loop is set up, the nudge repeats daily "
        "and loop health stays FAIL. Act on the backlog Inbox yourself; to turn the loop off "
        "again, remove the backlog's `## Inbox` line (or the backlog)"
    )


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
