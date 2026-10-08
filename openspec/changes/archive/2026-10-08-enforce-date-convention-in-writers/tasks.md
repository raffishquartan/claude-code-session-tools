## 1. Readiness scan: compact timestamps

- [x] 1.1 Add failing tests in the readiness-scan test module for the scenarios "Compact timestamp column", "Compact timestamps mixed with ISO datetimes" and "A value that is not a real compact timestamp"
- [x] 1.2 Add `compact-datetime` to `_DATE_FORMATS` in `src/cc_session_tools/lib/pdata/readiness.py` with calendar and clock validation; keep it out of `_ISO_DATE_FORMATS`
- [x] 1.3 Update the `pm-pdata-do-audit-and-prepare-to-migrate` skill text listing recognised formats (line ~60) to include compact timestamps

## 2. Guard test for date format strings

- [x] 2.1 Write `tests/test_date_format_conformance.py`: AST-collect format strings under `src/`, compare with the allowlist from design.md's audit table, with a reason per entry; fail on unlisted formats and on stale entries
- [x] 2.2 Run it; reconcile any difference between the audit table and the real set (fix the table in design.md if the audit missed one, and treat a non-conforming find as a bug to fix in this change)

## 3. Release

- [x] 3.1 Run the full check suite (`uv run pytest -q`, type check, lint) and confirm exit 0
- [x] 3.2 Update `CHANGELOG.md` (Keep a Changelog style) and bump `pyproject.toml` to the next patch version; run `uv lock` and commit `uv.lock` in the same commit
- [x] 3.3 `openspec-sync-specs` and `openspec-archive-change` so `openspec/specs/` reflects the change before shipping
- [ ] 3.4 Push the branch, open the PR with a real summary, run `do-code-review` and post its findings as a PR comment; report the PR URL and stop (do not merge)
