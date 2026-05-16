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

### After merging a PR

Reinstall the global tool from the canonical source:

```sh
uv tool install --reinstall ~/repos/claude-code-session-tools
```

`--reinstall` is required: without it, uv detects the version number is unchanged
and silently skips the build, leaving the old binary in place.

This rebuilds the wheel from the current `main` and updates all four shims
(ccd, ccr, ccs, claude-code-usage).

## Version policy

- **Minor bump** (0.x.0): changes to install interface, CLI flags, or configuration
  contract
- **Patch bump** (0.0.x): bug fixes with no interface change

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
