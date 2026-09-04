"""Health-check logic for ``ccst doctor``.

Runs a suite of checks and returns a list of :class:`CheckResult` objects.
Each check has a status (OK / WARN / FAIL), a name, and a reason string.

The module is intentionally pure: no I/O side effects, all filesystem paths
are passed in (makes unit testing straightforward).
"""
from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cc_session_tools.lib.db import connect as _db_connect
from cc_session_tools.lib.db import migration_applied as _migration_applied
from cc_session_tools.lib.hook_registry import HOOK_VERBS, hook_name_from_command
from cc_session_tools.lib.pdata.init_paths import (
    MIGRATED_ARCHIVE_DIRNAME,
    MIGRATED_MANIFEST_FILENAME,
)

if TYPE_CHECKING:
    from cc_session_tools.lib.install_sync import FailedAttempt
    from cc_session_tools.lib.scheduler.bundled_jobs import BundledJob


class Status(str, Enum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    reason: str

    def __str__(self) -> str:
        return f"[{self.status.value:<4}] {self.name}: {self.reason}"


# The two session-root env vars, in the order they're checked, mapped to an
# example value for the doctor WARN/FAIL hint. See README.md's "Configuration:
# where do your sessions live?" for what each root means.
_ROOT_ENV_EXAMPLES = {
    "CLAUDE_SESSION_TOOLS_REPO_ROOT": "$HOME/repos",
    "CLAUDE_SESSION_TOOLS_PROJ_ROOT": "$HOME/cc-claude-code",
}


# ---------- individual checks ----------


def check_cli_on_path(cli_name: str) -> CheckResult:
    """Verify the named CLI is on PATH and reports a version."""
    if shutil.which(cli_name) is None:
        return CheckResult(
            name=f"PATH:{cli_name}",
            status=Status.FAIL,
            reason=f"{cli_name!r} not found on PATH",
        )
    try:
        result = subprocess.run(
            [cli_name, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return CheckResult(
                name=f"PATH:{cli_name}",
                status=Status.WARN,
                reason=f"{cli_name} --version exited {result.returncode}",
            )
        version_out = (result.stdout + result.stderr).strip()
        return CheckResult(
            name=f"PATH:{cli_name}",
            status=Status.OK,
            reason=version_out.split("\n")[0],
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired) as e:
        return CheckResult(
            name=f"PATH:{cli_name}",
            status=Status.WARN,
            reason=f"could not run {cli_name} --version: {e}",
        )


def check_env_dir(
    var_name: str, env_value: str | None, *, hint: str | None = None
) -> CheckResult:
    """Check that an env var is set and points to an existing directory.

    ``hint``, when given, is appended to the WARN/FAIL reason to tell the
    user where and how to set the variable. It is omitted from the OK reason
    — once the value is valid, there's nothing left to point the user at.
    """
    name = f"ENV:{var_name}"
    if env_value is None:
        reason = "not set"
        if hint:
            reason = f"{reason} — {hint}"
        return CheckResult(name=name, status=Status.WARN, reason=reason)
    p = Path(env_value)
    if not p.is_dir():
        reason = f"set to {env_value!r} but directory does not exist"
        if hint:
            reason = f"{reason} — {hint}"
        return CheckResult(name=name, status=Status.FAIL, reason=reason)
    return CheckResult(name=name, status=Status.OK, reason=str(p))


def check_settings_json(settings_path: Path) -> CheckResult:
    """Verify settings.json exists and is valid JSON."""
    name = "settings.json"
    if not settings_path.exists():
        return CheckResult(name=name, status=Status.FAIL, reason=f"not found: {settings_path}")
    try:
        with settings_path.open() as f:
            json.load(f)
        return CheckResult(name=name, status=Status.OK, reason=str(settings_path))
    except json.JSONDecodeError as e:
        return CheckResult(name=name, status=Status.FAIL, reason=f"invalid JSON: {e}")
    except OSError as e:
        return CheckResult(name=name, status=Status.FAIL, reason=f"cannot read: {e}")


def _hook_command_present(settings: dict[str, Any], command: str) -> bool:
    """Return True if ``command`` appears in any hooks block in settings."""
    hooks_section = settings.get("hooks", {})
    for _event, blocks in hooks_section.items():
        for block in blocks:
            for hook_entry in block.get("hooks", []):
                if hook_entry.get("command") == command:
                    return True
    return False


def check_hook_registered(
    hook_name: str,
    settings: dict[str, Any],
) -> CheckResult:
    """Check that ccst hooks run <hook_name> is present in settings."""
    command = f"ccst hooks run {hook_name}"
    name = f"hook:{hook_name}"
    if _hook_command_present(settings, command):
        return CheckResult(name=name, status=Status.OK, reason="registered")
    return CheckResult(
        name=name,
        status=Status.WARN,
        reason=f"{command!r} not found in settings.json",
    )


def check_no_stale_hooks(settings: dict[str, Any]) -> list[CheckResult]:
    """FAIL for every settings.json entry naming a hook CCST no longer has.

    :func:`check_hook_registered` only looks bundle -> settings, so it cannot
    see an entry for a hook that was removed or renamed: nothing else reads
    settings.json looking for names CCST does not recognise, and nothing
    rewrites the file unless ``ccst hooks install`` runs. That blind spot let
    three hooks dropped in 0.17.0 sit in a settings.json across two upgrades.

    FAIL rather than WARN because the consequence is a broken session, not a
    missing feature: Claude Code runs the entry on every event it is bound to
    and surfaces the failure each time.
    """
    stale: list[tuple[str, str]] = []  # (hook_name, event)
    for event, blocks in settings.get("hooks", {}).items():
        for block in blocks:
            for entry in block.get("hooks", []):
                name = hook_name_from_command(entry.get("command", ""))
                if name is not None and name not in HOOK_VERBS:
                    stale.append((name, event))

    if not stale:
        return [CheckResult(
            name="hooks:no-stale",
            status=Status.OK,
            reason="no settings.json entries for removed hooks",
        )]

    return [
        CheckResult(
            name=f"hooks:stale:{name}",
            status=Status.FAIL,
            reason=(
                f"settings.json registers 'ccst hooks run {name}' on {event}, but this "
                f"CCST has no such hook — it fails on every {event} event. Remove it with "
                f"`ccst hooks uninstall --hook {name} --apply`, or run "
                "`ccst hooks install --apply` to prune every stale entry at once"
            ),
        )
        for name, event in sorted(set(stale))
    ]


def check_skill_symlink(skill_name: str, skill_src: Path, skills_dir: Path) -> CheckResult:
    """Check that skills_dir/<skill_name> is a valid CCST skill symlink.

    A symlink is OK if:
      - it resolves to ``skill_src`` exactly (the currently invoked ccst's
        bundled source), OR
      - it resolves to *any* directory named ``<skill_name>`` that contains a
        SKILL.md file — i.e. a different but otherwise valid CCST install
        (canonical clone vs worktree, multiple clones, pipx vs uv tool dir).

    The second case is reported as OK with a parenthetical NOTE so the user
    can spot the divergence but doctor does not FAIL on what is a legitimate
    multi-install setup.
    """
    name = f"skill:{skill_name}"
    dest = skills_dir / skill_name
    if not dest.exists() and not dest.is_symlink():
        return CheckResult(
            name=name,
            status=Status.WARN,
            reason=f"no symlink at {dest}",
        )
    if not dest.is_symlink():
        return CheckResult(
            name=name,
            status=Status.FAIL,
            reason=f"{dest} exists but is not a symlink",
        )
    actual = dest.resolve()
    expected = skill_src.resolve()
    if actual == expected:
        return CheckResult(name=name, status=Status.OK, reason=f"-> {actual}")
    if actual.is_dir() and actual.name == skill_name and (actual / "SKILL.md").is_file():
        return CheckResult(
            name=name,
            status=Status.OK,
            reason=f"-> {actual} (NOTE: different CCST install than this one at {expected})",
        )
    return CheckResult(
        name=name,
        status=Status.FAIL,
        reason=(
            f"symlink points to {actual}, which is not a valid {skill_name!r} "
            f"skill directory (expected {expected} or another CCST install)"
        ),
    )


def check_ccsched_job_registered(
    job_id: str, expected: "BundledJob | None" = None
) -> CheckResult:
    """WARN (not FAIL) if a CCST-bundled ccsched job (lib/scheduler/bundled_jobs.py) is missing,
    disabled, or — when `expected` is given — no longer matches its bundled definition.
    Recoverable by re-running `ccst ccsched-jobs install --apply` / `ccsched enable <id>` /
    `ccsched edit`, not a silent-data-loss risk (this repo's version policy reserves FAIL for
    breaking on-disk migrations, which this is not).

    `expected` is optional (default None, meaning "skip the drift check") so a caller checking a
    job id with no corresponding BundledJob at hand — or an older call site — keeps its existing
    missing/disabled-only behaviour unchanged."""
    from cc_session_tools.lib.scheduler import registry

    name = f"ccsched-job:{job_id}"
    try:
        specs = registry.load_registry()
    except registry.RegistryError as exc:
        return CheckResult(name=name, status=Status.WARN, reason=f"ccsched.db unreadable: {exc}")
    for spec in specs:
        if spec.job_id == job_id:
            if expected is not None:
                from cc_session_tools.lib.scheduler.bundled_jobs import (
                    diff_from_bundled_detail, render_field_diffs,
                )

                changed_detail = diff_from_bundled_detail(spec, expected)
                if changed_detail:
                    field_word = "field differs" if len(changed_detail) == 1 else "fields differ"
                    return CheckResult(
                        name=name, status=Status.WARN,
                        reason=(
                            f"registered but {len(changed_detail)} {field_word} from "
                            f"the bundled definition:\n{render_field_diffs(changed_detail)}\n"
                            f"run 'ccsched edit {job_id}' to realign, or leave as your "
                            f"intentional customization"
                        ),
                    )
            if spec.enabled:
                return CheckResult(name=name, status=Status.OK, reason="registered and enabled")
            return CheckResult(
                name=name, status=Status.WARN,
                reason=f"registered but disabled — run 'ccsched enable {job_id}'",
            )
    return CheckResult(
        name=name, status=Status.WARN,
        reason="not registered — run 'ccst ccsched-jobs install --apply'",
    )


def check_pypi_version(installed_version: str, timeout: float = 3.0) -> CheckResult:
    """Compare installed version against the latest on PyPI.

    Skips silently (OK, no network) if the request fails.
    """
    name = "version:pypi"
    try:
        import httpx  # optional dep — present in the installed wheel

        r = httpx.get(
            "https://pypi.org/pypi/cc-session-tools/json",
            timeout=timeout,
            follow_redirects=True,
        )
        if r.status_code != 200:
            return CheckResult(name=name, status=Status.OK, reason="PyPI query failed (skipped)")
        latest = r.json()["info"]["version"]
        if _version_tuple(latest) > _version_tuple(installed_version):
            return CheckResult(
                name=name,
                status=Status.WARN,
                reason=f"installed {installed_version}, latest {latest} available on PyPI",
            )
        return CheckResult(
            name=name,
            status=Status.OK,
            reason=f"installed {installed_version} is up to date",
        )
    except Exception:
        # Network failure, import error, etc. — don't fail doctor for this
        return CheckResult(
            name=name,
            status=Status.OK,
            reason="PyPI check skipped (network unavailable or httpx not installed)",
        )


def check_install_everything_synced(
    installed_version: str,
    synced_version: str | None,
    failed_attempt: FailedAttempt | None = None,
) -> CheckResult:
    """OK when the recorded sync marker matches the running ccst version.

    WARN when it doesn't: the next non-exempt `ccst` command auto-applies it,
    so this is self-recoverable with no user action at all - same severity
    rationale as check_ccsched_job_registered.

    FAIL when an auto-apply has already been attempted and failed for this
    exact version. That state is not self-recoverable: it will keep failing
    identically on every retry (a real ~/.claude/skills/<name> directory
    blocking a symlink, unbalanced CLAUDE.md sentinels) until a human looks at
    it. doctor is exempt from auto-applying, so it can always report this
    rather than silently erasing it.
    """
    name = "install:synced"
    if synced_version == installed_version:
        return CheckResult(
            name=name, status=Status.OK,
            reason=f"install-everything last synced at {installed_version}",
        )
    if failed_attempt is not None and failed_attempt.version == installed_version:
        return CheckResult(
            name=name, status=Status.FAIL,
            reason=(
                f"installed {installed_version}, and the automatic install sync already "
                f"failed for this version (rc {failed_attempt.rc} at "
                f"{failed_attempt.at.strftime('%Y-%m-%dT%H:%M:%SZ')}) — "
                "run `ccst install-everything --apply` to see why"
            ),
        )
    if synced_version is None:
        return CheckResult(
            name=name, status=Status.WARN,
            reason=(
                f"installed {installed_version}, install-everything has never been run — "
                "it will be applied automatically on the next `ccst` command; run "
                "`ccst install-everything --apply` to do it now"
            ),
        )
    return CheckResult(
        name=name, status=Status.WARN,
        reason=(
            f"installed {installed_version}, install-everything last synced at "
            f"{synced_version} — it will be applied automatically on the next `ccst` "
            "command; run `ccst install-everything --apply` to do it now"
        ),
    )


def _version_tuple(v: str) -> tuple[int, ...]:
    """Parse a simple a.b.c version string into a comparable tuple."""
    parts: list[int] = []
    for segment in v.split("."):
        try:
            parts.append(int(segment))
        except ValueError:
            parts.append(0)
    return tuple(parts)


# ---------- data-store health check ----------


def _nearest_existing_ancestor(path: Path) -> Path:
    """Walk up from ``path`` to the first directory that actually exists."""
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:  # reached filesystem root without finding one
            return current
        current = parent
    return current


def check_data_stores(store_paths: dict[str, Path]) -> list[CheckResult]:
    """Attempt to open each per-subsystem data store under ``data_home()``.

    ``store_paths`` maps a short store name (e.g. ``"ccmsg"``) to its resolved
    file path (e.g. ``data_home() / "ccmsg.db"``, already accounting for that
    subsystem's own env-var override) — this function never resolves paths
    itself, matching every other check in this module ("all filesystem paths
    are passed in").

    For a path ending in ``.db``: if it exists, opens it read-only via
    :func:`cc_session_tools.lib.db.connect` — a FAIL means the file is
    present but not a valid/openable SQLite database (corruption,
    permissions, wrong schema version). If it does not exist yet, this is
    expected before first use — every store creates its own schema on first
    real ``connect(path, ddl=...)`` call (data-store-uplift overview, binding
    decision 8) — so this WARNs rather than FAILs, unless the nearest
    existing ancestor directory is not writable, in which case first use
    would also fail, so this FAILs instead.

    For a path not ending in ``.db`` (``claude-flags.json`` is a flat JSON
    file, not a SQLite store — see Phase 6): the same
    exists/invalid-is-FAIL, missing/WARN-unless-unwritable-ancestor
    treatment applies, but "invalid" is checked via ``json.load`` instead of
    ``db.connect``.
    """
    results: list[CheckResult] = []
    for store_name, path in store_paths.items():
        name = f"data-store:{store_name}"
        if path.exists():
            try:
                if path.suffix == ".db":
                    conn = _db_connect(path, readonly=True)
                    conn.execute("PRAGMA schema_version").fetchone()
                    conn.close()
                else:
                    with path.open() as f:
                        json.load(f)
                results.append(CheckResult(name=name, status=Status.OK, reason=str(path)))
            except (sqlite3.DatabaseError, sqlite3.OperationalError, json.JSONDecodeError, OSError) as e:
                results.append(
                    CheckResult(
                        name=name,
                        status=Status.FAIL,
                        reason=f"{path} exists but failed to open: {e}",
                    )
                )
            continue

        ancestor = _nearest_existing_ancestor(path)
        if os.access(ancestor, os.W_OK):
            results.append(
                CheckResult(
                    name=name,
                    status=Status.WARN,
                    reason=f"not yet created; will be created at {path} on first use",
                )
            )
        else:
            results.append(
                CheckResult(
                    name=name,
                    status=Status.FAIL,
                    reason=f"not yet created and {ancestor} is not writable",
                )
            )
    return results


# ---------- pending data-store migration check ----------


@dataclass(frozen=True)
class LegacyMigrationPaths:
    """Old on-disk locations for the four stores that gained a one-shot
    migration script in the 1.0.0 data-store restructure, plus the new
    ``data_home`` root their SQLite replacements live under."""

    ccmsg_old_root: Path
    ccsched_old_dir: Path
    tags_dir: Path
    mutes_file: Path
    telemetry_old_dir: Path
    data_home: Path


def _count_legacy_ccmsg(old_root: Path) -> int:
    if not old_root.is_dir():
        return 0
    return sum(1 for p in old_root.rglob("*.md") if p.is_file() and ".locks" not in p.parts)


def _count_legacy_ccsched(old_dir: Path) -> int:
    if not old_dir.is_dir():
        return 0
    return sum(1 for p in old_dir.rglob("*") if p.is_file())


def _count_legacy_sessions(tags_dir: Path, mutes_file: Path) -> int:
    count = len(list(tags_dir.glob("*.tag"))) if tags_dir.is_dir() else 0
    if mutes_file.is_file():
        try:
            data = json.loads(mutes_file.read_text())
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            count += len(data)
    return count


def _count_legacy_telemetry(log_dir: Path) -> int:
    if not log_dir.is_dir():
        return 0
    return sum(
        1 for n in ("fires.jsonl", "fires.jsonl.1", "fires.jsonl.2", "fires.jsonl.3")
        if (log_dir / n).is_file()
    )


def _migration_recorded(db_path: Path, marker_name: str) -> bool:
    """True if ``db_path`` records ``marker_name`` as an applied migration.

    Single implementation shared by all four common stores. Row counts are not a usable
    signal here: each store's writer starts inserting the moment CCST is installed, well
    before any migration runs, so a non-empty table means "somebody wrote something", not
    "the legacy data was imported" - only an explicit marker can tell the two apart.

    Never creates the store: a fresh install (nothing to check) must leave the filesystem
    exactly as it found it.
    """
    if not db_path.exists():
        return False
    try:
        conn = _db_connect(db_path, readonly=True)
    except (sqlite3.DatabaseError, sqlite3.OperationalError, OSError):
        return False
    try:
        return _migration_applied(conn, marker_name)
    except sqlite3.DatabaseError:
        # sqlite3.OperationalError ("no such table: migrations") is the common case for a
        # pre-marker schema; a corrupt file also reaches here, not the connect() guard
        # above, because sqlite3.connect() opens lazily and only fails once a statement
        # actually touches the file - both count as "can't tell, so not recorded".
        return False
    finally:
        conn.close()


def check_pending_data_store_migration(paths: LegacyMigrationPaths) -> list[CheckResult]:
    """Detect legacy flat-file data left over from a pre-1.0.0 install.

    :func:`check_data_stores` can't distinguish a fresh install (nothing to
    migrate) from an upgrade that hasn't run the one-shot migration yet
    (legacy data present, new store empty) — both read as "not yet created,
    expected before first use". This check makes that distinction explicit:

    - No legacy data found -> OK (fresh install or already cleaned up).
    - Legacy data found, migration not yet run -> FAIL: the upgrade is
      silently sitting on unmigrated data until ``ccst migrate all`` is run.
    - Legacy data found, migration already run -> WARN: the old files weren't
      deleted (or deletion failed partway); no data is at risk, just
      uncommitted cleanup.

    "Has the migration run?" is answered by an explicit marker for all four stores
    (`_migration_recorded`), never inferred from row counts - a heuristic that was wrong
    wherever the new code writes to the store before any migration happens: each store
    fills from the first hook fire after install, so counting rows reported "already
    migrated" for anyone who opened a session before migrating, and the FAIL that should
    have prompted them never fired.
    """
    from cc_session_tools.lib.messaging.repository import (
        LEGACY_FLAT_FILE_MIGRATION as _CCMSG_MIGRATION,
    )
    from cc_session_tools.lib.scheduler.store import (
        LEGACY_FLAT_FILE_MIGRATION as _CCSCHED_MIGRATION,
    )
    from cc_session_tools.lib.sessions_db import (
        LEGACY_FLAT_FILE_MIGRATION as _SESSIONS_MIGRATION,
    )
    from cc_session_tools.lib.telemetry_store import LEGACY_JSONL_MIGRATION as _TELEMETRY_MIGRATION

    sources: dict[str, tuple[tuple[Path, ...], int, Path, str]] = {
        "ccmsg": (
            (paths.ccmsg_old_root,),
            _count_legacy_ccmsg(paths.ccmsg_old_root),
            paths.data_home / "ccmsg.db",
            _CCMSG_MIGRATION,
        ),
        "ccsched": (
            (paths.ccsched_old_dir,),
            _count_legacy_ccsched(paths.ccsched_old_dir),
            paths.data_home / "ccsched.db",
            _CCSCHED_MIGRATION,
        ),
        "sessions": (
            (paths.tags_dir, paths.mutes_file),
            _count_legacy_sessions(paths.tags_dir, paths.mutes_file),
            paths.data_home / "sessions.db",
            _SESSIONS_MIGRATION,
        ),
        "telemetry": (
            (paths.telemetry_old_dir,),
            _count_legacy_telemetry(paths.telemetry_old_dir),
            paths.data_home / "telemetry.db",
            _TELEMETRY_MIGRATION,
        ),
    }

    results: list[CheckResult] = []
    for store_name, (old_paths, legacy_count, new_db_path, marker_name) in sources.items():
        name = f"migration-to-1.0.0:{store_name}"
        if legacy_count == 0:
            results.append(
                CheckResult(name=name, status=Status.OK, reason="no legacy data found — nothing to migrate")
            )
            continue

        existing = ", ".join(str(p) for p in old_paths if p.exists())
        already_migrated = _migration_recorded(new_db_path, marker_name)
        evidence = f"the import is recorded in {new_db_path.name}"
        if not already_migrated:
            results.append(
                CheckResult(
                    name=name,
                    status=Status.FAIL,
                    reason=(
                        f"{legacy_count} unmigrated item(s) at {existing}; run "
                        "`ccst migrate all` from a plain terminal (NOT inside a Claude Code "
                        "session — the delete step is blocked by bash-hard-deny) to migrate "
                        f"into {new_db_path}"
                    ),
                )
            )
        else:
            results.append(
                CheckResult(
                    name=name,
                    status=Status.WARN,
                    reason=(
                        f"migration already ran ({evidence}) but old files remain at "
                        f"{existing} — safe to remove once verified"
                    ),
                )
            )
    return results


def check_pending_pdata_migration(projects_root: Path) -> list[CheckResult]:
    """Warn about ccst pdata init runs whose archived-but-undeleted migrated-source
    originals (spec §7.1 step 7) are still sitting under a project's
    .pdata-migrated/ directory. Mirrors check_pending_data_store_migration()'s
    pattern but is WARN-only: unlike the CCST-infra migration that check covers,
    there is no version upgrade forcing every project through ccst pdata init, so
    a project with no .pdata-migrated/ directory at all is a completely normal,
    unremarkable state — nothing to FAIL on. Reuses init_paths.py's own
    MIGRATED_ARCHIVE_DIRNAME/MIGRATED_MANIFEST_FILENAME constants (the same ones
    cutover.py writes to) rather than re-typing the literal strings, so the two
    can never drift apart."""
    if not projects_root.is_dir():
        return [CheckResult(
            name="pdata-init:pending", status=Status.OK,
            reason=f"{projects_root} does not exist — nothing to check",
        )]

    pending: list[tuple[str, Path, int]] = []
    for project_dir in sorted(p for p in projects_root.iterdir() if p.is_dir()):
        archive_dir = project_dir / MIGRATED_ARCHIVE_DIRNAME
        if not archive_dir.is_dir():
            continue
        remaining = [
            p for p in archive_dir.rglob("*")
            if p.is_file() and p.name != MIGRATED_MANIFEST_FILENAME
        ]
        if remaining:
            pending.append((project_dir.name, archive_dir, len(remaining)))

    if not pending:
        return [CheckResult(
            name="pdata-init:pending", status=Status.OK,
            reason="no archived-but-undeleted migration sources found",
        )]

    return [
        CheckResult(
            name=f"pdata-init:pending:{project_name}",
            status=Status.WARN,
            reason=(
                f"{count} archived migrated-source file(s) remain at {archive_dir} — "
                "safe to remove once verified (ccst pdata init never deletes "
                "automatically, spec §7.1 step 7; manual delete only)"
            ),
        )
        for project_name, archive_dir, count in pending
    ]


def _fmt_epoch(epoch: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def check_pdata_verify(projects: list[str]) -> list[CheckResult]:
    """Read (never run) each project's most recently persisted `ccst pdata verify` result — a
    background ccsched job produces it, this only reports it (spec §8.2's own "feeding failures
    into ccst doctor... rather than only running silently in the background"). A project with no
    verify db yet, or that has never had a verify run, WARNs rather than FAILing — matching
    check_pending_data_store_migration's own precedent that an unremarkable not-yet-run state
    should never read as broken.

    Deliberately takes validated project *names*, not pre-resolved Path objects like
    check_data_stores()'s `store_paths` — unlike a generic file-backed store, a project's pdata
    store is never addressed by raw path anywhere in this codebase (service.py, repository.py,
    verify.py itself are all keyed by project name; repository.connect(project) does the one
    env-var-aware path lookup, in one place). Accepting a Path here would mean either duplicating
    that lookup a second time in ccst.py just to hand doctor.py something it then discards in
    favour of calling verify.last_run(project) anyway, or threading a parallel path-based entry
    point through verify.py that nothing else needs. The project name *is* this check's resolved
    handle, in the same sense that store_paths' Path values are check_data_stores()'s."""
    from cc_session_tools.lib.pdata import verify

    results: list[CheckResult] = []
    for project in projects:
        name = f"pdata-verify:{project}"
        summary = verify.last_run(project)
        if summary is None:
            results.append(CheckResult(
                name=name, status=Status.WARN,
                reason="ccst pdata verify has not run yet for this project",
            ))
            continue
        if summary.status == "OK":
            results.append(CheckResult(
                name=name, status=Status.OK,
                reason=f"last run {_fmt_epoch(summary.run_at)}, no issues",
            ))
        else:
            status = Status.FAIL if summary.status == "FAIL" else Status.WARN
            results.append(CheckResult(
                name=name, status=status,
                reason=(
                    f"last run {_fmt_epoch(summary.run_at)}: {len(summary.issues)} "
                    f"issue(s), worst={summary.status} — run 'ccst pdata verify "
                    f"--project {project}' for details"
                ),
            ))
    return results


def check_sessions_project_dir_absolute(sessions_db_path: Path) -> list[CheckResult]:
    """WARN if any sessions.db row has a non-absolute project_dir. Every reader that
    scopes --global by root (ccs.py, sessions.find_matching_sessions) compares
    project_dir.parent against a resolved, absolute root — a relative project_dir can
    never match, so the row becomes permanently invisible to --global listings without
    ever raising an error. Not FAIL: it degrades --global listings but does not block
    core functionality, and `ccst repair sessions` fixes it non-destructively.

    FAILs instead if sessions.db exists but isn't a valid SQLite file — same
    "exists but failed to open" treatment check_data_stores already gives
    this exact condition for other stores; sqlite3.connect() opens lazily
    and only fails once find_non_absolute_rows actually queries the file, so
    this must be caught here rather than assumed impossible."""
    import sqlite3

    from cc_session_tools.lib import sessions_repair

    try:
        bad = sessions_repair.find_non_absolute_rows(path=sessions_db_path)
    except sqlite3.DatabaseError as exc:
        return [CheckResult(
            name="sessions:project-dir-absolute", status=Status.FAIL,
            reason=f"{sessions_db_path} exists but failed to open: {exc}",
        )]
    if not bad:
        return [CheckResult(
            name="sessions:project-dir-absolute", status=Status.OK,
            reason="all sessions.db rows have an absolute project_dir",
        )]
    return [CheckResult(
        name="sessions:project-dir-absolute", status=Status.WARN,
        reason=(
            f"{len(bad)} sessions.db row(s) have a non-absolute project_dir and are "
            "invisible to `ccl`/`ccs --global` — run 'ccst repair sessions --dry-run' "
            "to see them, then --execute to fix"
        ),
    )]


# ---------- high-level runner ----------


def run_all_checks(
    *,
    installed_version: str,
    settings_path: Path,
    bundle_path: Path,
    skills_source_dir: Path | None,
    skills_target_dir: Path,
    env: dict[str, str | None],
    skip_pypi: bool = False,
    store_paths: dict[str, Path] | None = None,
    legacy_migration_paths: LegacyMigrationPaths | None = None,
    projects_root: Path | None = None,
    pdata_verify_projects: list[str] | None = None,
    sessions_db_path: Path | None = None,
    synced_version: str | None = None,
    failed_attempt: FailedAttempt | None = None,
) -> list[CheckResult]:
    """Run the full doctor suite and return results.

    Parameters
    ----------
    installed_version:
        The ``__version__`` string of the installed package.
    settings_path:
        Path to ``~/.claude/settings.json``.
    bundle_path:
        Path to the bundled ``config/hooks-bundle.json``.
    skills_source_dir:
        Bundled skills directory (may be None if discovery fails).
    skills_target_dir:
        Target skills directory (usually ``~/.claude/skills/``).
    env:
        Dict with relevant env var names → values (or None).
    skip_pypi:
        If True, skip the PyPI version check.
    store_paths:
        Dict mapping short store name -> resolved file path; when None,
        data-store checks are skipped (used by callers/tests that don't care
        about them).
    legacy_migration_paths:
        Old on-disk locations for the pre-1.0.0 flat-file stores; when None,
        the pending-migration check is skipped.
    projects_root:
        Root holding every ``~/cc/<project>`` directory; when None, the
        pending ``ccst pdata init`` cutover check is skipped.
    pdata_verify_projects:
        Project names whose last persisted ``ccst pdata verify`` result should
        be reported; when None, the pdata-verify checks are skipped. Never
        triggers a verify run — only reads what the recurring job left behind.
    sessions_db_path:
        Path to sessions.db; when None, the project_dir-absolute check is
        skipped.
    synced_version:
        The version ``install-everything --apply`` last succeeded for, or
        None if it has never been recorded.
    failed_attempt:
        The most recently recorded auto-apply failure, or None if the last
        attempt succeeded (or none has been recorded).
    """
    results: list[CheckResult] = []

    # CLI presence
    for cli in ("ccd", "ccr", "ccs", "claude-code-usage", "ccst", "ccmsg"):
        results.append(check_cli_on_path(cli))

    # Environment variables
    for var, example in _ROOT_ENV_EXAMPLES.items():
        hint = (
            f'set via `export {var}="{example}"` in ~/.shellrc.d/env.sh '
            "(a plain file you create yourself — not ccl.sh, which "
            "`ccst shell install --apply` overwrites)"
        )
        results.append(check_env_dir(var, env.get(var), hint=hint))

    # settings.json validity
    results.append(check_settings_json(settings_path))

    # Hook registrations (from bundle)
    settings_data: dict[str, Any] = {}
    if settings_path.exists():
        try:
            with settings_path.open() as f:
                settings_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    bundle_hooks = _extract_bundle_hook_names(bundle_path)
    for hook_name in bundle_hooks:
        results.append(check_hook_registered(hook_name, settings_data))

    # ...and the reverse direction: entries for hooks that no longer exist.
    results.extend(check_no_stale_hooks(settings_data))

    # Skill symlinks
    if skills_source_dir is not None and skills_source_dir.is_dir():
        for skill_dir in sorted(skills_source_dir.iterdir()):
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").is_file():
                results.append(
                    check_skill_symlink(skill_dir.name, skill_dir, skills_target_dir)
                )
    else:
        results.append(
            CheckResult(
                name="skills:source-dir",
                status=Status.WARN,
                reason="bundled skills/ directory not found; skill checks skipped",
            )
        )

    # Bundled ccsched jobs
    from cc_session_tools.lib.scheduler import bundled_jobs

    for job in bundled_jobs.BUNDLED_CCSCHED_JOBS:
        results.append(check_ccsched_job_registered(job.job_id, expected=job))

    # Data stores
    if store_paths is not None:
        results.extend(check_data_stores(store_paths))

    # Pending legacy-data migration
    if legacy_migration_paths is not None:
        results.extend(check_pending_data_store_migration(legacy_migration_paths))

    # Pending ccst pdata init cutover (spec §7.1 step 7)
    if projects_root is not None:
        results.extend(check_pending_pdata_migration(projects_root))

    # Last persisted ccst pdata verify result per project (spec §8.2)
    if pdata_verify_projects is not None:
        results.extend(check_pdata_verify(pdata_verify_projects))

    # Non-absolute project_dir rows in sessions.db
    if sessions_db_path is not None:
        results.extend(check_sessions_project_dir_absolute(sessions_db_path))

    # PyPI version check
    if not skip_pypi:
        results.append(check_pypi_version(installed_version))

    # Install-everything sync state
    results.append(
        check_install_everything_synced(installed_version, synced_version, failed_attempt)
    )

    return results


def _extract_bundle_hook_names(bundle_path: Path) -> list[str]:
    """Return hook names derived from the bundle file.

    Each hook entry has a command like ``ccst hooks run <name>``; we extract
    ``<name>``.
    """
    names: list[str] = []
    if not bundle_path.exists():
        return names
    try:
        with bundle_path.open() as f:
            bundle = json.load(f)
        for _event, blocks in bundle.get("hooks", {}).items():
            for block in blocks:
                for hook_entry in block.get("hooks", []):
                    cmd = hook_entry.get("command", "")
                    prefix = "ccst hooks run "
                    if cmd.startswith(prefix):
                        name = cmd[len(prefix):].strip()
                        if name and name not in names:
                            names.append(name)
    except (json.JSONDecodeError, OSError):
        pass
    return names


def format_results(results: list[CheckResult], *, show_all: bool = False) -> str:
    """Return a human-readable table of check results.

    By default (show_all=False) only WARN/FAIL results are printed, with a
    hint on how to see the rest — a clean machine's `ccst doctor` output is
    otherwise dozens of [OK] lines a user has to scroll past to find the one
    thing that needs attention. show_all=True reproduces the full table this
    function always printed before this parameter existed.
    """
    if not results:
        return "(no checks ran)"
    shown = results if show_all else [r for r in results if r.status is not Status.OK]
    lines = []
    if shown:
        name_w = max(len(r.name) for r in shown)
        for r in shown:
            lines.append(f"[{r.status.value:<4}] {r.name:<{name_w}}  {r.reason}")
    elif not show_all:
        lines.append(f"All {len(results)} checks OK.")
    has_issues = any(r.status in (Status.WARN, Status.FAIL) for r in results)
    if has_issues:
        lines.append("\nTip: run `ccst install-everything --apply` to sync skills, hooks, shell, and CLAUDE.md")
    if not show_all:
        lines.append("\nTo see full doctor output use --all argument")
    return "\n".join(lines)


# ---------- drift monitor (ccst doctor --drift) ----------


def filter_unmuted_issues(
    results: list[CheckResult], muted: set[str]
) -> list[CheckResult]:
    """Return the WARN/FAIL results whose ``name`` is not muted.

    OK results never appear; muted names are dropped. Order is preserved.
    """
    return [
        r
        for r in results
        if r.status in (Status.WARN, Status.FAIL) and r.name not in muted
    ]


def format_drift_report(unmuted: list[CheckResult], *, muted_count: int) -> str:
    """Format the drift report for the monitor job.

    Returns the empty string when there is nothing un-muted to report — the
    caller prints nothing and exits 0 in that case, so a clean run produces no
    surfaced output.
    """
    if not unmuted:
        return ""
    name_w = max(len(r.name) for r in unmuted)
    lines = ["ccst doctor: un-muted drift detected —"]
    for r in unmuted:
        lines.append(f"  [{r.status.value:<4}] {r.name:<{name_w}}  {r.reason}")
    lines.append("")
    lines.append(
        "Acknowledge an item with:  ccst doctor --mute <name>"
        f"   ({muted_count} already muted)"
    )
    return "\n".join(lines)
