## 1. Scratchpad derivation

- [x] 1.1 Failing tests in `tests/test_bash_hard_deny_scratchpad.py`: scratchpad derived from `session_id` + `transcript_path` (+ `CLAUDE_CODE_TMPDIR`), none without `session_id`/`transcript_path` or with a session id containing `/`
- [x] 1.2 Add the payload context (scratchpad, cwd) to `bash_hard_deny.py`, read it in `main()`, make `check_command` take it as an optional argument; tests from 1.1 pass

## 2. Shell parser and command positions

- [x] 2.1 Failing tests: quoted mentions in `grep`/`echo`/`printf`/`git commit -m`/data heredoc/comment are allowed; `bash -c`, `sh -c`, `eval`, `$(...)`, backticks, subshell, `;`, `&&`, `||`, `|`, newline, env prefix, `env`, `command`, `nohup`, `timeout`, `\rm`, `"rm"`, `$'rm'`, `/bin/rm`, `grm`, `find -delete`, `find -exec rm`, `xargs rm` are blocked; unterminated quote falls back to blocking
- [x] 2.2 Implement the lexer (quotes, escapes, comments, redirections, heredocs, substitutions, separators) and command unwrapping (assignments, keywords, wrappers, shells, `eval`, `find`, `xargs`); tests from 2.1 pass

## 3. Scratchpad delete allowance

- [x] 3.1 Failing tests: scratchpad file, nested dir, root itself, `working/`, `out/`, other session's scratchpad, `..` escape, symlink escape (followed and trailing slash), mixed targets, variable/brace/`~` targets, glob below vs at/above root, relative target with payload cwd vs after `cd`, nonexistent target (rm vs shred vs trailing slash), `ln ... && rm` combo, `find -delete` under the scratchpad vs with `-L`, inline python/node deletes inside/outside and with non-literal arguments, tempfile self-cleanup still allowed
- [x] 3.2 Implement target resolution and the per-verb rules; tests from 3.1 pass

## 4. Delete-by-move and messages

- [x] 4.1 Failing tests: `mv` of an outside file into the scratchpad (`/private/tmp` form) and `mv` nested in `bash -c` are blocked; `mv` within and out of the scratchpad is allowed; block messages contain the deletion-list guidance plus the scratchpad path, or the pattern when unknown
- [x] 4.2 Add `/private/tmp`, `/private/var/tmp` to the tmp-like bases, run the `mv` check on parsed commands, turn the guidance into a function of the scratchpad; tests from 4.1 pass

## 5. Docs and release

- [x] 5.1 Update the module docstring of `bash_hard_deny.py`, the `hook_registry.py` description, `README.md` mention of the hook (`docs/data-store-migration-steps.md` stays accurate: script files are still scanned with no scratchpad exception)
- [x] 5.2 Run `uv run pytest -q`, `uv run mypy src` and `openspec validate --strict bash-hard-deny-scratchpad-deletes`; all exit 0
- [x] 5.3 Add a 4.8.0 entry dated 2026-11-25 to `CHANGELOG.md` (Keep a Changelog), bump `pyproject.toml` to 4.8.0, run `uv lock` and commit `uv.lock` in the same commit
- [x] 5.4 Sync the delta specs into `openspec/specs/` and archive the change
