## Purpose

Defines where the `bash-hard-deny` hook looks for delete commands: only in positions the shell would execute as a command, so text that merely mentions a delete verb is not blocked while nested and wrapped commands are still caught.

## ADDED Requirements

### Requirement: Delete verbs are matched only in command positions
The hook SHALL treat `rm`, `rmdir`, `unlink`, `shred` (and their `g`-prefixed names) as delete commands only when they are the command word of a simple command, after skipping leading variable assignments and shell keywords. A delete verb inside a quoted argument, an `echo`/`printf` body, a commit message, a comment or a heredoc body that is not fed to an interpreter SHALL NOT cause a block.

#### Scenario: grep pattern
- **WHEN** the command is `grep -rn "; rm -rf" src/`
- **THEN** the command is allowed

#### Scenario: echo and printf bodies
- **WHEN** the command is `echo "then rm the file"` or `printf 'x | rm y\n'`
- **THEN** the command is allowed

#### Scenario: Commit message
- **WHEN** the command is `git commit -m "stop blocking rm -rf in echo"`
- **THEN** the command is allowed

#### Scenario: Heredoc used as data
- **WHEN** the command writes a heredoc whose body contains `rm -rf /x` to a file with `cat > notes.md <<'EOF'`
- **THEN** the command is allowed

### Requirement: Nested and wrapped command positions are recognised
The hook SHALL recognise a delete command at every position the shell executes: after `;`, `&&`, `||`, `|`, `&` and newlines; inside `( ... )` subshells, `$( ... )` and backtick substitutions and process substitutions; inside the string passed to `bash -c`, `sh -c`, `zsh -c` and similar shells, and to `eval`; after `env`, `command`, `builtin`, `exec`, `nohup`, `time`, `nice`, `timeout`, `stdbuf`, `setsid` and `busybox`; as the command run by `xargs` or by `find -exec`; in a `<<<` here-string fed to a shell or to `python`/`node`; and when written as `\rm`, a quoted `"rm"`, an ANSI-C quoted `$'rm'`, an absolute path such as `/bin/rm`, in any letter case (`RM`), or glued to an expansion (`rm${IFS}x`). A shell that reads its commands from a pipe (`... | bash`, `sh -s`) SHALL be treated as running a delete whenever the command text mentions a delete verb. When the command cannot be parsed (for example an unterminated quote or nesting too deep to analyse), the hook SHALL block if the text mentions a delete verb anywhere.

#### Scenario: Shell -c string
- **WHEN** the command is `bash -c "rm -rf /home/alice/project"`
- **THEN** the command is blocked

#### Scenario: Substitution and subshell
- **WHEN** the command is `echo $(rm x)`, ``echo `rm x` `` or `( rm x )`
- **THEN** the command is blocked

#### Scenario: Wrappers and escapes
- **WHEN** the command is `env FOO=1 rm x`, `command rm x`, `nohup rm x`, `timeout 5 rm x`, `\rm x` or `/bin/rm x`
- **THEN** the command is blocked

#### Scenario: Here-strings, pipes into a shell, case and expansion tricks
- **WHEN** the command is `bash <<< 'rm x'`, `echo 'rm x' | bash`, `RM x` or `rm${IFS}x`
- **THEN** the command is blocked

#### Scenario: find and xargs
- **WHEN** the command is `find . -name '*.pyc' -delete`, `find . -exec rm {} \;` or `ls | xargs rm`
- **THEN** the command is blocked

#### Scenario: Unparseable command falls back
- **WHEN** the command is `rm x "unterminated`
- **THEN** the command is blocked

### Requirement: Delete-by-move into the scratchpad stays blocked
The hook SHALL treat `/private/tmp` and `/private/var/tmp` as tmp-like destinations for the delete-by-move check and SHALL apply that check to `mv` commands in every recognised command position, so a file outside a tmp-like location cannot be moved into the scratchpad. Moving a file within the scratchpad or out of it SHALL be allowed.

#### Scenario: Move into the scratchpad
- **WHEN** the command is `mv /home/alice/notes.txt <scratchpad>/notes.txt`
- **THEN** the command is blocked

#### Scenario: Move nested in a shell string
- **WHEN** the command is `bash -c "mv notes.txt /tmp/"`
- **THEN** the command is blocked

#### Scenario: Move within the scratchpad
- **WHEN** the command is `mv <scratchpad>/a.txt <scratchpad>/b.txt`
- **THEN** the command is allowed
