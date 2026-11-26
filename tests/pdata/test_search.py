from __future__ import annotations

from datetime import datetime

import pytest

from cc_session_tools.lib.pdata import search, service, store

_PROJECT = "example"


@pytest.fixture(autouse=True)
def _store_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))


def _add(group: str, content: str, *, created_at: int = 1000, **fields: str) -> str:
    return service.add_record(
        project=_PROJECT, record_group=group, content=content, file_path=None,
        fields=fields, created_at=created_at,
    ).id


def _field(group: str, spec: str) -> None:
    name, sql_type = spec.split(":")
    service.schema_add_field(
        project=_PROJECT, record_group=group, field_name=name, sql_type=sql_type,
        description=None, default=None,
    )


def _req(**kwargs: object) -> search.SearchRequest:
    base: dict[str, object] = {"project": _PROJECT, "groups": ("notes",)}
    base.update(kwargs)
    return search.SearchRequest(**base)  # type: ignore[arg-type]


def _contents(result: search.SearchResult) -> list[object]:
    return [row["content"] for row in result.rows]


# --- read-only, live records ------------------------------------------------------------------


def test_soft_deleted_records_are_not_returned():
    rid = _add("notes", "alice one")
    _add("notes", "alice two")
    service.delete_record(project=_PROJECT, record_id=rid, expected_version=1)
    assert _contents(search.run(_req(text="alice"))) == ["alice two"]


def test_missing_store_is_rejected_and_not_created():
    with pytest.raises(ValueError, match="'example' has no pdata store"):
        search.run(_req(text="x"))
    assert not store.db_path(_PROJECT).exists()


# --- groups -----------------------------------------------------------------------------------


def test_multi_group_search_names_each_group():
    _add("notes", "alice in notes")
    _add("letters", "alice in letters")
    _add("letters", "bob")
    result = search.run(_req(groups=("notes", "letters"), text="alice"))
    assert [(r["record_group"], r["content"]) for r in result.rows] == [
        ("notes", "alice in notes"), ("letters", "alice in letters"),
    ]


def test_unknown_group_lists_existing_groups():
    _add("notes", "x")
    with pytest.raises(ValueError, match=r"unknown group 'nosuch'.*notes"):
        search.run(_req(groups=("notes", "nosuch"), text="x"))


def test_duplicate_group_is_searched_once():
    _add("notes", "alice")
    assert _contents(search.run(_req(groups=("notes", "notes"), text="alice"))) == ["alice"]


# --- text and regex ---------------------------------------------------------------------------


def test_text_is_case_insensitive_by_default():
    _add("notes", "Met Alice today")
    assert _contents(search.run(_req(text="alice"))) == ["Met Alice today"]


def test_text_case_insensitive_folds_non_ascii():
    _add("notes", "ÉCOLE")
    assert _contents(search.run(_req(text="école"))) == ["ÉCOLE"]


def test_case_sensitive_text():
    _add("notes", "Met Alice today")
    assert search.run(_req(text="alice", case_sensitive=True)).rows == []


def test_text_is_literal_not_a_pattern():
    _add("notes", "a.c")
    _add("notes", "abc")
    assert _contents(search.run(_req(text="a.c"))) == ["a.c"]


def test_extension_text_field_is_searched_by_default():
    _field("notes", "sender:TEXT")
    _add("notes", "no match here", sender="alice@example.com")
    assert len(search.run(_req(text="alice")).rows) == 1


def test_integer_extension_field_is_not_searched_by_default():
    _field("notes", "amount:INTEGER")
    _add("notes", "x", amount="42")
    assert search.run(_req(text="42")).rows == []


def test_fields_restricts_search():
    _field("notes", "sender:TEXT")
    _add("notes", "no match here", sender="alice@example.com")
    assert search.run(_req(text="alice", fields=("content",))).rows == []


def test_fields_may_name_a_numeric_field():
    _field("notes", "amount:INTEGER")
    _add("notes", "x", amount="42")
    assert len(search.run(_req(text="42", fields=("amount",))).rows) == 1


def test_regex():
    _add("notes", "invoice 42")
    _add("notes", "invoice abc")
    assert _contents(search.run(_req(regex=r"invoice \d+"))) == ["invoice 42"]


def test_regex_case_insensitive_unless_case_sensitive():
    _add("notes", "INVOICE 42")
    assert len(search.run(_req(regex="invoice")).rows) == 1
    assert search.run(_req(regex="invoice", case_sensitive=True)).rows == []


