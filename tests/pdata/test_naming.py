from __future__ import annotations

import pytest

from cc_session_tools.lib.pdata import naming


@pytest.mark.parametrize("name", ["ccst-ideas", "filings", "session-output", "a", "a1-b2-c3"])
def test_validate_record_group_accepts_valid_names(name):
    naming.validate_record_group(name)  # must not raise


@pytest.mark.parametrize(
    "name",
    ["", "CCST-Ideas", "ccst_ideas", "ccst ideas", "-leading", "trailing-", "double--hyphen",
     "has.dot", "1-2-3-"],
)
def test_validate_record_group_rejects_invalid_names(name):
    with pytest.raises(ValueError, match="record_group"):
        naming.validate_record_group(name)


def test_extension_table_name_transforms_hyphens_to_underscores():
    assert naming.extension_table_name("key-events") == "ext_key_events"
    assert naming.extension_table_name("filings") == "ext_filings"
    assert naming.extension_table_name("a-b-c") == "ext_a_b_c"


def test_extension_table_name_rejects_invalid_record_group():
    with pytest.raises(ValueError, match="record_group"):
        naming.extension_table_name("Not_Valid")


@pytest.mark.parametrize("name", ["sender", "sent_at", "is_read", "a1", "a_1_b"])
def test_validate_field_name_accepts_valid_names(name):
    naming.validate_field_name(name)  # must not raise


@pytest.mark.parametrize("name", ["", "Sender", "1abc", "sent-at", "sent at", "sent.at"])
def test_validate_field_name_rejects_invalid_names(name):
    with pytest.raises(ValueError, match="field name"):
        naming.validate_field_name(name)


@pytest.mark.parametrize(
    "name",
    ["id", "record_group", "content", "file_path", "created_at", "updated_at",
     "version", "deleted_at", "record_id"],
)
def test_validate_field_name_rejects_reserved_base_column_names(name):
    with pytest.raises(ValueError, match="collides"):
        naming.validate_field_name(name)
