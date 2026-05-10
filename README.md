# claude-code-session-tools

Two concerns, one repo, for life on the [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI:

1. **Session management** - start, resume, find, and relocate Claude Code sessions from the shell, with tagged dated session directories that don't pollute your repo root.
2. **Usage analytics** - parse `~/.claude/projects/**/*.jsonl` into tokens-and-dollars breakdowns by project, session, model, MCP server, plugin, and tool.

The repo ships four CLIs and three bundled skills:

| | What it does |
|---|---|
| **`ccd <tag>`** | Start a new session with a pre-created `cc-sessions/<date>-<tag>/` directory and a tagged display name. |
| **`ccr <fragment>`** | Resume an existing session by typing any substring of its name. |
| **`ccs <query>`** | Search across your sessions by name (default), or by file contents (`--contents`), in the current project (default) or across every configured root (`--global`). |
| **`claude-code-usage`** | Multi-dimensional usage analytics CLI: query/group/filter by project, session, model, MCP server, plugin, tool, day/week/month/year. Reconciles dollar totals against `ccusage`. |
| Skill: **`find-claude-code-session`** | Wraps `ccs`. Lets a Claude Code session locate one of your prior sessions by name or content and offer a `ccr` command to resume it. |
| Skill: **`move-session`** | Move, rename, or move+rename a session while keeping its `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` transcript resumable. |
| Skill: **`claude-usage`** | Wraps `claude-code-usage`. Lets a Claude Code session answer "how much have I spent on Opus this month?" without you typing the CLI yourself. |

If you've ever tried to remember which `1f4a8b3c-...` UUID is the session where you were debugging that flaky test last Tuesday, or wondered which project burned through last week's Opus budget, this is for you.

## Why bother?

Claude Code stores each session as a `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` transcript and exposes them through `claude --resume`. That works, but the picker shows untagged sessions in opaque order, the working files for a session sprawl into your repo root, and there's no built-in way to grep across past conversations or see where your tokens went.

These tools add:

1. **A tagged, dated session directory** under `<project>/cc-sessions/<YYYYMMDD>-<tag>/` with `working/` and `out/` subdirs - the convention Claude Code's [session memory](https://docs.anthropic.com/en/docs/claude-code/memory) hooks expect when you want scratch space and deliverables that don't pollute your repo.
2. **Resume-by-fragment** so you can type `ccr flaky` instead of scrolling through the picker.
3. **Cross-session search** so `ccs --contents --global "GraphQL retry"` finds every conversation that mentioned it.
4. **Usage analytics** so `claude-code-usage query --since 2026-04-01 --group-by project,model` answers where the spend went.
5. **Skill wrappers** so the Claude Code agent can do all of the above on your behalf when you ask in natural language.

## Installation

### Prerequisites

- **Python 3.10+**
- **The `claude` CLI on your `$PATH`.** Install it first via [the official Claude Code instructions](https://docs.anthropic.com/en/docs/claude-code/setup) and verify with `claude --version`.
- **`ccusage` (optional)** - if on `$PATH`, `claude-code-usage reconcile` cross-checks dollar totals against it. Skipped gracefully if missing.
- **`ripgrep` (optional)** - `ccs --contents` prefers `rg`; falls back to threaded Python `grep` if missing.

### Install the tools

For end users, [`pipx`](https://pipx.pypa.io/) is the cleanest option - it installs the four commands into an isolated venv and drops them on your `$PATH`:

```sh
pipx install git+https://github.com/raffishquartan/claude-code-session-tools.git
```

Or for a hackable, editable install in a clone:

```sh
git clone https://github.com/raffishquartan/claude-code-session-tools.git
cd claude-code-session-tools
pip install -e .
```

Either way, `ccd`, `ccr`, `ccs`, and `claude-code-usage` will be available on your `$PATH`. Verify:

```sh
ccd --version
claude-code-usage --version
```

### Install the skills (optional)

Each skill is a self-contained directory under `skills/`. To make them visible to Claude Code, symlink them into `~/.claude/skills/`:

```sh
ln -s "$PWD/skills/find-claude-code-session" ~/.claude/skills/find-claude-code-session
ln -s "$PWD/skills/move-session"             ~/.claude/skills/move-session
ln -s "$PWD/skills/claude-usage"             ~/.claude/skills/claude-usage
```

The skills shell out to the installed CLIs - they don't import the Python library directly, so the only requirement is that `ccs` / `claude-code-usage` are on `$PATH`.

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
- `--force` - skip the root check and any naming-convention checks (escape hatch for one-off invocations outside your roots).
- Anything after the tag is forwarded to `claude` verbatim, so `ccd my-tag --model opus` works.

### `ccr` - resume by fragment

```sh
ccr flaky        # resumes whichever session has "flaky" in its name
ccr 20260509     # resumes whichever session was started on that date
```

If multiple sessions match, `ccr` prints them and exits cleanly so you can rerun with a more specific fragment. If exactly one matches, it execs `claude --resume <basename>` with the right working directory.

### `ccs` - search

```sh
ccs flaky                       # name search in current project
ccs flaky --global              # name search across all configured roots
ccs "GraphQL retry" --contents  # full-text search of files in current project's sessions
ccs "GraphQL retry" --contents --global   # ... across all projects
```

Results are ordered newest-first by session start date. `--contents` shows one line of context around each match.

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

### `find-claude-code-session`

Wraps `ccs`. Triggers on prompts like "find my session about X", "did I work on foo before", "what session was I in when Y". Constructs the right `ccs` invocation, escalates from local to global search if the local hit list is empty, and presents results as `ccr <fragment>` commands you can paste.

### `move-session`

Moves, renames, or move+renames a session directory while keeping the JSONL transcript resumable. Triggers on "move session to", "rename my session", "this session belongs in a different folder". Dry-run by default - you must pass `--execute` for any filesystem change. Validates source and destination against the same rules as `ccd`, copies the session directory tree, rewrites JSONL `cwd` fields to the destination path, and appends a tombstone record to the source JSONL so `claude --resume` on the old session explains where it went.

### `claude-usage`

Wraps `claude-code-usage`. Triggers on usage questions: "how much have I spent on Claude Code", "tokens used this week", "which project costs the most", "Opus vs Sonnet", "any spike in usage recently". Picks the right subcommand and flags, runs it, and summarises the result in plain English.

See `docs/design.md` for the full design and CLI contract.

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
pip install -e .
pytest
```

Tests run on Python 3.10, 3.11, and 3.12 (see `.github/workflows/ci.yml`). The full architecture and module layout is in [`docs/design.md`](docs/design.md).

## Limitations and caveats

- Linux and macOS only. Windows is not tested; the tools assume POSIX paths and `os.execvpe`-style process replacement.
- The session-management CLIs shell out to `claude` via `os.execvpe`. If `claude` isn't on `$PATH`, `ccd` and `ccr` will fail with the standard "command not found" error.
- `claude-code-usage` reads from `~/.claude/projects/` and writes a Parquet cache under `~/.cache/claude-code-usage/parquet/` (overrideable via `--projects-dir` and `--cache-dir`). Pricing data is loaded from `data/pricing.json` shipped with the package, refreshed lazily from LiteLLM upstream with a 7-day TTL.
- The strict-root convention is opinionated. If you don't want it, just leave `CLAUDE_SESSION_TOOLS_PROJ_ROOT` unset.

## Licence

MIT - see [LICENSE](LICENSE).
