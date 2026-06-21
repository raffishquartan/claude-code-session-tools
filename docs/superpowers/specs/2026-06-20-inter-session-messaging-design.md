# Inter-session messaging for Claude Code sessions

- **Status:** Design — approved for spec-review
- **Date:** 2026-06-20
- **Owner:** raffishquartan
- **Target repo:** `claude-code-session-tools` (CCST)
- **Supersedes / relates to:** deferred Telegram push delivery → separate `scheduled-tasks-catchup` design

## 1. Problem

Claude Code sessions are isolated. A session working in one project (or a different
session in the same project) has no way to leave a durable, addressed, auditable message
for another session. Today the only cross-session channel is the human relaying context by
hand. This blocks natural workflows such as:

- A session that discovers something relevant to another project flagging it there.
- A long-running orchestration handing off a sub-task to a session better placed to do it.
- Two sessions in the same project coordinating without the user shuttling between them.

## 2. Goals

1. A session can **send** a message to: a specific session, a whole project, or a
   *description* of a session ("whoever is working on X").
2. Messages are **delivered without the user having to prompt for them** — both at the
   start of a session and *during* a session's life.
3. Every message durably records: that it was sent, who from, when, its contents, and
   **whether it has been read** by the recipient.
4. Messages may reference **attachments** by absolute path (reference-only; the store does
   not copy file bodies).
5. Reading the store is **token-efficient**: a session never loads the whole store into
   context. A CLI does the filtering and emits compact summaries.
6. Sending is **rename-safe** and **move-safe**: renaming or moving a session/project does
   not orphan its messages.
7. Claude **proactively** considers sending a cross-session message when warranted, and
   **clarifies and confirms the recipient** when it is ambiguous.

## 3. Non-goals (this design)

- **Push delivery to a phone (Telegram) and any cron-based global digest.** This depends
  on the unsolved "laptop is often off; detect and back-fill missed scheduled runs"
  problem, which is split into a separate `scheduled-tasks-catchup` design. Cross-project
  reach in *this* design is achieved purely by hook-time sweeps when a session is active
  (§8), not by a background daemon.
- Real-time / synchronous chat. Messaging is store-and-forward only.
- Encryption or multi-machine sync. The store is a single local directory.
- Cross-user messaging. Single-user, single-machine.

## 4. Architecture overview

Four pieces, all shipped inside CCST:

| Piece | What it is | Installed by |
|-------|-----------|--------------|
| **Message store** | A directory tree of markdown-with-frontmatter files under `~/.claude/cc-messages/` | created lazily by `ccmsg` on first use |
| **`ccmsg` CLI** | New `[project.scripts]` entry point; all read/write/filter logic | `install-everything.sh` step 1 (`uv tool install` / `pipx`) |
| **Delivery hooks** | A `SessionStart` hook and a `UserPromptSubmit` hook, both calling `ccmsg deliver`, injecting `additionalContext` | `ccst hooks install` (step 3) |
| **`send-session-message` skill** | Guides Claude through composing/addressing/confirming a message | `ccst skills install` (step 2, symlink) |

Plus two supporting changes: a new `ccst claude-md install` primitive that maintains a
managed block in the global `~/.claude/CLAUDE.md` (proactive-send behaviour), and a small
update to the existing `move-session` skill (rename/move safety).

Markdown-with-frontmatter is the canonical and only store format. Frontmatter is
Python-parseable, message volume is small, and the files stay human-readable and
greppable. No JSONL; if volume ever explodes, a derived index can be added later without
changing the canonical format.

## 5. Message store layout

```
~/.claude/cc-messages/
├── projects/
│   └── <project-name>/
│       ├── inbox/
│       │   └── <sortable-id>__<slug>.md
│       └── archive/
│           └── YYYY-MM/
│               └── <sortable-id>__<slug>.md
├── repos/
│   └── <repo-name>/
│       ├── inbox/
│       └── archive/YYYY-MM/
├── other-paths/
│   ├── inbox/
│   └── archive/YYYY-MM/
├── _global/                      # description-addressed + broadcast messages
│   ├── inbox/
│   └── archive/YYYY-MM/
└── .cursors/
    └── <session-uuid>.json       # per-session delivery cursor
```

- **Partitioning by location** lets a hook sweep only the partitions relevant to the
  current session's working directory, keeping reads cheap.
- A session's location is derived from the hook's stdin `cwd`: a known project name →
  `projects/<name>/`; a known repo → `repos/<name>/`; anything else → `other-paths/`.
  (`other-paths` keys on a stable slug of the absolute path.)
