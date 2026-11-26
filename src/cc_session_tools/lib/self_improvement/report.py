"""``ccst self-improvement status``: what the state directory says, without writing to it."""
from __future__ import annotations

from pathlib import Path

from cc_session_tools.lib.self_improvement import state


def status_lines(state_dir: Path) -> list[str]:
    out = [f"state directory: {state_dir}"]
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
