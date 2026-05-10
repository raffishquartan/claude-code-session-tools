# cc-session-tools design

## Overview

`cc-session-tools` is a Python package plus three bundled Claude Code skills covering
two concerns: (1) session management - starting, resuming, finding, and relocating
Claude Code session directories; (2) usage analytics - parsing session JSONL files to
produce token and dollar breakdowns by project, session, model, MCP server, and tool.

Both concerns live in this repo because they both operate on Claude Code session data.
Keeping them together minimises the number of repos that `claude-code-config-sync`
(CCCS) must track as externally-managed skill sources.

Design ethos:

- **No fallbacks.** If an environment variable is missing or wrong, the CLI errors
  immediately with a precise, actionable message. Silent degradation (returning empty
  lists, skipping bad config) hides misconfiguration until the user is confused.
- **Thin CLIs, thick lib.** Business logic lives in `lib/`; the CLI scripts are wiring.
  The bundled skills call the CLIs, not the lib directly - keeping the AI-to-shell
  interface narrow and auditable.
- **Stable error markers.** The string `[CST-ROOTS-CONFIG-ERROR]` is a first-class
  part of the public CLI contract, not an implementation detail. Skills and wrappers
  pattern-match on it so they can give useful diagnosis without parsing natural language.

---

## Components

### `src/cc_session_tools/cli/`

**`ccd.py`** - Start a new Claude Code session. Validates the current working
directory against the configured session roots, checks the proposed tag suffix
against naming rules (and against the project name for strict roots), then
launches `claude` with the session display name already set. Includes Levenshtein
fuzzy-matching to catch likely typos in the tag before committing.

**`ccr.py`** - Resume an existing Claude Code session by fuzzy-matching a fragment
against session basenames found in `cc-sessions/` under the configured roots. Rewrites
the session directory name to encode the resumed-on date (`<start>-to-<today>-<tag>`)
and calls `claude --resume` on the matched JSONL.

**`ccs.py`** - Search Claude Code sessions by basename or file contents. Supports
local search (current directory's `cc-sessions/`) and global search (all projects
under the configured roots). Content search shells out to `ripgrep` when available,
falls back to threaded Python `grep`. Exit code 0 on match, 1 on no match or config
error.

### `src/cc_session_tools/lib/`

**`roots.py`** - Env-var-driven session root discovery. Defines `REPO_ROOT_ENV` and
`PROJ_ROOT_ENV`, the `RootsConfigError` exception, and `load_session_roots()` which
raises rather than silently degrading when configuration is absent or invalid. Also
provides `repo_root()`, `proj_root()`, `is_strict_root()`, `matched_session_root()`,
and `is_valid_session_cwd()` helpers used by the rules layer.

**`rules.py`** - Session naming rules and validation. `check_session_init()` and
`check_session_destination()` validate (cwd, tag) pairs against the configured roots,
strict-root naming conventions, and tag format constraints. Re-exports the full public
API from `roots.py` plus `RootsConfigError` so callers can import from one place.

**`sessions.py`** - Session directory enumeration and content search. Provides
`iter_sessions()` (sorted by start date), `session_start_date()`, `grep_session()`,
`enumerate_session_files()`, and `grep_files()`.

**`tasklist.py`** - Task list ID derivation. `id_for_project()` returns the Claude
Code task-list ID for a given project directory by matching it against the configured
roots - used so `ccd` can set `CLAUDE_CODE_TASK_LIST_ID` before launching Claude.

**`levenshtein.py`** - Pure-Python Levenshtein distance. Used by `ccd` to detect
likely tag typos and by `ccr` to fuzzy-match session basename fragments.

**`prompts.py`** - Text prompts shown to the user when validation fails. Separates
the prompt text from the validation logic so wording can be tuned without touching
the rules.

### `src/claude_code_usage/`

**`parser.py`** - Parses `~/.claude/projects/**/*.jsonl` session logs into a Pandas
DataFrame. mtime-keyed Parquet cache means only changed JSONL files are re-parsed on
subsequent runs.

**`attribution.py`** - Token attribution. When one assistant message contains N
`tool_use` blocks its tokens are split evenly across all N tools, giving per-tool
token and cost totals.

**`query.py`** - Multi-dimensional filtering and grouping. Accepts `--since/--until`,
`--project`, `--session`, `--model`, `--mcp`, `--tool` and group-by combinations
(`project, session, model, mcp, plugin, tool, day, week, month, year`).

**`report.py`** - Formats query results as terminal tables and reconciles dollar
totals against `ccusage` (external tool) for cross-validation.

**`pricing.py`** - Per-token pricing data. Loads `data/pricing.json` (shipped with
the package, resolved via `__file__`-relative path). Falls back to family-match for
unknown model IDs. Can refresh from LiteLLM upstream; treats the local cache as fresh
for 7 days.

**`schema.py`** - JSONL schema validation. Validates each parsed record against an
expected schema; unknown fields warn, missing required fields fail loudly.

