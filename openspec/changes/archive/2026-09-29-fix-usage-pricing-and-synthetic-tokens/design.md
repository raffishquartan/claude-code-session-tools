## Context

- `claude_code_usage.pricing.lookup()` resolves a model id by exact match on `data/pricing.json`,
  then by substring family match (`opus`/`sonnet`/`haiku`) to a fixed row, else `KeyError`.
  `add_cost_column()` turns a `KeyError` into a $0 cost plus one warning listing the models. The
  JSON was hand-curated in 2026-05 and never updated for the Claude 5 family or the Opus 4.5 price
  cut. The module docstring claims a LiteLLM `refresh()` that does not exist.
- `hooks/model_info.py` has its own flat input-price table used only by the context-window
  warning's rough "~$/turn in cache reads" estimate (0.1x input price). It is deliberately
  separate from `claude_code_usage.pricing`: the hook runs on every Stop event, and the usage
  package imports pandas.
- `hooks/context_window_warning._current_context_tokens()` keeps the last non-sidechain entry
  whose `message.usage` is a dict. Claude Code writes `<synthetic>` assistant entries (flagged
  `isApiErrorMessage`, `stop_reason: stop_sequence`, e.g. a usage-limit notice) with an all-zero
  usage block.

## Price source

All figures come from Anthropic's published pricing page
(https://platform.claude.com/docs/en/about-claude/pricing), fetched 2026-09-29, and agree with the
model table in the `claude-api` skill (cached 2026-09-25). Per million tokens (input / 5m write /
1h write / cache read / output):

| Model ids | Rates |
|---|---|
| Fable 5.1, Mythos 5.1 | 10 / 12.50 / 20 / 0.25 / 50 |
| Fable 5, Mythos 5 | 10 / 12.50 / 20 / 1 / 50 |
| Opus 5.5 | 4 / 5 / 8 / 0.20 / 20 |
| Opus 5, 4.8, 4.7, 4.6, 4.5 | 5 / 6.25 / 10 / 0.50 / 25 |
| Opus 4.1, Opus 4, Claude 3 Opus | 15 / 18.75 / 30 / 1.50 / 75 |
| Sonnet 5.5, Sonnet 5 | 2 / 2.50 / 4 / 0.20 / 10 |
| Sonnet 4.6, 4.5, 4, 3.7, 3.5 | 3 / 3.75 / 6 / 0.30 / 15 |
| Haiku 4.5 | 1 / 1.25 / 2 / 0.10 / 5 |
| Haiku 3.5 | 0.80 / 1 / 1.60 / 0.08 / 4 |

Every current model id is on the page, so no price in this change is assumed. The Claude 3
Opus and Sonnet 3.5 / 3.7 rows are retired models not on the current page; their existing figures
match the historical list price and are kept unchanged. The `claude-haiku-4` row (not a real
model id) is kept unchanged too: removing it has no effect on any real id.

## Decisions

1. **Keep `pricing.json` hand-curated; fix its provenance fields.** No refresh script or pinning
   test exists, and none is added: `_source` / `_refreshed_at` name the published page and the
   date checked, and the stale `refresh()` sentence in the module docstring is removed. Per-token
   floats keep the file's existing plain-decimal notation.
2. **Dated snapshots resolve before family fallback**, using the same "exactly eight trailing
   digits, applied once" rule as `hooks/model_info.py`. Without it, moving the Opus fallback to
   the newest Opus would reprice `claude-opus-4-20250514` / `claude-opus-4-1-20250805` from $15
   to $4. A `claude-opus-4-1` row is added for the same reason.
3. **Family fallback targets the newest model per family** (`fable`->Fable 5.1,
   `mythos`->Mythos 5.1, `opus`->Opus 5.5, `sonnet`->Sonnet 5.5, `haiku`->Haiku 4.5), and matches
   only `claude-<family>-` (previously any substring). Every Claude 4+ release has a row, so an
   unlisted id of that shape is most likely a newer release, for which the newest known price is
   the best estimate. Unlisted Claude 3 ids (`claude-3-haiku-20240307`,
   `claude-3-sonnet-20240229`, ...) are older than the table: with the old bare-substring match
   they would get the newest model's price (e.g. Haiku 3 at 4x its real rate), so they now fall
   through to "unpriced" instead, as does any non-Claude id that happens to contain a family word.
   Rows for those retired Claude 3 ids are not added: they are not on the current pricing page and
   Claude Code does not use them. Provider-prefixed ids (for example a Bedrock
   `...anthropic.claude-opus-4-1-...-v1:0`) still reach the fallback and are named in its warning.
   Because a fallback price is an estimate, `add_cost_column()` logs a warning naming each model it
   priced by fallback and the row used. `lookup()` still returns only the rates; a new
   `resolve()` returns the row key used and whether it was a fallback, so the warning does not
   duplicate the resolution logic.
4. **`<synthetic>` gets an explicit zero row**, not a special case in code: it is a real,
   recurring `message.model` value whose correct cost is zero, and listing it keeps the
   unknown-model warning meaningful. Unknown models keep the existing documented behaviour
   (cost 0, a warning naming them).
5. **Align `model_info`'s estimate with the published prices, still as its own table.** The table
   now stores each model's cache-read price per million tokens (Fable 5 / Mythos 5 $1, Fable 5.1 /
   Mythos 5.1 $0.25, Opus 5 / 4.6-4.8 $0.50, Opus 5.5 $0.20, Sonnet 5 / 5.5 $0.20, Sonnet 4.6 $0.30, Haiku
   4.5 $0.10) and gains `claude-mythos-5-1` (1M window) so every Fable/Mythos id priced in
   `pricing.json` is also recognised; `input_price_per_mtok()` becomes `cache_read_price_per_mtok()`; the warning
   multiplies tokens by it directly. A flat 0.1x multiplier cannot express Fable 5.1's 0.025x or
   Opus 5.5's 0.05x. The unknown-model default stays at the previous effective rate ($0.50, i.e.
   0.1x $5). Reusing `claude_code_usage.pricing` was rejected: it would pull pandas into a hook
   that runs on every Stop event. The two tables can drift again; a test asserts that every id in
   `model_info` has the same cache-read price in `pricing.json`, which catches that.
6. **The warning skips `<synthetic>` entries by model id, not by all-zero usage.** The model id is
   how Claude Code marks an entry that is not an API response (API-error stubs and others alike;
   `isApiErrorMessage` alone would miss some). An all-zero usage from a real
   model would be a real (if odd) measurement and is left alone. A transcript with only synthetic
   entries behaves like a fresh session: `(0, "")`.
7. **New capability `hooks/context-window-warning-token-count`** rather than extending
   `hooks/context-window-model-recognition`: that capability is about mapping an id to window,
   name and price; which transcript entry is measured is a separate concern that was not specified
   anywhere.

## Risks / Trade-offs

- [Published prices change again] -> the table is dated in `_refreshed_at` and the docstring says
  to re-check on each model release, as `model_info` already does.
- [Existing cost reports change noticeably] -> intended; called out in the CHANGELOG.
- [Fast mode, US-only inference and batch rates are not modelled] -> unchanged pre-existing gap,
  listed in the proposal; costs for such messages are understated.
