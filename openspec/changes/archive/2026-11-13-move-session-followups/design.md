## Context

- Claude Code writes `~/.claude/sessions/<pid>.json` with `pid`, `sessionId`, `startedAt` (epoch ms) and `procStart`, the output of `LC_ALL=C TZ=UTC ps -o lstart= -p <pid>` taken at process start, for example `Thu Nov 12 09:24:55 2026`. Records can outlive the process after an unclean exit, and pids are recycled. On the records inspected, `startedAt` falls 0.8 to 8.4 seconds after the process start.
- `ps -o lstart=` prints local time in the locale's order, so the same command without `TZ=UTC` and `LC_ALL=C` does not reproduce `procStart` on a non-UTC or non-English machine.
- `detect_live_holder` currently refuses when the record names the session and `os.kill(pid, 0)` succeeds. `PermissionError` counts as alive.
- `append_tombstone` appends a user record (content beginning `[TOMBSTONE]` and naming the destination `cc-sessions` directory) and an assistant record (model `tombstone`) to the source after the copy; `--verify-only` later compares `src_lines <= dst_lines`. A source moved twice (A to B, then A to C) carries both pairs at its end, and the C copy was made after the B pair existed, so it contains that pair.
- `~/.claude/sessions` is built ad hoc in `move_session.py` and as an import-time constant in `claude_code_usage/session_names.py`; `~/.claude/tasks` is hardcoded in `move_session.py` and `delete_sessions.py`.

## Goals / Non-Goals

**Goals:**
- A stale record for a reused pid never blocks a rename, a live session is never judged stale, and a genuine refusal tells the user how to clear a stale record.
- `--verify-only` after a default MOVE passes when, and only when, the only extra source lines are the tombstones appended for that destination.
- One definition of each `~/.claude/...` directory this skill and its siblings use.

**Non-Goals:**
- Handling records from another pid namespace (`pidDomain`, containers); they keep today's pid-alive behaviour.
- Tombstone format, the `--no-tombstone` flag, strict (post-copy) verification, or a liveness override flag.
- Showing what command a reused pid now belongs to.
- The stale `cc-session-roots.txt` wording in `SKILL.md` and the `--dst-cwd` help text (unrelated to this change).

## Decisions

- **Time-based staleness, not string equality.** `process_start_time(pid)` runs `ps -o lstart= -p <pid>` with `LC_ALL=C` and `TZ=UTC` in the environment, parses the result with `time.strptime(..., "%a %b %d %H:%M:%S %Y")` and `calendar.timegm`, and returns epoch seconds, or `None` if `ps` is missing, fails, prints nothing or prints something unparseable. The record's `procStart` is parsed the same way. A record is stale only when both are known and the live process started more than two seconds after the record's `procStart`. Every other case (no `procStart`, unparseable, `ps` unavailable, live not later) keeps the record as a live holder. One-sided on purpose: clock changes or jitter make the script refuse, never proceed. Alternative: string equality - rejected (breaks with time zone and locale, so it could call a live session stale, the dangerous direction).
- **Hint on every liveness refusal.** The reason names the record file and says that if no Claude Code session is open the record is stale and may be deleted. No override flag.
- **Verify-only excludes the tombstones for this destination.** `verify_dst_jsonl` (non-strict only) receives the destination `cc-sessions` directory. It finds the first user record in the source whose content begins `[TOMBSTONE]` and contains `cc-sessions: <dst session dir>`, then counts from there to the end of the file the lines belonging to tombstone pairs (a `[TOMBSTONE]` user record, or an assistant record whose model is `tombstone`). Those lines are excluded from the source length. Any other line after that point stays counted, so conversation appended to a still-live source is reported as unexplained. With no matching tombstone nothing is excluded. Alternative: count every trailing pair - rejected: after A to B then A to C it would subtract the pair already present in the C copy.
- **Wording.** The PASS output contains the line `tombstone lines excluded from src: N` and avoids the word "expected"; a remaining excess prints the "unexplained" note and FAIL.
- **Signal exit status.** `_cmd_move_session` returns `128 + signum` when the child's `returncode` is negative. (Ctrl-C reaches the parent as `KeyboardInterrupt` and is not part of this mapping.) Tested by patching `subprocess.run` to return a negative `returncode`.
- **Paths.** `claude_live_sessions_dir()` and `claude_tasks_dir()` join `claude_projects_dir()` in `lib/paths`, resolved on each call. `session_names.load_live_names` resolves its default through `claude_live_sessions_dir()` at call time instead of an import-time constant.
- **Tail check.** After the append the last two lines are read from at most the last 64 KiB of the file in binary mode and decoded line by line; the appended pair is under 1 KiB so the window always holds both.

## Risks / Trade-offs

- [`ps` unavailable or output format different on another platform] -> fallback is the existing pid-alive refusal.
- [A reused pid started within two seconds of the original's recorded start] -> negligible; the record would still refuse.
- [Verify-only now exits 0 where it exited 1 after a default MOVE] -> intended; documented in `SKILL.md` and the CHANGELOG under Changed.
- [A real divergence of exactly the tombstone's length] -> not distinguishable by line counts; the JSON-validity and cwd checks remain.
- [`session_names` loses its import-time constant] -> tests that patched it are updated in the same task.

## Migration Plan

Ships as 4.1.1. No data migration.
