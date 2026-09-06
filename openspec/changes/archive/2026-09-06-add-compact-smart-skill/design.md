## Context

See `proposal.md` - Why, and
`cc-sessions/20260905-fork-disambiguation-impl-3-0-0/working/compact-smart-research.md` (in
the `claude` project) for the full research this design is grounded in. Key facts already
established there, not re-derived here:

- `/compact` is explicitly excluded from the set of built-in commands invokable through the
  Skill tool (confirmed via Claude Code's own docs) - a skill can prepare text, never invoke
  compaction itself.
- `/compact [instructions]` is a real, documented, optional free-text argument.
- What survives compaction automatically (CLAUDE.md, memory, system/env info, all re-loaded
  from disk) vs. what doesn't (anything that only ever existed in the conversation, with no
  durable file behind it) - the latter is what this skill's draft needs to protect, and a
  `/compact` instruction only protects the *next* compaction, not later ones, so refreshing a
  durable file (WORKLOG.md) alongside the draft is a real second safeguard, not redundant.
- `src/hooks/worklog_guard.py` already exists: a PreCompact hook (`matcher: "manual"` only)
  that blocks manual `/compact` if the session's `working/WORKLOG.md` is stale (>1hr),
  escape-hatched by `CCCS_ALLOW_STALE_WORKLOG=1`. It never creates a WORKLOG.md itself -
  matching `ccd`'s own convention that WORKLOG.md is opt-in scratch content, not mandatory
  session state.
- Claude Code's own auto-generated pre-compact summary uses a fixed 9-section structure
  (Primary Request/Intent, Key Technical Concepts, Files/Code Sections, Errors/Fixes, Problem
  Solving, All User Messages, Pending Tasks, Current Work, Optional Next Step) - strong prior
  art for what a good preservation draft should cover, adapted below.
- Real transcript examples (past 7 days, all projects) show Chris consistently wants: exact
  artifact pointers, decisions-with-rationale (especially corrections), user-only outstanding
  actions called out distinctly from Claude's own next steps, standing constraints/policies
  restated, and an explicit "what happens next" - plus, at least once, an explicit exclusion
  request (drop a stale unrelated thread). This is the evidentiary basis for spec.md's
  requirements.

## Goals / Non-Goals

**Goals:**
- Automate the DRAFTING Chris currently does by asking for it manually; leave the actual
  `/compact` invocation to him, since that's the one part that's technically not automatable.
- Reuse the existing `WORKLOG.md`/worklog-guard convention rather than inventing new session
  state tracking.

**Non-Goals:**
- Making the skill fire automatically without being invoked. `/compact-smart` is
  user-invoked only (like every other bundled skill in this repo) - it does not hook into
  `PreCompact` itself. Automating it further (e.g. a hook that runs it unprompted before
  every manual `/compact`) is a plausible future enhancement but changes the trigger model
  in a way the research didn't validate and isn't asked for.
- A backing CLI command or data store. There is no deterministic state this skill needs to
  read/write beyond a plain markdown file already handled by ordinary file tools - adding a
  `ccst compact-smart ...` subcommand or a SQLite store would be manufacturing structure this
  skill doesn't need, contrary to this repo's own "ship a query subcommand... before being
  considered done" convention, which applies to genuine *data stores*, not every skill.

## Decisions

**1. Pure instruction-guided skill, no script, no CLI command, no tests beyond bundling
discovery.** Every piece of this skill's actual work - reading the current conversation,
identifying decisions/pointers/constraints, drafting text, asking for confirmation, checking
for and editing a WORKLOG.md file - is something Claude does directly with tools it already
has (Read, Edit, Bash for a stat/mtime check) and its own already-loaded context. There is no
deterministic algorithm to unit-test the way `add-field`'s type check or `sessions_db`'s
uuid resolution are - this skill's "testing" is: (a) it installs and is discovered like every
other bundled skill (a `_discover_skills` assertion, matching the `pm-pdata-*` precedent),
and (b) manual exercise of the actual drafted output quality, which is a judgment call, not
a pytest assertion. This is consistent with this repo's other pure-instruction skills
(`find-claude-code-session`, `reduce-persistent-context`) having no test suite of their own.

**2. WORKLOG.md handling matches `ccd`'s existing opt-in convention exactly.** Refresh if
present (touch its content so the timestamp updates, satisfying `worklog_guard`'s staleness
check for a subsequent manual `/compact`); never create one. This mirrors the hook's own
documented scope boundary ("only fires if a WORKLOG.md already exists - it never forces you
to create one") rather than introducing a second, conflicting policy.

**3. The skill locates the session's `working/` directory via `$CLD_SESSION_DIR`**, the same
environment variable `worklog_guard.py` itself reads - not a fresh discovery mechanism.

**4. Confirmation step is a single review-and-edit pass, not a multi-question interview.**
The research explicitly weighed against option (b) (interactive Q&A) because every real
example already showed Claude producing a usable first draft unprompted - re-litigating
"what's relevant" via a series of questions reintroduces the friction Chris wants removed.
The skill instead: drafts, shows the draft, and accepts free-form edits/exclusions in one
round (matching the PBT transcript's real exclusion request - "discard all the X, keep Y" -
answered in a single turn, not a guided interview).

**5. Final deliverable is the literal ready-to-paste command**, not just prose describing
what to preserve, since design.md's Context already established a skill cannot invoke
`/compact` itself - the most useful artifact this skill can hand back is something the user
can act on with zero further composition effort.

## Risks / Trade-offs

- **[Risk]** A skill-drafted paragraph could still miss something the auto-summary or Chris's
  own hand-written version would have caught, since it runs on the model's own judgment, not
  a fixed algorithm. → **Mitigation**: the confirmation step (spec requirement 3) exists
  precisely so a miss can be caught and fixed before compaction happens, not discovered
  afterward when the detail is already gone.
- **[Trade-off]** No automated test proves the draft's *quality* (decisions genuinely
  captured, pointers genuinely accurate) - only that the skill file exists and installs.
  Accepted per Decision 1: this is inherent to an instruction-guided skill with no
  deterministic backing logic, not a testing gap specific to this change.
