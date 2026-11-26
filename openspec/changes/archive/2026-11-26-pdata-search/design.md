## Context

`repository.query_records` builds `--where` SQL per group (base columns, then auto-joined `ext_<group>` columns); `service._parse_where_clause` parses and quote-strips clauses. Rows are flattened by `service.record_to_dict` and rendered by `formatting.render`, whose CSV writer currently emits CRLF. `repository.connect` creates a store file when none exists. Requirements are in `specs/pdata/search/spec.md`.

## Goals / Non-Goals

**Goals:**
- One SQL statement per searched group does filtering, ordering and limiting; Python does only cross-group merging, projection and snippets.
- One validation function for every request-level rule, one for every schema-level rule, both in `search.py`.

**Non-Goals:**
- `--include-deleted` and a table format (search output is for scripts and sessions; `query` still has both).
- Full-text indexing (FTS5). Groups of thousands of rows scan fast enough.
- OR-combined predicates or multiple `--text` terms.

## Decisions

- **Text and regex run in SQL through a Python function.** The search opens its connection, registers a deterministic SQLite function (`create_function`) that applies the compiled pattern (`re.escape(text)` for `--text`, `re.IGNORECASE` unless `--case-sensitive`) to its arguments, and puts `pdata_search_match(<searched columns>)` in the WHERE clause, negated for `--not-text`. Filtering, ORDER BY and LIMIT then stay in one statement and only matching rows are fetched. Alternative: SQL `LIKE` prefilter plus a Python recheck - rejected, because SQLite's `LIKE` folds only ASCII case and would need a second pass for regex anyway; two engines for one predicate invite disagreement.
- **`--where` reuse.** The clause-to-SQL loop in `repository.query_records` moves into `repository.where_sql(conditions, ext_columns)`, used by both `query_records` and search; `_parse_where_clause` becomes public `parse_where_clause`. The `--where` syntax is therefore `'<field> <op> <value>'`, not `field=value`: the same as `query`, as the brief asked for reuse over a second syntax.
- **Multi-group.** One statement per group (each has its own extension table). With `--sort`, the per-group results, each already sorted and limited, are merged and cut to `--limit`; without it, groups are concatenated in the order given. `--count-only` uses `SELECT COUNT(*)` with the same WHERE.
- **Field rules across groups.** Filter and sort fields (`--where`, `--sort`, `--date-field`) must exist in every group, because a filter on a missing column has no meaning. Output-side fields (`--fields`, `--select`) need only exist in one group; a group without the field outputs null for it and does not search it.
- **Projection.** `--select` always keeps `id` and `record_group`: a result row is only actionable with its id, and only unambiguous in multi-group output with its group. There is no opt-out; a caller that wants neither drops them.
- **Dates.** `--since`/`--until` take only yyyy-MM-dd (`date.fromisoformat` after a regex fullmatch, so `20260105` and datetimes are rejected). TEXT fields compare `substr(field, 1, 10)` as strings, which works for every ISO form the date-field warnings accept. Numeric fields are epochs compared against local-midnight bounds, local because a user's "on 2026-03-15" means their own day.
- **Snippets** use the same compiled pattern on the output value, so a snippet always shows the match that selected the row.
- **Missing store.** Search checks `store.db_path(project).exists()` before connecting and refuses, rather than letting `connect` create an empty store as a read side effect.
- **CSV line endings.** `formatting._render_csv` gains `lineterminator="\n"` for every verb, and an empty result with known columns renders as a header row. The CRLF output was the csv module default, not a choice.
- **Count output** is a bare JSON number (`3`), not an object, so `jq` and shells read it directly.

## Risks / Trade-offs

- [A regex with catastrophic backtracking stalls the scan] → The pattern is the caller's own and the store is local; no timeout is added.
- [`list`/`query` CSV consumers that expected CRLF] → CSV readers accept both; recorded under Changed in the CHANGELOG.
- [Local-time epoch bounds differ between machines in different time zones] → Documented in `--help`; TEXT ISO date fields are unaffected.
