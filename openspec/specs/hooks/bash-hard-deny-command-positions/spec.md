# hooks/bash-hard-deny-command-positions Specification

## Purpose
Defines where the `bash-hard-deny` hook looks for delete commands: only in positions the shell would execute as a command, so text that merely mentions a delete verb is not blocked while nested and wrapped commands are still caught.

## Requirements

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
The hook SHALL recognise a delete command at every position the shell executes: after `;`, `&&`, `||`, `|`, `&` and newlines; inside `( ... )` subshells, `$( ... )` and backtick substitutions and process substitutions; inside the string passed to `bash -c`, `sh -c`, `zsh -c` and similar shells, and to `eval`; after `env`, `command`, `builtin`, `exec`, `nohup`, `time`, `nice`, `timeout`, `stdbuf`, `setsid` and `busybox`; as the command run by `xargs` or by `find -exec`; in a `<<<` here-string fed to a shell or to `python`/`node`; and when written as `\rm`, a quoted `"rm"`, an ANSI-C quoted `$'rm'`, an absolute path such as `/bin/rm`, in any letter case (`RM`), or glued to an expansion (`rm${IFS}x`). A shell that reads its commands from a pipe (`... | bash`, `sh -s`) SHALL be treated as running a delete whenever the command text mentions a delete verb. When the command cannot be parsed (for example an unterminated quote, an unterminated heredoc, or nesting too deep to analyse), it SHALL be treated as opaque (see below). `<<` inside arithmetic SHALL NOT start a heredoc, and a heredoc delimiter SHALL be matched literally, without expansion.

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

### Requirement: Opaque commands block on any delete mention
When a command runs code the hook cannot read - a command word that is dynamic, a glob or a brace expansion; a dynamic `-c`, `eval` or inline-script argument; a shell reading its commands from stdin, a redirect, `-` or `/dev/stdin`; `source`/`.`, `trap`, `ssh`, `su`, `watch`, `script`, `flock`, `parallel`, `awk`, `perl`, `make`, `let`, `[[`; arithmetic; a function definition; a heredoc written as data in the same command as something that runs code; or text the parser cannot read - the hook SHALL NOT apply the scratchpad allowance and SHALL block the command whenever its text, raw or quote-decoded, mentions a delete verb, a `find` delete action, `xargs` or an inline-script delete call.

#### Scenario: Heredoc fed to a shell through a substitution
- **WHEN** the command is `bash -c "$(cat <<'EOF'` followed by a line `rm -rf /home/alice/project`, `EOF` and `)"`
- **THEN** the command is blocked

#### Scenario: Script written by a heredoc and then run
- **WHEN** the command writes `rm -rf /home/alice/project` into `s.sh` with `cat <<'EOF' > s.sh` and then runs `bash s.sh`
- **THEN** the command is blocked

#### Scenario: Strings run later or elsewhere
- **WHEN** the command is `trap 'rm -rf /home/alice/project' EXIT`, `ssh host 'rm -rf x'` or `X='rm x'; eval "$X"`
- **THEN** the command is blocked

#### Scenario: Opaque command without a delete
- **WHEN** the command is `echo $((1<<2))`
- **THEN** the command is allowed

### Requirement: Analysis errors block
Any error while the hook analyses a command SHALL block the command with a message saying it could not be analysed, because a hook that crashes is treated as non-blocking and the command would run unchecked.

#### Scenario: Unrepresentable escape
- **WHEN** the command is `rm -rf /home/alice/project $'\UFFFFFFFF'`
- **THEN** the command is blocked
