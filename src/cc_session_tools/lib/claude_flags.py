from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from cc_session_tools.hooks_install import write_json_atomic
from cc_session_tools.lib.paths import data_home

_CLAUDE_FLAGS_DIR_ENV = "CCST_CLAUDE_FLAGS_DIR"


def _cache_dir() -> Path:
    env = os.environ.get(_CLAUDE_FLAGS_DIR_ENV, "").strip()
    return Path(env) if env else data_home()


def _cache_file() -> Path:
    return _cache_dir() / "claude-flags.json"


def get_claude_flags() -> set[str]:
    """Return set of valid long-form claude flags (e.g. {'--model', '--debug'}).

    Parses `claude --help` at runtime; cached by binary mtime.
    Returns empty set if claude is not on PATH or help parse fails.
    """
    claude = shutil.which("claude")
    if not claude:
        return set()

    try:
        mtime = Path(claude).stat().st_mtime
    except OSError:
        return set()

    cache_file = _cache_file()
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
            if cached.get("mtime") == mtime and cached.get("path") == claude:
                return set(cached["flags"])
        except (json.JSONDecodeError, KeyError, OSError):
            pass

    try:
        result = subprocess.run(
            ["claude", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        text = result.stdout + result.stderr
    except (OSError, subprocess.TimeoutExpired):
        return set()

    # Match --flag-name at word boundaries, exclude short -f flags
    flags = set(re.findall(r"(?<!\w)(--[\w-]+)", text))

    try:
        _cache_dir().mkdir(parents=True, exist_ok=True)
        write_json_atomic(
            cache_file,
            {"mtime": mtime, "path": claude, "flags": sorted(flags)},
        )
    except OSError:
        pass

    return flags
