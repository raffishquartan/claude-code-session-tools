from __future__ import annotations

from pathlib import Path

import pytest

from cc_session_tools.lib import api_keys
from cc_session_tools.lib.api_keys import ApiKeysError

SECRET = "sk-ant-secret-value"


def _write(tmp_path: Path, text: str, mode: int = 0o600) -> Path:
    p = tmp_path / "keys"
    p.write_text(text)
    p.chmod(mode)
    return p


def test_parse_ignores_comments_and_blank_lines() -> None:
    assert api_keys.parse_keys(f"# c\n\nwork={SECRET}\n  personal = k2 \n") == {
        "work": SECRET,
        "personal": "k2",
    }


@pytest.mark.parametrize("text", ["nolabel", "=value", "label=", f"a={SECRET}\na=x"])
def test_parse_rejects_bad_lines_without_leaking_values(text: str) -> None:
    with pytest.raises(ApiKeysError) as ei:
        api_keys.parse_keys(text)
    assert SECRET not in str(ei.value)


def test_load_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ApiKeysError, match="not found"):
        api_keys.load_keys(tmp_path / "nope")


def test_load_refuses_group_or_world_readable(tmp_path: Path) -> None:
    p = _write(tmp_path, f"work={SECRET}", mode=0o644)
    with pytest.raises(ApiKeysError, match="chmod 600") as ei:
        api_keys.load_keys(p)
    assert SECRET not in str(ei.value)


def test_load_empty_file(tmp_path: Path) -> None:
    with pytest.raises(ApiKeysError, match="no keys"):
        api_keys.load_keys(_write(tmp_path, "# only a comment\n"))


def test_load_ok(tmp_path: Path) -> None:
    assert api_keys.load_keys(_write(tmp_path, f"work={SECRET}")) == {"work": SECRET}


def test_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(api_keys.API_KEYS_FILE_ENV, str(tmp_path / "x"))
    assert api_keys.keys_file_path() == tmp_path / "x"


def test_default_path_is_outside_claude_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(api_keys.API_KEYS_FILE_ENV, raising=False)
    p = api_keys.keys_file_path()
    assert p == Path.home() / ".config" / "ccst" / "api-keys"
    assert ".claude" not in p.parts


def test_validate_label_lists_available() -> None:
    with pytest.raises(ApiKeysError, match="available: a, b"):
        api_keys.validate_label({"b": "1", "a": "2"}, "nope")


def test_choose_label_non_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    with pytest.raises(ApiKeysError, match="no terminal"):
        api_keys.choose_label({"a": "1"})


def test_choose_label_menu_pick_and_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda _="": "2")
    assert api_keys.choose_label({"work": "1", "personal": "2"}) == "work"  # sorted: personal, work
    monkeypatch.setattr("builtins.input", lambda _="": "q")
    assert api_keys.choose_label({"work": "1"}) is None


def test_choose_label_too_many(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    with pytest.raises(ApiKeysError, match="pass -k"):
        api_keys.choose_label({f"k{i}": "v" for i in range(11)})


def test_with_api_key_sets_key_and_drops_oauth_token() -> None:
    env = {"CLAUDE_CODE_OAUTH_TOKEN": "t", "X": "1"}
    api_keys.with_api_key(env, SECRET)
    assert env == {"ANTHROPIC_API_KEY": SECRET, "X": "1"}


def test_template_parses_to_zero_entries() -> None:
    template = Path(api_keys.__file__).parents[1] / "config" / "api-keys.example"
    assert api_keys.parse_keys(template.read_text()) == {}
