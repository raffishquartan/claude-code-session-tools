## 1. Measure Change A first (gate for everything in group 2)

- [x] 1.1 Write `scripts/measure_review_call.py` and a ~20-command reference corpus (safe, suspicious, dangerous, one-liner); it reports per-run `usage` from `--output-format json`.
- [x] 1.2 Measure baseline, then each candidate mechanism alone and combined; specifically confirm whether `CLAUDE_CONFIG_DIR` breaks OAuth on macOS.
- [x] 1.3 Record the results table in the change's design notes and pick the adopted flag set against the acceptance bar; report to the user if the bar cannot be met.

## 2. Change A implementation

- [x] 2.1 Apply the adopted flags / empty cwd / config-dir to `call_claude`; keep `CLD_SESSION_*` env handling.
- [x] 2.2 Tests: argument list construction; failure path still yields "unavailable" (mock non-zero exit).

## 3. Allowlist store and CLI

- [x] 3.1 Add the `script_allowlist` table (additive DDL) and access functions beside the existing cache functions.
- [x] 3.2 Add `ccst hooks allowlist list|add|remove|verify`; `add` computes the hash itself.
- [x] 3.3 Tests for store functions and each subcommand, including empty-input validation.

## 4. Matcher

- [x] 4.1 Quote-aware shell-structure scanner (rejects unquoted `| ; & < >`, substitutions, backticks, unquoted `$`); table-driven tests for accept and reject cases.
- [x] 4.2 Interpreter-form parser (`python*`, `bash|sh|zsh`, `node`, `uv run [python*]`, direct path), rejecting interpreter options and `VAR=` / `env` prefixes.
- [x] 4.3 Path resolution: cwd-relative realpath, git-root containment, `cc-sessions` and symlink-escape exclusions.

## 5. Hook integration

- [x] 5.1 Insert the allowlist tier between heuristics and cache lookup; heuristic hits skip it.
- [x] 5.2 Hash-mismatch path: stderr message, telemetry marker, entry update or removal after re-review.
- [x] 5.3 `build_prompt`: include script content (<= 64 KiB, read once) and the arbitrary-arguments question; carry the hashed bytes through to auto-add.
- [x] 5.4 Auto-add after Tier 3 `safe` under all conditions; tests for each exclusion (scratch script, outside root, heuristic flag, too large, unrecognised form).
- [x] 5.5 Telemetry: `exit_tier=2`, `cache_source` values `script-allowlist` / `script-allowlist-mismatch`; test for each.

## 6. Docs and verification

- [x] 6.1 Update hook module docstring tier list and README hook description.
- [x] 6.2 `uv run pytest -q` and all other configured checks exit 0 (run ad hoc `ccst` commands with `CCST_NO_AUTO_SYNC=1`).
- [x] 6.3 Manual smoke: a live session-free run of the hook over the corpus with a temp `CCST_CACHE_DB`, confirming second invocation with different args skips `claude`.
- [x] 6.4 Update `CHANGELOG.md` (`### Added` / `### Changed`, including the Change A token numbers) and bump `pyproject.toml` minor; run `uv lock` and commit `uv.lock` in the same commit.
