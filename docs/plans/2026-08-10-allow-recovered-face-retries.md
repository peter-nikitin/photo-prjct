# Allow Recovered Face Retry Evidence During Activation

- Date: 2026-08-10
- Status: Approved for execution
- Owner: project maintainer
- Parent plan: `docs/plans/2026-08-07-gallery-face-quality-gate.md`
- Execution: use `$execute-implementation-plan`

## Goal

Allow the reviewed candidate face generation to activate when transient failed, expired, or stale
attempts were preserved but every approved photo subsequently produced its required accepted
successful attempt and immutable projection.

## Global constraints

- Preserve every historical attempt and projection; do not delete or rewrite retry evidence.
- Keep the approved event, cohort, configuration, model, comparison, kept-face, and rejected-face
  hashes and counts unchanged.
- Require exactly the approved job count, accepted successful attempt count, projection count, and
  projected photo set.
- Require every candidate job to be terminal and successful, with zero nonterminal or failed jobs.
- Require zero technical face failures.
- Do not make arbitrary incomplete candidate evidence activatable.

## Task 1: Accept only fully recovered retry attempts

**Files:**

- Modify: `src/backend/processing/services/face_quality.py`
- Modify: `src/backend/processing/tests/test_face_quality_activation.py`

- [ ] Add a failing activation test containing preserved unaccepted failed, expired, and stale
      attempts followed by the approved accepted successful attempt and projection.
- [ ] Confirm the test fails because total/failure attempt counts currently reject the recovered
      evidence.
- [ ] Change the activation gate to compare the approval attempt count with accepted successful
      attempts rather than all historical attempts, while retaining all job, projection, cohort,
      reviewed face-count, and technical-failure gates.
- [ ] Keep regression coverage proving an unrecovered failed job, missing accepted attempt,
      incomplete projection coverage, or technical face failure still blocks activation.
- [ ] Run the focused activation tests and affected processing checks.

## Rollout

Merge and deploy the reviewed change to staging. Re-run the guarded candidate activation for
`cyclingrace-vechernee-sadovoe` with the already approved configuration and comparison hashes.
Verify the append-only activation record, ordinary event page, recognized-face display, and search
generation. Production remains out of scope.
