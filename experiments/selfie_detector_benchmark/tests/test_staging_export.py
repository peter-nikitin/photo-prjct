from __future__ import annotations

from detector_benchmark.staging_export import metadata_matches


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
