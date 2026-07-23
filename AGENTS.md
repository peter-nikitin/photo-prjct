# AGENTS.md

## Project

FindMe Photo is an event photo marketplace where customers discover photos from events and
photographers and operators publish and manage event photos.

## Where to find information

- [Product jobs](docs/product-jobs.md) records customer-facing jobs and their evidence-backed status.
- [Engineering jobs](docs/engineering-jobs.md) records engineering and operational capabilities and
  their evidence-backed status.
- [Architecture](docs/architecture.md) describes the system architecture and its current boundaries.
- [Architecture decision records](docs/adr/) contain durable architecture decisions.
- [Implementation plans](docs/plans/) contain decision-complete plans for multi-step work.
- [Agent skills](.agents/skills/) contain reusable project-specific guidance.

## Subagent delegation

- An implementer subagent must perform its assigned task itself and must not spawn, dispatch, or
  delegate to another agent, including a reviewer.
- An implementer's self-review means inspecting its own work without creating another agent.
- Only the root controller may dispatch an independent reviewer after the implementer has produced
  the task diff and report.
- Review fixes return to the implementer, and re-review returns to the same reviewer when available;
  do not create an additional reviewer for the same task.
- The root controller must inspect the agent tree while delegated work is active and interrupt any
  unplanned nested agent before using its result.
