# `ccst pdata init --write` usability follow-ups — design spec

Status: DRAFT, awaiting review. Source: inter-session messages from
`claude-resume-home-migration` (2026-08-20), found while completing `home`'s migration on the
fixed 2.7.1 build. The 2026-08-19 crash itself is already fixed
([[2026-08-19-pdata-write-robustness]]); these are follow-on polish found by actually using the
fixed build end-to-end.

## Scope

Four independent, low-risk usability fixes to the existing `--write` path. No behavioural change
to what gets classified, imported, or cut over — only what gets printed/logged and what a backup
file is named.

1. Explicit success/failure sentinel — the log's last line must say, unambiguously, whether the
   run succeeded.
2. Per-run logging to `<project>/ccst-pdata-init-write.log` — already correct, confirmed on a real
   run. No code change; call this out as verified-no-action in the plan.
3. Print the self-verify command (`ccst pdata verify --project <name> --full`) at the end of a
   successful `--write`.
4. Human-readable backup filenames.

## Design

### 1. Explicit success/failure sentinel

Today `_cmd_pdata_init` (`src/cc_session_tools/cli/ccst.py:1046`) has three exit paths inside the
`with write_log.WriteLog(project_root):` block, all of which are plain `return`s (not raised
exceptions) — so `WriteLog.__exit__` sees `exc is None` on every one of them and can't
distinguish success from a reported failure:

- `except (FileNotFoundError, ValueError)` → prints to stderr, `return 2`.
- `write_result.failure is not None` (verification failed) → prints reasons to stderr, `return 1`.
- success → prints the report, `return 0`.

Plus a fourth path `WriteLog.__exit__` already handles: a genuinely unhandled exception escaping
the block, which it already captures as a traceback written to the log file
(`src/cc_session_tools/lib/pdata/write_log.py:80-102`).

Fix, two parts:

- Add an explicit `print("SUCCESS")` / `print(f"ERROR: {reason}")` at each of the three known exit
  points in `_cmd_pdata_init`, right before the `return`. These already flow through
  `write_log._Tee` into the log file for free — no new plumbing needed, just a line at each site.
- `WriteLog.__exit__` gets a matching line for the fourth (unhandled-exception) path: when
  `exc is not None`, after writing the traceback, write
  `f"ERROR: {exc_type.__name__}: {exc}\n"` to `self._file` directly (same best-effort
  `try/except OSError: pass` pattern already used for the traceback write — never let the sentinel
  write itself crash the exit path).

Net effect: every `--write` run's log ends in exactly one of `SUCCESS` or an `ERROR: ...` line,
regardless of which of the four paths it took. A log with neither is itself a signal something
outside this contract went wrong (e.g. the process was killed before `__exit__` ran) — that's
already the intended behaviour, not a gap this fix needs to cover.

### 2. Per-run logging — no action

Confirmed working as-is on the 2026-08-20 `home` run. Not touched by this spec; listed for
completeness against the source message.

### 3. Print the self-verify command

`ccst pdata verify --project NAME --full` is a real, already-shipped command
(`src/cc_session_tools/cli/ccst.py:2121-2130`: `pdata verify` takes a mutually-exclusive
`--project`/`--all-projects` and a `--full` flag). Add one line after the existing
`print(write_result.report)` in the success path:

```python
print(f"\nVerify: ccst pdata verify --project {args.project} --full")
```

No new plumbing — `args.project` is already in scope at that point.

### 4. Human-readable backup filenames

`backup.py:95` currently names the archive `f"{project}-{int(time.time())}.tar.gz"` — e.g.
`home-1787231983.tar.gz`. `int(time.time())` is second-granularity, which is what gives today's
scheme its collision-resistance across reruns within the same minute.

Replace with a second-granularity human-readable timestamp, which preserves that exact
collision-resistance without needing a separate numeric tie-breaker (the source message's
caveat was specifically about a *minute*-granularity example like `home-20260820-1419.tar.gz`,
which does need one — a seconds-granularity format sidesteps the trade-off entirely):

```python
final_path = target_dir / f"{project}-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
```

giving e.g. `home-20260820-1419-07.tar.gz`. `time.strftime` with no `t` argument reads the current
local time same as `time.time()` did — matches existing precedent for local-vs-UTC in this file
(no other pdata code currently forces UTC for user-facing filenames).

Two existing tests glob on `demo-*.tar.gz` (`tests/pdata/test_backup.py:68,218`), which match
either format unchanged. No other code depends on the epoch value being parseable back out of the
filename (checked: `store.py`, `init_service.py`, `write_log.py` — none of them parse backup
filenames).

## Out of scope

- Any change to backup *content*, retry behaviour, or the `.db` snapshot — all handled by
  [[2026-08-19-pdata-write-robustness]], not reopened here.
- The two "reusable prompt" asks (CLAUDE.md/doc updates, consuming-skill updates) — separate spec,
  [[2026-08-20-pdata-migration-prompts-design]].
- The broader "how should ccst help set up `~/cc/<project>` consistently" design question — held
  per Chris, to be discussed directly (not via ccmsg) when this set of requirements is spec'd out.
