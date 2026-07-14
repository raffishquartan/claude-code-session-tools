"""ccst — Claude Code Session Tools umbrella CLI.

Entry point: ccst <noun> <verb> [options]

Current subcommands:
  hooks install [--hook <name>]  Merge hook entries from the bundled set (or a
                                 custom --source) into a target settings.json.
  hooks uninstall [--hook <name>] Remove hook entries from a target settings.json.
  hooks run <name>               Run a Claude Code hook by name.
                                 Available hooks: bash-hard-deny,
                                 bash-security-review, marker-allow,
                                 confirm-8digit, after-response, worklog-guard,
                                 session-tag.
  skills install                 Symlink bundled skills into ~/.claude/skills/.
  skills uninstall [--skill <name>] Remove bundled skill symlinks.
  doctor                         Health-check: PATH, env vars, settings.json,
                                 hook registrations, skill symlinks, PyPI drift.
  shell install                  Add the ccl() wrapper function to ~/.bashrc /
                                 ~/.zshrc between sentinel markers.
  shell uninstall                Remove the ccl() block from shell rc files.
  sessions migrate               One-shot migration of the flat tag cache,
                                 activity sentinels, and cc-doctor-mutes.json
                                 into sessions.db. Non-destructive; never
                                 deletes old files automatically.
  sessions list                  List all sessions recorded in sessions.db
                                 (debug/inspection; --json for scripting).
  telemetry trim                 Trim telemetry.db by size / age (see ccst telemetry trim --help).
  telemetry query                Query recent hook fires from telemetry.db (see
                                 ccst telemetry query --help).
  gc report                      Report orphaned per-session-uuid entries across the
                                 scheduler, messaging, and session-env stores (never
                                 deletes anything).
  migrate ccsched                Migrate ccsched flat-file stores into ccsched.db
                                 (verify + tar-backup old files before removal).
  claude-md install              Add/update the inter-session-messaging block in
                                 ~/.claude/CLAUDE.md.
  claude-md uninstall            Remove the messaging block from CLAUDE.md.
  install-everything             Run all install steps (skills, hooks, shell,
                                 claude-md) then health-check. Dry run by default;
                                 pass --apply to write changes.
"""
from __future__ import annotations

import argparse
import datetime
import importlib
import json
import os
import sys
from enum import Enum
from pathlib import Path
from typing import Any

from cc_session_tools import __version__
from cc_session_tools.hooks_install import load_json, merge_hook_settings, write_json_atomic


HOOK_VERBS: dict[str, str] = {
    "bash-hard-deny": "cccs_hooks.bash_hard_deny",
    "bash-security-review": "cccs_hooks.bash_security_review",
    "marker-allow": "cccs_hooks.marker_allow",
    "confirm-8digit": "cccs_hooks.confirm_8digit",
    "after-response": "cccs_hooks.after_response",
    "worklog-guard": "cccs_hooks.worklog_guard",
    "session-tag": "cccs_hooks.session_tag",
    "last-screenshot": "cccs_hooks.last_screenshot",
    "messaging-deliver": "cccs_hooks.messaging_deliver",
    "catchup": "cccs_hooks.catchup",
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
}


# ---------- path discovery ----------


def _discover_source_dir() -> Path:
    """Return the bundled skills/ directory.

    Walk up from this module's file to find the repo root containing skills/.
    Falls back to ~/repos/claude-code-session-tools/skills/ for wheel installs.
    """
    # From src/cc_session_tools/cli/ccst.py walk up: cli/ -> cc_session_tools/ -> src/ -> repo root
    here = Path(__file__).resolve()
    candidate = here.parent.parent.parent.parent / "skills"
    if candidate.is_dir():
        return candidate
    fallback = Path.home() / "repos" / "claude-code-session-tools" / "skills"
    if fallback.is_dir():
        return fallback
    raise FileNotFoundError(
        "Cannot locate bundled skills/ directory. "
        "Run from the source tree or use --source to specify the path explicitly."
    )


def _discover_bundle() -> Path:
    """Return the bundled config/hooks-bundle.json path.

    Walk up from this module's file to find the repo root containing config/.
    Falls back to ~/repos/claude-code-session-tools/config/... for wheel installs.
    """
    here = Path(__file__).resolve()
    candidate = here.parent.parent.parent.parent / "config" / "hooks-bundle.json"
    if candidate.is_file():
        return candidate
    fallback = (
        Path.home() / "repos" / "claude-code-session-tools" / "config" / "hooks-bundle.json"
    )
    if fallback.is_file():
        return fallback
    raise FileNotFoundError(
        "Cannot locate bundled config/hooks-bundle.json. "
        "Run from the source tree or use --source to specify the path explicitly."
    )


# ---------- skills install ----------


class SkillAction(str, Enum):
    CREATE = "create"
    ALREADY_CORRECT = "already-correct"
    WRONG_TARGET = "wrong-target"
    NON_SYMLINK_EXISTS = "non-symlink-exists"


def _discover_skills(source_dir: Path) -> list[Path]:
    """Return immediate subdirs of source_dir that contain a SKILL.md file."""
    skills: list[Path] = []
    for entry in sorted(source_dir.iterdir()):
        if entry.is_dir() and (entry / "SKILL.md").is_file():
            skills.append(entry)
    return skills


def _decide_action(skill_src: Path, target_dir: Path) -> tuple[SkillAction, Path]:
    """Decide what action to take for a single skill."""
    dest = target_dir / skill_src.name
    if not dest.exists() and not dest.is_symlink():
        return SkillAction.CREATE, dest
    if dest.is_symlink():
        if dest.resolve() == skill_src.resolve():
            return SkillAction.ALREADY_CORRECT, dest
        return SkillAction.WRONG_TARGET, dest
    # exists and is not a symlink
    return SkillAction.NON_SYMLINK_EXISTS, dest


