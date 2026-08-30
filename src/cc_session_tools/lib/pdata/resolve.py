"""Cross-machine fork resolution for `ccst pdata resolve` (spec: "Conflict handling &
notification" — the relational-integrity paragraph and the "Post-resolve vector-clock update"
paragraph). Diffs the live local `.db` against the project's published dump, record-by-record
with each record's base row and extension row paired as one unit, and applies a caller-supplied
set of per-record "local"/"dump" choices as one atomic transaction — never a blunt whole-database
overwrite (that is rehydrate.py's job for the clean fast-forward case; this module is what
`ccst pdata resolve` reaches for once vector_clock.compare() has already reported a genuine FORK).

Never auto-merges, never silently keeps one side and discards the other — every record surfaced
here needs an explicit choice from the caller (ultimately Chris, via the CLI/skill), matching the
existing `pm-pdata-conflict-resolution` skill's single-file-conflict framing exactly. Every
record: `apply_resolution` is all-or-nothing over the whole diff, for the reasons in its own
docstring.

Named types, one line each — see each type's own docstring for the full reasoning:
- `ApplyOutcome` — what `apply_resolution` did (`APPLIED`, or `LOCKED` for a transient lock).
- `ClassifyResult` — `_classify`'s `(id_collision, group_mismatch, is_delete_vs_update)`, named so
  the two callers' unpacking can't silently transpose a position.
- `BaseFields`/`RecordPayload` — the typed shape of one side's view of one record, replacing a
  bare `dict[str, object]` for the parts of it this module itself guarantees are fixed.
"""
from __future__ import annotations

import enum
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NamedTuple, TypedDict, TypeGuard

from cc_session_tools.lib import machine_identity
from cc_session_tools.lib.pdata import (
    dump,
    naming,
    repository,
    store,
    sync_lock,
    vector_clock,
    vector_clock_store,
)

Choice = Literal["local", "dump"]

_VALID_CHOICES = frozenset({"local", "dump"})


class ApplyOutcome(enum.Enum):
    """What `apply_resolution` did. Every genuine refusal (an unresolvable diff, a malformed or
    incomplete `choices`) still raises ValueError — see that function's docstring for why only
    the transient lock case is modelled as a return value."""

    APPLIED = "applied"  # the resolution was written and re-dumped
    LOCKED = "locked"  # another writer holds this project's .db right now — retry shortly


class BaseFields(TypedDict):
    """The six `records` columns a resolve ever compares or applies. `id` and `record_group` are
    deliberately absent: they identify the record rather than describing it, and both are carried
    by `RecordDiff`'s own fields instead (`record_group` per side, since the two can disagree)."""

    content: str
    file_path: str | None
    created_at: int
    updated_at: int
    version: int
    deleted_at: int | None


class RecordPayload(TypedDict):
    """One side's view of one record. `extension` is a column->value dict whose keys are the
    group's caller-defined extension fields — arbitrary per record_group, hence `object` values —
    and `None` when that side has no ext_<group> table for the record's group at all.

    That reading of `None` rests on an invariant this module does not itself enforce:
    `repository.get_extension_row` returns `None` both for a missing table and for an existing
    table with no row for this record_id, and it is `repository.insert_extension_row`'s "an
    extension row is always created alongside its base row" guarantee that rules the second case
    out. If that invariant were ever violated elsewhere (a partial migration, a hand-edited row),
    a diff built here would describe the affected side as having no extension table rather than
    surfacing the integrity violation."""

    base: BaseFields
    extension: dict[str, object] | None


