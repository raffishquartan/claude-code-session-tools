# uv-aware bash-security-review Trivial/Cache Tiers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `uv run pytest ...` / `uv run python -m ...` (and similar `uv`-wrapped invocations
of already-trusted verbs) from getting a full Claude-CLI security review on every single
invocation, let `uv sync`/`uv build`/`uv lock` share a cache entry the way `npm install`/
`cargo build` already do, and fix a separate, more serious pre-existing bug — found while
verifying the above — that lets some short, unpiped, write-risk commands (e.g. a bare `rm -rf
...`) bypass review entirely. All by extending the *existing* tiered trivial/normalise/cache
system in `bash_security_review.py`/`normalise.py`, instead of inventing a new mechanism.

**Architecture:** Six additive/corrective changes to code that already exists and already does
most of this job, found and ordered by actually running the real `run()` pipeline end-to-end at
every step (not by reading the tiers in isolation and assuming they compose correctly):

1. **A `run()` pipeline bug, unrelated to `uv`, found while verifying this plan.** A `nontrivial`
   pre-gate exits silently for any short (≤120 char), unpiped, non-heuristic-flagged command
   *without ever consulting `has_write_risk()`* — so a bare `rm -rf /tmp/x` or `sudo apt install
   vim` bypasses review entirely today, despite `has_write_risk()` correctly recognising both as
   risky. Tier 0.5's own check (`not hits and not has_write_risk(command)`) already does this
   correctly for piped/long commands; the fix is to let it run for every command, unconditionally,
   rather than short-circuiting before it for short/unpiped ones. Fixed first because it also
   simplifies verifying Task 6's `uv` cache entry — see Task 6. (Task 1)
2. Tier 0 (`is_trivial()`) already unconditionally trusts bare `pytest`, `python3`, `npm`, `node`,
   etc. — zero review, ever. `uv run <one of those verbs> ...` never gets this same trust decision
   today purely because `uv` (the first token) isn't on that allowlist; the wrapped verb is never
   evaluated on its own merits. Strip a `uv run ` prefix before the Tier 0 check so the wrapped
   verb gets the *same* trust decision it already gets when invoked directly — not a new, broader
   one. (Task 2)
3. A pre-existing bug in `_HEURISTIC_PATTERNS`' "raw network tool" pattern false-positives on `uv
   sync` (it matches the substring `nc ` inside `sync `), which would silently defeat Task 6's
   caching for that exact command. Found empirically while verifying this plan, not by guessing —
   fixed as its own task, because Task 6 depends on it actually working. (Task 3)
4. **Folded in after Task 3 shipped, at the user's explicit request.** The same unbounded-substring
   defect Task 3 fixed exists in four more `_HEURISTIC_PATTERNS` entries (`base64`, `id_rsa`/
   `id_ed25519`, `printenv`/`env`, `wget`/`curl`), found by the code-quality review of Task 3
   itself. Same severity class — an extra unnecessary review, not a security bypass. (Task 4)
5. `uv sync`/`uv build`/`uv lock` have real write/network effects (fetch packages, write
   lockfiles/venvs/wheels) but aren't recognised by `has_write_risk()` at all today, unlike their
   npm/pip counterparts (`npm install`, `pip install`). Add them to `_WRITE_RISK_RE`'s
   package-management alternation, mirroring the existing `npm install`/`pip install` entries —
   without this, fixing Task 3's bug would make `uv sync` go from "always fully reviewed today, by
   accident" to "never reviewed at all", a real, self-inflicted security regression this plan must
   not ship. (Task 5)
6. Tier 2 (`normalise()`'s package-manager rule) already gives `npm install`/`cargo build`/etc. one
   real review, then caches by normalised shape. `uv`'s own closed-ended subcommands (`sync`,
   `build`, `lock`, `export`, `tree`, `version` — none of which execute an arbitrary wrapped
   command) belong in the same `_PKG_SAFE_SUBCMDS` table `npm`/`pip`/`cargo` already live in.
   (Task 6)

**Tech Stack:** Python 3.11+, stdlib `re`/`shlex`, `pytest`.

---

## Diagnosis (context for the engineer — already root-caused, verified end-to-end against the
real `run()` pipeline with repeated temporary local patch-and-revert passes, not just read in
isolation; every claim below was independently reproduced, and one — see point 6 — was corrected
mid-investigation after an initial test example turned out to be wrong)

1. **The caching/normalisation system already exists and is well-designed** —
   `src/cccs_hooks/cache.py` (SQLite-backed, exact-hash + normalised-hash lookup, 90-day
   auto-prune) and `src/cccs_hooks/normalise.py` (token classifier + per-verb rules for read-only
   builtins, `git`, `find`, and package managers) were built in three prior commits (`cb7f44f`,
   `e2bc403`, `4fafc4d`) specifically so structurally-identical repeated commands share a cache
   entry and stop needing a fresh Claude-CLI review each time. `uv` (a comparatively recent
   addition to this session's own tooling — this repo's own `.claude/CLAUDE.md` mandates `uv run`
   over bare `pytest`/`python` inside worktrees) was never added to Tier 0's allowlist, Tier 0.5's
   write-risk list, or Tier 2's normalisation table.
2. **The `nontrivial`-gate bug, root-caused and reproduced against the real pipeline, not
   assumed:** `run()`'s `nontrivial` gate (`bash_security_review.py:301-317`) reads:
   ```python
   nontrivial = (
       bool(hits)
       or _NONTRIVIAL_RE.search(command) is not None
       or len(command) > 120
   )
   if not nontrivial:
       ...  # exit 0, silently — has_write_risk() is never called
       return 0
   ```
   This is a strictly *weaker*, earlier-running duplicate of Tier 0.5's own check
   (`not hits and not has_write_risk(command)`), which already correctly accounts for write risk.
   Any command that is short, unpiped, and heuristic-clean exits here regardless of write risk,
   *before* Tier 0.5 is ever reached. Confirmed live: `bsr.run()` on a synthetic `"rm -rf
   /tmp/x"` and on `"sudo apt install vim"` both exit with `call_claude` never invoked, despite
   `has_write_risk()` correctly returning `True` for both.
3. **`uv run pytest ...`'s specific pain point, root-caused:** `bash_security_review.py`'s
   `_TRIVIAL_RE` (`~line 37`) already lists `pytest`, `python3?`, `node`, `npm` as fully-trusted
   Tier 0 verbs — a bare `pytest tests/foo.py -k bar -v` exits silently with zero review, today,
   already. `uv run pytest tests/foo.py -k bar -v` does not get this same trust decision, because
   `is_trivial()` (`~line 123`) matches `_TRIVIAL_RE` against the *first* token, which is `uv`, not
   `pytest` — the wrapped verb is never inspected, and `normalise()` (`normalise.py:91`) has no
   rule for verb `uv` at all, so no cache entry can ever form for it either. Task 2 closes this by
   giving `uv`-wrapped verbs the same Tier 0 trust decision the bare verb already gets.
