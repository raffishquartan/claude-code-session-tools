import json
from pathlib import Path

import pytest

from hooks import context_window_warning as cww


def _write_transcript(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")


def test_fresh_session_has_zero_tokens(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    _write_transcript(p, [{"type": "user", "message": {"content": "hi"}}])
    assert cww._current_context_tokens(p) == (0, "")


def test_reads_last_assistant_usage(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "assistant", "message": {"model": "claude-sonnet-5",
            "usage": {"input_tokens": 100, "cache_creation_input_tokens": 50, "cache_read_input_tokens": 5000}}},
        {"type": "assistant", "message": {"model": "claude-sonnet-5",
            "usage": {"input_tokens": 200, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 154800}}},
    ])
    assert cww._current_context_tokens(p) == (155000, "claude-sonnet-5")


def test_sidechain_turns_are_skipped(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "assistant", "message": {"model": "claude-sonnet-5",
            "usage": {"input_tokens": 100, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}},
        {"type": "assistant", "isSidechain": True, "message": {"model": "claude-haiku-4-5",
            "usage": {"input_tokens": 9, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}},
    ])
    assert cww._current_context_tokens(p) == (100, "claude-sonnet-5")


def test_blank_lines_tolerated(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    p.write_text('\n{"type": "user", "message": {"content": "hi"}}\n\n')
    assert cww._current_context_tokens(p) == (0, "")


def test_missing_transcript_raises(tmp_path: Path):
    with pytest.raises(cww.TranscriptError):
        cww._current_context_tokens(tmp_path / "nope.jsonl")


def test_corrupt_transcript_raises(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    p.write_text("not json at all\nstill not json\n")
    with pytest.raises(cww.TranscriptError):
        cww._current_context_tokens(p)


def test_cost_is_tokens_times_cache_read_price():
    """155k tokens at a $0.30/MTok cache-read price is $0.0465, shown as 0.04."""
    assert cww._format_cost(tokens=155_000, cache_read_price_per_mtok=0.30) == "0.04"


@pytest.mark.parametrize(
    ("model_id", "tokens", "cost"),
    [("claude-fable-5-1", 400_000, "0.10"), ("claude-sonnet-5-5", 500_000, "0.10")],
)
def test_cost_uses_the_models_published_cache_read_price(model_id, tokens, cost):
    price = cww.model_info.cache_read_price_per_mtok(model_id)
    assert cww._format_cost(tokens=tokens, cache_read_price_per_mtok=price) == cost


def test_cost_truncates_not_rounds():
    """tokens=199_000, cache-read price 0.30 -> 0.199*0.30 = 0.0597 -> bc truncates
    to "0.05"; a naive f'{x:.2f}' would round to "0.06". Chosen specifically
    because truncation and rounding clearly disagree on this input."""
    assert cww._format_cost(tokens=199_000, cache_read_price_per_mtok=0.30) == "0.05"


def test_cost_does_not_lose_a_cent_to_float_representation_error():
    """math.floor(raw * 100) / 100 on a native float loses a cent here:
    350000/1e6 * 0.20 evaluates to 0.06999999999999999 in float, not
    exactly 0.07, so a naive floor gives "0.06". 350k tokens at the Sonnet 5.x
    / Opus 5.5 cache-read price is an entirely ordinary state for this hook."""
    assert cww._format_cost(tokens=350_000, cache_read_price_per_mtok=0.20) == "0.07"


def test_k_and_pct_integer_arithmetic():
    assert cww._k_tokens(155_000) == 155
    assert cww._k_tokens(155_499) == 155  # (155499+500)//1000 == 155
    assert cww._k_tokens(155_500) == 156  # (155500+500)//1000 == 156
    assert cww._pct_of_window(155_000, 1_000_000) == 16  # (15500000+500000)//1000000
    assert cww._pct_of_window(200_000, 200_000) == 100


def test_window_label():
    assert cww._window_label(1_000_000) == "1M"
    assert cww._window_label(200_000) == "200k"


import io


def _run_main(monkeypatch, capsys, stdin_obj):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(stdin_obj)))
    rc = cww.main([])
    return rc, capsys.readouterr()


def test_no_transcript_path_is_silent(monkeypatch, capsys):
    rc, out = _run_main(monkeypatch, capsys, {})
    assert rc == 0
    assert out.out == ""


def test_stop_hook_active_is_silent(monkeypatch, capsys):
    rc, out = _run_main(monkeypatch, capsys, {"transcript_path": "/x", "stop_hook_active": True})
    assert rc == 0
    assert out.out == ""


