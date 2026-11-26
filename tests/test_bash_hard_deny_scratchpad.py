"""Tests for bash-hard-deny's scratchpad delete allowance and command-position matching.

Each test drives ``main()`` with a full PreToolUse payload (``session_id``,
``transcript_path``, ``cwd``) whose scratchpad lives under a pytest temp dir, by pointing
``CLAUDE_CODE_TMPDIR`` at it.
"""
from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from hooks.bash_hard_deny import derive_scratchpad, main

SESSION_ID = "11111111-2222-3333-4444-555555555555"
OTHER_SESSION_ID = "99999999-8888-7777-6666-555555555555"
ENCODED_PROJECT = "-home-alice-repos-proj"


@dataclass(frozen=True)
class Env:
    scratchpad: Path
    session_dir: Path
    other_scratchpad: Path
    outside: Path
    transcript_path: str


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    tmp_root = tmp_path / "tmproot"
    tmp_root.mkdir()
    monkeypatch.setenv("CLAUDE_CODE_TMPDIR", str(tmp_root))
    monkeypatch.delenv("CCST_FIRES_ACCESS", raising=False)
    claude_root = Path(os.path.realpath(tmp_root)) / f"claude-{os.getuid()}" / ENCODED_PROJECT
    scratchpad = claude_root / SESSION_ID / "scratchpad"
    (scratchpad / "build" / "tmp").mkdir(parents=True)
    (scratchpad / "out.txt").write_text("x")
    (scratchpad / "build" / "a.o").write_text("x")
    (scratchpad / "build" / "b.o").write_text("x")
    other = claude_root / OTHER_SESSION_ID / "scratchpad"
    other.mkdir(parents=True)
    (other / "theirs.txt").write_text("x")
    outside = Path(os.path.realpath(tmp_path)) / "project"
    for sub in ("working", "out"):
        (outside / "cc-sessions" / "tag" / sub).mkdir(parents=True)
        (outside / "cc-sessions" / "tag" / sub / "draft.md").write_text("x")
    (outside / "notes.txt").write_text("x")
    transcript = tmp_path / "projects" / ENCODED_PROJECT / f"{SESSION_ID}.jsonl"
    return Env(scratchpad, claude_root / SESSION_ID, other, outside, str(transcript))


def _run(
    monkeypatch: pytest.MonkeyPatch, env: Env, command: str, *, cwd: str | None = None,
    session_id: str | None = SESSION_ID,
) -> tuple[int, str, str]:
    payload: dict[str, object] = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "transcript_path": env.transcript_path,
        "cwd": cwd if cwd is not None else str(env.outside),
    }
    if session_id is not None:
        payload["session_id"] = session_id
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", err)
    rc = main()
    return rc, out.getvalue(), err.getvalue()


def _allowed(monkeypatch: pytest.MonkeyPatch, env: Env, command: str, **kw: str) -> None:
    rc, out, err = _run(monkeypatch, env, command, **kw)
    assert rc == 0, f"expected allow for {command!r}, got rc={rc}: {err}"
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "allow"


def _blocked(monkeypatch: pytest.MonkeyPatch, env: Env, command: str, **kw: str) -> str:
    rc, _out, err = _run(monkeypatch, env, command, **kw)
    assert rc == 2, f"expected block for {command!r}, got rc={rc}"
    return err


# --- Allowed: deletes inside this session's scratchpad ---


