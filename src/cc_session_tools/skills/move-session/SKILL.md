---
name: move-session
description: Use when copying, relocating, or renaming a Claude Code session - i.e. changing a `cc-sessions/<tag>/` directory's parent project, its tag suffix, or both, while keeping the `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` transcript resumable. Supports MOVE (different project cwd), RENAME (new tag suffix in place), and MOVE+RENAME (both at once). Triggers on "move session to", "relocate session", "copy CC session into another project", "this session belongs in a different folder", noticing a session was started under the wrong working directory, "rename my session", "rename session tag", "rename cc-sessions directory", "give my session a more descriptive tag/name", or any phrasing that implies changing the cc-sessions directory's parent or suffix without losing the transcript.
---

# Move session

**Dry-run by default. Pass `--execute` to make any filesystem changes.** Without `--execute` the script prints a plan and exits 0. The user expects something to happen - check that you actually passed `--execute` before reporting success.

Claude Code stores a session in two places:

1. **User-facing folder:** `<src-cwd>/cc-sessions/<tag>/` (with `working/` and `out/`).
2. **Transcript:** `~/.claude/projects/<encoded-src-cwd>/<uuid>.jsonl`, where `<encoded-src-cwd>` is the absolute path with `/` replaced by `-`.

This skill is the **only sanctioned way** to change a session's `cc-sessions/<tag>/` directory after creation. It supports three operations (per CLAUDE.md):

- **Move** to a different project cwd (jsonl is copied to a new project key, paths rewritten).
- **Rename** the tag suffix in place (cc-sessions dir is copied to the new name; the jsonl stays where it is because the project key is unchanged, and gains only the picker-label records for the new tag).
- **Move + rename** atomically (both happen in one invocation).

The `YYYYMMDD-` start-date prefix is **immutable** in all cases. Every other part of the tag and the project location may change, subject to validation.

## When to use

- The user explicitly asks to move/relocate/copy a session into a different project folder.
- The user asks to rename a session's tag suffix (e.g. add a more descriptive ending).
- The user realises a session was started in the wrong directory (e.g. `one-shot/` when it should have been `socials/`) and wants to fix it without losing the transcript.

## When NOT to use

- Cleaning up old hook-security-check transcripts - that is the `clean-hook-sessions` skill.
- Moving a session to a *fresh* directory that does not yet exist - first ensure the destination cwd exists on disk and is a direct subdir of one of the roots in `~/.claude/cc-session-roots.txt`.
- Changing only the *picker display name* without touching the cc-sessions directory - that is Claude Code's built-in rename command, not this skill.

## Defaults and safety