def test_below_orange_threshold_is_silent(tmp_path, monkeypatch, capsys):
    p = tmp_path / "t.jsonl"
    _write_transcript(p, [{"type": "assistant", "message": {"model": "claude-sonnet-5",
        "usage": {"input_tokens": 1000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}])
    monkeypatch.setattr(cww, "_is_overridden", lambda session_id: False)
    rc, out = _run_main(monkeypatch, capsys, {"transcript_path": str(p), "session_id": "s1"})
    assert rc == 0
    assert out.out == ""


def test_orange_band_emits_block_decision(tmp_path, monkeypatch, capsys):
    p = tmp_path / "t.jsonl"
    _write_transcript(p, [{"type": "assistant", "message": {"model": "claude-sonnet-5",
        "usage": {"input_tokens": 155000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}])
    monkeypatch.setattr(cww, "_is_overridden", lambda session_id: False)
    rc, out = _run_main(monkeypatch, capsys, {"transcript_path": str(p), "session_id": "s1"})
    assert rc == 0
    payload = json.loads(out.out)
    assert payload["decision"] == "block"
    assert "🟠 CONTEXT" in payload["reason"]
    assert "~155k tokens" in payload["reason"]
    assert "~$0.03/turn" in payload["reason"]  # Sonnet 5 cache reads $0.20/MTok


def test_red_band_emits_red_decision(tmp_path, monkeypatch, capsys):
    p = tmp_path / "t.jsonl"
    _write_transcript(p, [{"type": "assistant", "message": {"model": "claude-sonnet-5",
        "usage": {"input_tokens": 200000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}])
    monkeypatch.setattr(cww, "_is_overridden", lambda session_id: False)
    rc, out = _run_main(monkeypatch, capsys, {"transcript_path": str(p), "session_id": "s1"})
    payload = json.loads(out.out)
    assert "🔴 CONTEXT" in payload["reason"]


def test_override_silences_even_above_threshold(tmp_path, monkeypatch, capsys):
    p = tmp_path / "t.jsonl"
    _write_transcript(p, [{"type": "assistant", "message": {"model": "claude-sonnet-5",
        "usage": {"input_tokens": 200000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}])
    monkeypatch.setattr(cww, "_is_overridden", lambda session_id: True)
    rc, out = _run_main(monkeypatch, capsys, {"transcript_path": str(p), "session_id": "s1"})
    assert rc == 0
    assert out.out == ""


def test_unreadable_transcript_surfaces_error_nonblocking(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cww, "_is_overridden", lambda session_id: False)
    rc, out = _run_main(monkeypatch, capsys, {"transcript_path": str(tmp_path / "nope.jsonl"), "session_id": "s1"})
    assert rc == 1
    assert out.out == ""
    assert "context-window-warning" in out.err


def test_override_end_to_end_via_real_store(tmp_path, monkeypatch, capsys):
    from cc_session_tools.lib import context_overrides

    db = tmp_path / "sessions.db"
    monkeypatch.setenv("CCST_SESSIONS_DIR", str(tmp_path))
    context_overrides.set_override("s1", "on", path=db)

    p = tmp_path / "t.jsonl"
    _write_transcript(p, [{"type": "assistant", "message": {"model": "claude-sonnet-5",
        "usage": {"input_tokens": 200000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}])
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"transcript_path": str(p), "session_id": "s1"})))
    rc = cww.main([])
    out = capsys.readouterr()
    assert rc == 0
    assert out.out == ""


@pytest.mark.parametrize(
    ("model_id", "name", "cost"),
    [
        # 266k tokens x published cache-read price ($0.20, $0.20, $0.25 per MTok)
        ("claude-sonnet-5-5", "Sonnet 5.5", "0.05"),
        ("claude-opus-5-5", "Opus 5.5", "0.05"),
        ("claude-fable-5-1", "Fable 5.1", "0.06"),
    ],
)
def test_new_5x_models_are_measured_against_their_one_million_token_window(
    tmp_path, monkeypatch, capsys, model_id, name, cost
):
    p = tmp_path / "t.jsonl"
    _write_transcript(p, [{"type": "assistant", "message": {"model": model_id,
        "usage": {"input_tokens": 266000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}}])
    monkeypatch.setattr(cww, "_is_overridden", lambda session_id: False)
    rc, out = _run_main(monkeypatch, capsys, {"transcript_path": str(p), "session_id": "s1"})
    reason = json.loads(out.out)["reason"]
    assert "~27% of the 1M-token window" in reason
    assert name in reason
    assert f"~${cost}/turn" in reason
    assert "unrecognized" not in reason


def _synthetic_stub() -> dict:
    """The shape Claude Code writes for a non-API assistant entry (e.g. a usage-limit notice)."""
    return {"type": "assistant", "isApiErrorMessage": True, "message": {"model": "<synthetic>",
        "stop_reason": "stop_sequence",
        "usage": {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0,
                  "cache_read_input_tokens": 0}}}


def test_trailing_synthetic_entry_does_not_reset_the_count(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "assistant", "message": {"model": "claude-sonnet-5-5",
            "usage": {"input_tokens": 500, "cache_creation_input_tokens": 1500, "cache_read_input_tokens": 178000}}},
        _synthetic_stub(),
    ])
    assert cww._current_context_tokens(p) == (180000, "claude-sonnet-5-5")


def test_only_synthetic_entries_behave_like_a_fresh_session(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    _write_transcript(p, [{"type": "user", "message": {"content": "hi"}}, _synthetic_stub()])
    assert cww._current_context_tokens(p) == (0, "")


def test_warning_still_fires_after_a_trailing_synthetic_entry(tmp_path, monkeypatch, capsys):
    p = tmp_path / "t.jsonl"
    _write_transcript(p, [
        {"type": "assistant", "message": {"model": "claude-sonnet-5-5",
            "usage": {"input_tokens": 180000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}},
        _synthetic_stub(),
    ])
    monkeypatch.setattr(cww, "_is_overridden", lambda session_id: False)
    rc, out = _run_main(monkeypatch, capsys, {"transcript_path": str(p), "session_id": "s1"})
    assert rc == 0
    reason = json.loads(out.out)["reason"]
    assert "~180k tokens" in reason
    assert "Sonnet 5.5" in reason
    assert "unrecognized" not in reason
