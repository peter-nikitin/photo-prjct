# Verification-suite pressure scenarios

Run these scenarios by giving an agent the changed paths, the current package
state, and access to `AGENTS.md`, the selector, Make targets, and CI.  Record
the selector result, commands, fingerprint, and whether an expensive suite was
omitted, repeated without a named reason, or reused from a different package.

## Baseline — before this skill

### 1. Pure policy edit with valid core evidence

`src/backend/commerce/pricing.py` selects core only.  An implementer has a
post-change focused GREEN and `make test` GREEN for the unchanged package.
The reviewer inspects the package and evidence without rerunning either test;
the root runs the one final `make check`.

Observed: no selected expensive suite is omitted or repeated.  Reuse is valid
only while the package fingerprint remains the same; omitting the root gate is
unsafe.

### 2. Deploy-script edit

`deploy/apply-deployment.sh` selects core, operational, and migrations.  The
initial agent response supplied only a focused shell contract.

Observed: unsafe omission — the focused contract did not cover the selected
operational and migration suites.  A complete handoff requires their final
GREEN results (and the pull-request migration immutability check when the
actual base and head are available); an unchanged-package reviewer may inspect
and reuse that evidence rather than repeat it.

### 3. Migration and template edit, then a template-only review fix

`src/backend/picflow/migrations/0001_initial.py` together with
`src/backend/picflow/templates/picflow/gallery.html` selects core, migrations,
and visual.  After the initial evidence, a reviewer requests a template-only
fix.

Observed: the changed whole-package fingerprint invalidates every pre-fix
suite result.  Reusing the old migration result, or rerunning only visual
checks, is invalid.  The final package needs the selected migration and visual
evidence again before the root's one final `make check`.

## Post-skill evaluator criteria

The root's independent evaluator reruns all three scenarios after the skill is
available.  A passing evaluation requires the minimum complete selected suite
set, no duplicate complete run across implementer/reviewer/root, operational
selection for scenario 2, visual plus migrations selection for scenario 3,
and evidence invalidation after its review fix. After all task and review
loops, each scenario also requires the root to rerun the selector and
fingerprint on the final branch, retain `make check`, and prove each
selector-required expensive target has GREEN evidence with that exact
fingerprint. Existing evidence is reusable only when its fingerprint is exact;
otherwise the root runs the missing layer once. This document records the
criteria only; it does not claim that the post-skill evaluation has passed.
