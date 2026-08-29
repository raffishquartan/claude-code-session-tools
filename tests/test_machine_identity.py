from __future__ import annotations

import socket

import pytest

from cc_session_tools.lib import machine_identity


def test_resolve_uses_stored_value_if_present(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("CCST_MACHINE_NAME", "ltxy")
    result = machine_identity.resolve()
    assert result.machine_id == "ltxy"
    assert result.confirmed is True


def test_resolve_falls_back_to_hostname_unconfirmed(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("CCST_MACHINE_NAME", raising=False)
    result = machine_identity.resolve()
    assert result.machine_id == socket.gethostname()
    assert result.confirmed is False


def test_resolve_uses_confirmed_store_when_no_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("CCST_MACHINE_NAME", raising=False)
    machine_identity.confirm("ltxy")
    result = machine_identity.resolve()
    assert result.machine_id == "ltxy"
    assert result.confirmed is True


def test_check_collision_flags_a_different_known_machine():
    vector = {"macbook": 3}
    assert machine_identity.check_collision(proposed="ltxy", known_vector=vector) is True
    assert machine_identity.check_collision(proposed="macbook", known_vector=vector) is False


def test_check_collision_is_fine_with_a_name_already_recorded_as_itself():
    vector = {"ltxy": 5}
    assert machine_identity.check_collision(proposed="ltxy", known_vector=vector) is False


def test_confirm_rejects_an_empty_name(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))
    with pytest.raises(ValueError):
        machine_identity.confirm("")


def test_confirm_rejects_a_whitespace_only_name(tmp_path, monkeypatch):
    monkeypatch.setenv("CCST_DATA_HOME", str(tmp_path))
    with pytest.raises(ValueError):
        machine_identity.confirm("   ")
