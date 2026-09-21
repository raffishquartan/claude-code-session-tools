## 1. Scanner (red first)

- [x] 1.1 Write failing unit tests in `tests/pdata/test_readiness.py` with fictional tmp-path fixtures covering: inventory (rows, columns, header excluded), each kind in `FINDING_KINDS` with a positive and (where meaningful) a negative case, no cell text in any finding, BOM/CRLF with BOM-free header names, header-only and empty files, unterminated-quote file reported `unreadable`/`csv-parse-error` and not-UTF-8 `not-utf8` without aborting other files, unique columns with `integer-like`/`free-text` labels and the two-row minimum, `--path` prefix, and that the scanned CSV set equals the CSVs from `classify.walk_and_classify` on a tree with a dot-directory, `.git/` and `cc-sessions/`; verify they fail because the module does not exist
- [x] 1.2 Implement `lib/pdata/readiness.py` (bytes read once, strict csv, `os.walk(followlinks=False)`, frozen dataclasses, `FINDING_KINDS`, Markdown and JSON formatters); verify 1.1 passes and `mypy` is clean on it

## 2. CLI verb (red first)

- [x] 2.1 Write failing CLI tests in `tests/test_ccst_pdata_readiness_cli.py` (subprocess with `CCST_PROJECTS_ROOT`): exit 0 with findings, `--format json` parses with `files` and `findings`, `--findings-only` and `--path`, zero-CSV project says so and exits 0, exit 2 with stderr for a missing project directory and an invalid name, and that nothing under the project (nor the project directory) is created or changed; verify they fail
- [x] 2.2 Wire `readiness-scan` into `cli/ccst.py` (parser with `--format`, `--path`, `--findings-only`; dispatch; `store.project_root`); verify 2.1 passes

## 3. Skill rewrite

- [x] 3.1 Record the baseline (`wc -c` and `wc -l` of `SKILL.md`: 14442 bytes, 199 lines) and write a failing test in `tests/test_pdata_audit_skill.py` asserting: the scan is mentioned before any agent-dispatch instruction; every `FINDING_KINDS` entry is named; the three-domain threshold; the row_id-instability warning; the reviewable-delete-script rule; the six Phase 5 manifest section names and the Phase 3 checkpoint-table split; multi-schema and comma/semicolon are listed as agent bullets; verify it fails
- [x] 3.2 Rewrite `pm-pdata-do-audit-and-prepare-to-migrate/SKILL.md` per the design; verify 3.1 passes and the existing skills-discovery tests still pass
- [x] 3.3 Re-read `pm-pdata-do-manual-migration/SKILL.md` and `pm-pdata-do-init/SKILL.md` and confirm every reference they make into the audit skill still resolves to surviving text; record the new byte and line counts

## 4. Docs and release

- [x] 4.1 Add a short "pdata readiness scan" subsection to `README.md` and verify it with `grep -n readiness-scan README.md`
- [x] 4.2 Add `### Added` and `### Changed` entries under a dated `## [3.8.0]` section in `CHANGELOG.md` (stating the measured before/after skill size and that live-audit token savings are unmeasured), bump `pyproject.toml` to 3.8.0, run `uv lock`, and verify `uv.lock` shows 3.8.0
- [x] 4.3 Run the full suite and mypy on touched files and verify every check exits 0; run `CCST_NO_AUTO_SYNC=1 ccst pdata readiness-scan --project <a real project>` (the env var stops the install-sync check rewriting the live `~/.claude` from this newer checkout) and confirm sensible output and that no file under that project changed
- [x] 4.4 Sync delta specs and archive the change; verify `openspec/specs/pdata/readiness-scan/spec.md` exists and `openspec/specs/pdata/migration-guidance/spec.md` contains the new requirement
