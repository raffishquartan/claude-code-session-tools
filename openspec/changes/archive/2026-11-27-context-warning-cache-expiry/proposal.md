## Why

The context-window warning shows what a turn costs when the prompt cache is warm, but not when the cache goes cold or what the next turn costs if it does. Whether to `/compact` now or keep going depends on both: after a break longer than the cache lifetime, the next turn re-writes the whole context at a much higher price.

## What Changes

- The warning's first parenthesis gains the cache expiry time: `CONTEXT (15:13, cached till 16:13)`.
- The cost clause gains the cold-cache price: `~$0.10/turn cache reads, ~$2.00/turn uncached`.
- Expiry is the last API response's timestamp plus the cache lifetime in use (5 minutes or 1 hour), read from the transcript. It is a close, slightly conservative estimate.
- "Uncached" is the price of the next turn when the cache has expired: the whole context written back to the cache at the model's write price for that lifetime.
- The known-model and unknown-model (assumed window) messages both carry the new fields.
- The hook's model table gains cache-write prices, kept in step with the usage-pricing data by a test.
- Patch version bump (4.12.0 to 4.12.1), as requested.

## Capabilities

### New Capabilities
- `hooks/context-warning-cache-expiry`: what the context warning says about when the prompt cache expires and what a turn costs with a cold cache.

### Modified Capabilities

## Impact

- `src/hooks/context_window_warning.py`, `src/hooks/model_info.py`, `tests/test_context_window_warning.py`, `tests/test_model_info.py`, `tests/test_pricing.py`.
- `pyproject.toml`, `uv.lock`, `CHANGELOG.md`.
- The text of the message changes; nothing parses it.