@dataclass(frozen=True, slots=True)
class RecordDiff:
    """One resolvable unit: a `records` row (+ its `ext_<record_group>` row, if any) that differs
    between local and the dump, or exists on only one side. Base and extension are always paired
    here — never split into two separate diffs — per the spec's relational-integrity requirement.

    `local`/`dump` are each `None` if the record does not exist on that side, otherwise that
    side's `RecordPayload`.

    Three group-name-shaped fields, one job each: `local_record_group`/`dump_record_group` are the
    authoritative per-side truth (each `None` exactly when that side has no row) and always safe
    to read; `record_group` is a single derived convenience value — whichever side's snapshot was
    available, local's when both are — for the common case where the two agree and a caller just
    wants one name. When `group_mismatch` is True, `record_group` is that one side's name standing
    in for a real disagreement, so read the two per-side fields instead.
    """

    record_id: int
    record_group: str
    local: RecordPayload | None
    dump: RecordPayload | None
    local_record_group: str | None
    dump_record_group: str | None
    is_delete_vs_update: bool
    id_collision: bool
    group_mismatch: bool


@dataclass(frozen=True, slots=True)
class SchemaFieldDiff:
    """`record_group_fields` can diverge independently of any record — its own diff category."""

    record_group: str
    field_name: str
    present_locally: bool
    present_in_dump: bool


@dataclass(frozen=True, slots=True)
class ResolveDiff:
    records: list[RecordDiff]
    schema_fields: list[SchemaFieldDiff]
    dump_vector: dict[str, int]
    dump_machine_id: str | None


@dataclass(frozen=True, slots=True)
class _Snapshot:
    """One side's view of one record_id — record_group kept separate from payload so
    group-mismatch detection (see `_classify`) can compare it without reaching into the payload
    dict."""

    record_group: str
    payload: RecordPayload


def is_choice(value: str) -> TypeGuard[Choice]:
    """True iff `value` is one of the two choices `apply_resolution` accepts, narrowing it to
    `Choice` for the caller. The one sanctioned way to turn an untrusted string (CLI argument,
    skill-supplied value) into the closed set the rest of this module is typed against."""
    return value in _VALID_CHOICES


def narrow_choices(choices: dict[int, str]) -> dict[int, Choice]:
    """Validate a `{record_id: str}` mapping at the untrusted boundary and return it typed as
    `{record_id: Choice}`, raising ValueError naming the first offending record_id otherwise.

    `apply_resolution` re-checks the same thing at runtime (its parameter is statically closed,
    but nothing stops an unchecked call from an untyped caller), so this is not the only guard —
    it exists so a CLI/skill caller has a boundary at which arbitrary strings become `Choice`
    without a cast, and so both paths raise the identical message."""
    narrowed: dict[int, Choice] = {}
    for record_id, choice in choices.items():
        if not is_choice(choice):
            raise ValueError(_invalid_choice_message(record_id, choice))
        narrowed[record_id] = choice
    return narrowed


def _invalid_choice_message(record_id: int, choice: str) -> str:
    return f"invalid choice {choice!r} for record_id {record_id}: must be 'local' or 'dump'"


def _read_valid_dump_info(project: str, project_root: Path) -> dump.DumpInfo:
    """The published dump's header (vector/machine_id), refusing a dump that fails its checksum —
    there is nothing reliable to diff against in that case (spec: "Checksum failure... nothing
    reliable to diff"; the fix is `ccst pdata dump --force`, not a resolve)."""
    info = dump.read_latest(project_root)
    if not info.checksum_valid:
        raise ValueError(
            f"dump for project {project!r} fails its checksum check — nothing reliable to diff "
            f"against (see `ccst pdata dump --force` to republish from local)"
        )
    return info


def _build_diff(
    local_conn: sqlite3.Connection, dump_conn: sqlite3.Connection, info: dump.DumpInfo,
) -> ResolveDiff:
    """The whole diff, computed from one already-open pair of connections. Shared by
    `diff_against_dump` (which opens, diffs, closes) and `apply_resolution` (which keeps the same
    pair open across diff, validation and write) so the two can never diff differently."""
    return ResolveDiff(
        records=_diff_records(local_conn, dump_conn),
        schema_fields=_diff_schema_fields(local_conn, dump_conn),
        dump_vector=info.vector,
        dump_machine_id=info.machine_id,
    )


