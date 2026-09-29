## Why

The context-window warning hook phrases its nudge as a percentage of the running model's real
window. It looks the model id up in an explicit table, and any id not in the table is treated as
having the 200,000-token pre-Claude-5 window. Claude Sonnet 5.5 reports itself as
`claude-sonnet-5-5`, which is not in the table, so a Sonnet 5.5 session is told it is at "133% of
an unrecognized model's 200k-token window" at 266k tokens when it has used about 27% of a
1,000,000-token window (observed live in this session's own warning; `claude-sonnet-5-5` and
`claude-opus-5-5` are confirmed as real transcript `model` values). The table also lacks the other two current 5.x ids,
`claude-opus-5-5` and `claude-fable-5-1`, so they will show the same wrong percentage.

## What Changes

- Add `claude-sonnet-5-5` (1,000,000 tokens, "Sonnet 5.5"), `claude-opus-5-5` ("Opus 5.5") and
  `claude-fable-5-1` ("Fable 5.1") to the model table with 1,000,000-token windows.
- A dated snapshot id (`<known-id>-YYYYMMDD`) resolves to its undated family row instead of the
  200k default; this replaces the one existing prefix rule (Haiku 4.5), so recognition is now
  exact-or-dated everywhere. An id that merely begins with a known id (for example an unreleased
  `claude-sonnet-5-9`, or `claude-haiku-4-5-garbage`) is unrecognized.
- The per-turn cache-read cost estimate for the new ids reuses each family's existing input price
  (Sonnet $3, Opus $5, Fable $10 per million tokens); these prices for the new ids are an
  assumption, not a published figure, and only feed a rough "~$/turn" estimate.
- The warning's absolute trigger thresholds (150k orange, 200k red) are unchanged; only the
  percentage, window label and model name in the message change.

## Capabilities

### New Capabilities
- `hooks/context-window-model-recognition`: how the context-window warning resolves a model id to
  a window size, display name and price, including the fallback for unknown ids.

### Modified Capabilities

## Impact

- `src/hooks/model_info.py`; tests `tests/test_model_info.py`, `tests/test_context_window_warning.py`.
- Known related gaps deliberately left for follow-ups: `claude_code_usage`'s pricing table
  (stale Opus 4.x/5.x prices that contradict this module's, and no rows for Fable/Mythos 5.x, so
  cost reports overstate Opus and report zero for Fable); the warning hook resetting to zero on a
  trailing `<synthetic>` assistant entry; and the separate statusline fork and config-sync repo,
  which keep their own model tables outside this repo.
- CHANGELOG entry and patch version bump (3.10.2).
