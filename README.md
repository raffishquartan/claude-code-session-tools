# claude-code-session-tools

Three concerns, one repo, for life on the [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI:

1. **Session management** - start, resume, find, and relocate Claude Code sessions from the shell, with tagged dated session directories that don't pollute your repo root.
2. **Usage analytics** - parse `~/.claude/projects/**/*.jsonl` into tokens-and-dollars breakdowns by project, session, model, MCP server, plugin, and tool.
3. **Hook library** - Python package (`cccs_hooks`) providing Claude Code SessionStart / PreToolUse / PostToolUse / UserPromptSubmit / Stop hook implementations, invokable via `ccst hooks run <name>`.

The repo ships five CLIs and three bundled skills:

| | What it does |
|---|---|
| **`ccd <tag>`** | Start a new session with a pre-created `cc-sessions/<date>-<tag>/` directory and a tagged display name. |
| **`ccr <fragment>`** | Resume an existing session by typing any substring of its name. |
| **`ccs <query>`** | Search across your sessions by name, file contents, or transcript messages, in the current project or across every configured root. |
| **`claude-code-usage`** | Multi-dimensional usage analytics CLI: query/group/filter by project, session, model, MCP server, plugin, tool, day/week/month/year. Reconciles dollar totals against `ccusage`. |
| **`ccst <noun> <verb>`** | Umbrella CLI for hook and skill management. `ccst hooks install` merges hook entries; `ccst hooks run <name>` runs a hook by name; `ccst skills install` symlinks bundled skills into `~/.claude/skills/`. |
| Skill: **`find-claude-code-session`** | Wraps `ccs`. Lets a Claude Code session locate one of your prior sessions by name or content and offer a `ccr` command to resume it. |
| Skill: **`move-session`** | Move, rename, or move+rename a session while keeping its `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` transcript resumable. |
| Skill: **`analyse-cc-usage`** | Wraps `claude-code-usage`. Lets a Claude Code session answer "how much have I spent on Opus this month?" without you typing the CLI yourself. |

If you've ever tried to remember which `1f4a8b3c-...` UUID is the session where you were debugging that flaky test last Tuesday, or wondered which project burned through last week's Opus budget, this is for you.

## Installation

### Prerequisites

- **Python 3.11+** (3.12+ recommended)
- **The `claude` CLI on your `$PATH`.** Install it first via [the official Claude Code instructions](https://docs.anthropic.com/en/docs/claude-code/setup) and verify with `claude --version`.
- **`ccusage` (optional)** - if on `$PATH`, `claude-code-usage reconcile` cross-checks dollar totals against it. Skipped gracefully if missing.
- **`ripgrep` (optional)** - `ccs --contents` prefers `rg`; falls back to threaded Python `grep` if missing.

### Install the tools

The simplest path is [`pipx`](https://pipx.pypa.io/), which installs the commands into
an isolated venv and puts them on your `$PATH`:

```sh
pipx install cc-session-tools
```

If you use [`uv`](https://docs.astral.sh/uv/):

```sh
uv tool install cc-session-tools
```

Either way, `ccd`, `ccr`, `ccs`, `claude-code-usage`, and `ccst` will be available on
your `$PATH`. Verify:

```sh
ccd --version
claude-code-usage --version
ccst --help
```

> **Installing from source (pre-release or offline):**
> ```sh
> git clone https://github.com/raffishquartan/claude-code-session-tools.git
> cd claude-code-session-tools
> uv tool install .
> ```

### Upgrade

```sh
# pipx
pipx upgrade cc-session-tools

# uv
uv tool upgrade cc-session-tools
```

> **Installing from a local clone:** if you keep a local clone for development or
> to stay on the latest commit, refresh the global install with:
> ```sh
> uv tool install ~/repos/claude-code-session-tools
> ```
> **Do NOT run `uv tool install` from inside a git worktree.** That overwrites the
> global install's source pointer with the worktree path, which breaks all five CLIs
> when the worktree is deleted. Use `uv run pytest` to test inside a worktree, and run
> `uv tool install ~/repos/claude-code-session-tools` from outside the worktree after
> merging.

### Install the skills

Run `ccst skills install` to symlink all bundled skills into `~/.claude/skills/`:

```sh
# Dry run (shows what would be created)
ccst skills install

# Write the symlinks
ccst skills install --apply
```

Manual symlinks still work if you prefer:

```sh
ln -s "$PWD/skills/find-claude-code-session" ~/.claude/skills/find-claude-code-session
ln -s "$PWD/skills/move-session"             ~/.claude/skills/move-session
ln -s "$PWD/skills/analyse-cc-usage"             ~/.claude/skills/analyse-cc-usage
```

The skills shell out to the installed CLIs - they don't import the Python library directly, so the only requirement is that `ccs` / `claude-code-usage` are on `$PATH`.

## Why bother?

Claude Code stores each session as a `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` transcript and exposes them through `claude --resume`. That works, but the picker shows untagged sessions in opaque order, the working files for a session sprawl into your repo root, and there's no built-in way to grep across past conversations or see where your tokens went.

These tools add:

1. **A tagged, dated session directory** under `<project>/cc-sessions/<YYYYMMDD>-<tag>/` with `working/` and `out/` subdirs - the convention Claude Code's [session memory](https://docs.anthropic.com/en/docs/claude-code/memory) hooks expect when you want scratch space and deliverables that don't pollute your repo.
2. **Resume-by-fragment** so you can type `ccr flaky` instead of scrolling through the picker.
3. **Cross-session search** so `ccs --contents --global "GraphQL retry"` finds every conversation that mentioned it.
4. **Usage analytics** so `claude-code-usage query --since 2026-04-01 --group-by project,model` answers where the spend went.
5. **Skill wrappers** so the Claude Code agent can do all of the above on your behalf when you ask in natural language.

## Configuration: where do your sessions live?

`ccd` refuses to start a session if your current working directory isn't a direct child of one of your configured **session roots**. This sounds annoying but turns out to be a feature: it stops you from accidentally starting a session in `/tmp` or in `~`, and it lets `ccr`/`ccs` find sessions across your projects without you telling them where to look.

Roots are configured via two environment variables - both optional, but you'll want at least one:

### `CLAUDE_SESSION_TOOLS_REPO_ROOT` - the **loose** root

Point this at the directory whose direct children are your projects (the typical case is `~/repos`):

```sh
export CLAUDE_SESSION_TOOLS_REPO_ROOT="$HOME/repos"
```

A "session root" means: if `$REPO_ROOT/foo/` exists, then `cd ~/repos/foo && ccd my-tag` is allowed and creates `~/repos/foo/cc-sessions/20260509-my-tag/`. Sessions started two levels deep (`~/repos/foo/sub/`) are rejected unless you pass `--force`.

Under the loose root the only naming rule is **no spaces in the tag**. Tag suffixes can be anything else: `bugfix-7`, `redesign`, `try-out-thing`.

### `CLAUDE_SESSION_TOOLS_PROJ_ROOT` - the **strict** (namespaced) root

Pointing at this is opt-in. It's useful if you keep a separate directory for "Claude Code project" workspaces (think one folder per long-running theme, like `~/cc-claude-code/migration/`, `~/cc-claude-code/oneshot/`):

```sh
export CLAUDE_SESSION_TOOLS_PROJ_ROOT="$HOME/cc-claude-code"
```

Under the strict root, two extra rules apply:

1. **Project directory names** must match `[a-z0-9]+` - lowercase, no dashes, no underscores.
2. **Tag suffixes** must start with `<project-name>-` followed by a descriptive label.

So in `~/cc-claude-code/oneshot/`, `ccd oneshot-config-cleanup` is fine but `ccd config-cleanup` is rejected with a friendly error. The strict root also enables `ccd`'s [Levenshtein typo prompt](#typo-protection-strict-root-only): if you type `oneshet-foo`, it offers to correct it to `oneshot-foo`.

You can configure either, both, or neither. With neither set, you'll need `ccd --force` to start any session, and `ccr`/`ccs` won't find anything.

### Why two roots, and which should I use?

| | `REPO_ROOT` (loose) | `PROJ_ROOT` (strict) |
|---|---|---|
| Where you point it | A directory you already use for code, e.g. `~/repos` | A purpose-built directory for Claude Code workspaces, e.g. `~/cc-claude-code` |
| Naming conventions | None beyond no-spaces | Project name `[a-z0-9]+`, tag `<project>-<label>` |
| Typo protection | Off | On (Levenshtein-checked against project name) |
| Best for | Day-to-day work in existing repos | Long-running, themed Claude Code projects you want kept tidy |

Most users only need `REPO_ROOT`. Configure `PROJ_ROOT` later if you find yourself wanting tighter conventions for a specific subset of work.

## Session management CLIs

### `ccd` - start a session

```sh
cd ~/repos/myproject
ccd bugfix-flaky-test
# Creates  ~/repos/myproject/cc-sessions/20260509-bugfix-flaky-test/
#                                            working/
#                                            out/
# And launches `claude` with -n 20260509-bugfix-flaky-test (tagged display name).
```

Useful flags:

| Flag | What it does |
|---|---|
| `--dry-run` | Print what would happen (session dir, launch command) without creating anything or launching `claude`. |
| `--force` | Skip the root check and any naming-convention checks (escape hatch for one-off invocations outside your roots). |
| `--debug` | Enable verbose debug output (`CCX_DEBUG=1`). |

Anything after the tag is forwarded to `claude` verbatim, so `ccd my-tag --model opus` works.

### `ccr` - resume by fragment

```sh
ccr flaky        # resumes whichever session has "flaky" in its name
ccr 20260509     # resumes whichever session was started on that date
```

If multiple sessions match, `ccr` shows a numbered picker (1-9/0) if stdin is a TTY and there are 10 or fewer candidates; otherwise it prints them and exits. If exactly one matches, it execs `claude --resume <basename>` with the right working directory.

Useful flags:

| Flag | What it does |
|---|---|
| `--include-orphans` | Also consider sessions whose `cc-sessions/` directory is missing or has been cleaned up (resume by transcript UUID only). |
| `--debug` | Enable verbose debug output (`CCX_DEBUG=1`). |

### `ccs` - search

```sh
ccs flaky                              # name search in current project
ccs flaky --global                     # name search across all configured roots
ccs "GraphQL retry" --contents        # full-text search of working/out files
ccs "GraphQL retry" --messages        # full-text search of JSONL transcripts
ccs "GraphQL retry" --contents --messages --global  # combined, all projects
ccs flaky --since 2026-04-01          # only sessions from April 2026 onwards
ccs flaky --sort newest               # explicit sort (default)
ccs flaky --sort oldest
```

Results are ordered newest-first by session start date by default. `--contents` shows one line of context around each match; `--messages` searches the Claude transcript JSONL files and surfaces matching turns.

Useful flags:

| Flag | What it does |
|---|---|
| `--name` | Search session basenames (the default; explicit opt-in). |
| `--contents` | Search text files inside each session's `working/` and `out/` directories. |
| `--messages` | Search Claude transcript JSONL files in `~/.claude/projects/`. |
| `--global` | Search across all configured roots, not just the current project. |
| `--since DATE` | Only sessions started on or after DATE. Accepts `YYYYMMDD`, `YYYY-MM-DD`, `7d` (days ago), `2w` (weeks ago), `1m` (months ago). |
| `--before YYYYMMDD` | Only sessions started before DATE. |
| `--days N` | Only sessions started within the last N days. |
| `--sort {newest,oldest}` | Sort order (default: newest). |
| `--exclude-hooks` | Hide hook-security-check sessions from results. |
| `--json` | Output results as a JSON array. |
| `--null` | Output null-delimited basenames (for `xargs -0`). |
| `--debug` | Enable verbose debug output (`CCX_DEBUG=1`). |

## Usage analytics CLI

### `claude-code-usage` - tokens and dollars by every dimension you care about

The CLI parses your `~/.claude/projects/**/*.jsonl` transcripts into a Pandas DataFrame (mtime-keyed Parquet cache means subsequent runs are fast), splits per-tool tokens evenly across `tool_use` blocks, and lets you slice the result.

Five subcommands:

| | What it does |
|---|---|
| `query` | Multi-dimensional filter + group-by, output as markdown / CSV / JSON. The workhorse. |
| `report` | Render a full multi-section markdown report (project / model / time-bucket breakdowns at once). |
| `children` | List child sessions (hook-security-review, subagent dispatches) of a given parent session. |
| `warm-cache` | Populate or refresh the Parquet cache without producing output. |
| `reconcile` | Compare our totals against [`ccusage`](https://github.com/ryoppippi/ccusage)'s authoritative figures, so we know the numbers are right. |

A few examples:

```sh
# Total spend last month, grouped by project (top 10 by cost)
claude-code-usage query --since 2026-04-01 --until 2026-04-30 --group-by project --top 10

# Where Opus tokens went this week, by session
claude-code-usage query --since 2026-05-04 --model opus --group-by session

# How often each MCP server gets used, across the last quarter
claude-code-usage query --since 2026-02-01 --group-by mcp --sort token_total

# Daily spend trend for one project
claude-code-usage query --project myproject --group-by day --format csv

# Full report of last calendar month
claude-code-usage report --since 2026-04-01 --until 2026-04-30

# Cross-validate against ccusage
claude-code-usage reconcile --since 2026-04-01
```

Run `claude-code-usage <subcommand> --help` for the full grammar. A few flags worth knowing:

- `--exclude-hooks` strips out the `bash-security-review.sh` hook sessions, which would otherwise distort per-session cost breakdowns by ~$1.60 each.
- `--include-children` (when grouping by session) folds child-session tokens and cost into the parent row.
- `--session-format {name,uuid,both}` controls how the session column renders - by display name (default), by UUID, or both.

## Bundled skills

The repo ships three Claude Code skills, designed to be symlinked into `~/.claude/skills/`. They're thin wrappers around the CLIs so a Claude Code session can invoke them on your behalf in response to natural-language prompts.

Install all three at once with `ccst skills install --apply` (see [Install the skills](#install-the-skills) above).

### `find-claude-code-session`

Wraps `ccs`. Triggers on prompts like "find my session about X", "did I work on foo before", "what session was I in when Y". Constructs the right `ccs` invocation, escalates from local to global search if the local hit list is empty, and presents results as `ccr <fragment>` commands you can paste.

### `move-session`

Moves, renames, or move+renames a session directory while keeping the JSONL transcript resumable. Triggers on "move session to", "rename my session", "this session belongs in a different folder". Dry-run by default - you must pass `--execute` for any filesystem change. Validates source and destination against the same rules as `ccd`, copies the session directory tree, rewrites JSONL `cwd` fields to the destination path, and appends a tombstone record to the source JSONL so `claude --resume` on the old session explains where it went.

### `analyse-cc-usage`

Wraps `claude-code-usage`. Triggers on usage questions: "how much have I spent on Claude Code", "tokens used this week", "which project costs the most", "Opus vs Sonnet", "any spike in usage recently". Picks the right subcommand and flags, runs it, and summarises the result in plain English.

See `docs/design.md` for the full design and CLI contract.

## Hook library (`cccs_hooks`)

The `cccs_hooks` Python package provides Claude Code hook implementations.
Install via `uv tool install cc-session-tools` or `pipx install cc-session-tools`
to make the hook library available. Hooks are invoked through `ccst hooks run <name>`.

### Modules

| Module | Hook event | What it does |
|---|---|---|
| `cccs_hooks.telemetry` | — | Writes structured JSONL to `~/.claude/hooks/fires.jsonl`; used by other modules. |
| `cccs_hooks.transcript` | — | Walks parent session transcript JSONL; shared by `confirm_8digit`. |
| `cccs_hooks.confirm_8digit` | PreToolUse | 8-digit confirmation guard for gated tools. |
| `cccs_hooks.cache` | — | SHA-256 command cache (CSV); used by `bash_security_review`. |
| `cccs_hooks.bash_security_review` | PreToolUse | Tiered Bash security review with cache. |
| `cccs_hooks.edit_write_audit` | PostToolUse | Sensitive-path + WORKLOG audit. |
| `cccs_hooks.prompt_guard` | UserPromptSubmit | Credential/injection pattern guard. |
| `cccs_hooks.session_end` | Stop | WORKLOG/uncommitted-changes nudge. |
| `cccs_hooks.session_tag` | **SessionStart** | Writes `<uuid>.tag` so `claude-code-usage` can map session UUIDs to `ccd` name tags (see [Session tag hook](#session-tag-hook)). |

### Running hooks via `ccst hooks run <name>`

Hook scripts invoke the dispatcher via `ccst` rather than calling
`python3 -m cccs_hooks.*` directly. This means CCST only needs to be installed
via `uv tool install` or `pipx install` - the hook modules do not need to be
importable by the system Python. The shim contract is:

```sh
exec ccst hooks run <name> <<< "$INPUT"
```

Where `<name>` is one of:

| Verb | Module |
|---|---|
| `bash-security-review` | `cccs_hooks.bash_security_review` |
| `confirm-8digit` | `cccs_hooks.confirm_8digit` |
| `prompt-guard` | `cccs_hooks.prompt_guard` |
| `edit-write-audit` | `cccs_hooks.edit_write_audit` |
| `session-end` | `cccs_hooks.session_end` |
| `session-tag` | `cccs_hooks.session_tag` |

The dispatcher reads the event payload from stdin, calls the matching module's
`main()`, and propagates its exit code.

### Session tag hook

`cccs_hooks.session_tag` is a **SessionStart** hook that writes a small tag file when a session is created via `ccd <tag>`:

- File written: `~/.claude/projects/<encoded-cwd>/<session_id>.tag`
- File content: the `ccd` name tag (e.g. `oneshot-add-uuid-for-better-usage-mapping`)
- If `CLD_SESSION_TAG` is not set (i.e. the session was not started by `ccd`), the hook exits silently.

Claude Code stores each session as `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`. The display name (set by `ccd` via `claude -n`) survives only in ephemeral PID files that disappear when the process exits. The `.tag` file gives `claude-code-usage` and other tools a persistent, stable mapping from UUID to human name - so `--session-format name` shows `oneshot-add-uuid-for-better-usage-mapping` instead of `sess-8f3a2c1d`.

#### Installing the session-tag hook (without CCCS)

**Option A — local clone** (simplest): use the bundled config file with `ccst hooks install`:

```sh
ccst hooks install \
  --source ~/repos/claude-code-session-tools/config/session-tag-hook.json \
  --apply
```

**Option B — PyPI/pipx install** (no local clone): save the snippet below to a temporary file and merge it in, or add it directly to `~/.claude/settings.json` by hand.

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "ccst hooks run session-tag",
            "timeout": 5,
            "statusMessage": "Writing session tag file..."
          }
        ]
      }
    ]
  }
}
```

```sh
# Save to a temp file and merge atomically:
cat > /tmp/session-tag-hook.json << 'EOF'
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "ccst hooks run session-tag",
            "timeout": 5,
            "statusMessage": "Writing session tag file..."
          }
        ]
      }
    ]
  }
}
EOF
ccst hooks install --source /tmp/session-tag-hook.json --apply
```

Either way, a single `SessionStart` entry is added to `~/.claude/settings.json`. Verify with `cat ~/.claude/settings.json | python3 -m json.tool | grep -A5 SessionStart`.

Once installed, every session started via `ccd` will automatically write a `.tag` file on startup.

#### CCCS users

If you use [claude-code-config-sync](https://github.com/raffishquartan/claude-code-config-sync), the hook is wired in automatically when you update `cc-wrapper-session-tag.sh` to pipe stdin to `ccst hooks run session-tag`. No additional `ccst hooks install` step is needed.

### Running modules directly (debugging only)

Each module is also runnable as a Python CLI if you want to bypass the dispatcher
and have `cccs_hooks` importable on `sys.path` (e.g. inside an activated venv,
or when installed via `uv tool install cc-session-tools`):

```sh
python3 -m cccs_hooks.telemetry log --help
python3 -m cccs_hooks.bash_security_review  # reads JSON from stdin
python3 -m cccs_hooks.prompt_guard          # reads JSON from stdin
```

## Hook management CLI (`ccst`)

The `ccst` umbrella CLI provides hook and skill management.

### `ccst hooks install`

Merges hook entries from a source `settings.json` into `~/.claude/settings.json`.
Matching is by event type + matcher + command string; already-present hooks are
never duplicated.

```sh
# Dry run (default) - shows what would be added
ccst hooks install \
  --source /path/to/source-settings.json \
  --target ~/.claude/settings.json

