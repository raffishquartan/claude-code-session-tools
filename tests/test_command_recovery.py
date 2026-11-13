"""Tests for cc_session_tools.lib.command_recovery: bash-security-review hash ->
command (or session) recovery over synthetic projects and telemetry dirs."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pytest

from cc_session_tools.lib import command_recovery as cr
from cc_session_tools.lib import paths, telemetry_store

KEY = "-home-alice-repos-demo"
SESSION_A = "11111111-1111-4111-8111-111111111111"
SESSION_B = "22222222-2222-4222-8222-222222222222"


def _sha(command: str) -> str:
    return hashlib.sha256(command.encode()).hexdigest()


def _bash_line(command: str) -> str:
    """One assistant transcript record carrying a Bash tool_use, in the compact
    JSON form Claude Code writes."""
    record = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "toolu_x", "name": "Bash",
                 "input": {"command": command, "description": "d"}},
            ],
        },
    }
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False)


def _text_line(text: str) -> str:
    record = {"type": "user", "message": {"role": "user", "content": text}}
    return json.dumps(record, separators=(",", ":"))


def _write(path: Path, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return path


def _main(projects: Path, session: str, lines: list[str], key: str = KEY) -> Path:
    return _write(projects / key / f"{session}.jsonl", lines)


def _subagent(projects: Path, session: str, name: str, lines: list[str]) -> Path:
    return _write(projects / KEY / session / "subagents" / f"agent-{name}.jsonl", lines)


def _fire(
    hooks_dir: Path, input_hash: str, session: str, ts: str = "2026-11-01T00:00:00Z",
    hook: str = "bash-security-review",
) -> None:
    conn = telemetry_store.connect(hooks_dir)
    conn.execute(
        "INSERT INTO telemetry_events "
        "(ts, hook, event, tool, session_id, cwd_short, decision, cache, verdict, input_hash) "
        "VALUES (?, ?, 'PreToolUse', 'Bash', ?, 'x', 'allow', 'miss', 'safe', ?)",
        (ts, hook, session, f"sha256:{input_hash}"),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    projects = tmp_path / "projects"
    projects.mkdir()
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    telemetry_store.connect(hooks).close()
    return projects, hooks


def _count_reads(monkeypatch: pytest.MonkeyPatch) -> Counter[Path]:
    counts: Counter[Path] = Counter()
    real = cr.iter_bash_commands

    def counting(path: Path) -> Iterator[tuple[int, str]]:
        counts[path] += 1
        return real(path)

    monkeypatch.setattr(cr, "iter_bash_commands", counting)
    return counts


# ---------- argument normalisation ----------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("abcdef12", "abcdef12"),
        ("ABCDEF12", "abcdef12"),
        ("sha256:abcdef12", "abcdef12"),
        ("SHA256:ABCDEF12", "abcdef12"),
        ("a" * 64, "a" * 64),
    ],
)
def test_normalise_hash_arg_accepts_forms(raw: str, expected: str) -> None:
    assert cr.normalise_hash_arg(raw) == expected


@pytest.mark.parametrize("raw", ["abcdef1", "", "sha256:", "abcdefgh", "abcdef12 ", "a" * 65, "abc-ef12"])
def test_normalise_hash_arg_rejects_malformed(raw: str) -> None:
    with pytest.raises(cr.MalformedHashError) as exc:
        cr.normalise_hash_arg(raw)
    assert exc.value.arg == raw
    assert repr(raw) in str(exc.value)


def test_normalise_args_collapses_duplicates_in_order() -> None:
    assert cr.normalise_args(["bbbbbbbb", "AAAAAAAA", "sha256:bbbbbbbb", "aaaaaaaa"]) == [
        "bbbbbbbb", "aaaaaaaa",
    ]


def test_normalise_args_raises_on_first_malformed() -> None:
    with pytest.raises(cr.MalformedHashError) as exc:
        cr.normalise_args(["aaaaaaaa", "xyz", "short"])
    assert exc.value.arg == "xyz"


# ---------- escaping and truncation ----------


def test_escape_command_escapes_controls_keeps_non_ascii() -> None:
    assert cr.escape_command("a\tb\r\nc\x01d\x1be é") == "a\\tb\\r\\nc\\u0001d\\u001be é"


def test_format_command_no_cut_when_short() -> None:
    assert cr.format_command("echo hi", 200) == "echo hi"


def test_format_command_truncates_with_elided_count() -> None:
    out = cr.format_command("x" * 250, 200)
    assert out == "x" * 200 + "…[+50 chars]"


def test_format_command_never_splits_an_escape() -> None:
    # 9 plain characters then a newline: the escaped text is "123456789\n" (11 chars);
    # a cut at 10 would leave a dangling backslash, so the whole escape is dropped.
    out = cr.format_command("123456789\nZ", 10)
    assert out == "123456789…[+3 chars]"


def test_format_command_zero_means_unlimited() -> None:
    command = "y" * 5000 + "\n" + "z"
    assert cr.format_command(command, 0) == "y" * 5000 + "\\n" + "z"


# ---------- recovery ----------


def test_exact_match_reports_command(dirs: tuple[Path, Path]) -> None:
    projects, hooks = dirs
    command = "git status"
    path = _main(projects, SESSION_A, [_text_line("hello"), _bash_line(command)])
    _fire(hooks, _sha(command), SESSION_A)
    [rec] = cr.recover([_sha(command)], projects_dir=projects, hooks_dir=hooks)
    assert rec.status is cr.RecoveryStatus.COMMAND
    assert rec.full_hash == _sha(command)
    assert rec.session_id == SESSION_A
    assert rec.location == f"{path}:2"
    assert rec.command == command


def test_near_miss_is_not_reported(dirs: tuple[Path, Path]) -> None:
    projects, hooks = dirs
    _main(projects, SESSION_A, [_bash_line("git  status"), _bash_line("Git status")])
    _fire(hooks, _sha("git status"), SESSION_A)
    [rec] = cr.recover([_sha("git status")], projects_dir=projects, hooks_dir=hooks)
    assert rec.status is cr.RecoveryStatus.SESSION
    assert rec.command == ""
    assert rec.full_hash == ""


def test_prefix_resolves_to_full_hash(dirs: tuple[Path, Path]) -> None:
    projects, hooks = dirs
    command = "ls -la"
    _main(projects, SESSION_A, [_bash_line(command)])
    _fire(hooks, _sha(command), SESSION_A)
    prefix = _sha(command)[:8]
    [rec] = cr.recover([prefix], projects_dir=projects, hooks_dir=hooks)
    assert rec.arg == prefix
    assert rec.status is cr.RecoveryStatus.COMMAND
    assert rec.full_hash == _sha(command)


def _two_commands_sharing_prefix(n: int = 2) -> tuple[str, str, str]:
    """Find two distinct commands whose hashes share their first ``n`` hex
    characters (cheap at n=2), returning (command1, command2, shared_prefix)."""
    seen: dict[str, str] = {}
    i = 0
    while True:
        command = f"echo {i}"
        prefix = _sha(command)[:n]
        if prefix in seen:
            return seen[prefix], command, prefix
        seen[prefix] = command
        i += 1


def test_ambiguous_prefix_from_telemetry(dirs: tuple[Path, Path]) -> None:
    """recover() takes already-validated arguments, so a short shared prefix
    stands in for an 8-character collision that would be costly to search for."""
    projects, hooks = dirs
    first, second, _ = _two_commands_sharing_prefix()
    shared = _shared_prefix(_sha(first), _sha(second))
    _main(projects, SESSION_A, [_bash_line(first), _bash_line(second)])
    _fire(hooks, _sha(first), SESSION_A)
    _fire(hooks, _sha(second), SESSION_A)
    [rec] = cr.recover([shared], projects_dir=projects, hooks_dir=hooks)
    assert rec.status is cr.RecoveryStatus.AMBIGUOUS
    assert rec.command == ""
    assert rec.full_hash == ""


def test_ambiguous_prefix_from_scan_all(dirs: tuple[Path, Path]) -> None:
    """Neither hash is in telemetry; with --scan-all both transcript commands
    match the prefix, so it is ambiguous rather than picking one."""
    projects, hooks = dirs
    first, second, _ = _two_commands_sharing_prefix()
    shared = _shared_prefix(_sha(first), _sha(second))
    _main(projects, SESSION_A, [_bash_line(first)])
    _main(projects, SESSION_B, [_bash_line(second)])
    [rec] = cr.recover([shared], projects_dir=projects, hooks_dir=hooks, scan_all=True)
    assert rec.status is cr.RecoveryStatus.AMBIGUOUS


def _shared_prefix(a: str, b: str) -> str:
    n = 0
    while a[n] == b[n]:
        n += 1
    return a[:n]


def test_subagent_only_command_found_with_subagent_path(dirs: tuple[Path, Path]) -> None:
    projects, hooks = dirs
    command = "uv run pytest -q"
    _main(projects, SESSION_A, [_bash_line("echo main")])
    sub = _subagent(projects, SESSION_A, "abc123", [_text_line("t"), _text_line("u"), _bash_line(command)])
    _fire(hooks, _sha(command), SESSION_A)
    [rec] = cr.recover([_sha(command)], projects_dir=projects, hooks_dir=hooks)
    assert rec.status is cr.RecoveryStatus.COMMAND
    assert rec.session_id == SESSION_A
    assert rec.location == f"{sub}:3"


def test_session_fallback_when_transcripts_missing(dirs: tuple[Path, Path]) -> None:
    projects, hooks = dirs
    _fire(hooks, _sha("gone"), SESSION_A)
    [rec] = cr.recover([_sha("gone")], projects_dir=projects, hooks_dir=hooks)
    assert rec.status is cr.RecoveryStatus.SESSION
    assert rec.session_id == SESSION_A
    assert rec.location == ""


def test_session_fallback_names_existing_transcript(dirs: tuple[Path, Path]) -> None:
    projects, hooks = dirs
    path = _main(projects, SESSION_A, [_bash_line("something else")])
    _fire(hooks, _sha("not in transcript"), SESSION_A)
    [rec] = cr.recover([_sha("not in transcript")], projects_dir=projects, hooks_dir=hooks)
    assert rec.status is cr.RecoveryStatus.SESSION
    assert rec.location == str(path)


def test_session_fallback_names_most_recent_firing_session(dirs: tuple[Path, Path]) -> None:
    projects, hooks = dirs
    h = _sha("absent everywhere")
    _fire(hooks, h, SESSION_B, ts="2026-11-05T00:00:00Z")
    _fire(hooks, h, SESSION_A, ts="2026-11-01T00:00:00Z")
    [rec] = cr.recover([h], projects_dir=projects, hooks_dir=hooks)
    assert rec.status is cr.RecoveryStatus.SESSION
    assert rec.session_id == SESSION_B


def test_multi_session_hash_searches_every_firing_session(dirs: tuple[Path, Path]) -> None:
    projects, hooks = dirs
    command = "make check"
    _main(projects, SESSION_A, [_bash_line(command)])
    _main(projects, SESSION_B, [_bash_line("other")])
    _fire(hooks, _sha(command), SESSION_A, ts="2026-11-01T00:00:00Z")
    _fire(hooks, _sha(command), SESSION_B, ts="2026-11-05T00:00:00Z")
    [rec] = cr.recover([_sha(command)], projects_dir=projects, hooks_dir=hooks)
    assert rec.status is cr.RecoveryStatus.COMMAND
    assert rec.session_id == SESSION_A


def test_unknown_when_no_telemetry_row(dirs: tuple[Path, Path]) -> None:
    projects, hooks = dirs
    command = "echo nobody fired me"
    _main(projects, SESSION_A, [_bash_line(command)])
    [rec] = cr.recover([_sha(command)], projects_dir=projects, hooks_dir=hooks)
    assert rec.status is cr.RecoveryStatus.UNKNOWN
    assert rec.session_id == ""
    assert rec.location == ""


def test_other_hooks_rows_are_ignored(dirs: tuple[Path, Path]) -> None:
    projects, hooks = dirs
    _fire(hooks, _sha("x"), SESSION_A, hook="bash-hard-deny")
    [rec] = cr.recover([_sha("x")], projects_dir=projects, hooks_dir=hooks)
    assert rec.status is cr.RecoveryStatus.UNKNOWN


def test_scan_all_finds_command_without_telemetry(dirs: tuple[Path, Path]) -> None:
    projects, hooks = dirs
    command = "echo only on disk"
    path = _subagent(projects, SESSION_B, "zz", [_bash_line(command)])
    [rec] = cr.recover([_sha(command)], projects_dir=projects, hooks_dir=hooks, scan_all=True)
    assert rec.status is cr.RecoveryStatus.COMMAND
    assert rec.session_id == SESSION_B
    assert rec.location == f"{path}:1"


def test_results_follow_argument_order(dirs: tuple[Path, Path]) -> None:
    projects, hooks = dirs
    _fire(hooks, _sha("b"), SESSION_A)
    recs = cr.recover([_sha("b"), _sha("a")[:10]], projects_dir=projects, hooks_dir=hooks)
    assert [r.arg for r in recs] == [_sha("b"), _sha("a")[:10]]


def test_unparseable_and_non_bash_lines_are_skipped(dirs: tuple[Path, Path]) -> None:
    projects, hooks = dirs
    command = "echo after junk"
    path = _main(projects, SESSION_A, [
        '{"name":"Bash", truncated mid-write',
        _text_line('mentions "name":"Bash" in prose'),
        _bash_line(command),
    ])
    _fire(hooks, _sha(command), SESSION_A)
    [rec] = cr.recover([_sha(command)], projects_dir=projects, hooks_dir=hooks)
    assert rec.status is cr.RecoveryStatus.COMMAND
    assert rec.location == f"{path}:3"


def test_one_open_per_file_for_two_hashes_in_one_session(
    dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects, hooks = dirs
    main = _main(projects, SESSION_A, [_bash_line("one")])
    sub = _subagent(projects, SESSION_A, "q", [_bash_line("two")])
    _fire(hooks, _sha("one"), SESSION_A)
    _fire(hooks, _sha("two"), SESSION_A)
    counts = _count_reads(monkeypatch)
    recs = cr.recover([_sha("one"), _sha("two")], projects_dir=projects, hooks_dir=hooks)
    assert [r.status for r in recs] == [cr.RecoveryStatus.COMMAND] * 2
    assert counts == Counter({main: 1, sub: 1})


def test_scan_all_opens_each_file_once(
    dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mixed backed (telemetry row) and unbacked hashes with --scan-all."""
    projects, hooks = dirs
    a = _main(projects, SESSION_A, [_bash_line("backed")])
    b = _main(projects, SESSION_B, [_bash_line("unbacked")])
    sub = _subagent(projects, SESSION_A, "q", [_bash_line("other")])
    _fire(hooks, _sha("backed"), SESSION_A)
    counts = _count_reads(monkeypatch)
    recs = cr.recover(
        [_sha("backed"), _sha("unbacked")], projects_dir=projects, hooks_dir=hooks, scan_all=True,
    )
    assert [r.status for r in recs] == [cr.RecoveryStatus.COMMAND] * 2
    assert counts == Counter({a: 1, b: 1, sub: 1})


