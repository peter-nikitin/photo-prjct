# Re-review dispatch

Role: re-reviewer
Review package: `<scoped fix review package path>`
Prior review: `<prior review path>`
Fix report: `<implementer fix report path>`
Risk class: `<low | normal | high>`

Re-review only the prior blocking findings and regressions introduced by their fixes. Do not modify
files or Git state and do not spawn another agent. Reuse exact successful post-fix commands run
after the last task-file change when the fix package is unchanged. Run a check only for incomplete
or invalidated evidence or a concrete regression hypothesis. For each prior finding, report
`resolved` or `open` with evidence. Classify any new finding as `blocking` or `future` under
`AGENTS.md`, then give the final scoped verdict.
