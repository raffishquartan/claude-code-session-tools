"""Unit tests for hooks.shell_parse, the command splitter behind bash-hard-deny."""
from __future__ import annotations

import pytest

from hooks.shell_parse import DYNAMIC, ShellParseError, split_commands


def _values(text: str) -> list[list[str]]:
    return [[w.value for w in cmd.words] for cmd in split_commands(text)]


def test_separators_split_commands() -> None:
    assert _values("a 1; b && c || d | e & f\ng") == [
        ["a", "1"], ["b"], ["c"], ["d"], ["e"], ["f"], ["g"],
    ]


def test_quotes_are_removed_and_keep_words_whole() -> None:
    assert _values("echo 'a; rm b' \"c | d\" e\\ f") == [["echo", "a; rm b", "c | d", "e f"]]


def test_ansi_c_quotes_are_decoded() -> None:
    assert _values("$'\\x72m' $'\\162m' $'a\\tb'") == [["rm", "rm", "a\tb"]]


def test_expansions_are_marked_dynamic() -> None:
    (cmd,) = split_commands('rm "$X/a" ${Y} $1')
    assert [w.dynamic for w in cmd.words] == [False, True, True, True]
    assert cmd.words[1].value == DYNAMIC + "/a"


def test_substitutions_are_parsed_recursively() -> None:
    assert _values("echo $(rm a) `rm b` \"$(rm c)\" <(rm d) ${X:-$(rm e)}") == [
        ["rm", "a"], ["rm", "b"], ["rm", "c"], ["rm", "d"], ["rm", "e"],
        ["echo", DYNAMIC, DYNAMIC, DYNAMIC, DYNAMIC, DYNAMIC],
    ]


def test_subshells_and_groups() -> None:
    assert _values("( rm a ); { rm b; }") == [["rm", "a"], ["{", "rm", "b"], ["}"]]


def test_redirection_targets_are_not_arguments() -> None:
    assert _values("rm a 2>/dev/null >out.txt &>log <in") == [["rm", "a"]]


def test_heredoc_body_is_data_unless_it_expands() -> None:
    assert _values("cat <<'EOF'\nrm -rf /\n$(rm x)\nEOF\nls") == [["cat"], ["ls"]]
    assert _values("cat <<EOF\nrm -rf /\n$(rm x)\nEOF") == [["cat"], ["rm", "x"]]


def test_comments_are_skipped() -> None:
    assert _values("ls # ; rm x\nrm y") == [["ls"], ["rm", "y"]]


def test_glob_mask_marks_only_unquoted_metacharacters() -> None:
    (cmd,) = split_commands("rm a*b 'c*d' ~/e {f,g}")
    assert [w.has_glob for w in cmd.words] == [False, True, False, False, False]
    assert cmd.words[3].tilde
    assert cmd.words[4].brace


@pytest.mark.parametrize("text", [
    "echo 'a", 'echo "a', "echo $(a", "echo `a", "echo ${a", "(" * 5000 + "rm x" + ")" * 5000,
])
def test_unterminated_input_raises(text: str) -> None:
    with pytest.raises(ShellParseError):
        split_commands(text)
