#!/usr/bin/env python3
"""Export Peakshot person clusters as filename-based reference artifacts."""

from __future__ import annotations

import csv
import json
import shutil
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

EVENT_URL = "https://peakshot.ru/disk/12-07-2026-cyclingrace-olimpiya-trassa"
ORIGINALS_DIR = Path("/Users/petrnikitin/Documents/Projects/photo-refs/all/")
OUTPUTS_DIR = ORIGINALS_DIR.parent / "peakshot-reference-exports"
USER_AGENT = "FindMe-Photo-Peakshot-Reference-Exporter/1.0"
HTTP_TIMEOUT_SECONDS = 30

CSV_FILENAME = "peakshot-person-photo-map.csv"
PEOPLE_FILENAME = "peakshot-people.json"
PHOTOS_FILENAME = "peakshot-photos.json"
METADATA_FILENAME = "metadata.json"


class ExportError(RuntimeError):
    """An expected export failure with a user-actionable message."""


class AllPiecesParser(HTMLParser):
    """Extract Peakshot piece IDs and original filenames."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attributes = dict(attrs)
        piece_id = attributes.get("data-gallery-piece-id")
        filename = attributes.get("data-gallery-title")
        if piece_id is None and filename is None:
            return
        if not piece_id or not piece_id.isdigit() or not filename:
            raise ExportError("Malformed photo entry in the all-photos response")
        self.rows.append((piece_id, filename))


class PeopleParser(HTMLParser):
    """Extract event-local numeric person IDs."""

    def __init__(self, event_path: str) -> None:
        super().__init__()
        self.person_ids: set[str] = set()
        self.person_prefix = f"{event_path.rstrip('/')}/people/"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        path = urlparse(href).path
        if not path.startswith(self.person_prefix):
            return
        person_id = path.removeprefix(self.person_prefix).strip("/")
        if person_id.isdigit():
            self.person_ids.add(person_id)


class PersonPiecesParser(HTMLParser):
    """Extract photo piece IDs from a person's gallery response."""

    def __init__(self) -> None:
        super().__init__()
        self.piece_ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        piece_id = dict(attrs).get("data-piece-id")
        if piece_id is None:
            return
        if not piece_id.isdigit():
            raise ExportError("Malformed piece ID in a person response")
        self.piece_ids.add(piece_id)


@dataclass(frozen=True)
class ExportData:
    captured_at: datetime
    piece_to_filename: dict[str, str]
    people: dict[str, list[str]]
    photos: dict[str, dict[str, str | list[str]]]

    @property
    def assignment_count(self) -> int:
        return sum(len(filenames) for filenames in self.people.values())


