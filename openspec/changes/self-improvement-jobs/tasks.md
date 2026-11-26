## 1. Loop set-up rule and init

- [ ] 1.1 Add `state.loop_set_up()` (backlog readable with a `## Inbox` line) and switch the nudge to it; verify with nudge tests for missing backlog, backlog without Inbox, and state directory only
- [ ] 1.2 `auto-review` logs `skipped` cost 0, no notification and no model call when the loop is not set up, before the run limit; verify with review tests (no stub `claude` call, no Telegram, one `skipped` line, backlog not created)
- [ ] 1.3 Collector: no escalation and the `loop not set up (run ccst self-improvement init)` suffix when not set up; verify with signals tests
- [ ] 1.4 `ccst self-improvement init` (exclusive create, exit 1 when an existing file lacks an Inbox, dangling symlink untouched) and `status` set-up lines; verify with CLI tests

## 2. Messages and environment

- [ ] 2.1 `signals.review_command()` used by the nudge and escalation; verify with tests with and without the review skill's `SKILL.md`
- [ ] 2.2 Extend `api_keys.API_AUTH_VARS` with the routing, gateway and provider variables of design D5; verify with `test_api_keys` and a review test that `ANTHROPIC_BASE_URL` does not reach the stub `claude`

## 3. Bundled jobs, installer and doctor

- [ ] 3.1 Add the two `BundledJob`s with `replaces_scripts` and `replaced_script()`; verify with bundled-jobs unit tests
- [ ] 3.2 `ccst ccsched-jobs install`: report a replaced-script registration with the migration steps and do not mark it installed; verify with tests for fresh install, matching definition, bash registration, and remove-then-install
- [ ] 3.3 Doctor: replaced-script WARN in `check_ccsched_job_registered` and new `check_self_improvement_loop`, wired into `run_checks`; verify with doctor tests

## 4. Docs and release

- [ ] 4.1 README: jobs bundled and on by default, subscription usage and USD 0.50 cap, `init`, migration from the bash registrations; CLI help for `init`
- [ ] 4.2 CHANGELOG 4.11.0 entry; bump `pyproject.toml` to 4.11.0 and run `uv lock` in the same commit
- [ ] 4.3 `uv run pytest -q`, `uv run mypy src` and `openspec validate --specs` all exit 0