**`cache.py`** - Parquet cache management. mtime-keyed so incremental parses are
sub-second on a warm cache.

**`ccusage_wrapper.py`** - Thin wrapper around the `ccusage` CLI for dollar-total
reconciliation.

**`session_names.py`** - Maps JSONL file paths to session display names.

**`parent_inference.py`** - Infers project context from session paths.

**`cli.py`** - Entry point for the `claude-code-usage` command. Parses flags,
calls query/report pipeline.

### `data/pricing.json`

Seed pricing data for the `pricing.py` module. Updated lazily from LiteLLM upstream
(7-day TTL on the cache). Resolved at runtime via `__file__`-relative path so it
works from any working directory.

### `skills/find-claude-code-session/`

A Claude Code skill that wraps the `ccs` CLI. When the user asks to find a prior
session by name or content, the skill constructs a `ccs` invocation, escalates from
local to global search if needed, and formats the results as `ccr <fragment>`
commands the user can paste. It pattern-matches `[CST-ROOTS-CONFIG-ERROR]` on stderr
to surface env-var diagnosis rather than a generic failure message.

Dependency on the CLI: `ccs` must be on `PATH`. The skill does not import any Python
library directly.

### `skills/move-session/`

A Claude Code skill that moves, renames, or move+renames a session while keeping the
JSONL transcript resumable. It:

1. Validates the source session and destination using `check_session_init()` /
   `check_session_destination()` from `cc_session_tools.lib.rules`.
2. Copies the session directory tree.
3. Rewrites JSONL `cwd` fields to the destination path.
4. Appends a tombstone record to the source JSONL so `claude --resume` on the old
   session explains where it went.
5. Generates a cleanup script for the user to run after verifying the move.

It imports the lib via `scripts/cc_session_rules.py`, a thin shim that re-exports the
public API. The shim preserves the `python3 cc_session_rules.py check-init ...`
command-line interface used by `ccd`'s bash wrapper.

A `hooks/sessionstart-pending-rename.sh` hook detects sessions that were started with
a placeholder tag and prompts the user to rename on resume.

### `skills/claude-usage/`

A Claude Code skill that wraps the `claude-code-usage` CLI. When the user asks
about their Claude Code usage (token spend, costs, MCP usage, tool breakdown),
the skill constructs a `claude-code-usage` invocation, runs it, and summarises
the result in plain English without exposing raw tables unless asked. It reconciles
dollar totals against `ccusage` for cross-validation.

Dependency on the CLI: `claude-code-usage` must be on `PATH` (satisfied when
`cc-session-tools` is installed as a package).

---

## Dependencies

### External tools (not Python packages)

- **ripgrep (`rg`)**: Optional. `ccs --contents` prefers `rg` for performance;
  falls back to threaded Python `grep` when `rg` is not on `PATH`.
- **bats**: Test-only. Used for `skills/move-session/tests/test_hook.bats`.
- **ccusage**: Optional, used by `claude_code_usage.ccusage_wrapper` for
  dollar-total cross-validation. If not on `PATH`, the reconciliation step is
  skipped gracefully.

### Python packages

Declared in `pyproject.toml` under `[project.dependencies]` (required by
`claude_code_usage`; `cc_session_tools` itself uses only stdlib):

- **pandas** ≥ 2.2 - DataFrame-based query and grouping
- **pyarrow** ≥ 15.0 - Parquet cache backend
- **jsonschema** ≥ 4.21 - JSONL schema validation
- **platformdirs** ≥ 4.0 - OS-appropriate cache directory for the Parquet store
- **httpx** ≥ 0.27 - LiteLLM pricing refresh

### Downstream dependents

This repo is depended on by:

- **`find-claude-code-session`, `move-session`, and `claude-usage` skills** -
  deployed as symlinks from `~/.claude/skills/<name>/` into this repo's
  `skills/<name>/`. Registered as externally-managed in
  `~/repos/claude-code-config-sync/externally-managed-skills.yaml` so the CCCS
  drift hook skips them.
- **`ccd`, `ccr`, `ms` bash wrappers** in `~/.bashrc` - thin wrappers around the
  installed Python entry points that also export the `CLAUDE_SESSION_TOOLS_*_ROOT`
  env vars before delegating to the Python CLI.

---

## Env-var contract

> **This section is load-bearing. All callers (skills, bash wrappers, CI) depend
> on the behaviour described here.**

### `CLAUDE_SESSION_TOOLS_REPO_ROOT`

The "loose" session root. Projects in direct subdirectories of this path can use any
valid tag (no project-name prefix required). Typical value: `$HOME/repos`.

### `CLAUDE_SESSION_TOOLS_PROJ_ROOT`

The "strict" session root. Projects in direct subdirectories of this path must use
a tag of the form `<project>-<descriptor>`, where `<project>` is the directory name
(must match `^[a-z0-9]+$`) and `<descriptor>` is a non-empty alphanumeric label.
Typical value: `$HOME/cc-claude-code`.

