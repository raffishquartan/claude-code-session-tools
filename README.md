# cc-session-tools (CCST)

[![PyPI](https://img.shields.io/pypi/v/cc-session-tools.svg)](https://pypi.org/project/cc-session-tools/)
[![Python](https://img.shields.io/pypi/pyversions/cc-session-tools.svg)](https://pypi.org/project/cc-session-tools/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Infrastructure for running Claude Code as your default way of working, not just an
occasional tool.**

If you open Claude Code a few times a day across a handful of projects, you don't need
this. If you run a dozen named sessions a day across a dozen projects, expect to pick up
exactly where you left off three weeks ago, want two sessions to leave notes for each
other, and want scheduled housekeeping to just happen - CCST is the layer that makes that
sustainable. It replaces "which terminal tab was that in" and "let me grep my shell
history" with sessions you can name, find, resume, message, and schedule like real
infrastructure.

## What you get

- **Named, findable sessions** - `ccd` starts a session under a tag you choose, `ccr`
  resumes it later by that tag, and `ccs` searches across all of them by name, contents,
  or transcript text.
- **A per-session scratch space** - every session gets its own `working/` and `out/`
  directories, so drafts and deliverables don't get lost when the conversation ends.
- **Cross-session messaging** - one session leaves a note for "whoever next works on
  project X"; it's there waiting, even if that's a different session on a different day.
- **A local job scheduler** - recurring housekeeping and periodic routines run themselves,
  reconciled the next time you open Claude Code.
- **A structured per-project data store** (`pdata`) - stop losing track of things in
  drifting CSVs and markdown logs.
- **A safety layer** - a hard block on destructive commands, tiered LLM review for
  everything else, and an 8-digit confirmation gate for anything genuinely high-stakes.
- **24 bundled skills** covering session hygiene, data-store workflows, usage analysis,
  document extraction, and multi-agent orchestration patterns.
- **Token/cost analytics** over your own Claude Code transcripts, cross-checked against an
  independent tool so the numbers are trustworthy.

## Install

```sh
uv tool install cc-session-tools   # or: pip install cc-session-tools
ccst install-everything --apply    # wires hooks, skills, shell functions, and
                                   # standard scheduled jobs into ~/.claude
ccst doctor                        # confirms everything's wired up correctly
```

Requires Python 3.11+. `install-everything` is idempotent - safe to re-run after every
upgrade, and it's also what happens automatically the first time you run any `ccst`
command from a newer version than the one currently installed.

## The daily workflow: `ccd`, `ccr`, `ccs`

Three commands cover almost everything you do day to day.

**`ccd <tag>`** starts a new session and scaffolds `cc-sessions/<date>-<tag>/` with empty
`working/` and `out/` subdirectories, ready to use:

```sh
ccd fix-login-bug
# -> starts Claude Code, session tagged 20260928-fix-login-bug (if today is 20260928)
```

**`ccr <fragment>`** resumes an existing session by fuzzy name match - you don't need the
exact tag or date, and if more than one session matches you get an interactive picker:

```sh
ccr login-bug
# -> resumes 20260928-fix-login-bug, right where you left it
```

**`ccs` / `ccl`** (the same tool - `ccl` is the shell-function wrapper `ccst shell install`
sets up) searches and lists sessions by name, by file contents, or by full transcript text,
with date filters and multiple sort orders:

```sh
ccs --global                              # every session, across every project
ccs "rate limit" --contents --global      # full-text search across working/ and out/
ccs --order-by active --limit 5 --global  # your 5 most recently active sessions anywhere
```

### Billing a session to an API key: `ccdapi`, `ccrapi`

`ccdapi` and `ccrapi` behave exactly like `ccd` and `ccr`, but launch Claude Code with an
Anthropic API key instead of your subscription. Pick the key with `-k <label>`, or omit `-k` to
choose from a numbered menu of labels:

```sh
ccdapi -k work fix-login-bug
ccrapi login-bug              # prompts for which key label to use
```

Keys live in `~/.config/ccst/api-keys` (override with `CCST_API_KEYS_FILE`), one `label=key` per
line with `#` comments. The file must be `chmod 600`, is parsed rather than sourced, and should
stay out of `~/.claude` and out of your shell rc files. Start from the value-free template
`api-keys.example`, bundled in the package under `cc_session_tools/config/`.

### `working/` and `out/`: draft, then finalize

The convention this repo's own sessions follow: intermediate versions accumulate in
`working/`; only the confirmed, final artefact gets copied into `out/` (sometimes in a
different format from the draft - a markdown draft finalized as a formatted document, for
example). Nothing on disk enforces this - but it's exactly what the `workflow` CLAUDE.md
section below teaches Claude to do, and tooling like `ccs --contents` and `pdata
reconcile-session-output` assume it once it's there.

```
cc-sessions/20260928-fix-login-bug/
├── working/        # draft-v1.md, draft-v2.md, scratch notes, intermediate data
└── out/             # final-report.md - the thing you actually wanted
```

### Where agents write their files

The same pattern extends to subagents: a dispatched agent gets its own folder at
`agents/<tag>--<task-slug>/` inside the session directory, with its exact prompt recorded
in `prompt.md` and its progress in `WORKLOG.md`, so a large or parallel task leaves an
audit trail instead of filling up the chat. Again, nothing forces this - it's what the
`agents` CLAUDE.md section teaches.

### Recurring routines get a fresh tag each time

A periodic task (a weekly review, a daily sync check) isn't one long-lived session - it's
a new dated session each time it recurs, sharing a task-type suffix so it's easy to find
the whole series later:

```sh
ccd myproject-weekly-review        # -> 20260901-myproject-weekly-review
# ... a week later ...
ccd myproject-weekly-review        # -> 20260908-myproject-weekly-review
ccs myproject-weekly-review --global   # lists every occurrence
```

## Sessions talking to each other: `ccmsg`

Leave a message for a session that isn't open right now - most commonly, for "whoever
next works on this project," delivered automatically the next time a matching session
starts:

```sh
ccmsg send --to-project myproject \
  --subject "heads up: shared config file renamed" \
  --body "renamed config/settings.yaml -> config/app.yaml, update references"

# in a later, unrelated session opened in the same project:
ccmsg deliver     # also runs automatically via a SessionStart hook
ccmsg read <id>
ccmsg claim <id>  # first session to claim wins, for description-addressed messages
```

Messages can also be addressed to a specific session (`--to-session`) or to a free-text
description for whichever session picks it up first (`--to-description`) - project-
addressed is by far the most common pattern in practice.

The easiest way to use messages when you need them is just to tell your session to send 
a message to another session (e.g. by name tag and/or location).

## Scheduled housekeeping: `ccsched`

Recurring jobs that don't need a real cron daemon - they're reconciled and caught up the
next time you open Claude Code, not run in the background while your laptop is asleep.

```sh
ccsched add --id nightly-lint --cadence 'daily@22:00' -- npm run lint
ccsched status
ccsched sweep     # reconcile + run anything due + surface results; also runs at session start
```

Cadences support `every:<duration>`, `daily@HH:MM`, `weekly:<day>@HH:MM`,
`monthly:<day>@HH:MM`, and `monthly:<weekday>#<n>@HH:MM`. Jobs auto-suspend after repeated
failures rather than silently failing forever. CCST itself ships nine bundled jobs (data
verification, doctor drift checks, telemetry trimming, and similar upkeep) installed via
`ccst ccsched-jobs install`.

## `ccst`: the administrative CLI

Everything else lives under one umbrella command, `ccst <noun> <verb>`:

| Noun                  | What it's for                                                               |
| --------------------- | --------------------------------------------------------------------------- |
| `hooks`               | install/uninstall Claude Code hooks, manage the security-review allowlist   |
| `skills`              | install/uninstall the bundled skills into `~/.claude/skills/`               |
| `doctor`              | full health check across every subsystem; `--drift` for unmuted issues only |
| `pdata`               | per-project structured data store - see below                               |
| `gc`                  | find and clean up orphaned rows/directories from deleted sessions           |
| `telemetry`           | inspect and trim the hook-invocation log                                    |
| `sessions` / `repair` | maintain `sessions.db`; fix corrupted or ambiguous entries                  |
| `migrate`             | one-shot migrations from legacy flat-file storage to SQLite                 |
| `claude-md`           | install/uninstall CCST's managed sections in your global CLAUDE.md          |
| `install-everything`  | run every install step, then a full doctor check                            |

Run `ccst <noun> --help` for the full flag reference on any of these - the sections below
cover what each subsystem is _for_, not every flag.

### Safety: hooks that watch every command

Every `Bash` tool call passes through a hard deny list first (destructive deletes, force
pushes, branch deletion, `sudo`, and similar - no bypass, by design), then a tiered review
for everything else: a fast allowlist for routine commands, escalating to an LLM-backed
review only for anything unusual, with results cached so the common case stays fast.
Genuinely high-stakes actions (configurable per deployment, via the `confirm-gate` CLAUDE.md
section) require a fresh 8-digit code exchanged between Claude and you before they run.

```sh
ccst hooks install --apply       # wire the hooks into settings.json
ccst hooks allowlist list        # see what's pre-approved to skip LLM review
```

### Teaching Claude the conventions above: `claude-md`

The `working/`/`out/` and agent-folder conventions aren't magic - they're taught. `ccst
claude-md install` inserts small, sentinel-delimited sections into your global
`~/.claude/CLAUDE.md`, each independently installable:

```sh
ccst claude-md install --apply                    # install every section
ccst claude-md install --section workflow --apply  # just the working/out convention
ccst claude-md uninstall --section agents --apply  # remove one section
```

Current sections: `messaging` (how to use `ccmsg` proactively), `workflow` (the
`working/`→`out/` convention above), `agents` (the agent-folder convention above), and
`confirm-gate` (how the 8-digit confirmation gate works, for anyone who's turned it on), and
`date-format` (the date convention: `yyyy.MM.dd` in filenames, `yyyy-MM-dd` everywhere else).
`install-everything` installs all of them; each is a plain, human-editable block you can
read, remove, or override in your own CLAUDE.md at any time.

### `pdata`: stop losing track of things in drifting flat files

Projects accumulate CSVs and markdown logs that drift out of sync and can't be queried.
`pdata` gives each project one SQLite database instead - while deliberately leaving prose
and free-form notes as ordinary files, not everything has to move in.

```sh
ccst pdata init --write                       # classify and migrate a project's data files
ccst pdata add mygroup --content '{"status": "open"}' --file-path notes/item-1.md
ccst pdata query mygroup --where 'status = "open"'
ccst pdata sync-check --all-projects          # cross-machine sync, safe fast-forward only
```

Records support optional typed extension tables (`ccst pdata schema add-field`) alongside
a generic content field, soft deletes, and optimistic-concurrency versioning. Multi-machine
sync uses a vector clock per project: a genuine fork between two machines' edits is
flagged for a manual choice, never silently merged. Date-like fields (name ending `_at` or
`_date`, or "date" in the description) get a stderr warning when a non-ISO value is written, and
`ccst pdata readiness-scan` flags dotted, compact, slash and long-form date columns.

### Usage and cost analytics

```sh
claude-code-usage query --group-by model,day --format markdown
claude-code-usage report --since 2026-09-01 --output report.md
claude-code-usage reconcile      # cross-checks totals against an independent tool
```

Groups by project, session, model, MCP server, plugin, tool, or time period; separates
subagent cost (already counted in the parent session) from hook-review cost (billed
separately) so totals aren't double-counted either way.

### Skills

24 bundled skills, symlinked into `~/.claude/skills/` by `ccst skills install`, covering:

- **Session hygiene** - find, clean up, or permanently delete old sessions
- **`pdata` workflows** - schema design, project audits, migration, conflict resolution
- **Multi-agent patterns** - model-tier selection, an executor/critic/assessor quality loop
- **Document extraction** - PowerPoint, Word, PDF, email, and other formats to clean text
- **Cross-session coordination** - natural-language front ends for `ccmsg` and `ccsched`

Browse `src/cc_session_tools/skills/` for the full list - each ships its own `SKILL.md`.

## How it fits together

CCST is deliberately not a server or a shared team tool - it's local infrastructure for
one person running Claude Code hard, on one or more of their own machines. Everything is
SQLite under `~/.local/share/claude/`, one file per subsystem, all inspectable with
`ccst telemetry query`, `ccst gc report`, or a plain `sqlite3` shell if you want to look
yourself. `ccst doctor` is the single command that checks all of it is healthy at once -
run it any time something feels off.

## Contributing

Development uses git worktrees and `uv`; see `.claude/CLAUDE.md` in this repo for the full
workflow, versioning policy, and release process.

## License

MIT
