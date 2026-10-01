## 1. Tests (red first)

- [ ] 1.1 In `tests/pdata/test_cutover.py`: real `.md` beside the CSV survives byte-for-byte and the pointer lands at `<stem>.pdata-pointer.md` with schema table and example query; no-collision pointer unchanged; substitute also occupied skips with an outcome and no overwrite; two entries sharing a stem keep both pointers; own earlier pointer is rewritten without a duplicate; `--leave-no-pointer-files`-equivalent (`write_pointer_files=False`) leaves the real `.md` untouched; MANIFEST.md line records substitution; verify they fail
- [ ] 1.2 In `tests/pdata/test_init_service.py`: dry-run report lists the collision and modifies nothing; no-collision dry-run report unchanged; end-to-end `write` keeps `REAL NARRATIVE - MUST SURVIVE` and reports the substitution; re-run `write` is idempotent; `leave_no_pointer_files=True` leaves it untouched; verify they fail

## 2. Implementation

- [ ] 2.1 Add `_pointer_heading`, `plan_pointer`/`PointerPlan`/`PointerOutcome` and exclusive-create writing to `src/cc_session_tools/lib/pdata/cutover.py`; make `archive_entries` return outcomes and log substitutions; verify 1.1 passes
- [ ] 2.2 Pass the project root into `_render_report`, add collision lines, and append the "Pointer files" section to the write report in `src/cc_session_tools/lib/pdata/init_service.py`; verify 1.2 passes
- [ ] 2.3 Document the collision behaviour in `src/cc_session_tools/skills/pm-pdata-do-init/SKILL.md` Step 7

## 3. Release

- [ ] 3.1 Add the CHANGELOG entry and bump to 3.10.5 in `pyproject.toml`, run `uv lock`; verify `uv run pytest -q`, mypy on the changed modules, and `openspec validate fix-pdata-pointer-file-overwrite --strict` all exit 0; manual temp-project repro confirms the narrative text survives