def diff_against_dump(project: str) -> ResolveDiff:
    """Diff project's live local `.db` against its published dump. Raises ValueError if the dump
    fails its checksum — there is nothing reliable to diff against in that case.

    Display/diagnostic use only. `apply_resolution` deliberately does NOT call this: it builds
    its own diff from the connections it then writes through, so the diff it validates `choices`
    against and the data it applies come from one snapshot rather than two."""
    project_root = store.project_root(project)
    info = _read_valid_dump_info(project, project_root)

    dump_conn = _open_dump(project_root)
    try:
        local_conn = repository.connect(project)
        try:
            return _build_diff(local_conn, dump_conn, info)
        finally:
            local_conn.close()
    finally:
        dump_conn.close()


def apply_resolution(project: str, choices: dict[int, Choice]) -> ApplyOutcome:
    """Apply choices (`{record_id: "local" | "dump"}`) for the current diff. All-or-nothing:
    `choices` must name EVERY record_id in the diff, no more and no fewer, or nothing is applied
    at all.

    A partial resolve is refused because the vector-clock bookkeeping below is not per-record —
    it declares the dump machine's revision fully incorporated for the whole project. Publishing
    that while some records were left unreconciled would tell the other machine its state is
    already absorbed; its next check would read DUMP_DOMINATES and wholesale-replace its own real
    edits with this side's, with no prompt. Refusing also closes the id-collision guard, which a
    subset call could otherwise sidestep by simply omitting the colliding id.

    One call is one transaction covering every chosen record plus the post-resolve vector-clock
    bookkeeping, followed by an immediate re-dump once that transaction commits (the spec's exact
    three-step "Post-resolve vector-clock update", whose step 3 is that immediate re-dump).

    The diff is built here, from the same dump connection and the same local connection the write
    below goes through, rather than by calling `diff_against_dump` (which opens and closes its
    own pair). That is what makes one call internally consistent: the vector merged into
    pdata_meta and the column types/field descriptions copied out of the dump all come from one
    read of one dump, never from two independently-opened ones that a concurrent OneDrive
    delivery or `ccst pdata dump --force` could have made disagree. It does NOT (and cannot)
    guarantee that `choices` a human decided against an earlier diagnostic `ccst pdata resolve`
    run still describes the same data minutes later — if something changed in between, the fresh
    diff either still agrees with `choices` or fails one of the validations below loudly.

    Returns APPLIED on success, or LOCKED if another writer holds this project's `.db` at the
    moment the write would start (nothing is written in that case — retry shortly). LOCKED is a
    return value rather than a raise for the same reason `rehydrate.RehydrateOutcome.DEFERRED`
    is: it is transient and expected, not a refusal. Every other failure below stays a
    `ValueError`. Those nine (invalid choice, checksum-invalid, schema-only-fork, empty choices,
    unknown record ids, id collisions, group mismatches, a partial resolve, and a dumpless choice)
    are genuine boundary-validation failures — a given `choices` either
    can or cannot be applied at all — and no automatic caller anywhere in `src/` branches on
    which category fired (unlike `rehydrate`/`dump`, whose hook and hourly-job callers are why
    those modules return typed outcomes); a human or session reads the message. Keeping them as
    ValueError is a deliberate scope decision, not an oversight."""
    # A pure input-shape check — no I/O, doesn't need a project/dump to exist — kept first so it
    # never depends on (or is masked by) anything below. `choices` is statically closed to
    # `Choice`, but it crosses a real untrusted boundary (CLI-parsed strings, skill-supplied
    # values) so the runtime check stays. Runs correctly even when choices is empty (the loop is
    # simply a no-op).
    for record_id, choice in choices.items():
        if not is_choice(choice):
            raise ValueError(_invalid_choice_message(record_id, choice))

    project_root = store.project_root(project)
    info = _read_valid_dump_info(project, project_root)

    dump_conn = _open_dump(project_root)
    try:
        local_conn = repository.connect(project)
        try:
            return _apply_resolution(
                project, project_root, choices, local_conn, dump_conn, info,
            )
        finally:
            local_conn.close()
    finally:
        dump_conn.close()


