# Implementer dispatch

Role: implementer
Worktree: `<absolute path>`
Task brief: `<task brief path>`
Report: `<report path>`
Model reason: `<mechanical | integration | exceptional-risk: explanation>`
Prior interfaces: `<paths and exact interfaces, or none>`
Resolved ambiguities: `<task-local decisions, or none>`

Read the task brief first; it is the requirements source. Work only in the named worktree. Perform
the task yourself without spawning or delegating. Follow strict red-green TDD and run the focused
checks from the brief. Do not stage, commit, amend, push, merge, switch branches, or modify Git
history/remotes.

Write the full report to the specified path with changed files, RED and GREEN evidence, final
checks, self-review, and concerns. Return only one status (`DONE`, `DONE_WITH_CONCERNS`,
`NEEDS_CONTEXT`, or `BLOCKED`), a one-line test summary, and the report path.
