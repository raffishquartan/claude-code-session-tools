"""Tests for record classification and schema validation."""

import pytest

from claude_code_usage import schema


def test_assistant_record_with_usage_is_billable() -> None:
    record = {
        "type": "assistant",
        "uuid": "u-1",
        "sessionId": "s-1",
        "timestamp": "2026-05-09T10:36:44.339Z",
        "cwd": "/mnt/c/Users/cfoge/OneDrive/claude/oneshot",
        "message": {
            "model": "claude-opus-4-7",
            "type": "message",
            "role": "assistant",
            "content": [],
            "usage": {
                "input_tokens": 6,
                "cache_creation_input_tokens": 29911,
                "cache_read_input_tokens": 18460,
                "output_tokens": 68,
            },
        },
    }
    assert schema.is_billable_record(record) is True


def test_non_assistant_records_are_not_billable() -> None:
    for rec in [
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        {"type": "queue-operation", "operation": "enqueue"},
        {"type": "system", "message": {}},
        {"type": "assistant", "message": {"role": "assistant", "content": []}},
        {"type": "assistant", "message": "not-a-dict"},
        {},
    ]:
        assert schema.is_billable_record(rec) is False


def _good_record() -> dict:
    return {
        "type": "assistant",
        "uuid": "u-1",
        "sessionId": "s-1",
        "timestamp": "2026-05-09T10:36:44.339Z",
        "cwd": "/mnt/c/Users/cfoge/OneDrive/claude/oneshot",
        "message": {
            "model": "claude-opus-4-7",
            "type": "message",
            "role": "assistant",
            "content": [],
            "usage": {
                "input_tokens": 6,
                "cache_creation_input_tokens": 29911,
                "cache_read_input_tokens": 18460,
                "output_tokens": 68,
            },
        },
    }


def test_validate_record_returns_no_issues_for_well_formed_record() -> None:
    result = schema.validate_record(_good_record())
    assert result.errors == []
    assert result.warnings == []


def test_validate_record_reports_missing_required_top_level_field() -> None:
    rec = _good_record()
    del rec["timestamp"]
    result = schema.validate_record(rec)
    assert any("timestamp" in e for e in result.errors)


def test_validate_record_reports_missing_usage_subfield() -> None:
    rec = _good_record()
    del rec["message"]["usage"]["output_tokens"]
    result = schema.validate_record(rec)
    assert any("output_tokens" in e for e in result.errors)


def test_validate_record_warns_on_unknown_top_level_key() -> None:
    rec = _good_record()
    rec["someBrandNewField"] = 123
    result = schema.validate_record(rec)
    assert result.errors == []
    assert any("someBrandNewField" in w for w in result.warnings)


def test_validate_record_warns_on_unknown_usage_subfield() -> None:
    rec = _good_record()
    rec["message"]["usage"]["surprise_token_bucket"] = 42
    result = schema.validate_record(rec)
    assert result.errors == []
    assert any("surprise_token_bucket" in w for w in result.warnings)


def test_assert_billable_raises_on_errors() -> None:
    rec = _good_record()
    del rec["timestamp"]
    with pytest.raises(schema.SchemaError) as exc:
        schema.assert_billable(rec)
    assert "timestamp" in str(exc.value)
