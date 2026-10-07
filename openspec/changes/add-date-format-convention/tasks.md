## 1. Branch and baseline

- [x] 1.1 Create a worktree and branch `f/20261007-date-format-convention` from `main`, move this
      change directory into it, `uv sync --extra dev`, and confirm the full check suite exits 0
      before any edit (use `CCST_NO_AUTO_SYNC=1` for every `uv run` command)

## 2. `date-format` managed section (commit 1)

- [x] 2.1 Add the `date-format` body to `_SECTION_BODIES` and the id to `SECTION_IDS` (appended
      last) in `lib/claude_md_install.py`, with no personal identifiers
- [x] 2.2 Extend `tests/test_claude_md_install.py`: section appears in an all-sections install,
      installs and uninstalls alone, is idempotent, leaves other sections byte-identical, and the
      body contains the three clauses, the time-of-day rule, the no-mixing rule and the exemptions
      and none of a fixed list of personal-identifier patterns
- [x] 2.3 Update docs that enumerate the managed sections (README, `docs/`)

## 3. Human-facing date output (commit 2)

- [x] 3.1 Re-check each row of the earlier writer sweep and the second sweep against the final
      rule; record any additional hit found in the PR description
- [x] 3.2 `lib/doctor.py`: render the pdata-verify run time and the failed-sync time as
      `yyyy-MM-dd HH:mm UTC`; `lib/install_sync.py`: same for the auto-sync failure message
- [x] 3.3 `lib/pdata/cutover.py`: log line time as `yyyy-MM-dd HH:mm UTC`
- [x] 3.4 `skills/clean-hook-sessions/scripts/clean-hook-sessions.py`: `human_time` returns
      `yyyy-MM-dd HH:mm`
- [x] 3.5 `cli/ccmsg.py` `read`: render `sent_at` as `yyyy-MM-dd HH:mm UTC` (stored value unchanged)
- [x] 3.6 Update the existing tests that assert the old text and add one test per changed output;
      add a test that stored machine timestamps (message, scheduler, telemetry) are unchanged

## 4. Readiness scan (commit 3)

- [x] 4.1 Add the dotted `yyyy.MM.dd` pattern to `_DATE_FORMATS` in `lib/pdata/readiness.py`
- [x] 4.2 Tests: ISO plus dotted, ISO plus compact, ISO plus `D Month YYYY`, ISO plus
      `Month D, YYYY` each raise `mixed-date-formats` with the right per-format counts; a column
      that is uniformly dotted does not
- [x] 4.3 Add `non-iso-dates` to `FINDING_KINDS` and emit it per the spec; tests: uniformly
      dotted, compact, slash and long-form columns, an ISO-only column (none), and a mixed
      column (both kinds, `non-iso-dates` counts non-ISO formats only)
- [x] 4.4 Update the `pm-pdata-do-audit-and-prepare-to-migrate` skill text and any docs listing the
      finding kinds or recognised formats; describe `non-iso-dates` as advisory

## 4b. pdata date-field warning (commit 4)

- [x] 4b.1 Add the date-field check to `lib/pdata/service.py` (pure, never raises, reads field
      descriptions) and call it from the `add` and `update` handlers in `cli/ccst.py`, printing
      warnings to stderr after a successful write
- [x] 4b.2 Tests for every scenario in the spec: dotted into `_date`/`_at`, description-based match,
      each ISO form silent, `_text` silent, non-date field silent, null/empty silent, update path,
      write still succeeds with exit 0
- [x] 4b.3 Document the warning and its heuristic in the pdata docs and the
      `pm-pdata-design-schema` skill

## 5. Release prep (commit 5)

- [ ] 5.1 CHANGELOG entry under a new `3.12.0` heading (Keep a Changelog headings stay ISO)
- [ ] 5.2 Bump `pyproject.toml` to 3.12.0, run `uv lock`, commit `uv.lock` in the same commit
- [ ] 5.3 Run the full check suite (build, mypy, lint, format, tests) and confirm every check exits 0
- [ ] 5.4 `openspec-sync-specs` and archive the change before shipping

## 6. Ship and install

- [ ] 6.1 Push the branch, open a PR with a summary, run `do-code-review` and post its findings as
      a PR comment; stop at the PR URL
- [ ] 6.2 After merge (done by the user): `uv tool install --reinstall ~/repos/claude-code-session-tools`,
      run `ccst doctor`, confirm `install:synced` passes, and show the installed `date-format` block
- [ ] 6.3 Only after 6.2 is verified, `ccmsg send --to-project claude-code-config-sync` asking it to
      run the config-sync conversion prompt, attaching that prompt and the convention spec and
      stating that the `date-format` block is installed
