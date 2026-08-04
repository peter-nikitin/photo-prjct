# Resumable Photographer Upload Design

- Date: 2026-08-01
- Status: Proposed for written review
- Owner: project maintainer
- Related architecture: [Photo ingestion and indexing](../../architecture.md#photo-ingestion-and-indexing)
- Related ADRs: [0012](../../adr/0012-use-django-photographer-permissions.md),
  [0013](../../adr/0013-use-direct-private-object-storage-ingestion.md), and
  [0014](../../adr/0014-keep-stage-2-ingestion-request-driven.md)
- Related specification:
  [Stage 2 photographer upload](2026-07-13-stage-2-photographer-upload-design.md)
- ADR impact: Conforms to ADR 0012, ADR 0013, and ADR 0014

## Goal

Let a photographer return after closing or leaving the upload page, find an unfinished owned
batch, reselect the original files, and upload only the files that PostgreSQL does not already
record as confirmed. At the same time, replace the unhelpful last-20-files queue with a
state-oriented view and keep the summary layout stable while counts and progress change.

The browser still cannot retain access to local files after the page closes. Resume therefore
requires the photographer to select the source files again through the normal file picker.

## Current behavior and problem

`UploadBatch` and `UploadItem` already preserve ownership, event, original filename, expected size,
per-item status, and confirmed `Photo` linkage. The upload page does not read that state: every
visit renders an empty page and the browser coordinator can only create a new batch. The current
queue renders only the most recent 20 in-memory items, which hides useful failures and active work
inside large selections. Changing counter values also alters the right summary's geometry during an
upload.

The accepted Stage 2 design deliberately excluded queue restoration after a tab closed. This
specification replaces only that product boundary. It retains browser-managed, request-driven
transfer and does not introduce automatic transfer without an open page.

## Selected design

### Unfinished upload history

The upload page shows the current photographer's unfinished batches before the new-upload form.
An unfinished batch is an owned batch that is not `completed`, including `created`, `uploading`,
`partial`, and `failed` batches that still contain at least one item without a confirmed `Photo`.

Each card shows:

- event name;
- creation date and last activity;
- confirmed count and expected total;
- failed or unresolved count; and
- a `Continue upload` action.

Cards are ordered by most recent activity. Completed batches are not shown. Existing ownership and
permission rules remain authoritative: a non-superuser cannot discover or resume another user's
batch.

### Resume interaction

Selecting `Continue upload` fixes the target batch and event, then opens the normal multi-file
picker. The photographer may select the whole original set. The browser receives a bounded resume
manifest containing only safe matching metadata and item state; it never receives storage keys,
credentials, ETags, or another photographer's records.

The coordinator matches selected local files to manifest items before requesting upload grants:

1. Match by normalized original filename, exact byte size, and client-reported last-modified time.
2. For a unique match, bind the local `File` to the existing `UploadItem`.
3. For a group with multiple candidates sharing that metadata, compare the stored SHA-256 values
   that were calculated only for ambiguous files when the batch was first registered.
4. If the match remains ambiguous or an older batch has insufficient ambiguous-match metadata,
   place the candidates in `Needs attention` and do not silently skip or upload one as another.

For a pre-feature batch without last-modified metadata, a unique filename-and-size pair may still
resume. A non-unique legacy pair is `Needs attention`; adding broader legacy inference is outside
this MVP.

The matching fields identify a local file only within one selected batch. They are not an identity
contract across batches, events, or photographers.

For an item already confirmed as `uploaded` with a `Photo`, resume marks it complete locally and
does not request a grant or transfer its bytes. A matched `pending`, `authorized`, or `failed` item
is returned to the existing upload path with a fresh authorization as required. An interrupted file
is uploaded again from byte zero; this design does not resume partway through one object.

Manifest items not present in the new selection remain unresolved in the durable batch and can be
supplied during a later resume. Files selected by the photographer that do not belong to the
manifest are not silently added to the resumed batch; the page identifies them as not part of this
upload and leaves creation of a new batch as a separate action.

### Minimal hybrid duplicate handling

The normal path does not hash every selected file. On initial selection, the coordinator groups
files by filename, size, and last-modified time. It calculates SHA-256 only for groups containing
more than one candidate and sends that fingerprint with registration for those ambiguous items.
On resume it repeats hashing only for ambiguous candidate groups that require a fingerprint match.
Files with the same metadata and the same SHA-256 are treated as an equivalent multiset: confirmed
server items consume the same number of selected candidates first, and remaining candidates bind
to the remaining unfinished items. The coordinator does not need to distinguish two byte-identical
copies from one another.

This is deliberately batch-local duplicate handling. The design does not search for equal media in
other batches or events, infer identity from Object Storage ETags, or require a media-wide checksum
index. Byte-identical candidates may be assigned interchangeably within their batch-local multiset
because the maintainer explicitly accepts automatic duplicate handling for this workflow.

### Queue presentation

The queue is organized by actionable state instead of showing the last 20 records:

- `Needs attention`: failures, unmatched ambiguities, and selected files outside the manifest;
- `Uploading`: active transfers with per-file progress;
- `Waiting`: matched items awaiting a transfer slot and unresolved manifest items not reselected;
- `Uploaded`: server-confirmed items.

`Needs attention` and `Uploading` are expanded by default. `Waiting` and `Uploaded` are collapsed
summaries by default and may be expanded without rendering all 10,000 rows at once. The page must
keep DOM work bounded when a group is opened, through a windowed or paginated presentation. Search
across every filename is not part of this MVP.

The summary column has a stable desktop width and does not derive its width from current counter
text. Count and percentage values use tabular numerals, and space for all summary rows is reserved
for every upload state. Responsive layouts may stack the summary below the controls, but count
changes must not cause horizontal movement or repeated vertical reflow of the upload controls.

## Data and interface contract

`UploadItem` persists the client-reported last-modified time used for batch-local matching and an
optional SHA-256 used only for ambiguous metadata groups. These values are untrusted hints for
matching local selections; existing Object Storage verification and confirmation remain the only
authority that bytes became a valid confirmed photo.

The server exposes owned, permission-protected read operations for:

- listing unfinished batches with aggregate counts; and
- reading one unfinished batch's resume manifest.

The manifest exposes item identity, original filename, expected size, last-modified time,
optional ambiguous-file fingerprint, and a public upload state. It omits incoming and final keys,
authorization details, storage verification details, and `Photo` storage metadata.

Resume mutations reuse the existing item row and confirmation semantics. They must remain
idempotent and must not create a second `Photo` for an already confirmed item. Batch aggregate
state continues to be derived from persisted item state rather than client counters.

## Failure semantics

- Closing the page stops active transfers but does not discard the batch or confirmed progress.
- An expired upload grant is replaced through the existing authorization flow.
- A previously failed matched item may be retried and returns its batch to an active state.
- A selected set missing some manifest files leaves those items waiting for a later resume.
- An ambiguous match is visible and inert until the user supplies an unambiguous selection; the
  system does not guess.
- A control or storage failure preserves the server's last confirmed counts. Local byte progress is
  advisory and disappears after leaving the page.
- Cleanup of stale unconfirmed objects remains governed by ADR 0013. Resume never treats an
  unconfirmed incoming object as a completed upload.

## Scope

### In scope

- An owned unfinished-batch list on the photographer upload page.
- Resume after ordinary page navigation, reload, tab close, or browser restart.
- Reselection of the original files with automatic skipping of server-confirmed items.
- Minimal batch-local hybrid matching and duplicate handling.
- State-grouped, bounded queue rendering.
- Stable summary geometry during upload.
- Existing direct Object Storage transfer, bounded concurrency, retries, and confirmation.

### Out of scope

- Retaining local `File` access or credentials in local storage or IndexedDB.
- File System Access API folder handles.
- Transfer that continues after the page closes.
- Byte-range or multipart continuation within one file.
- Global duplicate detection across batches, events, or photographers.
- Combining multiple unfinished batches.
- Adding newly selected files to an existing batch.
- Filename search and exhaustive history of completed batches.
- A worker, broker, service worker, desktop uploader, or CLI uploader.

## Rejected alternatives

### Filename and size only

This is fastest but can silently bind two different files with the same common camera filename and
size. Last-modified time is a cheap additional discriminator, with hashing reserved for actual
ambiguity.

### Hash every file before upload

This gives a uniform content identity but delays the normal path by reading the entire selection,
which can be hundreds of gigabytes. The current requirement is continuation of one known batch,
not a general media deduplication system.

### Persist folder access in the browser

This could remove repeated selection in supporting browsers but adds a browser-specific permission
and recovery path. The accepted MVP keeps the standard file picker and explicit reselection.

## Architecture and ADR impact

The design conforms to ADR 0012 by retaining Django permission and owned-batch isolation. It
conforms to ADR 0013 because media bytes still travel directly to a constrained private incoming
object and only Django verification can confirm and promote a photo. It conforms to ADR 0014
because the open browser still owns bounded transfer concurrency and every control operation
remains request-driven; no ingestion worker or automatic closed-browser continuation is added.

The matching algorithm, resume read model, and grouped presentation are reversible implementation
details within those accepted boundaries. No new or superseding ADR is required.

## Acceptance criteria

1. Returning to the upload page shows only the signed-in photographer's unfinished batches, newest
   activity first, with event, dates, confirmed count, total, and unresolved count.
2. Continuing a batch requires selecting local files again and does not claim that the browser
   retained file access.
3. Reselecting the original set binds unique metadata matches, skips every server-confirmed item,
   and uploads matched unfinished items through the existing bounded queue.
4. Normal unique files are not hashed. SHA-256 is calculated and stored only for ambiguous
   filename-size-last-modified groups and used again only when such a group requires resume
   matching.
5. Ambiguous matches are shown under `Needs attention` and are not silently transferred or marked
   complete.
6. Missing local files remain unresolved and may be supplied by a later resume; selected extra
   files are not silently inserted into the batch.
7. Resume reuses `UploadItem` rows and cannot create a second `Photo` for a confirmed item.
8. The queue prioritizes `Needs attention` and `Uploading`, collapses large waiting and uploaded
   groups, and keeps rendered rows bounded for a 10,000-file batch.
9. Summary width, reserved rows, and tabular numerals prevent count changes from shifting the
   desktop layout; responsive stacking remains usable at the existing supported widths.
10. Anonymous, permissionless, and cross-owner list, manifest, and mutation requests retain the
    existing fail-closed authorization behavior.
11. Tests cover the return-and-resume critical path, confirmed-item skipping, missing selections,
    an ambiguous duplicate group, idempotent confirmation, bounded rendering, and stable summary
    visual states.
