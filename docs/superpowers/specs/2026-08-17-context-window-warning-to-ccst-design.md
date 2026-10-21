# Context-window-warning hook + context-override skill: move from CCCS into CCST

Status: proposed, 2026-08-17, session `20260817-context-warnings-to-ccst`.

## Problem

The Stop hook that nudges toward `/compact` when the context window grows large, and the
`/context-override` skill that silences it, currently live in `claude-code-config-sync` (CCCS) as
bash + a flag file:

- `hooks/stop/context-window-warning.sh` (registered in `~/.claude/settings.json`'s `Stop` array as
  a raw path, `timeout: 5`, `statusMessage: "Checking context-window size..."`)
- `hooks/lib/context-tokens.sh` — reads the transcript, prints token count + model id
- `hooks/lib/model-info.sh` — model id → context window size / input price / display name lookup
  tables
- `skills/context-override/SKILL.md` + `skills/context-override/scripts/context-override.sh` — toggles
  a per-session flag file at `~/.claude/context-overrides/<session_id>`

CCST (`cc-session-tools`) already has a mature Python hook framework (`cccs_hooks.*`, dispatched via
`ccst hooks run <name>`, registered through `config/hooks-bundle.json` and merged into
`settings.json` by `ccst hooks install` / `ccst install-everything --apply`) and its own bundled
skills (symlinked into `~/.claude/skills/` by `ccst skills install`). This hook and skill are
general-purpose CCST-shaped concerns (not personal/config-sync concerns), so they belong there.

**Goal: relocate, do not redesign.** Same two thresholds (150k orange, 200k red), same message
wording, same override semantics, same skill triggers — reimplemented in Python inside CCST's
existing frameworks instead of bash inside CCCS.

## Non-goals

- Any change to the warning thresholds, message wording, or override behaviour as experienced by
  the user.
- Any change to `claude_code_usage.pricing` (the telemetry-cost-accounting pricing table) — it is a
  different data source serving a different purpose (precise per-token rates for cost reports, not
  an approximate cache-read heuristic for a nudge message) and is not reused here (§2).
- Removing the CCCS files from that repo — that is requested via a separate cross-session message
  (already sent, id `20260817T212309Z-5428`, asking CCCS to hold the removal until this ships) and
  is out of CCST's control.
- Editing CCCS's own `settings.json` entry from this session — see §8's transitional-overlap note.

## Design

### 1. New hook module and dispatch verb

New module `src/cccs_hooks/context_window_warning.py`, dispatched as `ccst hooks run
context-window-warning`. Registered like every other hook:

- `lib/hook_registry.py`: `HOOK_VERBS["context-window-warning"] = "cccs_hooks.context_window_warning"`,
  plus a `HOOK_DESCRIPTIONS` entry: `"Nudges toward /compact when the context window passes
  150k/200k tokens; silenced for the session by /context-override (Stop)"`.
- `config/hooks-bundle.json`: add to the existing `Stop` block's `hooks` array, **inserted first**
  (ahead of `after-response`), preserving today's effective firing order:

  ```json
  {
    "type": "command",
    "command": "ccst hooks run context-window-warning",
    "timeout": 5,
    "statusMessage": "Checking context-window size..."
  }
  ```

`main(argv=None) -> int` reads stdin JSON (`transcript_path`, `stop_hook_active`, `session_id`),
applies the same loop guard / no-transcript / threshold logic as the bash version, and on a
threshold hit prints `{"decision": "block", "reason": "..."}` to stdout via `json.dumps` — the same
Stop-hook JSON-decision protocol the bash script already uses. Always exits 0 (a parse/read error on
the transcript is a *hook* problem, but the shell version's `exit 1` on that path is worth keeping:
port it as-is so a genuinely broken transcript is still surfaced on stderr, non-blocking).

### 2. Model-info table: ported literally, not merged into `claude_code_usage.pricing`

