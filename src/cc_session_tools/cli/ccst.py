"""ccst — Claude Code Session Tools umbrella CLI.

Entry point: ccst <noun> <verb> [options]

Current subcommands:
  hooks install [--hook <name>]  Merge hook entries from the bundled set (or a
                                 custom --source) into a target settings.json.
  hooks uninstall [--hook <name>] Remove hook entries from a target settings.json.
  hooks run <name>               Run a Claude Code hook by name.
                                 Available hooks: bash-security-review,
                                 confirm-8digit, edit-write-audit, prompt-guard,
                                 session-end, session-tag.
  skills install                 Symlink bundled skills into ~/.claude/skills/.
  skills uninstall [--skill <name>] Remove bundled skill symlinks.
  doctor                         Health-check: PATH, env vars, settings.json,
                                 hook registrations, skill symlinks, PyPI drift.
  shell install                  Add the ccl() wrapper function to ~/.bashrc /
                                 ~/.zshrc between sentinel markers.
  shell uninstall                Remove the ccl() block from shell rc files.
  telemetry trim                 Trim ~/.claude/hooks/fires.jsonl by size / age.
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

from cc_session_tools import __version__
from cc_session_tools.hooks_install import load_json, merge_hook_settings, write_json_atomic


HOOK_VERBS: dict[str, str] = {
    "bash-security-review": "cccs_hooks.bash_security_review",
    "confirm-8digit": "cccs_hooks.confirm_8digit",
    "prompt-guard": "cccs_hooks.prompt_guard",
    "edit-write-audit": "cccs_hooks.edit_write_audit",
    "session-end": "cccs_hooks.session_end",
    "session-tag": "cccs_hooks.session_tag",
}


HOOK_DESCRIPTIONS: dict[str, str] = {
    "bash-security-review": "Reviews shell commands for security risks (tiered: allowlist, heuristics, LLM)",
    "confirm-8digit": "Enforces an 8-digit confirmation gate before risky tool calls",
    "prompt-guard": "Scans user prompts for credential shapes and prompt-injection patterns",
    "edit-write-audit": "Audits Edit/Write/NotebookEdit paths for sensitive or out-of-root writes",
    "session-end": "Warns on stale WORKLOG and uncommitted changes when Claude stops",
    "session-tag": "Writes the session tag file so ccusage can map UUIDs to human-readable names",
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
    has_error = False
    target_dir.mkdir(parents=True, exist_ok=True)

    for action, skill_src, dest in decisions:
        if action == SkillAction.ALREADY_CORRECT:
            continue  # no-op

        if action == SkillAction.NON_SYMLINK_EXISTS and not args.force:
            print(
                f"error: {dest} exists and is not a symlink; use --force to move it aside",
                file=sys.stderr,
            )
            has_error = True
            continue

        if action == SkillAction.WRONG_TARGET and not args.force:
            print(
                f"error: {dest} is a symlink to a different path; use --force to replace it",
                file=sys.stderr,
            )
            has_error = True
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
        print(f"  linked: {dest} -> {skill_src}")

    if has_error:
        return 1

    print(f"\nDone — symlinks written to {target_dir}")
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


def _bundle_inventory(bundle: dict) -> list[tuple[str, str, str | None, str]]:
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
    """Print a Hook | Status | Description | Event table to stdout."""
    headers = ("Hook", "Status", "Description", "Event")
    if not inventory:
        return

    rows: list[tuple[str, str, str, str]] = []
    for name, event, matcher, cmd in inventory:
        status = "install" if (event, matcher, cmd) in added_keys else "already-installed"
        event_label = f"{event}[{matcher}]" if matcher else event
        description = HOOK_DESCRIPTIONS.get(name, "")
        rows.append((name, status, description, event_label))

    widths = [
        max([len(headers[i])] + [len(r[i]) for r in rows]) for i in range(4)
    ]
    fmt = f"{{:<{widths[0]}}}  {{:<{widths[1]}}}  {{:<{widths[2]}}}  {{:<{widths[3]}}}"
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*row))


def _list_bundle_hook_names(bundle: dict) -> list[str]:
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


def _filter_bundle_to_hook(bundle: dict, hook_name: str) -> dict | None:
    """Return a single-entry bundle dict containing only the named hook, or None."""
    prefix = "ccst hooks run "
    target_cmd = f"{prefix}{hook_name}"
    filtered_hooks: dict = {}

    for event, blocks in bundle.get("hooks", {}).items():
        for block in blocks:
            matching_entries = [
                h for h in block.get("hooks", [])
                if h.get("command") == target_cmd
            ]
            if matching_entries:
                new_block: dict = {"hooks": matching_entries}
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
    settings: dict,
    hook_name: str | None,
    removed: list[tuple[str, str | None, str]],
) -> dict:
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
    """Run the full health-check suite."""
    from cc_session_tools.lib.doctor import (
        Status,
        format_results,
        run_all_checks,
    )

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

    results = run_all_checks(
        installed_version=__version__,
        settings_path=settings_path,
        bundle_path=bundle_path,
        skills_source_dir=skills_source_dir,
        skills_target_dir=skills_target_dir,
        env=env_vars,
        skip_pypi=args.no_pypi,
    )

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


# ---------- hooks run ----------


def _cmd_hooks_run(args: argparse.Namespace) -> int:
    module = importlib.import_module(HOOK_VERBS[args.hook])
    rc = module.main()
    return int(rc) if rc is not None else 0


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
        help="Trim ~/.claude/hooks/fires.jsonl by size and/or age",
    )
    telemetry_trim_parser.add_argument(
        "--max-size",
        type=float,
        metavar="MB",
        help="Rotate fires.jsonl when it exceeds this size in MB",
    )
    telemetry_trim_parser.add_argument(
        "--max-age-days",
        type=int,
        metavar="N",
        help="Drop lines older than N days",
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
        help="Hooks directory (default: ~/.claude/hooks/)",
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


if __name__ == "__main__":
    main()
