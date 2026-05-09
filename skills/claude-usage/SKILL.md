---
name: claude-usage
description: Analyse Chris's Claude Code usage across projects, sessions, MCP servers, plugins, tools, time and models, in tokens AND dollars. Use whenever Chris asks about his Claude Code usage in any way - "how much have I spent", "tokens used last week", "which project costs the most", "how much did Opus cost in April", "MCP usage by project", "compare Sonnet and Opus", "show my sessions in oneshot last month", "how often am I using the github plugin", "give me a usage report for the quarter", "what tools do I lean on", "any spike in usage recently". Backed by the claude-code-usage-cli repo at ~/repos/claude-code-usage-cli (also on GitHub at raffishquartan/claude-code-usage-cli). Reconciles token totals against ccusage so we know the numbers are right.
---

# claude-usage

Use this skill whenever Chris asks anything about his Claude Code usage.
The CLI behind this skill (`claude-code-usage`) is the canonical source.
Run it behind the scenes and summarise the result in the chat - don't
make Chris look at raw markdown tables unless he asks for them.

## When to use

Trigger phrases (non-exhaustive):

- "how much have I spent on Claude Code"
- "tokens used [yesterday|this week|in April|...]"
- "[which|what] project [costs the most|uses the most tokens|...]"
- "MCP usage by project"
- "Opus vs Sonnet usage"
- "show my sessions in <project> last month"
- "how often am I using the <plugin> plugin"
- "full usage report for [quarter|year|...]"
- "any spike in usage recently"
- "do I have any expensive sessions"
- "what tools am I leaning on"

If Chris's question maps to one of those, invoke the CLI rather than
guessing or trying to read the JSONLs yourself.

## How to translate questions into CLI invocations

The CLI's grammar:

```
claude-code-usage query
  [--since YYYY-MM-DD] [--until YYYY-MM-DD]
  [--project NAME] [--session NAME_OR_UUID] [--model SUBSTR]
  [--mcp SERVER] [--plugin PLUGIN] [--tool TOOL]
  [--group-by D1,D2,...]
  [--format markdown|csv|json] [--top N] [--sort COL]
  [--session-format name|uuid|both]
  [--include-children]

claude-code-usage children <PARENT_SESSION>
  [--format markdown|csv|json] [--top N] [--sort COL]
```

`--group-by` dimensions: `project, session, model, mcp, plugin, tool,
day, week, month, year`. Combine freely.

When grouping by `session`, the output column defaults to the human
display name set via `claude -n` / `ccd <tag>` (e.g.
`20260509-oneshot-test-claude-usage-skill`). Sessions whose name we
have never seen in `~/.claude/sessions/*.json` (the live registry, which
is ephemeral) are rendered as `sess-<uuid8>`. Pass
`--session-format uuid` to get the full UUID instead, or
`--session-format both` for a `session_name` + `session_id` pair.

`--session` filters accept either a name (full or case-insensitive
substring) or a UUID (full or 4+ char prefix), so users who think in
names don't need to look up UUIDs.

Recipes:

| Chris asks                                            | Run                                                                                       |
|-------------------------------------------------------|-------------------------------------------------------------------------------------------|
| "How much have I spent in the last week?"             | `claude-code-usage query --since "$(date -d '7 days ago' +%F)"`                           |
| "Tokens by project last week"                         | `claude-code-usage query --since "$(date -d '7 days ago' +%F)" --group-by project`        |
| "Opus vs Sonnet usage in April"                       | `claude-code-usage query --since 2026-04-01 --until 2026-05-01 --group-by model`          |
| "MCP usage by project for Feb / Mar / Apr by month"   | `claude-code-usage query --since 2026-02-01 --until 2026-05-01 --group-by project,mcp,month` |
| "Sessions in oneshot in April 2026"                   | `claude-code-usage query --project oneshot --since 2026-04-01 --until 2026-05-01 --group-by session` |
| "Top sessions in coparenting by cost"                 | `claude-code-usage query --project coparenting --group-by session`                        |
| "What's the UUID for the test-claude-usage session?"  | `claude-code-usage query --session test-claude-usage --group-by session --session-format both` |
| "Show today's sessions with UUIDs"                    | `claude-code-usage query --since "$(date +%F)" --group-by session --session-format both`  |
| "How much does opentabs eat in tokens?"               | `claude-code-usage query --mcp opentabs --group-by month`                                 |
| "Which native tools cost the most?"                   | `claude-code-usage query --group-by tool` then filter to kind=native in your summary      |
| "Full report for Q2"                                  | `claude-code-usage report --since 2026-04-01 --until 2026-07-01 --output cc-sessions/<tag>/out/usage-report.md` |
| "Are my numbers right?" / "Reconcile against ccusage" | `claude-code-usage reconcile --since 2026-04-01 --until 2026-05-01`                       |
| "Show session cost including hook sessions"           | `claude-code-usage query --group-by session --include-children`                           |
| "What hook sessions did this session spawn?"          | `claude-code-usage children <session-name-or-uuid>`                                       |

