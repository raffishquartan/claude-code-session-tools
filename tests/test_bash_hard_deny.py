"""Tests for the bash-hard-deny PreToolUse hook.

Ports every case from the original bats suites
(claude-code-config-sync/tests/test-bash-hard-deny.bats and
test-bash-hard-deny-fires-jsonl.bats) 1:1, plus net-new coverage for the
opentabs/gh-delete/curl-DELETE categories that had no bats coverage, and the
fires.jsonl path fix.

Each test drives ``main()`` with a JSON payload on stdin and asserts the exit
code (2 = blocked, 0 = allowed) and, for blocks, a message substring; for
allows, the ``permissionDecision == "allow"`` JSON on stdout.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from cccs_hooks.bash_hard_deny import main


def _run(
    monkeypatch: pytest.MonkeyPatch, payload: object
) -> tuple[int, str, str]:
    """Feed *payload* as JSON on stdin, run main(), return (rc, stdout, stderr)."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    out = io.StringIO()
    err = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)
    rc = main()
    return rc, out.getvalue(), err.getvalue()


def _run_bash(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> tuple[int, str, str]:
    return _run(monkeypatch, {"tool_name": "Bash", "tool_input": {"command": command}})


def _assert_blocked(
    monkeypatch: pytest.MonkeyPatch, command: str, substr: str
) -> None:
    rc, _out, err = _run_bash(monkeypatch, command)
    assert rc == 2, f"expected block for {command!r}, got rc={rc}"
    assert substr.lower() in err.lower(), f"missing {substr!r} in stderr: {err!r}"


def _assert_allowed(monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    rc, out, _err = _run_bash(monkeypatch, command)
    assert rc == 0, f"expected allow for {command!r}, got rc={rc}"
    decision = json.loads(out)
    assert decision["hookSpecificOutput"]["permissionDecision"] == "allow"


# --- Non-Bash tools should pass through ---


def test_ignores_non_bash_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    rc, out, _err = _run(
        monkeypatch, {"tool_name": "Read", "tool_input": {"file_path": "/tmp/foo"}}
    )
    assert rc == 0
    assert out == ""


# --- Simple safe commands should be auto-approved ---


def test_approves_simple_ls(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_allowed(monkeypatch, "ls -la /tmp")


def test_approves_simple_cat(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_allowed(monkeypatch, "cat /tmp/file.txt")


def test_approves_git_status(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_allowed(monkeypatch, "git status")


# --- Piped/compound commands should be auto-approved if safe ---


def test_approves_cat_piped_to_python3(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_allowed(
        monkeypatch,
        'cat /tmp/data.json | python3 -c "import json, sys; print(json.load(sys.stdin))"',
    )


def test_approves_compound_command_with_and_and_pipes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_allowed(monkeypatch, "git log --oneline -5 && echo done")


def test_approves_python3_inline_script_reading_a_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_allowed(
        monkeypatch, "python3 -c \"with open('/tmp/f.txt') as f: print(f.read())\""
    )


# --- Destructive file operations should be blocked ---


def test_blocks_rm(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "rm /tmp/file.txt", "destructive")


def test_blocks_rm_rf(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "rm -rf /tmp/somedir", "destructive")


def test_blocks_rmdir(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "rmdir /tmp/emptydir", "destructive")


def test_blocks_unlink(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "unlink /tmp/file.txt", "destructive")


def test_blocks_shred(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "shred /tmp/file.txt", "destructive")


def test_blocks_rm_after_pipe(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "echo done | rm /tmp/file.txt", "destructive")


def test_blocks_rm_after_semicolon(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "echo done; rm /tmp/file.txt", "destructive")


def test_blocks_rm_after_and(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "echo done && rm /tmp/file.txt", "destructive")


# --- Python/Node destructive operations should be blocked ---


def test_blocks_os_remove_inline_python(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(
        monkeypatch, "python3 -c \"import os; os.remove('/tmp/f.txt')\"", "destructive"
    )


def test_blocks_shutil_rmtree_inline_python(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(
        monkeypatch, "python3 -c \"import shutil; shutil.rmtree('/tmp/dir')\"", "destructive"
    )


def test_blocks_os_unlink_inline_python(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(
        monkeypatch, "python3 -c \"import os; os.unlink('/tmp/f.txt')\"", "destructive"
    )


def test_blocks_fs_unlinksync_inline_node(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(
        monkeypatch, "node -e \"fs.unlinkSync('/tmp/f.txt')\"", "destructive"
    )


# --- curl/wget with destructive HTTP methods should be blocked ---


def test_blocks_curl_x_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "curl -X DELETE https://api.example.com/resource", "blocked")


def test_blocks_curl_x_post(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "curl -X POST https://api.example.com/resource", "blocked")


def test_blocks_curl_x_put(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "curl -X PUT https://api.example.com/resource", "blocked")


def test_blocks_curl_x_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "curl -X PATCH https://api.example.com/resource", "blocked")


def test_blocks_curl_data(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(
        monkeypatch, "curl --data 'key=value' https://api.example.com/resource", "blocked"
    )


def test_blocks_curl_d(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(
        monkeypatch, "curl -d 'key=value' https://api.example.com/resource", "blocked"
    )


def test_blocks_wget_request_post(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(
        monkeypatch, "wget --request POST https://api.example.com/resource", "blocked"
    )


def test_allows_curl_get(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_allowed(monkeypatch, "curl https://api.example.com/resource")


def test_allows_wget_get(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_allowed(monkeypatch, "wget https://api.example.com/resource")


# --- sudo should be blocked ---


def test_blocks_sudo(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "sudo apt-get install something", "sudo")


def test_blocks_sudo_after_and(monkeypatch: pytest.MonkeyPatch) -> None:
    # Could match either sudo or rm - both are blocked.
    rc, _out, _err = _run_bash(monkeypatch, "echo hello && sudo rm -rf /")
    assert rc == 2


# --- Empty command should pass ---


def test_handles_empty_command_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    rc, _out, _err = _run_bash(monkeypatch, "")
    assert rc == 0


# --- Does not false-positive on safe commands containing blocked substrings ---


def test_does_not_block_grep_for_rm_pattern(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_allowed(monkeypatch, "grep -r 'rm' /tmp/docs/")


def test_does_not_block_echo_containing_rm(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_allowed(monkeypatch, "echo 'do not rm this file'")


def test_does_not_block_git_commit_mentioning_destructive_patterns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_allowed(
        monkeypatch,
        "git commit -m 'Block os.remove and fs.unlinkSync in inline scripts'",
    )


# --- Script-file bypass: interpreter invoked on a file containing destructive ops ---


def test_blocks_python3_on_file_containing_os_remove(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = tmp_path / "target.py"
    f.write_text('import os\nos.remove("/tmp/target.txt")\n')
    _assert_blocked(monkeypatch, f"python3 {f}", "destructive")


def test_allows_python3_on_benign_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = tmp_path / "benign.py"
    f.write_text('print("hello, world")\nx = 1 + 1\nprint(f"2 == {x}")\n')
    _assert_allowed(monkeypatch, f"python3 {f}")


def test_allows_interpreter_on_missing_file(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_allowed(monkeypatch, "python3 /tmp/this-file-does-not-exist-20260418.py")


def test_allows_python3_m_venv(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_allowed(monkeypatch, "python3 -m venv /tmp/venv-20260418")


def test_blocks_bash_on_script_file_containing_rm_rf(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = tmp_path / "del.sh"
    f.write_text("#!/usr/bin/env bash\nrm -rf /tmp/target-dir\n")
    _assert_blocked(monkeypatch, f"bash {f}", "destructive")


def test_blocks_compound_invoking_interpreter_on_destructive_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = tmp_path / "rmtree.py"
    f.write_text('import shutil\nshutil.rmtree("/tmp/target")\n')
    _assert_blocked(monkeypatch, f"echo starting && python3 {f}", "destructive")


def test_blocks_python_file_using_pathlib_unlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = tmp_path / "unlink.py"
    f.write_text('from pathlib import Path\nPath("/tmp/target.txt").unlink()\n')
    _assert_blocked(monkeypatch, f"python3 {f}", "destructive")


def test_allows_file_with_destructive_refs_only_in_comments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = tmp_path / "comments.sh"
    f.write_text(
        "#!/usr/bin/env bash\n"
        "# This script demonstrates how NOT to do it - never rm -rf /\n"
        "# os.remove() and shutil.rmtree() are documented here only.\n"
        'echo "safe"\n'
    )
    _assert_allowed(monkeypatch, f"bash {f}")


def test_allows_bash_n_on_hook_own_source(monkeypatch: pytest.MonkeyPatch) -> None:
    # The hook's own source contains detection patterns as regex literals; those
    # must not trip the detector when a tool invokes `bash -n` on it.
    import cccs_hooks.bash_hard_deny as mod

    src = Path(mod.__file__)
    _assert_allowed(monkeypatch, f"bash -n {src}")


# --- Delete-by-move: mv to tmp-like destination ---


def test_blocks_mv_to_tmp_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "mv myfile.txt /tmp/", "delete-by-move")


def test_blocks_mv_to_tmp_renamed(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "mv myfile.txt /tmp/foo.txt", "delete-by-move")


def test_blocks_mv_f_to_tmp(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "mv -f myfile.txt /tmp/", "delete-by-move")


def test_blocks_mv_to_var_tmp(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "mv myfile.txt /var/tmp/archive.txt", "delete-by-move")


def test_blocks_mv_to_trash(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "mv myfile.txt ~/.Trash/", "delete-by-move")


def test_blocks_mv_to_home_tmp(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "mv myfile.txt ~/tmp/", "delete-by-move")


def test_blocks_mv_to_dollar_home_trash(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "mv myfile.txt $HOME/.Trash/", "delete-by-move")


def test_blocks_mv_to_tmp_after_and(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "echo done && mv myfile.txt /tmp/", "delete-by-move")


def test_blocks_mv_to_tmp_after_semicolon(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "echo done; mv myfile.txt /tmp/", "delete-by-move")


def test_blocks_mv_to_quoted_tmp(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, 'mv myfile.txt "/tmp/foo"', "delete-by-move")


def test_allows_mv_from_tmp_to_nontmp(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_allowed(monkeypatch, "mv /tmp/foo.txt ./out/")


def test_allows_mv_within_tmp(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_allowed(monkeypatch, "mv /tmp/foo.txt /tmp/bar.txt")


def test_allows_mv_from_var_tmp_to_tmp(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_allowed(monkeypatch, "mv /var/tmp/foo.txt /tmp/bar.txt")


def test_allows_mv_within_trash(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_allowed(monkeypatch, "mv ~/.Trash/a ~/.Trash/b")


def test_allows_mv_two_files_into_nontmp_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_allowed(monkeypatch, "mv a.txt b.txt ./out/")


def test_allows_cp_to_tmp(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_allowed(monkeypatch, "cp myfile.txt /tmp/")


def test_allows_writing_to_tmp_via_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_allowed(monkeypatch, "echo hello > /tmp/foo.txt")


def test_allows_touch_tmp_file(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_allowed(monkeypatch, "touch /tmp/foo.txt")


def test_does_not_false_positive_on_grep_for_mv(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_allowed(monkeypatch, "grep -r 'mv' /tmp/docs/")


def test_does_not_false_positive_on_echo_about_mv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_allowed(monkeypatch, "echo 'remember to mv foo to /tmp'")


def test_allows_mv_to_nontmp_dir_with_tmp_substring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_allowed(monkeypatch, "mv foo /home/user/my-tmp-work/")


# --- Delete-by-move: inline python/node script calling move to tmp ---


def test_blocks_shutil_move_to_tmp_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(
        monkeypatch,
        "python3 -c \"import shutil; shutil.move('myfile.txt', '/tmp/foo.txt')\"",
        "delete-by-move",
    )


def test_blocks_os_rename_to_tmp_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(
        monkeypatch,
        "python3 -c \"import os; os.rename('myfile.txt', '/tmp/foo.txt')\"",
        "delete-by-move",
    )


def test_blocks_os_replace_to_tmp_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(
        monkeypatch,
        "python3 -c \"import os; os.replace('myfile.txt', '/tmp/foo.txt')\"",
        "delete-by-move",
    )


def test_blocks_fs_renamesync_to_tmp_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(
        monkeypatch,
        "node -e \"require('fs').renameSync('myfile.txt', '/tmp/foo.txt')\"",
        "delete-by-move",
    )


def test_blocks_shutil_move_to_trash_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(
        monkeypatch,
        "python3 -c \"import shutil; shutil.move('myfile.txt', '~/.Trash/foo.txt')\"",
        "delete-by-move",
    )


def test_allows_shutil_move_within_tmp_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_allowed(
        monkeypatch,
        "python3 -c \"import shutil; shutil.move('/tmp/foo.txt', '/tmp/bar.txt')\"",
    )


def test_allows_shutil_move_from_tmp_to_nontmp_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_allowed(
        monkeypatch,
        "python3 -c \"import shutil; shutil.move('/tmp/foo.txt', './out/bar.txt')\"",
    )


# --- Delete-by-move: interpreter invoked on a script file ---


def test_blocks_bash_script_file_moving_to_tmp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = tmp_path / "mv.sh"
    f.write_text("#!/usr/bin/env bash\nmv myfile.txt /tmp/\n")
    _assert_blocked(monkeypatch, f"bash {f}", "delete-by-move")


def test_blocks_bash_script_file_moving_to_trash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = tmp_path / "mv_trash.sh"
    f.write_text("#!/usr/bin/env bash\nmv myfile.txt ~/.Trash/\n")
    _assert_blocked(monkeypatch, f"bash {f}", "delete-by-move")


def test_blocks_python_script_file_shutil_move_to_tmp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = tmp_path / "move.py"
    f.write_text('import shutil\nshutil.move("myfile.txt", "/tmp/foo.txt")\n')
    _assert_blocked(monkeypatch, f"python3 {f}", "delete-by-move")


def test_blocks_python_script_file_os_rename_to_tmp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = tmp_path / "rename.py"
    f.write_text('import os\nos.rename("myfile.txt", "/tmp/foo.txt")\n')
    _assert_blocked(monkeypatch, f"python3 {f}", "delete-by-move")


def test_allows_bash_script_file_move_from_tmp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = tmp_path / "from_tmp.sh"
    f.write_text("#!/usr/bin/env bash\nmv /tmp/foo.txt ./out/\n")
    _assert_allowed(monkeypatch, f"bash {f}")


def test_allows_python_script_file_shutil_move_within_tmp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = tmp_path / "within_tmp.py"
    f.write_text('import shutil\nshutil.move("/tmp/foo.txt", "/tmp/bar.txt")\n')
    _assert_allowed(monkeypatch, f"python3 {f}")


def test_allows_script_file_tmp_only_in_comment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = tmp_path / "comment_tmp.sh"
    f.write_text(
        "#!/usr/bin/env bash\n"
        "# Do not mv files to /tmp as that is a delete-by-move pattern\n"
        'echo "safe"\n'
    )
    _assert_allowed(monkeypatch, f"bash {f}")


# --- Response message format ---


def test_rm_block_message_mentions_compile_end_of_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rc, _out, err = _run_bash(monkeypatch, "rm /tmp/file.txt")
    assert rc == 2
    assert "compile" in err.lower()
    assert "end of task" in err.lower()


def test_mv_block_message_mentions_compile_end_of_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rc, _out, err = _run_bash(monkeypatch, "mv myfile.txt /tmp/")
    assert rc == 2
    assert "compile" in err.lower()
    assert "end of task" in err.lower()


# --- Heredoc bypass: destructive ops in the body ---


def test_blocks_python3_heredoc_os_remove(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = "python3 <<EOF\nimport os\nos.remove('/tmp/target.txt')\nEOF"
    _assert_blocked(monkeypatch, cmd, "destructive")


def test_blocks_bash_heredoc_rm_rf(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = "bash <<EOF\necho starting\nrm -rf /tmp/target-dir\necho done\nEOF"
    _assert_blocked(monkeypatch, cmd, "destructive")


def test_blocks_python3_heredoc_quoted_delim_rmtree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmd = "python3 <<'EOF'\nimport shutil\nshutil.rmtree('/tmp/target')\nEOF"
    _assert_blocked(monkeypatch, cmd, "destructive")


def test_blocks_indented_heredoc_dash_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    # Body and terminator indented with literal tabs; <<- strips them.
    cmd = "bash <<-EOF\n\trm -rf /tmp/target\n\tEOF\n"
    _assert_blocked(monkeypatch, cmd, "destructive")


def test_allows_heredoc_destructive_only_in_comments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmd = (
        "bash <<EOF\n"
        "# Below is the kind of thing we never do: rm -rf /\n"
        "# Also avoid os.remove() and shutil.rmtree() in production\n"
        "echo safe\n"
        "EOF"
    )
    _assert_allowed(monkeypatch, cmd)


def test_allows_heredoc_benign_body(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = "python3 <<EOF\nprint('hello, world')\nx = 1 + 1\nprint(f'2 == {x}')\nEOF"
    _assert_allowed(monkeypatch, cmd)


# --- Heredoc bypass: delete-by-move patterns inside the body ---


def test_blocks_bash_heredoc_mv_to_tmp(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = "bash <<EOF\nmv /home/alice/important.txt /tmp/\nEOF"
    _assert_blocked(monkeypatch, cmd, "delete-by-move")


def test_blocks_python3_heredoc_shutil_move_to_tmp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmd = (
        "python3 <<EOF\nimport shutil\n"
        "shutil.move('/home/alice/important.txt', '/tmp/x.txt')\nEOF"
    )
    _assert_blocked(monkeypatch, cmd, "delete-by-move")


def test_blocks_python3_heredoc_os_rename_to_tmp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmd = (
        "python3 <<EOF\nimport os\n"
        "os.rename('/home/alice/important.txt', '/tmp/x.txt')\nEOF"
    )
    _assert_blocked(monkeypatch, cmd, "delete-by-move")


def test_blocks_node_heredoc_fs_renamesync_to_tmp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cmd = (
        "node <<EOF\n"
        "require('fs').renameSync('/home/alice/important.txt', '/tmp/x.txt')\nEOF"
    )
    _assert_blocked(monkeypatch, cmd, "delete-by-move")


def test_allows_heredoc_mv_within_tmp(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = "bash <<EOF\nmv /tmp/a.txt /tmp/b.txt\nEOF"
    _assert_allowed(monkeypatch, cmd)


def test_allows_heredoc_shutil_move_within_tmp(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = "python3 <<EOF\nimport shutil\nshutil.move('/tmp/a.txt', '/tmp/b.txt')\nEOF"
    _assert_allowed(monkeypatch, cmd)


# ---------- Net-new coverage: opentabs plugin_mark_reviewed ----------


def test_blocks_opentabs_mark_reviewed(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(
        monkeypatch, "opentabs tool call plugin_mark_reviewed gwr", "plugin_mark_reviewed"
    )


def test_blocks_opentabs_mark_reviewed_after_separator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_blocked(
        monkeypatch,
        "echo hi && opentabs tool call plugin_mark_reviewed some-plugin",
        "plugin_mark_reviewed",
    )


def test_block_message_echoes_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = "opentabs tool call plugin_mark_reviewed gwr"
    rc, _out, err = _run_bash(monkeypatch, cmd)
    assert rc == 2
    assert cmd in err  # the message copy-pastes the exact command back


def test_allows_opentabs_other_tool_call(monkeypatch: pytest.MonkeyPatch) -> None:
    # Near-miss: a different opentabs tool call is not the self-approval one.
    _assert_allowed(monkeypatch, "opentabs tool call plugin_list_tabs")


def test_allows_word_boundary_myopentabs(monkeypatch: pytest.MonkeyPatch) -> None:
    # `myopentabs` must not match (preceding char is a word char).
    _assert_allowed(monkeypatch, "myopentabs tool call plugin_mark_reviewed gwr")


# ---------- Net-new coverage: gh api DELETE ----------


def test_blocks_gh_api_x_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "gh api -X DELETE /repos/o/r/issues/1", "gh api")


def test_blocks_gh_api_method_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(
        monkeypatch, "gh api repos/o/r/git/refs/tags/v1 --method DELETE", "gh api"
    )


def test_blocks_gh_api_method_delete_equals(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "gh api some/endpoint --method=DELETE", "gh api")


def test_allows_gh_api_get(monkeypatch: pytest.MonkeyPatch) -> None:
    # Near-miss: a read-only gh api call has no DELETE method.
    _assert_allowed(monkeypatch, "gh api /repos/o/r/issues")


# ---------- Net-new coverage: gh release delete / rm ----------


def test_blocks_gh_release_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "gh release delete v1.2.3 --yes", "gh release delete")


def test_blocks_gh_release_rm(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_blocked(monkeypatch, "gh release rm v1.2.3", "gh release delete")


def test_allows_gh_release_list(monkeypatch: pytest.MonkeyPatch) -> None:
    # Near-miss: listing releases is not a delete.
    _assert_allowed(monkeypatch, "gh release list")


def test_allows_gh_release_create(monkeypatch: pytest.MonkeyPatch) -> None:
    _assert_allowed(monkeypatch, "gh release create v1.2.3 --notes 'x'")


# ---------- fires.jsonl telemetry-log reads (ported bats + path-fix) ----------


@pytest.mark.parametrize(
    "reader", ["cat", "tail", "head", "hexdump", "xxd"]
)
def test_blocks_fires_jsonl_old_path(
    monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    monkeypatch.delenv("CCCS_FIRES_ACCESS", raising=False)
    rc, _out, _err = _run_bash(monkeypatch, f"{reader} ~/.claude/hooks/fires.jsonl")
    assert rc == 2


def test_allows_fires_jsonl_with_access_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCCS_FIRES_ACCESS", "1")
    _assert_allowed(monkeypatch, "cat ~/.claude/hooks/fires.jsonl")


def test_allows_unrelated_cat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CCCS_FIRES_ACCESS", raising=False)
    _assert_allowed(monkeypatch, "cat /tmp/somefile.txt")


def test_blocks_fires_rotated_gz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CCCS_FIRES_ACCESS", raising=False)
    rc, _out, _err = _run_bash(
        monkeypatch, "cat ~/.claude/hooks/fires.2026-W20.jsonl.gz"
    )
    assert rc == 2


def test_blocks_fires_jsonl_real_cache_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # Path-fix: the real telemetry location (~/.cache/claude/logs) is blocked too.
    monkeypatch.delenv("CCCS_FIRES_ACCESS", raising=False)
    rc, _out, _err = _run_bash(monkeypatch, "cat ~/.cache/claude/logs/fires.jsonl")
    assert rc == 2


def test_allows_fires_real_cache_path_with_access_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CCCS_FIRES_ACCESS", "1")
    _assert_allowed(monkeypatch, "cat ~/.cache/claude/logs/fires.jsonl")


# ---------- main() robustness ----------


def test_main_handles_empty_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    assert main() == 0
    assert out.getvalue() == ""


def test_main_handles_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    assert main() == 0
    assert out.getvalue() == ""
