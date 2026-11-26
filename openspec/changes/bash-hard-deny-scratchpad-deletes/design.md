## Context

`src/hooks/bash_hard_deny.py` checks a Bash command with regexes over the raw text. Check 1 blocks `rm|rmdir|unlink|shred` at a line start or after `;`/`&`/`|`; check 2 blocks `os.remove`-style calls when `python`/`node ... -c/-e` appears anywhere in the text; check 3 splits on separators and blocks a `mv` command token whose destination is tmp-like. `main()` reads only `tool_name` and `tool_input.command` from the payload.

The PreToolUse payload Claude Code sends also carries `session_id`, `transcript_path` and `cwd` (other hooks in this repo already read them). Claude Code places each session's scratchpad at `<realpath(CLAUDE_CODE_TMPDIR or /tmp)>/claude-<uid>/<encoded project dir>/<session id>/scratchpad`. The encoded project dir uses the same encoder as `~/.claude/projects/` (every non-alphanumeric character becomes `-`, with a hash suffix past 200 characters). A subagent's scratchpad uses its parent session's id, which is also the `session_id` its hook payloads carry.

What the old regexes did not catch: `bash -c "rm x"` (the `rm` follows a quote), `xargs rm`, `find -delete`, `find -exec rm`, `$'\x72m'`, and a `mv` nested inside `bash -c`. `/private/tmp` (where `/tmp` resolves on macOS) was not tmp-like for the move check.

## Goals / Non-Goals

**Goals:**
- Allow deletes whose every target is provably inside the calling session's own scratchpad, failing closed everywhere else.
- Stop blocking text that merely mentions a delete verb, without opening a bypass through nested or wrapped commands.

**Non-Goals:**
- Allowing scratchpad deletes inside script files or interpreter heredoc bodies: those checks keep blocking every delete, as today.
- Other checks (gh, curl, sudo, telemetry reads, git branch/push) keep their text-based matching.
- Other destructive forms not currently covered (`git clean`, `truncate`, `> file`, `mv` that overwrites, `rsync --delete`, dynamic command names such as `$CMD x`, a delete string piped to a bare `sh`) stay out of scope, as before.

## Decisions

### Derive the scratchpad from `transcript_path`, not by re-encoding the cwd
The project-dir component is taken from the name of the directory holding `transcript_path`, because Claude Code wrote that directory with the same encoder it uses for the scratchpad, including the truncation hash for long paths. Re-encoding `cwd` would need that hash function and would also be wrong after a `cd` (the payload `cwd` is the shell's current directory, not the session's project dir). The uid comes from `os.getuid()` and the temp root from `CLAUDE_CODE_TMPDIR` or `/tmp`, resolved with `realpath`. Without a valid `session_id` (no path separators, not empty) and a `transcript_path`, there is no scratchpad and no delete is allowed. This pins deletes to the calling session: another session's scratchpad differs in the session-id component.

The short scratchpad form some desktop and IDE hosts use (`<root>/s/<hash>/scratchpad`) is not derived; deletes there stay blocked (fail closed).

Alternative considered: match any path shaped like `claude-<uid>/*/<uuid>/scratchpad`. Rejected because it lets one session delete another session's scratchpad.

### A small shell parser instead of a text sanitiser
A sanitiser (blank out quoted strings, heredoc bodies and comments, then regex the rest) fixes the false positives but cannot look into `bash -c "..."` strings, which it blanks. The hook therefore parses the command into simple commands with a hand-written lexer that handles single, double and ANSI-C quotes, backslashes, comments, redirections (their targets are not arguments), heredoc bodies (skipped as data), `$(...)`, backticks, `<(...)`, `${...}`, `$((...))`, subshell parentheses and all separators. Substitution bodies are parsed recursively. Each word records its unquoted value, its raw text, whether it contains an expansion (dynamic) and which characters are unquoted glob metacharacters.

Command words are then unwrapped: leading assignments and keywords (`!`, `{`, `then`, `do`, ...) are skipped; `for`/`case`/`select` headers are not commands; wrappers (`env` with its option arguments and `-S` string, `command`, `builtin`, `exec`, `nohup`, `time`, `nice`, `timeout`, `stdbuf`, `setsid`, `busybox`, `doas`) are skipped to the real command; `bash`/`sh`/`zsh`/`dash`/`ksh -c STRING` and `eval ARGS` are parsed recursively; the command name is compared by basename so `/bin/rm` and `\rm` match. A command word that is dynamic is not treated as a delete (as before).

Names are compared case-insensitively (a case-insensitive filesystem runs `RM` as `rm`); a command word glued to an expansion (`rm${IFS}-rf${IFS}/x`) whose literal fragment is a delete verb counts as an unresolvable delete; a `<<<` here-string fed to a shell or to `python`/`node` is scanned like a `-c` string; and a shell reading commands from a pipe (`... | bash`, `sh -s`), whose input the hook cannot see, blocks whenever the command text mentions a delete verb. A pure `$CMD x` command name stays unknowable, as before.

