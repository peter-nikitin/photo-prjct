# Future Work

This directory records concrete improvements discovered during scoped work that should not delay
the current critical path.

Create one Markdown file per coherent finding, named `YYYY-MM-DD-short-slug.md`, with:

- **Observed gap:** the behavior or limitation found.
- **Why it is non-blocking:** why it does not affect an accepted requirement or realistic current
  production path.
- **Revisit trigger:** the concrete product, architecture, operational, or incident condition that
  makes the finding relevant.
- **Likely scope:** the components and validation expected when the trigger occurs.

These artifacts are not accepted requirements or implementation plans. When a revisit trigger
occurs, reassess the finding against the current system and promote it into the appropriate
specification, ADR, plan, or issue.
