from __future__ import annotations

import importlib.util
import re
from pathlib import Path

_SCRIPT = (
    Path(__file__).parent.parent
    / "src" / "cc_session_tools" / "skills" / "clean-hook-sessions" / "scripts"
    / "clean-hook-sessions.py"
)


def test_human_time_is_text_form_without_seconds_or_t_separator() -> None:
    spec = importlib.util.spec_from_file_location("clean_hook_sessions_script", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", module.human_time(1_790_000_000))
