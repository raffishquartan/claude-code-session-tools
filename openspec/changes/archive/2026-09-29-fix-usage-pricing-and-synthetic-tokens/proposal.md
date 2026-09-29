## Why

Three independent defects, plus one related correction, shipped together as one patch release:

1. **Usage cost reports use stale and missing prices.** `claude_code_usage`'s pricing table
   (`data/pricing.json`) still prices Opus 4.5-4.7 at the pre-4.5 $15 input / $75 output rate,
   Haiku 4.5 at $0.80 instead of $1, and has no rows for any Claude 5.x model. Opus 5.x and
   Opus 4.8 fall back to the Opus 4.7 row ($15/$75, roughly 3-4x the published $4-5/$20-25), Sonnet
   5 / 5.5 fall back to Sonnet 4.6 ($3/$15 instead of $2/$10), and Fable 5 / 5.1 and Mythos 5 have
   no row and no family match, so their cost is reported as $0. Every Claude Code session also
   records zero-usage `<synthetic>` assistant stubs (for example the "session limit reached"
   message), which have no row either, so the "no pricing for models" warning fires on every report
   and hides a genuinely unknown model.
2. **The context-window warning goes silent after a `<synthetic>` stub.** The hook takes the token
   count from the last non-sidechain transcript entry carrying a `usage` block. `<synthetic>` stubs
   carry an all-zero `usage` block, so when one is the last assistant entry the hook reads 0 tokens
   and stays silent even though the real context is far past the threshold (reproduced against
   real transcripts: 29 ended in this state). If it did fire it would also call the model
   "an unrecognized model".
3. **`pm-pdata-do-init`'s `file_path_column` pitfall misses a second unsafe case.** A column
   documented to hold several path notations or a shorthand (for example brace expansion) is as
   unsafe to map to `file_path_column` as a subfolder-relative one; mapping it produced the
   `File name too long` failure fixed in 3.9.3.

The related correction: the warning's "~$/turn in cache reads" estimate uses input prices that
disagree with the published figures for Sonnet 5 / 5.5 and Opus 5.5, and a flat 0.1x cache-read
multiplier that is wrong for Fable 5.1 (0.025x) and Opus 5.5 (0.05x). The warning also does not
recognise `claude-mythos-5-1`.

## What Changes

- `data/pricing.json` carries Anthropic's published first-party per-token rates (input, output,
  5-minute and 1-hour cache writes, cache reads) for every current model id and the retired ids it
  already listed, verified against the published pricing page on 2026-09-29, and a zero-cost row
  for `<synthetic>`.
- A dated snapshot id (`<id>-YYYYMMDD`) resolves to its undated row before any family fallback, so
  `claude-opus-4-20250514` is priced as Opus 4 and not as the newest Opus.
- Family fallback (for ids with no row) now points at the newest model in each family, covers
  Fable and Mythos, and only matches `claude-<family>-` ids, so an unlisted Claude 3 id or a
  non-Claude id is reported as unpriced instead of being given a newer model's price. A cost
  report names every model it priced by family fallback in a warning, so an estimated price is
  never silent. A model with no row and no family match still costs 0 with a warning naming it.
- The context-window warning ignores `<synthetic>` assistant entries when choosing the "last"
  usage, so the token count and model come from the last real API response.
- The warning's cost estimate uses each model's published cache-read price directly instead of
  0.1x an input price; Sonnet 5 / 5.5 and Opus 5.5 move to their published figures, and
  `claude-mythos-5-1` is recognised (1,000,000-token window, "Mythos 5.1").
- `pm-pdata-do-init`'s `SKILL.md` gains one line in the `file_path_column` pitfall covering columns
  with multiple or shorthand path notations.

## Capabilities

### New Capabilities
- `cli/usage-model-pricing`: how `claude-code-usage` turns a message's model id and token mix into
  a dollar cost, including dated ids, family fallback, unknown models and `<synthetic>` stubs.
- `hooks/context-window-warning-token-count`: which transcript entry the context-window warning
  measures.

### Modified Capabilities
- `hooks/context-window-model-recognition`: adds the per-model cache-read price used by the
  warning's cost estimate, and Mythos 5.1.
- `pdata/migration-guidance`: adds the multiple/shorthand path-notation case to the
  `file_path_column` pitfall.

## Impact

- `src/claude_code_usage/data/pricing.json`, `src/claude_code_usage/pricing.py`,
  `src/hooks/model_info.py`, `src/hooks/context_window_warning.py`,
  `src/cc_session_tools/skills/pm-pdata-do-init/SKILL.md`; tests `tests/test_pricing.py`,
  `tests/test_query.py`, `tests/test_model_info.py`, `tests/test_context_window_warning.py`.
- Cost figures in existing `claude-code-usage` reports change (Opus 4.5-4.8 and Sonnet 5.x down,
  Haiku 4.5 up, Fable/Mythos from $0 to their real cost). Token totals, and so the ccusage token
  reconciliation, are unaffected.
- `<synthetic>` stubs still count towards message totals in usage reports (their tokens and cost
  are zero); unchanged.
- Not modelled (unchanged): the 1.1x US-only inference multiplier, fast-mode premium pricing and
  batch discounts. Transcripts record `usage.speed` and `usage.inference_geo`, but pricing ignores
  them.
- CHANGELOG entry and patch version bump (3.10.3).
