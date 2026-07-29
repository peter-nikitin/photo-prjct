"""Keep standalone worker tests independent of a developer's Django environment."""

from __future__ import annotations

import os

# Root pytest config enables pytest-django for the combined repository suite.  These tests never
# access Django, but settings import happens before collection, so provide inert local values when
# this isolated worktree has no project `.env` file.  Existing CI/developer values win unchanged.
os.environ.setdefault("DB_NAME", "worker_tests")
os.environ.setdefault("DB_USER", "worker_tests")
os.environ.setdefault("DB_PASSWORD", "worker_tests")
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("SECRET_KEY", "worker-tests-not-a-deployment-secret")
