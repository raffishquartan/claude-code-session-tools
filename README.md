# claude-code-session-tools

Three small CLIs that make long-running [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions pleasant to manage from the shell:

- **`ccd <tag>`** - Start a new session with a pre-created `cc-sessions/<date>-<tag>/` directory and a tagged display name.
- **`ccr <fragment>`** - Resume an existing session by typing any substring of its name.
- **`ccs <query>`** - Search across your sessions by name (default), or by file contents (`--contents`), in the current project (default) or across every configured root (`--global`).

If you've ever tried to remember which `1f4a8b3c-...` UUID is the session where you were debugging that flaky test last Tuesday, this is for you.

## Why bother?

Claude Code stores each session as a `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` transcript and exposes them through `claude --resume`. That works, but the picker shows untagged sessions in opaque order, the working files for a session sprawl into your repo root, and there's no built-in way to grep across past conversations.

These tools add three things on top:

1. **A tagged, dated session directory** under `<project>/cc-sessions/<YYYYMMDD>-<tag>/` with `working/` and `out/` subdirs - the convention Claude Code's [session memory](https://docs.anthropic.com/en/docs/claude-code/memory) hooks expect when you want scratch space and deliverables that don't pollute your repo.
2. **Resume-by-fragment** so you can type `ccr flaky` instead of scrolling through the picker.
3. **Cross-session search** so `ccs --contents --global "GraphQL retry"` finds every conversation that mentioned it.

## Installation

### Prerequisites

- **Python 3.10+**
- **The `claude` CLI on your `$PATH`.** Install it first via [the official Claude Code instructions](https://docs.anthropic.com/en/docs/claude-code/setup) and verify with `claude --version`.

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

Either way, `ccd`, `ccr`, `ccs`, and `claude-code-usage` will be available on your
`$PATH`. Verify:

```sh
ccd --version
```

> **Installing from source (pre-release or offline):**
> ```sh
> git clone https://github.com/raffishquartan/claude-code-session-tools.git
> cd claude-code-session-tools
> uv tool install .
> ```

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

## Usage

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

Tests run on Python 3.10, 3.11, 3.12, and 3.13 (see `.github/workflows/ci.yml`). CI also
includes an `install-check` job that runs `uv tool install .` and verifies all four CLIs
start up correctly - the direct guard against the editable-install/worktree failure mode.

> **When working in a git worktree:** test your changes with `uv run pytest` or
> `uv run python -m cc_session_tools.cli.ccd` - do not run `uv tool install` from inside
> a worktree. After merging, run `uv tool install ~/repos/claude-code-session-tools`
> (or `uv tool install cc-session-tools` if installed from PyPI) to update the global
> install.

## Limitations and caveats

- Linux and macOS only. Windows is not tested; the tools assume POSIX paths and `os.execvpe`-style process replacement.
- The tools shell out to `claude` via `os.execvpe`. If `claude` isn't on `$PATH`, `ccd` and `ccr` will fail with the standard "command not found" error.
- The strict-root convention is opinionated. If you don't want it, just leave `CLAUDE_SESSION_TOOLS_PROJ_ROOT` unset.

## Licence

MIT - see [LICENSE](LICENSE).
