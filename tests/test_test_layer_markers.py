from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROCESSING_MIGRATION_CLASSES = (
    "ProcessingInitialMigrationTests",
    "ProcessingMigrationFunctionTests",
    "ProcessingFaceEmbeddingMigrationTests",
    "ProcessingPreviewDerivativeMigrationTests",
    "ProcessingWatermarkedDerivativeProducerMigrationTests",
    "ProcessingFaceIndexNameMigrationTests",
    "ProcessingFaceQualityGenerationMigrationTests",
)
PROCESSING_MODEL_CLASS = "ProcessingModelTests"
PROCESSING_MODELS = "src/backend/processing/tests/test_models.py"


def _collect(marker_expression: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTEST_ADDOPTS", None)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-m",
            marker_expression,
            PROCESSING_MODELS,
        ],
        cwd=ROOT,
        capture_output=True,
        env=environment,
        text=True,
        check=False,
    )


def _assert_processing_migration_contract_layers() -> None:
    migration = _collect("migration")

    assert migration.returncode == 0, migration.stdout + migration.stderr
    assert all(test_class in migration.stdout for test_class in PROCESSING_MIGRATION_CLASSES)
    assert PROCESSING_MODEL_CLASS not in migration.stdout
    migration_nodeids = [
        line for line in migration.stdout.splitlines() if line.startswith(PROCESSING_MODELS)
    ]
    assert len(migration_nodeids) == 10

    core = _collect("not migration")

    assert core.returncode == 0, core.stdout + core.stderr
    assert all(test_class not in core.stdout for test_class in PROCESSING_MIGRATION_CLASSES)
    assert PROCESSING_MODEL_CLASS in core.stdout

    database = _collect("db")

    assert database.returncode == 0, database.stdout + database.stderr
    assert all(test_class not in database.stdout for test_class in PROCESSING_MIGRATION_CLASSES)
    assert PROCESSING_MODEL_CLASS in database.stdout


def test_processing_migration_contracts_leave_the_core_layer() -> None:
    _assert_processing_migration_contract_layers()


def test_processing_migration_contract_collection_ignores_quiet_parent_addopts(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PYTEST_ADDOPTS", "-q")

    _assert_processing_migration_contract_layers()
