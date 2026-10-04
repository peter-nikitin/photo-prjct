from __future__ import annotations

import os
import sys
from types import SimpleNamespace

from detector_benchmark.staging_export import bootstrap_django, metadata_matches


def test_export_requires_database_head_and_body_metadata_to_match() -> None:
    """A stale DB byte-size or content type must prevent a snapshot export."""
    assert metadata_matches(
        database_size=10,
        head_size=10,
        body_size=10,
        database_type="image/jpeg",
        head_type="image/jpeg",
    )
    assert not metadata_matches(
        database_size=9,
        head_size=10,
        body_size=10,
        database_type="image/jpeg",
        head_type="image/jpeg",
    )
    assert not metadata_matches(
        database_size=10,
        head_size=10,
        body_size=9,
        database_type="image/jpeg",
        head_type="image/jpeg",
    )
    assert not metadata_matches(
        database_size=10,
        head_size=10,
        body_size=10,
        database_type="image/jpeg",
        head_type="image/png",
    )


def test_staging_export_bootstraps_canonical_django_settings_before_orm_access(monkeypatch) -> None:
    """A raw container Python process needs the same settings/bootstrap as manage.py."""
    calls: list[str] = []
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
    monkeypatch.setitem(sys.modules, "django", SimpleNamespace(setup=lambda: calls.append("setup")))

    bootstrap_django()

    assert calls == ["setup"]
    assert os.environ["DJANGO_SETTINGS_MODULE"] == "config.settings"
