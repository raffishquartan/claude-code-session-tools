from __future__ import annotations

import uuid

import pytest

from cc_session_tools.lib.pdata import ids


def test_new_record_id_is_a_canonical_random_uuid():
    record_id = ids.new_record_id()
    parsed = uuid.UUID(record_id)
    assert str(parsed) == record_id
    assert parsed.version == 4


def test_new_record_id_never_repeats():
    assert len({ids.new_record_id() for _ in range(1000)}) == 1000


def test_deterministic_record_id_is_uuid5_over_project_and_key():
    expected = str(uuid.uuid5(ids.RECORD_ID_NAMESPACE, "proj:session-output-watermark"))
    assert ids.deterministic_record_id("proj", "session-output-watermark") == expected


def test_deterministic_record_id_is_stable_and_distinguishes_inputs():
    a = ids.deterministic_record_id("proj", "k")
    assert a == ids.deterministic_record_id("proj", "k")
    assert a != ids.deterministic_record_id("proj", "other")
    assert a != ids.deterministic_record_id("other-proj", "k")
    assert ids.is_record_id(a)


@pytest.mark.parametrize("value", [
    "12",
    "",
    "not-a-uuid",
    "40090682D73D4958A6089C9960620F31",          # no hyphens
    "40090682-D73D-4958-A608-9C9960620F31",      # upper case
    "{40090682-d73d-4958-a608-9c9960620f31}",    # braces
    "urn:uuid:40090682-d73d-4958-a608-9c9960620f31",
    " 40090682-d73d-4958-a608-9c9960620f31",
])
def test_is_record_id_rejects_non_canonical_values(value):
    assert ids.is_record_id(value) is False


def test_is_record_id_accepts_canonical_form():
    assert ids.is_record_id("40090682-d73d-4958-a608-9c9960620f31") is True
