from pathlib import Path

from cc_session_tools.lib.roots import load_session_roots


def test_load_skips_blank_and_comment_lines(tmp_path):
    roots_file = tmp_path / "roots.txt"
    real_root = tmp_path / "myroot"
    real_root.mkdir()
    roots_file.write_text(
        f"# a comment\n"
        f"\n"
        f"   # leading-whitespace comment\n"
        f"{real_root}\n"
    )
    result = load_session_roots(roots_file)
    assert result == [real_root.resolve()]


def test_load_skips_nonexistent_paths(tmp_path):
    roots_file = tmp_path / "roots.txt"
    real_root = tmp_path / "real"
    real_root.mkdir()
    roots_file.write_text(
        f"{tmp_path / 'does-not-exist'}\n"
        f"{real_root}\n"
    )
    assert load_session_roots(roots_file) == [real_root.resolve()]


def test_load_strips_inline_comments(tmp_path):
    roots_file = tmp_path / "roots.txt"
    real_root = tmp_path / "real"
    real_root.mkdir()
    roots_file.write_text(f"{real_root}  # trailing comment\n")
    assert load_session_roots(roots_file) == [real_root.resolve()]


def test_load_resolves_symlinks(tmp_path):
    roots_file = tmp_path / "roots.txt"
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    roots_file.write_text(f"{link}\n")
    assert load_session_roots(roots_file) == [real.resolve()]