def test_allows_file_in_scratchpad(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _allowed(monkeypatch, env, f"rm -f {env.scratchpad}/out.txt")


def test_allows_nested_dir_in_scratchpad(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _allowed(monkeypatch, env, f"rm -rf {env.scratchpad}/build/tmp")


@pytest.mark.parametrize("verb", ["rmdir", "unlink", "shred -u", "grm -f", "/bin/rm", "\\rm"])
def test_allows_other_delete_verbs_in_scratchpad(
    monkeypatch: pytest.MonkeyPatch, env: Env, verb: str
) -> None:
    target = "build/tmp" if verb == "rmdir" else "out.txt"
    _allowed(monkeypatch, env, f"{verb} {env.scratchpad}/{target}")


def test_allows_quoted_scratchpad_path(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _allowed(monkeypatch, env, f"rm -f '{env.scratchpad}/out.txt' \"{env.scratchpad}/build/a.o\"")


def test_allows_glob_below_scratchpad(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _allowed(monkeypatch, env, f"rm -rf {env.scratchpad}/*")
    _allowed(monkeypatch, env, f"rm -f {env.scratchpad}/build/*.o")


def test_allows_missing_target_for_rm(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _allowed(monkeypatch, env, f"rm -f {env.scratchpad}/not-there.txt")


def test_allows_relative_target_with_scratchpad_cwd(
    monkeypatch: pytest.MonkeyPatch, env: Env
) -> None:
    _allowed(monkeypatch, env, "rm out.txt build/a.o", cwd=str(env.scratchpad))


def test_allows_delete_with_redirect_and_compound(
    monkeypatch: pytest.MonkeyPatch, env: Env
) -> None:
    _allowed(
        monkeypatch, env, f"rm -f {env.scratchpad}/out.txt 2>/dev/null && ls {env.scratchpad}"
    )


def test_allows_delete_inside_bash_c(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _allowed(monkeypatch, env, f"bash -c 'rm -f {env.scratchpad}/out.txt'")


def test_allows_find_delete_under_scratchpad(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _allowed(monkeypatch, env, f"find {env.scratchpad}/build -name '*.o' -delete")


def test_allows_find_exec_rm_under_scratchpad(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _allowed(monkeypatch, env, f"find {env.scratchpad}/build -name '*.o' -exec rm -f {{}} +")


def test_allows_inline_python_delete_in_scratchpad(
    monkeypatch: pytest.MonkeyPatch, env: Env
) -> None:
    _allowed(monkeypatch, env, f"python3 -c \"import os; os.remove('{env.scratchpad}/out.txt')\"")
    _allowed(
        monkeypatch, env,
        f"python3 -c \"import shutil; shutil.rmtree('{env.scratchpad}/build', ignore_errors=True)\"",
    )


def test_allows_inline_node_delete_in_scratchpad(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _allowed(
        monkeypatch, env,
        f"node -e \"const fs = require('fs'); fs.unlinkSync('{env.scratchpad}/out.txt')\"",
    )


def test_allows_tempfile_self_cleanup(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _allowed(
        monkeypatch, env,
        "python3 -c \"import os, tempfile; fd, p = tempfile.mkstemp(); os.close(fd); os.remove(p)\"",
    )


def test_allows_mv_within_and_out_of_scratchpad(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _allowed(monkeypatch, env, f"mv {env.scratchpad}/out.txt {env.scratchpad}/renamed.txt")
    _allowed(monkeypatch, env, f"mv {env.scratchpad}/out.txt {env.outside}/kept.txt")


# --- Blocked: everything else ---


def test_blocks_scratchpad_root_itself(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _blocked(monkeypatch, env, f"rm -rf {env.scratchpad}")
    _blocked(monkeypatch, env, f"rm -rf {env.scratchpad}/")
    _blocked(monkeypatch, env, f"rm -rf {env.scratchpad}/.")
    _blocked(monkeypatch, env, f"rm -rf {env.scratchpad}/build/..")


@pytest.mark.parametrize("sub", ["working", "out"])
def test_blocks_session_working_and_out(
    monkeypatch: pytest.MonkeyPatch, env: Env, sub: str
) -> None:
    _blocked(monkeypatch, env, f"rm -f {env.outside}/cc-sessions/tag/{sub}/draft.md")


def test_blocks_other_sessions_scratchpad(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _blocked(monkeypatch, env, f"rm -f {env.other_scratchpad}/theirs.txt")


def test_blocks_dotdot_escape(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _blocked(monkeypatch, env, f"rm -f {env.scratchpad}/../../{OTHER_SESSION_ID}/scratchpad/theirs.txt")
    _blocked(monkeypatch, env, f"rm -rf {env.scratchpad}/build/../../scratchpad")


def test_blocks_symlink_escape(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    (env.scratchpad / "link").symlink_to(env.outside)
    (env.scratchpad / "filelink").symlink_to(env.outside / "notes.txt")
    _blocked(monkeypatch, env, f"rm -rf {env.scratchpad}/link/")
    _blocked(monkeypatch, env, f"rm -f {env.scratchpad}/link/notes.txt")
    _blocked(monkeypatch, env, f"shred {env.scratchpad}/filelink")


def test_blocks_glob_matching_symlink_escape(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    (env.scratchpad / "link").symlink_to(env.outside)
    _blocked(monkeypatch, env, f"rm -rf {env.scratchpad}/*/notes.txt")


def test_blocks_mixed_targets(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _blocked(monkeypatch, env, f"rm {env.scratchpad}/out.txt {env.outside}/notes.txt")
    _blocked(monkeypatch, env, f"rm {env.scratchpad}/out.txt; rm {env.outside}/notes.txt")


@pytest.mark.parametrize("target", [
    '"$SCRATCH/out.txt"',
    "$(echo out.txt)",
    "`echo out.txt`",
    "{SCRATCHPAD}/{a,b}.txt",
    "~/notes.txt",
    "${HOME}/x",
])
def test_blocks_unresolvable_targets(
    monkeypatch: pytest.MonkeyPatch, env: Env, target: str
) -> None:
    _blocked(monkeypatch, env, "rm -f " + target.replace("{SCRATCHPAD}", str(env.scratchpad)))


def test_blocks_glob_at_or_above_scratchpad(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _blocked(monkeypatch, env, f"rm -rf {env.session_dir}/scr*")
    _blocked(monkeypatch, env, f"rm -rf {env.session_dir}/*/out.txt")
    _blocked(monkeypatch, env, f"rm -rf {env.scratchpad}/*/../..")


def test_blocks_relative_target_after_cd(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _blocked(monkeypatch, env, f"cd {env.scratchpad} && rm out.txt")
    _blocked(monkeypatch, env, "rm out.txt", cwd=str(env.outside))


def test_blocks_missing_target_for_shred_or_trailing_slash(
    monkeypatch: pytest.MonkeyPatch, env: Env
) -> None:
    _blocked(monkeypatch, env, f"shred {env.scratchpad}/not-there")
    _blocked(monkeypatch, env, f"rm -rf {env.scratchpad}/not-there/")
    _blocked(monkeypatch, env, f"rm -rf {env.scratchpad}/no-parent/x")


def test_blocks_link_created_in_same_command(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _blocked(monkeypatch, env, f"ln -s {env.outside} {env.scratchpad}/d && rm -rf {env.scratchpad}/d/notes.txt")
    _blocked(
        monkeypatch, env,
        f"mv {env.scratchpad}/build {env.scratchpad}/b2 && rm -rf {env.scratchpad}/build/tmp",
    )


def test_blocks_find_delete_outside_or_following_links(
    monkeypatch: pytest.MonkeyPatch, env: Env
) -> None:
    _blocked(monkeypatch, env, f"find {env.outside} -name '*.txt' -delete")
    _blocked(monkeypatch, env, f"find -L {env.scratchpad}/build -delete")
    _blocked(monkeypatch, env, f"find {env.scratchpad} -delete")
    _blocked(monkeypatch, env, "find . -name '*.pyc' -delete", cwd=str(env.scratchpad))


def test_blocks_execdir_relative_target(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    # -execdir runs in each match's directory, so a relative target is not where it looks.
    _blocked(
        monkeypatch, env, f"find {env.outside} -name notes.txt -execdir rm -f out.txt \\;",
        cwd=str(env.scratchpad),
    )


def test_blocks_shred_of_hard_linked_file(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    os.link(env.outside / "notes.txt", env.scratchpad / "hardlink.txt")
    _blocked(monkeypatch, env, f"shred -u {env.scratchpad}/hardlink.txt")
    _allowed(monkeypatch, env, f"rm -f {env.scratchpad}/hardlink.txt")


def test_blocks_rmdir_parents(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _blocked(monkeypatch, env, f"rmdir -p {env.scratchpad}/build/tmp")


def test_blocks_inline_relative_literal(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _blocked(
        monkeypatch, env, "python3 -c \"import os; os.remove('out.txt')\"",
        cwd=str(env.scratchpad),
    )


def test_blocks_xargs_rm_even_in_scratchpad(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _blocked(monkeypatch, env, f"ls {env.scratchpad} | xargs rm -f")
    _blocked(monkeypatch, env, f"ls {env.scratchpad} | xargs -I{{}} rm -f {env.scratchpad}/{{}}")


def test_blocks_inline_python_delete_outside(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _blocked(monkeypatch, env, f"python3 -c \"import os; os.remove('{env.outside}/notes.txt')\"")
    _blocked(monkeypatch, env, "python3 -c \"import os, sys; os.remove(sys.argv[1])\" x")
    _blocked(
        monkeypatch, env,
        f"python3 -c \"import os; os.remove(f'{env.scratchpad}/out.txt')\"",
    )
    _blocked(
        monkeypatch, env,
        f"python3 -c \"import os; os.symlink('{env.outside}', '{env.scratchpad}/d'); "
        f"os.remove('{env.scratchpad}/d/notes.txt')\"",
    )


def test_blocks_inline_node_delete_outside(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _blocked(
        monkeypatch, env,
        f"node -e \"require('fs'); fs.unlinkSync('{env.outside}/notes.txt')\"",
    )


def test_blocks_all_deletes_without_session_id(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    err = _blocked(monkeypatch, env, f"rm -f {env.scratchpad}/out.txt", session_id=None)
    assert "claude-<uid>" in err


def test_blocks_mv_into_scratchpad(monkeypatch: pytest.MonkeyPatch, env: Env) -> None:
    _blocked(monkeypatch, env, f"mv {env.outside}/notes.txt {env.scratchpad}/notes.txt")
    _blocked(monkeypatch, env, f"bash -c 'mv {env.outside}/notes.txt {env.scratchpad}/'")


# --- Block message ---


def test_block_message_names_scratchpad_and_deletion_list(
    monkeypatch: pytest.MonkeyPatch, env: Env
) -> None:
    err = _blocked(monkeypatch, env, f"rm -f {env.outside}/notes.txt")
    assert "deletion list" in err
    assert str(env.scratchpad) in err


# --- Command positions: mentions are not commands ---


@pytest.mark.parametrize("command", [
    'grep -rn "; rm -rf" src/',
    "grep -E '(^|;) *rm ' x.sh",
    'echo "then rm the file"',
    "echo 'a | unlink b'",
    "printf 'x && shred y\\n'",
    'git commit -m "stop blocking rm -rf in echo; rm x"',
    "cat > notes.md <<'EOF'\nrm -rf /\nshred x\nEOF",
    "ls # && rm -rf /",
    "command -v rm",
    "grep -c 'os.remove' x.py && python3 -c 'print(1)'",
])
def test_mentions_are_not_commands(
    monkeypatch: pytest.MonkeyPatch, env: Env, command: str
) -> None:
    _allowed(monkeypatch, env, command)


@pytest.mark.parametrize("command", [
    "bash -c 'rm -rf {OUT}'",
    "sh -c \"rm {OUT}\"",
    "zsh -lc 'rm {OUT}'",
    "bash -o pipefail -c 'rm {OUT}'",
    "eval 'rm {OUT}'",
    "echo $(rm {OUT})",
    "echo `rm {OUT}`",
    'echo "$(rm {OUT})"',
    "( rm {OUT} )",
    "{ rm {OUT}; }",
    "true; rm {OUT}",
    "true && rm {OUT}",
    "false || rm {OUT}",
    "echo x | rm {OUT}",
    "true\nrm {OUT}",
    "FOO=1 rm {OUT}",
    "env FOO=1 rm {OUT}",
    "env -i rm {OUT}",
    "env -S 'rm {OUT}'",
    "command rm {OUT}",
    "builtin command rm {OUT}",
    "exec rm {OUT}",
    "nohup rm {OUT}",
    "time rm {OUT}",
    "nice -n 5 rm {OUT}",
    "timeout -s KILL 5 rm {OUT}",
    "stdbuf -oL rm {OUT}",
    "busybox rm {OUT}",
    "\\rm {OUT}",
    '"rm" {OUT}',
    "r''m {OUT}",
    "$'\\x72m' {OUT}",
    "/bin/rm {OUT}",
    "grm {OUT}",
    "if true; then rm {OUT}; fi",
    "for f in a; do rm {OUT}; done",
    "x=$(rm {OUT})",
    "cat <(rm {OUT})",
    "echo ${X:-$(rm {OUT})}",
    "cat <<EOF\n$(rm {OUT})\nEOF",
    "find . -name '*.pyc' -delete",
    "find . -exec rm {} \\;",
    "find . -exec sh -c 'rm \"$1\"' _ {} \\;",
    "ls | xargs rm",
    "ls | xargs -0 -n1 rm -f",
    "rm {OUT} \"unterminated",
    "rm${IFS}{OUT}",
    "RM {OUT}",
    "Bash -c 'rm {OUT}'",
    "bash <<< 'rm {OUT}'",
    "echo 'rm {OUT}' | bash",
    "printf 'rm {OUT}' | sh -s",
    "python3 <<< \"import os; os.remove('{OUT}')\"",
])
def test_real_command_positions_are_blocked(
    monkeypatch: pytest.MonkeyPatch, env: Env, command: str
) -> None:
    _blocked(monkeypatch, env, command.replace("{OUT}", f"{env.outside}/notes.txt"))


# --- Scratchpad derivation ---


def test_derive_scratchpad_from_payload(env: Env) -> None:
    assert derive_scratchpad(SESSION_ID, env.transcript_path) == env.scratchpad


@pytest.mark.parametrize("session_id, transcript", [
    (None, "/x/projects/-a/s.jsonl"),
    ("", "/x/projects/-a/s.jsonl"),
    ("../other", "/x/projects/-a/s.jsonl"),
    ("a/b", "/x/projects/-a/s.jsonl"),
    (SESSION_ID, None),
    (SESSION_ID, ""),
    (SESSION_ID, "/x/projects/../s.jsonl"),
    (SESSION_ID, "s.jsonl"),
])
def test_derive_scratchpad_rejects_unusable_payload(
    env: Env, session_id: object, transcript: object
) -> None:
    assert derive_scratchpad(session_id, transcript) is None


def test_derive_scratchpad_rejects_symlinked_component(env: Env, tmp_path: Path) -> None:
    redirected = tmp_path / "elsewhere"
    redirected.mkdir()
    link_session = env.session_dir.parent / "22222222-2222-2222-2222-222222222222"
    link_session.symlink_to(redirected)
    transcript = env.transcript_path.replace(SESSION_ID, link_session.name)
    assert derive_scratchpad(link_session.name, transcript) is None