4. **The `uv sync` heuristic false positive, confirmed empirically, not assumed:**
   `heuristic_flags("uv sync --extra dev")` returns `['raw network tool']` today, because
   `_HEURISTIC_PATTERNS`' pattern `(nc|ncat|netcat|socat)\s` has no word boundary and matches the
   substring `nc ` inside `sync `. Any non-empty `hits` forces `skip_cache = True`
   (`bash_security_review.py:295`), so `uv sync` currently always reaches a real Claude-CLI review
   — accidentally, via a bug, not by design. Task 3 fixes the bug.
5. **Why Task 3 alone would be a regression, and Task 5 exists to prevent it:** once Task 3's
   word-boundary fix lands, `uv sync --extra dev` has zero heuristic hits. `has_write_risk("uv
   sync --extra dev")` is `False` today (`uv` isn't in `_WRITE_RISK_RE` at all), where
   `has_write_risk("npm install")` is already `True` — so today, `uv sync` and `npm install` are
   NOT on equal footing despite having comparable write/network effects; only `uv sync` currently
   reaches Tier 3 at all, and only via the accidental heuristic hit. Removing that accident
   without adding the missing `has_write_risk` recognition would take `uv sync` from "always
   reviewed" straight to "never reviewed, not even once" — a real regression relative to today.
   Task 5 fixes this by giving `uv sync`/`build`/`lock` the same `has_write_risk() == True` status
   their npm/pip counterparts already have.
6. **A test-example correction made mid-investigation, worth recording so it isn't repeated:** an
   earlier pass at this diagnosis (and this plan's own round-2 review) used bare `npm install` as
   a second example of the Task 1 `nontrivial`-gate bug, alongside `rm -rf /tmp/x`. That example
   is wrong. `npm` is itself on `_TRIVIAL_RE`'s Tier-0 allowlist (`~line 37`) — `is_trivial("npm
   install")` returns `True`, so `bsr.run()` exits at Tier 0, *before* the `nontrivial` gate is
   ever reached at all. Bare `npm install`/`pip install anything`/`python3 -c "..."` are
   unconditionally trusted today, by an entirely separate, clearly-deliberate Tier-0 design
   decision this plan does not touch or question (it's the same trust decision Task 2 extends to
   `uv run <verb>` for the wrapped verb). The `nontrivial`-gate bug is real and Task 1 still fixes
   it, but its correct demonstration commands are ones that are write-risk *and not* on
   `_TRIVIAL_RE`'s allowlist — `rm -rf ...`, `sudo apt install ...`, `mv ... /somewhere`, bare
   `curl ...` all qualify and were re-verified directly against the real pipeline.
7. **`uv run` is fundamentally different from `npm install`/`cargo build` and must not be added to
   the package-manager table naively.** `npm install`/`pip install`/`cargo build` are closed-ended
   actions — the subcommand *is* the entire effect. `uv run <cmd>` *executes an arbitrary wrapped
   command* — it is architecturally an interpreter/dispatcher, the same category `bash`, `sh`,
   `python`, `node` are already in `_NEVER_NORMALISE` for. Collapsing `uv run <ARGS>` to one cache
   key regardless of the wrapped command, the way `git <safe-subcmd> <ARGS>` does, would let a
   first cached-as-safe `uv run pytest ...` silently authorise a later, unrelated `uv run
   <anything else>` — a real cache-poisoning / privilege-widening risk. This is why the fix for
   `uv run` is at Tier 0 (Task 2: evaluate the *wrapped* verb against the *existing* trust decision
   for that verb) and not a new blanket `uv` entry in `_PKG_SAFE_SUBCMDS`'s `run` position.
8. **`uv`'s own closed-ended subcommands are a different, safe case.** `uv sync`, `uv build`,
   `uv lock`, `uv export`, `uv tree`, `uv version` do not execute an arbitrary wrapped command —
   they're the same shape as `npm install`/`cargo build`. `uv run`, `uv tool run`/`uvx`, `uv
   python` (can invoke arbitrary interpreters), and `uv pip` (wraps `pip`, itself already handled
   separately) are deliberately excluded from `_PKG_SAFE_SUBCMDS` — see Task 6.
9. **No existing plan or TODO covers any of this** — searched `docs/`, `TODO.md`, and this repo's
   git log for `normalise`/`command-cache`/`uv`/`nontrivial`; nothing pre-existing addresses `uv`
   specifically or the `nontrivial`-gate bug. The `normalise.py` module docstring's "Task 1
   scope... Task 2 will add..." phrasing refers to the git/find/package-manager rules already
   shipped (`e2bc403`) — there is no dangling "Task 3" this plan continues; it is new, scoped work.

## File structure

| File | Change |
|---|---|
| `src/cccs_hooks/bash_security_review.py` | Remove the buggy `nontrivial` pre-gate, let Tier 0.5 run unconditionally (Task 1); `is_trivial()` strips a leading `uv run ` prefix (Task 2); word-boundary fix to the raw-network-tool heuristic (Task 3); word-boundary fixes to the remaining unbounded heuristics (Task 4); `_WRITE_RISK_RE` gains `uv sync/build/lock` (Task 5) |
| `src/cccs_hooks/normalise.py` | `_PKG_SAFE_SUBCMDS` gains a `uv` entry for closed-ended subcommands only (Task 6) |
| `tests/test_bash_security_review.py` | New tests for Tasks 1, 2, 3, 4, 5, and the Task 6 integration test |
| `tests/test_normalise.py` | New tests for the `uv` package-manager rule (Task 6) |

---

### Task 1: Fix the `nontrivial` gate to stop bypassing the write-risk check

**Status: ✅ Complete.** Implemented, spec-reviewed (compliant), code-quality-reviewed (Approved,
no blocking issues — one minor note confirming the `_TRIVIAL_RE` blanket-trust gap for
`npm`/`pip3`/`python3`/`node`/`pytest` is the same pre-existing, deliberate design decision this
plan's Diagnosis point 6 already identified and explicitly scoped out, not a new gap this task
introduced). Commit: `b58d2f98ce9ea9de2421deddf6b37fb81ecc93eb` on branch
`f/20260814-uv-aware-command-cache`.

**The most severe fix in this plan, and independent of `uv` entirely** — a currently-live gap that
lets some dangerous short commands skip review completely. Placed first because Tasks 2-6 don't
depend on it for correctness, but it simplifies Task 6's integration test (see that task) once
it's in place.

**Files:**
- Modify: `src/cccs_hooks/bash_security_review.py:301-336` (`run()`'s Tier-0.5-and-`nontrivial`
  region) and the module docstring's Tier 0.5 line (`~line 5-7`)
- Test: `tests/test_bash_security_review.py`

- [ ] **Step 1: Write the failing tests**

Add near the existing Tier 0.5 test block (`tests/test_bash_security_review.py`, after
`test_tier05_xargs_rm_escalates_to_claude`):

```python
def test_short_unpiped_rm_reaches_claude(
    isolated_env: Path, mocker: MockerFixture
) -> None:
    """A bare 'rm -rf ...' - no pipe, no heuristic hit, well under 120 chars -
    must still reach a real review. Write risk must be checked regardless of
    shell composition or length, not only for piped/long commands."""
    mocker.patch.object(bsr, "_resolve_claude_bin", return_value="/fake/claude")
    spy = mocker.patch.object(
        bsr, "call_claude",
        return_value=("SUMMARY: delete\nRISKS: data loss\nVERDICT: dangerous", None),
    )
    rc = bsr.run(_input("rm -rf /tmp/x"))
    assert rc == 0
    assert spy.called


def test_short_unpiped_sudo_apt_install_reaches_claude(
    isolated_env: Path, mocker: MockerFixture
) -> None:
    """Same bug, a different write-risk verb not on the Tier-0 trivial
    allowlist (unlike npm/pip3/pytest/python3/node, which are - see the
    plan's Diagnosis point 6 for why those aren't valid examples here)."""
    mocker.patch.object(bsr, "_resolve_claude_bin", return_value="/fake/claude")
    spy = mocker.patch.object(
        bsr, "call_claude",
        return_value=("SUMMARY: install\nRISKS: system change\nVERDICT: safe", None),
    )
    rc = bsr.run(_input("sudo apt install vim"))
    assert rc == 0
    assert spy.called


def test_short_unpiped_safe_command_still_exits_silently(
    isolated_env: Path, mocker: MockerFixture
) -> None:
    """This fix must not turn every short command into a review - a
    genuinely safe one (no heuristic hit, no write risk, not on the Tier 0
    allowlist) must keep exiting silently, same as today."""
    spy = mocker.patch.object(bsr, "call_claude")
    rc = bsr.run(_input("grep foo bar.txt"))
    assert rc == 0
    assert not spy.called
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_bash_security_review.py -k "short_unpiped" -v`
Expected: FAIL — `test_short_unpiped_rm_reaches_claude` and
`test_short_unpiped_sudo_apt_install_reaches_claude` fail (`call_claude` not called);
`test_short_unpiped_safe_command_still_exits_silently` already passes today (written now to lock
in the non-regression, not because it currently fails)

- [ ] **Step 3: Remove the buggy gate**

In `src/cccs_hooks/bash_security_review.py`'s `run()` (around lines 299-336), delete the
`nontrivial` computation and its early-return block entirely:

```python
    # Only "non-trivial" commands continue past this gate to claude. The bash
    # original short-circuits when the command is borderline. Mirror that:
    nontrivial = (
        bool(hits)
        or _NONTRIVIAL_RE.search(command) is not None
        or len(command) > 120
    )
    if not nontrivial:
        _emit_telemetry(
            hi=hi, decision="allow", cache_state="none", verdict="trivial", sha=sha
        )
        cache_mod.invocations_record(
            exit_tier=0,
            verdict="allow",
            session_id=hi.session_id or None,
            tool_name=hi.tool_name,
            exact_hash=sha,
        )
        return 0

    # ---- Tier 0.5: read-only pre-filter ----
    # At this point the command is nontrivial (has shell composition, heuristic
    # flags, or exceeds the length threshold). If there are no heuristic flags
    # and no write/network/exec risk patterns, the command is safe to skip
    # regardless of shell composition — piped read-only chains like
    # `grep foo | wc -l` or `git log | head -20` carry no meaningful risk.
    if not hits and not has_write_risk(command):
```

Replace with:

```python
    # ---- Tier 0.5: read-only pre-filter ----
    # Any command that reaches here (past Tier 0's trivial allowlist) is safe
    # to skip if it has no heuristic flags and no write/network/exec risk
    # pattern — regardless of shell composition or length. A piped read-only
    # chain like `grep foo | wc -l` carries no meaningful risk, and neither
    # does a short one like `grep foo bar.txt` alone: has_write_risk() must
    # be consulted for every command that reaches this point, not only ones
    # with shell composition or over the length threshold.
    if not hits and not has_write_risk(command):
```

(The `_emit_telemetry`/`cache_mod.invocations_record`/`return 0` block that already follows this
`if` — Tier 0.5's own — is unchanged; only the redundant, buggier gate above it is deleted.)

Also update the module docstring's Tier 0.5 line (`~line 5-7`) to drop the now-inaccurate
"nontrivial commands" framing:

```python
  0.5 Read-only pre-filter - any non-Tier-0 command with no heuristic flags
      and no write/network/exec risk pattern - exit silently, regardless of
      shell composition or length. Eliminates LLM calls for piped read-only
      commands like `grep foo | wc -l` and short write-risk-free ones alike.
```

`_NONTRIVIAL_RE` itself is NOT removed — `is_trivial()` (Tier 0) still uses it directly to reject
a Tier-0-trivial-verb command that has shell composition tacked on (e.g. `ls -la | rm -rf /`);
that usage is correct and unrelated to this bug.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_bash_security_review.py -v`
Expected: PASS (full file — confirms every existing Tier 0.5 test, which all use piped or
>120-char commands, is unaffected by removing the now-redundant earlier gate)

- [ ] **Step 5: Commit**

```bash
git add src/cccs_hooks/bash_security_review.py tests/test_bash_security_review.py
git commit -m "fix(bash-security-review): stop bypassing the write-risk check for short commands

run()'s 'nontrivial' gate exited silently for any short (<=120 char),
unpiped, non-heuristic-flagged command without ever consulting
has_write_risk() - a strictly weaker, earlier-running duplicate of Tier
0.5's own check, which already correctly accounts for write risk. A bare
'rm -rf ...' or 'sudo apt install ...' bypassed review entirely as a
result, despite has_write_risk() correctly flagging both. Delete the
redundant gate and let Tier 0.5's check run unconditionally for every
non-Tier-0 command - piped/long commands already went through it
correctly; short/unpiped ones now do too. Genuinely safe short commands
(no heuristic hit, no write risk) are unaffected: they still exit
silently, just via Tier 0.5's real check instead of the buggy shortcut."
```

---

### Task 2: Tier 0 — trust `uv run <already-trusted-verb>` the same as the bare verb

**Status: ✅ Complete.** Implemented, spec-reviewed (compliant). Code-quality review Approved with
three optional Minor suggestions (a stacked-`uv run` regression test, a length-check-uses-
stripped-string invariant test, relocating `_UV_RUN_PREFIX_RE` to sit with the other Tier-0
regexes) — all addressed and re-reviewed as Approved with no remaining issues. Commit:
`1581f1f5ea6ba22ca6d16c4a74e793736d289c70` on branch `f/20260814-uv-aware-command-cache`.

**Files:**
- Modify: `src/cccs_hooks/bash_security_review.py:123-131` (`is_trivial()`)
- Test: `tests/test_bash_security_review.py`

- [ ] **Step 1: Write the failing tests**

Add near the existing `is_trivial` test block (`tests/test_bash_security_review.py`, after
`test_is_trivial_long_command_not_trivial`):

```python
def test_is_trivial_uv_run_pytest() -> None:
    assert bsr.is_trivial("uv run pytest tests/test_foo.py -k bar -v")


def test_is_trivial_uv_run_python() -> None:
    assert bsr.is_trivial("uv run python -m cc_session_tools.cli.ccd --help")


def test_is_trivial_uv_run_different_args_both_trivial() -> None:
    """The whole point: two uv run pytest invocations with different args must
    BOTH independently satisfy is_trivial() - this tier never needs a cache,
    it just needs to recognise the wrapped verb every time."""
    assert bsr.is_trivial("uv run pytest tests/a.py -k foo")
    assert bsr.is_trivial("uv run pytest tests/b.py -v --no-header")


def test_is_trivial_uv_run_with_leading_uv_flag_not_trivial() -> None:
    """uv run --with foo pytest ... - a uv-level flag sits before the wrapped
    verb. Deliberately NOT parsed: bail out rather than risk misidentifying
    what's actually going to execute."""
    assert not bsr.is_trivial("uv run --with foo pytest tests/a.py")


def test_is_trivial_uv_run_untrusted_verb_not_trivial() -> None:
    """uv run wrapping a verb that ISN'T already Tier-0-trusted must not
    become trivial just because it's uv-wrapped."""
    assert not bsr.is_trivial("uv run ./some-script.sh")
    assert not bsr.is_trivial("uv run rm -rf /tmp/x")


def test_is_trivial_uv_run_pipe_still_not_trivial() -> None:
    """Shell composition inside the wrapped command still disqualifies it,
    same as it already does for a bare trivial verb."""
    assert not bsr.is_trivial("uv run pytest tests/a.py | tee out.log")


def test_is_trivial_bare_uv_without_run_not_trivial() -> None:
    """uv sync / uv build etc. are not Tier 0 - they go through Tier 2's
    package-manager cache rule instead (see Task 6). Only 'uv run <verb>'
    is handled here."""
    assert not bsr.is_trivial("uv sync --extra dev")
```

(Note: Task 2 already shipped with this docstring reading "see Task 5" — accurate numbering at
the time, now off-by-one after Task 4 was inserted below. Not worth amending an already-reviewed,
already-merged commit for a one-word drift in a comment; left as a known, harmless inaccuracy.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_bash_security_review.py -k "is_trivial_uv" -v`
Expected: mixed — `test_is_trivial_uv_run_pytest`, `test_is_trivial_uv_run_python`, and
`test_is_trivial_uv_run_different_args_both_trivial` FAIL (none of these `uv run ...` cases
currently match `_TRIVIAL_RE`); the other three selected tests (the leading-flag, untrusted-verb,
and pipe cases) already PASS today, since they assert `not is_trivial(...)`, which already holds
when `uv` isn't matched by `_TRIVIAL_RE` at all — they're written now to lock in that existing
behaviour, not to test new behaviour.

- [ ] **Step 3: Implement the prefix-strip**

In `src/cccs_hooks/bash_security_review.py`, add a new compiled regex directly above
`is_trivial()` (around line 123):

```python
# Matches only 'uv run <verb>' where <verb> does not itself start with '-' -
# a uv-level flag between 'run' and the wrapped verb (e.g. `uv run --with foo
# pytest ...`) is deliberately NOT parsed here; is_trivial() should bail out
# rather than risk misidentifying what's actually going to execute.
_UV_RUN_PREFIX_RE = re.compile(r"^\s*uv\s+run\s+(?!-)")
```

Then replace `is_trivial()` (lines 123-131):

```python
def is_trivial(command: str) -> bool:
    """True if the command is on the trivial allowlist with no shell composition.

    A leading 'uv run ' is stripped first (when not followed by a uv-level
    flag) so a uv-wrapped invocation of an already-trusted verb - e.g.
    `uv run pytest ...`, `uv run python -m ...` - gets the exact same trust
    decision the bare verb already gets. This is not a new trust decision:
    every verb this can newly match was already unconditionally trusted by
    _TRIVIAL_RE before this function is ever reached.
    """
    checked = _UV_RUN_PREFIX_RE.sub("", command, count=1)
    if not (_TRIVIAL_RE.match(checked) or _GIT_TRIVIAL_RE.match(checked)):
        return False
    if _NONTRIVIAL_RE.search(checked):
        return False
    if len(checked) >= 120:
        return False
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_bash_security_review.py -v`
Expected: PASS (full file, confirms no regression on the existing trivial/heuristic/cache tests)

- [ ] **Step 5: Commit**

```bash
git add src/cccs_hooks/bash_security_review.py tests/test_bash_security_review.py
git commit -m "fix(bash-security-review): trust uv-wrapped already-trivial verbs

is_trivial() matched _TRIVIAL_RE (pytest, python3, npm, node, ...) against
the first token only, so 'uv run pytest ...' never got the same zero-review
trust decision a bare 'pytest ...' already gets. Strip a leading 'uv run '
prefix (only when not followed by a uv-level flag, to avoid misidentifying
what's actually executing) before the trivial check - this extends an
existing trust decision to a wrapped form of the same verb, it does not
create a new one."
```

---

### Task 3: Fix a word-boundary bug that would silently defeat Task 6 for `uv sync`

**Status: ✅ Complete.** Implemented, spec-reviewed (compliant). Code-quality review found one
cheap Minor issue (a test assertion for `uv build` that didn't actually exercise the fix — no
substring of `nc|ncat|netcat|socat` in that command at all — removed) and one separate, larger
finding: the same unbounded-substring defect exists in several OTHER `_HEURISTIC_PATTERNS` entries
(`env`/`printenv`, `base64`, `id_rsa`/`id_ed25519`, `wget`/`curl`), same low severity (forces an
extra LLM review, not a security bypass). Surfaced to the user rather than silently expanding this
task's scope — **folded into this plan as Task 4** at the user's explicit request. Re-reviewed and
Approved after the Minor fix. Commit: `6b79249d86e737b31619ed823cce2420514c2436` on branch
`f/20260814-uv-aware-command-cache`.

**Found during plan review, not in the original scope — required before Tasks 5-6, not
optional.** `_HEURISTIC_PATTERNS`' "raw network tool" entry (`bash_security_review.py:55`) is
`re.compile(r"(nc|ncat|netcat|socat)\s")` with no word boundary before `nc`, so it matches the
substring `nc ` inside `sync ` — confirmed empirically: `heuristic_flags("uv sync --extra dev")`
returns `['raw network tool']` today. In `run()`, any non-empty `hits` forces `skip_cache = True`
(`bash_security_review.py:295`), which forces `norm_form = None` regardless of what Task 6 adds to
`_PKG_SAFE_SUBCMDS`, and later blocks `cache_record()` from ever firing. Without this fix, Task 6
would ship a `uv` cache-table entry that never actually caches `uv sync` — its own headline case —
while its `normalise()`-only unit tests would pass anyway and hide the problem entirely.

**Files:**
- Modify: `src/cccs_hooks/bash_security_review.py:55` (`_HEURISTIC_PATTERNS`)
- Test: `tests/test_bash_security_review.py`

- [ ] **Step 1: Write the failing test**

Add near the existing heuristic-flag tests in `tests/test_bash_security_review.py`:

```python
def test_heuristic_raw_network_tool_word_boundary() -> None:
    """'sync' contains the substring 'nc ' - the pattern must not false-positive
    on it, or on any other word that merely contains nc/ncat/netcat/socat."""
    assert bsr.heuristic_flags("uv sync --extra dev") == []
    assert bsr.heuristic_flags("uv build --wheel -o dist/") == []
    assert bsr.heuristic_flags("rsync -av src/ dst/") == []
    # still correctly flags the real thing:
    assert "raw network tool" in bsr.heuristic_flags("nc -l 1234")
    assert "raw network tool" in bsr.heuristic_flags("socat TCP-LISTEN:8080 -")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bash_security_review.py -k word_boundary -v`
Expected: FAIL — `heuristic_flags("uv sync --extra dev")` returns `['raw network tool']`, not `[]`

- [ ] **Step 3: Add the word boundary**

In `src/cccs_hooks/bash_security_review.py`, in `_HEURISTIC_PATTERNS` (around line 55), change:

```python
    (re.compile(r"(nc|ncat|netcat|socat)\s"), "raw network tool"),
```

to:

```python
    (re.compile(r"\b(nc|ncat|netcat|socat)\s"), "raw network tool"),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_bash_security_review.py -v`
Expected: PASS (full file — confirms the real `nc`/`socat` cases the pattern exists for still fire,
and no other existing test relied on the old, over-broad substring match)

- [ ] **Step 5: Commit**

```bash
git add src/cccs_hooks/bash_security_review.py tests/test_bash_security_review.py
git commit -m "fix(bash-security-review): word-boundary the raw-network-tool heuristic

(nc|ncat|netcat|socat)\\s had no word boundary before the alternation, so it
matched the substring 'nc ' inside any word ending in those letters followed
by a space - 'uv sync ', 'rsync ', etc. all false-positived as a 'raw network
tool' heuristic hit. Found while adding a uv cache-table entry: the false hit
forces skip_cache=True in run(), which would have silently defeated caching
for 'uv sync' specifically, with no normalise()-only unit test able to catch
it - only an end-to-end run() test (added two commits from now) would."
```

---

### Task 4: Sweep the remaining word-boundary bugs in `_HEURISTIC_PATTERNS`

**Status: ✅ Complete.** Implemented, spec-reviewed (compliant). Code-quality review found two
real gaps: the `wget`/`curl` fix only had a left-side boundary (still false-positived on `curlie`,
a real curl-compatible client) — fixed to `\b(wget|curl)\b.*-O\s*/`; and the `id_rsa`/`id_ed25519`
fix wasn't isolated by any test (all positive cases matched via the untouched `.ssh` alternative
regardless) — fixed with a `.ssh`-free negative test. Re-reviewed and Approved, with the reviewer
independently hand-tracing that both fixes are genuine differentiators, not just new assertions.
Commit: `5c5b3a46e30712f6e2b760525a6aa87e7ff9cf3d` on branch `f/20260814-uv-aware-command-cache`.

**Folded into this plan at the user's explicit request, after Task 3's code-quality review found
the same unbounded-substring defect elsewhere.** Task 3 fixed one instance (`nc`/`ncat`/`netcat`/
`socat` matching inside `sync`/`rsync`); the same shape exists in four more entries: `env`/
`printenv` (`env\s*$` matches inside any word ending in `...env`; `printenv` matches inside
`printenvironment...`), `base64` (unbounded on the left, matches inside `somebase64`), `id_rsa`/
`id_ed25519` (unbounded both sides, matches inside `myid_rsa_backup.txt`), and `wget`/`curl` in
the "download to absolute path" entry (unbounded on the left, matches inside `newwget`). Same
severity class as Task 3: a false hit only forces an extra Tier-3 review (cost/latency), not a
security bypass — unlike Task 1's bug, nothing here lets a dangerous command through unreviewed.

Verified empirically before writing this task (not assumed): all 5 known false-positive commands
stop matching after the fix, and 11 legitimate matches (bare `printenv`, `env` piped/at end,
`base64 -d`, real `~/.ssh/id_rsa`/`id_ed25519`/`~/.aws/credentials`/`~/.netrc` paths, `wget`/`curl
... -O /path`) all still fire correctly.

**Files:**
- Modify: `src/cccs_hooks/bash_security_review.py` (`_HEURISTIC_PATTERNS` — 4 entries: "base64
  decode", "credentials path", "env dump", "download to absolute path")
- Test: `tests/test_bash_security_review.py`

- [ ] **Step 1: Write the failing tests**

Add near the existing `test_heuristic_raw_network_tool_word_boundary` in
`tests/test_bash_security_review.py`:

```python
def test_heuristic_env_dump_word_boundary() -> None:
    """A variable/word merely ending or starting with 'env'/'printenv' must
    not false-positive as an env dump."""
    assert bsr.heuristic_flags("FOO=goodenv") == []
    assert bsr.heuristic_flags("echo printenvironment.sh") == []
    # still correctly flags the real thing:
    assert "env dump" in bsr.heuristic_flags("printenv")
    assert "env dump" in bsr.heuristic_flags("aws creds | env")
    assert "env dump" in bsr.heuristic_flags("env")


def test_heuristic_base64_decode_word_boundary() -> None:
    assert bsr.heuristic_flags("somebase64 -d file") == []
    assert "base64 decode" in bsr.heuristic_flags("base64 -d file.txt")
    assert "base64 decode" in bsr.heuristic_flags("cat secret | base64 --decode")


def test_heuristic_credentials_path_word_boundary() -> None:
    assert bsr.heuristic_flags("myid_rsa_backup.txt cat") == []
    assert "credentials path" in bsr.heuristic_flags("cat ~/.ssh/id_rsa")
    assert "credentials path" in bsr.heuristic_flags("cat ~/.ssh/id_ed25519")
    assert "credentials path" in bsr.heuristic_flags("cat ~/.aws/credentials")
    assert "credentials path" in bsr.heuristic_flags("cat ~/.netrc")


def test_heuristic_download_to_absolute_path_word_boundary() -> None:
    assert bsr.heuristic_flags("newwget --something -O /tmp/x") == []
    assert "download to absolute path" in bsr.heuristic_flags(
        "wget https://example.com/x -O /tmp/y"
    )
    assert "download to absolute path" in bsr.heuristic_flags(
        "curl https://example.com/x -O /tmp/y"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_bash_security_review.py -k "word_boundary" -v`
Expected: mixed — the 4 new false-positive assertions (`FOO=goodenv`, `printenvironment.sh`,
`somebase64 -d file`, `myid_rsa_backup.txt`, `newwget`) FAIL against the current patterns; the
true-positive assertions and Task 3's own test already PASS.

- [ ] **Step 3: Add word boundaries to the four patterns**

In `src/cccs_hooks/bash_security_review.py`'s `_HEURISTIC_PATTERNS`, change these lines:

```python
    (re.compile(r"base64\s+(-d|--decode)"), "base64 decode"),
    (re.compile(r"(\.ssh|id_rsa|id_ed25519|\.aws/credentials|\.netrc)"), "credentials path"),
    (re.compile(r"(printenv|env\s*$|env\s*\|)"), "env dump"),
```

and

```python
    (re.compile(r"(wget|curl).*-O\s*/"), "download to absolute path"),
```

to:

```python
    (re.compile(r"\bbase64\s+(-d|--decode)"), "base64 decode"),
    (re.compile(r"(\.ssh|\bid_rsa\b|\bid_ed25519\b|\.aws/credentials|\.netrc)"), "credentials path"),
    (re.compile(r"(\bprintenv\b|\benv\s*$|\benv\s*\|)"), "env dump"),
```

and

```python
    (re.compile(r"\b(wget|curl).*-O\s*/"), "download to absolute path"),
```

`.ssh`/`.aws/credentials`/`.netrc` are left as-is — they're already dot-prefixed path fragments,
not plain words, and no false positive was demonstrated for them; `\b` doesn't compose cleanly
with a leading `.` (a non-word character) for no demonstrated benefit. "pipe to shell" and "eval"
already have correct boundaries (`\|\s*`/`(^|\s)` and `(\s|$)`) and are untouched; "setuid chmod"
and "system path" weren't found to have this defect and are untouched.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_bash_security_review.py -v`
Expected: PASS (full file — confirms the existing `base64 decode`/`credentials path` tests, which
already use boundary-safe commands, are unaffected)

- [ ] **Step 5: Commit**

```bash
git add src/cccs_hooks/bash_security_review.py tests/test_bash_security_review.py
git commit -m "fix(bash-security-review): word-boundary the remaining unbounded heuristics

Task 3 fixed the raw-network-tool pattern matching 'nc ' inside 'sync '; the
same unbounded-substring shape existed in four more _HEURISTIC_PATTERNS
entries, found during that task's own code review: base64 (matched inside
'somebase64'), id_rsa/id_ed25519 (matched inside 'myid_rsa_backup.txt'),
printenv/env (matched 'printenv' inside 'printenvironment...', and a bare
'...env' at end-of-string regardless of what preceded it), and wget/curl in
the absolute-path-download pattern (matched inside 'newwget'). Same
severity class as Task 3: a false hit only forces an extra Tier-3 review,
not a security bypass. .ssh/.aws/credentials/.netrc are left as-is - no
false positive was demonstrated for them, and \\b doesn't compose cleanly
with a leading '.' for no demonstrated benefit."
```

---

### Task 5: Recognise `uv sync`/`build`/`lock` as write-risk, matching `npm install`/`pip install`

**Status: ✅ Complete.** Implemented, spec-reviewed (compliant), code-quality-reviewed (Approved —
no blocking or important issues; three Minor observations noted, none requiring a fix: a
plan-inherited docstring slightly overclaims `uv export` coverage that no test actually asserts,
`uv add`/`remove`/`publish`/`tool install` are a deliberately-out-of-scope residual gap, and the
new inline comment is longer than its neighbours but justified by the four exclusions it
documents). Commit: `7c9f70a` on branch `f/20260814-uv-aware-command-cache`.

**Found during plan review's second round, verified by applying Task 3's fix locally and
re-testing — required so removing Task 3's accidental protection doesn't leave these commands
with *less* scrutiny than before.** Today, `has_write_risk("npm install")` and
`has_write_risk("pip install requests")` are both `True` — `_WRITE_RISK_RE`'s package-management
alternation already lists them. `has_write_risk("uv sync --extra dev")` is `False` — `uv` isn't in
that pattern at all, despite `uv sync`/`uv build`/`uv lock` having comparable real effects
(fetching packages over the network, writing a venv/lockfile/wheel to disk). Before Task 3's fix,
`uv sync` still got a full review every time regardless, via the heuristic false positive; after
Task 3's fix removes that accident, `uv sync` would have zero heuristic hits AND (without this
task) zero write-risk match — combined with Task 1 already having fixed the `nontrivial`-gate
bypass, this task is what determines whether `uv sync` reaches Tier 0.5's real check with anything
to flag it at all. This task closes that gap by giving `uv sync`/`build`/`lock` the same
write-risk recognition `npm install`/`pip install` already have.

**Files:**
- Modify: `src/cccs_hooks/bash_security_review.py:68-90` (`_WRITE_RISK_RE`)
- Test: `tests/test_bash_security_review.py`

- [ ] **Step 1: Write the failing tests**

Add near the existing `has_write_risk` parametrised test block in
`tests/test_bash_security_review.py` (the `@pytest.mark.parametrize("cmd", [...])` block covering
write-risk commands):

```python
def test_write_risk_uv_sync_build_lock() -> None:
    assert bsr.has_write_risk("uv sync --extra dev")
    assert bsr.has_write_risk("uv build --wheel -o dist/")
    assert bsr.has_write_risk("uv lock")


def test_write_risk_uv_read_only_subcommands_unaffected() -> None:
    """uv tree / uv version / uv export don't fetch or install anything new -
    same treatment as npm build/npm test, which also aren't in _WRITE_RISK_RE."""
    assert not bsr.has_write_risk("uv tree")
    assert not bsr.has_write_risk("uv version")


def test_write_risk_uv_run_unaffected() -> None:
    """uv run's write risk (if any) depends entirely on the wrapped command,
    not on 'uv run' itself - this pattern must not fire on it."""
    assert not bsr.has_write_risk("uv run pytest tests/a.py")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_bash_security_review.py -k "write_risk_uv" -v`
Expected: mixed — only `test_write_risk_uv_sync_build_lock` FAILs (`has_write_risk("uv sync
--extra dev")` etc. return `False` today); `test_write_risk_uv_read_only_subcommands_unaffected`
and `test_write_risk_uv_run_unaffected` already PASS today, since `uv` isn't in `_WRITE_RISK_RE`
at all yet — they're written now to lock in that existing behaviour, not to test new behaviour.

- [ ] **Step 3: Add the pattern**

In `src/cccs_hooks/bash_security_review.py`'s `_WRITE_RISK_RE` (around line 68-90), add a line
directly after the existing `npm` package-management entry:

```python
    | \bnpm\s+(?:install|uninstall|publish|update|ci)\b
    | \buv\s+(?:sync|build|lock)\b
```

(`run`, `tool`, `python`, `pip`, `export`, `tree`, `version` are deliberately not listed here —
`run`/`tool`/`python` because their risk depends entirely on what they wrap, not on the verb
itself; `pip` because `uv pip install` has different safety semantics from a bare `pip install`
and isn't handled by this plan; `export`/`tree`/`version` because they don't fetch or write new
content, matching how `npm build`/`npm test`/`cargo build` are also absent from this pattern today.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_bash_security_review.py -v`
Expected: PASS (full file, confirms no regression on the existing `has_write_risk` parametrised
cases)

- [ ] **Step 5: Commit**

```bash
git add src/cccs_hooks/bash_security_review.py tests/test_bash_security_review.py
git commit -m "fix(bash-security-review): recognise uv sync/build/lock as write-risk

npm install and pip install are already in _WRITE_RISK_RE's package-
management alternation; uv sync/build/lock have comparable effects (fetch
packages over the network, write a venv/lockfile/wheel to disk) but weren't
recognised at all. This matters now specifically because an earlier commit
in this branch removed uv sync's only source of scrutiny today - an
accidental heuristic false-positive that forced a full review every time.
Without this fix, that removal would leave uv sync with strictly less
scrutiny than before instead of the intended review-once-then-cached
treatment a later commit's cache-table entry provides. run/tool/python/pip/
export/tree/version are deliberately excluded - see the code comment for
why each one is."
```

---

### Task 6: Tier 2 — cache `uv`'s own closed-ended subcommands

**Status: ✅ Complete.** Implemented, spec-reviewed (compliant), code-quality-reviewed (Approved —
no blocking or important issues; the end-to-end integration test was independently traced through
`run()`'s tier gating and confirmed to genuinely exercise Tier 2, not just `normalise()` in
isolation). Commit: `5ea3a5c` on branch `f/20260814-uv-aware-command-cache`.

**Pre-existing, out-of-scope issue found during review (not introduced by, or Task 6's
responsibility to fix):** `tests/test_normalise.py` has two functions both named
`test_pip_install_normalises` (one asserting on `pip3`, one on `pip`) — confirmed present before
this commit too. Python only keeps the later definition, so the `pip3` one is silently shadowed
and never collected by pytest; the `pip3` normalisation path currently has zero live test
coverage. Flagged for a separate follow-up fix (rename one, e.g. `test_pip3_install_normalises`),
not folded into this plan.

**Files:**
- Modify: `src/cccs_hooks/normalise.py:78-84` (`_PKG_SAFE_SUBCMDS`)
- Test: `tests/test_normalise.py`, `tests/test_bash_security_review.py`

- [ ] **Step 1: Write the failing tests**

Add near the existing package-manager tests (`tests/test_normalise.py`, after
`test_cargo_build_normalises`):

```python
def test_uv_sync_normalises():
    assert normalise("uv sync --extra dev") == "uv sync <ARGS>"


def test_uv_build_normalises():
    assert normalise("uv build --wheel -o dist/") == "uv build <ARGS>"


def test_uv_lock_normalises():
    assert normalise("uv lock") == "uv lock <ARGS>"


def test_uv_run_returns_none():
    """uv run is NOT a closed-ended subcommand - it executes an arbitrary
    wrapped command, so it must never collapse to one cache key here (that
    would let one cached-safe 'uv run X' silently authorise a later, unrelated
    'uv run Y'). Handled instead by Tier 0's prefix-strip (Task 2) or, for
    untrusted wrapped verbs, by falling through to a real review every time -
    never by normalisation."""
    assert normalise("uv run pytest tests/a.py") is None


def test_uv_tool_returns_none():
    """uv tool run / uvx can invoke arbitrary installed tools - same
    wrapped-arbitrary-command concern as uv run."""
    assert normalise("uv tool run some-tool") is None


def test_uv_python_returns_none():
    """uv python can invoke an arbitrary interpreter - excluded for the same
    reason python/python3 are in _NEVER_NORMALISE."""
    assert normalise("uv python install 3.13") is None


def test_uv_pip_returns_none():
    """uv pip wraps pip's own subcommands with different safety semantics
    from a bare pip install (e.g. --system can install outside a venv) -
    deliberately not aliased into the existing 'pip' entry."""
    assert normalise("uv pip install requests") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_normalise.py -k "test_uv_" -v`
Expected: FAIL (`test_uv_sync_normalises`/`test_uv_build_normalises`/`test_uv_lock_normalises`
fail because `normalise()` returns `None` for all `uv` commands today; the four `_returns_none`
tests already pass today for the same reason — they're written now to lock in that behaviour
explicitly, not because they currently fail)

- [ ] **Step 3: Add the `uv` entry**

In `src/cccs_hooks/normalise.py`, edit `_PKG_SAFE_SUBCMDS` (lines 78-84):

```python
_PKG_SAFE_SUBCMDS: dict[str, frozenset[str]] = {
    'npm':   frozenset({'install', 'ci', 'test', 'build'}),
    # 'run', 'start', 'lint' excluded — script name determines what executes
    'pip':   frozenset({'install', 'show', 'list', 'freeze'}),
    'pip3':  frozenset({'install', 'show', 'list', 'freeze'}),
    'cargo': frozenset({'build', 'test', 'check', 'clippy', 'fmt', 'doc'}),
    'uv':    frozenset({'sync', 'build', 'lock', 'export', 'tree', 'version'}),
    # 'run', 'tool', 'python', 'pip' deliberately excluded — each either
    # executes an arbitrary wrapped command/interpreter, or has different
    # safety semantics from the bare tool it wraps. See Task 2 for 'run'.
}
```

No change is needed to the dispatch code (`normalise.py:161-166`) — it already looks up
`verb in _PKG_SAFE_SUBCMDS` and `subcmd in _PKG_SAFE_SUBCMDS[verb]` generically, so `uv run ...`
correctly falls through this rule (`'run'` is not in the frozenset) exactly as the new tests
require, with no special-casing.

- [ ] **Step 4: Write an end-to-end integration test that exercises the real `run()` pipeline**

The `normalise()` unit tests above verify the normalisation *rule* in isolation, but that alone
doesn't prove `uv sync` actually gets cached in practice — Task 3 found exactly this kind of gap
once already. Because Task 1 already fixed the `nontrivial`-gate bug, a **bare, non-piped** `uv
sync --extra dev` now genuinely reaches Tier 2 on its own — verified live, patching all five
tasks into the real source together and running `bsr.run()` for real: no compound (`&&`) form is
needed, unlike what an earlier draft of this plan assumed before Task 1 existed. Add this to
`tests/test_bash_security_review.py`, modelled on the existing `test_norm_cache_hit_skips_claude`
(same file — read it first for the precise fixture shape) but using a realistic, non-compound
command pair:

```python
def test_uv_sync_norm_cache_hit_skips_claude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    """Real end-to-end proof, not just a normalise()-unit-test one: two bare
    'uv sync' invocations with different flags share a norm_sha cache entry,
    and the second one never calls Claude. Reaches Tier 2 without any
    compound (&&) trick because Task 1 fixed the nontrivial-gate bug that
    would otherwise have made this test require one."""
    monkeypatch.setenv("CCCS_USE_COMMAND_CACHE", "1")
    monkeypatch.setenv("CCCS_CACHE_DB", str(tmp_path / "cache.db"))
    monkeypatch.setenv("CCCS_HOOKS_DIR", str(tmp_path / "hooks"))
    monkeypatch.delenv("CCCS_CACHE_PATH", raising=False)
    monkeypatch.delenv("CCCS_CLAUDE_BIN", raising=False)
    from cccs_hooks import cache as cache_mod
    from cccs_hooks import normalise as norm_mod

    cmd_a = "uv sync --extra dev"
    cmd_b = "uv sync --extra test"  # different flag, same norm_sha
    assert bsr.has_write_risk(cmd_a)  # sanity-check Task 5 actually landed
    assert bsr.heuristic_flags(cmd_a) == []  # sanity-check Task 3 actually landed
    exact_sha = cache_mod.sha256_command(cmd_a)
    norm_form = norm_mod.normalise(cmd_a)
    assert norm_form == "uv sync <ARGS>"  # sanity-check Step 3 actually landed
    norm_sha = cache_mod.sha256_command(norm_form)
    cache_mod.cache_record(exact_sha, "safe", "none", cmd_a, norm_sha=norm_sha)

    spy = mocker.patch("cccs_hooks.bash_security_review.call_claude")
    result = bsr.run(_input(cmd_b))

    assert result == 0
    spy.assert_not_called()
```

This exact scenario (all five tasks applied together) was verified live against the real `run()`
pipeline while writing this plan: the second command hit the cache via `norm_sha` and printed a
`(cached, ...)` review, with `call_claude` never invoked — confirmed both with and without a
compound `&&` form, and the plan was updated to use the simpler, more realistic non-compound form
once Task 1 made it work.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_normalise.py tests/test_bash_security_review.py -v`
Expected: PASS (both full files, confirms no regression on the existing 50 `normalise` tests plus
the full `bash_security_review` suite, and that the new integration test genuinely passes)

- [ ] **Step 6: Commit**

```bash
git add src/cccs_hooks/normalise.py tests/test_normalise.py tests/test_bash_security_review.py
git commit -m "feat(normalise): cache uv's closed-ended subcommands (sync/build/lock/...)

Adds a 'uv' entry to _PKG_SAFE_SUBCMDS alongside npm/pip/cargo, scoped to
subcommands that don't execute an arbitrary wrapped command: sync, build,
lock, export, tree, version. 'run', 'tool', and 'python' are deliberately
excluded - each can execute arbitrary code, the same concern that already
keeps bash/python/node out of _NEVER_NORMALISE; collapsing them to one
cache key would let a first cached-safe invocation silently authorise a
later, unrelated one. 'uv pip' is also excluded rather than aliased to the
existing 'pip' entry - it has different safety semantics (e.g. --system).
'uv run <already-trusted-verb>' is handled separately, at Tier 0 (Task 2) -
it fully bypasses review rather than getting a cached one, matching the
bare verb's existing trust level exactly. Includes an end-to-end run()-level
test using a realistic, non-compound command pair - reachable without a
compound (&&) form specifically because Task 1's nontrivial-gate fix is
already in this branch."
```

---

### Task 7: Full-suite verification

**Status: ✅ Complete.** `uv run pytest -q` run from the worktree root — exit code 0, all tests
passed, no failures. (Manual smoke test in Step 2 skipped as optional; Tasks 1-6 were each already
verified end-to-end against the real `run()` pipeline during plan-writing and again during each
task's own implementation/review cycle.)

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite**

```bash
uv run pytest -q
```

Expected: all pass, 0 failures — confirms Tasks 1-6 didn't regress `_TRIVIAL_RE`/`_NONTRIVIAL_RE`
matching, `has_write_risk`, the read-only pre-filter, heuristic flags, or any other
`bash_security_review`/`normalise`/`cache` test.

- [ ] **Step 2: Manual smoke test (optional but recommended given this touches a security hook)**

From a real shell with `CCCS_USE_COMMAND_CACHE=1` set (already default in
`~/.claude/settings.json`), run the same `uv run pytest <file>` command twice with different
`-k`/file arguments each time and confirm neither invocation prints a `[security review]` block —
both should now be Tier 0 silent, matching a bare `pytest` call. Separately, run a synthetic
short/unpiped write-risk command you don't mind reviewing once (not literally destructive — e.g.
`mv nonexistent-file.txt /tmp/`) and confirm it now prints a `[security review]` block instead of
silently passing.

---

## Out of scope (deliberately, not oversights)

- **`uv run` recursive normalisation** (collapsing `uv run pytest <ARGS>` to a Tier-2 cache entry
  keyed on the wrapped verb, for verbs that aren't already Tier-0-trusted) — Task 2's prefix-strip
  already fully solves the dominant real-world case (`pytest`/`python3`/`npm`/`node`, all already
  Tier-0-trusted bare) with a strictly smaller, easier-to-reason-about change (zero new cache
  entries, no recursion, no risk of a cached verdict for one wrapped command silently covering a
  different one). Revisit only if a real `uv run <verb-not-already-in-_TRIVIAL_RE>` pattern turns
  out to be common enough in practice to justify the added complexity and the cache-poisoning
  analysis it would require.
- **`uv run --with`/other uv-level-flag-before-verb forms** — Task 2 deliberately bails out
  (falls through to a real review, same as today) rather than parse uv's own flag surface. Uncommon
  in this session's actual usage; revisit only if it turns out to be common.
- **`uv add`/`uv remove`/`uv publish`/`uv tool install`** — genuinely write-risk-worthy uv actions
  this plan doesn't touch at all (not added to `_WRITE_RISK_RE` or `_PKG_SAFE_SUBCMDS`). They have
  no heuristic hit and no write-risk match today, so they exit silently, unreviewed, at Tier 0.5 —
  same as before this branch, not a regression this plan introduces, just an existing coverage gap
  it doesn't close. Not added because they weren't part of the reported pain point and each
  deserves its own deliberate look rather than being swept in by analogy to `sync`/`build`/`lock`.
- **Auditing `_TRIVIAL_RE`'s and `_WRITE_RISK_RE`'s existing coverage more broadly** (e.g. whether
  `npm`/`pip3?`/`python3?` truly deserve blanket Tier-0 trust, or whether `mkdir`/other verbs
  should be added to `_WRITE_RISK_RE`) — out of scope. Those are pre-existing, deliberate design
  decisions this plan doesn't have grounds to revisit; Diagnosis point 6 documents one place a
  test example was initially, wrongly assumed to be a bug when it's actually this same deliberate
  trust decision working as designed.
- **Task #112's other half** (the permission-prompt side, `Bash(uv *)` in
  `~/.claude/settings.json`) — already fixed, separately, earlier in this session. This plan is
  scoped to the security-review/cache side only.
