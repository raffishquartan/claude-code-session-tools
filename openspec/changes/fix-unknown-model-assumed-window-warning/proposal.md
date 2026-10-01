## Why

The context-window warning looks the transcript's model id up in an explicit table
(`src/hooks/model_info.py`). An id missing from the table is silently given a 200,000-token window
and the name "an unrecognized model", and the message then states a percentage and a dollar figure
as if they were facts. A `claude-sonnet-5-5` session on an installed ccst 3.9.1 (which predates the
3.10.2 table update) was told it was at "~81% of an unrecognized model's 200k-token window" at about
162k tokens; its real window is 1,000,000 tokens. The installed package is the one the Stop hook
runs, so a stale install produces exactly this failure, and nothing in the message said the window
was a guess or how to fix it.

Diagnosis (recorded here so the fix is evidence-led): the transcript's last real assistant message
carried the exact id `claude-sonnet-5-5`; the repo table already recognises it; the installed
3.9.1 resolves it to 200,000 / "an unrecognized model". Cause: stale install, not a different id
or alias. A sample of 150 recent transcripts showed only `claude-sonnet-5`, `claude-sonnet-5-5`
and `<synthetic>`, all handled by the current table, so no model rows need adding.

## What Changes

- When the model id is not in the table (unknown id, dated snapshot of an unknown id, or empty id),
  the warning says in plain words that the window is ASSUMED to be 200k, quotes the exact id (or says
  none was recorded), labels the percentage as assumed and unreliable, says the dollar figure uses
  an assumed default cache-read price, and names the file to update and the commands that refresh
  the installed ccst. The vague "an unrecognized model's 200k-token window" wording is removed.
- Known models keep their current concise wording unchanged.
- `model_info` gains `is_known(model_id)` so the hook branches on recognition explicitly instead of
  inferring it from a sentinel display name.
- The 150k / 200k thresholds and the 200,000 assumed window are unchanged.
- Patch release 3.10.4 (message wording only; no interface, flag or on-disk change).

## Capabilities

### New Capabilities

### Modified Capabilities
- `hooks/context-window-model-recognition`: the unknown-id, dated-snapshot and cost requirements
  define "unknown" via `is_known`; a new requirement adds the assumed-window wording, the exact
  (sanitised) id, an unreliable-percentage label, the assumed-price disclosure and the remediation
  pointer.
- `hooks/context-window-warning-token-count`: an empty model id on a real (non-synthetic) entry is
  explicitly covered by the same assumed-window behaviour.

## Impact

- `src/hooks/model_info.py`, `src/hooks/context_window_warning.py`; tests `tests/test_model_info.py`,
  `tests/test_context_window_warning.py`.
- CHANGELOG entry and patch bump to 3.10.4 (`pyproject.toml`, `uv.lock`).
- Operational: an installed ccst older than 3.10.2 keeps mis-reporting `claude-sonnet-5-5` until it is
  upgraded; for a PyPI install that is `uv tool upgrade cc-session-tools`.
