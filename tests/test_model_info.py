from hooks import model_info

CASES = [
    ("claude-fable-5", 1_000_000, 10.00, "Fable 5"),
    ("claude-mythos-5", 1_000_000, 10.00, "Mythos 5"),
    ("claude-opus-5", 1_000_000, 5.00, "Opus 5"),
    ("claude-opus-4-8", 1_000_000, 5.00, "Opus 4.8"),
    ("claude-opus-4-7", 1_000_000, 5.00, "Opus 4.7"),
    ("claude-opus-4-6", 1_000_000, 5.00, "Opus 4.6"),
    ("claude-sonnet-5", 1_000_000, 3.00, "Sonnet 5"),
    ("claude-sonnet-4-6", 1_000_000, 3.00, "Sonnet 4.6"),
    ("claude-sonnet-5-5", 1_000_000, 3.00, "Sonnet 5.5"),
    ("claude-opus-5-5", 1_000_000, 5.00, "Opus 5.5"),
    ("claude-fable-5-1", 1_000_000, 10.00, "Fable 5.1"),
    ("claude-haiku-4-5-20251001", 200_000, 1.00, "Haiku 4.5"),
]


def test_known_models():
    for model_id, window, price, name in CASES:
        assert model_info.context_window(model_id) == window
        assert model_info.input_price_per_mtok(model_id) == price
        assert model_info.display_name(model_id) == name


def test_unrecognized_model_falls_back():
    assert model_info.context_window("claude-nonexistent-9") == 200_000
    assert model_info.input_price_per_mtok("claude-nonexistent-9") == 5.00
    assert model_info.display_name("claude-nonexistent-9") == "an unrecognized model"


def test_empty_model_id_falls_back():
    assert model_info.context_window("") == 200_000


def test_dated_snapshot_ids_resolve_like_their_family_id():
    """Claude Code can report a dated snapshot id (`<family>-YYYYMMDD`); it must resolve to the
    same row as the undated id, not fall to the 200k default."""
    for base, window, price, name in CASES:
        if base.startswith("claude-haiku"):
            continue
        dated = f"{base}-20261001"
        assert model_info.context_window(dated) == window
        assert model_info.input_price_per_mtok(dated) == price
        assert model_info.display_name(dated) == name


def test_unknown_dated_and_decorated_ids_stay_unrecognised():
    for model_id in (
        "claude-sonnet-5-9-20261001",
        "claude-nonexistent-9-20261001",
        "claude-sonnet-5-5-2026100",      # 7 digits
        "claude-sonnet-5-5-202610011",    # 9 digits
        "claude-sonnet-5-5-latest",
        "claude-sonnet-5-5[1m]",
        "claude-sonnet-5-5-20261001-20261002",  # normalised once only
        "claude-haiku-4-5-garbage",
    ):
        assert model_info.context_window(model_id) == 200_000, model_id
        assert model_info.display_name(model_id) == "an unrecognized model", model_id


def test_id_that_merely_starts_with_a_known_id_is_not_recognised():
    """Guards against a future lazy prefix implementation (already true before the fix):
    `claude-sonnet-5-9` must not inherit `claude-sonnet-5`'s row."""
    assert model_info.context_window("claude-sonnet-5-9") == 200_000
    assert model_info.display_name("claude-sonnet-5-9") == "an unrecognized model"