# Write the changes
ccst hooks install \
  --source /path/to/source-settings.json \
  --target ~/.claude/settings.json \
  --apply
```

The target file is written atomically (`.tmp` swap).

### `ccst hooks run <name>`

Run a Claude Code hook by name. See the table above for the supported names.

### `ccst skills install`

Symlink all bundled skills into `~/.claude/skills/`.

```sh
# Dry run (default) - shows what would be created or skipped
ccst skills install

# Write the symlinks
ccst skills install --apply

# Replace wrong-target or conflicting symlinks
ccst skills install --apply --force
```

## How it interacts with Claude Code's task lists

Claude Code lets multiple sessions share a single task list if they all set the same `CLAUDE_CODE_TASK_LIST_ID` environment variable. `ccd` and `ccr` derive this from the project layout:

- If your cwd is a direct child of a configured root (e.g. `~/repos/myproject`), the task list ID is set to the project directory name (`myproject`). All sessions started in `~/repos/myproject` share one task list.
- If your cwd is anywhere else (or both env vars are unset and you used `--force`), no task list ID is set and the session gets a private task list.

This means you can pick up a task created in yesterday's session from today's session in the same project, without any extra setup.

## Typo protection (strict root only)

When you start a session under the **strict** (`PROJ_ROOT`) root, `ccd` checks whether your tag's first dash-separated term looks like a typo of the project directory name (Levenshtein distance ≤ 2):

```sh
cd ~/cc-claude-code/oneshot
ccd oneshet-fix-bug
ccd: 'oneshet' looks like a typo of project folder 'oneshot' (Levenshtein 1).
ccd: Start session with tag 'oneshot-fix-bug' instead? [y/N]
```

A second prompt fires if your first term is far from the current project name **and** far from every sibling project under `PROJ_ROOT` - in that case `ccd` offers to prepend the current project name. This behaviour is intentionally off under the loose root.

## Sessions on disk

Each session directory looks like:

```
cc-sessions/20260509-bugfix-flaky-test/
  working/      # scratch files, notes, WORKLOG.md - whatever you want
  out/          # deliverables you might keep or hand off