def test_targeted_scan_reads_only_firing_sessions(
    dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects, hooks = dirs
    a = _main(projects, SESSION_A, [_bash_line("backed")])
    _main(projects, SESSION_B, [_bash_line("elsewhere")])
    _fire(hooks, _sha("backed"), SESSION_A)
    counts = _count_reads(monkeypatch)
    cr.recover([_sha("backed")], projects_dir=projects, hooks_dir=hooks)
    assert counts == Counter({a: 1})


# ---------- output line ----------


def test_format_line_columns_for_command() -> None:
    rec = cr.Recovery(
        arg="abcdef12", status=cr.RecoveryStatus.COMMAND, full_hash="f" * 64,
        session_id=SESSION_A, location="/home/alice/x.jsonl:4", command="a\tb\nc",
    )
    assert cr.format_line(rec, 200) == "\t".join(
        ["abcdef12", "command", "f" * 64, SESSION_A, "/home/alice/x.jsonl:4", "a\\tb\\nc"]
    )


def test_format_line_columns_for_unknown() -> None:
    rec = cr.Recovery(
        arg="abcdef12", status=cr.RecoveryStatus.UNKNOWN, full_hash="",
        session_id="", location="", command="",
    )
    assert cr.format_line(rec, 200) == "abcdef12\tunknown\t\t\t\t"


def test_format_line_truncates_command() -> None:
    rec = cr.Recovery(
        arg="abcdef12", status=cr.RecoveryStatus.COMMAND, full_hash="f" * 64,
        session_id=SESSION_A, location="p:1", command="z" * 30,
    )
    assert cr.format_line(rec, 10).split("\t")[-1] == "z" * 10 + "…[+20 chars]"


# ---------- projects-dir default ----------


def test_claude_projects_dir_follows_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.claude_projects_dir() == tmp_path / ".claude" / "projects"


def test_recover_creates_no_telemetry_db_or_directory_when_missing(tmp_path: Path) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    missing_hooks = tmp_path / "no-such-hooks-dir"

    recs = cr.recover(["a" * 64], projects_dir=projects, hooks_dir=missing_hooks)

    assert [r.status for r in recs] == [cr.RecoveryStatus.UNKNOWN]
    assert not missing_hooks.exists()


def test_recover_skips_transcript_lines_that_are_not_valid_utf8(
    dirs: tuple[Path, Path],
) -> None:
    projects, hooks = dirs
    command = "echo findme"
    sha = _sha(command)
    path = projects / KEY / f"{SESSION_A}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(
        b'{"type":"user","message":{"content":"half a char \xe2\x82"}}\n'
        + _bash_line(command).encode("utf-8")
        + b"\n"
    )
    _fire(hooks, sha, SESSION_A)

    (rec,) = cr.recover([sha], projects_dir=projects, hooks_dir=hooks)

    assert rec.status is cr.RecoveryStatus.COMMAND
    assert rec.command == command


def test_recover_skips_commands_that_cannot_be_encoded(dirs: tuple[Path, Path]) -> None:
    projects, hooks = dirs
    good = "echo ok"
    sha = _sha(good)
    surrogate_record = (
        '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash",'
        '"input":{"command":"echo \\ud800"}}]}}'
    )
    _main(projects, SESSION_A, [surrogate_record, _bash_line(good)])
    _fire(hooks, sha, SESSION_A)

    (rec,) = cr.recover([sha], projects_dir=projects, hooks_dir=hooks)

    assert rec.status is cr.RecoveryStatus.COMMAND
    assert rec.command == good


@pytest.mark.parametrize(
    ("ch", "escaped"),
    [
        ("\x7f", "\\u007f"),
        ("\x85", "\\u0085"),
        ("\x9b", "\\u009b"),
        (" ", "\\u2028"),
        (" ", "\\u2029"),
    ],
)
def test_escape_command_escapes_del_c1_and_line_separators(ch: str, escaped: str) -> None:
    out = cr.escape_command(f"a{ch}b")
    assert out == f"a{escaped}b"
    assert len(out.splitlines()) == 1
