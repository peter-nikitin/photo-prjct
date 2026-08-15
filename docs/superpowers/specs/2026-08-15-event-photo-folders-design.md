# Event Photo Folders Design

## Status

Approved on 2026-08-15.

- Related architecture: [`docs/architecture.md`](../../architecture.md), photographer ingestion and
  public event gallery.
- Related ADRs: [ADR 0013](../../adr/0013-use-direct-private-object-storage-ingestion.md),
  [ADR 0014](../../adr/0014-keep-stage-2-ingestion-request-driven.md), and
  [ADR 0022](../../adr/0022-use-numbered-gallery-pages.md).
- ADR impact: conforms to the existing direct-upload, request-driven ingestion, gallery eligibility,
  media authorization, and numbered-pagination decisions. It introduces no new infrastructure or
  durable architecture decision.

## Goal

Let an administrator organize one event into named folders, let a photographer assign every file
to a folder or to `Без папки` while adding it to one shared resumable upload queue, and let a public
gallery visitor filter an event by several folders including `Без папки`.

Deliver only this end-to-end critical path. A separate increment will design mass reassignment of
already uploaded photos, filtering that editor by photographer and capture time, and the associated
photographer authorization boundary.

## Scope

### Included

- Administer named folders within one event through Django Admin.
- Assign files from several folder drop zones to one event upload batch and shared queue.
- Persist each upload item's folder through registration, retries, confirmation, and resume.
- Allow photos to belong to exactly one event folder or to no folder.
- Filter a public event gallery by several non-empty named folders and `Без папки`.
- Combine folder selection with the existing event-local capture-time filter.
- Preserve selected folders in numbered gallery URLs and pagination links.

### Excluded

- Moving a queued file between folders on the upload screen.
- Reassigning folders after a photo is uploaded.
- A photographer-facing mass editor, its time/photographer filters, and its permission model.
- Nested folders, cross-event folders, manual folder ordering, folder covers, and folder-specific
  publication state.
- Changes to background photo processing, gallery eligibility, media delivery, selfie search,
  commerce, or storage layout.

## Domain Model

`EventFolder` has a normalized display name and belongs to exactly one `Event`. A photo folder is
not a filesystem or Object Storage prefix: it is event-scoped catalog metadata. Folders are ordered
by name. Names are trimmed, must not be empty, and are unique within an event without regard to
case; the same name may be used by different events.

`Photo.folder` is an optional protected foreign key. `NULL` means `Без папки`. A folder can never
belong to more than one event, and application services must reject assigning a photo to a folder
from another event.

`UploadItem.folder` is also an optional protected foreign key. It records the photographer's
choice durably before object transfer begins. The item and folder must belong to the same event
through `UploadItem.batch.event`; registration and confirmation enforce that invariant. Successful
confirmation copies the exact item folder to the new photo in the same authoritative operation
that publishes the `Photo` relation. Retry and resume never infer or change it.

Protected references prevent deletion of a folder used by a photo or an upload item. An empty
folder may be deleted. An administrator may rename a folder; all upload and gallery surfaces then
show its current name.

## Administration

The event change page in Django Admin contains inline folder rows. An administrator can add,
rename, and delete empty folders without navigating to a separate workflow. Validation reports an
empty or duplicate normalized name on the relevant row. Attempting to delete a referenced folder
is rejected with a clear protected-object message rather than silently moving its contents to
`Без папки`.

No bulk photo reassignment is included in this increment. A referenced folder can be renamed but
not deleted until the later mass editor provides an explicit reassignment path.

## Photographer Upload

The photographer first chooses an event through the existing upload flow. The page then presents
one drop zone labelled `Без папки` followed by every current named folder for that event. Each zone
supports both dropping JPEG files and opening the system file picker. Files added from several
zones contribute to the same current event batch, overall 10,000-file limit, transfer concurrency,
progress totals, status groups, and finalization.

Every newly added client item captures the target folder ID or `NULL`. Registration sends that
choice per item; the server verifies that a non-null folder belongs to the batch event. The shared
queue labels each item with its current folder name or `Без папки`. Assignment cannot be changed
from the upload page after the file is added. A failed or retried transfer retains it.

Resume manifests include each item's folder identity and current display label so a refreshed page
restores one accurate mixed-folder queue. A rename appears under the new name. A stale client that
tries to register against a missing or foreign folder receives a sanitized item-registration error
that tells the photographer to add those files again from a current target; it must not silently
fall back to `Без папки`.

### Drag-and-drop feedback

While files are dragged over the folder-zone collection, only the current valid target is
emphasized and the other zones are visually subdued. The target shows explicit copy such as
`Загрузить в «Финиш»` or `Загрузить без папки`. Highlighting covers the entire target, remains
stable while moving across its children, and clears immediately on drop, drag exit, or cancellation.
Files receive an assignment only from the zone where the drop actually completes. This feedback is
required to make the destination unambiguous and prevent accidental folder assignment.

