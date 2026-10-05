## Why

`ccd` and `ccr` always start Claude Code on the user's subscription. Some sessions should instead
bill to an Anthropic API key (for example a work or project-specific key), and today that means
hand-exporting `ANTHROPIC_API_KEY` before launching, with the key sitting in shell history or an rc
file.

## What Changes

- Add `ccdapi` (new session) and `ccrapi` (resume session): identical to `ccd` / `ccr` except the
  launched `claude` process uses an API key from a named-key file instead of the subscription.
- Both take `-k <label>` to choose the key. Without `-k` they print a numbered menu of the labels
  in the file (the same picker `ccr` uses for ambiguous matches) and launch with the chosen one.
- API keys live in a dedicated file outside `~/.claude` that no shell rc file sources. The path
  defaults to `~/.config/ccst/api-keys` and is overridable with `CCST_API_KEYS_FILE`.
- The repo ships `api-keys.example`: comments and one commented-out example line, no real values.
  The real file's contents are managed by `claude-code-config-sync` (separate change in that repo).
- Add `ccdapi` and `ccrapi` console scripts; minor version bump.

## Capabilities

### New Capabilities
- `cli/api-key-launchers`: `ccdapi` / `ccrapi` behaviour, `-k` label selection and menu, and the
  API-keys file location, format and handling.

### Modified Capabilities

(none - `ccd` and `ccr` behaviour is unchanged)

## Impact

- New: `src/cc_session_tools/lib/api_keys.py`, `ccdapi` / `ccrapi` entry points, template file
  bundled in the package, tests.
- Touched: `ccd.py` / `ccr.py` gain a shared way to inject the key into the launch environment;
  `pyproject.toml`, `uv.lock`, `CHANGELOG.md`, README.
- Out of scope: the `claude-code-config-sync` side (populating and syncing the real file).
