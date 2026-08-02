#!/usr/bin/env python3
"""Compatibility shim for the move-session skill.

The authoritative implementation of session location/naming rules lives in
`cc_session_tools.lib.rules` (and `cc_session_tools.lib.roots`) in the same
repo as this skill (`~/repos/claude-code-session-tools`). This shim re-exports
that public API so move_session.py and the move-session test suite can keep
importing `cc_session_rules` without change.

If you are working on the rules themselves, edit
`src/cc_session_tools/lib/rules.py` and `.../roots.py` - NOT this file.

Roots discovery is env-var driven:
  - CLAUDE_SESSION_TOOLS_REPO_ROOT - "loose" root (no naming conventions).
  - CLAUDE_SESSION_TOOLS_PROJ_ROOT - "strict" root; sessions there must use a
    `<project>-<descriptor>` tag.
Both are exported from ~/.bashrc. Tests should use `monkeypatch.setenv`
rather than monkeypatching constants on this module.
"""
from __future__ import annotations

import sys
from pathlib import Path

from cc_session_tools.lib.roots import (  # noqa: F401
    PROJ_ROOT_ENV,
    REPO_ROOT_ENV,
    RootsConfigError,
    is_valid_session_cwd,
    load_session_roots,
    matched_session_root,
    proj_root,
    repo_root,
    strict_root_path,
)
from cc_session_tools.lib.rules import (  # noqa: F401
    DATE_PREFIX_RE,
    PROJECT_NAME_STRICT_RE,
    TAG_NEW_RE,
    TAG_SUFFIX_FORMAT_RE,
    check_session_destination,
    check_session_init,
    encode_cwd,
    is_strict_root,
    validate_new_tag,
    validate_strict_project_name,
    validate_strict_tag_suffix,
    validate_tag_suffix_no_spaces,
)


if __name__ == "__main__":
    # Preserve the legacy `python3 cc_session_rules.py check-init ...` entry point
    # used by ccd's bash wrapper. Delegate to the lib's rules module CLI if it
    # has one; otherwise fall back to a minimal CLI implementation.
    try:
        from cc_session_tools.lib.rules import main as _main  # type: ignore
    except ImportError:
        import argparse

        def _cmd_check_init(args: argparse.Namespace) -> int:
            cwd = Path(args.cwd).expanduser()
            try:
                cwd_abs = cwd.resolve(strict=True)
            except (FileNotFoundError, OSError) as e:
                print(f"ccd: cwd does not exist: {cwd} ({e})", file=sys.stderr)
                return 1
            ok, errors = check_session_init(cwd_abs, args.tag_suffix, force=args.force)
            if ok:
                return 0
            print("ccd: validation failed:", file=sys.stderr)
            for e in errors:
                for line in e.splitlines():
                    print(f"  {line}", file=sys.stderr)
            if not args.force:
                print("  (use --force to bypass root and strict-root checks)",
                      file=sys.stderr)
            return 1

        def _main(argv: list[str] | None = None) -> int:
            ap = argparse.ArgumentParser()
            sub = ap.add_subparsers(dest="cmd", required=True)
            p_init = sub.add_parser("check-init")
            p_init.add_argument("--cwd", required=True)
            p_init.add_argument("--tag-suffix", required=True)
            p_init.add_argument("--force", action="store_true")
            p_init.set_defaults(func=_cmd_check_init)
            args = ap.parse_args(argv)
            return args.func(args)

    sys.exit(_main())
