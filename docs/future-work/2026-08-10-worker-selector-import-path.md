# Make worker test selectors resolve the worker package

## Observed gap

Running the documented root command with explicit worker selectors:

```sh
make test TESTS="src/worker/tests/test_runner.py src/worker/tests/test_contracts.py"
```

causes pytest to select `src/worker/pytest.ini`, while the full root suite uses the root
`pyproject.toml` configuration. `photo_worker` is not installed into the root `.venv`, so focused
collection stops with `ModuleNotFoundError: No module named 'photo_worker'`. Supplying
`PYTHONPATH=src/worker` makes the same focused tests pass.

## Why this does not block the current task

The deployment rollout neither changes worker implementation nor either pytest configuration. Its
deployment and workflow tests pass, and the focused worker contract check passes with the explicit,
caller-local `PYTHONPATH=src/worker` projection. That narrow workaround is accepted for the current
task instead of changing the root Make target or package installation boundary without a broader
test-runner decision.

## Revisit trigger

Bring this into scope before the next plan or CI job requires the literal root selector command to
work without a caller-specific path override, or when the root or nested pytest configuration next
changes. Make the minimal correction in that test invocation: add the worker source directory to
the test import path (or run pytest from `src/worker`) without changing production packaging.
