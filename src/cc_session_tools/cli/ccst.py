"""ccst — Claude Code Session Tools umbrella CLI.

Entry point: ccst <noun> <verb> [options]

Current subcommands:
  hooks install   Merge hook entries from a source settings.json into a target.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cc_session_tools.hooks_install import load_json, merge_hook_settings, write_json_atomic


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
    sub = parser.add_subparsers(dest="noun", metavar="<noun>")
    sub.required = True

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
        default=str(Path.home() / "repos/claude-code-config-sync/config/settings.json"),
        metavar="PATH",
        help="Source settings.json to read hooks from",
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

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.noun == "hooks" and args.verb == "install":
        sys.exit(_cmd_hooks_install(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
