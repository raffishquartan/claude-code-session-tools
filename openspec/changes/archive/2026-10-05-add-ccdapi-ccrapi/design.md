## Context

`ccd` and `ccr` are Python console scripts that build a `claude` command plus an env dict and call
a monkeypatchable `launch_claude*` function that `execvpe`s. `ccr` already uses `pick_from_list`
(max 10 items) for ambiguous matches. Neither uses a `-k` flag. See proposal.md.

## Goals / Non-Goals

**Goals:**
- Zero duplication of `ccd`/`ccr` logic: the API variants are thin wrappers.
- The key never reaches argv, logs or dry-run output.

**Non-Goals:**
- Managing the real file's contents or syncing it (a `claude-code-config-sync` change).
- Switching an already-running session between subscription and API key.
- Validating a key against the API.

## Decisions

1. **Wrappers, not forks.** `ccd.main` and `ccr.main` gain an optional keyword `api_key: bool = False`.
   When set they parse `-k`, resolve the key via `lib/api_keys.py`, and set `ANTHROPIC_API_KEY` on the
   env they already build (and drop `CLAUDE_CODE_OAUTH_TOKEN` so it cannot win). `ccdapi`/`ccrapi`
   entry points are `main(api_key=True)` shims. Alternative: separate copies of each CLI - rejected,
   drift.
2. **`-k` parsed only in API mode.** `ccd`/`ccr` themselves are untouched, so a stray `-k` there still
   passes through to `claude` as before. In `ccr`, `-k` is consumed by its argparse parser before
   `parse_known_args` remainder handling.
3. **File format `label=key`, parsed by Python, not sourced.** A sourceable shell file was rejected:
   it invites sourcing from rc files, which is exactly what the user wants to avoid, and Python
   parsing lets us validate and never echo values. Default `~/.config/ccst/api-keys`
   (outside `~/.claude`, honours the requirement that cccs manages it as a separate file), override
   `CCST_API_KEYS_FILE`.
4. **Permission check.** Refuse files with any group/other mode bits (`mode & 0o077`), like ssh does.
5. **Menu.** Reuse `pick_from_list`; labels only. More than 10 labels: error asking for `-k`
   (the picker's documented limit) rather than extending it.
6. **Template** lives at `src/cc_session_tools/config/api-keys.example` (already-packaged `config/*`).
   It is not installed anywhere by ccst; cccs owns placing the real file.

## Risks / Trade-offs

- [Claude Code may still prefer an existing subscription login over `ANTHROPIC_API_KEY`, or prompt
  to approve the key on first use] -> verify manually during apply; if needed also pass the
  documented env/flag that forces API-key auth, and record the finding in the design.
- [Keys in the environment are visible to child processes of `claude`] -> accepted; same exposure as
  any exported `ANTHROPIC_API_KEY`.
- [Sessions started with an API key are indistinguishable in sessions.db] -> accepted for now.

## Open Questions

- None blocking. Whether to add a `ccst doctor` check for the keys file can follow later.