def fetch_html(url: str, *, headers: Mapping[str, str] | None = None) -> str:
    request_headers = {"User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)
    request = Request(url, headers=request_headers)
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            status = response.status
            body = response.read()
            content_type = response.headers.get_content_charset() or "utf-8"
    except HTTPError as exc:
        raise ExportError(f"Peakshot returned HTTP {exc.code} for {url}") from exc
    except URLError as exc:
        raise ExportError(f"Could not request {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ExportError(f"Timed out requesting {url}") from exc

    if status != 200:
        raise ExportError(f"Peakshot returned HTTP {status} for {url}")
    try:
        return body.decode(content_type)
    except (LookupError, UnicodeDecodeError) as exc:
        raise ExportError(f"Could not decode the response from {url}") from exc


def pieces_url(*, person_id: str | None = None) -> str:
    query: dict[str, str] = {"design_variant": "masonry"}
    if person_id is not None:
        query["person_id"] = person_id
    return f"{EVENT_URL}/pieces?{urlencode(query)}"


def collect_remote_photos() -> dict[str, str]:
    parser = AllPiecesParser()
    parser.feed(fetch_html(pieces_url()))
    parser.close()
    if not parser.rows:
        raise ExportError("The all-photos response contained no photos")

    piece_to_filename: dict[str, str] = {}
    filename_to_piece: dict[str, str] = {}
    for piece_id, filename in parser.rows:
        if piece_id in piece_to_filename:
            raise ExportError(f"Duplicate remote piece ID: {piece_id}")
        if filename in filename_to_piece:
            raise ExportError(f"Duplicate remote filename: {filename}")
        piece_to_filename[piece_id] = filename
        filename_to_piece[filename] = piece_id
    return piece_to_filename


def collect_person_ids() -> list[str]:
    event_path = urlparse(EVENT_URL).path
    parser = PeopleParser(event_path)
    parser.feed(
        fetch_html(
            f"{EVENT_URL}/people/recognition",
            headers={"Turbo-Frame": "modal"},
        )
    )
    parser.close()
    if not parser.person_ids:
        raise ExportError("The recognition response contained no people")
    return sorted(parser.person_ids, key=int)


def collect_person_piece_ids(person_id: str) -> set[str]:
    parser = PersonPiecesParser()
    parser.feed(fetch_html(pieces_url(person_id=person_id)))
    parser.close()
    if not parser.piece_ids:
        raise ExportError(f"Person {person_id} has no photos")
    return parser.piece_ids


def collect_local_filenames() -> set[str]:
    if not ORIGINALS_DIR.is_dir():
        raise ExportError(f"Originals directory does not exist: {ORIGINALS_DIR}")
    filenames = {
        path.name
        for path in ORIGINALS_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg"}
    }
    if not filenames:
        raise ExportError(f"No JPEG originals found in {ORIGINALS_DIR}")
    return filenames


def format_inventory_mismatch(label: str, filenames: set[str]) -> str:
    preview = ", ".join(sorted(filenames)[:10])
    suffix = "" if len(filenames) <= 10 else f", ... ({len(filenames)} total)"
    return f"{label}: {preview}{suffix}"


def build_export() -> ExportData:
    captured_at = datetime.now(timezone.utc)  # noqa: UP017 - system Python is 3.9.
    piece_to_filename = collect_remote_photos()
    local_filenames = collect_local_filenames()
    remote_filenames = set(piece_to_filename.values())

    missing_locally = remote_filenames - local_filenames
    if missing_locally:
        raise ExportError(
            format_inventory_mismatch("Remote photos missing locally", missing_locally)
        )
    missing_remotely = local_filenames - remote_filenames
    if missing_remotely:
        raise ExportError(
            format_inventory_mismatch("Local photos missing remotely", missing_remotely)
        )

    print(f"Photos: {len(piece_to_filename)} remote, {len(local_filenames)} local")
    person_ids = collect_person_ids()
    print(f"People: {len(person_ids)}")

    people: dict[str, list[str]] = {}
    filename_to_people = {filename: [] for filename in remote_filenames}
    for index, person_id in enumerate(person_ids, start=1):
        print(f"People progress: {index}/{len(person_ids)} (person {person_id})")
        piece_ids = collect_person_piece_ids(person_id)
        unknown_piece_ids = piece_ids - piece_to_filename.keys()
        if unknown_piece_ids:
            unknown = ", ".join(sorted(unknown_piece_ids, key=int))
            raise ExportError(f"Person {person_id} references unknown pieces: {unknown}")
        filenames = sorted(piece_to_filename[piece_id] for piece_id in piece_ids)
        people[person_id] = filenames
        for filename in filenames:
            filename_to_people[filename].append(person_id)

    filename_to_piece = {filename: piece_id for piece_id, filename in piece_to_filename.items()}
    photos: dict[str, dict[str, str | list[str]]] = {}
    for filename in sorted(remote_filenames):
        photos[filename] = {
            "piece_id": filename_to_piece[filename],
            "person_ids": sorted(filename_to_people[filename], key=int),
        }

    return ExportData(
        captured_at=captured_at,
        piece_to_filename=dict(sorted(piece_to_filename.items(), key=lambda item: int(item[0]))),
        people=people,
        photos=photos,
    )


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def csv_rows(data: ExportData) -> list[tuple[str, str, str]]:
    filename_to_piece = {
        filename: piece_id for piece_id, filename in data.piece_to_filename.items()
    }
    rows = [
        (person_id, filename_to_piece[filename], filename)
        for person_id, filenames in data.people.items()
        for filename in filenames
    ]
    return sorted(rows, key=lambda row: (int(row[0]), row[2], int(row[1])))


def validate_staged_artifacts(stage: Path, data: ExportData) -> None:
    people = json.loads((stage / PEOPLE_FILENAME).read_text(encoding="utf-8"))
    photos = json.loads((stage / PHOTOS_FILENAME).read_text(encoding="utf-8"))
    metadata = json.loads((stage / METADATA_FILENAME).read_text(encoding="utf-8"))
    with (stage / CSV_FILENAME).open(newline="", encoding="utf-8") as csv_file:
        rows = list(csv.DictReader(csv_file))

    if people != data.people:
        raise ExportError("Staged people JSON does not match the collected data")
    if photos != data.photos:
        raise ExportError("Staged photos JSON does not match the collected data")
    if len(rows) != data.assignment_count:
        raise ExportError("Staged CSV assignment count does not match the collected data")
    expected_counts = {
        "people_count": len(data.people),
        "photo_count": len(data.photos),
        "assignment_count": data.assignment_count,
    }
    if any(metadata.get(key) != value for key, value in expected_counts.items()):
        raise ExportError("Staged metadata counts do not match the collected data")


def write_export(data: ExportData) -> Path:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = data.captured_at.strftime("%Y%m%dT%H%M%SZ")
    destination = OUTPUTS_DIR / timestamp
    if destination.exists():
        raise ExportError(f"Output directory already exists: {destination}")

    stage = Path(tempfile.mkdtemp(prefix=f".{timestamp}-", dir=OUTPUTS_DIR))
    try:
        write_json(stage / PEOPLE_FILENAME, data.people)
        write_json(stage / PHOTOS_FILENAME, data.photos)
        write_json(
            stage / METADATA_FILENAME,
            {
                "assignment_count": data.assignment_count,
                "captured_at": data.captured_at.isoformat(),
                "event_url": EVENT_URL,
                "originals_directory": str(ORIGINALS_DIR),
                "people_count": len(data.people),
                "photo_count": len(data.photos),
            },
        )
        with (stage / CSV_FILENAME).open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(("person_id", "piece_id", "filename"))
            writer.writerows(csv_rows(data))
        validate_staged_artifacts(stage, data)
        stage.rename(destination)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return destination


def main() -> int:
    try:
        data = build_export()
        print(f"Assignments: {data.assignment_count}")
        output_path = write_export(data)
    except ExportError as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1
    print(f"Export written to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
