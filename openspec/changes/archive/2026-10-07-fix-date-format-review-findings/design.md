## Context

Findings from the review of the date format convention change. Only the three defects below are
addressed here; the other findings were judgement calls settled by the earlier decisions.

## Decisions

1. **`lib/timefmt.py`** holds `HUMAN_UTC_FORMAT = "%Y-%m-%d %H:%M UTC"` and two helpers,
   `format_utc(dt)` and `format_utc_epoch(epoch)`. Call sites use the helpers, so the literal
   exists once. The `clean-hook-sessions` skill script prints local time and is a standalone
   script, so it keeps its own format string.
2. **`format_sent_at` falls back to the stored value** when `strptime` rejects it. A message must
   stay readable even if its metadata was edited by hand; the rendering is a convenience.
3. **`date_field_warnings` returns an empty list on `sqlite3.Error` or `ValueError` from the
   schema read.** The warning is advisory and runs after the write has committed; a schema-read
   failure there must not make the command report failure, which could prompt a retry and a
   duplicate record.

## Risks / Trade-offs

- Swallowing the schema-read error hides a real problem from the advisory path only; the same
  error would already have failed the write that precedes it.
