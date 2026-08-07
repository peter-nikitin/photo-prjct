# Reviewer dispatch

Role: reviewer
Task brief: `<task brief path>`
Implementer report: `<report path>`
Review package: `<working-tree review package path>`
Risk class: `<low | normal | high: explanation>`
Global constraints: `<task-binding specification constraints>`

Review the supplied package against the brief and constraints. Do not modify files or Git state and
do not spawn another agent. Do not repeat checks already evidenced in the implementer report unless
the evidence is incomplete.

Classify every finding as `blocking` or `future` using `AGENTS.md`. Give the exact file/location,
contract at risk, and smallest acceptable correction. End with separate spec-compliance and code-
quality verdicts; approval requires both.