### Mandatory for `--global` operations and new-session validation

Both env vars are optional individually (you can set only one or both), but at least
one must be set for any operation that needs to locate or validate a session root.
If both are unset, or if any set var points to a non-existent path or a file rather
than a directory, `load_session_roots()` raises `RootsConfigError` immediately.

**There is intentionally no default fallback.** The env vars are the contract, and
the contract surfaces clearly when violated. This prevents silent misconfiguration
where operations appear to succeed but search an empty universe.

### `[CST-ROOTS-CONFIG-ERROR]` - public CLI contract

When `load_session_roots()` raises, the exception message always starts with the
literal token `[CST-ROOTS-CONFIG-ERROR]`. This token is part of the **public CLI
contract**: downstream skills and wrappers are expected to pattern-match on it.

- Skills should relay the full error message verbatim - it is self-explanatory.
- If a skill or wrapper sees a non-zero exit from `ccs`/`ccd`/`ccr` where stderr
  does **not** contain `[CST-ROOTS-CONFIG-ERROR]`, it should treat the error as a
  CLI version skew and prompt the user to check `ccs --version` and update the skill.

### Shell inheritance

Both env vars are typically exported in `~/.bashrc`. Any shell or process that wants
to use `ccd`/`ccr`/`ccs`/`ms` must inherit them from its parent process. Non-interactive
shells (such as Claude Code's Bash tool, which does not source `~/.bashrc`) inherit
them only if Claude Code itself was launched from a shell that had them exported.

---

## Deployment model

Skills are deployed as **symlinks**:

```
~/.claude/skills/find-claude-code-session/ -> ~/repos/claude-code-session-tools/skills/find-claude-code-session/
~/.claude/skills/move-session/             -> ~/repos/claude-code-session-tools/skills/move-session/
~/.claude/skills/claude-usage/             -> ~/repos/claude-code-session-tools/skills/claude-usage/
```

Changes to skill files take effect immediately (symlink - no deploy step needed).

The symlinks are maintained externally: CCCS (`claude-code-config-sync`) tracks them
in `externally-managed-skills.yaml`. The CCCS drift hook skips these entries during
its normal sync check, so changes are never overwritten by a drift-sync operation.

The Python package is installed non-editably for production use (`uv tool install`),
meaning the source is copied into the tool venv at install time - no fragile `.pth`
pointer that breaks when a git worktree is deleted. For local development, `uv sync
--extra dev` installs editably into a project venv so source changes take effect
immediately without reinstalling.

---

## Test layout

```
tests/                          lib + claude_code_usage tests (pytest tests/)
  conftest.py                   autouse fixture: clears CLAUDE_SESSION_TOOLS_*_ROOT
  test_roots.py                 unit tests for lib/roots.py (including RootsConfigError)
  test_rules.py                 unit tests for lib/rules.py
  test_sessions.py              unit tests for lib/sessions.py
  test_cli_ccd.py               integration tests for ccd CLI
  test_cli_ccr.py               integration tests for ccr CLI
  test_cli_ccs.py               integration tests for ccs CLI
  test_cli_version.py           --version flag tests
  test_levenshtein.py           unit tests for lib/levenshtein.py
  test_prompts.py               unit tests for lib/prompts.py
  test_tasklist.py              unit tests for lib/tasklist.py
  test_attribution.py           unit tests for claude_code_usage/attribution.py
  test_cache.py                 unit tests for claude_code_usage/cache.py
  test_ccusage_wrapper.py       unit tests for claude_code_usage/ccusage_wrapper.py
  test_cli.py                   integration tests for claude-code-usage CLI
  test_parent_inference.py      unit tests for claude_code_usage/parent_inference.py
  test_parser.py                unit tests for claude_code_usage/parser.py
  test_pricing.py               unit tests for claude_code_usage/pricing.py
  test_query.py                 unit tests for claude_code_usage/query.py
  test_report.py                unit tests for claude_code_usage/report.py
  test_schema.py                unit tests for claude_code_usage/schema.py
  test_session_names.py         unit tests for claude_code_usage/session_names.py

skills/move-session/tests/      move-session skill tests (88 tests)
  conftest.py                   fixtures: tmp_home, roots_file (env-var driven), make_session
  test_detection.py             in-session detection, dry-run flag, CLAUDECODE env var
  test_hook.bats                bash hook tests (requires bats)
  test_integration.py           end-to-end subprocess tests of move_session.py
  test_jsonl.py                 JSONL rewriting and tombstone logic
  test_validators.py            validator functions re-exported via cc_session_rules shim
```

Top-level `pytest` (from the repo root) discovers both suites via `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests", "skills/move-session/tests"]
pythonpath = ["src"]
```

The move-session tests ensure the local `cc_session_tools` is used (not a stale
installed version) by prepending the repo `src/` to `sys.path` in `conftest.py` and
`PYTHONPATH` in the subprocess helper `_run()`.