def _cmd_skills_install(args: argparse.Namespace) -> int:
    # Resolve source
    if args.source:
        source_dir = Path(args.source)
    else:
        try:
            source_dir = _discover_source_dir()
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    if not source_dir.is_dir():
        print(f"error: source directory not found: {source_dir}", file=sys.stderr)
        return 1

    target_dir = Path(args.target) if args.target else (Path.home() / ".claude" / "skills")

    skills = _discover_skills(source_dir)
    if not skills:
        print(f"No skills found in {source_dir} (no subdirectories with SKILL.md).")
        return 0

    decisions: list[tuple[SkillAction, Path, Path]] = []
    for skill_src in skills:
        action, dest = _decide_action(skill_src, target_dir)
        decisions.append((action, skill_src, dest))

    # Print table
    col_w = max(len(s.name) for _, s, _ in decisions)
    print(f"{'Skill':<{col_w}}  Action")
    print(f"{'-' * col_w}  {'-' * 20}")
    for action, skill_src, dest in decisions:
        print(f"{skill_src.name:<{col_w}}  {action.value}")

    if not args.apply:
        print(f"\nDry run — re-run with --apply to create symlinks in {target_dir}")
        return 0

    # Perform writes
    linked: list[Path] = []
    skipped: list[Path] = []
    failed: list[Path] = []
    target_dir.mkdir(parents=True, exist_ok=True)

    for action, skill_src, dest in decisions:
        if action == SkillAction.ALREADY_CORRECT:
            skipped.append(dest)
            continue

        if action == SkillAction.NON_SYMLINK_EXISTS and not args.force:
            print(
                f"error: {dest} exists and is not a symlink; use --force to move it aside",
                file=sys.stderr,
            )
            failed.append(dest)
            continue

        if action == SkillAction.WRONG_TARGET and not args.force:
            print(
                f"error: {dest} is a symlink to a different path; use --force to replace it",
                file=sys.stderr,
            )
            failed.append(dest)
            continue

        # Move aside existing non-symlink or wrong-target symlink
        if dest.exists() or dest.is_symlink():
            if action == SkillAction.NON_SYMLINK_EXISTS:
                timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
                backup = dest.parent / f"{dest.name}.bak-{timestamp}"
                dest.rename(backup)
                print(f"  moved aside: {dest.name} -> {backup.name}")
            else:
                # wrong-target symlink — just unlink
                dest.unlink()

        dest.symlink_to(skill_src)
        linked.append(dest)
        print(f"  linked: {dest} -> {skill_src}")

    print()
    if linked:
        print(f"Linked {len(linked)} skill(s) in {target_dir}")
    if skipped:
        print(f"Skipped {len(skipped)} (already correct)")
    if not linked and not skipped and not failed:
        print(f"Nothing to do in {target_dir}")

    if failed:
        print(
            f"\n{len(failed)} skill(s) could not be installed — see errors above",
            file=sys.stderr,
        )
        return 1

    return 0


# ---------- skills uninstall ----------


def _cmd_skills_uninstall(args: argparse.Namespace) -> int:
    """Remove bundled skill symlinks from the target directory."""
    # Resolve the bundled source for validation
    try:
        source_dir = _discover_source_dir()
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    target_dir = Path(args.target) if args.target else (Path.home() / ".claude" / "skills")
    if not target_dir.is_dir():
        print(f"No skills directory found at {target_dir} — nothing to do.")
        return 0

    bundled_skills = {s.name: s for s in _discover_skills(source_dir)}

    # If --skill was given, narrow to that one
    if args.skill:
        if args.skill not in bundled_skills:
            print(
                f"error: {args.skill!r} is not a known bundled skill. "
                f"Known: {', '.join(sorted(bundled_skills))}",
                file=sys.stderr,
            )
            return 1
        candidates = {args.skill: bundled_skills[args.skill]}
    else:
        candidates = bundled_skills

    removals: list[Path] = []
    errors = False

    for skill_name, skill_src in sorted(candidates.items()):
        dest = target_dir / skill_name
        if not dest.exists() and not dest.is_symlink():
            print(f"  skip: {skill_name} — not installed")
            continue
        if not dest.is_symlink():
            if not args.force:
                print(
                    f"  skip: {skill_name} — exists but is not a symlink; use --force to remove",
                    file=sys.stderr,
                )
                errors = True
                continue
        removals.append(dest)
        print(f"  - {dest}")

    if not removals:
        print("Nothing to remove.")
        return 1 if errors else 0

    if not args.apply:
        print(f"\nDry run — re-run with --apply to remove {len(removals)} symlink(s)")
        return 0

    for dest in removals:
        dest.unlink(missing_ok=True)
    print(f"\nRemoved {len(removals)} symlink(s) from {target_dir}")
    return 1 if errors else 0


# ---------- hooks install ----------


def _cmd_hooks_install(args: argparse.Namespace) -> int:
    # Resolve source path
    if args.source:
        source_path = Path(args.source)
    else:
        try:
            source_path = _discover_bundle()
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    target_path = Path(args.target)

    if not source_path.exists():
        print(f"error: source not found: {source_path}", file=sys.stderr)
        return 1

    source = load_json(source_path)

    # --hook selector: filter the bundle to just the named hook
    if args.hook:
        filtered = _filter_bundle_to_hook(source, args.hook)
        if filtered is None:
            known = _list_bundle_hook_names(source)
            print(
                f"error: hook {args.hook!r} not found in bundle. "
                f"Known hooks: {', '.join(sorted(known))}",
                file=sys.stderr,
            )
            return 1
        source = filtered

    target = load_json(target_path) if target_path.exists() else {}

    merged, additions = merge_hook_settings(source_settings=source, target_settings=target)

    inventory = _bundle_inventory(source)
    added_keys = {(a.event, a.matcher, a.command) for a in additions}
    _print_hooks_install_table(inventory, added_keys)

    if not additions:
        print("\nAlready up to date — nothing to add.")
        return 0

    if args.apply:
        write_json_atomic(target_path, merged)
        print(f"\nWrote {target_path}")
    else:
        print(f"\nDry run — re-run with --apply to write {target_path}")

    return 0


