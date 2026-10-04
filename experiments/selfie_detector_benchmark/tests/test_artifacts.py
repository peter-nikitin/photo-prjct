from __future__ import annotations

from pathlib import Path

import pytest
from detector_benchmark.artifacts import publish_immutable


def test_immutable_publication_preserves_destination_and_removes_partial_stage(
    tmp_path: Path,
) -> None:
    """A failed run must not leave a completed-looking or overwritten review bundle."""
    output = tmp_path / "run"
    output.mkdir()
    (output / "sentinel").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        publish_immutable(
            output, lambda stage: (stage / "data").write_text("new", encoding="utf-8")
        )
    assert (output / "sentinel").read_text(encoding="utf-8") == "keep"

    failed = tmp_path / "failed"

    def write_then_fail(stage: Path) -> None:
        (stage / "partial").write_text("partial", encoding="utf-8")
        raise RuntimeError("injected failure")

    with pytest.raises(RuntimeError, match="injected"):
        publish_immutable(failed, write_then_fail)
    assert not failed.exists()
    assert not list(tmp_path.glob(".failed.*"))
