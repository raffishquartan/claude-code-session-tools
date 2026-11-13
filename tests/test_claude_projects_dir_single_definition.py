"""The ~/.claude/projects path has one definition: lib/paths.claude_projects_dir()."""

from pathlib import Path

from claude_code_usage import cli

_SRC = Path(__file__).resolve().parent.parent / "src"
_ALLOWED = _SRC / "cc_session_tools" / "lib" / "paths.py"


def test_usage_cli_default_projects_dir_follows_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    args = cli._make_parser().parse_args(["query"])
    assert Path(args.projects_dir) == tmp_path / ".claude" / "projects"


def test_no_second_definition_of_the_projects_path():
    offenders = [
        str(p.relative_to(_SRC))
        for p in _SRC.rglob("*.py")
        if p != _ALLOWED
        and "tests" not in p.parts
        and '".claude" / "projects"' in p.read_text(encoding="utf-8")
    ]
    assert offenders == []