def _bundle_inventory(bundle: dict[str, Any]) -> list[tuple[str, str, str | None, str]]:
    """Return [(hook_name, event, matcher, command)] for every hook in the bundle.

    For commands matching ``ccst hooks run <name>``, ``hook_name`` is ``<name>``.
    For other commands (custom --source), ``hook_name`` is the command itself.
    """
    prefix = "ccst hooks run "
    out: list[tuple[str, str, str | None, str]] = []
    for event, blocks in bundle.get("hooks", {}).items():
        for block in blocks:
            matcher = block.get("matcher")
            for h in block.get("hooks", []):
                cmd = h.get("command", "")
                if cmd.startswith(prefix):
                    name = cmd[len(prefix):].strip() or cmd
                else:
                    name = cmd
                if cmd:
                    out.append((name, event, matcher, cmd))
    out.sort(key=lambda r: r[0])
    return out


def _print_hooks_install_table(
    inventory: list[tuple[str, str, str | None, str]],
    added_keys: set[tuple[str, str | None, str]],
) -> None:
    """Print a Hook | Status | Event | Description table to stdout."""
    headers = ("Hook", "Status", "Event", "Description")
    if not inventory:
        return

    rows: list[tuple[str, str, str, str]] = []
    for name, event, matcher, cmd in inventory:
        status = "install" if (event, matcher, cmd) in added_keys else "already-installed"
        event_label = f"{event}[{matcher}]" if matcher else event
        description = HOOK_DESCRIPTIONS.get(name, "")
        rows.append((name, status, event_label, description))

    widths = [
        max([len(headers[i])] + [len(r[i]) for r in rows]) for i in range(4)
    ]
    fmt = f"{{:<{widths[0]}}}  {{:<{widths[1]}}}  {{:<{widths[2]}}}  {{:<{widths[3]}}}"
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*row))


def _list_bundle_hook_names(bundle: dict[str, Any]) -> list[str]:
    """Return the list of hook names (the <name> in ccst hooks run <name>) in the bundle."""
    names: list[str] = []
    prefix = "ccst hooks run "
    for _event, blocks in bundle.get("hooks", {}).items():
        for block in blocks:
            for hook_entry in block.get("hooks", []):
                cmd = hook_entry.get("command", "")
                if cmd.startswith(prefix):
                    name = cmd[len(prefix):].strip()
                    if name and name not in names:
                        names.append(name)
    return names


def _filter_bundle_to_hook(bundle: dict[str, Any], hook_name: str) -> dict[str, Any] | None:
    """Return a single-entry bundle dict containing only the named hook, or None."""
    prefix = "ccst hooks run "
    target_cmd = f"{prefix}{hook_name}"
    filtered_hooks: dict[str, Any] = {}

    for event, blocks in bundle.get("hooks", {}).items():
        for block in blocks:
            matching_entries = [
                h for h in block.get("hooks", [])
                if h.get("command") == target_cmd
            ]
            if matching_entries:
                new_block: dict[str, Any] = {"hooks": matching_entries}
                if "matcher" in block:
                    new_block["matcher"] = block["matcher"]
                filtered_hooks.setdefault(event, []).append(new_block)

    if not filtered_hooks:
        return None
    return {"hooks": filtered_hooks}


# ---------- hooks uninstall ----------


def _cmd_hooks_uninstall(args: argparse.Namespace) -> int:
    """Remove hook entries from settings.json."""
    target_path = Path(args.target)

    if not target_path.exists():
        print(f"No settings.json found at {target_path} — nothing to do.")
        return 0

    settings = load_json(target_path)
    hook_name: str | None = args.hook

    removed: list[tuple[str, str | None, str]] = []  # (event, matcher, command)
    new_settings = _remove_hooks(settings, hook_name, removed)

    if not removed:
        if hook_name:
            print(f"No entries for {hook_name!r} found in {target_path}.")
        else:
            print(f"No ccst hook entries found in {target_path}.")
        return 0

    for event, matcher, command in removed:
        matcher_label = f" [{matcher}]" if matcher else ""
        print(f"  - {event}{matcher_label}: {command}")

    if args.apply:
        write_json_atomic(target_path, new_settings)
        print(f"\nWrote {target_path}")
    else:
        print(f"\nDry run — re-run with --apply to write {target_path}")

    return 0


def _remove_hooks(
    settings: dict[str, Any],
    hook_name: str | None,
    removed: list[tuple[str, str | None, str]],
) -> dict[str, Any]:
    """Return a copy of settings with matching ccst hooks removed.

    Appends removed entries to ``removed`` as (event, matcher, command).
    Removes empty blocks and empty event lists.
    """
    import copy

    result = copy.deepcopy(settings)
    hooks_section = result.get("hooks", {})
    prefix = "ccst hooks run "
    target_cmd = f"{prefix}{hook_name}" if hook_name else None

    events_to_delete = []
    for event, blocks in hooks_section.items():
        blocks_to_delete = []
        for block_idx, block in enumerate(blocks):
            matcher = block.get("matcher")
            kept_hooks = []
            for hook_entry in block.get("hooks", []):
                cmd = hook_entry.get("command", "")
                should_remove = (
                    (target_cmd is not None and cmd == target_cmd)
                    or (target_cmd is None and cmd.startswith(prefix))
                )
                if should_remove:
                    removed.append((event, matcher, cmd))
                else:
                    kept_hooks.append(hook_entry)
            block["hooks"] = kept_hooks
            if not kept_hooks:
                blocks_to_delete.append(block_idx)

        # Remove empty blocks (in reverse order to preserve indices)
        for idx in reversed(blocks_to_delete):
            blocks.pop(idx)

        if not blocks:
            events_to_delete.append(event)

    for event in events_to_delete:
        del hooks_section[event]

    if not hooks_section and "hooks" in result:
        del result["hooks"]

    return result