New pure module `src/cccs_hooks/model_info.py`, with the three lookup functions ported verbatim from
`hooks/lib/model-info.sh`'s case tables (same model-id patterns, same fallback row — 200000 tokens /
$5.00 per MTok / `"an unrecognized model"`):

```python
def context_window(model_id: str) -> int: ...
def input_price_per_mtok(model_id: str) -> float: ...
def display_name(model_id: str) -> str: ...
```

`claude_code_usage.pricing.lookup()` was considered and rejected as the source for the price figure:
it has no `context_window` field at all (a second table would still be needed), its per-token rates
are precise litellm-sourced figures rather than this hook's flat "cache-read ≈ 0.1× standard input"
approximation, and drifting the *approximation* onto a *precise* table would be an actual behaviour
change, which is the one thing this move must not be. Re-check this table whenever a new model
ships, exactly as the bash version's header comment already instructs — carry that comment over.

### 3. Token counting: a small local function, not `cccs_hooks.transcript`

`cccs_hooks.transcript.Turn` (used by `confirm_8digit` and `worklog_guard`) does not carry `usage`
data and is shared by unrelated callers — extending it would bloat a dataclass other hooks depend on
for an unrelated need. Port `hooks/lib/context-tokens.sh`'s logic directly as a local function in
`context_window_warning.py`:

```python
def _current_context_tokens(transcript_path: Path) -> tuple[int, str]:
    """Sum of input_tokens + cache_creation_input_tokens + cache_read_input_tokens from the
    last non-sidechain assistant message's usage, plus the model id that reported it.
    (0, "") for a fresh session with no assistant usage yet - not an error."""
```

Same behaviour as the bash version: skip `isSidechain` turns, tolerate blank/malformed lines,
raise (caught by `main`, printed to stderr, hook exits 1) only when the transcript path is missing
or unreadable, or has content but zero parseable JSON lines.

### 4. Arithmetic: port bash's integer/truncating semantics exactly, not Python's rounding defaults

Three formulas must reproduce the bash version bit-for-bit, because naive Python equivalents round
differently:

- `K = (tokens + 500) // 1000` — integer floor division, matches `$(( (TOKENS + 500) / 1000 ))`.
- `PCT = (tokens * 100 + window // 2) // window` — integer floor division with the round-to-nearest
  offset baked in, matches `$(( (TOKENS * 100 + WINDOW / 2) / WINDOW ))`. Do not use `round()`.
- **Cost: truncate to 2dp, do not round.** `bc`'s `scale=2` *truncates* toward zero; Python's
  `f"{x:.2f}"` *rounds*. `cost = math.floor(tokens / 1_000_000 * price * 0.1 * 100) / 100`, formatted
  `f"{cost:.2f}"` (the floor already landed on a 2dp-safe value, so formatting is now cosmetic).
  **Golden test case, taken directly from the warning the user pasted at the top of this session:**
  `tokens=155000, price=3.00 (Sonnet 5)` → `0.155 * 3.00 * 0.1 = 0.0465` → bc truncates to `0.04`,
  matching the observed `~$0.04/turn`. A naive `f"{0.0465:.2f}"` risks `"0.05"` depending on float
  representation — this is exactly the kind of silent drift this move must not introduce, hence a
  dedicated test on this exact input (Task 8 in the plan).

### 5. Override store: `context_overrides` table in the shared `sessions.db`, not a flag file

Per `.claude/CLAUDE.md`'s data-store conventions ("Any new Chris-added data store in this repo... must
use SQLite (WAL mode)... Ship a corresponding ccst query subcommand"), the flag-file store
(`~/.claude/context-overrides/<session_id>`) becomes a table in the already-shared `sessions.db`,
mirroring `doctor_mutes`'s established shape and degrade-to-safe-default pattern:

```sql
CREATE TABLE IF NOT EXISTS context_overrides (
    session_id TEXT PRIMARY KEY,
    state      TEXT NOT NULL,      -- 'on' | 'off'
    updated_at TEXT NOT NULL
);
```

