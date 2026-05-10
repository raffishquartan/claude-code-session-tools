from __future__ import annotations


def pick_from_list(labels: list[str]) -> int | None:
    """Display a 1-9/0 numbered menu. Returns 0-based index or None if cancelled.

    Requires 1 <= len(labels) <= 10. Reads one line from stdin.
    """
    assert 1 <= len(labels) <= 10
    for i, label in enumerate(labels):
        num = (i + 1) if i < 9 else 0
        print(f"  {num}) {label}")
    n = len(labels)
    range_str = f"1-{min(n, 9)}" + (", 0" if n == 10 else "")
    try:
        raw = input(f"Pick [{range_str}, q to cancel]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not raw or raw[0].lower() == "q":
        return None
    if raw[0].isdigit():
        d = int(raw[0])
        idx = d - 1 if d != 0 else 9
        if 0 <= idx < n:
            return idx
    return None
