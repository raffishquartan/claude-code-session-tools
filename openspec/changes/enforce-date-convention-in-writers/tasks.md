## 1. Readiness scan: compact timestamps

- [ ] 1.1 Add failing tests in the readiness-scan test module for the scenarios "Compact timestamp column", "Compact timestamps mixed with ISO datetimes" and "A value that is not a real compact timestamp"
- [ ] 1.2 Add `compact-datetime` to `_DATE_FORMATS` in `src/cc_session_tools/lib/pdata/readiness.py` with calendar and clock validation; keep it out of `_ISO_DATE_FORMATS`
- [ ] 1.3 Update the `pm-pdata-do-audit-and-prepare-to-migrate` skill text listing recognised formats (line ~60) to include compact timestamps

## 2. Guard test for date format strings

- [ ] 2.1 Write `tests/test_date_format_conformance.py`: AST-collect format strings under `src/`, compare with the allowlist from design.md's audit table, with a reason per entry; fail on unlisted formats and on stale entries
- [ ] 2.2 Run it; reconcile any difference between the audit table and the real set (fix the table in design.md if the audit missed one, and treat a non-conforming find as a bug to fix in this change)

## 3. Skill text

- [ ] 3.1 Read the external correspondence-archiving skill's current naming rule and compare with the convention's example; stop and ask if they differ
- [ ] 3.2 Change `pm-update-central-files` Step 1b to the `<yyyy.MM.dd>-<HHmm>--...` pattern; confirm `pm-project-layout-reference` section 6 uses the same date part
- [ ] 3.3 Add a test that fails if either skill contains `<yyyy.MM.dd> <HHmm>` (space-separated), pinning the scenario "Correspondence filename pattern"

## 4. Release

- [ ] 4.1 Run the full check suite (`uv run pytest -q`, type check, lint) and confirm exit 0
- [ ] 4.2 Update `CHANGELOG.md` (Keep a Changelog style) and bump `pyproject.toml` to the next patch version; run `uv lock` and commit `uv.lock` in the same commit
- [ ] 4.3 `openspec-sync-specs` and `openspec-archive-change` so `openspec/specs/` reflects the change before shipping
- [ ] 4.4 Push the branch, open the PR with a real summary, run `do-code-review` and post its findings as a PR comment; report the PR URL and stop (do not merge)
