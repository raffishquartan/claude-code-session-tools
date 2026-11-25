## 1. cwd verification

- [x] 1.1 Failing tests in `tests/test_jsonl.py`: strict and non-strict verification pass when source cwds include `<projects>/<src-key>` and the destination holds the rewritten `<projects>/<dst-key>`; fail and name the value when a destination cwd is not a rewritten source cwd
- [x] 1.2 Add `apply_replacements()`, use it in `rewrite_jsonl_paths()`, pass the replacement list to `verify_dst_jsonl()` and compute the expected cwd set; print unexpected cwds; verify the 1.1 tests pass
- [x] 1.3 Integration regression test in `tests/test_integration.py`: MOVE of a transcript with records whose cwd is under `~/.claude/projects/<src-key>` exits 0 with `RESULT : PASS`

## 2. Rollback on failed verification

- [x] 2.1 Failing integration test: force a strict verification failure by appending a line that is not valid JSON to the source transcript (copied verbatim, so `json_invalid_lines` is non-zero) and assert exit 1, removed paths printed, destination session dir and transcript gone, source unchanged, pre-existing destination key dir kept, and a re-run is not rejected by the clobber check
- [x] 2.2 Record created paths in `main()`, move verification ahead of the memory copy, add `rollback_created()`; verify 2.1 passes

## 3. Strict-root tag derivation

- [x] 3.1 Failing unit tests for `derive_strict_tag()` covering: src-project prefix stripped, no src prefix, `make-` vs `making-` collision kept, remainder already carrying the destination prefix, suffix equal to the source project
- [x] 3.2 Failing integration tests with a strict root: dry-run MOVE prints the NOTE and derived tag; `--execute` creates `cc-sessions/<derived>/`, writes the picker label and repeats the NOTE in DONE; `--no-auto-rename` fails with the tag-prefix error; an explicit failing `--rename-tag` is not overridden; a non-tag failure (invalid strict project name) is not masked
- [x] 3.3 Implement `derive_strict_tag()`, the `--no-auto-rename` flag and the retry in `main()`, plus the NOTE output; verify 3.1 and 3.2 pass

## 4. Docs and checks

- [x] 4.1 Update `SKILL.md`: quick-reference flag table, "Destination cwd validation" / "Strict-root rules" (auto-rename, derivation rule, collision case, `--no-auto-rename`), "Verification" (cwd rule, rollback), happy-path step order
- [x] 4.2 Run `uv run pytest -q`, `uv run mypy src` and `openspec validate --strict move-session-verify-and-auto-tag`; all exit 0

## 5. Release

- [x] 5.1 Update `CHANGELOG.md` (Keep a Changelog) with a 4.6.0 entry dated 2026-11-25, bump `pyproject.toml` to 4.6.0 (minor: new flag and behaviour, per the repo version policy), run `uv lock` and commit `uv.lock` in the same commit
