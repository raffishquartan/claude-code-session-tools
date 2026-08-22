# claude-code-session-tools — Claude Code instructions

## Development workflow

This repo uses git worktrees for feature development. The `.worktrees/` directory
is gitignored.

### Starting a feature

```sh
git worktree add .worktrees/<branch-name> -b f/<date>-<label>
cd .worktrees/<branch-name>
uv sync --extra dev
```

### Testing in a worktree

Use `uv run` — never `uv tool install`:

```sh
uv run pytest -q                                     # run all tests
uv run python -m cc_session_tools.cli.ccd --help     # test ccd manually
```

`uv run` keeps everything inside the worktree venv. Running `uv tool install`
from a worktree overwrites the global install's source pointer and will break
the CLIs when the worktree is deleted.

### `uv run ccst` and the auto-sync side effect

This applies to **any local checkout** — the base repo at `~/repos/claude-code-session-tools`
just as much as a `.worktrees/` feature branch — whenever its `pyproject.toml`
version differs from the globally `uv tool install`-installed version.

**`uv run ccst` still writes to the real `~/.claude`, and can fight the globally
installed `ccst`.** `ensure_synced()` (`src/cc_session_tools/lib/install_sync.py`)
runs before almost every `ccst` subcommand and compares the *invoking process's*
package version against a single shared, machine-wide "last synced version"
stamp in `~/.local/share/claude/sessions.db`. If the checkout you're running
`uv run` from has a different `pyproject.toml` version than the currently
installed tool (e.g. mid-release-bump on `main`, or any feature branch), that
version won't match the stamp, so an un-exempt command triggers a real
`install-everything --apply` — sourced from *that checkout's* bundled
skills/hooks/CLAUDE.md, applied to the live `~/.claude` directory, not
sandboxed to the checkout in any way. The next ordinary invocation of the
globally installed `ccst` then sees its own version mismatch against that
stamp and re-syncs back the other way, ping-ponging `~/.claude` between the
two versions. Exempt commands (`install-everything`, `doctor`, `repair`,
`migrate`, any `install`/`uninstall` verb, `hooks run`) are unaffected; any
other ad hoc `uv run` command during dev/testing — in the base repo or a
worktree — should be prefixed with `CCST_NO_AUTO_SYNC=1`:

```sh
CCST_NO_AUTO_SYNC=1 uv run python -m cc_session_tools.cli.ccst pdata schema list --project home
```

### After merging a PR

Reinstall the global tool from the canonical source:

```sh
uv tool install --reinstall ~/repos/claude-code-session-tools
```

`--reinstall` is required: without it, uv detects the version number is unchanged
and silently skips the build, leaving the old binary in place.

This rebuilds the wheel from the current `main` and updates all four shims
(ccd, ccr, ccs, claude-code-usage).

No follow-up `ccst install-everything --apply` is needed: the reinstall changes the installed
version, and the next `ccst` command notices the mismatch and syncs skills, hooks, shell
functions, `CLAUDE.md` and scheduled jobs itself. Run it by hand only to see the full five-step
output, or when `ccst doctor` FAILs `install:synced` (which means an automatic sync already tried
and failed).

## Data store conventions

Any new Chris-added data store in this repo (or moved here from elsewhere) must:

- Use SQLite (WAL mode) under `~/.local/share/claude/<subsystem>.db`, one file per subsystem —
  not flat files, not hand-rolled locks. Open every connection through the shared
  connection-setup helper (WAL pragma + busy-timeout) rather than repeating the pragma setup ad
  hoc per module.
- Ship a corresponding `ccmsg`/`ccsched`/`ccst` query subcommand for its common read
  operations before being considered done — mirrors the existing per-concern-subcommand pattern
  (`hooks`, `skills`, `doctor`, `telemetry`, `gc`, `tags`). A store nobody can query except by
  opening it with a raw `sqlite3` shell isn't finished.

Rationale and full design: `data-stores-design-spec.md` and
`ccst-migration-and-cli-update-spec.md`, in the `claude` project's session
`cc-sessions/20260712-claude-finalise-common-extra-claude-data-store-requirements/out/`.

## Version policy

Standard SemVer as of 1.0.0 (see CHANGELOG.md's 1.0.0 entry for why the 0.x scheme was retired
early — 0.19.0 shipped a breaking on-disk change as a minor bump with no migration guard, was
yanked from PyPI, and 1.0.0 replaced it):

- **Major bump** (x.0.0): breaking changes — including any change that relocates, reformats, or
  otherwise makes existing on-disk data unreadable by old code paths until a migration step runs,
  even if no CLI flag or config contract changed. If a change like this ships, it must also ship
  a `ccst doctor` check that FAILs (not WARNs) when the migration hasn't run yet — an empty/silent
  new store is not distinguishable from a fresh install without one.
- **Minor bump** (0.x.0): backwards-compatible changes — new features, new CLI flags/subcommands,
  additive configuration.
- **Patch bump** (0.0.x): bug fixes with no interface change.

## Commit style

Imperative mood, explain WHY not WHAT. One logical change per commit. Use
conventional commit prefixes: `feat:`, `fix:`, `ci:`, `docs:`, `build:`,
`refactor:`.

## Releasing

See `.github/workflows/release.yml`. Before the first release, configure a PyPI
Trusted Publisher at pypi.org → Account Settings → Publishing:

- Project name: `cc-session-tools`
- Owner: `raffishquartan`
- Repository: `claude-code-session-tools`
- Workflow filename: `release.yml`
- Environment: (leave blank)

Then tag and push:

```sh
git tag v0.6.0
git push --tags
```

Create a GitHub Release from the tag — the workflow fires automatically, builds
the wheel and sdist, attaches them to the release, and publishes to PyPI.
