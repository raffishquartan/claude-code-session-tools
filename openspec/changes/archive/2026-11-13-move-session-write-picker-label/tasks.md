## 1. Picker label and liveness (tests first, red then green)

- [x] 1.1 Rewrite and add tests in `skills/move-session/tests/` that fail against current code: replace `TestRenameOnlyDoesNotCorruptJsonl` (`test_integration.py:220-252`) so RENAME-only asserts original bytes are a prefix, exactly two pinned-shape lines follow and no tombstone records appear; delete the marker assertion at `test_integration.py:72-75` and assert no `.pending-rename` is written; MOVE+RENAME appends to the destination only; MOVE-only appends none; no-trailing-newline case; dry run and `--verify-only` leave the transcript untouched; refusal when a live `~/.claude/sessions/<pid>.json` names the uuid (plain-shell run), no refusal for a dead pid; append re-parse failure exits 1; run and confirm each new/rewritten test fails for the right reason
- [x] 1.2 Implement in `scripts/move_session.py`: the liveness check (process records plus the 30-second mtime fallback, applied to tag-changing operations whether or not `in_cc`) and `append_picker_label` (single write, newline guard, re-parse), called directly after destination verification and before the tombstone step; remove the marker write; keep the `session_tags` upsert; update the end-of-run output and the line-930 tombstone message; verify `uv run pytest -q` passes
- [x] 1.3 Update the module docstring (the "rename never touches the jsonl" statements), `discover_session_jsonl`'s docstring and the `write_tag` comment (new rationale: keeps `session_tags` coherent for strategy 1 and step 4); verify by re-reading that no comment still says the jsonl is untouched or that `/rename` is pending

## 2. Retire the hook

- [x] 2.1 Delete `hooks/sessionstart-pending-rename.sh`, `tests/test_hook.bats` and `src/hooks/pending_rename.py`; remove the `pending-rename` entries from `hook_registry.py` and `hooks-bundle.json`; update `tests/test_ccst_hooks_install_zero_arg.py`, `tests/test_ccst_hook_dispatcher.py` and the hook-check count at `tests/test_ccst_doctor.py:790` (14 to 13); add a test that `prune_stale_hooks` removes a `ccst hooks run pending-rename` entry and that the verb is rejected; verify `uv run pytest -q` passes
- [x] 2.2 Update `README.md` (line ~366), `docs/design.md` (hook description, the bats test dependency, the move-session test count) and the `pdata/sync_notify.py` comment; verify with `grep -rn "pending-rename\|pending_rename" . --exclude-dir=.git --exclude-dir=.venv --exclude-dir=cc-sessions --exclude-dir=archive --exclude=CHANGELOG.md --exclude-dir=superpowers` that nothing remains outside historical documents

## 3. Invocation

- [x] 3.1 Write red tests: `ccst move-session --src-session <synthetic> --rename-tag <tag>` (leading option) prints `PLAN (RENAME)` and exits 0; `ccst move-session --help` output contains `--src-session`; a missing `--src-session` returns the script's exit code 2; all run as subprocesses with `CCST_NO_AUTO_SYNC=1`
- [x] 3.2 Implement the pre-`parse_args` dispatch in `cli/ccst.py` (subprocess with `sys.executable`), add the verb to the help listing, verify 3.1 passes
- [x] 3.3 Write a red test for the refusal recipe (`CLAUDECODE=1`, cwd = source cwd, fresh transcript): exit 2, transcript unchanged, output contains `ccst move-session` and neither `python3 ` nor `/rename`; then change `print_in_session_recipe`, delete the `SCRIPT_PATH` constant, verify green

## 4. Documentation and release hand-off

- [x] 4.1 Update `SKILL.md`: quick reference and the "Why the SKILL.md uses ~/.claude/skills paths" section use `ccst move-session`; remove marker, `/rename` and `.tag`-file text (lines ~18, 33, 43-44, 77-86, 102, 137, 169, 188-193, 198, 224); narrow the "editing the source jsonl" mistake to tombstone-and-title appends only; verify by re-reading for leftover references
- [x] 4.2 Run `ccst move-session` by hand (via `uv run`, `CCST_NO_AUTO_SYNC=1`) in a scratch HOME against a synthetic session and check the transcript tail
- [x] 4.3 Record in the PR description the pre-merge manual check from design.md (real `claude --resume` after a rename shows the new tag); CHANGELOG entries for this change (Changed, Added `ccst move-session`, Removed `pending-rename` with the upgrade note and the interim "unknown hook" warning) and the version bump are done once for the combined release after both changes are archived
