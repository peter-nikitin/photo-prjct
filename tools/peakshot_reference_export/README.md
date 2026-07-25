# Peakshot reference exporter

This isolated utility exports Peakshot face-cluster assignments for the CyclingRace event
and joins Peakshot photo IDs to the original filenames already stored on disk.

It is not part of the application runtime and has no dependencies beyond Python 3.9.

## Fixed inputs

- Peakshot event:
  `https://peakshot.ru/disk/12-07-2026-cyclingrace-olimpiya-trassa`
- Local originals:
  `/Users/petrnikitin/Documents/Projects/photo-refs/all/`

The script reads local filenames only. It does not download, change, or delete photographs.

## Run

From the repository root:

```bash
python3 tools/peakshot_reference_export/export.py
```

Each successful run creates an immutable UTC-timestamped directory alongside the originals:

```text
/Users/petrnikitin/Documents/Projects/photo-refs/
├── all/
└── peakshot-reference-exports/
    └── <UTC timestamp>/
```

Existing output directories are never replaced. A failed run removes its hidden staging
directory and publishes nothing.

## Artifacts

- `peakshot-person-photo-map.csv`: one `person_id,piece_id,filename` assignment per row.
- `peakshot-people.json`: each Peakshot person ID mapped to its original filenames.
- `peakshot-photos.json`: each original filename mapped to its Peakshot piece ID and all
  associated person IDs.
- `metadata.json`: source, capture time, local path, and exported counts.

A group photo can have multiple person IDs. Photos without a recognized person remain in
`peakshot-photos.json` with an empty `person_ids` list and do not have a CSV row.

## Validation and semantics

Before publishing, the exporter requires exact case-sensitive equality between the remote
Peakshot filename inventory and the local JPEG filename inventory. It also rejects duplicate
IDs or filenames, unknown photo IDs in a person cluster, malformed HTML, unsuccessful HTTP
responses, and inconsistent staged artifacts.

Peakshot clusters are algorithm-produced reference labels, not manually verified ground
truth. Ambiguous group photographs still require face-level manual review when they are used
to evaluate a recognition model.