If the lexer fails (unterminated quote or substitution, or nesting deep enough to exhaust recursion), the hook fails closed: any mention of a delete verb blocks. A crash would make Claude Code treat the hook as a non-blocking error and run the command, so the parse must not raise anything but `ShellParseError`.

Alternative considered: `shlex`. Rejected: it does not split on operators inside words, does not handle heredocs, substitutions or ANSI-C quoting.

### Target resolution
For each delete target (non-option word, honouring `--`):
- dynamic, `~`-prefixed, or containing an unquoted `{` (brace expansion, except the `find` `{}` placeholder) -> unresolvable -> block;
- relative -> joined to the payload `cwd`, but only when no `cd`/`pushd`/`popd`/`env -C` appears anywhere in the command (tracking directory changes across `&&`, `||` and subshells correctly is error-prone; absolute paths are the norm because the scratchpad path is given to the session);
- final component `.` or `..` -> block;
- the parent directory must exist now and `realpath(parent)` must be inside or equal to `realpath(scratchpad)`; `realpath` resolves `..` after symlinks, matching the kernel;
- an existing target must have `realpath(target)` strictly inside the scratchpad, which covers a symlink that would be followed (`link/`, `shred`) and conservatively also one that would only be unlinked;
- a target that does not exist yet is allowed only for `rm`/`rmdir`/`unlink` without a trailing slash, which never follow a final symlink; so a symlink the same command creates later cannot redirect the delete;
- glob: the static prefix (components before the first one holding an unquoted metacharacter) must resolve inside or equal to the scratchpad, no `..` may follow, and every current match (`glob.glob`, which like bash skips dotfiles) must pass the non-glob check.

Further refusals close specific escapes: `shred` of a file with more than one hard link (it would destroy content shared with a file outside), `rmdir -p` (it walks up past the scratchpad), relative targets under `find -execdir`/`-okdir` (they run in each match's directory), and relative literals in inline scripts (the script could `chdir` first).

Because the checks run before the command, a command could first replace an existing directory under the scratchpad with a symlink pointing outside (`mv`, `ln`) and then delete through it. A command containing an allowed scratchpad delete therefore also blocks when it runs `ln`, `link`, `mv`, `cp`, `rsync`, `tar`, `unzip`, `ditto`, `install`, `cpio` or `git` anywhere, and an inline script delete is not allowed when the script text mentions `symlink`, `link(`, `rename`, `replace(`, `move(` or `copytree`.

`find`: start paths are the words before the first expression word; `-L`, `-H` and `-follow` block. A `-delete` action, or an `-exec`/`-execdir`/`-ok`/`-okdir` whose command is a delete verb, requires every start path (default `.`) to pass the target check; other words of that delete must pass on their own, and `{}` is accepted because it names a match under a checked start path. Any other `-exec` command is analysed as a normal command. `xargs` followed by a delete verb always blocks (targets come from stdin).

Inline `python`/`node`: within a parsed simple command, the words after a `python*`/`node` word form the script text. Each delete call found there is accepted only if it is the tempfile self-cleanup idiom or its first argument is a plain string literal that passes the target check; anything else blocks.

### Delete-by-move
The text-based `mv` check is kept (it also serves script files and heredoc bodies) and the same per-command check now also runs on every parsed simple command, so a `mv` nested in `bash -c` is caught. `/private/tmp` and `/private/var/tmp` join the tmp-like bases, so moving an outside file into the scratchpad blocks. Moving within or out of the scratchpad is unchanged (source already tmp-like).

### Block message
`_DELETION_GUIDANCE` becomes a function of the scratchpad: the deletion-list sentence is unchanged and a second sentence names the scratchpad path (or the pattern `<CLAUDE_CODE_TMPDIR or /tmp>/claude-<uid>/<encoded project dir>/<session id>/scratchpad`) as the place where plain deletes with absolute, literal paths are allowed.

## Risks / Trade-offs

- [Parser gaps open a bypass] -> fail closed on parse errors; tests for every separator, substitution, wrapper and escape form; the code review is asked to hunt for bypasses.
- [Newly blocked `find -delete`, `xargs rm`, `grm`] -> deliberate tightening, called out in the changelog; the scratchpad allowance covers the common cleanup case.
- [Time-of-check vs time-of-use: a symlink created after the hook runs] -> parents must exist now, a not-yet-existing target is only allowed for verbs that do not follow a final symlink, and link/move/copy commands in the same command disable the allowance. Residual: an arbitrary program or script run earlier in the same command could still swap a scratchpad directory for a symlink; that needs deliberately adversarial command text and is accepted.
- [Payload fields missing in some host] -> no scratchpad, so deletes block as before.
