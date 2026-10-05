"""Named Anthropic API keys for ccdapi / ccrapi.

Keys live in one dedicated file (default ``~/.config/ccst/api-keys``, override
``CCST_API_KEYS_FILE``), one ``label=key`` per line with ``#`` comments. The file is parsed here,
never sourced by a shell, and key values are never included in error messages.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from cc_session_tools.lib.picker import pick_from_list

API_KEYS_FILE_ENV = "CCST_API_KEYS_FILE"
MAX_MENU_LABELS = 10


class ApiKeysError(Exception):
    """The keys file or the requested label cannot be used. Never contains key values."""


def keys_file_path() -> Path:
    override = os.environ.get(API_KEYS_FILE_ENV)
    if override:
        return Path(override)
    return Path.home() / ".config" / "ccst" / "api-keys"


def parse_keys(text: str) -> dict[str, str]:
    keys: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        label, sep, value = line.partition("=")
        label, value = label.strip(), value.strip()
        if not sep or not label or not value:
            raise ApiKeysError(f"line {lineno}: expected 'label=key'")
        if label in keys:
            raise ApiKeysError(f"line {lineno}: duplicate label '{label}'")
        keys[label] = value
    return keys


def load_keys(path: Path | None = None) -> dict[str, str]:
    path = path or keys_file_path()
    if not path.is_file():
        raise ApiKeysError(f"API keys file not found: {path}")
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ApiKeysError(f"{path} is readable by group or others; run: chmod 600 {path}")
    try:
        keys = parse_keys(path.read_text())
    except ApiKeysError as e:
        raise ApiKeysError(f"{path}: {e}") from e
    if not keys:
        raise ApiKeysError(f"{path} contains no keys")
    return keys


def validate_label(keys: dict[str, str], label: str) -> None:
    if label not in keys:
        raise ApiKeysError(
            f"no API key labelled '{label}'; available: {', '.join(sorted(keys))}"
        )


def choose_label(keys: dict[str, str]) -> str | None:
    """Menu pick of a label. Returns None if cancelled."""
    labels = sorted(keys)
    if not sys.stdin.isatty():
        raise ApiKeysError(f"no -k given and no terminal for a menu; labels: {', '.join(labels)}")
    if len(labels) > MAX_MENU_LABELS:
        raise ApiKeysError(
            f"more than {MAX_MENU_LABELS} keys; pass -k <label> (labels: {', '.join(labels)})"
        )
    idx = pick_from_list(labels)
    return None if idx is None else labels[idx]


def with_api_key(env: dict[str, str], key: str) -> None:
    """Make the launched claude authenticate with `key` instead of a subscription login."""
    env["ANTHROPIC_API_KEY"] = key
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
