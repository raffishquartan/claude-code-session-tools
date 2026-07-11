"""Single tokenizer call site. Wraps tiktoken-tools' count_text_tokens."""
from tiktoken_tools.text import count_text_tokens


def token_count(text: str) -> int:
    return count_text_tokens(text)["tokens"]
