## Context

`src/hooks/context_window_warning.py` reads the last non-sidechain, non-synthetic assistant usage from the transcript. Entries carry an ISO `timestamp` (UTC, `Z`) and `usage.cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`. `hooks.model_info` holds cache-read prices; it must not import pandas, so a test checks it against `claude_code_usage` pricing.

## Goals / Non-Goals

**Goals:**
- Show cache expiry time and cold-cache cost in both message variants.

**Non-Goals:**
- Changing thresholds, the override, or the window logic.
- Predicting the user's own idle time or auto-compacting.

## Decisions

- **Expiry = last response timestamp + lifetime.** A cache read refreshes the entry's lifetime, so the last response's time is the right anchor. The hook fires at Stop, right after that response. Alternative of "now + lifetime" was rejected: it is wrong whenever the hook runs late.
- **Lifetime from the transcript, not config.** `_current_context_tokens` is extended to also return the response timestamp and the lifetime: 1 hour if the most recent cache-writing response wrote any 1h tokens, else 5 minutes if it wrote 5m tokens; if the last response wrote nothing, the scan keeps the lifetime from the latest earlier writer; default 5 minutes. When a response wrote both, 1h wins because the 1h block is the one that outlives the other.
- **"Uncached" = cache-write price, not base input price.** Claude Code always requests caching, so a cold turn pays the write rate (1.25x input at 5m, 2x at 1h), not the plain input rate. Alternative (input price) understates the real bill by 25-100%.
- **Prices live in `model_info`** as `cache_write_price_per_mtok(model_id, ttl)` with the same canonicalisation and default rule as the read price; unknown models default to Opus-class write prices ($6.25 5m, $10 1h per MTok), consistent with the existing $0.50 read default. `tests/test_pricing.py` extends its parity check to the write prices.
- **Time display** is local `HH:MM`, like the existing `now`. The timestamp is converted with `astimezone()`. A response whose expiry falls on another day still shows only `HH:MM`; with a 1h lifetime that is unambiguous enough for a nudge.
- **Timestamp** is read only from the entry that supplies the usage. Real transcripts always carry one, so there is no fallback; `fromisoformat`'s `ValueError` is wrapped as `TranscriptError`, which `main` already reports on stderr with exit 1. Every existing test fixture gains a timestamp through one shared helper.
- **Return shape**: `_current_context_tokens` returns a small frozen dataclass (`tokens`, `model`, `responded_at`, `cache_ttl`) instead of a tuple; existing assertions are updated. A fresh session returns zero tokens, empty model, and the other fields unused.
- **Lifetime with no breakdown**: a write with a total but no `cache_creation` object is treated as 5 minutes (the API default). Real data has one such entry in about 89k, so this is a rare degraded case.
- **Accuracy**: the Stop hook's continuation response refreshes the cache a few seconds after the anchor, and entry timestamps are stream-time, so the shown expiry is slightly early, never late.

## Risks / Trade-offs

- [Cache already expired when the hook fires, e.g. very late] → the printed expiry is then in the past, which is itself accurate.
- [Lifetime unknown because no breakdown exists] → 5 minutes: the expiry shows early and the uncached price uses the lower write rate. Rare in real data (about 1 in 89k).
- [Prices drift] → parity test fails on drift, as for read prices.
