## Context

`ccusage_wrapper.py` uses `shutil.which("ccusage")`. `ccst install-everything` runs five install
steps then `ccst doctor` as a health check; `ensure_synced()` runs the same five steps (without the
health check) automatically after a CCST upgrade, before most `ccst` subcommands. The doctor's
existing `check_cli_on_path` covers CCST's own shims and FAILs when one is missing.

## Decisions

### D1. Detect and instruct; do not auto-install

Options considered:

1. **Doctor WARN + explicit skill instructions (chosen).**
2. Add an `install-everything` step that runs the bun installer and `bun add -g ccusage`.
3. Make `claude-code-usage` shell out to `bunx ccusage` when `ccusage` is absent.

Option 2 puts a network install of a runtime (`curl | bash` for bun) inside a path that runs
unattended after upgrades, for a dependency the tool already treats as optional, and it edits shell
rc files. Option 3 needs bun anyway and adds a silent multi-second network fetch per call. Option 1
makes the gap loud (doctor runs on every upgrade sync via `install-everything`) without CCST
performing installs the user did not ask for. If review prefers option 2, it would be an opt-in
`ccst install-everything --with-ccusage` flag rather than default behaviour.

### D2. WARN, not FAIL

`ccusage` is documented as optional and `claude-code-usage` degrades gracefully without it. This
repo's version policy reserves FAIL for states that break sessions or lose data.

### D3. Lookup falls back to bun's global bin directory

`bun add -g` installs into `$BUN_INSTALL/bin` (default `~/.bun/bin`). Requiring that directory on
`PATH` in every shell type is fragile (zsh: `~/.zshenv` is the only file read by non-interactive
shells). Resolving it directly in the wrapper and the doctor check removes the dependency on the
user's shell rc layout, while the skill still documents the `PATH` edit for interactive use of the
`ccusage` command itself.

### D4. Three doctor states for ccusage

`ccusage` on `PATH` and runnable: OK (reports version). Found only under bun's bin directory: WARN
with the `PATH` line to add. Not found: WARN with install command (and `deps:bun` says whether bun
is the missing piece). Kept as separate check names so `ccst doctor --mute` can silence each
independently for users who deliberately skip ccusage.

## Risks / Trade-offs

- Doctor runs `ccusage --version` (subprocess, 5s timeout, same pattern as `check_cli_on_path`),
  adding a small amount to doctor runtime.
- The bun install command in the skill is upstream's documented installer; if upstream changes it,
  the skill text goes stale. The doctor reason links the upstream docs URL rather than embedding
  more than one command.