- **Copy, not move.** Source files are never deleted by the script. After a successful EXECUTE, the script writes a `/tmp/move-session-cleanup-<tag>-<ts>.sh` bash script the user can run themselves once they have verified the destination resumes correctly. This indirection exists because the global `bash-hard-deny` hook blocks `rm` of local files from inside CC, so the script cannot perform the cleanup itself.
- **Dry-run by default.** No filesystem changes happen unless `--execute` is passed.
- **Memory mostly not copied, but auto-included when source key has only this session.** `~/.claude/projects/<encoded-cwd>/memory/` is shared per project key. By default, memory is NOT copied unless `--include-memory` is passed. Exception: if the source project key contains exactly one session (the one being moved), the memory effectively belonged to it, and the script auto-includes it to avoid orphaning the dir. The output names which path was taken.
- **Tombstone on by default.** In copy mode with `--execute`, the script appends a synthetic user/assistant exchange to the SOURCE jsonl announcing the move, plus drops a `TOMBSTONE.md` in the source `cc-sessions/<tag>/`. Disable with `--no-tombstone`.
- **Messaging safety (rename + project move).** After a rename or move, the script refreshes the `from_session` display tag in any pending (non-archived) messages that reference the moved session's uuid. uuid routing is unaffected — messages are never orphaned — so this is a cosmetic update that keeps receipts and digests showing the new tag. On a project move the session's uuid-keyed delivery cursor is preserved unchanged; no message is re-delivered or lost.
- **Task file migration.** When a session is moved or renamed, `~/.claude/tasks/` stores task JSON files keyed by the session directory's absolute path (with `/` replaced by `-`). The script migrates these automatically for all operation types (MOVE, RENAME, MOVE+RENAME), copying the source task directory to the new key. If the source task directory does not exist the step is silently skipped. Clobber check applies: aborts if the destination task directory already exists. The cleanup script (`/tmp/move-session-cleanup-*.sh`) includes `rm -rf` of the source task directory.
- **Picker label (tag-changing operations only).** When the operation changes the tag (`RENAME` or `MOVE+RENAME`), the script appends a `custom-title` record and a matching `agent-name` record carrying the new tag to the transcript the session resumes into (the shared jsonl on `RENAME`, the destination copy on `MOVE+RENAME`). These are the records Claude Code's own rename command writes, so the picker and status line show the new tag on first resume with no step inside the session. The append happens after destination verification and before the tombstone, in a single write; the script re-reads it and exits 1 naming the transcript if it does not read back as intended.
- **Tag mapping update (tag-changing operations only).** When the tag changes, the script upserts the new tag suffix into the `session_tags` table in `sessions.db` (`~/.local/share/claude/sessions.db`), keyed by the session's UUID, via `sessions_db.write_tag`. This keeps `session_tags` coherent with the cc-sessions directory, so `ccr <new-name>` confirms the session through its tag lookup and a later move can tell the session apart from same-date siblings.
- **Enumeration row re-key (all operations).** Session enumeration for `ccr`/`ccs` is backed by the `sessions` table in `sessions.db`, keyed by `(project_dir, basename)`. Unlike the old `.last-opened`/`.last-active` sentinel files (which lived inside `cc-sessions/<tag>/` and moved with the directory for free), this row does NOT follow a filesystem move. On every successful MOVE / RENAME / MOVE+RENAME the script re-keys the row: it copies the source row's `last_opened`/`last_active` timestamps onto a fresh destination row keyed by the new `(project_dir, basename)`, then deletes the stale source row. A pre-migration session with no source row still gets a destination row created (so it becomes discoverable going forward). Without this step the moved session would vanish from `ccr`/`ccs` at both the old and new locations.
- **Refuses to clobber.** Aborts if any destination path already exists.
- **Refuses in-session moves, and renames of open sessions.** If the source session is the *currently running* CC session, or (for a tag change) any live Claude Code process has the session open, the script refuses with exit code 2 and prints a recipe of commands the user must run after exiting that session. There is no override flag - see "In-session refusal" below for why and the detection signals.

## Design decisions

### Historical references are not rewritten

When a session is moved or renamed, this skill does NOT rewrite references to
the old basename or old path that appear inside `WORKLOG.md`, the JSONL
transcript records before the move, or any other file the user wrote during
the original session.

This is an intentional design decision, not an oversight. Three reasons:

