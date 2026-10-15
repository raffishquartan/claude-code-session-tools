# cc-session-tools (CCST)

[![PyPI](https://img.shields.io/pypi/v/cc-session-tools.svg)](https://pypi.org/project/cc-session-tools/)
[![Python](https://img.shields.io/pypi/pyversions/cc-session-tools.svg)](https://pypi.org/project/cc-session-tools/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Tools for running Claude Code at scale as your default way of working, not just a
sometimes tool.**

Claude Code on its own is a great single conversation. It is much less good at the things
that appear once you use it all day, across many projects, for weeks:

- _"Which session was that in?"_ - sessions are anonymous UUIDs in a transcript folder.
- _"Where did that draft go?"_ - files end up wherever the conversation left them.
- _"I need to tell the other session about this."_ - sessions cannot talk to each other.
- _"This should just run every week."_ - nothing runs while Claude Code is closed.
- _"Did that command it just ran do something bad?"_ - you either approve everything by hand
  or approve nothing.
- _"My CSV of records has drifted out of sync with my notes again."_ - ad hoc files do not
  scale.
- _"I'm working with GB of unstructured data, and I need multiple sessions to work on the
  files simultaneously"_ - race conditions anyone?
- _"I can't give Claude Code all this power"_ - and you definitely don't want to trust the
  LLM to enforce its own guardrails

CCST fixes each of those with small, boring, local tools. Everything is a command line
program plus a handful of Claude Code hooks and skills. There is no server, no account and
no network service (the only optional outbound call is a Telegram notification you opt
into). All state is SQLite under `~/.local/share/claude/`.

If you run Claude Code only a few times a week for one-off questions or just 1 or 2 coding
sessions on different code bases at the same time then you do not need this. If you run
several named sessions a day, want to resume one from three weeks ago, want sessions to
leave each other notes, and want housekeeping to just happen, CCST is the layer that makes
that sustainable.

## Contents

- [What you get](#what-you-get)
- [Install](#install)
- [Quick start](#quick-start)
- [Sessions: `ccd`, `ccr`, `ccs`](#sessions-ccd-ccr-ccs)
- [Sessions talking to each other: `ccmsg`](#sessions-talking-to-each-other-ccmsg)
- [Scheduled jobs: `ccsched`](#scheduled-jobs-ccsched)
- [Safety hooks](#safety-hooks)
- [Per-project data store: `pdata`](#per-project-data-store-pdata)
- [Usage and cost analytics: `claude-code-usage`](#usage-and-cost-analytics-claude-code-usage)
- [Bundled skills](#bundled-skills)
- [Teaching Claude the conventions: `claude-md`](#teaching-claude-the-conventions-claude-md)
- [Command reference](#command-reference)
- [Configuration](#configuration)
- [Health checks, upgrades and uninstalling](#health-checks-upgrades-and-uninstalling)
- [How it fits together](#how-it-fits-together)
- [Contributing](#contributing)

## What you get

| You want to...                                    | CCST gives you                                                                                                                 |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| Find and resume past work                         | `ccd` starts a session under a name you choose, `ccr` resumes it by name fragment, `ccs` searches names, files and transcripts |
| Keep drafts and deliverables together             | A `working/` and `out/` directory per session, created for you                                                                 |
| Hand work between sessions                        | `ccmsg`: durable messages addressed to a session, a project, or "whoever picks this up"                                        |
| Run things on a schedule without a daemon         | `ccsched`: recurring jobs, caught up the next time you open Claude Code                                                        |
| Stop destructive commands                         | A hard-deny hook plus a cached, tiered (allowlist, heuristics, LLM) review of every shell command                              |
| Gate high-stakes actions                          | An 8-digit confirmation code you must type back before gated tools run                                                         |
| Track structured records per project              | `pdata`: one SQLite database per project, with typed fields, versioning and multi-machine sync                                 |
| See what Claude Code costs you                    | `claude-code-usage`: tokens and dollars by project, session, model, tool, MCP server, plugin                                   |
| Avoid context bloat                               | A warning hook when the context window gets large, plus `/compact-smart` and `/context-override`                               |
| Get Claude to follow these conventions unprompted | Managed, human-editable sections installed into your global `CLAUDE.md`                                                        |
| Start from sensible workflows                     | 26 bundled skills (session hygiene, `pdata` workflows, document extraction, agent patterns)                                    |
| Know it all still works                           | `ccst doctor`, a single health check across every subsystem, also run weekly by a bundled job                                  |

## Install

Requires Python 3.11 or newer, [Claude Code](https://docs.claude.com/en/docs/claude-code)
and a POSIX shell (macOS or Linux; Windows via WSL).

### 1. Install the package

```sh
uv tool install cc-session-tools   # recommended
# or: pipx install cc-session-tools
# or: pip install cc-session-tools
```

This puts the commands `ccd`, `ccr`, `ccdapi`, `ccrapi`, `ccs`, `ccmsg`, `ccsched`, `ccst`,
`claude-code-usage` and `cccs-stats` on your `PATH`.

### 2. Tell CCST where your projects live

`ccd` creates a session directory inside the project you are working in, and refuses to run
outside a configured root. Set one or both of these in your shell profile (for example
`~/.zshrc` or `~/.bashrc`). Each must point at a directory whose direct children are your
projects:

```sh
export CLAUDE_SESSION_TOOLS_REPO_ROOT="$HOME/repos"   # loose: any session name tags must not have spaces in them
export CLAUDE_SESSION_TOOLS_PROJ_ROOT="$HOME/cc"      # strict: session name tags must be <project folder name>-<label>
```

- Use `REPO_ROOT` for code repositories. Any tag works (`ccd fix-login-bug`).
- Use `PROJ_ROOT` for non-code "project" folders, where you want tags namespaced as
  `<project>-<label>` (project names are lowercase letters and digits only). `pdata` (below)
  lives under this root.

### 3. Wire CCST into Claude Code

```sh
ccst install-everything            # dry run: shows exactly what would change
ccst install-everything --apply    # does it
ccst doctor                        # health check; prints only problems (add --all to see everything)
```

`install-everything` installs, into `~/.claude`:

1. the bundled **skills** (symlinked into `~/.claude/skills/`)
2. the **hooks** (merged into `~/.claude/settings.json`; existing entries you wrote are left
   alone)
3. the **`ccl` shell function**, written as `~/.shellrc.d/ccl.sh`
4. the managed **`CLAUDE.md` sections**, delimited blocks in `~/.claude/CLAUDE.md`
5. the standard **scheduled jobs**

then runs `ccst doctor`. It is idempotent, so it is safe to re-run.

### 4. Load the shell functions

`ccst` does not edit your `~/.zshrc` or `~/.bashrc`. Make sure your shell sources the
fragment directory (add this once if it does not already):

```sh
for f in ~/.shellrc.d/*.sh; do [ -r "$f" ] && . "$f"; done
```

Open a new terminal, `cd` into a project under one of your roots, and you are ready.

### Optional configuration

These are not needed to get started; see [Configuration](#configuration) for details.

- `CCST_CONFIRM_8DIGIT_GATED_TOOLS` - which tools require the 8-digit confirmation code.
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` - push scheduled-job outcomes or other messages
  via Telegram.
- `CCST_SCREENSHOT_DIR` - where the `>lss` prompt token looks for your latest screenshot.
- `~/.config/ccst/api-keys` - Anthropic API keys for `ccdapi`/`ccrapi`.

## Quick start

```sh
cd ~/repos/myproject
ccd fix-login-bug        # new session: cc-sessions/20261015-fix-login-bug/ (today's date)
# ... work, then quit Claude Code ...

ccr login-bug            # days later: resume it by any fragment of its name
ccs --global             # list sessions across every project
ccs "rate limit" --contents --global   # search inside working/ and out/ of all sessions
ccst doctor              # is everything healthy?
```

Then, from inside any session, just ask Claude in plain language: _"leave a note for the
next session in this project saying the config file was renamed"_, or _"run
`ccst doctor --drift` every Monday morning"_, or _"send a message to
~/repos/my-other-project about ..."_. The installed skills and `CLAUDE.md` sections teach
Claude how to use `ccmsg` and `ccsched` for you.

## Sessions: `ccd`, `ccr`, `ccs`

Three commands cover starting, resuming and finding your sessions. Run them from
inside a project directory under one of your configured roots.

**`ccd <tag>`** starts a new session and scaffolds `cc-sessions/<yyyyMMdd>-<tag>/` with
empty `working/` and `out/` subdirectories. Tags use letters, digits and dashes. `--dry-run`
shows what it would do; extra arguments are passed through to `claude`.

```sh
ccd fix-login-bug
# -> Claude Code, session 20261015-fix-login-bug (if today is 2026-10-15)
```

**`ccr <fragment>`** resumes a session by fuzzy name match. You do not need the exact tag or
date; if several sessions match you get an interactive picker. If a session was forked
(Ctrl-L), `ccr` helps you pick the right transcript. `--include-orphans` also finds
transcripts that have no `cc-sessions/` directory.

```sh
ccr login-bug
```

**`ccs`** (and `ccl`, its shell-function wrapper) lists and searches sessions:

```sh
ccs --global                              # every session, across every project
ccs login --global                        # by name
ccs "rate limit" --contents --global      # full text in working/ and out/
ccs "stack trace" --messages              # full text of transcripts in the current project
ccs --order-by active -n 5 --global       # your 5 most recently active sessions anywhere
ccs --since 2026-10-01 --emptiness exclude --json --global
```

Filters cover date ranges (`--since`, `--before`, `--days`), sessions you never typed in
(`--emptiness`), and hook-spawned review sessions (`--exclude-hooks`). Output can be JSON or
null-delimited for `xargs -0`. Set `CCS_DEFAULT_GLOBAL=1` to make `--global` the default.

### `working/` and `out/`: draft, then finalise

Each session directory gets two folders:

```
cc-sessions/20261015-fix-login-bug/
├── working/        # drafts, scratch notes, WORKLOG.md, intermediate data
└── out/            # the confirmed final artefact
```

Drafts and iterations accumulate in `working/`; only the finished deliverable goes in
`out/`. Nothing on disk enforces this, but the managed `workflow` `CLAUDE.md` section
teaches Claude to do it, and `ccs --contents` and `pdata reconcile-session-output` rely on
it. The same section asks Claude to keep a `working/WORKLOG.md` (timestamped entries), and
the `worklog-guard` hook blocks a manual `/compact` while that log is stale, so a compaction
cannot silently lose your session history.

### Where subagents write

A subagent Claude dispatches gets its own folder, `agents/<tag>--<task-slug>/`, inside the
session directory, with the exact prompt in `prompt.md` and a running `WORKLOG.md`, so a
large or parallel task leaves an audit trail instead of filling the chat. This is taught by
the managed `agents` section.

### Recurring routines get a fresh session each time

A weekly review is not one long-lived session; it is a new dated session each time, sharing
a suffix so the series is easy to find:

```sh
ccd myproject-weekly-review        # -> 20261001-myproject-weekly-review
ccd myproject-weekly-review        # a week later: 20261008-myproject-weekly-review
ccs myproject-weekly-review --global   # lists every occurrence
```

### Billing a session to an API key: `ccdapi`, `ccrapi`

`ccdapi` and `ccrapi` behave like `ccd` and `ccr`, but launch Claude Code with an Anthropic
API key instead of your subscription. Pick the key with `-k <label>`, or omit `-k` to choose
from a numbered menu:

```sh
ccdapi -k work fix-login-bug
ccrapi login-bug              # prompts for which key label to use
```

Keys live in `~/.config/ccst/api-keys` (override with `CCST_API_KEYS_FILE`), one `label=key`
per line, `#` comments allowed. The file must be `chmod 600` and is parsed, not sourced.
Keep it out of `~/.claude` and your shell rc files. Start from the value-free template
`api-keys.example`, bundled under `cc_session_tools/config/`.

### Session housekeeping

- `ccst sessions list` shows what `sessions.db` knows.
- `ccst repair sessions` fixes rows that `ccl`/`ccs --global` cannot see.
- `ccst gc report` finds orphaned data left behind by deleted sessions;
  `ccst gc prune --execute` removes it. A bundled job runs the report weekly.
- Skills (below) cover deleting empty sessions, moving or renaming a session, and archiving
  hook-review sessions.

## Sessions talking to each other: `ccmsg`

Leave a message for a session that is not open right now. The most common use is "whoever
next works on this project", delivered automatically the next time a matching session
starts:

```sh
ccmsg send --to-project myproject \
  --subject "heads up: shared config file renamed" \
  --body "renamed config/settings.yaml -> config/app.yaml, update references"

# in a later session opened in the same project (also done by a SessionStart hook):
ccmsg deliver
ccmsg list
ccmsg read <id>
ccmsg claim <id>      # first session to claim wins, for description-addressed messages
```

Three ways to address a message:

- `--to-project <name>` - any session in that project (by far the most common).
- `--to-session <session name or uuid prefix>` - one specific session, for example
  `--to-session 20261015-fix-login-bug` or `--to-session 3f9c1a2b`. An ambiguous or unknown
  reference is rejected and nothing is stored.
- `--to-description "whoever is working on the billing migration"` - a free-text proposal
  that the first interested session claims.

You rarely type any of this. Tell your session _"send a message to the myproject session
about X"_ and the installed `send-session-message` skill does the rest.

If you send a message to a session that is open the hooks will flag its appearance to it
(and you) at the beginning of its next response to you.

## Scheduled jobs: `ccsched`

Recurring jobs without a cron daemon. They are reconciled and caught up the next time you
open Claude Code, rather than run in the background while your laptop sleeps. A catch-up
digest at session start tells you what ran, what failed, and what was skipped.

```sh
ccsched add --id nightly-lint --cadence 'daily@22:00' -- npm run lint
ccsched list           # jobs and when each is next due
ccsched status         # recent run history
ccsched show nightly-lint
ccsched run nightly-lint     # run one now
ccsched sweep          # reconcile, run anything due, surface results (also automatic at session start)
```

- **Cadences**: `every:<duration>` (for example `every:6h`, `every:2w`), `daily@HH:MM`,
  `weekly:<day>@HH:MM`, `monthly:<day>@HH:MM`, and `monthly:<weekday>#<n>@HH:MM`.
- **Safe by default**: a job that fails repeatedly auto-suspends instead of failing silently
  forever, and `ccsched add --success-exit-codes` lets a job declare which exit codes mean
  "ran fine".
- **Notifications**: set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to also get a Telegram
  push for every job outcome, including when no session is open.
- **Bundled jobs**: CCST registers its own upkeep with `ccst ccsched-jobs install` (part of
  `install-everything`): `pdata` verification and multi-machine sync, a weekly `doctor`
  drift check, a weekly orphan report, telemetry trimming, session-output reconciliation, a
  fortnightly reminder to curate the command-review cache, and a weekly sweep of hook-review
  sessions.

You can also just ask Claude: _"run this every weekday at 09:00 and tell me if it fails"_ -
the `manage-recurring-cc-jobs-using-ccsched` skill translates that into a `ccsched add`.

## Safety hooks

CCST installs hooks that sit in front of what Claude does. They are on by default after
`install-everything`.

- **`bash-hard-deny`** - every shell command passes a deny list first, with no bypass:
  deletes (`rm` and friends, including inside inline scripts), delete-by-move into temp or
  trash locations, force-deleting git branches, deleting remote branches, mutating `gh` and
  `curl` calls, `sudo`, and similar. Everything else is auto-approved so routine compound
  commands do not stall on prompts.
- **`bash-security-review`** - a tiered review of shell commands: a fast allowlist and
  command cache, read-only pre-filters and heuristics, and an LLM-backed review only for
  anything unusual. Results are cached so the common case stays fast. Use
  `ccst hooks allowlist list` to see what is pre-approved and the `update-command-cache`
  skill to curate the cache.
- **`confirm-8digit`** - for tools you choose to gate, Claude must show you a fresh random
  8-digit code and you type it back in your own message before the call runs. Gate tools
  with `CCST_CONFIRM_8DIGIT_GATED_TOOLS="tool_a,tool_b"` (none are gated by default). A code
  that came from another agent or a file never counts. The author uses this to stop Claude
  Code from merging PR's without consent.
- **`context-window-warning`** - a non-blocking nudge toward `/compact` at 150k and 200k
  tokens. Silence it for a session with `/context-override`.
- **`worklog-guard`** - blocks a manual `/compact` when the session's `WORKLOG.md` is stale.

```sh
ccst hooks install               # dry run
ccst hooks install --apply       # merge CCST's hooks into ~/.claude/settings.json
ccst hooks uninstall --apply     # remove only CCST's entries; yours are never touched
```

### Other hooks

These keep the rest of CCST working without any action from you:

- `session-tag` - tells Claude the session tag and directory when launched via `ccd`/`ccr`.
- `after-response` - records last-activity time for `ccs --order-by active`.
- `messaging-deliver` - delivers `ccmsg` messages at session start and each prompt.
- `catchup` - catches up missed scheduled jobs and shows the digest.
- `pdata-sync` - syncs `pdata` databases across your machines (see below).
- `pending-migration` and `pending-rename` - surface unfinished data migrations and
  half-finished session renames.
- `last-screenshot` - type `>lss` in a prompt and Claude gets the path of your newest
  screenshot (set `CCST_SCREENSHOT_DIR`; several directories may be separated by `:`).
- `marker-allow` - lets skills drop small marker files without a permission prompt.

## Per-project data store: `pdata`

Projects accumulate CSVs, JSON files and markdown logs that drift out of sync and cannot be
queried. `pdata` gives each project one SQLite database instead, while deliberately leaving
prose and free-form notes as ordinary files. It lives under your
`CLAUDE_SESSION_TOOLS_PROJ_ROOT`.

```sh
ccst pdata readiness-scan --project myproject   # read-only: what would block a migration?
ccst pdata init --project myproject             # dry run: classifies each file
ccst pdata init --project myproject --write     # migrate (backs up first, verifies after)

ccst pdata add --project myproject --group invoices --content '{"status": "open", "amount": 120}'
ccst pdata query --project myproject --group invoices --where 'status = "open"'
ccst pdata schema list --project myproject
```

- **Typed fields on demand** - records carry a flexible content field, and
  `ccst pdata schema add-field` adds typed columns when a record shape settles.
- **Safe edits** - soft deletes, optimistic-concurrency versioning (a stale `update` or
  `delete` exits non-zero rather than clobbering), and `ccst pdata verify` as an integrity
  backstop (also run daily by a bundled job).
- **Multi-machine sync** - each project's database is published as a deterministic SQL dump
  inside the project folder, so any folder-sync service carries it between your machines. A
  vector clock per project means a genuine fork (two machines both edited) is flagged for
  your choice with `ccst pdata resolve`, never silently merged. Each machine has a confirmed
  identity (`ccst machine-identity show|confirm`).
- **New projects start well** - the first `pdata init` for a new project seeds starter
  folders and a `CLAUDE.md` with date-format and line-ending conventions. An existing
  `CLAUDE.md` is never touched.
- **Date hygiene** - writing a non-ISO date into a date-like field prints a warning, and
  `readiness-scan` flags dotted, compact, slash and long-form date columns before you
  migrate.
- **Session-output index** - `ccst pdata reconcile-session-output` indexes every session's
  `out/` deliverables across projects (a bundled job runs it weekly).

The `pm-pdata-*` skills walk Claude through the whole lifecycle: auditing a project,
migrating it, designing schemas for new record shapes, resolving conflicts, and rewriting
skills and docs that pointed at the old flat files.

## Usage and cost analytics: `claude-code-usage`

```sh
claude-code-usage query --group-by model,day --format markdown
claude-code-usage report --since 2026-10-01 --output report.md
claude-code-usage children <session-uuid-or-name>   # hook and subagent sessions under a parent
claude-code-usage reconcile               # cross-check totals against the independent ccusage tool
```

Groups by project, session, model, MCP server, plugin, tool or time period, in tokens and
dollars (using Anthropic's published prices). It separates subagent cost, already counted in
the parent session, from hook-review cost, billed separately, so totals are never
double-counted. Ask Claude _"analyse my Claude Code usage this month"_ and the
`analyse-cc-usage` skill runs these for you. `cccs-stats` reports the efficiency of the
command-review cache.

## Bundled skills

26 skills are installed into `~/.claude/skills/` by `ccst skills install --apply` (part of
`install-everything`). Claude loads each one when its description matches what you ask for.
Each lives in `src/cc_session_tools/skills/<name>/SKILL.md`.

- **Finding and managing sessions**: `find-claude-code-session`, `list-empty-sessions`,
  `delete-sessions`, `move-session`, `clean-hook-sessions`
- **Cross-session coordination**: `send-session-message` (for `ccmsg`),
  `manage-recurring-cc-jobs-using-ccsched`
- **Context and cost**: `compact-smart` (drafts the `/compact` instruction that preserves
  what matters), `context-override`, `reduce-persistent-context` (measures and trims the
  fixed per-session context footprint), `analyse-cc-usage`
- **Per-project data (`pdata`)**: `pm-pdata-do-init`,
  `pm-pdata-do-audit-and-prepare-to-migrate`, `pm-pdata-design-schema`,
  `pm-pdata-resolve-conflicts`, `pm-pdata-do-update-consuming-skills`,
  `pm-pdata-do-update-project-docs`, `pm-project-layout-reference`,
  `pm-update-central-files` (end-of-session wrap-up), and the deprecated fallback
  `pm-pdata-do-manual-migration`
- **Multi-agent patterns**: `select-agent-model` (which model tier for a subagent),
  `do-executor-critic-assessor-loop` (an iterate-and-critique quality loop)
- **Document extraction**: `extract-to-text` (docx, doc, rtf, odt, pptx, eml, msg, pdf
  including scanned pages via OCR, xlsx, xls) and `extract-pptx`
- **Safety**: `generate-8digit-code`, `update-command-cache`

`ccst skills install` and `ccst skills uninstall` manage them individually if you want a
subset.

## Teaching Claude the conventions: `claude-md`

The conventions above are not magic; they are taught. `ccst claude-md install` inserts
small, sentinel-delimited sections into your global `~/.claude/CLAUDE.md`, each
independently installable:

```sh
ccst claude-md install --apply                      # every section
ccst claude-md install --section workflow --apply   # just one
ccst claude-md uninstall --section agents --apply   # remove one
ccst claude-md backups list                         # saved copies of hand-edited sections
```

| Section        | Teaches Claude                                                   |
| -------------- | ---------------------------------------------------------------- |
| `messaging`    | to use `ccmsg` proactively to leave notes for other sessions     |
| `workflow`     | the `working/` to `out/` convention and a mandatory `WORKLOG.md` |
| `agents`       | the per-subagent `agents/<tag>--<slug>/` folder convention       |
| `confirm-gate` | how the 8-digit confirmation gate works                          |
| `date-format`  | `yyyy.MM.dd` in filenames, `yyyy-MM-dd` everywhere else          |

Each is a plain block you can read, edit or override. If you hand-edit a managed block, the
next install saves your version first (`ccst claude-md backups show <id>`), and
`ccst doctor` warns you before that happens.

## Command reference

`ccst <noun> <verb>` is the administrative umbrella. Run `ccst <noun> --help` for every
flag.

| Noun                 | What it is for                                                            |
| -------------------- | ------------------------------------------------------------------------- |
| `install-everything` | run every install step, then a health check (dry run unless `--apply`)    |
| `doctor`             | health check; `--all`, `--drift`, `--mute NAME`, `--list-mutes`           |
| `hooks`              | install/uninstall hooks, manage the review allowlist, `run <name>` a hook |
| `skills`             | install/uninstall the bundled skills                                      |
| `claude-md`          | install/uninstall the managed `CLAUDE.md` sections, list/show backups     |
| `shell`              | install/uninstall the `ccl` shell function                                |
| `ccsched-jobs`       | install the bundled scheduled jobs                                        |
| `pdata`              | the per-project data store (see above)                                    |
| `machine-identity`   | show/confirm this machine's identity for `pdata` sync                     |
| `sessions`           | inspect `sessions.db`; one-shot schema migrations                         |
| `repair`             | repair known `sessions.db` corruption                                     |
| `gc`                 | `report` and `prune` orphaned per-session data                            |
| `telemetry`          | `query` and `trim` the hook-invocation log                                |
| `migrate`            | one-shot migrations from legacy flat-file stores to SQLite                |
| `context-override`   | toggle the context-window warning for the current session                 |

Other commands: `ccd`, `ccr`, `ccs`/`ccl`, `ccdapi`, `ccrapi`, `ccmsg`, `ccsched`,
`claude-code-usage`, `cccs-stats`. Every command has `--help` and `--version`.

## Configuration

Nothing here is required beyond the two root variables in [Install](#install).

| Setting                                  | Purpose                                                                      |
| ---------------------------------------- | ---------------------------------------------------------------------------- |
| `CLAUDE_SESSION_TOOLS_REPO_ROOT`         | loose session root (code repositories)                                       |
| `CLAUDE_SESSION_TOOLS_PROJ_ROOT`         | strict `<project>-<label>` session root; also where `pdata` projects live    |
| `CCST_CONFIRM_8DIGIT_GATED_TOOLS`        | comma-separated tool names that require the 8-digit code (none by default)   |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram push for scheduled-job outcomes (also read from `~/.creds`)         |
| `CCST_SCREENSHOT_DIR`                    | directory (or `:`-separated list) searched by the `>lss` token               |
| `CCST_API_KEYS_FILE`                     | alternative path for the `ccdapi`/`ccrapi` key file                          |
| `CCST_MACHINE_NAME`                      | explicit machine identity for `pdata` multi-machine sync                     |
| `CCS_DEFAULT_GLOBAL=1`                   | make `ccs`/`ccl` search globally by default                                  |
| `CCST_NO_AUTO_SYNC=1`                    | disable the automatic post-upgrade install sync (CI, bisecting, development) |

## Health checks, upgrades and uninstalling

- **`ccst doctor`** checks PATH, environment, `settings.json`, hooks, skills, data stores,
  version drift against PyPI, and whether the install is in sync. By default it prints only
  problems. Mute a known, accepted warning with `ccst doctor --mute <check>`. A bundled job
  runs `ccst doctor --drift` weekly so problems reach you without you asking.
- **Upgrading**: `uv tool upgrade cc-session-tools` (or `pip install -U cc-session-tools`).
  The next `ccst` command notices the new version and re-runs the install itself. Run
  `ccst install-everything` (dry run) any time to preview what it would change.
- **Data migrations**: a major version that changes on-disk data ships a `ccst doctor` FAIL
  and a one-shot `ccst migrate ...` command, and a SessionStart hook surfaces it. See
  [CHANGELOG.md](CHANGELOG.md) before a major upgrade.
- **Uninstalling**: `ccst hooks uninstall --apply`, `ccst skills uninstall --apply`,
  `ccst claude-md uninstall --apply` and `ccst shell uninstall --apply` remove what CCST
  added (everything is dry run until you pass `--apply`), then
  `uv tool uninstall cc-session-tools`. Your session directories and data stores are left in
  place.

## How it fits together

CCST is deliberately not a server or a shared team tool. It is local infrastructure for one
person running Claude Code hard, on one or more of their own machines. Everything is SQLite
under `~/.local/share/claude/`, one file per subsystem (sessions, messages, scheduler,
telemetry, command cache), and all of it is inspectable with `ccst telemetry query`,
`ccst gc report`, `ccst sessions list`, or a plain `sqlite3` shell. `ccst doctor` is the one
command that checks all of it at once. Run it any time something feels off.

## Contributing

Development uses git worktrees and `uv`; see `.claude/CLAUDE.md` in this repo for the
workflow, versioning policy and release process. Changes follow an OpenSpec proposal process
under `openspec/`. Release history is in [CHANGELOG.md](CHANGELOG.md).

## License

MIT
