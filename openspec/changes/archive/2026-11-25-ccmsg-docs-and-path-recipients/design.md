## Context

Status transitions, as implemented in `lib/messaging/repository.py` and `service.py`:

- `sent` on insert. `service.deliver` flips session- and project-addressed messages to `read` when a digest shows them (`mark_read`). Description-addressed messages are shown as proposals and stay `sent`. `ccmsg read` only fetches the row; it never changes status.
- `repository.claim` accepts `sent` or `read` and sets `claimed` inside `BEGIN IMMEDIATE`, so first claim wins. One row per message, so a claimed project message is gone for every session of the project.
- `repository.archive_one` archives a row in any status. `retention.archive_old` (run on every delivery sweep) archives `claimed` rows whose `claimed_at` is 14 or more days old; nothing else is archived automatically.
- `repository.unarchive_one` restores only archived rows with no `claimed_at`: to `read` when `read_at` is set, else `sent`.

The requester's draft said `ccmsg read` moves a message to `read`; the code contradicts that, so the help text says the opposite.

Sender-side project resolution: `store.partition_for_cwd(cwd)` maps a cwd whose parent is a configured root to `projects/<name>` (strict root) or `repos/<name>` (loose root); the project label is the part after the slash.

## Decisions

- **Status text lives in one constant** (`STATUS_HELP` in `cli/ccmsg.py`) used as the epilog of the top-level and `list` parsers with `RawDescriptionHelpFormatter`. It is CLI documentation, so it stays in the CLI module rather than the library.
- **Path detection by prefix.** A value starting with `/` or `~` is a path; anything else is a name. Project names never contain `/`, so there is no ambiguity, and a relative path is not accepted (it would depend on the sender's cwd).
- **Reuse the sender resolution.** New `store.project_dir_for_path(path)` walks the resolved path and its ancestors and returns the first that `roots.matched_session_root` accepts and that is a directory. The CLI then stores `to_value = dir.name` and, unless `--to-partition` overrides it, `to_partition = partition_for_cwd(dir)`, the exact function a session started in that folder uses. Using the resolved folder directly (not `partition_for_project(name)`) also picks the right root when the same name exists under both roots.
- **Error, not fallback.** An unresolvable path is a mistake by the sender, so it is rejected at the boundary (exit 2, like other `send` validation errors) rather than routed to `_global` as an unknown name is.
- **Body-file guidance is text only.** `--body-file` already exists; the change adds help text and skill wording. Hooks are untouched.

## Risks

- A path under a root whose project folder does not exist yet is rejected. Acceptable: there is no session there to receive it.