New module `src/cc_session_tools/lib/context_overrides.py`:

```python
def get_override(session_id: str, *, path: Path | None = None) -> bool:
    """True iff the session's override is 'on'. Degrades to False (warnings stay ON) for every
    unusable-store case - no file, pre-migration db, corrupt file - matching doctor_mutes's
    graceful-degradation policy: the hook fires on every Stop event in every session, so it must
    never be the thing that crashes someone's unrelated turn."""

def set_override(session_id: str, state: str, *, path: Path | None = None) -> None: ...
```

Semantics unchanged from the bash version: state is `"on"`/`"off"`, keyed by
`$CLAUDE_CODE_SESSION_ID`, does not persist across sessions, `/compact` does not clear it.

### 6. `ccst context-override [on|off|status]` replaces the skill's standalone script

A new top-level `ccst` noun (bare noun + optional positional action, default `on` — same shape as
`ccst install-everything [--apply]`), reading `CLAUDE_CODE_SESSION_ID` from the environment and
erroring (rc 1) if unset, exactly as `context-override.sh` does today. This is both the "ship a ccst
query subcommand" requirement and the mechanism itself — there is no separate script to duplicate
the logic in.

`src/cc_session_tools/skills/context-override/SKILL.md` is ported with **no `scripts/` directory**:
same `name`/`description` frontmatter, same triggers, same "How it works" explanation (updated to
name `sessions.db` instead of the flag file), and the "Steps" section's invocation line changes from

```
~/.claude/skills/context-override/scripts/context-override.sh <action>
```

to

```
ccst context-override <action>
```

Installed the same way every other CCST skill is: `ccst skills install` symlinks
`~/.claude/skills/context-override` into the installed package tree.

### 7. Ship-time filesystem step: the existing `~/.claude/skills/context-override` is a real directory

Confirmed (`ls -la ~/.claude/skills/`): `context-override` is `drwx------` — a real directory CCCS's
own sync copies files into — not a symlink like every CCST-installed skill
(`generate-8digit-code -> .../site-packages/cc_session_tools/skills/generate-8digit-code`).
`_cmd_skills_install` refuses (rc 1, no write) when the destination exists as a real directory and
`--force` is absent, and auto-apply never passes `--force` (by design — it must not move a user's
real directory aside behind their back). **The ship step for this change must therefore `rm -rf
~/.claude/skills/context-override` before running `ccst skills install` / `ccst install-everything
--apply`.** This is independent of whether CCCS's own repo-side deletion (§9) has landed — it only
touches the local `~/.claude` runtime state, and it is safe to do immediately once CCST's own
symlink is ready to take its place.

### 8. Transitional double-fire is expected and self-resolving

