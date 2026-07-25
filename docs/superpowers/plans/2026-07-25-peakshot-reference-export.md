# Peakshot Reference Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an isolated command-line utility that joins Peakshot person clusters to the
1420 locally stored original filenames and publishes deterministic reference artifacts.

**Architecture:** A standard-library Python script fetches and parses Peakshot's HTML
endpoints, validates the complete remote and local photo inventories, joins people to photos
through Peakshot piece IDs, and atomically publishes a timestamped output directory. A
co-located README documents the fixed inputs and generated formats.

**Tech Stack:** Python 3.9+ standard library (`csv`, `datetime`, `html.parser`, `json`,
`pathlib`, `shutil`, `tempfile`, `urllib`)

## Global Constraints

- Create the utility under `tools/peakshot_reference_export/`, outside `src`.
- Use the fixed event URL
  `https://peakshot.ru/disk/12-07-2026-cyclingrace-olimpiya-trassa`.
- Use the fixed originals directory
  `/Users/petrnikitin/Documents/Projects/photo-refs/all/`.
- Do not download or modify photographs.
- Do not modify existing labels or experiment runs.
- Add no automated tests, per the user's explicit request.
- Publish only fully validated, timestamped, immutable output directories under
  `/Users/petrnikitin/Documents/Projects/photo-refs/peakshot-reference-exports/`.
- Create no intermediate commits; repository instructions permit one final task commit only
  after complete review and final verification.

---

### Task 1: Implement and document the exporter

**Files:**

- Create: `tools/peakshot_reference_export/export.py`
- Create: `tools/peakshot_reference_export/README.md`
- Modify: none
- Test: none, per explicit request

**Interfaces:**

- Consumes: the fixed Peakshot event endpoints and local JPEG filenames.
- Produces: `main() -> int`, called by `raise SystemExit(main())`.
- Produces: a timestamped directory below
  `/Users/petrnikitin/Documents/Projects/photo-refs/peakshot-reference-exports/` containing
  `peakshot-person-photo-map.csv`, `peakshot-people.json`,
  `peakshot-photos.json`, and `metadata.json`.

- [ ] **Step 1: Implement bounded HTTP and HTML parsing**

  Create `export.py` with:

  - constants `EVENT_URL`, `ORIGINALS_DIR`, `OUTPUTS_DIR`, `USER_AGENT`, and
    `HTTP_TIMEOUT_SECONDS`;
  - `fetch_html(url: str, *, headers: Mapping[str, str] | None = None) -> str`;
  - `AllPiecesParser`, which extracts one `(piece_id, filename)` tuple from every gallery
    anchor carrying `data-gallery-piece-id` and `data-gallery-title`;
  - `PeopleParser`, which extracts numeric person IDs only from event-local `/people/<id>`
    links;
  - `PersonPiecesParser`, which extracts piece IDs from `data-piece-id` attributes;
  - explicit `ExportError` failures for HTTP, decoding, duplicate, missing, and malformed
    data.

- [ ] **Step 2: Implement collection, joining, and validation**

  Add:

  - `collect_remote_photos() -> dict[str, str]`;
  - `collect_person_ids() -> list[str]`;
  - `collect_person_piece_ids(person_id: str) -> set[str]`;
  - `collect_local_filenames() -> set[str]`;
  - `build_export() -> ExportData`.

  `ExportData` contains the capture time, `piece_id -> filename`,
  `person_id -> filenames`, and `filename -> {piece_id, person_ids}`. Enforce exact,
  case-sensitive equality between remote titles and local `.jpg`/`.jpeg` filenames. Reject
  unknown piece IDs and duplicate remote IDs or filenames. Sort all public collections
  deterministically.

- [ ] **Step 3: Implement atomic artifact publication**

  Add `write_export(data: ExportData) -> Path` that:

  - creates a hidden staging directory below `OUTPUTS_DIR`;
  - writes UTF-8 JSON with indentation, sorted keys, and trailing newlines;
  - writes CSV columns `person_id,piece_id,filename`;
  - writes metadata containing source URL, UTC capture time, originals directory, person
    count, photo count, and assignment count;
  - validates written JSON and CSV counts by reopening the staged artifacts;
  - renames staging to `<UTC timestamp>` only after validation;
  - removes staging on failure;
  - refuses to overwrite an existing final directory.

- [ ] **Step 4: Add progress and command behavior**

  Implement `main()` so a normal run reports the all-photo count, person count, current
  person progress, assignment count, and final output path. Send actionable errors to
  standard error and return a nonzero exit status without a traceback for expected
  `ExportError` failures.

- [ ] **Step 5: Document usage and label semantics**

  In `README.md`, document:

  ```bash
  python3 tools/peakshot_reference_export/export.py
  ```

  Explain the fixed live event and originals paths, all four formats, immutable timestamped
  outputs, failure conditions, and that Peakshot clusters are algorithmic reference labels
  rather than manually verified ground truth. Note that one filename may have multiple
  person IDs.

- [ ] **Step 6: Run live verification**

  Run:

  ```bash
  python3 tools/peakshot_reference_export/export.py
  ```

  Expected: a successful timestamped export covering exactly 1420 local and remote photos,
  with no inventory mismatch.

  Parse all generated formats and compare metadata counters to the actual JSON/CSV contents.
  Do not retain a failed or partial output.

- [ ] **Step 7: Run repository checks applicable to the utility**

  Run:

  ```bash
  .venv/bin/ruff format --check tools/peakshot_reference_export/export.py
  .venv/bin/ruff check tools/peakshot_reference_export/export.py
  python3 -m py_compile tools/peakshot_reference_export/export.py
  git diff --check
  ```

  Expected: every command exits successfully.

- [ ] **Step 8: Self-review and handoff**

  Inspect the complete working-tree diff, confirm no files under `src`, no photographs, and
  no existing labels or experiment runs changed. Report the live export counts and artifact
  path. Leave changes unstaged unless the user separately requests the final commit.