def test_not_text_inverts():
    _add("notes", "alpha")
    _add("notes", "beta")
    assert _contents(search.run(_req(text="alpha", not_text=True))) == ["beta"]


def test_null_field_never_matches():
    _field("notes", "sender:TEXT")
    _add("notes", "x")
    assert search.run(_req(text="x", fields=("sender",))).rows == []


def test_fields_missing_from_one_group_skips_that_group_for_positive_match():
    _field("notes", "sender:TEXT")
    _add("notes", "a", sender="alice")
    _add("letters", "alice")
    result = search.run(_req(groups=("notes", "letters"), text="alice", fields=("sender",)))
    assert [r["record_group"] for r in result.rows] == ["notes"]
    assert result.rows[0]["sender"] == "alice"


# --- where and dates --------------------------------------------------------------------------


def test_where_reuses_query_semantics_with_quotes():
    _field("notes", "status:TEXT")
    _add("notes", "invoice one", status="open")
    _add("notes", "invoice two", status="closed")
    result = search.run(_req(text="invoice", where=("status = 'open'",)))
    assert _contents(result) == ["invoice one"]


def test_where_alone_is_a_predicate():
    _field("notes", "status:TEXT")
    _add("notes", "a", status="open")
    _add("notes", "b", status="closed")
    assert _contents(search.run(_req(where=('status = "closed"',)))) == ["b"]


def test_date_range_on_text_field():
    _field("notes", "sent_date:TEXT")
    for i, d in enumerate(("2026-01-05", "2026-02-10", "2026-03-15 09:30")):
        _add("notes", d, created_at=1000 + i, sent_date=d)
    result = search.run(
        _req(date_field="sent_date", since="2026-02-01", until="2026-03-15"),
    )
    assert _contents(result) == ["2026-02-10", "2026-03-15 09:30"]


def test_date_range_on_created_at_uses_local_days():
    def noon(day: str) -> int:
        return int(datetime.fromisoformat(f"{day}T12:00:00").timestamp())

    _add("notes", "jan", created_at=noon("2026-01-05"))
    _add("notes", "mar", created_at=noon("2026-03-15"))
    assert _contents(search.run(_req(date_field="created_at", since="2026-03-01"))) == ["mar"]
    assert _contents(search.run(_req(date_field="created_at", until="2026-03-15"))) == [
        "jan", "mar",
    ]
    assert _contents(search.run(_req(date_field="created_at", until="2026-03-14"))) == ["jan"]


def test_date_range_on_integer_extension_field():
    _field("notes", "sent_at:INTEGER")
    _add("notes", "old", sent_at=str(int(datetime(2026, 1, 5, 12).timestamp())))
    _add("notes", "new", sent_at=str(int(datetime(2026, 3, 5, 12).timestamp())))
    assert _contents(search.run(_req(date_field="sent_at", since="2026-02-01"))) == ["new"]


def test_date_field_of_blob_type_is_rejected():
    _field("notes", "raw:BLOB")
    _add("notes", "x")
    with pytest.raises(ValueError, match="date field 'raw'"):
        search.run(_req(date_field="raw", since="2026-01-01"))


# --- output -----------------------------------------------------------------------------------


def test_rows_have_query_shape_and_columns_list():
    _field("notes", "sender:TEXT")
    rid = _add("notes", "alice", sender="bob")
    result = search.run(_req(text="alice"))
    row = result.rows[0]
    assert row["id"] == rid
    assert list(row) == result.columns
    assert result.columns == [
        "id", "record_group", "content", "file_path", "created_at", "updated_at",
        "version", "deleted_at", "sender",
    ]


def test_multi_group_columns_are_the_union_and_missing_fields_are_null():
    _field("notes", "sender:TEXT")
    _add("notes", "alice", sender="x")
    _add("letters", "alice")
    result = search.run(_req(groups=("notes", "letters"), text="alice"))
    assert result.columns[-1] == "sender"
    assert result.rows[1]["sender"] is None


def test_select_keeps_id_and_group():
    _add("notes", "alice")
    result = search.run(_req(text="alice", select=("content",)))
    assert list(result.rows[0]) == ["id", "record_group", "content"]
    assert result.columns == ["id", "record_group", "content"]


