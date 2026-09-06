## 1. Write the skill

- [x] 1.1 Create `src/cc_session_tools/skills/compact-smart/SKILL.md` with frontmatter
      (`name: compact-smart`, a `description` covering trigger phrases: "/compact-smart",
      "give me a paragraph to pass to compact", "draft a compact instruction", "help me
      compact this session") and body sections: When to use, What it drafts (the structure
      from design.md's Context - decisions+rationale, artifact pointers, standing
      constraints, pending tasks split mine/yours, explicit next step), How it handles
      `working/WORKLOG.md` (refresh if present via `$CLD_SESSION_DIR`, never create -
      design.md Decision 2/3), the confirmation step (design.md Decision 4), and the final
      output format (the literal `/compact <text>` command - design.md Decision 5).
- [x] 1.2 Write a failing test asserting `compact-smart` is discovered as a bundled skill
      (`ccst._discover_skills(ccst._discover_source_dir())`), matching the
      `test_pm_pdata_audit_and_migrate_are_bundled_skills` precedent in
      `tests/test_ccst_bundle_discovery.py`. Implement (satisfied once 1.1 lands).

## 2. Document it

- [x] 2.1 Add a `compact-smart` row to `README.md`'s bundled-skills table.

## 3. Manual verification

- [ ] 3.1 Manually invoke `/compact-smart` in a real session with genuine session history
      (this session, once ready to compact) and confirm: the draft covers real
      decisions/pointers/tasks from the actual conversation, an existing `WORKLOG.md` (if
      any) is refreshed rather than skipped or wrongly created, and the final output is a
      directly-runnable `/compact <text>` command with no further editing needed. **Deferred
      to after this PR merges and the skill is installed** - the user asked mid-branch to
      pause and exit/resume the session, so this real-world exercise (which needs a live
      session actually reaching a natural compaction point) happens in a later session, not
      blocking this PR. The mechanical parts (1.1/1.2/2.1) are complete and tested.

## 4. Version bump and release artifacts

- [x] 4.1 Bump `pyproject.toml`'s `version` to `3.2.0` (minor - new bundled skill, no
      breaking change), run `uv lock`, commit both together.
- [x] 4.2 Write the `[3.2.0]` CHANGELOG.md entry.
- [x] 4.3 Full test suite green (`uv run pytest -q`); no `.py` production files touched by
      this change (pure-instruction skill, per design.md Decision 1), so no mypy delta to
      check beyond the one test file, which needs no type annotations beyond what already
      exists in that file.
- [x] 4.4 `openspec-sync-specs` (`skills/compact-smart` main spec created and validated) +
      `openspec-archive-change` for `add-compact-smart-skill`.
- [ ] 4.5 Recommend a PR title/body (style matching recent merged PRs) and confirm with the
      user before running `gh pr create`. No attribution lines in commits or the PR
      description.
- [ ] 4.6 Give the user the PR URL once open.
