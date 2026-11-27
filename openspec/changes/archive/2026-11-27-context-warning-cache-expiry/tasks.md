## 1. Prices

- [x] 1.1 Add `_CACHE_WRITE_PRICE` (per model id: 5m and 1h) and `cache_write_price_per_mtok(model_id, ttl)` to `src/hooks/model_info.py`, with defaults for unknown models and update the module docstring; verify new tests in `tests/test_model_info.py` pass, including that every id in `_NAME` has write prices
- [x] 1.2 Extend `tests/test_pricing.py` parity test to the write prices and verify it passes

## 2. Transcript

- [x] 2.1 Make `_current_context_tokens` return a frozen dataclass with tokens, model, response timestamp and cache lifetime (1h, 5m, both -> 1h, no breakdown -> 5m, carried forward, default 5m), wrap a bad timestamp as `TranscriptError`, add a shared fixture helper adding timestamps to all existing fixtures and update existing assertions; verify tests for each scenario pass

## 3. Message

- [x] 3.1 Add the expiry time and uncached cost to `_reason` and `_reason_unknown`; verify exact-text tests, including the 5m and 1h scenarios in the spec, pass
- [x] 3.2 Wire `main` to the new values; verify the end-to-end `main` tests pass, including a malformed-timestamp test (stderr, exit 1) and an expiry-across-midnight test

## 4. Release

- [x] 4.1 Bump `pyproject.toml` to 4.12.1, run `uv lock`, add a CHANGELOG entry; verify `uv run pytest -q` and `openspec validate` pass
