## Context

See proposal.md. The errors fall into: missing pandas stubs (6), `except ... as e` name reused as a
loop variable (3), `str | None` flowing into `str` parameters in `ccs.py` (8), `dict[str, int]`
passed where `dict[str, float]` is expected (4), and four one-offs (`pd` used in an annotation
without an import, `Any | None` assigned to `str`, an unannotated list, an `object` indexed).

## Goals / Non-Goals

**Goals:** `uv run mypy src` exits 0; zero behaviour change; existing tests still pass.

**Non-Goals:** CI wiring for mypy; enabling `--strict`; touching tests' typing.

## Decisions

1. **Fix, don't suppress.** Prefer narrowing/annotation over `# type: ignore` or per-module
   `ignore_errors`. A `type: ignore` is allowed only where the type system cannot express a correct
   runtime-checked fact, with a reason.
2. **`pandas-stubs` over `ignore_missing_imports`** for pandas, so pandas use is actually checked.
3. **Real bugs get a test.** If narrowing exposes a genuine latent bug (e.g. a `None` reaching a
   function that cannot take it), fix it and add a regression test rather than silencing it.
4. **Patch bump** (3.11.1): no interface change.

## Risks / Trade-offs

- [Stubs may surface further errors] -> fix them in this change; scope stays "mypy exits 0".
- [Narrowing changes behaviour on a `None` path] -> tests plus review of each `ccs.py` site.