After running, summarise the headline figure(s) in chat in plain English.
For complex breakdowns, save the full table to the session's `out/`
directory and reference the path in the summary.

## What's in the data

Each row of the fact table is one billable assistant message and carries:
`ts, session_id, project_name (= basename(cwd)), model, input_tokens,
cache_creation_5m, cache_creation_1h, cache_read, output_tokens,
tool_calls, message_id, ...`. Token-to-tool attribution splits a
message's tokens evenly across its `tool_use` blocks; tool-call counts
are tracked separately as a more reliable metric.

**Parent/child session grouping (v0.3.0):** The parser classifies each
session as `regular`, `hook`, or `subagent` and infers a `parent_session_id`
where possible. Hook sessions (bash security-review checks) are linked to
their parent when the `bash-security-review.sh` hook was able to embed a
session-name prefix in the prompt - this only happens when exactly one
`cc-sessions/` directory exists at the time the hook fires. Use
`--include-children` with `--group-by session` to fold hook session
tokens/cost into the parent row; use `children <session>` to list a
parent's children individually.

`ccusage` is the canonical source of dollar figures. Our `cost_usd` is
a self-consistent estimate that may differ - the `reconcile` sub-command
shows both side by side. **Token counts are guaranteed to reconcile with
ccusage within 0.5%**.

Session display names are sourced from `~/.claude/sessions/*.json`
(live registry, set by `claude -n`/`ccd`) and merged into a persistent
on-disk cache at `<cache_dir>/session_names.json` so any name we ever
saw is preserved after the live record is pruned. UUID-only fallback
(`sess-<uuid8>`) covers sessions launched without `-n`.

## Setup on a new machine

This block lives in the skill body (not the frontmatter / preview note)
so that it travels with the skill when it's deployed elsewhere.

The recipes above call `claude-code-usage` directly (not `uv run
claude-code-usage`). For that to work, the CLI must be on `PATH`. The
canonical install is via `uv tool install --editable .`, which drops a
shim into `uv tool dir --bin` (typically `~/.local/bin`). Editable mode
means a later `git pull` in the repo updates the installed CLI without
reinstalling.

```bash
# 1. Clone the repo
git clone https://github.com/raffishquartan/claude-code-usage-cli.git \
    ~/repos/claude-code-usage-cli
cd ~/repos/claude-code-usage-cli

# 2. Install the CLI globally (puts `claude-code-usage` on PATH)
#    Install uv first if needed: https://docs.astral.sh/uv/getting-started/
uv tool install --editable .
claude-code-usage --version    # should print the version
#    If `claude-code-usage` is not found, ensure `uv tool dir --bin`
#    is on your PATH (run `uv tool update-shell` or add it to your rc).

# 3. (Optional) project-local dev venv with test deps, for hacking on
#    the CLI. Not required for normal skill use.
uv sync --extra dev
uv run pytest    # all 47+ tests should pass

# 4. Install ccusage globally so reconciliation works
#    Install bun first if needed: https://bun.com/docs/installation
bun add -g ccusage
ccusage --version

# 5. Symlink the repo as a Claude Code skill
#    The skill name in ~/.claude/skills must match the frontmatter `name`
#    above (claude-usage), so use that as the link name regardless of
#    the repo directory name.
ln -s ~/repos/claude-code-usage-cli ~/.claude/skills/claude-usage

# 6. Warm the cache (first run is the slow one)
claude-code-usage warm-cache

# 7. Sanity-check by reconciling against ccusage
claude-code-usage reconcile
```

The repo can also be sourced from GitHub directly (via the upstream
remote) if you don't want to clone it - see the README's "Install"
section for that path.

## Performance notes

The mtime-keyed parquet cache makes re-runs sub-second:

- Cold parse of `~/.claude/projects` (1.7 GB / 7,256 files): ~11 s
- Warm with no changes: < 1 s
- Warm with a few new sessions since last run: ~3 s

Cache location: `~/.cache/claude-code-usage/parquet/` by default
(platformdirs user_cache_dir). Override with `--cache-dir`.

## Repo

- Local: `~/repos/claude-code-usage-cli`
- GitHub: <https://github.com/raffishquartan/claude-code-usage-cli> (public, MIT)
- Run `pytest` after any change. The schema sanity check is the early
  warning if Anthropic ever changes the JSONL format.