def _apply_resolution(
    project: str,
    project_root: Path,
    choices: dict[int, Choice],
    local_conn: sqlite3.Connection,
    dump_conn: sqlite3.Connection,
    info: dump.DumpInfo,
) -> ApplyOutcome:
    """`apply_resolution`'s body, with both connections already open and owned by the caller —
    diff, validate, write, re-dump, all against this one pair. See `apply_resolution`'s docstring
    for the contract; this split exists only so the two `try/finally` close-blocks above don't
    have to wrap a hundred lines of logic."""
    diff = _build_diff(local_conn, dump_conn, info)

    # Checked before anything about `choices` — including before the empty-choices check below,
    # since a schema-only fork (no differing records, only record_group_fields drift) would
    # otherwise report a generic "give me at least one choice" with no way to satisfy it: there
    # is no record_id to put in choices for a schema-catalog-only difference. Refusing here is
    # the minimum-honest behaviour, not the full fix (which would need a field-level choice
    # surface this function doesn't have yet — the resolve.py module docstring covers this
    # module's per-record scope; extending to per-field choices is a real follow-up, not
    # something to build silently as a side effect of this fix) — but it closes the two real
    # failure modes an unfixed version has: an unresolvable dead end (no argument clears the
    # fork), and a resolve of the record-level diff alone silently publishing a vector that
    # claims the dump machine is fully incorporated while its schema-catalog additions were
    # dropped — the exact same "vector lies about full incorporation" shape already fixed for
    # partial record resolution above, surviving in this other category until now.
    if diff.schema_fields:
        offending = sorted({(f.record_group, f.field_name) for f in diff.schema_fields})
        raise ValueError(
            f"record_group_fields (the schema catalog) differs from the dump for {offending} — "
            f"apply_resolution has no way to resolve a schema-catalog difference on its own (it "
            f"only takes per-record local/dump choices), so it refuses to publish anything while "
            f"any exists: doing so would either leave this unresolvable (if no record also "
            f"differs) or silently drop one side's field registration while claiming the dump "
            f"machine is fully incorporated (if some records do differ and get resolved). "
            f"Reconcile the schema catalog first via `ccst pdata schema add-field` on whichever "
            f"machine is missing a field, matching the other's definition, then retry."
        )

    if not choices:
        raise ValueError("apply_resolution requires at least one record_id in choices")

    by_id = {record_diff.record_id: record_diff for record_diff in diff.records}

    unknown = sorted(set(choices) - set(by_id))
    if unknown:
        raise ValueError(f"record_id(s) {unknown} are not part of the current diff")

    # Checked across the whole diff, not just `choices`: these records can never be resolved by a
    # local/dump pick, so the all-or-nothing requirement below is unsatisfiable while they are in
    # the diff — say why up front instead of reporting them as merely "missing from choices".
    collisions = sorted(rid for rid, rd in by_id.items() if rd.id_collision)
    if collisions:
        raise ValueError(
            f"record_id(s) {collisions}: id collision (the same id was independently assigned to "
            f"two unrelated records) — not resolvable as a local/dump choice, since either pick "
            f"would discard one side's real record; needs a manual, out-of-band fix (see the "
            f"pm-pdata-conflict-resolution skill, point 4)"
        )

    group_mismatches = sorted(rid for rid, rd in by_id.items() if rd.group_mismatch)
    if group_mismatches:
        raise ValueError(
            f"record_id(s) {group_mismatches}: group mismatch (same id and created_at, but the "
            f"two sides disagree on record_group) — not resolvable as a local/dump choice. "
            f"Compare each side's content/file_path before assuming a rename is safe (see the "
            f"pm-pdata-conflict-resolution skill, point 5, for why it may instead be a "
            f"same-second id collision), then retry"
        )

    missing = sorted(set(by_id) - set(choices))
    if missing:
        raise ValueError(
            f"record_id(s) {missing} are in the current diff but have no choice — resolution is "
            f"all-or-nothing: every differing record must get a 'local'/'dump' choice in one "
            f"call, or none are applied. A partial resolve would publish a vector claiming the "
            f"dump machine is fully incorporated while leaving these records unreconciled, and "
            f"the other machine would then overwrite its own unmerged edits without prompting"
        )

    dumpless = sorted(
        rid for rid, choice in choices.items() if choice == "dump" and by_id[rid].dump is None
    )
    if dumpless:
        raise ValueError(
            f"record_id(s) {dumpless}: choice 'dump' is invalid — the dump has no row for these "
            f"records (they exist locally only)"
        )

    # The last thing checked before any write: is someone else writing to this exact .db right
    # now? Non-blocking and point-in-time (sync_lock.is_locked's own docstring), the same probe
    # rehydrate.py runs immediately before its atomic swap. Returning here costs nothing — no
    # transaction has been opened, so there is no partial work to roll back — and LOCKED is not a
    # refusal: it says "retry shortly", exactly like RehydrateOutcome.DEFERRED.
    if sync_lock.is_locked(store.db_path(project)):
        return ApplyOutcome.LOCKED

    with repository._immediate(local_conn):
        for record_id in sorted(choices):
            if choices[record_id] == "dump":
                _apply_dump_choice(local_conn, dump_conn, by_id[record_id])
            # choice == "local" needs no write: local's row is already what we're keeping.

        machine_id = machine_identity.resolve().machine_id
        local_vector = vector_clock_store.read_vector(local_conn)
        # Spec's "Post-resolve vector-clock update" steps 2 then 1, in that order: merge first
        # (adopt the dump machine's revision as fully incorporated, elementwise max for every
        # other machine), then bump local's own counter once for the resolve itself — one bump
        # regardless of how many records this call touched.
        #
        # The spec numbers the bump first, and the two orders give the identical vector whenever
        # dump_vector[machine_id] <= local_vector[machine_id]: writing n for local's own entry and
        # d for the dump's value of it, bump-then-merge is max(n+1, d) and merge-then-bump is
        # max(n, d)+1, and those agree exactly when d <= n. That inequality holds under normal
        # forward-only propagation, where a machine's live revision is never behind what a remote
        # dump believes about it. It is not unconditional, though:
        # `ccst pdata rehydrate --force` replaces the local DB wholesale, pdata_meta included, and
        # can roll local's own counter backward — after which a remote dump can legitimately hold
        # a HIGHER value for local's own machine than local does. With d > n, bumping first yields
        # max(n+1, d) == d whenever d >= n+1 — a vector merely EQUAL to the dump's on this entry,
        # which the other machine's compare() reads as LOCAL_DOMINATES: it never fast-forwards,
        # and this resolve is silently lost. Merging first and bumping the merged result gives
        # max(n, d)+1, strictly above both n and d whatever the rollback history, so the published
        # vector always strictly dominates both forked inputs.
        #
        # diff.dump_vector comes from the same read of the same latest.sql that dump_conn (and so
        # every column type and field description written above) was built from — see
        # apply_resolution's docstring on why that single-snapshot property is the point.
        merged_vector = vector_clock.merge(local_vector, diff.dump_vector)
        vector_clock.bump_own(merged_vector, machine_id)
        vector_clock_store.write_vector(local_conn, merged_vector, updated_at=int(time.time()))

    # Step 3 (spec): re-dump immediately, after commit — a filesystem write. The spec states the
    # reason directly: "publishing right away means the other machine's next check sees a
    # dominating fast-forward, not a repeat of the same fork". The spec's "Triggers" section
    # mandates the same immediate re-dump after a rehydrate; that half belongs to the hook/CLI
    # orchestration and is not built yet, so this is the only caller of dump.write_latest anywhere
    # in src/ today.
    dump.write_latest(
        local_conn, project_root=project_root, machine_id=machine_id, vector=merged_vector,
    )
    return ApplyOutcome.APPLIED


