from tokens import token_count


def test_empty_is_zero():
    assert token_count("") == 0


def test_exact_known_value():
    # Pins the parser to the "Tokens:" line, not "Characters:".
    # Fake tokenizer (see conftest.fake_count_text_tokens) counts whitespace
    # words: "hello world" -> 2 tokens, 11 chars.
    assert token_count("hello world") == 2


def test_monotonic():
    assert token_count("a b c d e f g") > token_count("a b")
