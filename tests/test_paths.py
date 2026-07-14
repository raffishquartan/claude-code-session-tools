from pathlib import Path

from cc_session_tools.lib import paths


def test_data_home_defaults_to_xdg_data_claude(monkeypatch, tmp_path):
    monkeypatch.delenv("CCST_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.data_home() == tmp_path / ".local" / "share" / "claude"


def test_data_home_honours_env_override(monkeypatch, tmp_path):
    override = tmp_path / "custom-data-home"
    monkeypatch.setenv("CCST_DATA_HOME", str(override))
    assert paths.data_home() == override


def test_data_home_env_override_beats_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "unused-home"))
    override = tmp_path / "custom-data-home"
    monkeypatch.setenv("CCST_DATA_HOME", str(override))
    assert paths.data_home() == override
