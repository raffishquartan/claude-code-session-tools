## 1. Recovery library (tests first, red then green)

- [x] 1.1 Write tests for `lib/command_recovery.py` over synthetic projects dirs (`projects_dir` injected, never the real home): exact match, near-miss rejected, full/prefix/upper-case/`sha256:` forms, ambiguous prefix (telemetry and `--scan-all` cases), multi-line and tabbed command escaping, escape-then-truncate never splitting an escape, truncation marker with elided count, `--max-chars 0`, subagent-only command found with the subagent path, `session` fallback with transcripts missing, most-recent-session choice for a multi-session hash, `unknown`, `--scan-all` opening each file once, one open per file across two hashes in one session; run and confirm they fail because the module does not exist
- [x] 1.2 Hoist the projects-directory constant into `lib/paths` and reuse it from `lib/session_gc.py` and `lib/sessions.py`; implement `lib/command_recovery.py`; verify the 1.1 tests and the existing suite pass

## 2. CLI verb

- [x] 2.1 Write tests for `ccst telemetry recover`: gate refusal (exit 2, nothing opened), malformed/short/non-hex argument exits 2 naming it with no result lines, duplicate collapse, column order including the full hash for `command`, exit 0 for non-`command` statuses, `--max-chars`, `--scan-all`, `--projects-dir`/`--hooks-dir`, telemetry.db row count and max id unchanged afterwards, and that `bash_hard_deny` allows `ccst telemetry recover ...`; confirm red
- [x] 2.2 Add the `recover` verb to `cli/ccst.py`, its usage-block entry, and the README telemetry rows (`README.md` ~504 and ~550); verify the 2.1 tests pass and `grep -n "recover" README.md` shows the verb

## 3. update-command-cache

- [x] 3.1 Write tests: `list` prints a truncated preview per candidate with one recovery pass and shows the elided count, `--no-preview` opens no transcript, unrecoverable candidates show their status and the stale "cannot be resolved" note is gone, no subcommand behaves as `list` with preview (subprocess, like the existing real-subprocess test), the refusal text names a working command, and a docstring-vs-argparse parity check; confirm red
- [x] 3.2 Update `scripts/update_command_cache.py` (preview column, `--no-preview` defaulted on the root parser, refusal text, docstring Usage) to call the library; verify 3.1 passes
- [x] 3.3 Update `SKILL.md`: the end-to-end flow `list` -> `recover --max-chars 0 <sha>` -> `promote <full sha> --preview "<recovered>"`, the full-command-before-promote rule, the preview text, the gate-is-a-convention note; verify by re-reading that no sentence still calls recovery manual

## 4. Verification

- [x] 4.1 Run `ccst telemetry recover` on real hashes from the live store with `CCST_FIRES_ACCESS=1` (via `uv run`, `CCST_NO_AUTO_SYNC=1`), covering at least one hash whose command exists only in a subagent transcript; confirm `command` lines whose full hash re-hashes correctly, and that output is only the compact lines
- [x] 4.2 Run `uv run pytest -q`, `mypy src` and lint as configured and verify all exit 0

## 5. Release (this change lands last)

- [x] 5.1 Add CHANGELOG entries for both changes (Added: `ccst telemetry recover`, `update-command-cache list` previews, `ccst move-session`; Changed: rename writes the picker label and refuses while a live process holds the session; Removed: `pending-rename` hook and marker, with the upgrade note and interim "unknown hook" warning), bump `pyproject.toml` to 4.1.0 (minor: new CLI verbs; the maintainer first said 4.0.3, then agreed it is a minor bump), run `uv lock` and commit `uv.lock` with it; verify `uv run pytest -q` exits 0
