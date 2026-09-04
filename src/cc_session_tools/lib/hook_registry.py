"""The set of hooks this build of CCST can dispatch, and what each one does.

Single source of truth for three consumers that must agree on which hook
names are live:

- ``cli.ccst`` dispatches ``ccst hooks run <name>`` through HOOK_VERBS and
  renders HOOK_DESCRIPTIONS in the install table.
- ``hooks_install.prune_stale_hooks`` removes settings.json entries naming a
  hook that is no longer here.
- ``lib.doctor.check_no_stale_hooks`` reports those entries before they wedge
  a session.

HOOK_VERBS, not config/hooks-bundle.json, is the authority on "can this build
run that hook". The bundle is the default *selection* installed by ``ccst
hooks install``; an operator may deliberately install a non-default hook with
``--hook``, and pruning against the bundle would silently undo that. The two
happen to list the same names today, which is exactly why the distinction is
worth writing down before they diverge.
"""
from __future__ import annotations

HOOK_VERBS: dict[str, str] = {
    "bash-hard-deny": "hooks.bash_hard_deny",
    "bash-security-review": "hooks.bash_security_review",
    "marker-allow": "hooks.marker_allow",
    "confirm-8digit": "hooks.confirm_8digit",
    "after-response": "hooks.after_response",
    "worklog-guard": "hooks.worklog_guard",
    "session-tag": "hooks.session_tag",
    "last-screenshot": "hooks.last_screenshot",
    "messaging-deliver": "hooks.messaging_deliver",
    "catchup": "hooks.catchup",
    "pending-migration": "hooks.pending_migration",
    "context-window-warning": "hooks.context_window_warning",
    "pending-rename": "hooks.pending_rename",
    "pdata-sync": "hooks.pdata_sync",
}


HOOK_DESCRIPTIONS: dict[str, str] = {
    "bash-hard-deny": "Hard-deny gate for Bash: blocks deletes, delete-by-move, gh/curl mutations, sudo, opentabs self-approval, telemetry-log reads (telemetry.db/fires.jsonl); auto-allows the rest (PreToolUse, Bash)",
    "bash-security-review": "Reviews shell commands for security risks (tiered: allowlist, heuristics, LLM)",
    "marker-allow": "Auto-approves a bare `touch` of a skill marker under ~/.cache/claude/markers/ (PreToolUse, Bash)",
    "confirm-8digit": "Enforces an 8-digit confirmation gate before risky tool calls",
    "after-response": "Touches a .last-active sentinel so `ccs --order-by active` can sort by recency",
    "worklog-guard": "Blocks manual /compact if the session's WORKLOG.md is stale (PreCompact, matcher: manual)",
    "session-tag": "For ccd/ccr-launched sessions: writes the session tag file so ccusage can map UUIDs to human-readable names, and emits additionalContext telling the assistant the tag/session-dir is already set",
    "last-screenshot": "Resolves the newest screenshot for the >lss token and injects its path",
    "messaging-deliver": "Delivers inter-session messages (digest + auto-read + receipts) on session start and each prompt",
    "catchup": "Reconciles+launches missed scheduled jobs (ccsched) detached and surfaces a catch-up digest (SessionStart + UserPromptSubmit)",
    "pending-migration": "Detects legacy pre-1.0.0 flat-file data left unmigrated (ccmsg/ccsched/sessions/telemetry) and surfaces a FAIL digest pointing at `ccst migrate all`; honours `ccst doctor --mute` (SessionStart)",
    "context-window-warning": "Nudges toward /compact when the context window passes 150k/200k tokens; silenced for the session by /context-override (Stop)",
    "pending-rename": "Surfaces `.pending-rename` markers left by the move-session skill, skipping those whose /rename already landed and collapsing the list above 3; self-times-out at 5s with a visible notice (SessionStart)",
    "pdata-sync": "Multi-laptop `ccst pdata` sync: rehydrates this project's DB from the published dump when it dominates local and the project isn't occupied by another live session (SessionStart), and publishes a fresh dump when local has new writes (SessionEnd); surfaces forks/checksum failures via Telegram + the catch-up digest",
}

# Every settings.json hook entry CCST owns is spelled `ccst hooks run <name>`.
# Both the prune and the doctor check key off this prefix so a third-party or
# hand-written hook entry is never touched.
HOOK_COMMAND_PREFIX = "ccst hooks run "


def hook_name_from_command(command: str) -> str | None:
    """The ``<name>`` in ``ccst hooks run <name>``, or None if not ours."""
    if not command.startswith(HOOK_COMMAND_PREFIX):
        return None
    return command[len(HOOK_COMMAND_PREFIX):].strip() or None