def test_select_does_not_duplicate_id():
    _add("notes", "alice")
    result = search.run(_req(text="alice", select=("id", "content")))
    assert result.columns == ["id", "record_group", "content"]


def test_sort_desc_with_limit_across_groups():
    _field("notes", "rank:INTEGER")
    _field("letters", "rank:INTEGER")
    _add("notes", "n100", rank="100")
    _add("letters", "l300", rank="300")
    _add("notes", "n200", rank="200")
    result = search.run(
        _req(groups=("notes", "letters"), where=("rank > 0",),
             sort="rank", desc=True, limit=2),
    )
    assert _contents(result) == ["l300", "n200"]


def test_sort_ascending():
    _add("notes", "b alice", created_at=2000)
    _add("notes", "a alice", created_at=1000)
    assert _contents(search.run(_req(text="alice", sort="content"))) == ["a alice", "b alice"]


def test_default_order_is_group_order_then_created_at():
    _add("notes", "alice n2", created_at=2000)
    _add("notes", "alice n1", created_at=1000)
    _add("letters", "alice l1", created_at=500)
    result = search.run(_req(groups=("notes", "letters"), text="alice"))
    assert _contents(result) == ["alice n1", "alice n2", "alice l1"]


def test_limit_without_sort_stops_across_groups():
    _add("notes", "alice 1")
    _add("letters", "alice 2")
    result = search.run(_req(groups=("notes", "letters"), text="alice", limit=1))
    assert _contents(result) == ["alice 1"]


def test_count_only():
    for i in range(3):
        _add("notes", f"alice {i}", created_at=1000 + i)
    _add("letters", "alice")
    result = search.run(_req(groups=("notes", "letters"), text="alice", count_only=True))
    assert result.count == 4
    assert result.rows == []


def test_no_match_returns_empty_rows_and_zero_count():
    _add("notes", "x")
    result = search.run(_req(text="nothing"))
    assert result.rows == [] and result.count == 0
    assert result.columns[0] == "id"
    assert search.run(_req(text="nothing", count_only=True)).count == 0


# --- snippets ---------------------------------------------------------------------------------


def test_snippet_around_first_match():
    _add("notes", "aaaaaaaaaa alice bbbbbbbbbb alice")
    result = search.run(_req(text="ALICE", snippet=3, fields=("content",)))
    assert result.rows[0]["content"] == "...aa alice bb..."


def test_snippet_without_cut_ends_has_no_ellipsis():
    _add("notes", "x alice y")
    assert search.run(_req(text="alice", snippet=5)).rows[0]["content"] == "x alice y"


def test_snippet_shortens_unmatched_searched_field_and_leaves_others():
    _field("notes", "sender:TEXT")
    _field("notes", "notes_text:TEXT")
    _add("notes", "alice", sender="bob@example.com", notes_text="long untouched text")
    row = search.run(
        _req(text="alice", snippet=3, fields=("content", "sender")),
    ).rows[0]
    assert row["content"] == "alice"
    assert row["sender"] == "bob..."
    assert row["notes_text"] == "long untouched text"


# --- validation -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({}, "requires a predicate"),
        ({"groups": ()}, "at least one --group"),
        ({"text": "a", "regex": "b"}, "not both"),
        ({"regex": "("}, "invalid --regex"),
        ({"case_sensitive": True, "where": ("content = x",)}, "--case-sensitive needs"),
        ({"not_text": True, "where": ("content = x",)}, "--not-text needs"),
        ({"fields": ("content",), "where": ("content = x",)}, "--fields needs"),
        ({"snippet": 5, "where": ("content = x",)}, "--snippet needs"),
        ({"text": "a", "snippet": 5, "not_text": True}, "--snippet cannot"),
        ({"text": "a", "snippet": 5, "count_only": True}, "--snippet cannot"),
        ({"text": "a", "count_only": True, "select": ("content",)}, "--count-only cannot"),
        ({"text": "a", "count_only": True, "sort": "content"}, "--count-only cannot"),
        ({"text": "a", "count_only": True, "limit": 3}, "--count-only cannot"),
        ({"text": "a", "desc": True}, "--desc needs --sort"),
        ({"since": "2026-01-01"}, "--date-field"),
        ({"text": "a", "date_field": "created_at"}, "--date-field needs"),
        ({"date_field": "created_at", "since": "05/01/2026"}, "yyyy-MM-dd"),
        ({"date_field": "created_at", "since": "2026-02-30"}, "yyyy-MM-dd"),
        ({"date_field": "created_at", "since": "20260105"}, "yyyy-MM-dd"),
        ({"date_field": "created_at", "since": "2026-03-01", "until": "2026-02-01"},
         "later than"),
        ({"text": "a", "limit": 0}, "--limit"),
        ({"text": "a", "snippet": 0}, "--snippet"),
        ({"text": "a", "fields": ()}, "--fields names no field"),
        ({"text": "a", "select": ()}, "--select names no field"),
        ({"where": ("content",)}, "malformed --where"),
        ({"text": "a", "groups": ("Not Valid",)}, "record_group"),
    ],
)
def test_request_validation(kwargs, message):
    with pytest.raises(ValueError, match=message):
        _req(**kwargs)


