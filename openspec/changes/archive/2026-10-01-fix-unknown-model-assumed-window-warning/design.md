## Context

`context_window_warning.main` calls `model_info.context_window`, `cache_read_price_per_mtok` and
`display_name`; each falls back to a default row for an id not in the table, and the display
fallback is the string "an unrecognized model", which `_reason` interpolates into
"about N% of {name}'s {label}-token window". The caller cannot tell a known 200k model (Haiku 4.5)
from an unknown id, and the text reads as fact either way.

## Goals / Non-Goals

**Goals:** make the assumption unmistakable; surface the exact id; point to the remedy; keep known-model
output byte-identical.

**Non-Goals:** changing thresholds, the 200k assumed window, the fallback price, or hook registration;
auto-refreshing the install; reading model data from the network.

## Decisions

- **`model_info.is_known(model_id) -> bool`**: true iff `_canonical(model_id)` is in the name table
  (the table every other lookup keys on, so it cannot drift). An empty id is unknown.
- **`display_name` becomes strict**: `_DEFAULT_NAME` is deleted and `display_name` indexes the table
  directly, raising `KeyError` for an unknown id. The hook calls it only after `is_known`, so no
  caller needs a fallback string, and an invented default is not left behind to be misused.
- **Separate message builder for the unknown case** (`_reason_unknown`) rather than a flag threaded
  through `_reason`, so the known path stays untouched and its output is pinned by an exact-equality
  test.
- **Derived, not hardcoded, numbers**: the window label and the price in the unknown message come from
  the `window` and `price` the hook already resolved from `model_info` (which are the 200k / $0.50
  defaults when unknown), so the text cannot drift from the constants.
- **Id rendering**: the exact id is shown in backticks after replacing every character outside
  `[A-Za-z0-9._:<>\[\]-]` with `?`. Real ids contain only allowed characters and are quoted exactly; a
  hostile or corrupt transcript value cannot break the single-quoted lines the assistant is told to
  echo. An empty id is rendered as the unbackticked phrase `no model id was recorded`.
- **Required wording** (each substring must appear in BOTH the instruction text and the visible
  nudge line unless stated):
  - `ASSUMED`; `may be much larger`; the sanitised id in backticks (or `no model id was recorded`);
  - `unreliable` next to the percentage; `assumes the default $0.50/MTok cache-read price`
    (price derived); `200k` (derived);
  - remediation, visible line only: `src/hooks/model_info.py`, `uv tool upgrade cc-session-tools`
    and `uv tool install --reinstall <path to your checkout>`, introduced as "if installed from PyPI
    with uv" and "or from a local checkout" respectively.
  - Neither text contains "an unrecognized model" or "an unrecognised model".
- **Illustrative visible line**: `{emoji} CONTEXT ({now}): ~{k}k tokens used; window size ASSUMED (200k)
  for unknown model `<id>` - the real window may be much larger, so ~{pct}% is unreliable. ~${cost}/turn
  assumes the default $0.50/MTok cache-read price. This is a nudge, not a limit - keep going; /compact
  {when}. To fix: add the id to src/hooks/model_info.py in claude-code-session-tools; if ccst is just
  out of date, run `uv tool upgrade cc-session-tools` (installed from PyPI with uv) or `uv tool install
  --reinstall <path to your checkout>` (local checkout).`
- **No personal paths** in the hint: the checkout path is a placeholder, per the repo's
  no-personal-identifiers rule; a test asserts the hint has no `/Users/`, `/home/` or username.
- **Version:** patch (3.10.4). Observable change is the text of a nudge; no flag, schema or
  on-disk change, so not minor/major under the repo's policy.

## Risks / Trade-offs

- The hint's install commands can go stale if packaging changes -> they are in one string constant
  with a test, so a change is a single edit.
- Making `display_name` strict breaks any future caller that skips `is_known` -> intended: it fails
  loudly instead of printing a made-up name.
- A very long unknown id lengthens the message -> ids are short; no truncation, since the exact id is
  the point (only unusual characters are replaced).
- The remedy line is also shown when the id is genuinely new and no ccst release has it yet; the text
  covers both causes ("add the id ... or, if out of date, upgrade").
