"""This laptop's identity for ccst pdata's cross-machine vector clock (spec: "Machine identity").

Confirmation is an explicit, separate CLI command (ccst machine-identity confirm) — never
something a hook blocks on, since a SessionStart/SessionEnd/cron hook has no interactive tty.
resolve() always returns *something* usable; .confirmed tells the caller whether it's trustworthy
enough to silence the "unconfirmed" note."""
from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path

from cc_session_tools.hooks_install import write_json_atomic
from cc_session_tools.lib import paths

MACHINE_NAME_ENV = "CCST_MACHINE_NAME"


@dataclass(frozen=True, slots=True)
class MachineIdentity:
    machine_id: str
    confirmed: bool


def _store_path() -> Path:
    return paths.data_home() / "machine-identity.json"


def resolve() -> MachineIdentity:
    """CCST_MACHINE_NAME wins over the on-disk store, which wins over the raw hostname — the
    env var is for tests and one-off overrides; the store is what `confirm()` persists for every
    later process on this machine that doesn't set the env var."""
    env = os.environ.get(MACHINE_NAME_ENV)
    if env:
        return MachineIdentity(machine_id=env, confirmed=True)
    store = _store_path()
    if store.exists():
        data = json.loads(store.read_text())
        return MachineIdentity(machine_id=data["machine_id"], confirmed=True)
    return MachineIdentity(machine_id=socket.gethostname(), confirmed=False)


def confirm(name: str) -> None:
    if not name.strip():
        raise ValueError("machine name must not be empty or whitespace-only")
    store = _store_path()
    store.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(store, {"machine_id": name})


def check_collision(*, proposed: str, known_vector: dict[str, int]) -> bool:
    """True iff `proposed` would collide with a different machine already known to this
    project's vector — i.e. the vector has any entry other than `proposed` itself. A vector
    containing only `proposed` (or being empty) is not a collision — that's either this same
    machine continuing, or a brand-new project nobody has touched yet."""
    others = set(known_vector) - {proposed}
    return len(others) > 0
