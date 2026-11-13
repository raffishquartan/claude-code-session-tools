## Context

- The picker/status label is read from `custom-title` (and `agent-name`) records in `~/.claude/projects/<key>/<uuid>.jsonl`. Real transcripts hold many such pairs: Claude Code appends the pair after every prompt (right after `last-prompt`) from its in-memory title, and `/rename` appends one too. The label therefore tracks the last record for a live process; what a fresh `claude --resume` loads is verified manually (see Risks).
- RENAME-only never copies the jsonl: source and destination are the same file (`dst_jsonl == src_jsonl`, same project key). MOVE+RENAME copies it to a new project key and rewrites paths.
- `move_session.py` refuses (exit 2) only when it runs inside Claude Code (`in_cc`) and either `recent_write` or `cwd_match_and_freshest` holds. Run from a plain shell, which is where the refusal recipe sends the user, it does not refuse even if the session is still open in another terminal.
- Verification on MOVE+RENAME requires `lines src == lines dst`, so the label append must follow it.
- `sessions_db.write_tag` populates `session_tags`, which `lib/sessions.py` strategy 1 (`find_jsonl_for_session`) and `discover_session_jsonl` step 4 read. `hook_registry.prune_stale_hooks` removes settings entries naming a retired hook verb, and `ccst doctor` flags them. `claude_code_usage/session_names.py` already reads `~/.claude/sessions/<pid>.json` (`sessionId`, `pid`).

## Goals / Non-Goals

**Goals:**
- The renamed session shows its new name in the picker and status line on first resume, with no user step.
- A rename never races a running Claude Code process.
- One documented, working way to invoke the script.

**Non-Goals:**
- Changing first-title readers: `claude_code_usage` and, through `load_jsonl_titles`, `lib.sessions.find_orphan_transcripts`. After a rename and the cleanup script, `ccs` can report a phantom orphan under the old name. That predates this change and is left alone.
- Tombstone behaviour, the clean-up script, memory or task migration.
- A MOVE without a rename: the tag is unchanged, so the existing title stays correct.

## Decisions

- **Append to the transcript the session resumes into**: `src_jsonl` for RENAME-only (the shared file), the copied `dst_jsonl` for MOVE+RENAME. Placement: directly after destination verification and before the tombstone step, so on MOVE+RENAME it writes only the destination copy and nothing earlier needs undoing, and verification's line-parity check is untouched.
- **Why titles are safe to append but tombstones are not.** Tombstone records are conversation content claiming the session has moved; in a transcript the user resumes into they would corrupt the conversation, so they stay out of RENAME-only. Title records are metadata that Claude Code itself appends on every prompt.
- **Record shape:** `{"type":"custom-title","customTitle":<new tag>,"sessionId":<uuid>}` then `{"type":"agent-name","agentName":<new tag>,"sessionId":<uuid>}`, with `<new tag>` the full `YYYYMMDD-...` directory name. Keys match real transcripts exactly. A test pins them.
- **One write, then re-parse.** The newline guard (when the file does not end in `\n`) and both records go out in a single `write()`. The last two lines are re-read and parsed; failure to parse exits 1 with the transcript path in the message.
- **Liveness check independent of `in_cc`.** For tag-changing operations the script refuses (exit 2) when any `~/.claude/sessions/<pid>.json` has `sessionId == <uuid>` and `<pid>` is alive, and as a fallback when the transcript was modified under 30 seconds ago. This reuses the existing refusal path and recipe text. Alternative: rely on the existing in-CC check - rejected because Claude Code would re-append the old title on the live process's next prompt, silently undoing the rename.
- **Keep the `session_tags` upsert.** It keeps `session_tags` coherent so `find_jsonl_for_session` strategy 1 confirms immediately and `discover_session_jsonl` step 4 still works. Only the "before `/rename` has been run" rationale in comments and docs changes.
- **Remove the hook outright rather than keep it as a fallback.** A fallback would keep a SessionStart hook, a bash script and a marker format alive for a case the script now handles. Markers already on disk are left alone: deleting files stays a user action.
- **`ccst move-session` dispatches before argument parsing.** `ccst.main()` checks for `move-session` as the first argument and runs `subprocess.run([sys.executable, <script>, *rest])`, returning its exit code. A regular argparse subparser cannot forward calls that begin with an option (every real call starts with `--src-session`), and `--help` would print ccst's own help instead of the script's. A subprocess keeps `sys.argv`, argparse `prog`, `sys.path` and `sys.modules` clean and uses the same interpreter that has `cc-session-tools` installed. The verb is documented in the `ccst` help listing. It is not exempt from install auto-sync (like other non-exempt verbs, the first call after an upgrade may run a sync first).
- **Delete the unused `SCRIPT_PATH` constant** once the recipe prints `ccst move-session`.

## Risks / Trade-offs

- [Undocumented transcript format may change in Claude Code] -> a shape-pinning test makes drift visible; failure mode is a stale picker label, not data loss.
- [A fresh `claude --resume` might load the first rather than the last title, in which case Claude Code would keep re-appending the old title] -> a real resume check is a pre-merge manual step recorded in the PR (scratch copy of a real session, rename via `ccst move-session`, `claude --resume`, send one prompt, confirm the picker/status line show the new tag and the newest `custom-title` is the new tag). If it fails, the hook removal is reconsidered.
- [Users with `ccst hooks run pending-rename` in `settings.json` before the next sync see an "unknown hook" warning at session start] -> `prune_stale_hooks` removes it on the next non-exempt `ccst` command; the CHANGELOG notes the warning.
- [Sessions renamed under the old version keep a stale label and an inert `.pending-rename` marker] -> the CHANGELOG gives a one-time remedy: `/rename <tag>` inside the session, then delete the marker.
- [Append fails after verification] -> on MOVE+RENAME the source jsonl, `cc-sessions/<tag>/` and the destination copy are intact and the tombstone has not yet been written; the script exits 1 naming the transcript. On RENAME-only nothing earlier needs undoing.

## Migration Plan

Ships in the combined 4.0.3 release. On the next non-exempt `ccst` invocation after upgrade, install sync prunes the `pending-rename` settings entry. Leftover `.pending-rename` files are harmless; the user may delete them.
