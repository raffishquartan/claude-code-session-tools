# Tests removed: superseded by tests/test_cache_sqlite.py.
#
# The original 19 tests in this file covered the CSV-backed cache implementation
# (cache_path= kwarg, entry.hash field, CSV seed helpers). That implementation
# was replaced by an SQLite backend in the feat(cache) commit that introduced
# cache.py v2. The functional coverage is now provided by test_cache_sqlite.py;
# test_sha256_command_is_stable moved there as well.
