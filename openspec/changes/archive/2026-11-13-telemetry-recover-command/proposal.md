## Why

Telemetry records only a SHA-256 `input_hash` per `bash-security-review` fire, never the command text, and that must stay true. The `update-command-cache` skill's `list` therefore shows hash, fire count, last-seen and a sample session id with no command, so vetting a candidate means walking a transcript by hand. A 2026-11-08 sweep left 324 candidates pending, which does not scale and costs model tokens per candidate.

## What Changes

- Add `ccst telemetry recover <hash>...`: for each hash (full, or a prefix of at least 8 hex characters; a `sha256:` prefix is accepted) it finds the exact command that hashes to it by reading the transcripts of the sessions that fired it, and prints one compact line per hash.
- A command is reported only when it re-hashes to the given hash. When no command can be confirmed but the hash fired in a known session, the line names that session and its transcript instead; otherwise the line says `unknown`.
- Batch by construction: every transcript is read at most once per invocation, however many hashes point into it.
- Gated by `CCST_FIRES_ACCESS=1`, the same self-imposed convention `update_command_cache.py list` uses (bash-hard-deny does not enforce it for this verb).
- Searches subagent transcripts as well as main ones (half of all fired hashes exist only in `<uuid>/subagents/`).
- `--scan-all` widens the search to every transcript for hashes with no telemetry row.
- `update-command-cache list` uses the same code to print a truncated command preview for each candidate for triage (`--no-preview` skips it); `SKILL.md` requires the full command (`recover --max-chars 0`) to be shown before `promote`, and passes the recovered preview into `promote`.
- Telemetry storage is unchanged: still hash-only.
- The combined release (this change plus `move-session-write-picker-label`) ships as 4.0.3, a patch, by the maintainer's decision, although a new CLI verb is normally a minor bump. This change lands last, so its final task owns the CHANGELOG entries and version bump for both.

## Capabilities

### New Capabilities
- `cli/telemetry-recover-command`: hash-to-command (or hash-to-session) recovery from telemetry and transcripts.

### Modified Capabilities
- `skills/update-command-cache`: `list` shows a recovered preview per candidate; `SKILL.md` no longer says recovery is manual.

## Impact

- New `src/cc_session_tools/lib/command_recovery.py`; `src/cc_session_tools/cli/ccst.py` (new `telemetry recover` verb and its help listing).
- `src/cc_session_tools/skills/update-command-cache/` (`scripts/update_command_cache.py`, `SKILL.md`, tests).
- New tests under `tests/`; `README.md` telemetry rows; the projects-dir constant shared through `lib/paths`.
- `openspec/specs/cli/telemetry-recover-command/spec.md` (new) and `openspec/specs/skills/update-command-cache/spec.md` (modified), after sync.
- `pyproject.toml`, `uv.lock`, `CHANGELOG.md`: version bump at the end of the combined release.
