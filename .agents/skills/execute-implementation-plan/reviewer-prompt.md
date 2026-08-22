# Reviewer dispatch

Role: reviewer
Task brief: `<task brief path>`
Implementer report: `<report path>`
Review package: `<working-tree review package path>`
Risk class: `<low | normal | high: explanation>`
Global constraints: `<task-binding specification constraints>`

Review the supplied package against the brief and constraints. Do not modify files or Git state and
do not spawn another agent. Treat exact successful commands run after the last task-file change as
reusable evidence. Run a check only when that evidence is incomplete, the package does not match the
reported change set, or a concrete review hypothesis needs a different check.

Classify every finding as `blocking` or `future` using `AGENTS.md`. Give the exact file/location,
contract at risk, and smallest acceptable correction. End with separate spec-compliance and code-
quality verdicts; approval requires both.
