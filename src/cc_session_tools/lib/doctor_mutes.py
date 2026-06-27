"""Persistent mute store for ``ccst doctor --drift``.

A *mute* records that the user has acknowledged a specific doctor check (by its
stable ``name``, e.g. ``version:pypi`` or ``skill:foo``) and does not want the
drift monitor to flag it again until it is un-muted.

The store is a small JSON object mapping check name -> ISO-8601 date the mute was
added. File I/O lives here so the check/filter/format logic in :mod:`doctor`
stays pure.
"""
from __future__ import annotations

import json
from pathlib import Path


def default_mutes_path() -> Path:
    """Canonical mute-store path.

    Lives under ``~/.claude`` (persistent state), not ``~/.cache`` — these are
    user acknowledgements that must survive a cache clear.
    """
    return Path.home() / ".claude" / "cc-doctor-mutes.json"


def load_mutes(path: Path) -> dict[str, str]:
    """Return the mute map (check name -> ISO date). Empty if absent/corrupt."""
    if not path.exists():
        return {}
    try:
        with path.open() as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def add_mute(path: Path, name: str, *, today: str) -> dict[str, str]:
    """Mute ``name`` (recording ``today`` as the mute date) and persist."""
    mutes = load_mutes(path)
    mutes[name] = today
    _write(path, mutes)
    return mutes


def remove_mute(path: Path, name: str) -> bool:
    """Un-mute ``name``. Return True if it was muted, False if it was not."""
    mutes = load_mutes(path)
    if name not in mutes:
        return False
    del mutes[name]
    _write(path, mutes)
    return True


def _write(path: Path, mutes: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(mutes, f, indent=2, sort_keys=True)
        f.write("\n")
