from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path


_CACHE_DIR: Path = Path.home() / ".cache" / "cc-session-tools"
_CACHE_FILE: Path = _CACHE_DIR / "claude-flags.json"


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

    if _CACHE_FILE.exists():
        try:
            cached = json.loads(_CACHE_FILE.read_text())
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
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps({"mtime": mtime, "path": claude, "flags": sorted(flags)})
        )
    except OSError:
        pass

    return flags
