from cccs_hooks import model_info

CASES = [
    ("claude-fable-5", 1_000_000, 10.00, "Fable 5"),
    ("claude-mythos-5", 1_000_000, 10.00, "Mythos 5"),
    ("claude-opus-5", 1_000_000, 5.00, "Opus 5"),
    ("claude-opus-4-8", 1_000_000, 5.00, "Opus 4.8"),
    ("claude-opus-4-7", 1_000_000, 5.00, "Opus 4.7"),
    ("claude-opus-4-6", 1_000_000, 5.00, "Opus 4.6"),
    ("claude-sonnet-5", 1_000_000, 3.00, "Sonnet 5"),
    ("claude-sonnet-4-6", 1_000_000, 3.00, "Sonnet 4.6"),
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
