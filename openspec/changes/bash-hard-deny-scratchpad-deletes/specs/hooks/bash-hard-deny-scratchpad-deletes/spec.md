## Purpose

Defines which file deletions the `bash-hard-deny` PreToolUse hook allows: only deletes whose every target lies strictly inside the calling session's own Claude Code scratchpad directory, with every other delete blocked and pointed at the deletion-list workflow.

## ADDED Requirements

### Requirement: Session scratchpad is derived from the hook payload
The hook SHALL derive the calling session's scratchpad directory as `<temp root>/claude-<uid>/<encoded project dir>/<session id>/scratchpad`, where the temp root is the resolved `CLAUDE_CODE_TMPDIR` (or `/tmp` when unset), the encoded project directory is the name of the directory that holds the payload's `transcript_path`, and the session id is the payload's `session_id`. When `session_id` or `transcript_path` is missing or malformed, the hook SHALL treat the session as having no scratchpad and SHALL allow no delete.

#### Scenario: Scratchpad derived from session id and transcript path
- **WHEN** the payload has `session_id` `11111111-2222-3333-4444-555555555555` and `transcript_path` `/home/alice/.claude/projects/-home-alice-repos-proj/11111111-2222-3333-4444-555555555555.jsonl`, the uid is 1000 and `CLAUDE_CODE_TMPDIR` is unset
- **THEN** the scratchpad is the resolved form of `/tmp/claude-1000/-home-alice-repos-proj/11111111-2222-3333-4444-555555555555/scratchpad`

#### Scenario: Missing session id allows no delete
- **WHEN** the payload carries no `session_id` and the command is `rm` of a path under some session's scratchpad
- **THEN** the command is blocked

### Requirement: Deletes are allowed only when every target is inside this session's scratchpad
The hook SHALL allow a delete command (`rm`, `rmdir`, `unlink`, `shred` and their `g`-prefixed names, `find` with `-delete` or an `-exec`/`-execdir`/`-ok`/`-okdir` delete, and inline `python`/`node` delete calls) only when every target resolves strictly inside this session's scratchpad. If any target of any delete in the command is outside it, the whole command SHALL be blocked. The scratchpad directory itself SHALL NOT be deletable; its contents SHALL be.

#### Scenario: File in the scratchpad
- **WHEN** the command is `rm -f <scratchpad>/out.txt`
- **THEN** the command is allowed

#### Scenario: Nested directory in the scratchpad
- **WHEN** the command is `rm -rf <scratchpad>/build/tmp`
- **THEN** the command is allowed

#### Scenario: Scratchpad root itself
- **WHEN** the command is `rm -rf <scratchpad>`
- **THEN** the command is blocked

#### Scenario: Session working and out folders
- **WHEN** the command deletes a file under the session's `cc-sessions/<tag>/working/` or `out/` folder
- **THEN** the command is blocked

#### Scenario: Another session's scratchpad
- **WHEN** the command deletes a file under a scratchpad whose session id differs from the payload's
- **THEN** the command is blocked

#### Scenario: Mixed targets
- **WHEN** the command is `rm <scratchpad>/a.txt /home/alice/notes.txt`
- **THEN** the command is blocked

#### Scenario: Relative target resolved against the payload cwd
- **WHEN** the payload `cwd` is the scratchpad, the command is `rm a.txt` and no `cd`, `pushd`, `popd` or `env -C` appears in the command
- **THEN** the command is allowed

#### Scenario: Relative target after a directory change
- **WHEN** the command is `cd <scratchpad> && rm a.txt`
- **THEN** the command is blocked

#### Scenario: Inline python delete inside the scratchpad
- **WHEN** the command is `python3 -c "import os; os.remove('<scratchpad>/a.txt')"`
- **THEN** the command is allowed

#### Scenario: Inline python or node delete outside the scratchpad
- **WHEN** the command is an inline `python3 -c` or `node -e` delete of a path outside the scratchpad, or of a non-literal argument
- **THEN** the command is blocked

#### Scenario: find -delete under the scratchpad
- **WHEN** the command is `find <scratchpad>/build -name '*.o' -delete`
- **THEN** the command is allowed

#### Scenario: xargs delete
- **WHEN** the command pipes paths into `xargs rm`
- **THEN** the command is blocked, because the targets are not known

### Requirement: Targets must resolve statically and safely
The hook SHALL block a delete target that it cannot resolve statically or that escapes the scratchpad: a target containing a variable, command substitution, arithmetic expansion, brace expansion or a leading `~`; a target whose `..` components or symlinks resolve outside the scratchpad (checked on both the fully resolved path and the resolved parent plus final name); a target whose last component is `.` or `..`; and a glob whose first wildcard component is at or above the scratchpad directory, or that is followed by `..`. A glob whose static prefix is inside or equal to the scratchpad SHALL be allowed only when every current match passes the same checks. Because the check runs before the command, the target's parent directory SHALL already exist, a target that does not yet exist SHALL be allowed only for `rm`, `rmdir` or `unlink` without a trailing slash, and the allowance SHALL NOT apply to a command that also runs a link, move or copy command (`ln`, `link`, `mv`, `cp`, `rsync`, `tar`, `unzip`, `ditto`, `install`, `cpio`, `git`) or to an inline script that also creates links or renames paths.

#### Scenario: Symlink created in the same command
- **WHEN** the command is `ln -s /home/alice <scratchpad>/d && rm -rf <scratchpad>/d/notes`
- **THEN** the command is blocked

#### Scenario: Dot-dot escape
- **WHEN** the command is `rm <scratchpad>/../../escape.txt`
- **THEN** the command is blocked

#### Scenario: Symlink escape
- **WHEN** `<scratchpad>/link` is a symlink to a directory outside the scratchpad and the command is `rm -rf <scratchpad>/link/`
- **THEN** the command is blocked

#### Scenario: Variable target
- **WHEN** the command is `rm -rf "$SCRATCH/x"`
- **THEN** the command is blocked

#### Scenario: Glob below the scratchpad
- **WHEN** the command is `rm -rf <scratchpad>/*`
- **THEN** the command is allowed

#### Scenario: Glob at or above the scratchpad
- **WHEN** the command is `rm -rf <session dir>/scr*`
- **THEN** the command is blocked

### Requirement: Self-cleanup of tempfile variables stays exempt
A Python `os.remove`, `os.unlink` or `shutil.rmtree` whose sole argument is a variable assigned earlier in the same script from `tempfile.mkstemp`, `tempfile.mkdtemp` or `tempfile.NamedTemporaryFile` SHALL NOT be blocked, in inline scripts, script files and heredoc bodies alike.

#### Scenario: Inline self-cleanup
- **WHEN** the command is `python3 -c "import os,tempfile; fd, p = tempfile.mkstemp(); os.remove(p)"`
- **THEN** the command is allowed

### Requirement: Deletion block messages name the scratchpad
Every deletion and delete-by-move block message SHALL keep the deletion-list guidance (compile the paths into a list and present it to the user at the end of the task) and SHALL also state that deletes whose every target is inside this session's scratchpad are allowed, naming the scratchpad path, or the path pattern when it cannot be derived.

#### Scenario: Block message with a known scratchpad
- **WHEN** a delete outside the scratchpad is blocked and the scratchpad is derivable
- **THEN** the message contains the deletion-list guidance and the scratchpad's absolute path

#### Scenario: Block message without a scratchpad
- **WHEN** a delete is blocked and the payload has no `session_id`
- **THEN** the message contains the deletion-list guidance and the scratchpad path pattern
