## Context

`hooks/model_info.py` holds three lookup tables (window, price, name) keyed by exact model id,
with a prefix rule only for Haiku 4.5. Unknown ids fall to a deliberately conservative
200k default (documented in the module: quiet-or-early is worse than conservative-for-most-history).

## Goals / Non-Goals

**Goals:** recognise the three current 5.x ids; tolerate dated snapshots. **Non-Goals:** a
generic "any claude-*-N with N>=5 is 1M" rule (would silently assume windows for models nobody has
verified); changing thresholds; the separate statusline/config-sync tables.

## Decisions

1. **Exact ids added to the existing tables.** Follows the module's own "re-check this table
   whenever a new model ships" contract. Alternative rejected: family-prefix inference, above.
2. **Dated snapshots via one normalisation step**: `re.fullmatch(r"(.+)-\d{8}", id)` applied
   once (never looped), used by all three lookups so the rule cannot drift between them. Exact-or-
   dated only, so `claude-sonnet-5-9` and `claude-sonnet-5-9-20261001` stay unknown. Haiku 4.5
   becomes an ordinary table row and its `startswith` rule is removed (a dated Haiku id still
   resolves; only malformed ids like `claude-haiku-4-5-garbage` stop being recognised). `[1m]` and
   `-latest` are not handled: the hook reads `message.model`, where neither occurs (`[1m]` appears
   only in subagent `resolvedModel` metadata).
3. **Prices for the new ids are assumed equal to their family's existing price.** No published
   figure was available; the price feeds only the "~$/turn cache read" estimate (0.1x input
   price), so an error changes that estimate, not the percentage or window.
4. **New capability spec** `hooks/context-window-model-recognition`: no existing spec covers the
   warning's model handling.

## Risks / Trade-offs

- [Assumed prices are wrong] -> low impact (estimate only); called out in the CHANGELOG.
- [The next model release reintroduces the gap] -> inherent to an exact table; the docstring
  already says to re-check on release, and the conservative default keeps failure loud enough to
  notice (as it did here).