## Public Gallery Filtering

The event detail page derives a stable list of folder choices from the base public gallery
eligibility queryset before applying any active folder or capture-time filter:

- show a named folder only when at least one base-gallery photo belongs to it;
- show `Без папки` only when at least one base-gallery photo has no folder;
- hide the complete folder control when no non-empty named folder exists;
- when at least one non-empty named folder exists, keep every eligible choice visible even if only
  one choice exists.

Active filters never change this list. A folder remains visible when its intersection with the
selected capture-time range or other selected folders has no results. Uploaded photos that are not
eligible for the public gallery do not make a choice visible.

Choices are checkboxes. With none selected, the gallery shows all base-eligible photos. Selecting
several choices applies `OR` within the folder group; `Без папки` is an explicit nullable-folder
choice in the same group. The resulting folder predicate combines with the existing capture-time
range using `AND`.

Selection is encoded in stable GET parameters. Folder IDs from another event, deleted folders,
malformed values, and other unknown values are ignored and cannot widen event authorization.
Numbered pagination operates on the filtered queryset and preserves all valid folder and time
parameters in page links. A zero-result combination keeps the stable controls visible, shows an
accessible empty state, and offers `Сбросить фильтры`.

## Data Flow

1. An administrator creates folders inside one event.
2. The upload page obtains that event's current ordered folder list.
3. The photographer adds files through one or more folder-specific zones.
4. The browser creates one shared batch and registers every item with its target folder or `NULL`.
5. Existing direct Object Storage transfer, confirmation, retry, and finalization continue; Django
   persists the item folder on the confirmed photo.
6. The event page builds stable non-empty filter choices from its base eligible gallery queryset,
   validates GET selections against those event choices, applies folder and time predicates, then
   paginates.

## Failure and Security Semantics

- A folder identifier is never authority on its own. Admin, upload, confirmation, gallery, media,
  and event boundaries continue to validate the owning event.
- Cross-event or stale folder assignment fails registration without creating an item under a
  different target.
- Confirmation refuses an inconsistent item/folder/event relation instead of publishing a
  misfiled photo.
- Resume reports the durable server assignment; browser-local state cannot overwrite it.
- Deleting a referenced folder fails closed. There is no implicit cascade or conversion to
  `Без папки`.
- Folder query parameters only narrow the already-authorized base gallery. They never make a paid,
  unpublished, unprocessed, or cross-event photo eligible.
- Existing upload ownership, permission checks, signed media redirects, and selfie-result
  authorization are unchanged.

## Validation Contract

Focused automated coverage must prove:

- folder name normalization, event-scoped case-insensitive uniqueness, ordering, and protected
  deletion;
- nullable folder relations and rejection of cross-event assignment in registration and
  confirmation;
- one batch registers, uploads, confirms, and resumes items from several named folders plus
  `Без папки` without splitting progress or finalization;
- queue responses and resume manifests retain the durable assignment across retry and rename;
- stale and foreign folder IDs fail clearly and never fall back to no folder;
- drag enter/leave/drop behavior highlights exactly one zone, displays its explicit destination,
  clears correctly, and assigns dropped files to that zone;
- existing picker-based upload remains keyboard-accessible for every zone;
- gallery choices include only folders and `Без папки` variants containing base-eligible photos;
- choices remain stable while folder and capture-time filters change, including zero-result
  intersections;
- no selection returns the full base gallery, multi-selection uses `OR`, and time combines with
  folder selection using `AND`;
- malformed, deleted, and cross-event query values cannot expose photos or break the page;
- numbered page links preserve valid filter state, reset removes folder and time parameters, and
  empty results remain accessible;
- when no non-empty named folder exists, the folder control is absent;
- desktop and mobile visual coverage shows multiple drop zones, an active unambiguous drop target,
  mixed-folder queue labels, public checkboxes, and stable zero-result controls.

## Acceptance Criteria

- An administrator can manage event-scoped folder names from the event admin page and cannot delete
  a referenced folder.
- A photographer can add files to several named folders and `Без папки` on one page and complete or
  resume them through one shared queue.
- The current drop destination is visually unmistakable before files are released.
- Every confirmed photo has exactly the folder selected for its upload item or no folder.
- A public visitor can select several stable, non-empty folder choices, combine them with the
  capture-time range, paginate, copy the URL, reset filters, and receive only base-authorized event
  photos.
- Empty choices never appear, active filters never make choices disappear, and the folder control
  is absent when the event has no non-empty named folder.
- Existing ingestion reliability, gallery eligibility, media authorization, processing, and selfie
  search behavior remain unchanged outside the new folder dimension.
