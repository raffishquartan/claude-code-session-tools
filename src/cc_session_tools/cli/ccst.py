"""ccst — Claude Code Session Tools umbrella CLI.

Entry point: ccst <noun> <verb> [options]

Current subcommands:
  hooks install         Merge hook entries from a source settings.json into a target.
  hooks run <name>      Run a Claude Code hook by name, reading the event payload from stdin.
                        Available hooks: bash-security-review, confirm-8digit, edit-write-audit,
                        prompt-guard, session-end, session-tag.
  skills install        Symlink bundled skills into ~/.claude/skills/ (dry run by default).
"""
from __future__ import annotations

import argparse
import datetime
import importlib
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


# ---------- skills install ----------


class SkillAction(str, Enum):
    CREATE = "create"
    ALREADY_CORRECT = "already-correct"
    WRONG_TARGET = "wrong-target"
    NON_SYMLINK_EXISTS = "non-symlink-exists"


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


def _cmd_hooks_install(args: argparse.Namespace) -> int:
    source_path = Path(args.source)
    target_path = Path(args.target)

    if not source_path.exists():
        print(f"error: source not found: {source_path}", file=sys.stderr)
        return 1

    source = load_json(source_path)
    target = load_json(target_path) if target_path.exists() else {}

    merged, additions = merge_hook_settings(source_settings=source, target_settings=target)

    if not additions:
        print("Already up to date — nothing to add.")
        return 0

    for add in additions:
        matcher_label = f" [{add.matcher}]" if add.matcher else ""
        print(f"  + {add.event}{matcher_label}: {add.command}")

    if args.apply:
        write_json_atomic(target_path, merged)
        print(f"\nWrote {target_path}")
    else:
        print(f"\nDry run — re-run with --apply to write {target_path}")

    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ccst",
        description="Claude Code Session Tools umbrella CLI",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="noun", metavar="<noun>")

    # hooks
    hooks_parser = sub.add_parser("hooks", help="Hook management commands")
    hooks_sub = hooks_parser.add_subparsers(dest="verb", metavar="<verb>")
    hooks_sub.required = True

    install_parser = hooks_sub.add_parser(
        "install",
        help="Merge hook entries from a source settings.json into a target",
    )
    install_parser.add_argument(
        "--source",
        required=True,
        metavar="PATH",
        help="Source settings.json to read hook entries from",
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

    # skills
    skills_parser = sub.add_parser("skills", help="Skill management commands")
    skills_sub = skills_parser.add_subparsers(dest="verb", metavar="<verb>")
    skills_sub.required = True

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

    return parser


def _cmd_hooks_run(args: argparse.Namespace) -> int:
    module = importlib.import_module(HOOK_VERBS[args.hook])
    rc = module.main()
    return int(rc) if rc is not None else 0


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.noun is None:
        parser.print_help(sys.stderr)
        sys.exit(1)

    if args.noun == "hooks" and args.verb == "install":
        sys.exit(_cmd_hooks_install(args))

    if args.noun == "hooks" and args.verb == "run":
        sys.exit(_cmd_hooks_run(args))

    if args.noun == "skills" and args.verb == "install":
        sys.exit(_cmd_skills_install(args))


if __name__ == "__main__":
    main()