def test_validation_happens_before_any_store_access():
    with pytest.raises(ValueError, match="requires a predicate"):
        _req()
    assert not store.db_path(_PROJECT).exists()


def test_empty_text_is_rejected():
    with pytest.raises(ValueError, match="--text"):
        _req(text="")


def test_unknown_search_field_lists_valid_fields():
    _field("notes", "sender:TEXT")
    _add("notes", "x")
    with pytest.raises(ValueError, match=r"unknown --fields field 'nosuch'.*content.*sender"):
        search.run(_req(text="a", fields=("nosuch",)))


def test_unknown_select_field_lists_valid_fields():
    _add("notes", "x")
    with pytest.raises(ValueError, match=r"unknown --select field 'nosuch'.*updated_at"):
        search.run(_req(text="a", select=("nosuch",)))


def test_sort_field_must_exist_in_every_group():
    _field("notes", "rank:INTEGER")
    _add("notes", "x")
    _add("letters", "x")
    with pytest.raises(ValueError, match=r"unknown --sort field 'rank' for group 'letters'"):
        search.run(_req(groups=("notes", "letters"), text="x", sort="rank"))


def test_where_field_must_exist_in_every_group():
    _add("notes", "x")
    with pytest.raises(ValueError, match=r"unknown --where field 'nosuch'.*content"):
        search.run(_req(where=("nosuch = 1",)))


def test_date_field_must_exist():
    _add("notes", "x")
    with pytest.raises(ValueError, match=r"unknown --date-field field 'nosuch'"):
        search.run(_req(date_field="nosuch", since="2026-01-01"))



# --- review fixes -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"date_field": "created_at", "until": "9999-12-31"}, "--until 9999-12-31"),
        ({"date_field": "created_at", "since": "0001-01-01"}, "--since 0001-01-01"),
    ],
)
def test_out_of_range_epoch_dates_are_rejected_naming_the_flag(kwargs, message):
    _add("notes", "x")
    with pytest.raises(ValueError, match=message):
        search.run(_req(**kwargs))


def test_far_dates_work_on_text_date_fields():
    _field("notes", "sent_date:TEXT")
    _add("notes", "x", sent_date="2026-01-01")
    result = search.run(_req(date_field="sent_date", since="0001-01-01", until="9999-12-31"))
    assert _contents(result) == ["x"]


def test_empty_regex_is_rejected():
    with pytest.raises(ValueError, match="--regex"):
        _req(regex="")


def test_search_does_not_write_to_the_store():
    _add("notes", "alice")
    path = store.db_path(_PROJECT)
    import sqlite3
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE pdata_meta")
    conn.commit()
    conn.close()
    assert len(search.run(_req(text="alice")).rows) == 1
    conn = sqlite3.connect(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "pdata_meta" not in tables


def test_group_with_schema_but_no_records_is_searchable():
    _add("notes", "x")
    _field("letters", "sender:TEXT")
    assert search.run(_req(groups=("letters",), text="x")).rows == []


def test_snippet_cuts_unmatched_numeric_field():
    _field("notes", "amount:INTEGER")
    _add("notes", "alice", amount="123456")
    row = search.run(_req(text="alice", snippet=2, fields=("content", "amount"))).rows[0]
    assert row["amount"] == "12..."


def test_not_text_returns_every_row_of_a_group_without_the_searched_field():
    _field("notes", "sender:TEXT")
    _add("notes", "a", sender="alice")
    _add("letters", "alice")
    result = search.run(
        _req(groups=("notes", "letters"), text="alice", fields=("sender",), not_text=True),
    )
    assert [r["record_group"] for r in result.rows] == ["letters"]
