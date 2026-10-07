## 1. Shared time format

- [x] 1.1 Add `lib/timefmt.py` and use it in `doctor.py`, `install_sync.py`, `pdata/cutover.py` and
      `messaging/service.py`; tests for the helpers and an unchanged-output test per call site

## 2. Robustness

- [x] 2.1 `format_sent_at` returns the stored value when it cannot be parsed; test the `ccmsg read`
      scenario
- [x] 2.2 `date_field_warnings` returns `[]` on `sqlite3.Error`/`ValueError` from the schema read;
      test with a failing `schema_show`

## 3. Release

- [x] 3.1 CHANGELOG entry, patch bump to 3.12.1, `uv lock`, full checks, archive the change
