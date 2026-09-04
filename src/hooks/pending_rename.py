"""SessionStart hook: surface `.pending-rename` markers left by move-session.

The implementation is the bash script bundled with the move-session skill —
it is the thing the skill's bats suite grades, and it stays in bash so the
skill remains self-contained for anyone reading or running it on its own.
This module exists so the hook registers like every other CCST hook
(`ccst hooks run pending-rename` in the bundle, one name in HOOK_VERBS,
covered by the prune and doctor checks) instead of as a hand-written raw-path
entry in someone's settings.json.

`execv` rather than `subprocess`: the script owns stdin, stdout and the exit
code, and replacing this process hands all three over with nothing left to
forward.
"""
from __future__ import annotations

import os
from pathlib import Path

import cc_session_tools

HOOK_SCRIPT = (
    Path(cc_session_tools.__file__).parent
    / "skills"
    / "move-session"
    / "hooks"
    / "sessionstart-pending-rename.sh"
)


def main() -> int:
    """Hand the process over to the bundled hook script. Does not return."""
    os.execvp("bash", ["bash", str(HOOK_SCRIPT)])


if __name__ == "__main__":
    raise SystemExit(main())
