## Why

`bash-hard-deny` blocks every `rm`/`rmdir`/`unlink`/`shred`, so a session cannot clean up even the throwaway files in its own Claude Code scratchpad, and scratch and build-artifact cleanup gets blocked across many sessions. It also matches the delete verbs as plain text after any `;`/`&`/`|`, so a `grep` pattern or an `echo` body that merely contains the words can be blocked, while some real delete paths (`bash -c "rm x"`, `xargs rm`, `find -delete`) are not recognised at all.

## What Changes

- Deletes (`rm`, `rmdir`, `unlink`, `shred`, their GNU `g`-prefixed names, `find -delete`/`-exec rm`, and inline `python`/`node` deletes) are allowed when every target resolves strictly inside this session's own scratchpad directory. Deletes anywhere else - including a session's `working/` and `out/` folders, another session's scratchpad, and the scratchpad directory itself - stay blocked. Targets that cannot be resolved statically (variables, command substitution, brace expansion, `~`, globs at or above the scratchpad, `..` or symlinks escaping it) are blocked.
- The scratchpad is pinned to the calling session using the hook payload's `session_id` and `transcript_path`; when they are missing, no delete is allowed.
- Delete verbs are matched only in real command positions, found by a shell-aware parser: quoted arguments, `echo`/`printf` bodies, commit messages, comments and heredoc bodies used as data no longer trigger a block. Command positions inside `bash -c`/`sh -c`, `eval`, `$(...)`, backticks, subshells, pipelines, `env`/`command`/`nohup`/`timeout`-style wrappers, `\rm` and `/bin/rm` are still recognised.
- **Tightening**: `xargs rm`, `find ... -delete`, `find ... -exec rm` and the `g`-prefixed coreutils names (`grm` etc.) were previously not blocked; they now follow the same rule (outside the scratchpad they block; `xargs` deletes always block because their targets are unknown).
- **Tightening**: `/private/tmp` and `/private/var/tmp` count as tmp-like destinations for the delete-by-move check, and a direct `mv` nested inside `bash -c` or similar is recognised, so a file cannot be moved into the scratchpad and then deleted there.
- Every deletion block message still points at the deletion-list workflow and now also names this session's scratchpad path (or the path pattern when it cannot be derived) as the place where deletes are allowed.
- Script files and heredoc bodies fed to an interpreter keep today's behaviour (any delete is blocked), and the `tempfile` self-cleanup exemption is unchanged.

## Capabilities

### New Capabilities
- `hooks/bash-hard-deny-scratchpad-deletes`: which delete commands the hard-deny hook allows (only targets inside the calling session's scratchpad), how the scratchpad is derived, and what the block message says.
- `hooks/bash-hard-deny-command-positions`: the hook matches delete verbs only where the shell would execute them, including nested and wrapped command positions, and not in data.

### Modified Capabilities

None.

## Impact

- `src/hooks/bash_hard_deny.py` (new shell parser and scratchpad resolution, `check_command` gains an optional context, `main` reads `session_id`/`transcript_path`/`cwd`), `src/cc_session_tools/lib/hook_registry.py` (description).
- `tests/test_bash_hard_deny.py`. `README.md`, `docs/data-store-migration-steps.md` (hook description).
- `CHANGELOG.md`, `pyproject.toml`, `uv.lock` (minor bump to 4.8.0). No on-disk change.
