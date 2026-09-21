"""Behavioural guards on the bundled pm-pdata-do-audit-and-prepare-to-migrate skill text.

Asserts what the skill must keep saying (scan first, kinds named, thresholds, warnings, manifest
layout) rather than how long it is - the size is measured once per release, not pinned here.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from cc_session_tools.lib.pdata import readiness

SKILL = (
    Path(__file__).parent.parent
    / "src" / "cc_session_tools" / "skills" / "pm-pdata-do-audit-and-prepare-to-migrate" / "SKILL.md"
)


@pytest.fixture(scope="module")
def text() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_frontmatter_name_is_stable(text: str) -> None:
    assert text.startswith("---\nname: pm-pdata-do-audit-and-prepare-to-migrate\n")


def test_scan_is_run_before_any_agent_dispatch(text: str) -> None:
    lowered = text.lower()
    assert "ccst pdata readiness-scan" in text
    assert lowered.index("ccst pdata readiness-scan") < lowered.index("dispatch")


def test_agents_get_only_their_domain_slice(text: str) -> None:
    assert "--path" in text and "--findings-only" in text


@pytest.mark.parametrize("kind", readiness.FINDING_KINDS)
def test_every_finding_kind_is_named(text: str, kind: str) -> None:
    assert kind in text


def test_parallel_agents_are_conditional_on_domain_count(text: str) -> None:
    assert re.search(r"three or fewer|more than three", " ".join(text.split()))


def test_row_id_instability_warning_survives(text: str) -> None:
    assert "row_id" in text
    assert re.search(r"not stable keys", " ".join(text.split()), re.IGNORECASE)
    assert "input to that judgement" in " ".join(text.split())


def test_blind_spots_are_agent_bullets(text: str) -> None:
    assert "multi-schema" in text.lower()
    assert re.search(r"comma.{0,40}semicolon", " ".join(text.split()), re.IGNORECASE)


def test_deletions_stay_a_reviewable_script(text: str) -> None:
    assert "reviewable" in text and "`rm`" in text and "never run" in text.lower()


@pytest.mark.parametrize(
    "heading",
    ["Unambiguous fixes", "Genuine judgement calls", "Real gaps"],
)
def test_checkpoint_table_split_survives(text: str, heading: str) -> None:
    assert heading in text


@pytest.mark.parametrize(
    "section",
    [
        "Header", "File inventory", "Per-file migration blockers",
        "Recommended natural key per file", "Known-acceptable duplication", "Open decisions",
    ],
)
def test_phase_5_manifest_sections_survive(text: str, section: str) -> None:
    assert section in text


def test_phase_numbering_referenced_by_dependents_is_kept(text: str) -> None:
    for phase in ("Phase 1", "Phase 2", "Phase 3", "Phase 4", "Phase 5"):
        assert f"## {phase}:" in text


def test_names_the_four_readiness_artefacts(text: str) -> None:
    assert ".pdata-migration-manifest.json" in text and "pdata-readiness-notes.md" in text
