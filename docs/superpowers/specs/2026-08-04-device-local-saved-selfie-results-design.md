# Device-local Saved Selfie-search Results Design

- **Status:** Approved in conversation and written review on 2026-08-04
- **Date:** 2026-08-04
- **Owner:** FindMe Photo
- **Related architecture:** [`docs/architecture.md`](../../architecture.md), current public
  event-scoped selfie search and security, privacy, and legal boundaries
- **Related product jobs:**
  [`PJ-008 — Customer — Find photos by face`](../../product-jobs.md#pj-008--customer--find-photos-by-face)
  and
  [`PJ-014 — Customer — Return to saved selfie-search results`](../../product-jobs.md#pj-014--customer--return-to-saved-selfie-search-results)
- **Related specifications:**
  [`2026-07-30-public-selfie-search-design.md`](2026-07-30-public-selfie-search-design.md)
- **Related ADRs:**
  [ADR 0019](../../adr/0019-use-public-event-selfie-search.md)
- **ADR impact:** **Conforms to ADR 0019.** This feature stores existing non-expiring bearer-result
  URLs only in the customer's browser. It does not change result identity, authorization,
  retention, event isolation, selfie cleanup, query-vector handling, or media access.

## Outcome

After a customer starts one or more selfie searches in a browser, the corresponding event page
shows every result saved by that browser for that event. The customer can reopen any saved result
without selecting or uploading the selfie again.

The list is local to one browser profile on one device. It is not an account, does not synchronize
between devices, and cannot recover a link after browser site data is cleared.

## Success criteria

The feature succeeds when:

- opening an accepted selfie-search result saves it for the result's event in the current browser;
- the event page lists all locally saved results for that event, newest first;
- reopening the same result does not create a duplicate and moves it to the top of the list;
- a customer can reopen a saved result without selecting a selfie again;
- a customer can remove an individual result from the device-local list;
- searches from one event never appear on another event's page;
- unavailable or malformed browser storage does not block the existing selfie-search path; and
- an event page's server-rendered HTML and ordinary links never contain a saved bearer token.

## Scope

### Included

- Automatic device-local saving when a valid result page opens.
- One event-specific `Мои результаты поиска` list below the existing selfie-search form.
- Every saved result for the current event, ordered by most recent open time.
- A local date and time label for each result.
- An `Открыть результат` action and an individual `Удалить с устройства` action.
- URL deduplication and event isolation.
- Graceful handling of unavailable, malformed, or externally modified browser storage.
- Critical-path JavaScript, Django markup, privacy, accessibility, and visual coverage.

### Excluded

- Accounts, authentication, server-side search history, ownership, or recovery.
- Synchronization between devices, browsers, profiles, or private-browsing sessions.
- A global history page or saved-result list outside the matching event page.
- Renaming, searching, filtering, grouping, or bulk deletion of saved results.
- Expiry, revocation, or deletion of the server-side bearer result.
- Validation or refreshing of result status before the customer opens it.
- A fixed history limit or automatic removal of old entries.
- Changes to ranking, result membership, selfie retention, feedback, analytics, or media access.

## Customer experience

The event page keeps the existing search form unchanged. When the current browser has saved
results for the event, a section titled `Мои результаты поиска` appears below the form. When there
are none, the section is omitted; no empty-state message adds noise to a first visit.

Each row is labelled `Поиск от <local date and time>`, using the browser's locale and timezone.
Rows are newest first and contain two buttons:

- `Открыть результат` navigates to the saved bearer result; and
- `Удалить с устройства` removes only that local entry and immediately removes the row.

Removing the final entry hides the section. The action does not delete or revoke the server-side
result. Clearing site data has the same local effect for the entire list.

The page explains the boundary once below the heading:

> Ссылки сохранены только в этом браузере. Любой, у кого есть ссылка, сможет открыть результат.

## Browser storage contract

Use `localStorage` because the feature stores only a small list of strings and timestamps that must
survive closing the tab and browser. IndexedDB is reserved for the existing bounded binary feedback
selfie lifecycle; cookies would unnecessarily send bearer data to Django.

Use one versioned key owned by this feature:

```text
findme_selfie_search_history:v1
```

The value is a JSON array. Each entry contains only:

```json
{
  "eventSlug": "event-slug",
  "resultPath": "/events/event-slug/selfie-search/opaque-token/",
  "openedAt": "2026-08-04T12:34:56.000Z"
}
```

`resultPath` must be a same-origin absolute path matching the canonical result route, with no query
or fragment. The implementation must not persist an absolute origin, event name, result status,
selfie data, query embedding, match data, signed media URL, feedback data, or analytics identifier.

On a result page, the browser derives the canonical path and event slug from server-provided
non-secret page attributes plus the current canonical location. It validates the path before
writing. An existing matching path is replaced with the current timestamp; otherwise a new entry
is appended. The full list is then sorted by `openedAt` descending.

On an event page, the browser parses the array defensively, selects entries whose `eventSlug`
exactly matches the server-provided event slug, and ignores invalid entries. Invalid data must not
be rendered or navigated to. A successful later write replaces malformed storage with the valid
entries available to the feature.

## Bearer-link privacy

A saved result URL is a non-expiring bearer capability under ADR 0019. It may be stored locally
because the customer explicitly chose device-local history, but it must not be exposed to the
event page's analytics or server request.

The event page therefore must not render saved result paths into HTML attributes, anchor `href`
values, text, form values, or URLs. JavaScript keeps the path in its in-memory entry and binds it to
an ordinary button. Activation validates the path again and performs same-origin navigation. The
remove action identifies the entry by its in-memory canonical path without placing that path in the
DOM.

The existing bearer result page remains excluded from analytics. This design sends no local
history to Django and adds no analytics event containing a result path or token.

## Failure behavior

Browser storage is optional enhancement state:

- if `localStorage` is unavailable, full, blocked, or throws, search submission and result viewing
  continue unchanged;
- if saving fails on a result page, the page remains usable and does not show a blocking error;
- if reading fails on an event page, the saved-results section stays hidden;
- if one entry is invalid, it is ignored while other valid entries remain usable;
- if a saved result later returns `404`, the existing result response is authoritative and the
  local entry remains until the customer removes it; and
- opening or removing one entry must not mutate another event's entries.

## Design alternatives

### IndexedDB

Rejected for this increment. It is useful for the existing temporary binary selfie copy, but adds
unnecessary asynchronous schema and lifecycle machinery for a small list of paths and timestamps.

### Cookie-backed history

Rejected because cookies have tight size limits and would attach bearer-result data to ordinary
requests. That expands disclosure without providing cross-device recovery.

### Server-side history

Rejected because it requires an account or a separate recovery credential and ownership model.
Both are outside the accepted device-local requirement.

## Acceptance evidence

Automated evidence must cover:

- first save, multiple results, duplicate reopening, newest-first order, and persistence after a
  new page load;
- strict event separation and preservation of other events during deletion;
- individual deletion and hiding the section after the final deletion;
- unavailable storage, malformed JSON, invalid path, mismatched event slug, and failed writes;
- event-page HTML containing neither a saved token nor a saved-result `href`;
- keyboard activation, meaningful button labels, and focus behavior after deletion; and
- desktop and mobile event-page presentation with multiple saved results.

Django remains responsible only for rendering the event slug and an initially empty, hidden list
container. JavaScript owns device-local persistence and presentation. No migration, model, API,
worker, Object Storage, deployment, or ADR change is required.
