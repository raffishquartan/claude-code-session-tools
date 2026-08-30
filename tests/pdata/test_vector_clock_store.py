from cc_session_tools.lib.pdata import repository, vector_clock_store


def test_read_vector_is_empty_on_a_fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("proj")
    assert vector_clock_store.read_vector(conn) == {}


def test_bump_own_and_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("proj")
    with repository._immediate(conn):
        vector_clock_store.bump_own(conn, "ltxy")
        vector_clock_store.bump_own(conn, "ltxy")
    assert vector_clock_store.read_vector(conn) == {"ltxy": 2}


def test_write_vector_overwrites_every_row(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_PROJECT_DB_DIR", str(tmp_path))
    conn = repository.connect("proj")
    with repository._immediate(conn):
        vector_clock_store.write_vector(conn, {"ltxy": 3, "macbook": 1}, updated_at=100)
    assert vector_clock_store.read_vector(conn) == {"ltxy": 3, "macbook": 1}
    with repository._immediate(conn):
        vector_clock_store.write_vector(conn, {"ltxy": 4}, updated_at=200)
    # macbook's row must be gone — write_vector replaces the whole table, it doesn't merge.
    assert vector_clock_store.read_vector(conn) == {"ltxy": 4}
