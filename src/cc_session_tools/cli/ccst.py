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
  shell install                  Write the ccl() wrapper function to a fragment
                                 file in ~/.shellrc.d/ (or --fragments-dir).
  shell uninstall                Remove that ccl() fragment file.
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
  gc prune                       Delete the orphaned entries `gc report` finds, gated
                                 by --execute (default: dry run) and a --min-age-hours
                                 floor (default 24) so a brand-new session's own state
                                 is never mid-race deleted.
  pdata add                      Insert a new record into a project's SQLite data store (see
                                 ccst pdata --help for the full records/schema subcommand set).
  pdata reconcile-session-output Backfill the session-output index from cc-sessions/*/out/ on
                                 disk for one project (--project NAME) or every discovered
                                 project (--all-projects). Provisioned as a 7-day ccsched job by
                                 `ccst ccsched-jobs install` — see ccst ccsched-jobs --help.
                                 --schema-only bootstraps the schema/index for --project without
                                 scanning or registering files.
  pdata verify                   Run the integrity-check backstop (row-count parity, file_path
                                 resolution, suspicious double-updates) for --project NAME or
                                 --all-projects and persist the result for ccst doctor.
  pdata dump                     Publish the current local DB state to
                                 .pdata-db-dump/latest.sql for --project NAME or
                                 --all-projects. Refuses (unless --force) to overwrite an
                                 unresolved cross-machine fork.
  pdata rehydrate                Fast-forward the local DB from the published dump for
                                 --project NAME or --all-projects, if it's ahead. --force
                                 fast-forwards even over a diverged local revision.
  pdata resolve                  Diagnose (default, prints the diff) or apply per-record
                                 --choice ID=local|dump resolutions for a cross-machine fork.
  pdata sync-check               Do whichever of rehydrate/dump each project needs, in that
                                 order, for --project NAME or --all-projects. The unattended
                                 trigger behind the hourly pdata-sync-hourly ccsched job:
                                 skips an unchanged republish and never overrides a fork.
  machine-identity show          Print this laptop's resolved machine id and whether it's
                                 confirmed.
  machine-identity confirm       Store this laptop's confirmed machine id (--name NAME), used
                                 by pdata's cross-machine vector clock.
  migrate ccsched                Migrate ccsched flat-file stores into ccsched.db
                                 (verify + tar-backup old files before removal).
  migrate ccmsg                  Migrate the flat-file message store into ccmsg.db
                                 (verify + tar-backup old files before removal).
  migrate telemetry              Migrate fires.jsonl (+ rotated slots) into
                                 telemetry.db (verify + tar-backup before removal).
  migrate all                    Run every one-shot migration above in sequence.
                                 Run from a plain terminal, not inside Claude Code
                                 — the delete steps are blocked by bash-hard-deny.
  claude-md install              Add/update the inter-session-messaging block in
                                 ~/.claude/CLAUDE.md.
  claude-md uninstall            Remove the messaging block from CLAUDE.md.
  ccsched-jobs install           Register CCST's bundled ccsched jobs (see
                                 lib/scheduler/bundled_jobs.py) if not already present.
                                 Dry run by default; pass --apply to register. An
                                 already-registered job whose fields no longer match its
                                 bundled definition, or that has been disabled, is reported
                                 as such and left untouched, never overwritten.
  install-everything             Run all install steps (skills, hooks, shell,
                                 claude-md, scheduled jobs) then health-check.
                                 Dry run by default; pass --apply to write changes.
  context-override [on|off|status]  Toggle the /context-override flag for the current
                                 session (default: on). See ccst hooks run
                                 context-window-warning.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import importlib
import json
import os
import sys
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO, cast

from cc_session_tools import __version__
from cc_session_tools.hooks_install import (
    load_json,
    merge_hook_settings,
    prune_stale_hooks,
    write_json_atomic,
)
from cc_session_tools.lib import machine_identity
from cc_session_tools.lib.hook_registry import HOOK_DESCRIPTIONS, HOOK_VERBS

if TYPE_CHECKING:
    # Type-only, so the handler's own lazy `from ... import sync_check` (this file's convention:
    # pdata modules are imported inside the handler that needs them, never at module scope) is
    # still the only runtime import.
    from cc_session_tools.lib.pdata.resolve import RecordPayload
    from cc_session_tools.lib.pdata.sync_check import SyncCheckResult


# ---------- path discovery ----------


def _discover_source_dir() -> Path:
    """Return the bundled skills/ directory.

    skills/ is packaged inside cc_session_tools (declared as package-data in
    pyproject.toml), so it sits at the same relative depth from this module
    whether cc_session_tools is an editable source checkout or an installed
    wheel: src/cc_session_tools/cli/ccst.py -> ../skills. No install-location
    fallback is needed - a fallback here would just mask a broken install.
    """
    candidate = Path(__file__).resolve().parent.parent / "skills"
    if candidate.is_dir():
        return candidate
    raise FileNotFoundError(
        "Cannot locate bundled skills/ directory. "
        "Run from the source tree or use --source to specify the path explicitly."
    )


def _discover_bundle() -> Path:
    """Return the bundled config/hooks-bundle.json path.

    config/ is packaged inside cc_session_tools the same way skills/ is (see
    _discover_source_dir) - src/cc_session_tools/cli/ccst.py -> ../config.
    """
    candidate = Path(__file__).resolve().parent.parent / "config" / "hooks-bundle.json"
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(
        "Cannot locate bundled config/hooks-bundle.json. "
        "Run from the source tree or use --source to specify the path explicitly."
    )


def _discover_prompts_dir() -> Path:
    """Return the bundled prompts/ directory (pdata-migration follow-up prompts).

    prompts/ is packaged inside cc_session_tools the same way skills/ and config/ are (see
    _discover_source_dir) - src/cc_session_tools/cli/ccst.py -> ../prompts. No install-location
    fallback, same reasoning as the other two: a fallback here would just mask a broken install.
    """
    candidate = Path(__file__).resolve().parent.parent / "prompts"
    if candidate.is_dir():
        return candidate
    raise FileNotFoundError(
        "Cannot locate bundled prompts/ directory. Run from the source tree or reinstall ccst."
    )


def _print_migration_prompt_reminders(
    project_root: Path, *labeled_filenames: tuple[str, str]
) -> None:
    """Print one "<label>: <path>" reminder line per (label, filename) pair, for the bundled
    prompts/ directory, followed by a line telling the user to run it in a fresh Claude Code
    session started in project_root.

    Each prompt's own "Run it via" section already spells out the `cd <project> && claude -p
    ...` invocation (see e.g. pdata-migration-skills-update.md) - that only works if the
    session's cwd is the project root, since every prompt's Step 1 checks for it and aborts
    otherwise. Printing the reminder without saying so reads as "open this file", which invites
    running it inline in whatever session called `ccst pdata init` - the wrong cwd entirely.

    Best-effort, deliberately swallowing a missing prompts/ directory: these reminders are a
    nicety layered on top of an otherwise-already-completed classification or migration, not
    the operation itself — a broken/partial install must never turn a successful --write (rows
    already written, files already cut over) or dry-run (proposal already written) into a
    reported failure just because this follow-up reminder couldn't be printed.
    """
    try:
        prompts_dir = _discover_prompts_dir()
    except FileNotFoundError as exc:
        print(f"({exc})", file=sys.stderr)
        return
    for label, filename in labeled_filenames:
        print(f"{label}: {prompts_dir / filename}")
        print(
            f"  Run in a new Claude Code session started in {project_root} (its own "
            f"\"Run it via\" section has the exact command) - not in this session."
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

        # WRONG_TARGET: dest is a symlink we manage (created by a previous
        # install), so repointing it is safe without --force — no user data
        # at risk, unlike NON_SYMLINK_EXISTS above.

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

    # Prune before merging: a rename (old name removed, new name added in the
    # same release) must drop the dead entry in the same pass that adds its
    # replacement, or the upgrade leaves both registered and the dead one
    # fires on every event it was bound to.
    pruned, removals = prune_stale_hooks(target)
    merged, additions = merge_hook_settings(source_settings=source, target_settings=pruned)

    inventory = _bundle_inventory(source)
    added_keys = {(a.event, a.matcher, a.command) for a in additions}
    _print_hooks_install_table(inventory, added_keys)

    if removals:
        print("\nStale entries in settings.json naming a hook this CCST no longer has:")
        for r in removals:
            matcher_label = f" [{r.matcher}]" if r.matcher else ""
            print(f"  - remove  {r.event}{matcher_label}: {r.command}")

    if not additions and not removals:
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
        LegacyMigrationPaths,
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

    from cc_session_tools.lib.paths import data_home
    from cc_session_tools.cli.migrate_ccmsg import DEFAULT_OLD_ROOT as ccmsg_old_root
    from cc_session_tools.cli.migrate_ccsched import DEFAULT_OLD_DIR as ccsched_old_dir
    from cc_session_tools.cli.migrate_sessions_db import DEFAULT_MUTES_FILE, DEFAULT_TAGS_DIR
    from cc_session_tools.cli.migrate_telemetry import DEFAULT_OLD_SOURCE_DIR as telemetry_old_dir

    legacy_migration_paths = LegacyMigrationPaths(
        ccmsg_old_root=ccmsg_old_root,
        ccsched_old_dir=ccsched_old_dir,
        tags_dir=DEFAULT_TAGS_DIR,
        mutes_file=DEFAULT_MUTES_FILE,
        telemetry_old_dir=telemetry_old_dir,
        data_home=data_home(),
    )

    from cc_session_tools.lib.pdata import verify as _pdata_verify
    from cc_session_tools.lib.pdata.init_paths import default_projects_root
    from cc_session_tools.lib import install_sync

    results = run_all_checks(
        installed_version=__version__,
        settings_path=settings_path,
        bundle_path=bundle_path,
        skills_source_dir=skills_source_dir,
        skills_target_dir=skills_target_dir,
        env=env_vars,
        skip_pypi=args.no_pypi,
        store_paths=store_paths,
        legacy_migration_paths=legacy_migration_paths,
        projects_root=default_projects_root(),
        pdata_verify_projects=_pdata_verify.discover_projects(),
        sessions_db_path=store_paths["sessions"],
        synced_version=install_sync.get_synced_version(),
        failed_attempt=install_sync.get_failed_attempt(),
    )

    if args.drift or getattr(args, "mode", None) == "drift":
        muted = set(doctor_mutes.load_mutes(mutes_path))
        unmuted = filter_unmuted_issues(results, muted)
        report = format_drift_report(unmuted, muted_count=len(muted))
        if report:
            print(report)
        return 1 if unmuted else 0

    print(format_results(results, show_all=getattr(args, "all", False)))

    any_issue = any(r.status in (Status.WARN, Status.FAIL) for r in results)
    return 1 if any_issue else 0


# ---------- shell install / uninstall ----------


def _cmd_shell_install(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.shell_install import RCAction, install_all

    fragments_dirs = _resolve_fragment_dirs(args)
    results = install_all(fragments_dirs, apply=args.apply)

    for r in results:
        print(f"  {r.path}: {r.message}")

    if not args.apply:
        print("\nDry run — re-run with --apply to write changes")
    else:
        modified = [r for r in results if r.action in (RCAction.ADDED, RCAction.REPLACED)]
        if modified:
            print(f"\nShell function installed in {len(modified)} location(s).")
            print(
                "Make sure your shell rc sources it, e.g.:\n"
                '  for f in ~/.shellrc.d/*.sh; do [ -r "$f" ] && source "$f"; done'
            )
            print("Then reload your shell or run: source ~/.bashrc  (or ~/.zshrc)")

    return 0


def _cmd_shell_uninstall(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.shell_install import RCAction, uninstall_all

    fragments_dirs = _resolve_fragment_dirs(args)
    results = uninstall_all(fragments_dirs, apply=args.apply)

    for r in results:
        print(f"  {r.path}: {r.message}")

    if not args.apply:
        print("\nDry run — re-run with --apply to write changes")
    else:
        removed = [r for r in results if r.action == RCAction.REMOVED]
        if removed:
            print(f"\nShell function removed from {len(removed)} location(s).")

    return 0


def _resolve_fragment_dirs(args: argparse.Namespace) -> list[Path] | None:
    """Return the list of fragments dirs from --fragments-dir args, or None for defaults."""
    fragments_dirs = getattr(args, "fragments_dir", None) or []
    if fragments_dirs:
        return [Path(p) for p in fragments_dirs]
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
        sessions_dir=Path(args.sessions_dir) if args.sessions_dir else None,
    )
    print(format_report(report))
    return 0


def _cmd_gc_prune(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.session_gc import format_prune_report, prune

    result = prune(
        min_age_hours=args.min_age_hours,
        execute=args.execute,
        only=frozenset(args.only) if args.only else None,
        projects_dir=Path(args.projects_dir) if args.projects_dir else None,
        scheduler_dir=Path(args.scheduler_dir) if args.scheduler_dir else None,
        messages_root=Path(args.messages_root) if args.messages_root else None,
        session_env_dir=Path(args.session_env_dir) if args.session_env_dir else None,
        sessions_dir=Path(args.sessions_dir) if args.sessions_dir else None,
    )
    print(format_prune_report(result))
    return 1 if result.any_failed else 0


# ---------- pdata ----------


def _parse_field_assignment(raw: str) -> tuple[str, str | None]:
    """Parse "k=v" into (k, v). The literal token `null` (unquoted, lowercase) is interpreted
    as SQL NULL rather than the four-character string "null" - this is the documented way to
    clear/unset a field via --field (see check-tesco-shop-due's SKILL.md for a real caller that
    relies on it). A field genuinely needing to store the text "null" cannot be written via
    --field; there's no escape hatch for that case since --field has no quoting syntax at all.
    Raises ValueError on malformed input."""
    if "=" not in raw:
        raise ValueError(f"malformed --field assignment (want name=value): {raw!r}")
    name, value = raw.split("=", 1)
    if not name:
        raise ValueError(f"malformed --field assignment (want name=value): {raw!r}")
    return name, (None if value == "null" else value)


def _cmd_pdata_add(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import service

    try:
        fields = dict(_parse_field_assignment(raw) for raw in (args.field or []))
        record = service.add_record(
            project=args.project,
            record_group=args.group,
            content=args.content,
            file_path=args.file,
            fields=fields,
            created_at=args.created_at,
        )
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2
    print(record.id)
    return 0


def _parse_field_spec(raw: str) -> tuple[str, str]:
    """Parse "name:TYPE" into (name, TYPE). Raises ValueError on malformed input."""
    if ":" not in raw:
        raise ValueError(f"malformed --field spec (want name:TYPE): {raw!r}")
    name, sql_type = raw.split(":", 1)
    if not name or not sql_type:
        raise ValueError(f"malformed --field spec (want name:TYPE): {raw!r}")
    return name, sql_type


def _cmd_pdata_schema_add_field(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import service

    try:
        field_name, sql_type = _parse_field_spec(args.field)
        service.schema_add_field(
            project=args.project,
            record_group=args.group,
            field_name=field_name,
            sql_type=sql_type,
            description=args.description,
            default=args.default,
        )
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2
    print(f"added field {field_name!r} ({sql_type}) to {args.group!r}")
    return 0


def _cmd_pdata_schema_list(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import service

    try:
        groups = service.schema_list(project=args.project)
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2
    if not groups:
        print(f"No record_groups found in project {args.project!r}.")
        return 0
    name_w = max(len(str(g["record_group"])) for g in groups)
    for g in groups:
        ext = "yes" if g["has_extension_table"] else "no"
        max_updated_at = g["max_updated_at"]
        updated = _fmt_ts(cast(float, max_updated_at)) if max_updated_at else "(never)"
        print(f"{str(g['record_group']):<{name_w}}  rows={g['row_count']:<6} "
              f"ext={ext:<3} updated={updated}")
    return 0


def _cmd_pdata_schema_show(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import service

    try:
        columns = service.schema_show(project=args.project, record_group=args.group)
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2
    for c in columns:
        type_label = c["type"] or ""
        desc = c["description"] or ""
        print(f"{c['source']:<9} {c['name']:<20} {type_label:<10} {desc}")
    return 0


def _cmd_pdata_get(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import formatting, service

    try:
        record = service.get_record(
            project=args.project, record_id=args.id, include_deleted=args.include_deleted,
        )
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2
    if record is None:
        print(f"ccst pdata: record not found: {args.id}", file=sys.stderr)
        return 1
    print(formatting.render([service.record_to_dict(record)], fmt="table"))
    return 0


def _cmd_pdata_list(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import formatting, service

    try:
        records = service.list_records(
            project=args.project, record_group=args.group,
            since=args.since, until=args.until, limit=args.limit,
            include_deleted=args.include_deleted,
        )
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2
    print(formatting.render([service.record_to_dict(r) for r in records], fmt=args.format))
    return 0


def _cmd_pdata_query(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import formatting, service

    try:
        records = service.query_records(
            project=args.project, record_group=args.group,
            where=args.where or [], limit=args.limit,
            include_deleted=args.include_deleted,
        )
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2
    print(formatting.render([service.record_to_dict(r) for r in records], fmt=args.format))
    return 0


def _cmd_pdata_update(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import formatting, service

    try:
        fields = dict(_parse_field_assignment(raw) for raw in (args.field or []))
        record = service.update_record(
            project=args.project, record_id=args.id, expected_version=args.version,
            content=args.content, file_path=args.file, fields=fields,
        )
    except service.RecordNotFoundError:
        print(f"ccst pdata: record not found: {args.id}", file=sys.stderr)
        return 1
    except service.VersionConflictError as exc:
        print(formatting.render_conflict_diff(exc.current, exc.attempted, fmt=args.format))
        return 3
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2
    print(f"updated record {record.id} (version {record.version})")
    return 0


def _cmd_pdata_delete(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import formatting, service

    try:
        service.delete_record(
            project=args.project, record_id=args.id, expected_version=args.version,
        )
    except service.RecordNotFoundError:
        print(f"ccst pdata: record not found: {args.id}", file=sys.stderr)
        return 1
    except service.VersionConflictError as exc:
        print(formatting.render_conflict_diff(exc.current, exc.attempted, fmt="table"))
        return 3
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2
    print(f"deleted record {args.id}")
    return 0


def _cmd_pdata_restore(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import service

    try:
        service.restore_record(project=args.project, record_id=args.id)
    except service.RecordNotFoundError:
        print(f"ccst pdata: record not found (or not deleted): {args.id}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2
    print(f"restored record {args.id}")
    return 0


def _cmd_pdata_init(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import init_paths, init_service, write_log

    rehearse = Path(args.rehearse) if args.rehearse else None

    if not args.write:
        try:
            result = init_service.dry_run(project=args.project, rehearse=rehearse)
        except ValueError as exc:
            print(f"ccst pdata: {exc}", file=sys.stderr)
            return 2
        print(result.report)
        if result.would_adopt_from_dump:
            # No proposal was written and there's nothing to classify - the reminders below are
            # about updating project docs after a real classify/import cutover, which doesn't
            # apply here.
            return 0
        print(f"\nProposal: {result.proposal_path}")
        _print_migration_prompt_reminders(
            result.proposal_path.parent,
            ("After a successful --write, update project docs", "pdata-migration-claude-md-update.md"),
        )
        return 0

    try:
        project_root = init_paths.resolve_project_root(args.project, rehearse=rehearse)
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2

    with write_log.WriteLog(project_root):
        try:
            write_result = init_service.write(
                project=args.project, rehearse=rehearse, on_progress=print,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"ccst pdata: {exc}", file=sys.stderr)
            print(f"ERROR: {exc}")
            return 2

        if write_result.failure is not None:
            print("ccst pdata init: verification failed, nothing was cut over:", file=sys.stderr)
            for reason in write_result.failure.reasons:
                print(f"  - {reason}", file=sys.stderr)
            print(f"ERROR: verification failed ({len(write_result.failure.reasons)} reason(s))")
            return 1

        if write_result.adopted_from_dump:
            # A second machine's first-ever init for an already-migrated project - nothing was
            # classified or cut over here, so the ordinary "Wrote N records"/"Backup: ..."/
            # doc-update-reminders below would be actively misleading (they read as if nothing
            # happened, or that something went wrong, rather than "adoption succeeded").
            print(write_result.report)
            print(f"\nVerify: ccst pdata verify --project {args.project} --full")
            print("SUCCESS")
            return 0

        print(
            f"Wrote {len(write_result.created_record_ids)} record(s) across "
            f"{len(write_result.entries_written)} file(s)."
        )
        print(f"Backup: {write_result.backup_path}")
        for path in write_result.entries_written:
            print(f"  cut over: {path}")
        print()
        print(write_result.report)  # spec §7.1 step 4's diff report, for review
        print(f"\nVerify: ccst pdata verify --project {args.project} --full")
        _print_migration_prompt_reminders(
            project_root,
            ("Update project docs", "pdata-migration-claude-md-update.md"),
            ("Update consuming skills", "pdata-migration-skills-update.md"),
        )
        print("SUCCESS")
        return 0


def _cmd_pdata_reorganize(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import init_paths, reorganize, write_log

    try:
        project_root = init_paths.resolve_project_root(args.project, rehearse=None)
    except ValueError as exc:
        # Matches _cmd_pdata_init's own handling of the identical resolve_project_root() call -
        # a bad --project name (e.g. containing '/') must give the same clean exit-2 message
        # every other pdata command gives, not an uncaught traceback.
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2

    if not args.write:
        try:
            plan = reorganize.dry_run(
                project=args.project, project_root=project_root,
                folder=args.folder, strategy=args.strategy,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"ccst pdata: {exc}", file=sys.stderr)
            return 2
        for move in plan.moves:
            print(f"{move.old_relative} -> {move.new_relative}")
        for matched in plan.matched_records:
            print(f"  pdata record {matched.record.id} (group={matched.record.record_group}): "
                  f"file_path -> {matched.new_file_path}")
        for ref in plan.external_references:
            print(f"  external reference: {ref.file}:{ref.line_number}: {ref.line_text}")
        if not plan.moves:
            print(f"no files found directly under {args.folder}/")
        return 0

    # --write can move many files over a long-running, potentially flaky I/O path - durably
    # logged the same way `ccst pdata init --write` is, under this operation's own filename so
    # the two never truncate each other's log (see reorganize.REORGANIZE_WRITE_LOG_FILENAME).
    with write_log.WriteLog(project_root, log_filename=reorganize.REORGANIZE_WRITE_LOG_FILENAME):
        try:
            result = reorganize.write(
                project=args.project, project_root=project_root,
                folder=args.folder, strategy=args.strategy,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"ccst pdata: {exc}", file=sys.stderr)
            print(f"ERROR: {exc}")
            return 2

        if result.failure is not None:
            print("ccst pdata reorganize: failed, rolled back:", file=sys.stderr)
            for reason in result.failure.reasons:
                print(f"  - {reason}", file=sys.stderr)
            print(f"ERROR: failed, rolled back ({len(result.failure.reasons)} reason(s))")
            return 1

        print(f"Moved {len(result.plan.moves)} file(s) under {args.folder}/")
        print(f"Backup: {result.backup_path}")
        for ref in result.plan.external_references:
            print(f"  still needs manual review: {ref.file}:{ref.line_number}: {ref.line_text}")
        print("SUCCESS")
        return 0


def _cmd_pdata_rename_group(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import init_paths, naming, rename_group

    try:
        project_root = init_paths.resolve_project_root(args.project, rehearse=None)
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2

    if not args.write:
        try:
            plan = rename_group.dry_run(
                project=args.project, project_root=project_root, old=args.from_, new=args.to,
            )
        except ValueError as exc:
            print(f"ccst pdata: {exc}", file=sys.stderr)
            return 2
        print(f"record_group: {plan.old} -> {plan.new}")
        print(f"  {plan.row_count} row(s) in records to update")
        if plan.has_extension_table:
            print(f"  ext table: {naming.extension_table_name(plan.old)} -> "
                  f"{naming.extension_table_name(plan.new)}")
        else:
            print("  no extension table")
        if plan.manifest_entry_paths:
            n = len(plan.manifest_entry_paths)
            print(f"  {n} manifest entr{'y' if n == 1 else 'ies'} to update:")
            for path in plan.manifest_entry_paths:
                print(f"    {path}")
        else:
            print("  no matching manifest entries")
        return 0

    try:
        result = rename_group.write(
            project=args.project, project_root=project_root, old=args.from_, new=args.to,
        )
    except ValueError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 2

    if result.failure is not None:
        print("ccst pdata rename-group: failed:", file=sys.stderr)
        for reason in result.failure.reasons:
            print(f"  - {reason}", file=sys.stderr)
        return 1

    plan = result.plan
    print(f"Renamed record_group {plan.old!r} -> {plan.new!r}: {plan.row_count} row(s)")
    print(f"Backup: {result.backup_path}")
    n = len(plan.manifest_entry_paths)
    if n:
        print(f"{n} manifest entr{'y' if n == 1 else 'ies'} updated - remember to update any "
              f"skills/docs that still reference the old group name.")
    print("SUCCESS")
    return 0


def _cmd_pdata_reconcile_session_output(args: argparse.Namespace) -> int:
    from cc_session_tools.lib import roots
    from cc_session_tools.lib.pdata import session_output

    if args.schema_only and args.all_projects:
        print(
            "ccst pdata: --schema-only requires --project (not --all-projects)",
            file=sys.stderr,
        )
        return 2

    try:
        if args.all_projects:
            targets = session_output.discover_projects_with_sessions()
        else:
            root = session_output.find_project_root(args.project)
            if root is None:
                print(
                    f"ccst pdata: no project {args.project!r} found with a cc-sessions/ "
                    f"directory under $CLAUDE_SESSION_TOOLS_PROJ_ROOT",
                    file=sys.stderr,
                )
                return 1
            targets = [(args.project, root)]
    except roots.RootsConfigError as exc:
        print(f"ccst pdata: {exc}", file=sys.stderr)
        return 1

    if args.schema_only:
        # Bootstraps the session_tag column and the file_path partial index without scanning
        # cc-sessions/*/out/ — the fast path the pm-update-central-files skill's own AUTO item
        # calls before its per-file registration loop, so that loop's dedupe query runs against
        # the index instead of an unindexed scan (spec Goal G5). A full (non-schema-only)
        # reconcile also ensures the schema as a side effect (see reconcile_project), so this
        # flag only matters when the caller wants the schema bootstrapped WITHOUT also
        # backfilling every unregistered file.
        (name, _root), = targets
        session_output.ensure_session_output_schema(name)
        print(f"{name}: schema ensured")
        return 0

    for name, root in targets:
        result = session_output.reconcile_project(name, root, dry_run=args.dry_run)
        suffix = " (dry-run)" if args.dry_run else ""
        print(f"{name}: scanned {result.scanned}, registered {result.registered}{suffix}")
    return 0


