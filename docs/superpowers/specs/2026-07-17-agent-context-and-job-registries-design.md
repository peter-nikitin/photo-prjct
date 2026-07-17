# Agent Context and Job Registries Design

## Goal

Keep `AGENTS.md` stable and non-contradictory by limiting it to project orientation and pointers to
authoritative knowledge. Add separate product and engineering job registries that show which
capabilities remain, which are under delivery, and which have been completed or validated.

## Scope and non-goals

This change is documentation routing and test maintenance only. It will not change architecture,
ADRs, delivery plans, application behavior, deployment topology, or domain decisions. Architecture,
plans, executable configuration, and tests are inputs and evidence for the initial registries, not
targets for redesign.

Implementation edits are limited to `AGENTS.md`, the two job registries, Markdown-focused cleanup in
repository contract tests, and removal of the redundant CI test invocation.

## AGENTS.md responsibility

`AGENTS.md` will contain only:

- a concise description of FindMe Photo;
- a map to the repository's authoritative information.

The map will point to:

- `docs/product-jobs.md` for user-facing outcomes and their status;
- `docs/engineering-jobs.md` for engineering capabilities and their status;
- `docs/architecture.md` for implemented, accepted, proposed, and deferred architecture;
- `docs/adr/` for durable decisions;
- `docs/plans/` for decision-complete delivery plans;
- `.agents/skills/` for task-specific agent procedures.

It will not list individual skills or duplicate current stage, technology stack, priorities,
deployment topology, domains, goals, validation commands, or operational procedures. Those facts
change at different rates and already have more appropriate owners.

## Product job registry

`docs/product-jobs.md` will contain only user-facing jobs for visitors, customers, photographers,
and operators. Each job will use the Jobs to Be Done form:

> When `<situation>`, I want to `<motivation>`, so I can `<expected outcome>`.

Each registry row will contain a stable `PJ-XXX` identifier, actor, short title, job statement,
status, evidence, and last-updated date. The initial rows will be derived from implemented and
proposed behavior in `docs/architecture.md`; proposed components will not be described as delivered.

## Engineering job registry

`docs/engineering-jobs.md` will describe engineering capabilities rather than individual technical
tasks. Examples include reproducible local development, automated staging delivery, controlled
production promotion, backup recovery, and operational visibility.

Each row will contain a stable `EJ-XXX` identifier, actor, short title, job statement, status,
evidence, and last-updated date. Concrete implementation steps remain in `docs/plans/` and pull
requests, preventing the registry from becoming a second task tracker.

## Shared statuses and history

Both registries will use the same status vocabulary:

- `Candidate`: recorded but not planned;
- `Planned`: covered by an approved delivery plan;
- `In progress`: currently being delivered;
- `Delivered`: implemented and available in the relevant environment;
- `Validated`: supported by automated evidence or real use;
- `Deferred`: intentionally postponed.

Each file will include an append-only status log with:

`Date | Job | Previous status | New status | Evidence or reason`

The registry row shows current state; the log preserves changes over time. Evidence should link to
architecture sections, ADRs, plans, executable configuration, tests, or deployed behavior. A status
must not be advanced only because a target is described in a proposal.

Every seeded registry row will have an initial log entry from `Not recorded` to its first assigned
status so that all current state is traceable from creation.

## Markdown test removal

Every repository contract assertion and file read whose subject is Markdown content or existence
will be removed. This includes assertions over `AGENTS.md`, architecture and planning documents, ADR
templates and indexes, project `SKILL.md` files, and Markdown inventories. Mixed tests will be split:
their Markdown reads and assertions will be removed while executable YAML, configuration, or runtime
assertions remain covered.

Executable contracts remain covered, including Django configuration, URL isolation, Compose and
deployment behavior, npm scripts, and visual-test container configuration. No Markdown linter or
replacement content test will be introduced.

The dedicated CI step that repeats `tests/test_repository_foundation.py` after the full pytest run
will be removed because the preceding full test command already discovers that file.

## Verification

This documentation and test-maintenance change will be verified with:

- `git diff --check`;
- collection and execution of the remaining repository foundation tests using `.env.example`;
- inspection that no remaining test reads Markdown solely to enforce prose or document structure;
- inspection that `AGENTS.md` contains orientation and source routing only.
