#!/usr/bin/env python3
"""Build face-spike labels from `all` and `Person ...` directories."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

JPEG_SUFFIXES = {".jpg", ".jpeg"}
LABEL_HEADER = ("filename", "participant_group", "face_expected")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create labels.csv from an event directory containing `all` and `Person ...` folders. "
            "Photos assigned to multiple people are omitted as ambiguous."
        )
    )
    parser.add_argument(
        "dataset",
        type=Path,
        help="Directory containing `all` and `Person ...` directories",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output CSV path (default: DATASET/labels.csv)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing output file",
    )
    return parser.parse_args()


def jpeg_files(directory: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in JPEG_SUFFIXES
        ),
        key=lambda path: path.name,
    )


def build_labels(dataset: Path, output: Path, *, force: bool) -> None:
    dataset = dataset.resolve()
    all_photos = dataset / "all"
    person_directories = sorted(
        (path for path in dataset.glob("Person *") if path.is_dir()),
        key=lambda path: path.name,
    )

    if not all_photos.is_dir():
        raise SystemExit(f"missing photo directory: {all_photos}")
    if not person_directories:
        raise SystemExit(f"no `Person ...` directories found in: {dataset}")
    if output.exists() and not force:
        raise SystemExit(f"output already exists: {output} (use --force to replace it)")

    all_by_name: dict[str, Path] = {}
    for photo in jpeg_files(all_photos):
        if photo.name in all_by_name:
            raise SystemExit(f"duplicate filename in `all`: {photo.name}")
        all_by_name[photo.name] = photo

    groups_by_filename: defaultdict[str, set[str]] = defaultdict(set)
    person_mapping: list[tuple[str, str]] = []
    missing: list[tuple[str, str]] = []

    for index, directory in enumerate(person_directories, start=1):
        group = f"person-{index:03d}"
        person_mapping.append((directory.name, group))
        for photo in jpeg_files(directory):
            if photo.name not in all_by_name:
                missing.append((directory.name, photo.name))
                continue
            groups_by_filename[photo.name].add(group)

    if missing:
        for directory, filename in missing:
            print(f"missing from all: {directory}/{filename}", file=sys.stderr)
        raise SystemExit(f"{len(missing)} recognized photos are missing from `all`")

    ambiguous = {
        filename: groups for filename, groups in groups_by_filename.items() if len(groups) > 1
    }
    rows: list[tuple[str, str, str]] = []
    positive_count = 0
    uncertain_count = 0

    for filename in sorted(all_by_name):
        groups = groups_by_filename.get(filename, set())
        if len(groups) > 1:
            continue
        if groups:
            rows.append((filename, next(iter(groups)), "yes"))
            positive_count += 1
        else:
            rows.append((filename, "", "uncertain"))
            uncertain_count += 1

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    try:
        with temporary.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(LABEL_HEADER)
            writer.writerows(rows)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)

    print("Person directory mapping:")
    for directory, group in person_mapping:
        print(f"  {directory} -> {group}")
    if ambiguous:
        print("Ambiguous photos omitted:", file=sys.stderr)
        for filename, groups in sorted(ambiguous.items()):
            print(f"  {filename}: {', '.join(sorted(groups))}", file=sys.stderr)
    print(f"Wrote: {output}")
    print(f"All event photos: {len(all_by_name)}")
    print(f"Positive labelled photos: {positive_count}")
    print(f"Uncertain photos: {uncertain_count}")
    print(f"Ambiguous photos omitted: {len(ambiguous)}")
    print(f"CSV rows: {len(rows)}")


def main() -> None:
    arguments = parse_args()
    dataset = arguments.dataset.resolve()
    output = (arguments.output or dataset / "labels.csv").resolve()
    build_labels(dataset, output, force=arguments.force)


if __name__ == "__main__":
    main()
