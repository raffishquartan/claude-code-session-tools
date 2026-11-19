## Context

`_parse_where_clause` in `lib/pdata/service.py` splits a clause into field, operator and value with a regex and returns the value verbatim. `query_records` binds it as a SQL parameter, so any quote characters are compared literally. Extension fields are already resolved by `query_records`; see proposal.md for the symptom.

## Goals / Non-Goals

**Goals:**
- Quoted values match the unquoted text, for base and extension fields and for LIKE.

**Non-Goals:**
- Escape sequences inside quotes, multi-value operators (IN), or any new operator.
- Changing field resolution.

## Decisions

- Strip in `_parse_where_clause`, the single place values are parsed, so every caller (CLI, skills, session-output lookup) gets the same behaviour. Alternative: strip in the CLI layer only - rejected, it would leave the service API inconsistent.
- Strip exactly one pair, only when first and last characters are the same quote and the value is at least 2 characters. Alternative: reject quoted values - rejected, the documented example uses them.

## Risks / Trade-offs

- A stored value that really starts and ends with a quote can no longer be matched unquoted → write it with an extra outer pair of quotes, e.g. `"'x'"`.
