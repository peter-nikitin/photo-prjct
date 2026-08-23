"""Assign one primary test layer from the versioned suite-selection manifest."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.test import TransactionTestCase

from scripts.select_test_suites import DEFAULT_CONFIG, layer_for_path, load_config, select_suites

PRIMARY_LAYERS = {"unit", "db", "product_flow", "operational", "migration"}
CONFIG = load_config(DEFAULT_CONFIG)
ROOT = Path(__file__).resolve().parent


def _uses_django_database(item: pytest.Item) -> bool:
    test_class = getattr(item, "cls", None)
    if isinstance(test_class, type) and issubclass(test_class, TransactionTestCase):
        return True
    if item.get_closest_marker("django_db") is not None:
        return True
    return bool({"db", "transactional_db", "live_server"} & set(item.fixturenames))


def resolve_layer(path: str, *, django_db_enabled: bool, explicit_layers: set[str]) -> str:
    """Validate an explicit layer marker against manifest-owned selection."""
    if len(explicit_layers) > 1:
        raise pytest.UsageError(
            f"conflicting explicit layer markers for {path}: {sorted(explicit_layers)}"
        )
    expected_layer = layer_for_path(CONFIG, path, django_db_enabled)
    migration_override = (
        explicit_layers == {"migration"}
        and expected_layer == "db"
        and select_suites(CONFIG, [path]).migrations
    )
    if explicit_layers and explicit_layers != {expected_layer} and not migration_override:
        raise pytest.UsageError(
            f"explicit layer ownership for {path} conflicts with manifest: "
            f"{sorted(explicit_layers)} != {expected_layer}"
        )
    return next(iter(explicit_layers), expected_layer)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        path = item.path.relative_to(ROOT).as_posix()
        explicit_layers = {
            marker.name for marker in item.iter_markers() if marker.name in PRIMARY_LAYERS
        }
        expected_layer = resolve_layer(
            path,
            django_db_enabled=_uses_django_database(item),
            explicit_layers=explicit_layers,
        )
        if not explicit_layers:
            item.add_marker(expected_layer)
