## Context

- `hooks.bash_security_review` hashes the exact command string (`hooks.cache.sha256_command`: SHA-256 of the UTF-8 bytes of `hi.command`, no rewriting) and logs `sha256:<hex>` plus the hook's `session_id` to `telemetry_events`. The same string is the `input.command` of a `tool_use` record named `Bash` in a transcript. Measured on live data, hashing each Bash command in the firing session's files reproduces 99.94% of fired hashes.
- Transcripts live at `~/.claude/projects/<key>/<uuid>.jsonl` and, for subagents, `~/.claude/projects/<key>/<uuid>/subagents/agent-*.jsonl`. The hook's `session_id` for a subagent command is the parent session's uuid. About half of all fired hashes (and most candidate matches) exist only in subagent files.
- `update_command_cache.py` already reads `telemetry_events`, groups by hash and keeps one sample session per hash. Its `CCST_FIRES_ACCESS=1` gate is a convention shared with bash-hard-deny's telemetry-read rule; bash-hard-deny never matches `ccst telemetry recover` and does not enforce the variable for it.
- Transcripts are large (3.7 GB total, largest file ~134 MB); a batch scan of all candidate sessions measured about 6 s.

## Goals / Non-Goals

**Goals:**
- Deterministic, exact, batch recovery with compact output that never dumps transcript content.
- Reuse by `update-command-cache list`, with no second implementation.
- Previews that cannot be mistaken for the full command when it matters.

**Non-Goals:**
- Storing command text anywhere new (telemetry stays hash-only).
- Fuzzy or normalised matching: only exact SHA-256 equality counts.
- Recovering commands that never reached a transcript (deleted sessions).
- Marking candidates that can never be cached (heuristic hits bypass the cache by design, `skip_cache = bool(hits)`); a future `list` could tag them.

## Decisions

- **Searched files.** For session S: `<projects>/*/S.jsonl` and every `<projects>/*/S/subagents/*.jsonl`. `--scan-all` replaces that targeted set with every transcript file in one pass, so no file is opened twice.
- **Targets-driven scan.** Resolve hashes to sessions with one telemetry query (a hash fired in several sessions searches all of them; the `session` line names the one with the latest `ts`), group hashes by session, read each file once. Cheap prefilter: a line is parsed as JSON only if it contains `"name":"Bash"` (the compact form Claude Code writes).
- **Validation up front.** Each argument is lower-cased, a `sha256:` prefix stripped, and must be 8-64 hex characters; any malformed argument makes the command exit 2 with a stderr message naming it, before anything is read. Duplicate arguments are collapsed.
- **Prefix semantics.** A prefix is `ambiguous` if it matches more than one distinct full hash across telemetry and (with `--scan-all`) transcript commands. A `command` result is reported only on equality with the full hash (or the prefix then the full hash).
- **Output.** One tab-separated line per distinct argument, in argument order: the normalised argument as given, status (`command` | `session` | `ambiguous` | `unknown`), full 64-character hash (`command` only, else empty), session uuid, `<transcript path>:<1-based line>`, command. Command text is escaped first (tab, CR, LF and other C0 characters as `\t`, `\r`, `\n`, `\uXXXX`; non-ASCII literal), then truncated on a character boundary that never splits an escape, then, if cut, suffixed `…[+N chars]` (N = elided characters of the escaped text), a marker that cannot occur in escaped text of a command ending in `...`. Default `--max-chars` 200; `0` means no limit.
- **Exit codes.** 2 for refusal (gate) or malformed argument; 0 whenever every line printed, whatever the statuses.
- **Library, not script glue.** `lib/command_recovery.py` (pure functions plus one file-reading helper) takes a `projects_dir` parameter defaulting to the repo's single projects-directory constant (hoisted into `lib/paths` and reused by `lib/session_gc.py` and `lib/sessions.py` rather than copied a third time); the verb has `--projects-dir` and `--hooks-dir` for tests and parity with `telemetry query`.
- **`update_command_cache list`.** Previews are for triage and truncated. SKILL.md's promote step requires the full command first (`ccst telemetry recover --max-chars 0 <sha>`) because a cache entry approves the whole command, and passes the recovered text to `promote --preview`. The root parser gets `no_preview` defaulted so the no-subcommand path (`list` by default) works; the refusal text names the right re-run command.
- **MODIFIED requirement header.** The existing header "...and preview limitation" is kept to avoid a RENAMED block; its text now describes the new behaviour.

## Risks / Trade-offs

- [Large transcripts make a sweep slow] -> one read per file, substring prefilter before `json.loads`; measured ~6 s for all candidate sessions.
- [Command in a transcript differs from what the hook hashed] -> reported as `session`, never as a guessed command; measured 6 of 10,334 hashes.
- [Command text in output reaches model context] -> intentional for review; bounded by `--max-chars`, and the truncation marker states how much was cut.
- [Telemetry rows trimmed, leaving hashes with no session] -> `unknown`, with `--scan-all` as the explicit opt-in sweep.
- [Gate is a convention, not enforced] -> documented as such; a test asserts bash-hard-deny does not block `ccst telemetry recover`, so a future regex change cannot silently break the skill.
