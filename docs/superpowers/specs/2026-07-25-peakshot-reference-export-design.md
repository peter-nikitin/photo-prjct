# Peakshot reference export design

## Goal

Add an isolated repository utility that exports Peakshot face-cluster assignments into
filename-based reference artifacts for the originals stored in
`/Users/petrnikitin/Documents/Projects/photo-refs/all/`.

The utility is not part of the application runtime and must live outside `src`.

## Location and interface

The utility lives in `tools/peakshot_reference_export/` and consists of:

- `export.py`, an executable Python 3 script using only the standard library;
- `README.md`, with the purpose, fixed inputs, output formats, and invocation.

The script is run without required arguments:

```bash
python3 tools/peakshot_reference_export/export.py
```

It uses these fixed inputs:

- event URL:
  `https://peakshot.ru/disk/12-07-2026-cyclingrace-olimpiya-trassa`;
- originals directory:
  `/Users/petrnikitin/Documents/Projects/photo-refs/all/`.

## Data flow

1. Request the event's `/pieces?design_variant=masonry` endpoint.
2. Parse each photo's `data-gallery-piece-id` and `data-gallery-title` to build the
   `piece_id -> filename` map.
3. Request `/people/recognition` with the `Turbo-Frame: modal` header.
4. Parse the unique person IDs from the returned person links.
5. For every person, request
   `/pieces?design_variant=masonry&person_id=<person_id>` and parse its piece IDs.
6. Join person assignments to original filenames through the piece ID.
7. Validate the complete result before publishing any artifacts.
8. Write artifacts to a hidden staging directory and rename it to a timestamped output
   directory only after every file is complete.

## Validation

The export fails without publishing a partial result if:

- a piece ID or filename is missing or duplicated in the all-photos response;
- a person response references an unknown piece ID;
- a remote filename is absent from the originals directory;
- a local JPEG filename is absent from the remote all-photos response;
- the HTTP response is unsuccessful or cannot be parsed.

Filename comparison is case-sensitive because the currently verified local and remote sets
match exactly.

## Outputs

Each successful run creates:

```text
/Users/petrnikitin/Documents/Projects/photo-refs/peakshot-reference-exports/<UTC timestamp>/
```

The directory contains:

- `peakshot-person-photo-map.csv` with columns `person_id,piece_id,filename`;
- `peakshot-people.json`, mapping each person ID to its sorted filenames;
- `peakshot-photos.json`, mapping each filename to its Peakshot piece ID and sorted person
  IDs;
- `metadata.json`, recording source URL, UTC capture time, originals directory, people
  count, photo count, and assignment count.

JSON files are UTF-8, pretty-printed, and end with a newline. CSV ordering is deterministic:
person ID, filename, then piece ID.

Timestamped output directories are immutable: the script refuses to replace an existing
directory.

## Scope boundaries

- The script exports Peakshot's algorithmic clusters as reference labels; it does not claim
  that they are manually verified ground truth.
- It does not download or modify photographs.
- It does not modify existing label files or experiment runs.
- It does not add automated tests, per the request.
- It does not depend on Django or application packages.

## Verification

Run the exporter against the live event and current originals directory. Confirm successful
completion, validate that all four artifacts exist, parse both JSON files and the CSV, and
check the reported counts against their contents. Run repository formatting and lint checks
that apply to the new Python file.
