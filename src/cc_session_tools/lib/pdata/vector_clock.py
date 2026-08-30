"""Pure vector-clock comparison/merge for ccst pdata's cross-machine sync (spec: "The vector
clock"). No I/O, no SQLite — operates on a plain {machine_id: revision} mapping so it's trivially
unit-testable; vector_clock_store.py (Task 2) is the only module that touches pdata_meta rows."""
from __future__ import annotations

import enum


class Comparison(enum.Enum):
    LOCAL_DOMINATES = "local_dominates"  # dump is stale/already-incorporated, or equal — no-op
    DUMP_DOMINATES = "dump_dominates"     # clean fast-forward: safe to rehydrate
    FORK = "fork"                        # each side has a revision the other lacks


def compare(*, local: dict[str, int], dump: dict[str, int]) -> Comparison:
    """Spec's "Comparison rule". Missing entries on either side default to 0 — a machine neither
    vector has ever heard of contributes nothing either way."""
    keys = set(local) | set(dump)
    local_ahead = any(local.get(k, 0) > dump.get(k, 0) for k in keys)
    dump_ahead = any(dump.get(k, 0) > local.get(k, 0) for k in keys)
    if local_ahead and dump_ahead:
        return Comparison.FORK
    if dump_ahead:
        return Comparison.DUMP_DOMINATES
    return Comparison.LOCAL_DOMINATES  # local strictly ahead, or exactly equal


def bump_own(vector: dict[str, int], machine_id: str) -> None:
    """Mutates vector in place — the one call every local pdata write makes, in the same
    transaction as the data change (binding invariant #1)."""
    vector[machine_id] = vector.get(machine_id, 0) + 1


def merge(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    """Elementwise max, union of keys. Used on a clean fast-forward (adopt the dump's vector,
    which already dominates) and after a manual resolve (adopt the other side's revision,
    max every other machine)."""
    keys = set(a) | set(b)
    return {k: max(a.get(k, 0), b.get(k, 0)) for k in keys}
