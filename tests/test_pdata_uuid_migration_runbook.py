"""The committed pdata UUID migration runbook (task 6.3): it exists at the path the doctor check,
the install banner and the migration messages name, and it gives every command the migration
needs. The commands themselves are exercised in tests/pdata/test_migrate_uuid_rehearsal.py."""
from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib.doctor import pdata_uuid_migration_remedy
from cc_session_tools.lib.pdata import formats, migrate_uuid

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RUNBOOK = _REPO_ROOT / formats.RUNBOOK_PATH


@pytest.fixture(scope="module")
def runbook() -> str:
    return _RUNBOOK.read_text()


def test_runbook_exists_at_the_published_path() -> None:
    assert formats.RUNBOOK_PATH == "docs/pdata-uuid-migration-runbook.md"
    assert _RUNBOOK.is_file()
    assert formats.RUNBOOK_URL.endswith("/" + formats.RUNBOOK_PATH)


@pytest.mark.parametrize("command", [
    "ccst --version",
    "ccst doctor",
    "ccst doctor --all",
    "ccsched list",
    "ccsched disable pdata-sync-hourly",
    "ccsched disable pm-session-output-reconcile",
    "ccsched disable pdata-verify-all",
    "ccsched enable pdata-sync-hourly",
    "ccsched enable pm-session-output-reconcile",
    "ccsched enable pdata-verify-all",
    "ccst pdata sync-check --all-projects",
    "ccst pdata resolve --project <project>",
    "ccst pdata migrate-uuid --all-projects",
    "ccst pdata migrate-uuid --all-projects --write",
    "ccst pdata verify --all-projects",
    "ccst pdata rehydrate --project <project>",
    f"uv tool install cc-session-tools=={formats.LAST_FORMAT3_RELEASE}",
    "uv tool install cc-session-tools --upgrade",
    "migration-backups/pdata-<project>-pre-4.0.0-<UTC stamp>.db",
])
def test_runbook_names_each_command(runbook: str, command: str) -> None:
    assert command in runbook


@pytest.mark.parametrize("section", [
    "Step 0 - on every machine, while still on 3.x, BEFORE upgrading anything",
    "## 2. Machine order",
    "## 4. First machine",
    "## 5. Each later machine",
    "## 6. Troubleshooting",
    "## 7. Rollback",
    "## 8. Final checklist",
])
def test_runbook_has_each_section(runbook: str, section: str) -> None:
    assert section in runbook


@pytest.mark.parametrize("message", [
    "unpublished changes",
    "(fork)",
    "[NO] no `claude` session is running",
    "[NO] the database is not locked",
    "[NO] this database is the format 3 state the format 4 dump came from",
])
def test_runbook_troubleshooting_is_keyed_by_message(runbook: str, message: str) -> None:
    troubleshooting = runbook[runbook.index("## 6. Troubleshooting"):runbook.index("## 7.")]
    assert message in troubleshooting


def test_runbook_uses_hyphens_not_em_dashes(runbook: str) -> None:
    assert "\u2014" not in runbook


def test_install_banner_names_the_runbook() -> None:
    banner = "\n".join(migrate_uuid.install_banner(["notes"]))
    assert formats.RUNBOOK_PATH in banner


def test_doctor_remedy_names_the_runbook() -> None:
    assert formats.RUNBOOK_PATH in pdata_uuid_migration_remedy("notes")


def test_install_banner_is_empty_with_nothing_to_migrate() -> None:
    assert migrate_uuid.install_banner([]) == []
