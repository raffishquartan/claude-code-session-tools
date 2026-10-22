# type-check-clean Specification

## Purpose

The project's static type check is clean in a freshly synced development environment and stays clean in CI, so a real type error is never hidden among missing-stub noise.

## Requirements

### Requirement: Type check is clean in a synced development environment
`mypy src` SHALL exit 0 in an environment created by `uv sync` from the repository, with no stub package installed by hand.

#### Scenario: Fresh sync
- **WHEN** a developer runs `uv sync` and then `uv run mypy src`
- **THEN** mypy reports no errors and exits 0

### Requirement: CI enforces the type check
The CI workflow SHALL run `mypy src` and fail the build when it exits non-zero.

#### Scenario: New type error
- **WHEN** a pull request introduces a mypy error under `src`
- **THEN** the CI type-check step fails