# ---------- doctor ----------


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Run the full health-check suite, or a mute/drift sub-mode."""
    from datetime import date

    from cc_session_tools.lib import doctor_mutes
    from cc_session_tools.lib.doctor import (
        Status,
        filter_unmuted_issues,
        format_drift_report,
        format_results,
        run_all_checks,
    )

    mutes_path = (
        Path(args.mutes_file) if args.mutes_file else doctor_mutes.default_mutes_path()
    )

    # Mute-management modes short-circuit before running any checks.
    if args.mute is not None:
        doctor_mutes.add_mute(mutes_path, args.mute, today=date.today().isoformat())
        print(f"Muted {args.mute!r}; 'ccst doctor --drift' will skip it.")
        return 0
    if args.unmute is not None:
        if doctor_mutes.remove_mute(mutes_path, args.unmute):
            print(f"Un-muted {args.unmute!r}.")
            return 0
        print(f"{args.unmute!r} was not muted.")
        return 1
    if args.list_mutes:
        mutes = doctor_mutes.load_mutes(mutes_path)
        if not mutes:
            print("No checks are muted.")
            return 0
        for name in sorted(mutes):
            print(f"{name}  (muted {mutes[name]})")
        return 0

    settings_path = Path(args.settings) if args.settings else (
        Path.home() / ".claude" / "settings.json"
    )

    try:
        bundle_path = _discover_bundle()
    except FileNotFoundError:
        bundle_path = Path("/dev/null")  # no bundle; doctor will WARN on missing hooks

    try:
        skills_source_dir: Path | None = _discover_source_dir()
    except FileNotFoundError:
        skills_source_dir = None

    skills_target_dir = Path(args.skills_dir) if args.skills_dir else (
        Path.home() / ".claude" / "skills"
    )

    env_vars = {
        "CLAUDE_SESSION_TOOLS_REPO_ROOT": os.environ.get("CLAUDE_SESSION_TOOLS_REPO_ROOT"),
        "CLAUDE_SESSION_TOOLS_PROJ_ROOT": os.environ.get("CLAUDE_SESSION_TOOLS_PROJ_ROOT"),
    }

    # The six migrated/new data stores (data-store SQLite uplift). Each accessor
    # resolves its own env-var override; three already return a full file path,
    # the two directory accessors get their .db filename appended.
    from cc_session_tools.lib.scheduler.store import scheduler_dir      # Phase 3 (moved here from .state)
    from cc_session_tools.lib.messaging.store import store_root         # Phase 2
    from cc_session_tools.lib.sessions_db import default_db_path as sessions_db_path  # Phase 4 (full .db path)
    from cc_session_tools.lib import telemetry_store                    # Phase 5 (db_path() -> full .db path)
    from cccs_hooks.cache import _db_path as command_cache_db_path      # Phase 6 (replaces deleted _DEFAULT_DB)
    from cc_session_tools.lib.claude_flags import _cache_file as claude_flags_file  # Phase 6 (full .json path)

    store_paths = {
        "ccmsg": store_root() / "ccmsg.db",
        "ccsched": scheduler_dir() / "ccsched.db",
        "sessions": sessions_db_path(),
        "telemetry": telemetry_store.db_path(),
        "command-cache": command_cache_db_path(),
        "claude-flags": claude_flags_file(),
    }

    results = run_all_checks(
        installed_version=__version__,
        settings_path=settings_path,
        bundle_path=bundle_path,
        skills_source_dir=skills_source_dir,
        skills_target_dir=skills_target_dir,
        env=env_vars,
        skip_pypi=args.no_pypi,
        store_paths=store_paths,
    )

    if args.drift or getattr(args, "mode", None) == "drift":
        muted = set(doctor_mutes.load_mutes(mutes_path))
        unmuted = filter_unmuted_issues(results, muted)
        report = format_drift_report(unmuted, muted_count=len(muted))
        if report:
            print(report)
        return 1 if unmuted else 0

    print(format_results(results))

    any_issue = any(r.status in (Status.WARN, Status.FAIL) for r in results)
    return 1 if any_issue else 0


# ---------- shell install / uninstall ----------


def _cmd_shell_install(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.shell_install import RCAction, install_all

    rc_paths = _resolve_rc_paths(args)
    results = install_all(rc_paths, apply=args.apply)

    for r in results:
        print(f"  {r.path}: {r.message}")

    if not args.apply:
        print("\nDry run — re-run with --apply to write changes")
    else:
        modified = [r for r in results if r.action in (RCAction.ADDED, RCAction.REPLACED)]
        if modified:
            print(f"\nShell function installed in {len(modified)} file(s).")
            print("Reload your shell or run: source ~/.bashrc  (or ~/.zshrc)")

    return 0


def _cmd_shell_uninstall(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.shell_install import RCAction, uninstall_all

    rc_paths = _resolve_rc_paths(args)
    results = uninstall_all(rc_paths, apply=args.apply)

    for r in results:
        print(f"  {r.path}: {r.message}")

    if not args.apply:
        print("\nDry run — re-run with --apply to write changes")
    else:
        removed = [r for r in results if r.action == RCAction.REMOVED]
        if removed:
            print(f"\nShell function removed from {len(removed)} file(s).")

    return 0


def _resolve_rc_paths(args: argparse.Namespace) -> list[Path] | None:
    """Return the list of rc paths from --rc-file args, or None for defaults."""
    rc_files = getattr(args, "rc_file", None) or []
    if rc_files:
        return [Path(p) for p in rc_files]
    return None


# ---------- claude-md install / uninstall ----------


def _cmd_claude_md_install(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.claude_md_install import (
        MalformedBlockError,
        install_claude_md,
    )
    target = Path(args.target) if args.target else (Path.home() / ".claude" / "CLAUDE.md")
    try:
        result = install_claude_md(target, apply=args.apply)
    except MalformedBlockError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"  {result.path}: {result.message}")
    if not args.apply:
        print("\nDry run — re-run with --apply to write changes")
    return 0


def _cmd_claude_md_uninstall(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.claude_md_install import (
        MalformedBlockError,
        uninstall_claude_md,
    )
    target = Path(args.target) if args.target else (Path.home() / ".claude" / "CLAUDE.md")
    try:
        result = uninstall_claude_md(target, apply=args.apply)
    except MalformedBlockError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"  {result.path}: {result.message}")
    if not args.apply:
        print("\nDry run — re-run with --apply to write changes")
    return 0


# ---------- telemetry trim ----------


def _cmd_telemetry_trim(args: argparse.Namespace) -> int:
    from cccs_hooks.telemetry_trim import main as trim_main

    # Pass arguments through to the trim CLI
    argv: list[str] = []
    if args.max_size is not None:
        argv += ["--max-size", str(args.max_size)]
    if args.max_age_days is not None:
        argv += ["--max-age-days", str(args.max_age_days)]
    if getattr(args, "dry_run", False):
        argv.append("--dry-run")
    if getattr(args, "hooks_dir", None):
        argv += ["--hooks-dir", args.hooks_dir]

    return trim_main(argv)


def _cmd_telemetry_query(args: argparse.Namespace) -> int:
    from cccs_hooks.telemetry_query import main as query_main

    argv: list[str] = []
    if args.hook is not None:
        argv += ["--hook", args.hook]
    if args.decision is not None:
        argv += ["--decision", args.decision]
    if args.verdict is not None:
        argv += ["--verdict", args.verdict]
    if args.since is not None:
        argv += ["--since", args.since]
    if args.limit != 50:
        argv += ["--limit", str(args.limit)]
    if getattr(args, "hooks_dir", None):
        argv += ["--hooks-dir", args.hooks_dir]

    return query_main(argv)


# ---------- gc report ----------


def _cmd_gc_report(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.session_gc import build_report, format_report

    report = build_report(
        projects_dir=Path(args.projects_dir) if args.projects_dir else None,
        scheduler_dir=Path(args.scheduler_dir) if args.scheduler_dir else None,
        messages_root=Path(args.messages_root) if args.messages_root else None,
        session_env_dir=Path(args.session_env_dir) if args.session_env_dir else None,
    )
    print(format_report(report))
    return 0


# ---------- hooks run ----------


def _cmd_hooks_run(args: argparse.Namespace) -> int:
    module = importlib.import_module(HOOK_VERBS[args.hook])
    rc = module.main()
    return int(rc) if rc is not None else 0


# ---------- sessions migrate / list ----------


def _cmd_sessions_migrate(args: argparse.Namespace) -> int:
    from cc_session_tools.cli.migrate_sessions_db import DEFAULT_MUTES_FILE, DEFAULT_TAGS_DIR, run_migration
    from cc_session_tools.lib import sessions_db
    from cc_session_tools.lib.roots import RootsConfigError, load_session_roots

    db_path = Path(args.sessions_db) if args.sessions_db else sessions_db.default_db_path()
    tags_dir = Path(args.tags_dir) if args.tags_dir else DEFAULT_TAGS_DIR
    mutes_file = Path(args.mutes_file) if args.mutes_file else DEFAULT_MUTES_FILE

    try:
        roots = load_session_roots()
    except RootsConfigError as e:
        print(str(e), file=sys.stderr)
        return 1

    backup_dir = sessions_db.default_db_path().parent / "migration-backups"
    return run_migration(
        dry_run=args.dry_run, db_path=db_path, tags_dir=tags_dir,
        mutes_file=mutes_file, roots=roots, backup_dir=backup_dir,
    )


def _cmd_sessions_list(args: argparse.Namespace) -> int:
    from cc_session_tools.lib import sessions_db

    db_path = Path(args.sessions_db) if args.sessions_db else None
    rows = sessions_db.list_sessions(path=db_path)
    if not rows:
        print("No sessions recorded in sessions.db.")
        return 0

    rows = sorted(rows, key=lambda r: r.start_date, reverse=True)
    if args.json:
        import json as _json
        print(_json.dumps([
            {
                "basename": r.basename,
                "project_dir": str(r.project_dir),
                "start_date": r.start_date,
                "last_opened": r.last_opened,
                "last_active": r.last_active,
            }
            for r in rows
        ]))
        return 0

    name_w = max(len(r.basename) for r in rows)
    for r in rows:
        print(
            f"{r.basename:<{name_w}}  "
            f"opened={_fmt_ts(r.last_opened)}  active={_fmt_ts(r.last_active)}  "
            f"{r.project_dir}"
        )
    return 0


def _fmt_ts(epoch: float) -> str:
    if not epoch:
        return "(never)"
    import datetime as _dt
    return _dt.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


# ---------- migrate ccsched ----------


def _cmd_migrate_ccsched(args: argparse.Namespace) -> int:
    from cc_session_tools.cli.migrate_ccsched import main as migrate_main

    argv: list[str] = []
    if args.old_dir:
        argv += ["--old-dir", args.old_dir]
    if args.backup_dir:
        argv += ["--backup-dir", args.backup_dir]
    if args.dry_run:
        argv.append("--dry-run")
    return migrate_main(argv)


# ---------- install-everything ----------


_INSTALL_STEPS: list[tuple[str, str]] = [
    ("1/5  Skills",          "skills"),
    ("2/5  Hooks",           "hooks"),
    ("3/5  Shell helpers",   "shell"),
    ("4/5  Global CLAUDE.md", "claude-md"),
    ("5/5  Health check",    "doctor"),
]


def _cmd_install_everything(args: argparse.Namespace) -> int:
    """Run all install steps in sequence, then health-check."""
    apply: bool = args.apply
    no_pypi: bool = args.no_pypi

    steps: list[tuple[str, str, object]] = [
        (
            "1/5  Skills",
            "skills",
            argparse.Namespace(source=None, target=None, apply=apply, force=False),
        ),
        (
            "2/5  Hooks",
            "hooks",
            argparse.Namespace(
                source=None,
                hook=None,
                target=str(Path.home() / ".claude" / "settings.json"),
                apply=apply,
            ),
        ),
        (
            "3/5  Shell helpers",
            "shell",
            argparse.Namespace(apply=apply, rc_file=None),
        ),
        (
            "4/5  Global CLAUDE.md",
            "claude-md",
            argparse.Namespace(target=None, apply=apply),
        ),
    ]

    dispatch: dict[str, object] = {
        "skills": _cmd_skills_install,
        "hooks": _cmd_hooks_install,
        "shell": _cmd_shell_install,
        "claude-md": _cmd_claude_md_install,
    }

    overall_rc = 0
    for label, key, step_args in steps:
        print(f"\n=== {label} ===")
        rc = dispatch[key](step_args)  # type: ignore[operator]
        if rc != 0:
            overall_rc = rc

    print("\n=== 5/5  Health check ===")
    _cmd_doctor(
        argparse.Namespace(
            settings=None,
            skills_dir=None,
            no_pypi=no_pypi,
            drift=False,
            mute=None,
            unmute=None,
            list_mutes=False,
            mutes_file=None,
            mode=None,
        )
    )

    if not apply:
        print("\nDry run complete — re-run with --apply to write all changes.")
    else:
        print("\nAll install steps complete.")
    return overall_rc


# ---------- arg parser ----------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ccst",
        description="Claude Code Session Tools umbrella CLI",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="noun", metavar="<noun>")

    # ---- hooks ----
    hooks_parser = sub.add_parser("hooks", help="Hook management commands")
    hooks_sub = hooks_parser.add_subparsers(dest="verb", metavar="<verb>")
    hooks_sub.required = True

    # hooks install
    install_parser = hooks_sub.add_parser(
        "install",
        help="Merge hook entries from the bundled set (or --source) into a target settings.json",
    )
    install_parser.add_argument(
        "--source",
        default=None,
        metavar="PATH",
        help="Source settings.json to read hook entries from (default: bundled hooks-bundle.json)",
    )
    install_parser.add_argument(
        "--hook",
        default=None,
        metavar="NAME",
        help="Install only the named hook from the bundle (e.g. session-tag)",
    )
    install_parser.add_argument(
        "--target",
        default=str(Path.home() / ".claude/settings.json"),
        metavar="PATH",
        help="Target settings.json to merge hooks into",
    )
    install_parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (default: dry run)",
    )

    # hooks uninstall
    uninstall_parser = hooks_sub.add_parser(
        "uninstall",
        help="Remove ccst hook entries from a target settings.json",
    )
    uninstall_parser.add_argument(
        "--hook",
        default=None,
        metavar="NAME",
        help="Remove only the named hook (e.g. session-tag); default: remove all ccst hooks",
    )
    uninstall_parser.add_argument(
        "--target",
        default=str(Path.home() / ".claude/settings.json"),
        metavar="PATH",
        help="Target settings.json to remove hooks from",
    )
    uninstall_parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (default: dry run)",
    )

    # hooks run
    run_parser = hooks_sub.add_parser(
        "run",
        help="Run a Claude Code hook by name (reads event payload from stdin)",
    )
    run_parser.add_argument(
        "hook",
        choices=sorted(HOOK_VERBS),
        metavar="<name>",
        help="Hook to run: " + ", ".join(sorted(HOOK_VERBS)),
    )

    # ---- skills ----
    skills_parser = sub.add_parser("skills", help="Skill management commands")
    skills_sub = skills_parser.add_subparsers(dest="verb", metavar="<verb>")
    skills_sub.required = True

    # skills install
    skills_install_parser = skills_sub.add_parser(
        "install",
        help="Symlink bundled skills into ~/.claude/skills/ (dry run by default)",
    )
    skills_install_parser.add_argument(
        "--source",
        default=None,
        metavar="DIR",
        help="Source skills/ directory (default: bundled skills/)",
    )
    skills_install_parser.add_argument(
        "--target",
        default=None,
        metavar="DIR",
        help="Target directory for symlinks (default: ~/.claude/skills/)",
    )
    skills_install_parser.add_argument(
        "--apply",
        action="store_true",
        help="Create symlinks (default: dry run)",
    )
    skills_install_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing non-symlink files (moves them to <name>.bak-<timestamp>)",
    )

    # skills uninstall
    skills_uninstall_parser = skills_sub.add_parser(
        "uninstall",
        help="Remove bundled skill symlinks from ~/.claude/skills/ (dry run by default)",
    )
    skills_uninstall_parser.add_argument(
        "--skill",
        default=None,
        metavar="NAME",
        help="Remove only the named skill; default: remove all bundled skill symlinks",
    )
    skills_uninstall_parser.add_argument(
        "--target",
        default=None,
        metavar="DIR",
        help="Target directory (default: ~/.claude/skills/)",
    )
    skills_uninstall_parser.add_argument(
        "--apply",
        action="store_true",
        help="Remove symlinks (default: dry run)",
    )
    skills_uninstall_parser.add_argument(
        "--force",
        action="store_true",
        help="Remove even if the path is not a symlink",
    )

    # ---- doctor ----
    doctor_parser = sub.add_parser(
        "doctor",
        help="Health-check: PATH, env vars, settings.json, hooks, skills, version drift",
    )
    doctor_parser.add_argument(
        "--settings",
        default=None,
        metavar="PATH",
        help="settings.json path (default: ~/.claude/settings.json)",
    )
    doctor_parser.add_argument(
        "--skills-dir",
        default=None,
        metavar="DIR",
        help="Skills target directory (default: ~/.claude/skills/)",
    )
    doctor_parser.add_argument(
        "--no-pypi",
        action="store_true",
        help="Skip the PyPI version-drift check",
    )
    doctor_parser.add_argument(
        "--drift",
        action="store_true",
        help="Drift-monitor mode: print only un-muted WARN/FAIL, exit 1 if any",
    )
    doctor_parser.add_argument(
        "--mute",
        metavar="NAME",
        default=None,
        help="Mute a check by name so --drift ignores it",
    )
    doctor_parser.add_argument(
        "--unmute",
        metavar="NAME",
        default=None,
        help="Un-mute a previously muted check",
    )
    doctor_parser.add_argument(
        "--list-mutes",
        action="store_true",
        help="List all muted check names",
    )
    doctor_parser.add_argument(
        "--mutes-file",
        metavar="PATH",
        default=None,
        help="Mute-store sessions.db path (default: ~/.local/share/claude/sessions.db, "
             "or $CCST_SESSIONS_DIR)",
    )
    doctor_parser.add_argument(
        "mode",
        nargs="?",
        choices=["drift"],
        default=None,
        help=argparse.SUPPRESS,
    )

    # ---- shell ----
    shell_parser = sub.add_parser("shell", help="Shell function management commands")
    shell_sub = shell_parser.add_subparsers(dest="verb", metavar="<verb>")
    shell_sub.required = True

    shell_install_parser = shell_sub.add_parser(
        "install",
        help="Add the ccl() wrapper function to ~/.bashrc and ~/.zshrc (dry run by default)",
    )
    shell_install_parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (default: dry run)",
    )
    shell_install_parser.add_argument(
        "--rc-file",
        action="append",
        metavar="PATH",
        help="RC file to modify (may repeat; default: ~/.bashrc and ~/.zshrc)",
    )

    shell_uninstall_parser = shell_sub.add_parser(
        "uninstall",
        help="Remove the ccl() block from ~/.bashrc and ~/.zshrc (dry run by default)",
    )
    shell_uninstall_parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (default: dry run)",
    )
    shell_uninstall_parser.add_argument(
        "--rc-file",
        action="append",
        metavar="PATH",
        help="RC file to modify (may repeat; default: ~/.bashrc and ~/.zshrc)",
    )

    # ---- telemetry ----
    telemetry_parser = sub.add_parser("telemetry", help="Telemetry management commands")
    telemetry_sub = telemetry_parser.add_subparsers(dest="verb", metavar="<verb>")
    telemetry_sub.required = True

    telemetry_trim_parser = telemetry_sub.add_parser(
        "trim",
        help="Trim telemetry.db by size and/or age",
    )
    telemetry_trim_parser.add_argument(
        "--max-size",
        type=float,
        metavar="MB",
        help="Delete the oldest rows until the DB is under this size in MB (lossy)",
    )
    telemetry_trim_parser.add_argument(
        "--max-age-days",
        type=int,
        metavar="N",
        help="Delete rows older than N days",
    )
    telemetry_trim_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be done without making changes",
    )
    telemetry_trim_parser.add_argument(
        "--hooks-dir",
        default=None,
        metavar="DIR",
        help="telemetry.db directory (default: CCCS_HOOKS_DIR or ~/.local/share/claude/)",
    )

    telemetry_query_parser = telemetry_sub.add_parser(
        "query",
        help="Query recent hook fires from telemetry.db's telemetry_events table",
    )
    telemetry_query_parser.add_argument(
        "--hook", default=None, metavar="NAME", help="Filter by exact hook name",
    )
    telemetry_query_parser.add_argument(
        "--decision", default=None, choices=["allow", "deny", "annotate"],
        help="Filter by decision",
    )
    telemetry_query_parser.add_argument(
        "--verdict", default=None, metavar="VERDICT",
        help="Filter by exact verdict text (e.g. safe, suspicious, dangerous)",
    )
    telemetry_query_parser.add_argument(
        "--since", default=None, metavar="DURATION",
        help="Only events at or after now-DURATION, e.g. 1h, 30m, 2d, 1w",
    )
    telemetry_query_parser.add_argument(
        "--limit", type=int, default=50, metavar="N", help="Max rows to print (default: 50)",
    )
    telemetry_query_parser.add_argument(
        "--hooks-dir", default=None, metavar="DIR",
        help="telemetry.db directory (default: CCCS_HOOKS_DIR or ~/.local/share/claude/)",
    )

    # ---- gc ----
    gc_parser = sub.add_parser("gc", help="Garbage-collection reports for per-session-uuid stores")
    gc_sub = gc_parser.add_subparsers(dest="verb", metavar="<verb>")
    gc_sub.required = True

    gc_report_parser = gc_sub.add_parser(
        "report",
        help=(
            "Report orphaned per-session-uuid entries across the scheduler, "
            "messaging, and session-env stores. Report-only — never deletes "
            "or modifies anything."
        ),
    )
    gc_report_parser.add_argument(
        "--projects-dir",
        default=None,
        metavar="PATH",
        help="Transcript projects directory (default: ~/.claude/projects/)",
    )
    gc_report_parser.add_argument(
        "--scheduler-dir",
        default=None,
        metavar="PATH",
        help="Scheduler directory (default: from CC_SCHEDULER_DIR or ~/.claude/cc-scheduler/)",
    )
    gc_report_parser.add_argument(
        "--messages-root",
        default=None,
        metavar="PATH",
        help="Messaging store root (default: from CCST_MESSAGES_ROOT or ~/.claude/cc-messages/)",
    )
    gc_report_parser.add_argument(
        "--session-env-dir",
        default=None,
        metavar="PATH",
        help="Session-env directory (default: ~/.claude/session-env/)",
    )

    # ---- sessions ----
    sessions_parser = sub.add_parser("sessions", help="sessions.db management commands")
    sessions_sub = sessions_parser.add_subparsers(dest="verb", metavar="<verb>")
    sessions_sub.required = True

    sessions_migrate_parser = sessions_sub.add_parser(
        "migrate",
        help=(
            "One-shot migration of the flat tag cache, activity sentinels, and "
            "cc-doctor-mutes.json into sessions.db. Non-destructive — never "
            "deletes old files automatically."
        ),
    )
    sessions_migrate_parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be migrated without writing anything.",
    )
    sessions_migrate_parser.add_argument(
        "--sessions-db", default=None, metavar="PATH",
        help="Destination sessions.db path (default: from CCST_SESSIONS_DIR or "
             "~/.local/share/claude/sessions.db)",
    )
    sessions_migrate_parser.add_argument(
        "--tags-dir", default=None, metavar="PATH",
        help="Source flat tags dir (default: ~/.cache/claude/session-tags/)",
    )
    sessions_migrate_parser.add_argument(
        "--mutes-file", default=None, metavar="PATH",
        help="Source doctor-mutes JSON file (default: ~/.claude/cc-doctor-mutes.json)",
    )

    sessions_list_parser = sessions_sub.add_parser(
        "list",
        help="List all sessions recorded in sessions.db (debug/inspection).",
    )
    sessions_list_parser.add_argument(
        "--sessions-db", default=None, metavar="PATH",
        help="sessions.db path override (default: from CCST_SESSIONS_DIR)",
    )
    sessions_list_parser.add_argument(
        "--json", action="store_true",
        help="Output as a JSON array instead of a formatted table.",
    )

    # ---- migrate ----
    migrate_parser = sub.add_parser("migrate", help="One-shot data-store migrations")
    migrate_sub = migrate_parser.add_subparsers(dest="verb", metavar="<verb>")
    migrate_sub.required = True
    m_ccsched = migrate_sub.add_parser(
        "ccsched",
        help="Migrate ccsched flat-file stores into ccsched.db (non-destructive)")
    m_ccsched.add_argument("--old-dir", default=None, metavar="PATH")
    m_ccsched.add_argument("--backup-dir", default=None, metavar="PATH")
    m_ccsched.add_argument("--dry-run", action="store_true")

    # ---- claude-md ----
    cmd_parser = sub.add_parser("claude-md", help="Manage the global CLAUDE.md messaging block")
    cmd_sub = cmd_parser.add_subparsers(dest="verb", metavar="<verb>")
    cmd_sub.required = True
    cmd_install = cmd_sub.add_parser("install", help="Add/update the messaging block (dry run by default)")
    cmd_install.add_argument("--target", default=None, metavar="PATH",
                             help="CLAUDE.md path (default: ~/.claude/CLAUDE.md)")
    cmd_install.add_argument("--apply", action="store_true", help="Write changes (default: dry run)")
    cmd_uninstall = cmd_sub.add_parser("uninstall", help="Remove the messaging block (dry run by default)")
    cmd_uninstall.add_argument("--target", default=None, metavar="PATH",
                               help="CLAUDE.md path (default: ~/.claude/CLAUDE.md)")
    cmd_uninstall.add_argument("--apply", action="store_true", help="Write changes (default: dry run)")

    # ---- install-everything ----
    ie_parser = sub.add_parser(
        "install-everything",
        help=(
            "Run all install steps (skills, hooks, shell, claude-md) then health-check. "
            "Dry run by default; pass --apply to write changes."
        ),
    )
    ie_parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (default: dry run)",
    )
    ie_parser.add_argument(
        "--no-pypi",
        action="store_true",
        help="Skip the PyPI version-drift check in the final health-check",
    )

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.noun is None:
        parser.print_help(sys.stderr)
        sys.exit(1)

    if args.noun == "hooks":
        if args.verb == "install":
            sys.exit(_cmd_hooks_install(args))
        if args.verb == "uninstall":
            sys.exit(_cmd_hooks_uninstall(args))
        if args.verb == "run":
            sys.exit(_cmd_hooks_run(args))

    if args.noun == "skills":
        if args.verb == "install":
            sys.exit(_cmd_skills_install(args))
        if args.verb == "uninstall":
            sys.exit(_cmd_skills_uninstall(args))

    if args.noun == "doctor":
        sys.exit(_cmd_doctor(args))

    if args.noun == "shell":
        if args.verb == "install":
            sys.exit(_cmd_shell_install(args))
        if args.verb == "uninstall":
            sys.exit(_cmd_shell_uninstall(args))

    if args.noun == "telemetry":
        if args.verb == "trim":
            sys.exit(_cmd_telemetry_trim(args))
        if args.verb == "query":
            sys.exit(_cmd_telemetry_query(args))

    if args.noun == "gc":
        if args.verb == "report":
            sys.exit(_cmd_gc_report(args))

    if args.noun == "sessions":
        if args.verb == "migrate":
            sys.exit(_cmd_sessions_migrate(args))
        if args.verb == "list":
            sys.exit(_cmd_sessions_list(args))

    if args.noun == "migrate":
        if args.verb == "ccsched":
            sys.exit(_cmd_migrate_ccsched(args))

    if args.noun == "claude-md":
        if args.verb == "install":
            sys.exit(_cmd_claude_md_install(args))
        if args.verb == "uninstall":
            sys.exit(_cmd_claude_md_uninstall(args))

    if args.noun == "install-everything":
        sys.exit(_cmd_install_everything(args))


if __name__ == "__main__":
    main()
