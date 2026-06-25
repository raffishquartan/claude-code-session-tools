# claude-code-session-tools

Claude Code on its own is great. But once you're running parallel sessions across a codebase, orchestrating subagents, or doing sustained work over days and projects, the overhead starts to compound: sessions with UUID names you can't recognise, working files scattered through your repo root, no idea where your token budget went, and nothing stopping a background agent from pushing to main while you're away.

CCST is an opinionated toolkit that addresses this directly. It keeps sessions named and findable, gives Claude a consistent place to write its working files, gates high-stakes actions behind a confirmation step, and tells you exactly what each model and project cost you.

Three concerns, one repo, for life on the [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI:

1. **Session management** — start, resume, find, relocate, and delete Claude Code sessions from the shell, with tagged dated session directories that don't pollute your repo root.
2. **Usage analytics** — parse `~/.claude/projects/**/*.jsonl` into tokens-and-dollars breakdowns by project, session, model, MCP server, plugin, and tool.
3. **Hook library** — Python package (`cccs_hooks`) providing Claude Code SessionStart / PreToolUse / PostToolUse / UserPromptSubmit / Stop hook implementations, invokable via `ccst hooks run <name>`.

The repo ships seven CLIs, one shell helper, eight bundled skills, and ten bundled hooks:

**CLIs and shell helper**

| | What it does |
|---|---|
| **`ccd <tag>`** | Start a new session with a pre-created `cc-sessions/<date>-<tag>/` directory and a tagged display name. |
| **`ccr <fragment>`** | Resume an existing session by typing any substring of its name. |
| **`ccs [query]`** | Search across your sessions by name, file contents, or transcript messages. No query → list all sessions newest-first. |
| **`claude-code-usage`** | Multi-dimensional usage analytics CLI: query/group/filter by project, session, model, MCP server, plugin, tool, day/week/month/year. Reconciles dollar totals against `ccusage`. |
| **`ccst <noun> <verb>`** | Umbrella CLI for hook and skill management: install, uninstall, health-check, shell helpers, telemetry trim, global CLAUDE.md messaging block. |
| **`ccmsg <command>`** | Inter-session messaging CLI: send, deliver, read, list, claim, and archive durable messages between Claude Code sessions. |
| **`ccsched <command>`** | Scheduled-task CLI: register, list, edit, enable/disable, and remove recurring jobs; inspect status; one-shot sweep. |
| **`ccl` (shell fn)** | Shell function wrapping `ccs` for list-mode usage. Installed by `ccst shell install`. |

**Bundled skills** (installed via `ccst skills install`)

| | What it does |
|---|---|
| **`analyse-cc-usage`** | Wraps `claude-code-usage`. Lets a Claude Code session answer "how much have I spent on Opus this month?" without you typing the CLI yourself. |
| **`delete-sessions`** | Permanently deletes one or more sessions (cc-sessions dir + JSONL transcript + .tag file). Dry-run by default; requires an 8-digit confirmation code to execute. |
| **`find-claude-code-session`** | Wraps `ccs`. Lets a Claude Code session locate one of your prior sessions by name or content and offer a `ccr` command to resume it. |
| **`generate-8digit-code`** | Generates a cryptographically secure 8-digit confirmation code via `secrets.randbelow`. LLMs are statistically biased random number generators — this ensures gated-action codes are genuinely unpredictable. |
| **`list-empty-sessions`** | Wraps `ccs --emptiness only`. Finds sessions you never actually typed in — accumulate from accidental `ccd` invocations or abandoned starts. |
| **`move-session`** | Move, rename, or move+rename a session while keeping its `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` transcript resumable. |
| **`send-session-message`** | Guides recipient choice, message composition, and confirmation when sending an inter-session message via `ccmsg send`. |
| **`manage-recurring-cc-jobs-using-ccsched`** | Translates natural-language cadence requests into `ccsched add` calls; disambiguates `ccsched` (local recurring jobs) vs `/schedule` (cloud cron) vs `/loop` (in-session poll). |

**Bundled hooks** (installed via `ccst hooks install`)

| | What it does |
|---|---|
| **`session-tag`** (SessionStart) | Writes a `<uuid>.tag` file so `claude-code-usage` can map session UUIDs to human-readable names. |
| **`prompt-guard`** (UserPromptSubmit) | Scans incoming prompts for credential shapes and injection patterns before they reach the model. |
| **`last-screenshot`** (UserPromptSubmit) | Resolves your newest screenshot for the `>lss` token and injects its path. Requires `CCST_SCREENSHOT_DIR`. |
| **`bash-security-review`** (PreToolUse) | Tiered Bash command security review with an allowlist cache and LLM fallback. |
| **`confirm-8digit`** (PreToolUse) | Blocks a configurable set of high-stakes tool calls unless the user repeats back an 8-digit confirmation code. |
| **`edit-write-audit`** (PostToolUse) | Audits file writes for sensitive paths and checks that WORKLOG.md is being maintained. |
| **`session-end`** (Stop) | Nudges you to commit uncommitted changes and update WORKLOG.md when a session ends. |
| **`messaging-deliver`** (SessionStart + UserPromptSubmit) | Sweeps `~/.claude/cc-messages/` for messages addressed to this session and injects a compact digest as additional context. Handles auto-read, read-receipts, first-claim-wins claims, and 14-day archival without prompting. |
| **`catchup`** (SessionStart) | Reconciles the scheduled-job registry, launches owed jobs as detached workers, and surfaces previously-completed runs as a digest. |
| **`catchup`** (UserPromptSubmit) | Surfaces (reaps) completed scheduled runs on a throttle (60 s), so a job launched at session start surfaces at the next prompt in the same session. |

See [CHANGELOG.md](CHANGELOG.md) for a full version history. See [TODO.md](TODO.md) for known follow-up work (including the notify-user skill integration).

If you've ever tried to remember which `1f4a8b3c-...` UUID is the session where you were debugging that flaky test last Tuesday, or wondered which project burned through last week's Opus budget, this is for you.

## Installation

### Prerequisites

- **Python 3.11+** (3.12+ recommended)
- **The `claude` CLI on your `$PATH`.** Install it first via [the official Claude Code instructions](https://docs.anthropic.com/en/docs/claude-code/setup) and verify with `claude --version`.
- **`ccusage` (optional)** - if on `$PATH`, `claude-code-usage reconcile` cross-checks dollar totals against it. Skipped gracefully if missing.
- **`ripgrep` (optional)** - `ccs --contents` prefers `rg`; falls back to threaded Python `grep` if missing.

### Install and set up

**Easiest path — run the bundled script from a local clone:**

```sh
git clone https://github.com/raffishquartan/claude-code-session-tools.git
cd claude-code-session-tools
bash install-everything.sh
```

`install-everything.sh` installs the CLIs (via `uv` or `pipx`, whichever is
present), then symlinks the skills, merges the hooks, adds the `ccl` shell
function, and registers the inter-session-messaging block in `~/.claude/CLAUDE.md`.
It runs `ccst doctor` at the end so you can see the health check immediately. Re-running is safe — every step is idempotent.

> **Options:** `--from-source` reinstalls from the local clone rather than
> PyPI. `--upgrade` forces an upgrade of an existing install.

> **Note:** `install-everything.sh` handles steps 1–5 of setup. Step 6 — adding broader CCST guidance to your global `~/.claude/CLAUDE.md` (session management, 8-digit gate, etc.) — is interactive and must be run separately afterwards. See [Configure your global CLAUDE.md](#configure-your-global-claudemd) below.

**Manual path — step by step:**

```sh
# 1. Install the package (choose one)
uv tool install cc-session-tools          # recommended
# pipx install cc-session-tools           # alternative

# 2. Install bundled skills, hooks, and the ccl shell function (each command is
#    idempotent — safe to re-run after upgrades)
ccst skills install --apply               # symlinks skills into ~/.claude/skills/
ccst hooks install --apply                # merges all bundled hooks into ~/.claude/settings.json
ccst shell install --apply                # adds ccl() to ~/.bashrc and ~/.zshrc

# 3. Verify everything is wired up
ccst doctor

# 4. Add CCST guidance to ~/.claude/CLAUDE.md (see Configure your global CLAUDE.md below)
#    This is required: without it, Claude Code sessions won't know to use CCST's
#    CLIs and skills, and the 8-digit gate won't have an action list to enforce.
```

After `ccst shell install --apply`, open a new shell (or `source ~/.bashrc`) to activate `ccl`.

> **Installing from source (pre-release or offline):**
> ```sh
> git clone https://github.com/raffishquartan/claude-code-session-tools.git
> cd claude-code-session-tools
> uv tool install .
> ```

> **Installing from a local clone:** if you keep a local clone for development or
> to stay on the latest commit, refresh the global install with:
> ```sh
> uv tool install ~/repos/claude-code-session-tools
> ```
> **Do NOT run `uv tool install` from inside a git worktree.** That overwrites the
> global install's source pointer with the worktree path, which breaks all six CLIs
> when the worktree is deleted. Use `uv run pytest` to test inside a worktree, and run
> `uv tool install ~/repos/claude-code-session-tools` from outside the worktree after
> merging.

### Upgrade

```sh
# 1. Upgrade the package (choose one)
uv tool upgrade cc-session-tools          # recommended
# pipx upgrade cc-session-tools           # alternative

# 2. Pick up any new bundled skills, hooks, and shell helpers
ccst skills install --apply
ccst hooks install --apply
ccst shell install --apply

# 3. Verify
ccst doctor
```

After `ccst shell install --apply`, re-source your shell rc file to pick up any updated `ccl()` function:

```sh
source ~/.bashrc   # bash
source ~/.zshrc    # zsh
```

## Configure your global CLAUDE.md

This is the final required setup step — not included in `install-everything.sh` because it is interactive. Without it, Claude Code sessions have no guidance to use CCST's CLIs or skills, and the 8-digit confirmation gate has no action list to enforce.

Add CCST-aware guidance to your global `~/.claude/CLAUDE.md` so Claude Code sessions know about the session-management tools and which actions require an 8-digit confirmation gate.

The easiest way is to run the bundled bootstrap prompt:

```sh
cd ~/repos/claude-code-session-tools && \
  claude -p "Check that you are executing with the claude-code-session-tools repository as your cwd. If you are not then exit. If you are then use this file as your prompt: docs/global-claude-md-bootstrap-prompt.md"
```

This prompt will:

1. Detect which CCST CLIs, skills, and hooks are installed.
2. Propose standard additions to your `~/.claude/CLAUDE.md` — pointers to `ccs`/`ccd`/`ccr`/`ccl`, guidance to invoke the bundled skills (`find-claude-code-session`, `move-session`, `list-empty-sessions`, `delete-sessions`, `analyse-cc-usage`, `generate-8digit-code`), and a section explaining the 8-digit confirmation hook.
3. **Interactively ask you** which classes of high-stakes action you want gated (push-to-remote, force-push, PR merges, branch deletion, financial transactions, sending external messages, etc.) and write your choices into a `## 8-digit gated actions` section.
4. Write the additions idempotently (re-running the prompt replaces the block, not appends).

If you prefer to edit `~/.claude/CLAUDE.md` by hand, the suggested additions are:

- Use `ccs` (or `ccl`) to list sessions, `ccr` to resume, `ccd` to start a new one — do not start new sessions inside the running one.
- When the user wants to find a prior session, invoke the `find-claude-code-session` skill.
- When the user wants to relocate or rename a session, invoke the `move-session` skill.
- When the user wants to clean up never-used sessions, invoke `list-empty-sessions` then `delete-sessions` (8-digit gated).
- When the user wants usage analytics, invoke the `analyse-cc-usage` skill.
- When you need an 8-digit confirmation code for a gated action, invoke `generate-8digit-code` — never invent a number yourself.
- Ask before pushing to remote, merging PRs, deleting branches, or taking other high-stakes actions — the 8-digit confirmation hook is the mechanism.

## Why bother?

Claude Code stores each session as a `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl` transcript and exposes them through `claude --resume`. That works, but the picker shows untagged sessions in opaque order, the working files for a session sprawl into your repo root, and there's no built-in way to grep across past conversations or see where your tokens went.

These tools add:

1. **A tagged, dated session directory** under `<project>/cc-sessions/<YYYYMMDD>-<tag>/` with `working/` and `out/` subdirs - the convention Claude Code's [session memory](https://docs.anthropic.com/en/docs/claude-code/memory) hooks expect when you want scratch space and deliverables that don't pollute your repo.
2. **Resume-by-fragment** so you can type `ccr flaky` instead of scrolling through the picker.
3. **List and search** so `ccs` (no args) shows all local sessions newest-first, `ccl` wraps it for convenience, and `ccs --contents --global "GraphQL retry"` finds every conversation that mentioned it.
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

### `ccs` - search and list

```sh
# List all sessions (no args → list mode)
ccs                                        # newest-first, current project
ccs --global                               # all configured roots

# Search
ccs flaky                                  # name search in current project
ccs flaky --global                         # name search across all configured roots
ccs "GraphQL retry" --contents             # full-text search of working/out files
ccs "GraphQL retry" --messages             # full-text search of JSONL transcripts
ccs "GraphQL retry" --contents --messages --global  # combined, all projects

# Filter
ccs flaky --since 2026-04-01               # only sessions from April 2026 onwards
ccs --emptiness only                       # only empty sessions (no user messages)
ccs --emptiness exclude                    # exclude empty sessions
ccs flaky --sort newest                    # explicit sort (default)
ccs flaky --sort oldest
```

Results are ordered newest-first by session start date by default. `--contents` shows one line of context around each match; `--messages` searches the Claude transcript JSONL files and surfaces matching turns. Every run prints a session-count footer on stderr: `ccs: searching N sessions (M empty, K hook) in <scope>`.

Useful flags:

| Flag | What it does |
|---|---|
| `--name` | Search session basenames (the default; explicit opt-in). |
| `--contents` | Search text files inside each session's `working/` and `out/` directories. |
| `--messages` | Search Claude transcript JSONL files in `~/.claude/projects/`. |
| `--global` | Search across all configured roots, not just the current project. |
| `--emptiness {only,exclude,any}` | Filter by whether a session has any user-typed messages. Default: `any`. |
| `--since DATE` | Only sessions started on or after DATE. Accepts `YYYYMMDD`, `YYYY-MM-DD`, `7d` (days ago), `2w` (weeks ago), `1m` (months ago). |
| `--before YYYYMMDD` | Only sessions started before DATE. |
| `--days N` | Only sessions started within the last N days. |
| `--sort {newest,oldest}` | Sort order (default: newest). |
| `--exclude-hooks` | Hide hook-security-check sessions from results. |
| `--json` | Output results as a JSON array. |
| `--null` | Output null-delimited basenames (for `xargs -0`). |
| `--debug` | Enable verbose debug output (`CCX_DEBUG=1`). |

### `ccl` - list sessions (shell function)

`ccl` is a shell function installed by `ccst shell install --apply`. It wraps `ccs` in list mode:

```sh
ccl              # list all sessions in current project, newest-first
ccl --global     # list across all configured roots
ccl --emptiness only  # list only empty sessions
```

After install, activate it with `source ~/.bashrc` (or open a new shell).

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

The repo ships seven Claude Code skills, designed to be symlinked into `~/.claude/skills/`. They're thin wrappers around the CLIs so a Claude Code session can invoke them on your behalf in response to natural-language prompts.

Install all seven at once with `ccst skills install --apply` (see [Install and set up](#install-and-set-up-recommended-path) above).

### `find-claude-code-session`

Wraps `ccs`. Triggers on prompts like "find my session about X", "did I work on foo before", "what session was I in when Y". Constructs the right `ccs` invocation, escalates from local to global search if the local hit list is empty, and presents results as `ccr <fragment>` commands you can paste.

### `move-session`

Moves, renames, or move+renames a session directory while keeping the JSONL transcript resumable. Triggers on "move session to", "rename my session", "this session belongs in a different folder". Dry-run by default - you must pass `--execute` for any filesystem change. Validates source and destination against the same rules as `ccd`, copies the session directory tree, rewrites JSONL `cwd` fields to the destination path, and appends a tombstone record to the source JSONL so `claude --resume` on the old session explains where it went.

### `analyse-cc-usage`

Wraps `claude-code-usage`. Triggers on usage questions: "how much have I spent on Claude Code", "tokens used this week", "which project costs the most", "Opus vs Sonnet", "any spike in usage recently". Picks the right subcommand and flags, runs it, and summarises the result in plain English.

### `list-empty-sessions`

Wraps `ccs --emptiness only`. Triggers on prompts like "list empty sessions", "find sessions I never used", "which sessions are empty", "show abandoned sessions". Reformats the output with a count summary and two copy-pasteable follow-up commands: one `ccr <basename>` line to resume, and one `delete-sessions <basenames...>` line for bulk removal.

### `delete-sessions`

Permanently deletes one or more sessions. Triggers on "delete session", "remove empty sessions", "clean up sessions". Inputs must be explicit session basenames — the user or the `list-empty-sessions` skill must supply them. Pre-flight checks confirm each session exists and is not the currently running session. Dry-run by default; requires `--execute` plus an 8-digit confirmation code for actual deletion. Deletes the `cc-sessions/<basename>/` directory, the JSONL transcript, the `.tag` file, and optionally the `~/.claude/tasks/<encoded>/` task directory.

Note: the 8-digit confirmation in `delete-sessions` is an inline prompt (not a reuse of the `cccs_hooks.confirm_8digit` PreToolUse hook). The hook guards tool calls; the script guards its own execution.

See `docs/design.md` for the full design and CLI contract.

## Inter-session messaging

`ccmsg` lets Claude Code sessions send durable, addressed messages to one another. Messages are stored as markdown-with-frontmatter files under `~/.claude/cc-messages/` — on-disk, human-readable, and auditable. Each message carries a recipient (session tag, project name, or free-text description), a sender, a timestamp, and an optional list of attachment paths.

### `ccmsg` subcommands

| Subcommand | What it does |
|---|---|
| `send` | Compose and route a new message to a session, project, or description. |
| `deliver` | Sweep the store for messages addressed to this session and emit a compact digest; used by the delivery hooks. |
| `read` | Print the full body and metadata of one message by ID. |
| `list` | List messages in compact form (ID, recipient, status, subject). |
| `claim` | Claim a description-addressed message so no other session picks it up (first-claim-wins). |
| `archive` | Manually archive a message without reading it. |

### Delivery hooks

Two hooks drive automatic delivery without any extra steps:

- **`messaging-deliver` on `SessionStart`** — runs a full sweep of the message store when a session opens, injecting a digest of all pending messages as additional context.
- **`messaging-deliver` on `UserPromptSubmit`** — runs an incremental sweep before each prompt, picking up any messages that arrived since the last check.

Both hooks handle auto-read, read-receipts, first-claim-wins claims, and 14-day archival transparently.

Install both hooks with `ccst hooks install --apply` (they are included in the standard bundle).

### `send-session-message` skill

The `send-session-message` skill guides you through choosing a recipient, composing the message body, and confirming before `ccmsg send` is invoked. Useful when you want Claude to act as a dispatcher rather than constructing the `ccmsg send` command manually.

### `ccst claude-md install/uninstall`

`ccst claude-md install --apply` adds a managed proactive-messaging block to your global `~/.claude/CLAUDE.md`, telling every Claude Code session how to recognise and act on incoming message digests. `ccst claude-md uninstall --apply` removes it. Both commands are idempotent.

The `install-everything.sh` script runs `ccst claude-md install --apply` automatically as part of the standard installation sequence.

## Scheduled-task catch-up

`ccsched` registers local recurring jobs in `~/.claude/cc-scheduler/jobs.toml`
and reconciles them on Claude Code session activity. Jobs run on a declared
cadence and are back-filled when missed (e.g. while the laptop was off), with
coalescing controlled per-job.

### Execution model

The `catchup` hook only reconciles (what is owed?), launches detached background
workers (`ccsched _run-job`), and surfaces previously-completed runs — job
commands never run on the session critical path, so a slow or numerous backlog
never blocks or slows session start. A per-job `O_EXCL` in-flight lock with
stale-holder reclamation ensures each job runs at most once at a time; there is
no global sweep lock, so a duplicate launch from two sessions is harmless (the
loser exits).

### `ccsched` subcommands

| Subcommand | What it does |
|---|---|
| `add` | Register a new job with a declared cadence and command. |
| `list` | List all registered jobs and their next-fire times. |
| `edit` | Edit a job field (cadence, command, coalesce, etc.) in-place. |
| `enable` / `disable` | Toggle a job on or off without removing it. |
| `remove` | Delete a job from the registry. |
| `run` | Run a job immediately (foreground; bypasses cadence). |
| `status` | Show recent ledger history for a job (or all jobs). |
| `sweep` | One-shot reconcile + launch from the shell. |
| `_run-job` | Internal: run one owed instance and record the result (called by the hook). |

### Cadence grammar

| Form | Meaning |
|---|---|
| `every:2h` | Every 2 hours, drifting from last run. |
| `every:@from=2026-06-01T09:00Z/2w` | Drift-free fortnightly anchored to a fixed epoch. |
| `daily@09:00` | Once per calendar day at 09:00 local time. |
| `weekly:mon@08:30` | Once per week on Monday at 08:30. |
| `monthly:15@07:00` | Day-of-month (e.g. the 15th) at 07:00. |
| `monthly:fri#2@07:00` | Nth weekday of the month (e.g. 2nd Friday). |

Cadences that land on the same calendar occurrence are coalesced per the job's
`coalesce` setting: `one` fires once for any backlog; `each` fires once per owed
instant.

### Delivery hooks

Two hooks drive scheduled catch-up without extra steps:

- **`catchup` on `SessionStart`** — reconciles, launches owed workers, and
  surfaces any previously-completed runs when a session opens.
- **`catchup` on `UserPromptSubmit`** — surfaces (reaps) completed runs and
  re-reconciles on a 60-second throttle, so a job launched at session start
  surfaces at the next prompt in the same session.

Both hooks are included in the standard bundle and installed by
`ccst hooks install --apply`. Surfacing is per-session (per-session cursor), so
each session sees each completed run exactly once. Failures never block the
session; every action is recorded to the shared `~/.cache/claude/logs/fires.jsonl`
telemetry ledger.

### `manage-recurring-cc-jobs-using-ccsched` skill

The `manage-recurring-cc-jobs-using-ccsched` skill translates natural-language
cadence requests ("run my Tesco shop every other Sunday at 09:00") into
validated `ccsched add` calls and disambiguates between:

- `ccsched` — local recurring jobs, runs off the session critical path.
- `/schedule` — cloud-hosted agents that run on a cron schedule.
- `/loop` — in-session polling that runs while the session is open.

### Registry

The registry lives at `~/.claude/cc-scheduler/jobs.toml` — plain TOML,
hand-editable, created lazily on first `ccsched add`. Every job declares a
`cadence`, `command` (argv), `coalesce` (`one`/`each`), optional `surface`
(whether to include in the digest), and optional `catchup_window` (how far back
to back-fill; default 7 days).

## Hook library (`cccs_hooks`)

The `cccs_hooks` Python package provides Claude Code hook implementations.
Install via `uv tool install cc-session-tools` or `pipx install cc-session-tools`
to make the hook library available. Hooks are invoked through `ccst hooks run <name>`.

### Modules

| Module | Hook event | What it does |
|---|---|---|
| `cccs_hooks.telemetry` | — | Writes structured JSONL to `~/.cache/claude/logs/fires.jsonl`; used by other modules. Rotates at 10 MB (numbered slots: `fires.jsonl.1`, `.2`, `.3`). |
| `cccs_hooks.transcript` | — | Walks parent session transcript JSONL; shared by `confirm_8digit`. |
| `cccs_hooks.confirm_8digit` | PreToolUse | 8-digit confirmation guard for gated tools. |
| `cccs_hooks.cache` | — | SHA-256 command cache (CSV); used by `bash_security_review`. |
| `cccs_hooks.bash_security_review` | PreToolUse | Tiered Bash security review with cache. |
| `cccs_hooks.edit_write_audit` | PostToolUse | Sensitive-path + WORKLOG audit. |
| `cccs_hooks.prompt_guard` | UserPromptSubmit | Credential/injection pattern guard. |
| `cccs_hooks.session_end` | Stop | WORKLOG/uncommitted-changes nudge. |
| `cccs_hooks.session_tag` | **SessionStart** | Writes `<uuid>.tag` so `claude-code-usage` can map session UUIDs to `ccd` name tags (see [Session tag hook](#session-tag-hook)). |
| `cccs_hooks.last_screenshot` | UserPromptSubmit | Resolves the newest screenshot for the `>lss` token and injects its path (see [Last screenshot hook](#last-screenshot-hook)). |
| `cccs_hooks.messaging_deliver` | SessionStart + UserPromptSubmit | Sweeps `~/.claude/cc-messages/` for messages addressed to this session and injects a compact digest as additional context (see [Inter-session messaging](#inter-session-messaging)). |
| `cccs_hooks.catchup` | SessionStart + UserPromptSubmit | Reconciles + launches scheduled jobs detached, then surfaces completed runs as a digest (see [Scheduled-task catch-up](#scheduled-task-catch-up)). |

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
| `last-screenshot` | `cccs_hooks.last_screenshot` |
| `messaging-deliver` | `cccs_hooks.messaging_deliver` |
| `catchup` | `cccs_hooks.catchup` |

The dispatcher reads the event payload from stdin, calls the matching module's
`main()`, and propagates its exit code.

### Session tag hook

`cccs_hooks.session_tag` is a **SessionStart** hook that writes a small tag file when a session is created via `ccd <tag>`:

- File written: `~/.cache/claude/session-tags/<session_id>.tag` (flat layout keyed by UUID; overrideable via `CCCS_SESSION_TAGS_DIR`)
- File content: the `ccd` name tag (e.g. `oneshot-add-uuid-for-better-usage-mapping`)
- If `CLD_SESSION_TAG` is not set (i.e. the session was not started by `ccd`), the hook exits silently.

Claude Code stores each session as `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`. The display name (set by `ccd` via `claude -n`) survives only in ephemeral PID files that disappear when the process exits. The `.tag` file gives `claude-code-usage` and other tools a persistent, stable mapping from UUID to human name - so `--session-format name` shows `oneshot-add-uuid-for-better-usage-mapping` instead of `sess-8f3a2c1d`.

To migrate existing `.tag` files from the old `~/.claude/projects/` location to the new flat cache dir, run `ccst tags migrate` (see the `tags migrate` subcommand).

### Last screenshot hook

`cccs_hooks.last_screenshot` is a **UserPromptSubmit** hook that lets you refer to
your most recent screenshot with the token `>lss` ("last screenshot") instead of
finding and typing its path.

When a submitted prompt contains a standalone `>lss` token, the hook finds the
newest image in your screenshot directory and injects its absolute path as
additional context, with a note telling Claude how to decide whether you want it
read. The hook only ever injects **text** - the image enters context only if
Claude then `Read`s the path. So you can also talk *about* the feature
(`"what does the >lss hook do?"`) without an image being attached.

- **Token match:** `>lss` matches unless glued to a letter or digit. Matches
  `>lss`, `>lss?`, `(>lss is handy)`, `loss at >lss.`; does not match `>lssfoo`.
- **Selection:** newest `*.png` / `*.jpg` / `*.jpeg` by file mtime.
- **Staleness:** if the newest screenshot is older than 10 minutes, the injected
  note warns you to confirm it is the one you meant.

**Configuration (required):** set the `CCST_SCREENSHOT_DIR` environment variable
to the folder where your screenshots are saved - e.g. in the `env` block of
`~/.claude/settings.json`:

```json
"env": {
  "CCST_SCREENSHOT_DIR": "/path/to/your/Screenshots"
}
```

If `>lss` is used while `CCST_SCREENSHOT_DIR` is unset (or not a directory), the
hook prints a visible warning to stderr telling you to set it. The hook never
blocks and always exits 0.

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

The `ccst` umbrella CLI provides hook and skill management, shell helper install, and system health checks.

### `ccst hooks install`

Merges hook entries from a source `settings.json` into `~/.claude/settings.json`.
With no `--source`, auto-discovers the bundled `config/hooks-bundle.json` and
installs all eight default hooks.

```sh
# Dry run (default) - shows what would be added
ccst hooks install

# Write all bundled hooks
ccst hooks install --apply

# Install one specific hook from the bundle
ccst hooks install --hook session-tag --apply

# Install from a custom source file
ccst hooks install --source /path/to/custom-hooks.json --apply
```

Matching is by event type + matcher + command string; already-present hooks are
never duplicated. The target file is written atomically (`.tmp` swap).

### `ccst hooks uninstall`

Remove hook entries from `~/.claude/settings.json`. Dry-run by default.

```sh
# Show what would be removed
ccst hooks uninstall

# Remove all bundled hooks
ccst hooks uninstall --apply

# Remove one specific hook
ccst hooks uninstall --hook session-tag --apply
```

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

### `ccst skills uninstall`

Remove skill symlinks from `~/.claude/skills/`. Refuses to remove non-symlinks
unless `--force`. Dry-run by default.

```sh
# Show what would be removed
ccst skills uninstall

# Remove all bundled skill symlinks
ccst skills uninstall --apply

# Remove one specific skill
ccst skills uninstall --skill move-session --apply
```

### `ccst shell install`

Append a `ccl()` shell function to `~/.bashrc` and/or `~/.zshrc` between
sentinel markers. Idempotent — re-running replaces the existing block.

```sh
# Dry run (default) - shows what would be added
ccst shell install

# Write the ccl() function
ccst shell install --apply
```

After running `--apply`, re-source your shell rc file to activate the updated `ccl()` function:

```sh
source ~/.bashrc   # bash
source ~/.zshrc    # zsh
```

### `ccst shell uninstall`

Remove the sentinel-bracketed `ccl()` block from shell rc files.

```sh
ccst shell uninstall --apply
```

### `ccst claude-md install`

Add or update the inter-session-messaging block in the global `~/.claude/CLAUDE.md`. Idempotent — re-running replaces the existing block rather than appending.

```sh
# Dry run (default) - shows what would be added or replaced
ccst claude-md install

# Write the block
ccst claude-md install --apply
```

### `ccst claude-md uninstall`

Remove the managed messaging block from `~/.claude/CLAUDE.md`. Dry-run by default.

```sh
ccst claude-md uninstall --apply
```

### `ccst doctor`

Run a full health check: PATH for all six CLIs, env vars (`REPO_ROOT`/`PROJ_ROOT`),
`~/.claude/settings.json` JSON validity, expected hook registrations present,
skill symlinks correct and pointing at the installed source, version drift
between installed `ccst` and the latest release on PyPI.

```sh
ccst doctor           # checks everything, including PyPI version check
ccst doctor --no-pypi # skip the network version check (useful in CI)
```

Exit `0` if all checks are OK. Exit `1` if any check is WARN or FAIL.

### `ccst telemetry trim`

Prune old hook telemetry data from `~/.cache/claude/logs/fires.jsonl`.

```sh
# Remove entries older than 30 days, or if the file exceeds 5 MB
ccst telemetry trim --max-age-days 30 --max-size 5
```

Telemetry is also rotated automatically at 10 MB (numbered slots `fires.jsonl.1/.2/.3`).

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
includes an `install-check` job that runs `uv tool install .` and verifies all six CLIs
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
