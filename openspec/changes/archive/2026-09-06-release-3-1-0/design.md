## Context

See `proposal.md` - Why for motivation. Five independent items; this section records the
concrete code investigation each was grounded in before implementation.

- **Item 1 (Telegram)**: `send_telegram(message, *, post=_default_post) -> bool`
  (`lib/scheduler/notify.py:75`) is best-effort - returns `False` and logs on missing
  credentials or transport failure, never raises. Two existing call sites confirm the pattern:
  `notify.suspended()` (same file) and `lib/pdata/sync_notify.py:notify_conflict()`, which calls
  `send_telegram(message)` directly with no error handling around it, exactly the "fire and
  forget" shape item 1 needs. `confirm_8digit.verify()` (`hooks/confirm_8digit.py:171-262`)
  already builds a `VerificationResult(exit_code, message)` for every block/warn branch - the
  natural call site is right where each `VerificationResult` is constructed, using that same
  `message` text.
- **Item 2 (ccmsg age)**: `MessageRow` (`lib/messaging/service.py:112-119`) has no `sent_at`
  field; `list_messages()` (line 121) builds each row from the full `Message` object, which does
  have `sent_at`. `_relative_age(sent_at, now)` (line 137) already exists and is already used by
  `_digest_line()` (line 150). `ccmsg.py:_cmd_list` (line 203) prints
  `f"[{r.id}] {r.status:8} {r.to_kind}={r.to_value} · {r.subject}"` with no timestamp at all.
- **Item 3 (add-field type mismatch)**: `add_extension_column()` (`lib/pdata/repository.py:172-201`)
  returns `False` (a documented no-op) the moment `field_name in existing`
  (`list_extension_columns()`, line 160-169, names only, no types) - it never compares the
  requested `sql_type` against what is actually stored. `schema_add_field()`
  (`lib/pdata/service.py:439-467`) is the only caller from the CLI path; the CLI itself
  (`ccst.py:_cmd_pdata_schema_add_field`, line 985) already catches `ValueError` from this
  function and reports it cleanly with exit 2 - the same convention `naming.validate_field_name`/
  `validate_record_group` already use for this exact command's other input-validation failures.
- **Item 4 (docs)**: pure documentation; `add-field`'s description-update-on-rerun behavior is
  already correct and already tested
  (`test_schema_add_field_rerun_updates_description_without_duplicating_column`) - only the
  README and `--help` text are missing.
- **Item 5 (skill renames)**: confirmed via `grep -rln` across `src/` and `docs/` that
  `pm-pdata-audit`/`pm-pdata-migrate` are referenced only inside their own two `SKILL.md` files
  (each references the other by name) - no other skill, `README.md`, or bundled `CLAUDE.md`
  fragment mentions either. `_cmd_skills_install` (`ccst.py:236`) only ever creates symlinks for
  skills found in the source directory; it never removes a symlink whose target no longer
  exists, and no existing doctor check catches this (`check_skill_symlink` only checks skills
  that ARE in the source, not orphans). `check_no_stale_hooks` (`doctor.py:165-206`) is the
  established "flag settings.json entries CCST no longer recognises" pattern for a structurally
  identical problem (hooks removed/renamed leaving stale registrations) - the new skills check
  mirrors its shape.

## Goals / Non-Goals

**Goals:**
- Each of the five items ships exactly as decided (several as explicit prior user decisions
  recorded in the handoff prompt, not open questions).
- The new stale-skill-symlink check is general (any orphaned symlink, not special-cased to these
  two skill names), so it also covers future skill removals/renames without further doctor
  changes.

**Non-Goals:**
- Rebuilding an existing extension column's actual SQL type. Rejected as disproportionate for a
  metadata-editing command (a real column-type change is a full SQLite table rebuild, the same
  complexity class as the 3.0.0 `sessions` table migration) - see Decision below.
- Any change to `pm-pdata-schema-design`, `pm-project-init`, or `pm-pdata-conflict-resolution` -
  confirmed via grep that none reference the two renamed skills.

## Decisions

**1. Telegram call site and message format.** Call `send_telegram()` directly inside `verify()`
at the point each blocking/warning `VerificationResult` is constructed, reusing that
`VerificationResult.message` text verbatim (already descriptive: tool name, failure reasons,
remediation). No new message-formatting function - the existing text is already the right
content for both stderr and Telegram. Do not resurrect the `telegram_notify` marker/exemption
machinery (`confirm_8digit.py` lines ~77-104) - unrelated, a different exemption path (WhatsApp
send via the `notify-user` skill).

**2. `MessageRow.sent_at` type.** Plain `str` (the already-stored ISO8601 form), matching
`Message.sent_at`'s own type - `_relative_age()` already accepts and parses this exact string
format, so no conversion is needed at the `ccmsg list` call site.

**3. Type-mismatch rejection via `ValueError`, not a new exception class.** Matches this
function's own existing convention (`_normalize_column_type`, `validate_field_name`,
`validate_record_group` all raise `ValueError` for input problems on this exact command), and the
CLI layer already catches `ValueError` generically - no new except-clause needed.
`add_extension_column()` gains a new helper, `_extension_column_type(conn, table, field_name) ->
str | None` (reads `PRAGMA table_info`, returns the stored type or `None` if the column is
absent), and raises inside `add_extension_column()` itself (not in `service.schema_add_field()`)
so the check lives next to the column-existence check it extends, and so any other future caller
of `add_extension_column()` inherits the same protection for free.

**4. Stale-skill-symlink check scope and severity.** WARN, not FAIL, matching
`check_skill_symlink`'s own existing severity for skill-symlink problems (a missing/wrong symlink
degrades a feature, it does not break a running session the way a stale hook registration does -
`check_no_stale_hooks` is FAIL specifically because a hook fires on every matching event and
fails each time). New `check_no_stale_skill_symlinks(skills_target_dir: Path) -> list[CheckResult]`:
list every entry in `skills_target_dir`, keep the ones that are symlinks, and flag any whose
`readlink()` target does not exist. Runs regardless of whether `skills_source_dir` resolved (an
orphan can be reported even if source discovery failed for an unrelated reason) - added as an
unconditional step in `run_all_checks`, guarded only by `skills_target_dir` existing.

**5. `ccmsg` cross-repo notification for the rename.** After the rename lands, send a `ccmsg` to
`claude-code-config-sync` with the new names, per the handoff prompt's explicit instruction
(that repo was already told the *old* names via a prior message).

## Risks / Trade-offs

- **[Risk]** A user relying on the old skill names in their own private notes/scripts will see
  `pm-pdata-audit`/`pm-pdata-migrate` stop resolving after the next `ccst skills install --apply`.
  → **Mitigation**: the new stale-symlink doctor check surfaces exactly this, with the old
  symlink's name visible in the WARN, rather than a silent "the skill just isn't there anymore."
- **[Trade-off]** Rejecting a type-mismatched `add-field` rerun (Decision 3) means a genuine
  intentional type change has no first-class command - the user must manually drop and recreate
  the extension column (documented in the error message's remediation text), losing existing
  data in that column. Accepted per the user's own explicit steer in the handoff prompt, which
  named this exact trade-off and asked for the simpler, safer default.
