"""Select the smallest required verification suite from changed repository paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Protocol

EXPENSIVE_SUITES = ("operational", "migrations", "visual")
PRIMARY_LAYERS = {"operational", "migration", "product_flow"}
KNOWN_CATEGORIES = {
    "agent-tooling",
    "documentation",
    "operational",
    "migrations",
    "visual",
    "product-flow",
    "python",
    "javascript",
    "workflow",
    "build",
    "configuration",
    "repository",
}
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "tests" / "suite-selection.toml"


class Digest(Protocol):
    def update(self, data: bytes, /) -> object: ...


@dataclass(frozen=True)
class PathCategory:
    name: str
    patterns: tuple[str, ...]
    suites: tuple[str, ...]
    layer: str | None
    layer_patterns: tuple[str, ...] | None


@dataclass(frozen=True)
class SuiteSelectionConfig:
    categories: tuple[PathCategory, ...]


@dataclass(frozen=True)
class Selection:
    core: bool
    operational: bool
    migrations: bool
    visual: bool
    reasons: dict[str, tuple[str, ...]]

    def as_dict(self) -> dict[str, object]:
        return {
            "core": self.core,
            "operational": self.operational,
            "migrations": self.migrations,
            "visual": self.visual,
            "reasons": {suite: list(self.reasons[suite]) for suite in EXPENSIVE_SUITES},
        }


def _normalize_pattern(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("patterns must be non-empty strings")
    if "\\" in value or value.startswith("/") or "//" in value:
        raise ValueError(f"pattern must be a relative POSIX path: {value!r}")
    parts = PurePosixPath(value).parts
    if any(part in {".", ".."} for part in parts):
        raise ValueError(f"pattern must not escape the repository: {value!r}")
    return value


def _expect_keys(mapping: dict[str, object], expected: set[str], context: str) -> None:
    unsupported = set(mapping) - expected
    missing = expected - set(mapping)
    if unsupported:
        raise ValueError(f"unsupported {context} keys: {sorted(unsupported)}")
    if missing:
        raise ValueError(f"missing {context} keys: {sorted(missing)}")


def load_config(path: Path) -> SuiteSelectionConfig:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"cannot read suite selection manifest {path}: {error}") from error
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"invalid suite selection TOML: {error}") from error
    _expect_keys(raw, {"version", "categories"}, "manifest")
    if raw["version"] != 1:
        raise ValueError("unsupported manifest version")
    category_rows = raw["categories"]
    if not isinstance(category_rows, list) or not category_rows:
        raise ValueError("categories must be a non-empty array")

    categories: list[PathCategory] = []
    seen_names: set[str] = set()
    seen_suites: set[str] = set()
    for row in category_rows:
        if not isinstance(row, dict):
            raise ValueError("categories entries must be tables")
        unsupported = set(row) - {"name", "patterns", "suites", "layer", "layer_patterns"}
        missing = {"name", "patterns", "suites"} - set(row)
        if unsupported:
            raise ValueError(f"unsupported category keys: {sorted(unsupported)}")
        if missing:
            raise ValueError(f"missing category keys: {sorted(missing)}")
        name = row["name"]
        if not isinstance(name, str) or name not in KNOWN_CATEGORIES or name in seen_names:
            raise ValueError(f"category must be a unique known repository category: {name!r}")
        seen_names.add(name)
        raw_patterns = row["patterns"]
        if not isinstance(raw_patterns, list) or not raw_patterns:
            raise ValueError(f"category {name!r} must own at least one pattern")
        patterns = tuple(_normalize_pattern(pattern) for pattern in raw_patterns)
        raw_suites = row["suites"]
        if not isinstance(raw_suites, list) or any(
            not isinstance(suite, str) or suite not in EXPENSIVE_SUITES for suite in raw_suites
        ):
            raise ValueError(f"category {name!r} has unsupported suites")
        suites = tuple(sorted(set(raw_suites)))
        seen_suites.update(suites)
        layer = row.get("layer")
        if layer is not None and (not isinstance(layer, str) or layer not in PRIMARY_LAYERS):
            raise ValueError(f"category {name!r} has unsupported layer")
        if layer == "operational" and suites != ("operational",):
            raise ValueError("operational layer must select only the operational suite")
        if layer == "migration" and suites != ("migrations",):
            raise ValueError("migration layer must select only the migrations suite")
        raw_layer_patterns = row.get("layer_patterns")
        if raw_layer_patterns is None:
            layer_patterns = patterns if layer else None
        else:
            if layer is None:
                raise ValueError(f"category {name!r} has layer patterns without a layer")
            if not isinstance(raw_layer_patterns, list) or not raw_layer_patterns:
                raise ValueError(f"category {name!r} layer patterns must be a non-empty array")
            layer_patterns = tuple(_normalize_pattern(pattern) for pattern in raw_layer_patterns)
            if not set(layer_patterns).issubset(patterns):
                raise ValueError(f"category {name!r} layer patterns must be selected patterns")
        categories.append(PathCategory(name, patterns, suites, layer, layer_patterns))
    missing_suites = set(EXPENSIVE_SUITES) - seen_suites
    if missing_suites:
        raise ValueError(f"missing suite ownership: {sorted(missing_suites)}")
    return SuiteSelectionConfig(tuple(categories))


def _normalize_path(path: str) -> str | None:
    if not path or "\\" in path or path.startswith("/") or "//" in path:
        return None
    candidate = PurePosixPath(path)
    if any(part in {".", ".."} for part in candidate.parts):
        return None
    normalized = candidate.as_posix()
    return normalized if normalized and normalized != "." else None


def _matching_categories(config: SuiteSelectionConfig, path: str) -> tuple[PathCategory, ...]:
    return tuple(
        category
        for category in config.categories
        if any(fnmatchcase(path, pattern) for pattern in category.patterns)
    )


def _exhaustive_selection(reason: str) -> Selection:
    reasons = {suite: (reason,) for suite in EXPENSIVE_SUITES}
    return Selection(True, True, True, True, reasons)


def select_suites(config: SuiteSelectionConfig, changed_files: Sequence[str]) -> Selection:
    reasons = {suite: set() for suite in EXPENSIVE_SUITES}
    for supplied_path in changed_files:
        path = _normalize_path(supplied_path)
        if path is None:
            return _exhaustive_selection(f"fail-closed: malformed path {supplied_path!r}")
        categories = _matching_categories(config, path)
        if not categories:
            return _exhaustive_selection(f"fail-closed: unowned path {path!r}")
        for category in categories:
            for suite in category.suites:
                reasons[suite].add(path)
    ordered_reasons = {suite: tuple(sorted(reasons[suite])) for suite in EXPENSIVE_SUITES}
    return Selection(
        core=True,
        operational=bool(ordered_reasons["operational"]),
        migrations=bool(ordered_reasons["migrations"]),
        visual=bool(ordered_reasons["visual"]),
        reasons=ordered_reasons,
    )


def layer_for_path(config: SuiteSelectionConfig, path: str, django_db_enabled: bool) -> str:
    normalized = _normalize_path(path)
    if normalized is None:
        raise ValueError(f"cannot classify malformed test path {path!r}")
    layers = {
        category.layer
        for category in _matching_categories(config, normalized)
        if category.layer
        and category.layer_patterns
        and any(fnmatchcase(normalized, pattern) for pattern in category.layer_patterns)
    }
    if len(layers) > 1:
        raise ValueError(f"conflicting manifest layer ownership for {normalized}: {sorted(layers)}")
    if layers:
        return layers.pop()
    return "db" if django_db_enabled else "unit"


def _run_git(repository: Path, arguments: Sequence[str]) -> bytes:
    completed = subprocess.run(
        ["git", *arguments], cwd=repository, capture_output=True, check=False
    )
    if completed.returncode:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(stderr or f"git {' '.join(arguments)} failed")
    return completed.stdout


def changed_files_from_git(repository: Path, base: str, head: str) -> list[str]:
    if not base or not head:
        raise ValueError("base and head revisions are both required")
    output = _run_git(repository, ["diff", "--name-only", "--diff-filter=ACMRD", base, head])
    try:
        text = output.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("git diff paths must be UTF-8") from error
    paths = text.splitlines()
    if any(not path or "\x00" in path for path in paths):
        raise ValueError("git diff returned malformed paths")
    return paths


def fingerprint(repository: Path, base: str) -> str:
    base_revision = _run_git(repository, ["rev-parse", "--verify", f"{base}^{{commit}}"]).strip()
    base_name = base_revision.decode("ascii")
    changed = _run_git(
        repository,
        ["diff", "--name-only", "--no-renames", "--diff-filter=ACMRD", "-z", base_name],
    )
    untracked = _run_git(repository, ["ls-files", "--others", "--exclude-standard", "-z"])
    paths = _fingerprint_paths(changed.split(b"\0") + untracked.split(b"\0"))
    digest = hashlib.sha256()
    _update_fingerprint_record(digest, b"base", base_revision)
    for path in paths:
        _update_fingerprint_record(digest, b"path", path.encode("utf-8"))
        _update_fingerprint_final_state(digest, repository / path)
    return digest.hexdigest()


def _fingerprint_paths(raw_paths: Sequence[bytes]) -> tuple[str, ...]:
    paths: set[str] = set()
    for raw_path in raw_paths:
        if not raw_path:
            continue
        try:
            supplied_path = raw_path.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("git fingerprint paths must be UTF-8") from error
        path = _normalize_path(supplied_path)
        if path is None or path == ".git" or path.startswith(".git/"):
            raise ValueError("git returned an invalid fingerprint path")
        paths.add(path)
    return tuple(sorted(paths))


def _update_fingerprint_record(digest: Digest, label: bytes, payload: bytes) -> None:
    digest.update(label)
    digest.update(b"\0")
    digest.update(str(len(payload)).encode("ascii"))
    digest.update(b"\0")
    digest.update(payload)


def _update_fingerprint_final_state(digest: Digest, path: Path) -> None:
    try:
        state = path.lstat()
    except FileNotFoundError:
        _update_fingerprint_record(digest, b"exists", b"0")
        return

    _update_fingerprint_record(digest, b"exists", b"1")
    mode = stat.S_IMODE(state.st_mode)
    _update_fingerprint_record(digest, b"mode", f"{mode:o}".encode("ascii"))
    if stat.S_ISREG(state.st_mode):
        file_type = b"regular"
        content = path.read_bytes()
    elif stat.S_ISLNK(state.st_mode):
        file_type = b"symlink"
        content = os.fsencode(os.readlink(path))
    elif stat.S_ISDIR(state.st_mode):
        file_type = b"directory"
        content = b""
    else:
        file_type = b"other"
        content = b""
    _update_fingerprint_record(digest, b"type", file_type)
    _update_fingerprint_record(digest, b"content", content)


def _github_output(selection: Selection) -> str:
    lines = [f"core={str(selection.core).lower()}"]
    lines.extend(f"{suite}={str(getattr(selection, suite)).lower()}" for suite in EXPENSIVE_SUITES)
    for suite in EXPENSIVE_SUITES:
        reason = ", ".join(selection.reasons[suite]) or "not selected"
        lines.append(f"{suite}_reason={reason}")
    return "\n".join(lines) + "\n"


def _selection_from_args(args: argparse.Namespace) -> Selection:
    config = load_config(args.config)
    if args.changed_file:
        if args.base is not None or args.head is not None:
            raise ValueError("choose explicit changed files or base/head revisions")
        return select_suites(config, args.changed_file)
    if args.base is None or args.head is None:
        return _exhaustive_selection("fail-closed: both base and head revisions are required")
    try:
        paths = changed_files_from_git(Path.cwd(), args.base, args.head)
    except ValueError as error:
        return _exhaustive_selection(f"fail-closed: git diff unavailable: {error}")
    return select_suites(config, paths)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    select = subparsers.add_parser("select")
    select.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    select.add_argument("--changed-file", action="append")
    select.add_argument("--base")
    select.add_argument("--head")
    select.add_argument("--format", choices=("json", "github"), default="json")
    fingerprint_parser = subparsers.add_parser("fingerprint")
    fingerprint_parser.add_argument("--base", required=True)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "fingerprint":
            print(fingerprint(Path.cwd(), arguments.base))
            return 0
        selection = _selection_from_args(arguments)
    except ValueError as error:
        print(f"selector error: {error}", file=sys.stderr)
        return 2
    if arguments.format == "json":
        print(json.dumps(selection.as_dict(), sort_keys=True))
    else:
        print(_github_output(selection), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