def _open_dump(project_root: Path) -> sqlite3.Connection:
    """In-memory replay of the published dump. rehydrate.py's `_build_replacement()` builds the
    same kind of one-shot SQLite replica on disk, ready for an atomic swap; this module never
    swaps files — it only ever reads the dump side for comparison — so `:memory:` is used instead,
    with no temp file or cleanup needed.

    `row_factory` is set explicitly to `sqlite3.Row` to match `repository.connect()`'s real
    connections (`db.py`'s `connect()` sets this on every store connection unconditionally): the
    diff/snapshot/apply code below reads both sides through the same `row["col"]` access pattern,
    which a bare `sqlite3.connect()`'s default tuple rows would break silently (wrong-looking but
    still-truthy values from positional access, not a clean AttributeError)."""
    latest = project_root / ".pdata-db-dump" / "latest.sql"
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(dump.sql_body(latest.read_text()))
    return conn


def _row_to_base_dict(row: sqlite3.Row) -> BaseFields:
    return {
        "content": row["content"],
        "file_path": row["file_path"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "version": row["version"],
        "deleted_at": row["deleted_at"],
    }


def _all_record_ids(conn: sqlite3.Connection) -> set[int]:
    return {row["id"] for row in conn.execute("SELECT id FROM records")}


def _snapshot(conn: sqlite3.Connection, record_id: int) -> _Snapshot | None:
    row = repository.get_base_record(conn, record_id)
    if row is None:
        return None
    record_group = row["record_group"]
    ext_row = repository.get_extension_row(conn, record_group, record_id)
    extension = (
        {key: ext_row[key] for key in ext_row.keys() if key != "record_id"}
        if ext_row is not None else None
    )
    payload: RecordPayload = {"base": _row_to_base_dict(row), "extension": extension}
    return _Snapshot(record_group=record_group, payload=payload)


class ClassifyResult(NamedTuple):
    """`_classify`'s three mutually-exclusive verdicts, named rather than positional so neither
    the return sites nor the unpack site can transpose them without the name changing too."""

    id_collision: bool
    group_mismatch: bool
    is_delete_vs_update: bool


def _classify(
    local_snap: _Snapshot | None, dump_snap: _Snapshot | None,
) -> ClassifyResult:
    """Classifies one record_id's two snapshots. The three verdicts are mutually exclusive: a
    collision is decided first and reported alone.

    id_collision: true iff both sides have a row for this id but it is not the same logical
    record. `records.id` has no AUTOINCREMENT (repository.py's `_BASE_DDL`) — it is SQLite's bare
    rowid, assigned independently per-database from each database's own `max(rowid)+1`. Two
    machines that fork after a shared rehydrated ancestor and then each insert one or more
    brand-new records can legitimately allocate the SAME id to two entirely unrelated rows — this
    is not a hypothetical edge case, it is the expected outcome whenever both sides add the same
    number of new records to the same record_group after diverging (both start from the same
    max(id), both increment by the same count). `created_at` is set once at insert time and never
    mutated by any write path in the codebase (repository.py's
    `update_base_record`/`soft_delete`/`restore` all leave it alone, and so does
    rename_group.py's `_rename_in_db`) — so for the SAME logical record it must be identical on
    both sides forever, and a difference is conclusive proof the id was independently assigned to
    two different rows rather than proof of a genuine edit conflict on one row. It is the ONLY
    column with that property: `record_group` is deliberately excluded from this test because
    `ccst pdata rename-group` mutates it in place (rename_group._rename_in_db), so treating a
    record_group difference as proof of collision would misdiagnose every record of a group
    renamed on one machine only.

    group_mismatch: true iff created_at matches but the two sides disagree on `record_group` —
    the ordinary case is one record renamed on one side only. NOT airtight proof the two sides
    agree it's the same logical record, unlike id_collision's guarantee above: created_at is
    whole-second precision and is frequently caller-supplied from a file's mtime (see
    importers.py/session_output.py), so two genuinely unrelated records inserted into different
    groups in the same second (or imported from files sharing an mtime) can coincidentally match
    on created_at too, indistinguishable from a rename by id/created_at alone — apply_resolution's
    error text for this category says so and asks the caller to check content/file_path before
    assuming it's safe to rename-group and retry. Regardless of which case it actually is, it
    gets its own category rather than being folded into an ordinary content diff because a
    local/dump pick cannot express it: `RecordDiff.record_group` carries one arbitrary side's
    name, so applying a choice would target that side's `ext_<group>` table for a row whose real
    group may be the other one.

    `apply_resolution` refuses to touch either category — see its own docstring/error text."""
    if local_snap is None or dump_snap is None:
        return ClassifyResult(False, False, False)
    local_base = local_snap.payload["base"]
    dump_base = dump_snap.payload["base"]
    if local_base["created_at"] != dump_base["created_at"]:
        return ClassifyResult(True, False, False)
    if local_snap.record_group != dump_snap.record_group:
        return ClassifyResult(False, True, False)
    is_delete_vs_update = (local_base["deleted_at"] is None) != (dump_base["deleted_at"] is None)
    return ClassifyResult(False, False, is_delete_vs_update)


def _diff_records(
    local_conn: sqlite3.Connection, dump_conn: sqlite3.Connection,
) -> list[RecordDiff]:
    diffs: list[RecordDiff] = []
    for record_id in sorted(_all_record_ids(local_conn) | _all_record_ids(dump_conn)):
        local_snap = _snapshot(local_conn, record_id)
        dump_snap = _snapshot(dump_conn, record_id)
        if local_snap == dump_snap:
            continue  # identical on both sides — nothing to resolve

        if local_snap is not None:
            record_group = local_snap.record_group
        else:
            assert dump_snap is not None  # the union of ids guarantees at least one side has it
            record_group = dump_snap.record_group

        classified = _classify(local_snap, dump_snap)
        diffs.append(RecordDiff(
            record_id=record_id,
            record_group=record_group,
            local=local_snap.payload if local_snap is not None else None,
            dump=dump_snap.payload if dump_snap is not None else None,
            local_record_group=local_snap.record_group if local_snap is not None else None,
            dump_record_group=dump_snap.record_group if dump_snap is not None else None,
            is_delete_vs_update=classified.is_delete_vs_update,
            id_collision=classified.id_collision,
            group_mismatch=classified.group_mismatch,
        ))
    return diffs


def _record_group_fields(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    return {
        (row["record_group"], row["field_name"])
        for row in conn.execute("SELECT record_group, field_name FROM record_group_fields")
    }


def _diff_schema_fields(
    local_conn: sqlite3.Connection, dump_conn: sqlite3.Connection,
) -> list[SchemaFieldDiff]:
    local_fields = _record_group_fields(local_conn)
    dump_fields = _record_group_fields(dump_conn)
    diffs: list[SchemaFieldDiff] = []
    for record_group, field_name in sorted(local_fields ^ dump_fields):
        diffs.append(SchemaFieldDiff(
            record_group=record_group,
            field_name=field_name,
            present_locally=(record_group, field_name) in local_fields,
            present_in_dump=(record_group, field_name) in dump_fields,
        ))
    return diffs


def _apply_dump_choice(
    local_conn: sqlite3.Connection, dump_conn: sqlite3.Connection, record_diff: RecordDiff,
) -> None:
    """Writes the dump's side of one record — base row first, extension row second, matching the
    spec's dependency order ("schema before data... never write a row referencing a column that
    doesn't exist yet") within the extension step itself."""
    dump_payload = record_diff.dump
    assert dump_payload is not None  # validated by apply_resolution's `dumpless` check
    base = dump_payload["base"]

    if record_diff.local is None:
        local_conn.execute(
            "INSERT INTO records "
            "(id, record_group, content, file_path, created_at, updated_at, version, deleted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record_diff.record_id, record_diff.record_group, base["content"],
                base["file_path"], base["created_at"], base["updated_at"], base["version"],
                base["deleted_at"],
            ),
        )
    else:
        local_conn.execute(
            "UPDATE records SET content=?, file_path=?, updated_at=?, version=?, deleted_at=? "
            "WHERE id=?",
            (
                base["content"], base["file_path"], base["updated_at"], base["version"],
                base["deleted_at"], record_diff.record_id,
            ),
        )

    extension = dump_payload["extension"]
    if extension is None:
        return
    _apply_extension_fields(
        local_conn, dump_conn, record_diff.record_group, record_diff.record_id, extension,
    )


def _apply_extension_fields(
    local_conn: sqlite3.Connection,
    dump_conn: sqlite3.Connection,
    record_group: str,
    record_id: int,
    fields: dict[str, object],
) -> None:
    dump_types = _dump_extension_column_types(dump_conn, record_group)
    live_columns = set(repository.list_extension_columns(local_conn, record_group))
    for field_name in fields:
        if field_name not in live_columns:
            added = repository.add_extension_column(
                local_conn, record_group, field_name, dump_types[field_name], default=None,
            )
            if added:
                _copy_field_description(dump_conn, local_conn, record_group, field_name)

    if repository.get_extension_row(local_conn, record_group, record_id) is None:
        repository.insert_extension_row(local_conn, record_group, record_id, fields)
    else:
        repository.update_extension_row(local_conn, record_group, record_id, fields)


def _dump_extension_column_types(
    dump_conn: sqlite3.Connection, record_group: str,
) -> dict[str, str]:
    """The dump's ext_<group> columns and their declared types. Only ever called for a group whose
    extension table the dump definitely has: `_apply_dump_choice` returns early when the dump's
    payload has no `extension` dict, and `_snapshot` builds that dict as `None` precisely when
    `repository.extension_table_exists()` is False on that side. A group that reached here without
    the table would produce an empty PRAGMA result and a `KeyError` from
    `_apply_extension_fields`'s type lookup — a loud failure of a real invariant, which is the
    point; there is deliberately no guard returning `{}` for a state the call graph rules out."""
    table = naming.extension_table_name(record_group)
    return {
        row["name"]: row["type"]
        for row in dump_conn.execute(f'PRAGMA table_info("{table}")')
        if row["name"] != "record_id"
    }


def _copy_field_description(
    dump_conn: sqlite3.Connection,
    local_conn: sqlite3.Connection,
    record_group: str,
    field_name: str,
) -> None:
    """Keeps `record_group_fields` (the schema catalog) reconciled alongside the column itself —
    spec: "a record can only be considered 'resolved' once its record_group's schema is
    reconciled on the side that adopts it". Best-effort: the dump may never have registered a
    description for this field either (`schema add-field --description` is optional), in which
    case there is nothing to copy and the column addition above is already the whole fix."""
    row = dump_conn.execute(
        "SELECT description, added_at FROM record_group_fields "
        "WHERE record_group=? AND field_name=?",
        (record_group, field_name),
    ).fetchone()
    if row is None:
        return
    repository.upsert_field_description(
        local_conn, record_group=record_group, field_name=field_name,
        description=row["description"], added_at=row["added_at"],
    )