1. **Historical record integrity** — `WORKLOG.md` and earlier messages
   document what happened at the time. Rewriting them retroactively would
   falsify the record and lose the breadcrumb trail (e.g. "I called it
   `foo-bar`, then renamed it to `foo-baz` once I realised what the work
   actually was").
2. **Search still works** — `ccs <old-fragment>` continues to find the session
   via the tombstone in the source JSONL and the unchanged historical
   references inside the moved files.
3. **No safe definition of "reference"** — any rewrite would need to
   distinguish casual mentions ("the old foo-bar session") from path-shaped
   strings that are still valid (relative paths in shell snippets, URLs, etc.).
   The skill stays out of that judgement call.

If the user wants old references updated, they can do it manually with
`sed` / `rg --files-with-matches` — but that is an explicit user action, not
something the skill takes responsibility for.

### Why the SKILL.md uses `ccst move-session`

All script invocations in this SKILL.md use `ccst move-session <args>`. It runs
the bundled `scripts/move_session.py` with the arguments unchanged, under the
Python interpreter that has `cc-session-tools` installed, and exits with the
script's exit code. Running the script with a bare `python3` fails on a
`uv tool` or `pipx` install, because that interpreter cannot import
`cc_session_tools`. `ccst move-session --help` prints the script's own usage.

## In-session refusal

If a user asks the model to move or rename the session they are currently in, **the model must not bypass the refusal**. Run the script as normal; the script will detect the in-session condition and print the recipe. Read the recipe back to the user verbatim.

**Why no override:** Claude Code's process has its cwd and jsonl path fixed at startup. Moving the session while it is running creates a frozen snapshot at moment T — the live conversation continues writing to the source from T onwards, so the destination silently diverges. There is no in-session use case where this is the right outcome; the model should never pass an override flag and the script does not provide one.

**Detection signals.** Refusal fires if `in_cc` AND **either** (a) **or** (b) is true, or, for a tag-changing operation (`RENAME` / `MOVE+RENAME`), if (c) **or** (a) is true wherever the script runs:
- `in_cc`: `$CLAUDECODE=1` or `$CLAUDE_PROJECT_DIR` is set (we are inside CC at all).
- (a) `recent_write`: the source jsonl mtime is within the last 30 seconds (CC is actively appending to it - this is the running session, by definition).
- (b) `cwd_match_and_freshest`: `realpath(getcwd()) == src_cwd_abs` AND the source jsonl is the most-recently-modified non-hook-security jsonl in the source project key dir. This catches the case where you're inside the source session but it has been idle for >30s (no recent write); your source jsonl will still be the freshest in its key dir because no other session writes to it. Sibling sessions don't trigger this because *their* jsonl is the freshest in the dir, not the source's.
- (c) `live_process`: a Claude Code process record `~/.claude/sessions/<pid>.json` has `sessionId` equal to the session's uuid and `<pid>` is alive (the session is open in some terminal). This applies to tag changes even from a plain shell, because the open session re-appends its old title after every prompt and would undo the new picker label. A record whose process has exited is ignored.

**The recipe printed on refusal:**
1. Exit the CC session that has the source open (`Ctrl-D` or `/exit`).
2. From a normal shell, re-run `ccst move-session ... --execute`. The recipe preserves the exact flags you passed (`--dst-cwd`, `--rename-tag`, `--uuid`, `--include-memory`, `--no-tombstone`) so it matches your intent - it is NOT a generic move template.
3. Resume from the new location via `cd <dst-cwd> && claude --resume <uuid>`. A tag change already set the picker label, so there is nothing to type in the resumed session.
4. (Optional, after verification) clean up the source. The recipe distinguishes between operation types:
   - `MOVE` or `MOVE+RENAME`: delete both the source `cc-sessions/<tag>/` and the source jsonl (the destination jsonl is in a different project-key directory).
   - `RENAME`-only: delete only the source `cc-sessions/<tag>/`. The jsonl is **shared** (same project key, never copied) and must NOT be deleted - doing so would destroy the live transcript.

## Quick reference

```
ccst move-session \
    --src-session <path-to-source-cc-sessions-dir> \
    [--dst-cwd    <path-to-destination-cwd>] \
    [--rename-tag <YYYYMMDD-new-tag>] \
    [--uuid       <session-uuid>] \
    [--include-memory] \
    [--no-tombstone] \
    [--execute]
```

| Flag | Meaning |
|------|---------|
| `--src-session` | Path to source `cc-sessions/<tag>/` directory (the one with `working/` and `out/`). Required. |
| `--dst-cwd` | Path to destination working directory. Optional if `--rename-tag` is given. Must be a direct subdirectory of one of the roots in `~/.claude/cc-session-roots.txt` (same file `ccd` reads). |
| `--rename-tag` | New tag for the cc-sessions directory. Original `YYYYMMDD-` prefix MUST be preserved (immutable). May be combined with `--dst-cwd` to rename and move atomically. |
| `--uuid` | Disambiguate when multiple jsonls match the source session. Optional. |
| `--include-memory` | Also copy `memory/` into the destination project key dir (only meaningful when `--dst-cwd` changes the project key). Default: off. |
| `--no-tombstone` | Skip the source-side tombstone records and `TOMBSTONE.md`. Default: tombstone on. |
| `--execute` | Actually perform the operation. Without it, the script reports a plan only. |
| `--verify-only` | Skip copy/rewrite; only re-run verification against existing destination (requires `--dst-cwd`). |
| `--force` | Bypass the destination root-membership check and the strict-root project-name + tag-prefix rules. Tag-format checks (no spaces / underscores / double-dashes / trailing dash, immutable `YYYYMMDD-` prefix when renaming) still apply. Mirrors `ccd --force`. |

At least one of `--dst-cwd` or `--rename-tag` must be supplied. The combination determines the operation:

| `--dst-cwd` | `--rename-tag` | Operation |
|---|---|---|
| present, different from src cwd | absent | **MOVE** - copies cc-sessions dir and jsonl to new project key, rewrites paths |
| absent (or = src cwd) | present | **RENAME** - copies cc-sessions dir to new name in same parent; jsonl is not copied or rewritten (project key unchanged, source dir preserved by copy so old paths still resolve) and gains only the picker-label records |
| present, different | present | **MOVE+RENAME** - both happen atomically; path rewrites include `cc-sessions/<old-tag>` → `cc-sessions/<new-tag>` |

## Tag validation

`--rename-tag <new-tag>` must satisfy:

- Format `^\d{8}-[a-zA-Z0-9][a-zA-Z0-9-]*$` (8-digit date prefix, dash, then alphanumerics-and-dashes starting with alphanumeric).
- Original date prefix preserved (the `YYYYMMDD-` is **immutable**; trying to change it is rejected).
- No spaces, no underscores, no double-dashes, no trailing dash.

## Destination cwd validation

`--dst-cwd <path>` must be a **direct subdirectory** of a root listed in `~/.claude/cc-session-roots.txt` (after `readlink -f`). This is the same data file `ccd` reads, so the rule is identical. To allow a new root, edit that file (it has comments explaining the format).

**Strict-root rules.** When the resolved destination root is `~/cc-claude-code` (the "strict" root), two additional rules apply:

- The destination project directory name must match `^[a-z0-9]+$` (no dashes, no uppercase).
- The destination tag's suffix-after-date (`<suffix>` in `YYYYMMDD-<suffix>`) must equal the project name or start with `<project>-`. So a session moving to `~/cc-claude-code/dea/` must have tag `20260504-dea` or `20260504-dea-<rest>`; if the source tag doesn't satisfy this, you must combine the move with `--rename-tag` to fix it.

Strict-root rules and the root-membership check are bypassed by `--force`. Tag-format checks (no spaces / underscores / double-dashes / trailing dash, immutable `YYYYMMDD-` prefix when renaming) are NOT bypassed by `--force`.

These rules are defined once in `scripts/cc_session_rules.py` and shared with `ccd` (which calls the module's CLI for new-session validation). Editing the rules in one place is sufficient; both code paths pick up the change.

## What the script does (happy path)

1. Resolves `src-cwd` from the parent of `cc-sessions/<tag>/` and computes encoded project keys for both src and dst.
2. Lists jsonls in `~/.claude/projects/<encoded-src-cwd>/` and filters out hook-security-check transcripts (first user message starts with `Review this shell command for security risks`).
3. Picks the jsonl belonging to this session, in priority order:
   1. `--uuid <id>` exact match wins (even hook-security transcripts).
   2. Single non-hook candidate -> use it.
   3. A `custom-title` record matching the cc-sessions tag (the strongest discriminator when present - written by `claude -n <tag>` at startup, by Claude Code's rename command, or by this script's RENAME).
   4. A `session_tags` match in `sessions.db`: a RENAME records the new tag suffix there, keyed by uuid. This resolves a renamed session whose transcript has no title for the current tag, such as one renamed by an earlier version of this script.
   5. Filter remaining candidates by YYYYMMDD prefix in the cc-sessions tag matched against the jsonl's first timestamp.
   6. Otherwise list candidates and require `--uuid`.
4. **Refuse early** if tombstoning is on (the default) and the source jsonl has no parseable records with a uuid - the tombstone path needs `last_record` and would otherwise crash deep inside the writer. The error names the offending jsonl and tells you to either fix it, pick a different source, or pass `--no-tombstone`.
5. Refuses if any destination file already exists (clobber check).
6. **On `--execute`:**
   - Always copies `cc-sessions/<tag>/` (or `<new-tag>/`) from src into the destination parent.
   - **MOVE / MOVE+RENAME (cwd changed):**
     - Creates `~/.claude/projects/<encoded-dst-cwd>/` if missing.
     - Copies the jsonl to that directory under the same UUID.
     - Rewrites path strings in the destination jsonl:
       - Long path: `<src-cwd-abs>` → `<dst-cwd-abs>`
       - Project key: `-<src-cwd-encoded>-` → `-<dst-cwd-encoded>-`
       - Tilde form (only if `$HOME` prefixes the src abs): `~/<rel>` → `~/<dst-rel>`
       - Tag substring (MOVE+RENAME only): `cc-sessions/<old-tag>` → `cc-sessions/<new-tag>`
     - Runs jsonl verification (see below).
     - MOVE+RENAME only: appends the picker-label records for the new tag to the destination jsonl, after verification.
     - Memory: copies `memory/` to dst project key dir if `--include-memory` is passed, OR auto-includes if the source project key contains exactly this one session.
     - Tombstone: appends two records (user + assistant) to the SOURCE jsonl, with proper `parentUuid` chain and the SOURCE `sessionId`/cwd/version preserved, AND writes a `TOMBSTONE.md` into the source `cc-sessions/<tag>/`.
   - **RENAME-only (cwd unchanged):**
     - Does NOT copy or rewrite the jsonl - project key is unchanged, so the source jsonl IS the destination transcript and continues to serve the resumed session directly. Appends the picker-label records for the new tag to it.
     - Does NOT run jsonl verification - there is no destination jsonl to verify.
     - Does NOT touch memory - same project key, same memory; if `--include-memory` was passed it is ignored with a `WARNING` line.
     - Tombstone: writes a `TOMBSTONE.md` into the source `cc-sessions/<tag>/` only. Does NOT append tombstone records to the jsonl: they are conversation content claiming the session has moved, and in the transcript the user resumes into they would corrupt the conversation.
   - Both branches (tag-changing only): upsert the new tag suffix into `sessions.db`'s `session_tags` table, keyed by uuid.
   - Both branches: write a `/tmp/move-session-cleanup-<tag>-<ts>.sh` script the user can `bash` after verifying the destination resumes (the cleanup script omits the jsonl `rm` for RENAME-only).

## Verification

Verification only runs on operations that change the project key (MOVE / MOVE+RENAME). Those are the only cases where a destination jsonl is copied and rewritten. RENAME-only operations have nothing to verify - the source jsonl IS the destination - and the script skips this block. On MOVE+RENAME the picker-label records are appended after verification, so the line counts below compare the copy before that append.

When verification runs, check the script's "VERIFICATION" output block before declaring success. The mandatory checks are:

- `lines src == lines dst` for the jsonl
- `json_valid_lines == lines dst`
- `remaining_src_long_path == 0` and `remaining_src_encoded_key == 0`
- `cwd_distinct == {<dst-cwd-abs>}` (only the destination cwd appears in any cwd field)

If any check fails during the `--execute` run, the script prints `VERIFY: FAIL` and exits non-zero. Do not present the move as successful in that case.

**Expected FAIL with `--verify-only`.** After a MOVE with the tombstone enabled (the default), `--verify-only` prints `RESULT : FAIL` on `lines src == lines dst`: the tombstone appends two records to the source after the copy, so the source is longer than the destination by design. The output then carries a `NOTE` naming the extra lines and saying the FAIL is expected; check `json_invalid_lines` (0) and `cwd_distinct_in_dst` (only the destination cwd) instead. If the `NOTE` instead says the extra lines are unexplained, the source and destination have really diverged.

## After moving

Tell the user to verify with:

```
cd <dst-cwd> && claude --resume <uuid>
```

(or `claude --resume` to use the picker). They should send a short test message and confirm a new entry appears in the destination jsonl (`tail -1 ~/.claude/projects/<encoded-dst-cwd>/<uuid>.jsonl`).

## Common mistakes

- **Passing the destination's `cc-sessions/` instead of its cwd.** `--dst-cwd` is the project root, not its session-store sub-folder.
- **Forgetting `--execute`.** Default is dry-run. The user expects something to happen - check that you actually passed `--execute`.
- **Assuming a single jsonl per session.** A session that has been resumed across multiple invocations can have multiple jsonls under one UUID prefix; the script only moves the one that matches the cc-sessions tag mtime. If the user reports missing earlier conversation, they may have a sub-session in a UUID-named subdirectory under the project key. Check for `~/.claude/projects/<encoded>/<uuid>/` directories matching the chosen UUID and pass them through manually.
- **Editing a transcript other than by the script's appends.** The only writes the script makes to an existing transcript are appends: the tombstone records (source jsonl, MOVE / MOVE+RENAME) and the picker-label records (the transcript the session resumes into, tag changes only). A transcript is never rewritten in place - the path rewrites apply only to the destination copy.
