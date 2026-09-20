## Context

Tier order today: 0 trivial allowlist, 0.5 read-only pre-filter, 1 heuristic (always escalates,
never cached), 2 command cache, 3 `claude -p --model <model>`. The review prompt contains only the
command text - the reviewing model never sees a script's content. `command-cache.db` already holds
`command_cache` and `hook_invocations`; `hook_invocations.exit_tier` takes 0, 2, 3 and the
`cache_efficiency` view reads it.

## Decisions - Change A (slim call)

### A1. Measure, then pick; acceptance bar
Add `scripts/measure_review_call.py` that runs a fixed corpus (~20 commands covering safe,
suspicious, dangerous and one-line trivial cases) through `claude -p --output-format json` for each
candidate flag set and records `usage` (input, cache-creation, cache-read, output) from the JSON.
Acceptance for the adopted set: at least a 70% reduction in first-request cache-creation tokens
versus today's call (target figure, revisable once real numbers exist), and identical VERDICT on
every corpus command with SUMMARY/RISKS still present. Numbers go into the CHANGELOG entry.

### A2. Candidate mechanisms and known unknowns
- `--disable-slash-commands`, `--strict-mcp-config` (no `--mcp-config` given, so no MCP servers):
  low risk, expected to remove the skill listing and deferred-tool listing.
- Running the child with an empty temp dir as its cwd: removes project `CLAUDE.md` discovery.
  `session_prefix()` and the prompt are unaffected because the hook computes them from the
  parent's cwd, not the child's.
- Global `CLAUDE.md` (the bulk of the 230k chars) is not governed by `--setting-sources`. Two ways
  to drop it: a custom `--system-prompt` (replaces default prompt, may not affect CLAUDE.md injection),
  or `CLAUDE_CONFIG_DIR` pointing at a minimal directory. **Unverified:** on macOS, changing
  `CLAUDE_CONFIG_DIR` may change which keychain entry holds OAuth credentials, in which case the
  child would be unauthenticated. This is measured in the first implementation task; if it breaks
  auth, that mechanism is dropped rather than worked around.
- Child SessionStart hooks (~13k tokens of context) are already partially suppressed by
  `CLD_SESSION_MODE=hook`; whether `--setting-sources` can drop hook registration without dropping
  auth is part of the same measurement.

### A2a. Measured results (2026-09-20, macOS, claude 2.1.278, sonnet, 12-command corpus)

Mean first-request context (input + cache-creation + cache-read tokens) per fire. Baseline was
measured from this repository's directory, whose own `CLAUDE.md` is far smaller than a typical
project's; real baselines (~120k+) are higher, so the reduction is understated.

| Candidate | Mean context tokens | Notes |
|---|---|---|
| baseline (`claude -p --model sonnet`) | ~51,500 | 11/12 verdicts as labelled |
| + `--disable-slash-commands --strict-mcp-config --tools "" --no-session-persistence` | ~24,400 | 2-command sample |
| + empty scratch cwd | ~20,600 | 2-command sample |
| + custom `--system-prompt`, `--exclude-dynamic-system-prompt-sections` | ~18,500 | 2-command sample; user-level CLAUDE.md still loaded |
| + `--setting-sources local` (**adopted**) | ~770 | full corpus, run twice, identical results |
| `CLAUDE_CONFIG_DIR` -> empty dir | n/a | child exits 1, unauthenticated on macOS; rejected |

Adopted invocation vs baseline on the corpus: 98.5% fewer tokens; SUMMARY/RISKS/VERDICT present in
12/12 responses; verdicts identical on 11/12. The one difference is `echo ... | base64 -d | sh`
(baseline `safe`, adopted `suspicious`); that command is Tier-1 heuristic-flagged and never cached
regardless, and the adopted verdict is the more severe. A first attempt with a one-sentence system
prompt returned `suspicious` for benign project commands; adding the verdict rubric to the system
prompt fixed it.

### A3. Failure behaviour unchanged
The hook never blocks. A flag the installed `claude` rejects makes the child exit non-zero, which
already yields `[security review unavailable: claude exited N]`. Version drift is therefore loud
(and `ccst doctor` gains no new check for it); no silent fallback to the fat call, since a fallback
would double cost exactly when something is wrong.

## Decisions - Change B (script allowlist)

### B1. Storage: a table in the existing hook database, edited via CLI
"Configurable, not hard-coded" plus this repo's data-store convention (SQLite, WAL, shared
connection helper, ships with a query subcommand) gives: a new table `script_allowlist` in
`command-cache.db`, managed by `ccst hooks allowlist list|add|remove|verify`. Columns: `script_path`
(realpath, primary key), `sha256`, `project_root`, `source` (`auto` | `manual`), `interpreter_hint`,
`added_at`, `last_used`, `use_count`. It is machine-local data and is never committed. Alternative
considered: a YAML file. Rejected because hashes are machine-generated and a hand-edited file
invites stale or mis-typed hashes; the CLI's `add` computes the hash itself.