- `_global/` holds session-description and broadcast messages that any session may be a
  candidate recipient for.
- **Filenames carry no routing.** `<sortable-id>` is a lexicographically-sortable
  timestamp+random id (e.g. `20260620T231500Z-a1b2`); `<slug>` is a short kebab-case of
  the subject for human scanning only. All routing and state live in frontmatter.
- **Archive is inside `cc-messages`, partitioned identically**, by `YYYY-MM` of the time
  of archival. Archiving is a **move, never a delete**.

## 6. Message file format

A message is a markdown file: YAML frontmatter, then the body.

```markdown
---
id: 20260620T231500Z-a1b2          # == sortable-id; stable primary key
schema: 1                           # frontmatter schema version
from_project: oneshot               # sender's project/repo/path label
from_session: 20260615-oneshot-inter-session-message-skill
from_uuid: 8dbed047-...             # sender's stable session uuid
to_kind: session | project | description
to_value: <uuid | project-name | free-text description>
to_location: projects/oneshot       # which store partition this file lives in
subject: Short human subject
sent_at: 2026-06-20T23:15:00Z       # ISO-8601 UTC
status: sent | read | claimed | archived
read_at: null                       # ISO-8601 UTC when first surfaced/claimed
read_by_uuid: null                  # uuid of the session that read/claimed it
read_by_session: null               # display tag at read time
claimed_at: null                    # description-addressed only
receipt_shown: false                # has the sender seen the read-receipt yet
thread: null                        # id of the message this replies to, or null
attachments:                        # reference-only; absolute paths
  - /abs/path/to/file.md
---

Free-form markdown body. The message contents.
```

- `status` lifecycle: `sent` → (`read` | `claimed`) → `archived`.
- For `to_kind: session` and `to_kind: project`, surfacing the message to the addressed
  recipient auto-flips `sent` → `read` and stamps `read_at`/`read_by_*` (§7).
- For `to_kind: description`, the message is surfaced to *candidates* and only flips to
  `claimed` when one candidate claims it (§7.2). Auto-read cannot apply because many
  candidates may see it.

## 7. Read model, receipts, and claims

### 7.1 Auto-read (session- and project-addressed)

When `ccmsg deliver` surfaces a session- or project-addressed message to its intended
recipient for the first time, it atomically flips `status: read`, stamps `read_at`,
`read_by_uuid`, `read_by_session`. "First surface" is detected via the per-session cursor
(§8) so the same session is not re-charged for a message it has already seen, and the flip
happens exactly once.

### 7.2 Claim model (description-addressed) — propose → confirm, first-claim-wins

Description-addressed messages live in `_global/inbox/` and are surfaced to *any* session
whose context plausibly matches the `to_value` description. The surfacing is advisory:

1. The hook injects the message as a **proposal**: "A message addressed to '<description>'
   is unclaimed; if this session is the right place, claim it."