`hooks_install.merge_hook_settings`/`prune_stale_hooks` only ever add or remove
`"ccst hooks run <name>"`-prefixed entries; CCCS's raw-path entry
(`$HOME/repos/claude-code-config-sync/hooks/stop/context-window-warning.sh`) is untouched by design
(`hooks_install.py`'s own docstring: "must never disturb hook entries CCST does not own"). So
immediately after this ships and `ccst install-everything --apply` runs, `settings.json`'s `Stop`
array will carry **both** the old CCCS entry and the new `ccst hooks run context-window-warning`
entry — the warning fires twice per Stop event until the CCCS-side session (already messaged,
§"Non-goals") removes its entry. This session does not hand-edit CCCS's entry to close that window
early — that would fight the ownership boundary `hooks_install.py` is built around and could
conflict with CCCS's own drift-checker. The window closes on its own once the CCCS-side message is
actioned.

### 9. Version bump — flagged for your review, not decided here

You asked for **2.5.1** (patch). Per `.claude/CLAUDE.md`'s own version policy this adds a new hook
verb and a new top-level CLI subcommand (`ccst context-override`) — "new features, new CLI
flags/subcommands, additive configuration" is explicitly the **minor** bump criterion, which would
make this **2.6.0**. It is not a major bump either way: no on-disk data is relocated or made
unreadable (the new `context_overrides` table is purely additive, created via the same
create-if-missing DDL `sessions.db`'s other tables already use). Recommendation: **2.6.0**, per the
documented policy, but proceeding with your explicit 2.5.1 if you confirm you want the patch number
regardless.

## Documents and docstrings to update

- `README.md`
  - `## Bundled skills` (~L425): new `### context-override` entry; the "seven skills" count is
    already stale for unrelated reasons (pre-existing drift, out of scope) so not corrected here.
  - `## Hook library (cccs_hooks)` (~L578): new row in the module table
    (`cccs_hooks.context_window_warning` / `Stop` / one-line description) and new row in the
    `ccst hooks run <name>` verb table (`context-window-warning` / `cccs_hooks.context_window_warning`).
    New row in the module table for `cccs_hooks.model_info` (`—` event, "model id → context window /
    price / display name lookup").
  - `## Hook management CLI (ccst)` (~L721): document `ccst context-override [on|off|status]`.
- `src/cc_session_tools/cli/ccst.py` module docstring: append `context-window-warning` to the
  "Available hooks" list; add a `context-override` line to "Current subcommands".
- `CHANGELOG.md`: new entry under the chosen version (§9) — Added: `context-window-warning` hook,
  `context-override` skill + CLI subcommand, `context_overrides` sessions.db table. Removed (from
  the user's perspective, once CCCS lands its side): nothing — behaviour is identical, only the
  implementation moved.
- `.claude/CLAUDE.md` (this repo's own): no change needed — the data-store-conventions section
  already generically covers this; no repo-specific instruction currently names the old CCCS
  location.

## Testing

- `tests/test_model_info.py` — every case row of `context_window`/`input_price_per_mtok`/
  `display_name`, plus the fallback row for an unrecognised model id.
- `tests/test_context_window_warning.py`
  - `_current_context_tokens`: fresh session (0, ""), normal case, `isSidechain` turns skipped,
    missing transcript (raises), corrupt transcript with zero parseable lines (raises), blank lines
    tolerated.
  - Arithmetic: the §4 golden case (`tokens=155000`, `price=3.00` → `"0.04"`) plus one case each side
    of a truncation boundary (e.g. a cost that would round up under naive `.2f` but must truncate
    down) and the `PCT`/`K` integer-arithmetic formulas against hand-computed values.
  - `main()`: below `THRESHOLD_ORANGE` → no output, rc 0; orange band → 🟠 message, `"decision":
    "block"`; red band → 🔴 message; `stop_hook_active=true` → rc 0, no output (loop guard); override
    `on` → rc 0, no output even above threshold; missing `transcript_path` → rc 0, no output (nothing
    actionable); unreadable transcript path → stderr message, rc 1.
- `tests/test_context_overrides.py` — round-trip `set_override`/`get_override` for `on`/`off`;
  `get_override` on a never-written session → `False` (default: warnings stay on); on a missing db
  file → `False`; on a corrupt db file → `False` (mirrors `doctor_mutes`'s tests exactly).
- `ccst context-override` CLI tests (in `tests/test_ccst_cli.py` or a new
  `tests/test_ccst_context_override.py`): `on`/`off`/`status` each produce the right store state and
  the right stdout confirmation line; unset `CLAUDE_CODE_SESSION_ID` → stderr message, rc 1; unknown
  action → stderr message, rc 1 (matching the bash script's `case … *)` fallback).
- `tests/test_hooks_install.py` / bundle-consistency tests: `context-window-warning` appears in both
  `HOOK_VERBS` and the `Stop` block of `config/hooks-bundle.json`, and `hooks_install.py`'s existing
  "every bundle command name is in HOOK_VERBS" invariant (if such a test already exists) still holds.