```

Add `cc-sessions/` to your project's `.gitignore` if you don't want session artefacts tracked.

## Development

```sh
git clone https://github.com/raffishquartan/claude-code-session-tools.git
cd claude-code-session-tools
uv sync --extra dev
uv run pytest
```

Tests run on Python 3.11, 3.12, and 3.13 (see `.github/workflows/ci.yml`). CI also
includes an `install-check` job that runs `uv tool install .` and verifies all five CLIs
start up correctly - the direct guard against the editable-install/worktree failure mode.

> **When working in a git worktree:** test your changes with `uv run pytest` or
> `uv run python -m cc_session_tools.cli.ccd` - do not run `uv tool install` from inside
> a worktree. After merging, run `uv tool install ~/repos/claude-code-session-tools`
> (or `uv tool install cc-session-tools` if installed from PyPI) to update the global
> install.

## Limitations and caveats

- Linux and macOS only. Windows is not tested; the tools assume POSIX paths and `os.execvpe`-style process replacement.
- The session-management CLIs shell out to `claude` via `os.execvpe`. If `claude` isn't on `$PATH`, `ccd` and `ccr` will fail with the standard "command not found" error.
- `claude-code-usage` reads from `~/.claude/projects/` and writes a Parquet cache under `~/.cache/claude-code-usage/parquet/` (overrideable via `--projects-dir` and `--cache-dir`). Pricing data is loaded from `data/pricing.json` shipped with the package, refreshed lazily from LiteLLM upstream with a 7-day TTL.
- The strict-root convention is opinionated. If you don't want it, just leave `CLAUDE_SESSION_TOOLS_PROJ_ROOT` unset.

## Licence

MIT - see [LICENSE](LICENSE).