2. Claude, per the `send-session-message` skill guidance, **proposes to the user and the
   user confirms** before claiming (matches Chris's chosen claim model).
3. On confirmation, `ccmsg claim <id>` performs a **first-claim-wins atomic lock** (§11):
   the first session to claim flips `status: claimed`, stamps `claimed_at` and
   `read_by_*`; later claimants get a clear "already claimed by <session>" result and do
   nothing.

Claim implies read.

### 7.3 Read receipts (back to sender)

The sender learns its message was read without polling. On the sender's next
`ccmsg deliver` sweep, any message it sent whose `status` is now `read`/`claimed` and whose
`receipt_shown` is `false` is surfaced as a one-line receipt ("✓ read by <session> at
<time>") and `receipt_shown` flips to `true` so the receipt shows exactly once.

## 8. Delivery (no prompting required)

Two hooks, both thin wrappers over `ccmsg deliver`, both emitting
`hookSpecificOutput.additionalContext`:

- **`SessionStart` hook** — initial sweep. On session start/resume/compact, delivers all
  unseen messages addressed to this session/project/location, plus unclaimed
  description-proposals matching the location, plus any pending read-receipts for messages
  this session sent.
- **`UserPromptSubmit` hook** — incremental sweep. On each user prompt, delivers only what
  is **new since the last sweep**, using the per-session cursor. This is what makes
  mid-session delivery work: a message that arrives while the session is alive is picked up
  on the next prompt.

**Cursor:** `.cursors/<session-uuid>.json` records the high-water mark (last delivered
`id` per partition, or a last-swept timestamp) so each sweep is O(new messages), not
O(store). The cursor is keyed on the **session uuid**, so it survives renames.

Both hooks register through `config/hooks-bundle.json` and `merge_hook_settings`, matching
the existing dedup-by (event + matcher + command) install convention. Hook verbs are added
to the `HOOK_VERBS` dispatcher (`ccmsg deliver` is invoked as a normal CLI, or via a
`ccst hooks run` verb — decided in the plan; the existing hooks call dedicated
`cccs_hooks` modules, so a `messaging_deliver` module under `src/cccs_hooks/` that shells
to the delivery logic in `lib/` is the convention-matching choice).

## 9. Session identity and rename/move safety

- **Identity is the session uuid** (from the hook stdin `session_id`), never the display
  tag. The tag is a human label only.
- Frontmatter stores both `from_uuid` (stable) and `from_session` (display). Delivery and
  cursors key on uuid.
- The **`move-session` skill** gains a step: when a session is renamed, refresh the
  display tag (`from_session` / `read_by_session`) in any pending messages that reference
  its uuid (cosmetic; uuid routing already works). When a session *moves project*, move
  its cursor and re-evaluate which location partition its future deliveries read from.
  uuid-keying means no message is ever orphaned by a rename.

## 10. Token efficiency

The model never reads the raw store. `ccmsg`:

- `ccmsg deliver` emits a **compact digest** (one line per message: id, from, subject,
  age) into `additionalContext`. Bodies are fetched on demand with `ccmsg read <id>`.
- All filtering (partition selection, cursor diff, address matching) happens in Python.
- Frontmatter is parsed without loading bodies for the digest pass.

## 11. Concurrency and atomicity

State transitions (`read`, `claim`, `archive`) and the first-claim-wins lock require real
atomicity — they cannot be model-edited file rewrites.

- **Claim lock:** atomic create of a sidecar lock (`O_CREAT | O_EXCL`) keyed on the
  message id; the winner rewrites frontmatter then releases. Loser sees `EEXIST` →
  "already claimed". (Final primitive — `O_EXCL` sidecar vs `fcntl` advisory lock —
  chosen in the plan; both are viable on the WSL2/Linux target.)
- **Frontmatter writes** use the existing `write_json_atomic`-style `.tmp`-swap pattern
  already in `hooks_install.py`, generalised for the message files.
- Two sessions delivering concurrently is safe: auto-read flips are idempotent and guarded
  by the same atomic write.

## 12. Retention

- Read/claimed messages older than **14 days** are archived (moved to
  `archive/YYYY-MM/`), never deleted.
- **Unread messages never expire** and never archive automatically.
- Archival runs opportunistically inside `ccmsg deliver` (bounded work per sweep) — no
  separate scheduled job, keeping this design free of the deferred cron dependency.

## 13. `ccmsg` CLI surface

New entry point in `pyproject.toml`:

```toml
ccmsg = "cc_session_tools.cli.ccmsg:main"
```

Logic lives in `src/cc_session_tools/lib/messaging/` (store paths, frontmatter parse/write,
address matching, cursor, locking, retention). The CLI module is a thin argparse layer,
matching `ccd.py` conventions (`_build_parser()`, `main(argv=None) -> int`, `--version`).

Subcommands (noun-light, verb-style, matching CCST's argparse idiom):

| Command | Purpose |
|---------|---------|
| `ccmsg send` | Compose+route a message. Flags: `--to-session` / `--to-project` / `--to-description` (mutually exclusive, exactly one required), `--subject`, `--body` / `--body-file`, `--attach PATH` (repeatable), `--thread ID`. Rejects empty/no-target sends at the boundary. |
| `ccmsg deliver` | Hook entry: sweep + digest + auto-read + receipts + opportunistic archive. Reads session context from stdin JSON (or flags for testing). Emits `additionalContext`. |
| `ccmsg read <id>` | Print one message body (+ metadata). |
| `ccmsg list` | Filtered list (by status/location/from), compact. |
| `ccmsg claim <id>` | First-claim-wins claim of a description-addressed message. |
| `ccmsg archive <id>` | Manual archive (move). |

Validation lives at the CLI/schema boundary (per coding standards): exactly one
recipient kind, non-empty subject+body, attachment paths must be absolute. Internals trust
validated input.

## 14. `send-session-message` skill

A new skill directory `skills/send-session-message/` (SKILL.md + optional scripts),
deployed by `ccst skills install` (symlink). It guides Claude to:

- Recognise when a cross-session message is warranted (proactive trigger).
- **Clarify and confirm the recipient** when ambiguous — choosing between session /
  project / description addressing, and confirming with the user before sending.
- Compose to the writing-style rules, attach by absolute path, and call `ccmsg send`.
- For description-addressed proposals arriving via delivery: propose to the user and, on
  confirmation, `ccmsg claim`.

## 15. Global CLAUDE.md integration — `ccst claude-md install`

Proactive-send *behaviour* must be discoverable to every session, so a minimal instruction
block belongs in the global `~/.claude/CLAUDE.md`. Reactive *delivery* needs nothing in
CLAUDE.md (it rides on hook-injected `additionalContext`).

`ccst claude-md` does not exist today and is **built new**, mirroring the proven
sentinel-managed-block pattern already in `shell_install.py` (which manages a block in
`~/.bashrc`/`~/.zshrc`):

- `ccst claude-md install [--target PATH] [--apply]` inserts/updates a delimited block:

  ```
  <!-- CCST:messaging START -->
  ... minimal proactive-messaging instructions ...
  <!-- CCST:messaging END -->
  ```

- Idempotent: re-running updates the block in place; never duplicates; dry-run by default,
  `--apply` writes atomically.
- `ccst claude-md uninstall [--apply]` removes the block.
- Added as step 4.5 / folded into `install-everything.sh` so existing users get it on
  `--upgrade`.

## 16. CCST packaging, installer, docs, upgrade

- **pyproject.toml:** add `ccmsg` script; add `messaging` test paths to
  `[tool.pytest.ini_options].testpaths` if skill-local tests are used.
- **install-everything.sh:** no new top-level step needed for the store (lazy-created).
  Add the `ccst claude-md install --apply` invocation. Steps 1–3 already pick up the new
  CLI, skill, and hooks idempotently; `--upgrade` re-runs them, so **existing users get the
  whole capability by re-running the installer with `--upgrade`**.
- **README.md:** new subsections under the existing structure — a "Bundled skills" entry
  for `send-session-message`, a "Hook library" note for the two delivery hooks, a
  "Hook management CLI (ccst)" entry for `claude-md`, and a new top-level
  "## Inter-session messaging" section describing the store, `ccmsg`, and the no-prompt
  delivery model.
- **CHANGELOG.md:** new `### Added` entries under `[Unreleased]` for `ccmsg`, the two
  hooks, the skill, and `ccst claude-md`.

## 17. Error handling

- Empty/targetless sends rejected at the CLI boundary with a structured non-zero exit.
- Missing/locked message ids return a clear, structured error; no silent success.
- Hook delivery failures must never block a session: `ccmsg deliver` failures degrade to
  an empty `additionalContext` (the session proceeds) but log to the CCST telemetry
  channel — never `except: pass`.
- Attachment paths are stored verbatim; a missing target is the user's concern, surfaced
  (not validated away) at read time.

## 18. Testing strategy

Matches the existing pytest+subprocess+`tmp_path` convention; never touches real
`~/.claude/`.

- **`ccmsg` unit tests** (import `lib/messaging` functions): address matching, cursor diff,
  frontmatter round-trip, retention boundary (13 vs 15 days), atomic claim race
  (two concurrent claims → exactly one winner).
- **`ccmsg` CLI tests** (subprocess): each subcommand happy path + each validation branch
  (no recipient, two recipients, empty body, relative attachment path).
- **Delivery hook tests:** feed synthetic stdin JSON, assert the emitted `additionalContext`
  digest and the resulting status flips; assert auto-read fires once; assert receipts show
  once.
- **`ccst claude-md` tests:** install/idempotent-reinstall/uninstall block management on a
  `tmp_path` CLAUDE.md.
- **move-session tests:** extend existing tests for tag-refresh and project-move cursor
  handling.
- Every `try/except` in a handler gets a failure-path test (coding standard).

## 19. Open implementation details (resolved during writing-plans, no user input needed)

1. Whether `deliver` is `ccmsg deliver` called directly from the bundle, or a
   `cccs_hooks.messaging_deliver` module behind `ccst hooks run` (lean to the latter for
   consistency with existing hooks).
2. Claim-lock primitive: `O_EXCL` sidecar vs `fcntl`.
3. Cursor representation: per-partition last-id vs single last-swept timestamp.
4. Exact `additionalContext` digest wording/format.
5. `other-paths` slug derivation from absolute path.

## 20. Deferred / spun out

- **Telegram push delivery** and any **cron global digest** → handled by the separate
  `scheduled-tasks-catchup` design, which must first solve reliable scheduled execution
  with missed-run back-fill on a frequently-off laptop. Messaging is a consumer of that
  substrate once it exists; nothing in this design depends on it.
