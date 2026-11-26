## 1. State and shared helpers

- [x] 1.1 Write failing tests in `tests/self_improvement/test_state.py` for date validation, UTC day ages, last interactive and last `auto` review dates, last paid run date and atomic write (permissions kept, no temp file left on failure), then implement `lib/self_improvement/state.py`; verify `uv run pytest tests/self_improvement -q` passes
- [x] 1.2 Hoist the auth variable names into one tuple shared by `api_keys.with_api_key` and a new `without_api_auth` helper; verify with new cases in `tests/test_api_keys.py`

## 2. Collector

- [x] 2.1 Port every case of the collector bats suite to `tests/self_improvement/test_signals.py` (counts, unavailable sources, health boundaries, escalation rate limit, no-escalate, sensitive subject, writes only the state dir, impossible date, exact `signals.env` key order) and implement `lib/self_improvement/signals.py`; verify the tests pass

## 3. Nudge

- [x] 3.1 Port every case of the nudge bats suite to `tests/self_improvement/test_nudge.py`, plus the bash-nudge dedupe and preview-does-not-stamp cases, and implement `lib/self_improvement/nudge.py` and `src/hooks/self_improvement_nudge.py`; verify the tests pass
- [x] 3.2 Register `self-improvement-nudge` in `hook_registry.py` and `config/hooks-bundle.json`; verify the hook registry and bundle tests pass

## 4. Headless review

- [x] 4.1 Add `config/self-improvement-review-prompt.md` and `lib/self_improvement/prompt.py`; verify a test pins the parsed lever list
- [x] 4.2 Port every case of the headless-review bats suite to `tests/self_improvement/test_review.py` with a stub `claude` on PATH (run limit, skip, digest bounds and exclusions, facets, validation and Inbox insert, all failure paths, timeouts and SIGTERM-ignoring child, cleanup, blast radius), plus auth-stripping, auth pre-check and missing-backlog cases, and implement `lib/self_improvement/review.py`; verify the tests pass and no real model call is possible

## 5. CLI and docs

- [x] 5.1 Add `ccst self-improvement <collect-signals|auto-review|nudge|status>` with `--help`, and CLI tests in `tests/self_improvement/test_cli.py`; verify `CCST_NO_AUTO_SYNC=1 uv run python -m cc_session_tools.cli.ccst self-improvement --help` lists all four
- [x] 5.2 README section for the command, the hook, the state directory and file contract, and the subscription-usage cost; verify with `grep -n "self-improvement" README.md`

## 6. Release

- [x] 6.1 CHANGELOG 4.10.0 with today's date, bump `pyproject.toml`, `uv lock`; verify `uv run pytest -q`, `uv run mypy src` and `openspec validate --specs` all exit 0
