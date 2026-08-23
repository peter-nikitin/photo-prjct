# Testing

FindMe Photo keeps its verification interfaces small: core checks run for every pull request, and
the changed-path selector chooses the expensive layers. The executable authority is the
[Makefile](../Makefile), [suite manifest](../tests/suite-selection.toml), and
[selector](../scripts/select_test_suites.py); this page links to them rather than restating their
path rules.

## Local interfaces

| Command | Purpose |
| --- | --- |
| `make test` | Required core: unit, database, and product-flow tests. |
| `make test-operational` | Deployment, recovery, and other operational contracts. |
| `make test-migrations` | Migration-layer rehearsal contracts. |
| `make test-all` | Manual exhaustive Python interface. |
| `make static` | Ruff formatting, Ruff lint, and mypy. |
| `make check` | Static checks, core branch coverage, Django checks, and migration drift. |

Use the selector with explicit changed files or a base/head range before running expensive layers.
Core is always required. CI keeps stable jobs for every layer; an unselected optional job succeeds
with the selector's reason instead of running that layer. Unknown production, workflow, build, or
infrastructure paths fail closed to every expensive suite.

For a selected final migration run, pair the migration-layer target with
[`scripts/check_migration_immutability.py`](../scripts/check_migration_immutability.py) using the
actual base and head revisions. The target exercises the layer; the script checks the package's
historical migration identity.

## Layer ownership

Each Python test has one primary layer: `unit` for dependency-free decisions, `db` for PostgreSQL
state and transactions, `product_flow` for a critical Django path, `operational` for deployment and
recovery behavior, or `migration` for migration contracts. The manifest owns collection-time
classification; keep an invariant at one primary surface and retain higher-layer tests only for
their integration responsibility.

## Evidence handoff

Use the project [verification-selection skill](../.agents/skills/select-verification-suites/SKILL.md)
for focused RED/GREEN checks, selection, fingerprints, and report contents. Evidence is reusable
only when its whole-package fingerprint equals the final package fingerprint and it covers every
selected suite. The root controller performs the final `make check` and any selector-required
expensive layers once on that unchanged package; CI repeats verification after push.