### B2. Tier placement
`0 -> 0.5 -> 1 (heuristics) -> 1.5 (allowlist) -> 2 (cache) -> 3 (claude)`. Because Tier 1's
heuristics run over the whole command including arguments, any heuristic hit escalates before the
allowlist is consulted; an allowlisted script called with `/etc/...` in its arguments is still
reviewed. Telemetry for an allowlist hit uses `cache="hit"`, `verdict="safe"`; `hook_invocations`
gets `exit_tier=2` and `cache_source="script-allowlist"`, so `cache_efficiency` counts it with no
schema change.

### B3. What "the verdict is about the script" requires
Today the reviewer sees only the command line, so `safe` on
`python3 scripts/foo.py add --subject x` says nothing reliable about `foo.py`. To make hash-pinning
meaningful, when the command is a recognised script invocation and the script is readable and
<= 64 KiB, `build_prompt` appends the script's content (read once; the same bytes are hashed) and asks
the reviewer to assess the script's behaviour under arbitrary arguments. Auto-add requires that
content was included. Larger or unreadable scripts are reviewed as today and never auto-added
(a user can still `ccst hooks allowlist add` them, which is an explicit user vouch).

### B4. Which command shapes match
A command matches only if all of the following hold, otherwise it goes to the normal tiers:
- It is one simple command: no unquoted `| ; & < >`, no command/process substitution, no backticks
  anywhere outside single quotes, no unquoted `$` expansion. Checked by a small quote-aware scanner,
  not a regex over the raw string, so quoted arguments containing those characters (subjects, JSON)
  are fine.
- Shape is `<interp> <script> args...` where `<interp>` is `python`, `python3`, `python3.N`, `bash`,
  `sh`, `zsh`, `node`, or `uv run` optionally followed by one of `python`/`python3`; or the script
  is invoked directly as a path (`./x.sh`, `scripts/x.py`) and is executable.
- No interpreter options between interpreter and script (`python3 -c`, `-m`, `uv run --with ...`,
  `bash -c`). This mirrors the existing `_UV_RUN_PREFIX_RE` caution: unknown flags mean unknown
  behaviour, so bail out.
- No leading `VAR=value` assignments or `env` wrapper.

### B5. Arguments
Arguments are not part of the match. The stored verdict is about the script (B3), the shell-structure
rule (B4) means arguments cannot smuggle a second command, and the heuristic tier still inspects the
full argument text. Residual risk, accepted and documented: a script that itself executes its
arguments (`--exec`, `eval`) is only as safe as the reviewer's reading of it under arbitrary
arguments; this is why the reviewer is asked about arbitrary arguments explicitly.

### B6. Path resolution and the project boundary
Resolve the script relative to the hook's `cwd`, then `realpath`. It must lie inside the enclosing
git root of `cwd` (or, outside a git repo, the working directory itself), and no path component may
be `cc-sessions`. Symlinks resolving outside the root are rejected, and an entry is looked up by its
realpath, so `../x` tricks and worktree copies (different realpath) each get their own entry. Session
scratch scripts under `cc-sessions/` are one-offs and are excluded from auto-add and never matched.

### B7. Hash mismatch
The entry does not match; the command falls to Tier 2/3 as a normal fire, and the user sees on
stderr: `[security review] script <path> changed since it was allowlisted (sha256 <old8> -> <new8>);
re-reviewing`. Telemetry verdict for that fire is recorded as usual; a marker in
`hook_invocations` (`cache_source="script-allowlist-mismatch"`) makes changed scripts queryable.
If the re-review is `safe` and the auto-add conditions hold, the entry is rewritten with the new
hash; if it is not `safe`, the entry is removed. A missing script never matches.

### B8. Interaction with the command cache
Independent. The command cache keys on command text; the allowlist keys on script identity. Both
can hit; the allowlist is checked first because it is broader and cheaper (no normalisation). Tier 3
`safe` verdicts continue to be cached as today. `update-command-cache` skill is unaffected.

### Observation about Tier 0
`python3` is on the Tier 0 trivial list, so `python3 <script> ...` under 120 characters with no shell
metacharacters is already allowed with no review at all, whatever the script does. That is an
existing trust decision and is out of scope here, but it means the allowlist only changes behaviour
for the long or metacharacter-bearing invocations - which are exactly the ones observed firing.
Worth revisiting separately.

## Risks / Trade-offs
- A hash covers the entry script only; a modified sibling module it imports is not detected.
- Auto-add trusts one `safe` verdict from a model. Mitigations: heuristic exclusion, project-only
  scripts, content-based review, `verify`/`remove` commands, and hash pinning.
- Reading and hashing the script adds a small filesystem cost per fire; negligible next to a
  `claude -p` call.
- Change A's measurement may show that little can be dropped without breaking auth; B alone
  still removes the repeated-script fires, so the two are independently shippable.