def _cmd_pdata_verify(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import verify

    projects = verify.discover_projects() if args.all_projects else [args.project]
    if not projects:
        print("ccst pdata verify: no project databases found", file=sys.stderr)
        return 2

    # --all-projects without --verbose collapses the per-project listing to
    # one summary line - with N projects, a line each (even when every one
    # is clean) was the "very long" default output a daily unattended job
    # printed into every catch-up digest. --project already prints just one
    # project's worth, which isn't that problem, so the split only applies
    # to --all-projects; a single --project run always gets full detail.
    compact = args.all_projects and not args.verbose

    worst = 0
    flagged: list[str] = []
    for project in projects:
        try:
            summary = verify.run_verify(project=project, full=args.full)
        except ValueError as exc:
            print(f"ccst pdata verify: {project}: {exc}", file=sys.stderr)
            worst = max(worst, 2)
            continue
        if summary.status != "OK":
            worst = max(worst, 1)
            flagged.append(project)
        if compact:
            continue
        print(f"{project}: {summary.status} ({len(summary.issues)} issue(s))")
        for issue in summary.issues:
            print(f"  [{issue.severity}] {issue.check}: {issue.message}")

    if compact:
        if flagged:
            print(
                f"ccst pdata verify --all-projects: ISSUES in {len(flagged)} of "
                f"{len(projects)} project(s) - run 'ccst pdata verify --all-projects "
                f"--verbose' for details"
            )
        else:
            print(f"ccst pdata verify --all-projects: OK ({len(projects)} project(s), 0 issue(s))")
    return worst


def _cmd_pdata_dump(args: argparse.Namespace) -> int:
    from cc_session_tools.lib import machine_identity
    from cc_session_tools.lib.pdata import (
        dump, repository, store, sync_notify, vector_clock_store, verify,
    )

    projects = verify.discover_projects() if args.all_projects else [args.project]
    if not projects:
        print("ccst pdata dump: no project databases found", file=sys.stderr)
        return 2

    machine_id = machine_identity.resolve().machine_id
    compact = args.all_projects
    published = 0
    refused = 0
    errors = 0

    for project in projects:
        try:
            project_root = store.project_root(project)
            conn = repository.connect(project)
        except ValueError as exc:
            # Matches the sibling _cmd_pdata_rehydrate/_cmd_pdata_resolve handlers: record and
            # continue rather than abort the whole --all-projects loop on one bad project.
            print(f"ccst pdata dump: {project}: {exc}", file=sys.stderr)
            errors += 1
            continue
        try:
            local_vector = vector_clock_store.read_vector(conn)
            existing = dump.read_latest(project_root)
            # Shared with the SessionEnd hook (cccs_hooks.pdata_sync) - the spec's dump trigger
            # is one rule, so it lives in one function rather than inline in both callers.
            # `None` means safe to publish; anything else is the Comparison that refuses it.
            comparison = dump.decide_publish(local_vector=local_vector, existing=existing)
            if comparison is not None and not args.force:
                refused += 1
                detail = dump.refusal_detail(project)
                sync_notify.notify_conflict(project, outcome=comparison.value, detail=detail)
                if not compact:
                    print(f"ccst pdata dump: {project}: {detail}", file=sys.stderr)
                continue
            dump.write_latest(
                conn, project_root=project_root, machine_id=machine_id, vector=local_vector,
            )
        finally:
            conn.close()
        published += 1
        if not compact:
            print(f"ccst pdata dump: {project}: published (machine_id={machine_id})")

    if compact:
        if refused or errors:
            print(
                f"ccst pdata dump --all-projects: published {published}, refused {refused} "
                f"(unresolved fork), errors {errors} of {len(projects)} project(s) - see "
                f"`ccst pdata resolve --project NAME` for each"
            )
        else:
            print(
                f"ccst pdata dump --all-projects: published {published} of "
                f"{len(projects)} project(s)"
            )

    return 2 if errors else (1 if refused else 0)


def _cmd_pdata_rehydrate(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import rehydrate, sync_notify, verify

    projects = verify.discover_projects() if args.all_projects else [args.project]
    if not projects:
        print("ccst pdata rehydrate: no project databases found", file=sys.stderr)
        return 2

    compact = args.all_projects
    counts = {outcome.value: 0 for outcome in rehydrate.RehydrateOutcome}
    worst = 0

    for project in projects:
        try:
            result = rehydrate.rehydrate(project, force=args.force)
        except ValueError as exc:
            print(f"ccst pdata rehydrate: {project}: {exc}", file=sys.stderr)
            worst = max(worst, 2)
            continue
        counts[result.outcome.value] += 1

        if result.outcome is rehydrate.RehydrateOutcome.FAST_FORWARDED:
            if not compact:
                print(
                    f"ccst pdata rehydrate: {project}: fast-forwarded from "
                    f"{result.from_machine}"
                )
        elif result.outcome is rehydrate.RehydrateOutcome.NO_OP:
            if not compact:
                print(f"ccst pdata rehydrate: {project}: already up to date")
        elif result.outcome in (
            rehydrate.RehydrateOutcome.FORK, rehydrate.RehydrateOutcome.CHECKSUM_INVALID,
        ):
            worst = max(worst, 1)
            # Shared with the SessionStart hook (cccs_hooks.pdata_sync) so the CLI and the hook
            # can never describe the same conflict two different ways.
            detail = rehydrate.conflict_detail(result, project=project)
            sync_notify.notify_conflict(project, outcome=result.outcome.value, detail=detail)
            if not compact:
                print(f"ccst pdata rehydrate: {project}: {detail}", file=sys.stderr)
        else:
            # DEFERRED - another writer holds sync_lock right now. Expected and transient, not
            # an error (rehydrate.py's own RehydrateOutcome docstring: "retry later" rather than
            # "surfaced, nothing written" like FORK/CHECKSUM_INVALID above) - exit 0, and never
            # point at `ccst pdata resolve`, which would misdescribe this as a conflict.
            if not compact:
                print(
                    f"ccst pdata rehydrate: {project}: another writer holds the lock - "
                    f"retry later"
                )

    if compact:
        print(
            "ccst pdata rehydrate --all-projects: "
            f"fast-forwarded {counts[rehydrate.RehydrateOutcome.FAST_FORWARDED.value]}, "
            f"no-op {counts[rehydrate.RehydrateOutcome.NO_OP.value]}, "
            f"fork {counts[rehydrate.RehydrateOutcome.FORK.value]}, "
            f"checksum-invalid {counts[rehydrate.RehydrateOutcome.CHECKSUM_INVALID.value]}, "
            f"deferred {counts[rehydrate.RehydrateOutcome.DEFERRED.value]} of "
            f"{len(projects)} project(s)"
        )

    return worst


def _parse_resolve_choice(raw: str) -> tuple[int, str]:
    """Parse "ID=local|dump" into (record_id, choice). Only checks the shape - whether choice is
    actually 'local'/'dump' is apply_resolution's own job (its error message is printed verbatim,
    not re-derived here). Raises ValueError on malformed input."""
    id_str, sep, choice = raw.partition("=")
    if not sep or not id_str or not choice:
        raise ValueError(f"malformed --choice (want ID=local|dump): {raw!r}")
    try:
        record_id = int(id_str)
    except ValueError:
        raise ValueError(f"malformed --choice (want ID=local|dump): {raw!r}") from None
    return record_id, choice


def _resolve_deleted_at(payload: RecordPayload) -> int | None:
    return payload["base"]["deleted_at"]


def _cmd_pdata_resolve(args: argparse.Namespace) -> int:
    from cc_session_tools.lib.pdata import resolve, verify

    if args.choice:
        if args.all_projects:
            print(
                "ccst pdata resolve: --choice requires --project (not --all-projects)",
                file=sys.stderr,
            )
            return 2
        try:
            raw_choices = dict(_parse_resolve_choice(raw) for raw in args.choice)
        except ValueError as exc:
            print(f"ccst pdata resolve: {exc}", file=sys.stderr)
            return 2
        try:
            # narrow_choices raises the same "invalid choice" ValueError apply_resolution's own
            # runtime check does, from the same helper - so an unusable choice value still
            # reports exactly one way, whichever of the two sees it first.
            choices = resolve.narrow_choices(raw_choices)
            outcome = resolve.apply_resolution(args.project, choices)
        except ValueError as exc:
            print(f"ccst pdata resolve: {exc}", file=sys.stderr)
            return 1
        if outcome is resolve.ApplyOutcome.LOCKED:
            # Another writer holds sync_lock right now. Expected and transient, exactly like
            # rehydrate's DEFERRED above - exit 0, nothing written, and never point at `ccst pdata
            # resolve`, which would misdescribe lock contention as a conflict.
            print(
                f"ccst pdata resolve: {args.project}: another writer holds the lock - retry later"
            )
            return 0
        print(f"ccst pdata resolve: {args.project}: resolved {len(choices)} record(s)")
        return 0

    projects = verify.discover_projects() if args.all_projects else [args.project]
    if not projects:
        print("ccst pdata resolve: no project databases found", file=sys.stderr)
        return 2

    compact = args.all_projects
    outstanding = 0

    for project in projects:
        try:
            diff = resolve.diff_against_dump(project)
        except ValueError as exc:
            print(f"ccst pdata resolve: {project}: {exc}", file=sys.stderr)
            outstanding += 1
            continue

        has_diff = bool(diff.records) or bool(diff.schema_fields)
        if has_diff:
            outstanding += 1

        if compact:
            if has_diff:
                print(
                    f"{project}: {len(diff.records)} record(s) + "
                    f"{len(diff.schema_fields)} schema field(s) outstanding"
                )
            else:
                print(f"{project}: clean")
            continue

        if not has_diff:
            print(f"{project}: clean - nothing to resolve")
            continue

        print(
            f"{project}: {len(diff.records)} record(s) + {len(diff.schema_fields)} schema "
            f"field(s) to resolve"
        )
        for rd in diff.records:
            if rd.id_collision:
                print(
                    f"  record {rd.record_id} (group={rd.record_group}): id collision - "
                    f"this id was independently assigned to two unrelated records on "
                    f"different machines, not a genuine edit conflict; `ccst pdata resolve` "
                    f"cannot apply a local/dump choice to this one, it needs manual, "
                    f"out-of-band reconciliation"
                )
                continue
            if rd.group_mismatch:
                # Both sides' own group names, not rd.record_group: that field carries whichever
                # side happened to be available, and RecordDiff's docstring says so - printing it
                # alone in the one line whose whole point is which two names disagree would tell
                # the reader neither which side they are seeing nor what the other side's is.
                print(
                    f"  record {rd.record_id} (local group={rd.local_record_group}, "
                    f"dump group={rd.dump_record_group}): group mismatch - same id and "
                    f"created_at, but local and dump disagree on record_group; compare "
                    f"content/file_path on each side before assuming this is a safe "
                    f"`ccst pdata rename-group` rather than a same-second id collision - "
                    f"`ccst pdata resolve` cannot apply a local/dump choice to this one"
                )
                continue
            if rd.is_delete_vs_update:
                assert rd.local is not None and rd.dump is not None
                local_deleted = _resolve_deleted_at(rd.local) is not None
                side = (
                    "local deleted this record, dump has a live edit to it" if local_deleted
                    else "dump deleted this record, local has a live edit to it"
                )
                print(
                    f"  record {rd.record_id} (group={rd.record_group}): delete-vs-update - "
                    f"{side}"
                )
                continue
            if rd.local is None:
                print(
                    f"  record {rd.record_id} (group={rd.record_group}): dump-only "
                    f"(not present locally)"
                )
            elif rd.dump is None:
                print(
                    f"  record {rd.record_id} (group={rd.record_group}): local-only "
                    f"(not present in the dump)"
                )
            else:
                print(
                    f"  record {rd.record_id} (group={rd.record_group}): content differs "
                    f"between local and dump"
                )

        for fd in diff.schema_fields:
            side = "local only" if fd.present_locally else "dump only"
            print(f"  schema field {fd.record_group}.{fd.field_name}: {side}")

    return 1 if outstanding else 0


def _cmd_pdata_sync_check(args: argparse.Namespace) -> int:
    """The spec's "Hourly `ccsched` job" trigger: per project, rehydrate-check and - only if no
    rehydrate happened - dump-check. The whole decision lives in `pdata.sync_check` (shared with
    nothing else today, but kept out of this handler for the same reason `dump.decide_publish`
    and `rehydrate.rehydrate` are: this file holds argparse plumbing and printing, not sync
    rules). This handler is the loop, the printing, and the exit-code mapping."""
    import sqlite3

    from cc_session_tools.lib.pdata import sync_check, verify

    projects = verify.discover_projects() if args.all_projects else [args.project]
    if not projects:
        print("ccst pdata sync-check: no project databases found", file=sys.stderr)
        return 2

    compact = args.all_projects
    counts = {outcome.value: 0 for outcome in sync_check.SyncOutcome}
    errors = 0
    worst = 0

    for project in projects:
        try:
            result = sync_check.check_project(project)
        except (OSError, ValueError, sqlite3.Error) as exc:
            # Record and continue, matching _cmd_pdata_dump/_cmd_pdata_rehydrate: one bad or
            # unreadable project must not abort the whole batch. The caught set is wider than
            # those two siblings' bare ValueError, and matches `cccs_hooks.pdata_sync`'s instead,
            # for the reason that module documents: this command's real caller is an unattended
            # ccsched job, where an uncaught sqlite3.Error on project 3 of 12 both loses the
            # other nine projects' cycle and counts as a crash toward auto-suspend. A genuinely
            # corrupt <project>.db is the concrete case - discover_projects() finds it by
            # filename, and opening it raises sqlite3.DatabaseError, not ValueError.
            print(f"ccst pdata sync-check: {project}: {exc}", file=sys.stderr)
            errors += 1
            worst = max(worst, 2)
            continue
        counts[result.outcome.value] += 1

        if result.outcome is sync_check.SyncOutcome.CONFLICT:
            # sync_check.check_project has already pushed this through notify_conflict - the only
            # channel that reaches anyone when this runs unattended. Printing it too is for the
            # human who ran the command by hand.
            worst = max(worst, 1)
            if not compact:
                print(f"ccst pdata sync-check: {project}: {result.detail}", file=sys.stderr)
        elif not compact:
            print(f"ccst pdata sync-check: {project}: {_sync_check_line(result)}")

    if compact:
        print(
            "ccst pdata sync-check --all-projects: "
            f"fast-forwarded {counts[sync_check.SyncOutcome.FAST_FORWARDED.value]}, "
            f"published {counts[sync_check.SyncOutcome.PUBLISHED.value]}, "
            f"unchanged {counts[sync_check.SyncOutcome.UNCHANGED.value]}, "
            f"conflict {counts[sync_check.SyncOutcome.CONFLICT.value]}, "
            f"deferred {counts[sync_check.SyncOutcome.DEFERRED.value]}, "
            f"occupied {counts[sync_check.SyncOutcome.OCCUPIED.value]}, "
            f"errors {errors} of {len(projects)} project(s)"
        )

    # Same convention as _cmd_pdata_rehydrate's `worst`: 2 for a hard error, 1 for an unresolved
    # conflict, 0 otherwise. DEFERRED and OCCUPIED are transient, self-resolving states, not
    # findings, and contribute 0 - exactly as DEFERRED does there.
    return worst


def _sync_check_line(result: SyncCheckResult) -> str:
    """The one-line, --project-mode wording for each non-conflict sync-check outcome."""
    from cc_session_tools.lib.pdata import sync_check

    if result.outcome is sync_check.SyncOutcome.FAST_FORWARDED:
        return f"fast-forwarded from {result.from_machine}"
    if result.outcome is sync_check.SyncOutcome.PUBLISHED:
        return f"published (machine_id={result.machine_id})"
    if result.outcome is sync_check.SyncOutcome.UNCHANGED:
        return "unchanged - nothing new to publish"
    if result.outcome is sync_check.SyncOutcome.DEFERRED:
        return "another writer holds the lock - retry later"
    return "a live session is working in this project - skipped"


# ---------- machine-identity show / confirm ----------


def _cmd_machine_identity_show(args: argparse.Namespace) -> int:
    identity = machine_identity.resolve()
    if identity.confirmed:
        print(f"{identity.machine_id} (confirmed)")
    else:
        print(
            f"{identity.machine_id} (unconfirmed - run "
            "'ccst machine-identity confirm --name <name>')"
        )
    return 0


def _cmd_machine_identity_confirm(args: argparse.Namespace) -> int:
    try:
        machine_identity.confirm(args.name)
    except ValueError as exc:
        print(f"ccst machine-identity: {exc}", file=sys.stderr)
        return 2
    print(f"Confirmed machine id: {args.name}")
    return 0


# ---------- hooks run ----------


def _cmd_hooks_run(args: argparse.Namespace) -> int:
    """Dispatch to the named hook, or exit 1 (never 2) if it is unknown.

    The exit code carries meaning to Claude Code: 2 is the *blocking* code,
    and every other non-zero value is a non-blocking error that is merely
    surfaced. An unknown hook name means settings.json still registers a hook
    a later CCST removed — a stale-config problem, not a reason to block. When
    this was an argparse `choices=` constraint, argparse's own exit code (2)
    made every stale entry blocking: a stale UserPromptSubmit hook swallowed
    every prompt, and a stale Stop hook stopped the session from ever ending.
    Returning 1 degrades that to a visible warning instead.
    """
    module_path = HOOK_VERBS.get(args.hook)
    if module_path is None:
        print(
            f"ccst hooks run: unknown hook {args.hook!r}.\n"
            f"This build dispatches: {', '.join(sorted(HOOK_VERBS))}.\n"
            "If settings.json still registers this hook, remove the stale entry with:\n"
            f"    ccst hooks uninstall --hook {args.hook} --apply\n"
            "or bring every entry up to date with: ccst hooks install --apply",
            file=sys.stderr,
        )
        return 1
    module = importlib.import_module(module_path)
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


def _cmd_repair_sessions(args: argparse.Namespace) -> int:
    import sqlite3

    from cc_session_tools.lib import db as db_lib
    from cc_session_tools.lib import sessions_db, sessions_repair
    from cc_session_tools.lib.roots import RootsConfigError, load_session_roots

    db_path = Path(args.sessions_db) if args.sessions_db else sessions_db.default_db_path()
    try:
        roots = load_session_roots()
    except RootsConfigError as e:
        print(str(e), file=sys.stderr)
        return 1

    # sqlite3.connect() (and the online backup API below) open lazily and
    # only fail once a statement actually touches the file, so a corrupt-
    # but-existing sessions.db reaches this try, not the exists() check
    # above. This is the tool users are told to run to fix store corruption
    # - both the backup step and the repair itself must fail loudly with a
    # clear message here, not a raw traceback.
    try:
        if args.execute:
            if not db_path.exists():
                # sqlite3.connect() auto-creates an empty file, so without this
                # check db_lib.backup_to() below would silently back up (and
                # repair() would open) a brand-new, empty sessions.db instead
                # of failing loudly on the mistake.
                print(f"No sessions.db found at {db_path} — nothing to back up or repair.", file=sys.stderr)
                return 1
            backup_dir = db_path.parent / "repair-backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = backup_dir / f"sessions-{stamp}.db"
            db_lib.backup_to(db_path, backup_path)
            print(f"Backed up sessions.db to {backup_path}")

        report = sessions_repair.repair(roots, path=db_path, dry_run=not args.execute)
    except sqlite3.DatabaseError as exc:
        print(f"{db_path} exists but failed to open: {exc}", file=sys.stderr)
        return 1
    if not any((report.repaired, report.unresolved, report.ambiguous, report.conflicts)):
        print("No non-absolute project_dir rows found in sessions.db.")
        return 0

    verb = "fixed" if args.execute else "would fix"
    for basename, new_dir in report.repaired:
        print(f"  {verb}: {basename} -> {new_dir}")
    for basename in report.unresolved:
        print(
            f"  UNRESOLVED (no on-disk cc-sessions/{basename} under any root): {basename}",
            file=sys.stderr,
        )
    for basename, candidates in report.ambiguous.items():
        print(f"  AMBIGUOUS ({len(candidates)} candidates, skipped): {basename}", file=sys.stderr)
    for basename in report.conflicts:
        print(
            f"  CONFLICT (a correct row for this basename already exists, skipped): {basename}",
            file=sys.stderr,
        )

    if not args.execute and report.repaired:
        print(f"\n{len(report.repaired)} row(s) would be repaired. Re-run with --execute to apply.")
    return 1 if (report.unresolved or report.ambiguous or report.conflicts) else 0


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


def _cmd_migrate_ccmsg(args: argparse.Namespace) -> int:
    from cc_session_tools.cli.migrate_ccmsg import main as migrate_main

    argv: list[str] = []
    if args.old_root:
        argv += ["--old-root", args.old_root]
    if args.backup_dir:
        argv += ["--backup-dir", args.backup_dir]
    if args.dry_run:
        argv.append("--dry-run")
    return migrate_main(argv)


def _cmd_migrate_telemetry(args: argparse.Namespace) -> int:
    from cc_session_tools.cli.migrate_telemetry import main as migrate_main

    argv: list[str] = []
    if args.source_dir:
        argv += ["--source-dir", args.source_dir]
    if args.dest_dir:
        argv += ["--dest-dir", args.dest_dir]
    if args.dry_run:
        argv.append("--dry-run")
    return migrate_main(argv)


_MIGRATE_ALL_BANNER = (
    "Migrating all legacy data stores (ccmsg, ccsched, sessions, telemetry) into\n"
    "the SQLite stores under ~/.local/share/claude/. Each step is non-destructive\n"
    "(write, verify, tar-backup — old files are only ever removed after a\n"
    "successful verify).\n\n"
    "IMPORTANT: run this from a plain terminal, NOT from inside a Claude Code\n"
    "session. The ccmsg/ccsched/telemetry steps delete their own already-backed-up\n"
    "old files as a final step, and the bash-hard-deny PreToolUse hook statically\n"
    "blocks any script containing a delete call — there is no bypass for this."
)


def _cmd_migrate_all(args: argparse.Namespace) -> int:
    print(_MIGRATE_ALL_BANNER)

    steps: list[tuple[str, object]] = [
        ("sessions", lambda: _cmd_sessions_migrate(argparse.Namespace(
            dry_run=args.dry_run, sessions_db=None, tags_dir=None, mutes_file=None))),
        ("ccmsg", lambda: _cmd_migrate_ccmsg(argparse.Namespace(
            old_root=None, backup_dir=None, dry_run=args.dry_run))),
        ("ccsched", lambda: _cmd_migrate_ccsched(argparse.Namespace(
            old_dir=None, backup_dir=None, dry_run=args.dry_run))),
        ("telemetry", lambda: _cmd_migrate_telemetry(argparse.Namespace(
            source_dir=None, dest_dir=None, dry_run=args.dry_run))),
    ]

    overall_rc = 0
    for name, step in steps:
        print(f"\n=== {name} ===")
        rc = step()  # type: ignore[operator]
        if rc != 0:
            overall_rc = rc

    print()
    if args.dry_run:
        print("Dry run complete — re-run without --dry-run once you're satisfied.")
    else:
        print("All migrations attempted. Review any ERROR/ABORT lines above; a "
              "non-zero step leaves its own old files untouched and is safe to re-run.")
    return overall_rc


# ---------- ccsched-jobs install ----------


def _cmd_ccsched_jobs_install(args: argparse.Namespace) -> int:
    """Register CCST's bundled ccsched jobs (lib/scheduler/bundled_jobs.py) if not already
    present. Idempotent and non-destructive: an existing job id is left completely untouched,
    even if its cadence/timeout has since been hand-edited — a human may have deliberately
    edited it via `ccsched edit`, and silently stomping that on every re-run would be a
    surprising footgun. "Already there" is decided by an explicit membership check against
    registry.load_registry()'s existing ids before add_job is ever called, not by attempting the
    add and catching ccsched add's own duplicate-id RegistryError (this repo's "no exceptions
    for control flow" coding standard rules that out).

    An already-registered job that no longer matches its bundled definition — hand-edited fields,
    or disabled — is reported as such rather than silently counted as "already registered", so an
    upgrade (this command is one of install-everything's five steps, so it runs on every version
    bump) tells the operator they are out of sync with the shipped source instead of staying
    quiet about it. Neither state is ever auto-corrected here; `ccst doctor` surfaces the same
    two states on an ongoing basis via `check_ccsched_job_registered`.

    A bundled job id that is missing but was previously installed on this machine (per
    registry.bundled_install_ids() — a tombstone-free record `ccsched remove` cannot erase) means
    the operator deliberately removed it, not that this machine has simply never seen it; it is
    reported as "deleted" and never silently re-added — re-adding an intentional deletion on
    every version bump would defeat the point of removing it. `--reinstall JOB_ID` (repeatable)
    is the explicit override that brings one back."""
    from cc_session_tools.lib.scheduler import bundled_jobs, registry, state
    from cc_session_tools.lib.scheduler.jobspec import validate_job_fields

    existing = {spec.job_id: spec for spec in registry.load_registry()}
    ever_installed = registry.bundled_install_ids()
    reinstall_requested = set(args.reinstall or [])
    now = state.format_ts(datetime.datetime.now(datetime.timezone.utc))

    for job in bundled_jobs.BUNDLED_CCSCHED_JOBS:
        spec = existing.get(job.job_id)
        if spec is not None:
            changed = bundled_jobs.diff_from_bundled(spec, job)
            if changed:
                print(
                    f"  changed (not touched): {job.job_id} - {', '.join(changed)} "
                    f"differ from the bundled definition; run 'ccsched edit' to realign, or "
                    f"leave as your intentional customization"
                )
            elif not spec.enabled:
                print(f"  disabled (not touched): {job.job_id} - run 'ccsched enable {job.job_id}' to re-enable")
            else:
                print(f"  already registered: {job.job_id}")
            if args.apply and job.job_id not in ever_installed:
                registry.mark_bundled_installed(job.job_id, now)
            continue

        if job.job_id in ever_installed and job.job_id not in reinstall_requested:
            print(
                f"  deleted (not re-added): {job.job_id} - was previously installed and has "
                f"since been removed; pass --reinstall {job.job_id} to bring it back"
            )
            continue

        if not args.apply:
            verb = "would reinstall" if job.job_id in ever_installed else "would register"
            print(f"  {verb}: {job.job_id}")
            continue
        new_spec = validate_job_fields(
            job_id=job.job_id, cadence=job.cadence, coalesce=job.coalesce,
            command=list(job.command), surface=job.surface, enabled=True,
            catchup_window=job.catchup_window, timeout=job.timeout,
            success_exit_codes=job.success_exit_codes,
        )
        registry.add_job(new_spec)
        registry.mark_bundled_installed(job.job_id, now)
        print(f"  {'reinstalled' if job.job_id in ever_installed else 'registered'}: {job.job_id}")

    if not args.apply:
        print("\nDry run — re-run with --apply to register any missing job(s)")
    return 0


# ---------- install-everything ----------


def run_install_everything(
    *,
    apply: bool,
    stream: TextIO,
    health_check: bool,
    no_pypi: bool = False,
    skills_target: str | None = None,
    hooks_target: str | None = None,
    fragments_dir: str | None = None,
    claude_md_target: str | None = None,
) -> int:
    """Run the five install steps, optionally followed by a health check.

    Returns the worst rc the five steps produced; the health check's own rc is
    deliberately not part of it, matching the diagnostic-not-gate role it has
    always had here.

    `stream` receives every step's progress output. The five step functions
    print to stdout directly, so the loop runs under
    contextlib.redirect_stdout(stream): the CLI adapter passes sys.stdout (a
    no-op passthrough) and install_sync.ensure_synced passes a StringIO it
    discards on success. Auto-apply must not write to stdout at all -
    `ccst sessions list --json` emits machine-readable stdout, and
    scheduler/worker.py carries a job's stdout into the ledger as its recorded
    findings.

    `health_check=False` is what auto-apply uses: the five steps are 16.8 ms,
    the trailing `ccst doctor` is ~1.55 s dominated by its PyPI network call.
    An auto-apply is not a diagnostic run and must not take a network
    dependency on someone else's command.
    """
    hooks_target = hooks_target or str(Path.home() / ".claude" / "settings.json")

    steps: list[tuple[str, str, object]] = [
        (
            "Skills",
            "skills",
            argparse.Namespace(source=None, target=skills_target, apply=apply, force=False),
        ),
        (
            "Hooks",
            "hooks",
            argparse.Namespace(
                source=None,
                hook=None,
                target=hooks_target,
                apply=apply,
            ),
        ),
        (
            "Shell helpers",
            "shell",
            argparse.Namespace(
                apply=apply,
                fragments_dir=[fragments_dir] if fragments_dir else None,
            ),
        ),
        (
            "Global CLAUDE.md",
            "claude-md",
            argparse.Namespace(target=claude_md_target, apply=apply),
        ),
        (
            "Scheduled jobs",
            "ccsched-jobs",
            argparse.Namespace(apply=apply, reinstall=[]),
        ),
    ]

    dispatch: dict[str, object] = {
        "skills": _cmd_skills_install,
        "hooks": _cmd_hooks_install,
        "shell": _cmd_shell_install,
        "claude-md": _cmd_claude_md_install,
        "ccsched-jobs": _cmd_ccsched_jobs_install,
    }

    # +1 accounts for the trailing health check, which isn't itself a `steps` entry.
    total_steps = len(steps) + (1 if health_check else 0)
    overall_rc = 0
    with contextlib.redirect_stdout(stream):
        for i, (label, key, step_args) in enumerate(steps, start=1):
            print(f"\n=== {i}/{total_steps}  {label} ===")
            rc = dispatch[key](step_args)  # type: ignore[operator]
            if rc != 0:
                overall_rc = rc

        if apply and overall_rc == 0:
            import sqlite3

            from cc_session_tools.lib import install_sync
            try:
                install_sync.record_synced(__version__)
            except sqlite3.DatabaseError as exc:
                # A corrupt sessions.db must not erase the five install steps'
                # already-successful work (and their printed summary) that ran
                # just above - print a clear warning and continue. Letting this
                # propagate would also be self-defeating: this is the exact
                # command the out-of-sync warning tells a user to run.
                print(
                    f"  warning: could not record the install-everything sync marker "
                    f"({exc}) - sessions.db may be corrupt; run `ccst repair sessions` "
                    "to investigate",
                    file=sys.stderr,
                )

        if health_check:
            print(f"\n=== {total_steps}/{total_steps}  Health check ===")
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


def _cmd_install_everything(args: argparse.Namespace) -> int:
    """argparse adapter for run_install_everything: the interactive CLI path,
    which streams to stdout and always ends with the health check."""
    return run_install_everything(
        apply=args.apply,
        stream=sys.stdout,
        health_check=True,
        no_pypi=args.no_pypi,
        skills_target=getattr(args, "skills_target", None),
        hooks_target=getattr(args, "hooks_target", None),
        fragments_dir=getattr(args, "fragments_dir", None),
        claude_md_target=getattr(args, "claude_md_target", None),
    )


def _cmd_context_override(args: argparse.Namespace) -> int:
    """ccst context-override [on|off|status] - the CLI half of the /context-override
    skill. Reads CLAUDE_CODE_SESSION_ID exactly as the ported bash script did."""
    from cc_session_tools.lib import context_overrides

    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not session_id:
        print(
            "context-override: CLAUDE_CODE_SESSION_ID is not set; cannot key the flag to this session.",
            file=sys.stderr,
        )
        return 1

    action = args.action
    if action == "on":
        context_overrides.set_override(session_id, "on")
        print("Context-window override ON for this session.")
        print("The 150k/200k warnings are silenced until you run /context-override off or the session ends.")
        return 0
    if action == "off":
        context_overrides.set_override(session_id, "off")
        print("Context-window override OFF. The 150k/200k warnings will appear again.")
        return 0
    # action == "status" - the only remaining choice. The subparser's
    # choices=["on", "off", "status"] already rejects anything else before
    # this function ever runs, so there is no unknown-action branch here.
    state = "ON" if context_overrides.get_override(session_id) else "OFF"
    print(f"Context-window override is {state} for this session.")
    return 0


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
    # Deliberately no choices=: argparse rejects an unknown value by exiting 2,
    # which is Claude Code's blocking exit code. _cmd_hooks_run validates the
    # name itself and exits 1 so a stale settings.json entry warns rather than
    # wedges. See _cmd_hooks_run's docstring.
    run_parser.add_argument(
        "hook",
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
        "--all",
        action="store_true",
        help="Show every check result, including OK (default: only non-OK)",
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
        help="Write the ccl() wrapper function fragment to ~/.shellrc.d/ (dry run by default)",
    )
    shell_install_parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (default: dry run)",
    )
    shell_install_parser.add_argument(
        "--fragments-dir",
        action="append",
        metavar="PATH",
        help="Fragments dir to write into (may repeat; default: ~/.shellrc.d)",
    )

    shell_uninstall_parser = shell_sub.add_parser(
        "uninstall",
        help="Remove the ccl() fragment file from ~/.shellrc.d/ (dry run by default)",
    )
    shell_uninstall_parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (default: dry run)",
    )
    shell_uninstall_parser.add_argument(
        "--fragments-dir",
        action="append",
        metavar="PATH",
        help="Fragments dir to remove from (may repeat; default: ~/.shellrc.d)",
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
            "messaging, session-env, and sessions-index stores. Report-only "
            "— never deletes or modifies anything."
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
        help="Scheduler directory holding ccsched.db (default: from CC_SCHEDULER_DIR or data_home())",
    )
    gc_report_parser.add_argument(
        "--messages-root",
        default=None,
        metavar="PATH",
        help="Messaging store directory holding ccmsg.db (default: from CCST_MESSAGES_ROOT or data_home())",
    )
    gc_report_parser.add_argument(
        "--session-env-dir",
        default=None,
        metavar="PATH",
        help="Session-env directory (default: ~/.claude/session-env/)",
    )
    gc_report_parser.add_argument(
        "--sessions-dir",
        default=None,
        metavar="PATH",
        help="Directory holding sessions.db (default: from CCST_SESSIONS_DIR or data_home())",
    )

    gc_prune_parser = gc_sub.add_parser(
        "prune",
        help=(
            "Delete the orphaned entries `gc report` finds. Dry run by default "
            "— pass --execute to actually delete."
        ),
    )
    gc_prune_parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete (default: dry-run report only, same output shape plus what would happen)",
    )
    gc_prune_parser.add_argument(
        "--min-age-hours",
        type=float,
        default=24.0,
        metavar="N",
        help="Exclude entries younger than this from deletion (default: 24)",
    )
    gc_prune_parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="STORE",
        # Kept as a literal list, not an import of session_gc.STORE_NAMES, to
        # preserve this module's lazy-import-inside-handler convention for
        # lib.session_gc (see _cmd_gc_report/_cmd_gc_prune) — parser
        # construction runs on every `ccst` invocation, handlers only on a
        # matching one. Update both if the 5 store names ever change.
        choices=[
            "scheduler-reconcile-markers",
            "scheduler-cursors",
            "messages-cursors",
            "session-env",
            "sessions-index",
        ],
        help="Restrict to one store (may repeat); default: all 5",
    )
    gc_prune_parser.add_argument(
        "--projects-dir",
        default=None,
        metavar="PATH",
        help="Transcript projects directory (default: ~/.claude/projects/)",
    )
    gc_prune_parser.add_argument(
        "--scheduler-dir",
        default=None,
        metavar="PATH",
        help="Scheduler directory holding ccsched.db (default: from CC_SCHEDULER_DIR or data_home())",
    )
    gc_prune_parser.add_argument(
        "--messages-root",
        default=None,
        metavar="PATH",
        help="Messaging store directory holding ccmsg.db (default: from CCST_MESSAGES_ROOT or data_home())",
    )
    gc_prune_parser.add_argument(
        "--session-env-dir",
        default=None,
        metavar="PATH",
        help="Session-env directory (default: ~/.claude/session-env/)",
    )
    gc_prune_parser.add_argument(
        "--sessions-dir",
        default=None,
        metavar="PATH",
        help="Directory holding sessions.db (default: from CCST_SESSIONS_DIR or data_home())",
    )

    # ---- pdata ----
    pdata_parser = sub.add_parser("pdata", help="Per-project SQLite data store commands")
    pdata_sub = pdata_parser.add_subparsers(dest="verb", metavar="<verb>")
    pdata_sub.required = True

    pdata_add_parser = pdata_sub.add_parser("add", help="Insert a new record")
    pdata_add_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_add_parser.add_argument("--group", required=True, metavar="RECORD_GROUP")
    pdata_add_parser.add_argument("--content", required=True)
    pdata_add_parser.add_argument("--file", default=None, metavar="PATH",
                                   help="Relative sibling/source file path")
    pdata_add_parser.add_argument(
        "--created-at", type=int, default=None, metavar="EPOCH",
        help="Unix epoch seconds to backdate created_at/updated_at to (default: now); "
             "see spec §5.",
    )
    pdata_add_parser.add_argument(
        "--field", action="append", default=[], metavar="NAME=VALUE",
        help="Extension field assignment; may repeat. Field must already be registered via "
             "'ccst pdata schema add-field'. VALUE of the literal token 'null' sets SQL NULL "
             "rather than the string \"null\".",
    )

    pdata_schema_parser = pdata_sub.add_parser("schema", help="Schema discovery and evolution")
    pdata_schema_sub = pdata_schema_parser.add_subparsers(dest="subverb", metavar="<subverb>")
    pdata_schema_sub.required = True

    pdata_schema_add_field_parser = pdata_schema_sub.add_parser(
        "add-field", help="Add/describe an extension-table field (idempotent)",
    )
    pdata_schema_add_field_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_schema_add_field_parser.add_argument("--group", required=True, metavar="RECORD_GROUP")
    pdata_schema_add_field_parser.add_argument(
        "--field", required=True, metavar="NAME:TYPE",
        help="e.g. sender:TEXT — TYPE is one of TEXT, INTEGER, REAL, BLOB",
    )
    pdata_schema_add_field_parser.add_argument("--description", default=None, metavar="TEXT")
    pdata_schema_add_field_parser.add_argument("--default", default=None, metavar="VALUE")

    pdata_schema_list_parser = pdata_schema_sub.add_parser(
        "list", help="List every record_group and whether it has an extension table",
    )
    pdata_schema_list_parser.add_argument("--project", required=True, metavar="NAME")

    pdata_schema_show_parser = pdata_schema_sub.add_parser(
        "show", help="Show base + extension columns for one record_group",
    )
    pdata_schema_show_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_schema_show_parser.add_argument("--group", required=True, metavar="RECORD_GROUP")

    pdata_get_parser = pdata_sub.add_parser("get", help="Fetch a single record by id")
    pdata_get_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_get_parser.add_argument("--id", required=True, type=int)
    pdata_get_parser.add_argument("--include-deleted", action="store_true")

    pdata_list_parser = pdata_sub.add_parser("list", help="List records in one record_group")
    pdata_list_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_list_parser.add_argument("--group", required=True, metavar="RECORD_GROUP")
    pdata_list_parser.add_argument("--since", type=int, default=None, metavar="EPOCH")
    pdata_list_parser.add_argument("--until", type=int, default=None, metavar="EPOCH")
    pdata_list_parser.add_argument("--limit", type=int, default=None, metavar="N")
    pdata_list_parser.add_argument("--include-deleted", action="store_true")
    pdata_list_parser.add_argument(
        "--format", choices=("table", "json", "csv"), default="table",
    )

    pdata_query_parser = pdata_sub.add_parser(
        "query", help="Query records with structured --where filters",
    )
    pdata_query_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_query_parser.add_argument("--group", required=True, metavar="RECORD_GROUP")
    pdata_query_parser.add_argument(
        "--where", action="append", default=[], metavar="'<field> <op> <value>'",
        help="May repeat; clauses are ANDed. op is one of = != < > <= >= LIKE.",
    )
    pdata_query_parser.add_argument("--limit", type=int, default=None, metavar="N")
    pdata_query_parser.add_argument("--include-deleted", action="store_true")
    pdata_query_parser.add_argument(
        "--format", choices=("table", "json", "csv"), default="table",
    )

    pdata_update_parser = pdata_sub.add_parser("update", help="Update a record (version-checked)")
    pdata_update_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_update_parser.add_argument("--id", required=True, type=int)
    pdata_update_parser.add_argument("--version", required=True, type=int, dest="version",
                                      metavar="EXPECTED_VERSION")
    pdata_update_parser.add_argument(
        "--content", default=None,
        help="New content. Omit to leave content unchanged (at least one of --content, "
             "--file, --field is required). Never re-derived from --field - if a record's "
             "content is meant to mirror its fields, pass a matching --content on every "
             "--field update yourself.",
    )
    pdata_update_parser.add_argument(
        "--file", default=None, metavar="PATH",
        help="New relative file path. Omit to leave the existing file_path unchanged.",
    )
    pdata_update_parser.add_argument(
        "--field", action="append", default=[], metavar="NAME=VALUE",
        help="Extension field assignment; may repeat. Never updates content (see --content). "
             "VALUE of the literal token 'null' sets SQL NULL rather than the string \"null\".",
    )
    pdata_update_parser.add_argument(
        "--format", choices=("table", "json"), default="table",
        help="Format used only for the conflict diff on a version mismatch",
    )

    pdata_delete_parser = pdata_sub.add_parser("delete", help="Soft-delete a record (version-checked)")
    pdata_delete_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_delete_parser.add_argument("--id", required=True, type=int)
    pdata_delete_parser.add_argument("--version", required=True, type=int,
                                      metavar="EXPECTED_VERSION")

    pdata_restore_parser = pdata_sub.add_parser("restore", help="Clear a soft-delete")
    pdata_restore_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_restore_parser.add_argument("--id", required=True, type=int)

    pdata_init_parser = pdata_sub.add_parser(
        "init", help="Initialize/migrate a project's data store (spec §7)"
    )
    pdata_init_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_init_parser.add_argument(
        "--rehearse", default=None, metavar="PATH",
        help="Run against a copy at PATH instead of the live project (spec §7.1 step 0)",
    )
    pdata_init_parser.add_argument(
        "--write", action="store_true",
        help=(
            "Perform the write/verify/backup/cutover phase (default: dry-run only). "
            "Streams progress to stdout and also writes it to "
            "<project>/ccst-pdata-init-write.log"
        ),
    )

    pdata_reorganize_parser = pdata_sub.add_parser(
        "reorganize", help="Split a flat folder into a nested (by-year or by-year-month) structure"
    )
    pdata_reorganize_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_reorganize_parser.add_argument("--folder", required=True, metavar="RELATIVE_PATH")
    pdata_reorganize_parser.add_argument(
        "--strategy", required=True, choices=("by-year", "by-year-month"),
    )
    pdata_reorganize_parser.add_argument(
        "--write", action="store_true",
        help="Perform the move and update matching pdata records (default: dry-run only)",
    )

    pdata_rename_group_parser = pdata_sub.add_parser(
        "rename-group", help="Rename a record_group in place (records, record_group_fields, "
                              "its ext table, and any matching manifest entries)"
    )
    pdata_rename_group_parser.add_argument("--project", required=True, metavar="NAME")
    pdata_rename_group_parser.add_argument("--from", dest="from_", required=True, metavar="OLD")
    pdata_rename_group_parser.add_argument("--to", required=True, metavar="NEW")
    pdata_rename_group_parser.add_argument(
        "--write", action="store_true",
        help="Perform the rename and update .ccst-pdata-proposal.json (default: dry-run only)",
    )

    pdata_reconcile_parser = pdata_sub.add_parser(
        "reconcile-session-output",
        help="Backfill the session-output index from cc-sessions/*/out/ on disk (idempotent)",
    )
    pdata_reconcile_target = pdata_reconcile_parser.add_mutually_exclusive_group(required=True)
    pdata_reconcile_target.add_argument("--project", metavar="NAME")
    pdata_reconcile_target.add_argument("--all-projects", action="store_true")
    pdata_reconcile_parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be registered without writing anything",
    )
    pdata_reconcile_parser.add_argument(
        "--schema-only", action="store_true",
        help="Bootstrap the session-output schema/index for --project and exit "
             "(no file scan or registration)",
    )

    pdata_verify_parser = pdata_sub.add_parser(
        "verify", help="Run the integrity-check backstop (spec §6.3/§8.2)"
    )
    verify_target = pdata_verify_parser.add_mutually_exclusive_group(required=True)
    verify_target.add_argument("--project", metavar="NAME")
    verify_target.add_argument(
        "--all-projects", action="store_true",
        help="Verify every project with a .db under project-db/",
    )
    pdata_verify_parser.add_argument(
        "--full", action="store_true",
        help="Rescan every row instead of only rows changed since the last run",
    )
    pdata_verify_parser.add_argument(
        "--verbose", action="store_true",
        help="With --all-projects, print the full per-project listing instead of "
             "the default one-line summary (--project always prints full detail)",
    )

    pdata_dump_parser = pdata_sub.add_parser(
        "dump", help="Publish the current local DB state to .pdata-db-dump/latest.sql"
    )
    dump_target = pdata_dump_parser.add_mutually_exclusive_group(required=True)
    dump_target.add_argument("--project", metavar="NAME")
    dump_target.add_argument(
        "--all-projects", action="store_true",
        help="Dump every project with a .db under project-db/",
    )
    pdata_dump_parser.add_argument(
        "--force", action="store_true",
        help="Publish even if this would overwrite an unresolved fork, or a dump that's "
             "ahead of local (the checksum-invalid-dump recovery path)",
    )

    pdata_rehydrate_parser = pdata_sub.add_parser(
        "rehydrate", help="Fast-forward the local DB from the published dump, if it's ahead"
    )
    rehydrate_target = pdata_rehydrate_parser.add_mutually_exclusive_group(required=True)
    rehydrate_target.add_argument("--project", metavar="NAME")
    rehydrate_target.add_argument(
        "--all-projects", action="store_true",
        help="Rehydrate every project with a .db under project-db/",
    )
    pdata_rehydrate_parser.add_argument(
        "--force", action="store_true",
        help="Fast-forward even over a local revision the dump doesn't dominate",
    )

    pdata_resolve_parser = pdata_sub.add_parser(
        "resolve",
        help="Diagnose (default) or apply per-record local/dump choices for a "
             "cross-machine fork",
    )
    resolve_target = pdata_resolve_parser.add_mutually_exclusive_group(required=True)
    resolve_target.add_argument("--project", metavar="NAME")
    resolve_target.add_argument(
        "--all-projects", action="store_true",
        help="Diagnose every project with a .db under project-db/ (diagnostic mode only - "
             "--choice requires --project)",
    )
    pdata_resolve_parser.add_argument(
        "--choice", action="append", default=[], metavar="ID=local|dump",
        help="Apply a per-record resolution; may repeat. Requires --project. Without any "
             "--choice, resolve runs in diagnostic (dry-run) mode and prints the diff.",
    )

    pdata_sync_check_parser = pdata_sub.add_parser(
        "sync-check",
        help="Rehydrate-check then (only if no rehydrate happened) dump-check - the unattended "
             "trigger behind the hourly pdata-sync-hourly ccsched job",
    )
    sync_check_target = pdata_sync_check_parser.add_mutually_exclusive_group(required=True)
    sync_check_target.add_argument("--project", metavar="NAME")
    sync_check_target.add_argument(
        "--all-projects", action="store_true",
        help="Sync-check every project with a .db under project-db/",
    )
    # Deliberately no --force. This is the automatic, repeating trigger: overriding a fork or a
    # checksum failure is a decision a human makes with `ccst pdata dump/rehydrate --force`,
    # never one an hourly cron job makes on their behalf.

    # ---- machine-identity ----
    machine_identity_parser = sub.add_parser(
        "machine-identity", help="This laptop's identity for pdata's cross-machine vector clock"
    )
    machine_identity_sub = machine_identity_parser.add_subparsers(dest="verb", metavar="<verb>")
    machine_identity_sub.required = True

    machine_identity_sub.add_parser("show", help="Print the resolved machine id and its confirmed state")

    machine_identity_confirm_parser = machine_identity_sub.add_parser(
        "confirm", help="Store this laptop's confirmed machine id"
    )
    machine_identity_confirm_parser.add_argument("--name", required=True, metavar="NAME")

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
    m_ccmsg = migrate_sub.add_parser(
        "ccmsg",
        help="Migrate the flat-file message store into ccmsg.db (non-destructive)")
    m_ccmsg.add_argument("--old-root", default=None, metavar="PATH")
    m_ccmsg.add_argument("--backup-dir", default=None, metavar="PATH")
    m_ccmsg.add_argument("--dry-run", action="store_true")
    m_telemetry = migrate_sub.add_parser(
        "telemetry",
        help="Migrate fires.jsonl (+ rotated slots) into telemetry.db (non-destructive)")
    m_telemetry.add_argument("--source-dir", default=None, metavar="PATH")
    m_telemetry.add_argument("--dest-dir", default=None, metavar="PATH")
    m_telemetry.add_argument("--dry-run", action="store_true")
    m_all = migrate_sub.add_parser(
        "all",
        help="Run every one-shot data-store migration (sessions, ccmsg, ccsched, "
             "telemetry) in sequence. Run from a plain terminal, not inside a "
             "Claude Code session.")
    m_all.add_argument("--dry-run", action="store_true")

    # ---- repair ----
    repair_parser = sub.add_parser("repair", help="Repair known sessions.db/store corruption")
    repair_sub = repair_parser.add_subparsers(dest="verb", metavar="<verb>")
    repair_sub.required = True
    r_sessions = repair_sub.add_parser(
        "sessions",
        help=(
            "Fix sessions.db rows whose project_dir is not an absolute path (invisible "
            "to `ccl`/`ccs --global`) by re-resolving them from on-disk cc-sessions/ "
            "directories. Dry-run by default."
        ),
    )
    r_sessions_mode = r_sessions.add_mutually_exclusive_group()
    r_sessions_mode.add_argument(
        "--execute", action="store_true",
        help="Apply the repair (default: dry-run, print what would change).",
    )
    r_sessions_mode.add_argument(
        "--dry-run", action="store_true",
        help="Print what would change without writing (this is the default).",
    )
    r_sessions.add_argument(
        "--sessions-db", default=None, metavar="PATH",
        help="sessions.db path override (default: from CCST_SESSIONS_DIR)",
    )

    # ---- ccsched-jobs ----
    ccsched_jobs_parser = sub.add_parser(
        "ccsched-jobs", help="Provision CCST-bundled ccsched jobs",
    )
    ccsched_jobs_sub = ccsched_jobs_parser.add_subparsers(dest="verb", metavar="<verb>")
    ccsched_jobs_sub.required = True
    ccsched_jobs_install_parser = ccsched_jobs_sub.add_parser(
        "install", help="Register bundled jobs not already present (dry run by default)",
    )
    ccsched_jobs_install_parser.add_argument(
        "--apply", action="store_true", help="Register jobs (default: dry run)",
    )
    ccsched_jobs_install_parser.add_argument(
        "--reinstall", action="append", metavar="JOB_ID", default=[],
        help="Bring back a bundled job you previously removed with 'ccsched remove' "
             "(repeatable). Without this, a deleted bundled job is reported but never "
             "silently re-added.",
    )

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
            "Run all install steps (skills, hooks, shell, claude-md, scheduled jobs) then "
            "health-check. Dry run by default; pass --apply to write changes. ccst runs "
            "this for you automatically after an upgrade — use it directly to preview the "
            "changes, to pass per-category target overrides, or to see why an automatic "
            "sync failed."
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
    ie_parser.add_argument(
        "--skills-target",
        metavar="PATH",
        help="Override the skills install target (default: ~/.claude/skills)",
    )
    ie_parser.add_argument(
        "--hooks-target",
        metavar="PATH",
        help="Override the hooks settings.json target (default: ~/.claude/settings.json)",
    )
    ie_parser.add_argument(
        "--fragments-dir",
        metavar="PATH",
        help="Override the shell fragments dir for the ccl() install (default: ~/.shellrc.d)",
    )
    ie_parser.add_argument(
        "--claude-md-target",
        metavar="PATH",
        help="Override the global CLAUDE.md target (default: ~/.claude/CLAUDE.md)",
    )

    # ---- context-override ----
    p_context_override = sub.add_parser(
        "context-override",
        help="Toggle the /context-override flag for the current session.",
    )
    p_context_override.add_argument(
        "action", nargs="?", default="on", choices=["on", "off", "status"]
    )

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.noun is None:
        parser.print_help(sys.stderr)
        sys.exit(1)

    from cc_session_tools.lib import install_sync

    # Bring ~/.claude's registration into line with the installed version
    # before dispatching. What goes stale on an upgrade is registration, not
    # code: skills symlinks and `ccst hooks run`'s dynamic import both resolve
    # into the uv tools path that --reinstall reuses in place, but a hook or
    # skill added in a newer version isn't wired into settings.json /
    # ~/.claude/skills until install-everything runs, so it silently never
    # fires. Wheels have no post-install hook, so the first ccst process after
    # an upgrade is the earliest possible moment.
    #
    # Cost on a carrier invocation is one readonly SQLite open/read/close,
    # 0.56 ms against a 94 ms bare process start. `ccst hooks run <verb>` is
    # exempt and doesn't pay even that - not for the per-call cost but for the
    # invocation count (every tool call in every open session) and because
    # rewriting settings.json from inside a hook Claude Code invoked from
    # settings.json is a race no atomic write makes tidy. See
    # install_sync.is_auto_sync_exempt for the full exempt set.
    #
    # Placement is after parse_args and after the usage check on purpose: an
    # invalid command line still fails at argparse without triggering an
    # apply, --version/--help/bare ccst never trigger one, and a successful
    # apply is in place before the requested command runs.
    install_sync.ensure_synced(
        noun=args.noun,
        verb=getattr(args, "verb", None),
        installed_version=__version__,
    )

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
        if args.verb == "prune":
            sys.exit(_cmd_gc_prune(args))

    if args.noun == "pdata":
        if args.verb == "add":
            sys.exit(_cmd_pdata_add(args))
        if args.verb == "schema":
            if args.subverb == "add-field":
                sys.exit(_cmd_pdata_schema_add_field(args))
            if args.subverb == "list":
                sys.exit(_cmd_pdata_schema_list(args))
            if args.subverb == "show":
                sys.exit(_cmd_pdata_schema_show(args))
        if args.verb == "get":
            sys.exit(_cmd_pdata_get(args))
        if args.verb == "list":
            sys.exit(_cmd_pdata_list(args))
        if args.verb == "query":
            sys.exit(_cmd_pdata_query(args))
        if args.verb == "update":
            sys.exit(_cmd_pdata_update(args))
        if args.verb == "delete":
            sys.exit(_cmd_pdata_delete(args))
        if args.verb == "restore":
            sys.exit(_cmd_pdata_restore(args))
        if args.verb == "init":
            sys.exit(_cmd_pdata_init(args))
        if args.verb == "reorganize":
            sys.exit(_cmd_pdata_reorganize(args))
        if args.verb == "rename-group":
            sys.exit(_cmd_pdata_rename_group(args))
        if args.verb == "reconcile-session-output":
            sys.exit(_cmd_pdata_reconcile_session_output(args))
        if args.verb == "verify":
            sys.exit(_cmd_pdata_verify(args))
        if args.verb == "dump":
            sys.exit(_cmd_pdata_dump(args))
        if args.verb == "rehydrate":
            sys.exit(_cmd_pdata_rehydrate(args))
        if args.verb == "resolve":
            sys.exit(_cmd_pdata_resolve(args))
        if args.verb == "sync-check":
            sys.exit(_cmd_pdata_sync_check(args))

    if args.noun == "machine-identity":
        if args.verb == "show":
            sys.exit(_cmd_machine_identity_show(args))
        if args.verb == "confirm":
            sys.exit(_cmd_machine_identity_confirm(args))

    if args.noun == "sessions":
        if args.verb == "migrate":
            sys.exit(_cmd_sessions_migrate(args))
        if args.verb == "list":
            sys.exit(_cmd_sessions_list(args))

    if args.noun == "migrate":
        if args.verb == "ccsched":
            sys.exit(_cmd_migrate_ccsched(args))
        if args.verb == "ccmsg":
            sys.exit(_cmd_migrate_ccmsg(args))
        if args.verb == "telemetry":
            sys.exit(_cmd_migrate_telemetry(args))
        if args.verb == "all":
            sys.exit(_cmd_migrate_all(args))

    if args.noun == "repair":
        if args.verb == "sessions":
            sys.exit(_cmd_repair_sessions(args))

    if args.noun == "ccsched-jobs":
        if args.verb == "install":
            sys.exit(_cmd_ccsched_jobs_install(args))

    if args.noun == "claude-md":
        if args.verb == "install":
            sys.exit(_cmd_claude_md_install(args))
        if args.verb == "uninstall":
            sys.exit(_cmd_claude_md_uninstall(args))

    if args.noun == "install-everything":
        sys.exit(_cmd_install_everything(args))

    if args.noun == "context-override":
        sys.exit(_cmd_context_override(args))


if __name__ == "__main__":
    main()
