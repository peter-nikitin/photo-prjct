# Event photo bib editing and filtering

- Date: 2026-10-01
- Status: Approved
- Owner: project maintainer
- Related architecture: [Current architecture](../../architecture.md), especially the Django/PostgreSQL control plane and private event photo workspace
- Related ADRs: [ADR 0001](../../adr/0001-django-modular-monolith.md), [ADR 0002](../../adr/0002-postgresql-system-of-record.md), [ADR 0017](../../adr/0017-use-django-polled-photo-processing-jobs.md), and [ADR 0033](../../adr/0033-keep-durable-knowledge-test-executable-contracts.md)
- ADR impact: Conforms to ADRs 0001, 0002, 0017, and 0033. The change keeps Django and PostgreSQL authoritative, preserves immutable processing attempts, and adds a reversible operator workflow over the existing current bib projection. No new or superseding ADR is required.
- Extends: [Production bib recognition and event-scoped number search](2026-09-12-bib-number-production-search-design.md), specifically its former exclusion of production manual correction and its current-projection reader contract
- Product job: [PJ-007 — Customer — Find photos by bib](../../product-jobs.md#pj-007--customer--find-photos-by-bib)

## 1. Outcome

An authorized operator can inspect and correct the current bib numbers directly on each photo card
in the private event photo workspace. The operator can add a missed number after a successful empty
recognition, replace an incorrect number, or remove every incorrect number from a photo. The public
exact-number search immediately reads the resulting current `BibReading` rows.

Every manual mutation records append-only `before -> after` evidence for later quality analysis.
The log is not an override source, is not consulted by search, and is not consulted by the worker.

## 2. Scope

### Included

- Read-only bib numbers below each photo in the existing `/manage/events/<event_id>/photos/`
  card grid.
- A downward-opening overlay editor anchored to one photo card.
- One atomic save that can add, replace, merge, or delete the photo's current numbers.
- Append-only operator, time, source-attempt, and `before -> after` evidence for each mutation.
- Exact number filtering with the existing public `bib` query contract.
- An explicit administrative `without_bib=1` filter for applicable photos with zero current
  `BibReading` rows.
- Focused model, form, service, view, template, JavaScript, style, and regression coverage.

### Excluded

- Any Django Admin event or order-page change.
- Worker changes, log-aware recognition, repeat recognition, automatic reprocessing, or backfill.
- Conflict handling between a future backfill and existing manual changes.
- Fuzzy, partial, case-insensitive, integer-normalized, or cross-event number search.
- Editing processing attempts, worker result JSON, candidate evidence, or processing status.
- A separate override projection, approval workflow, comment field, or review queue.
- Displaying the change history in this first interface.

## 3. Existing authority and manual mutation

`ProcessingAttempt` keeps the immutable bounded worker result. `BibReading` remains the mutable
current exact-search projection. Manual editing changes the current `BibReading` rows directly; it
does not rewrite the attempt result or claim that the worker produced the corrected value.

The worker's existing projection replacement behavior remains unchanged. Production currently has
no repeat-recognition operation. A future manual backfill or reprocessing feature must define its
own logging and collision behavior before it is introduced.

Manual editing is available only for a photo whose immutable bib-processing policy requested bib
recognition and whose current accepted bib attempt completed successfully. This includes an empty
successful result. Pending, failed, cancelled, and bib-disabled photos remain visible in the
workspace but do not expose the editor.

## 4. Change evidence

Add an append-only `BibReadingChange` model with:

- `photo`: protected reference to the affected photo;
- `source_attempt`: protected reference to the accepted successful bib attempt used by the current
  projection;
- `before_number`: nullable 1-16 ASCII digit string;
- `after_number`: nullable 1-16 ASCII digit string;
- `changed_by`: protected reference to the authenticated operator;
- `created_at`: server-assigned timestamp.

Exactly one of these shapes is valid:

| Mutation | `before_number` | `after_number` |
| --- | --- | --- |
| Add | `NULL` | new number |
| Replace | old number | new number |
| Delete | old number | `NULL` |

Both values cannot be null. When both are present they must differ. Stored values satisfy the same
1-16 ASCII-digit contract as `BibReading.number`; leading zeroes remain significant. The change row
does not reference a `BibReading` row because delete and duplicate-merge operations may remove that
row.

Change rows are analytics evidence only. Neither current-number reads nor recognition behavior
depend on them.

## 5. Atomic edit interface

The server accepts one event-scoped photo edit containing:

- every existing `BibReading` identifier and its submitted value; and
- zero or more new-number values.

The interface treats a blank submitted value for an existing reading as deletion and ignores a
blank new-number row. Every non-blank value is stripped and validated by the existing bib-number
contract. Duplicate final values are invalid except when a replacement targets another current
reading: that operation merges the two memberships by deleting the replaced row and retaining the
already-existing target row.

The mutation module performs one PostgreSQL transaction. It locks the event-scoped photo and its
current readings, verifies the photo policy and accepted successful source attempt, rejects missing
or foreign reading identifiers, derives the final set, records one change row per add, replacement,
merge, or deletion, and applies the current `BibReading` mutations. A validation failure changes
nothing.

Manual additions create a `BibReading` tied to the current accepted successful bib attempt and use
bounded evidence identifying the row as manually added. Replacements retain the existing reading's
source attempt and candidate evidence; the separate change row records the human correction.

The module exposes one small event/photo-scoped save interface. Views and JavaScript do not
implement mutation rules.

## 6. Photo-card interaction

In read mode, current numbers appear below the image as compact wrapping labels. A photo with zero
numbers explicitly says that no numbers were found. Eligible cards provide one `Редактировать`
control.

Editing opens a small overlay below the photo without changing the card or grid height. The overlay
is positioned relative to the card, opens downward above following cards, and uses the card width on
narrow screens and a bounded compact width otherwise. Existing and newly added inputs are arranged
vertically.

The overlay contains:

- one input for each current number;
- `Добавить номер`, which appends one empty input;
- one `Сохранить` action; and
- an inline error area.

There is no implicit close through an outside click or Escape. The editor returns to read mode only
after a successful save. Failed validation keeps the overlay and submitted values visible. Saving
one card never mutates another card. Multiple numbers are supported without placing a long list of
inputs in normal card layout.

The POST endpoint is CSRF-protected, never cached, restricted to the existing event-photo change
capability, and verifies the event and photo relationship before disclosing or changing state. A
successful JSON response returns the complete current number list used to rerender that card.

## 7. Event-photo filters

The management filter reuses `BibSearchForm` and the existing `bib` query parameter. Trimming,
validation text, 1-16 ASCII-digit rule, leading-zero behavior, and exact
`bib_readings__number=<value>` membership are the same as the public event page. The management
query begins with its authorized event-photo queryset, so private and hidden administrative rows
remain inspectable; only the bib predicate is shared with public search.

The separate `without_bib=1` mode selects photos whose immutable policy requested bib recognition
and which currently have no `BibReading` rows. `bib` and `without_bib=1` are mutually exclusive; an
ambiguous request is a visible filter error and cannot drive a bulk action. The valid filter is
preserved in canonical URLs, pagination, result refreshes, and all-filtered bulk selections.

After the last current number is manually deleted, the photo matches `without_bib=1`. After a
number is manually added, it no longer matches. The change log does not affect either filter.

## 8. Failure and concurrency semantics

- Invalid digits, duplicates, unknown reading identifiers, wrong-event photos, ineligible photo
  policy, or absence of an accepted successful attempt return a form error and no mutation.
- Concurrent edits serialize on the photo and current readings. A request based on missing or
  changed reading identifiers is rejected rather than silently broadening its edit.
- Database uniqueness remains the final guard against duplicate photo/number membership.
- HTTP, JavaScript, or rendering failures never partially write current readings or change rows.
- Recognition and processing status are not changed by a manual edit.

## 9. Privacy, permissions, and analytics

Only users with the existing private event inspection and photo-change capabilities can use the
editor. Photographer-only upload access does not expose readings or mutation endpoints. Responses
contain photo identifiers and current bib numbers but no object-storage key, worker payload, or
private media credential.

The append-only log provides event/photo, accepted attempt, actor, timestamp, and before/after
dimensions for offline analysis. It is not shown publicly and does not change public authorization.

## 10. Acceptance criteria

1. Eligible cards show zero, one, or many current numbers below the image without rendering inputs
   until editing begins.
2. The overlay opens downward, stacks inputs vertically, and does not move neighboring cards.
3. A successful save can add after an empty successful recognition, replace a number, merge into an
   existing number, delete one number, or delete the final number.
4. Each mutation writes the corresponding append-only before/after evidence with actor, photo,
   source attempt, and timestamp in the same transaction.
5. A failed save changes neither current readings nor audit evidence and leaves the editor open.
6. The public exact search immediately reflects current rows without reading the change log.
7. The management `bib` filter has the same validation and exact-match semantics as the public
   `bib` form.
8. `without_bib=1` returns only bib-applicable photos with zero current readings and follows manual
   add/delete changes.
9. Unauthorized, photographer-only, cross-event, and malformed requests cannot inspect or mutate
   another photo's numbers.
10. Existing worker, attempt, publication, gallery, folder, visibility, upload, and processing
    behavior remains unchanged.

## 11. Rejected alternatives

### Separate manual override projection

A separate current manual-number graph would preserve automatic rows untouched but add precedence,
query, synchronization, and correction-history machinery that the requested direct current-state
edit does not need.

### Worker consultation of the change log

Production has no repeat-recognition operation. Making the worker interpret operator history would
add an unused processing contract. Future backfill work must design and log its own behavior when a
real operation exists.

### Inputs permanently visible on cards

Group photographs can contain many numbers. Persistent inputs make the grid tall and difficult to
scan. Compact read labels plus an overlay keep ordinary browsing stable.

### New administrative bib-search semantics

The public exact `bib` contract already defines the accepted validator and lookup. A second number
search would create drift without providing a product capability.
