# Re-review dispatch

Role: re-reviewer
Review package: `<scoped fix review package path>`
Prior review: `<prior review path>`
Fix report: `<implementer fix report path>`
Risk class: `<low | normal | high>`

Re-review only the prior blocking findings and regressions introduced by their fixes. Do not modify
files or Git state and do not spawn another agent. For each prior finding, report `resolved` or
`open` with evidence. Classify any new finding as `blocking` or `future` under `AGENTS.md`, then give
the final scoped verdict.
