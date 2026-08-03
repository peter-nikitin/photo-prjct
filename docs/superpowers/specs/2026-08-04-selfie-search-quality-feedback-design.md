# Selfie Search Quality Feedback Design

- **Status:** Approved in conversation and written review on 2026-08-04
- **Date:** 2026-08-04
- **Owner:** FindMe Photo
- **Related architecture:** [`docs/architecture.md`](../../architecture.md), current public
  event-scoped selfie search, private Object Storage media, PostgreSQL authority, and biometric
  security, privacy, and legal boundaries
- **Related product jobs:**
  [`PJ-008 — Customer — Find photos by face`](../../product-jobs.md#pj-008--customer--find-photos-by-face)
  and
  [`PJ-012 — Customer — Report selfie-search quality`](../../product-jobs.md#pj-012--customer--report-selfie-search-quality)
- **Related specifications:**
  [`2026-07-30-public-selfie-search-design.md`](2026-07-30-public-selfie-search-design.md),
  [`2026-08-02-asynchronous-selfie-search-submission-design.md`](2026-08-02-asynchronous-selfie-search-submission-design.md),
  and
  [`2026-08-02-contacts-legal-documents-cookie-notice-design.md`](2026-08-02-contacts-legal-documents-cookie-notice-design.md)
- **Related ADRs:**
  [ADR 0001](../../adr/0001-django-modular-monolith.md),
  [ADR 0002](../../adr/0002-postgresql-system-of-record.md),
  [ADR 0006](../../adr/0006-yandex-object-storage-media.md), and
  [ADR 0019](../../adr/0019-use-public-event-selfie-search.md), and
  [ADR 0023](../../adr/0023-store-consented-selfie-search-feedback.md)
- **ADR impact:** **Conforms to ADR 0023.** The search itself continues to conform to ADR 0019: its
  server-side temporary selfie is deleted before terminal publication and its query embedding is
  never persisted. ADR 0023 governs the separate consented feedback copy, plaintext contact,
  result-photo labels, lifecycle, and staff-access boundaries.

## Outcome

After one selfie search reaches a terminal outcome, the customer can submit one feedback record
about that exact search without selecting the same selfie again.

The browser keeps a short-lived local copy of the file selected for the search. The search pipeline
still deletes its server-side temporary copy before publishing a terminal result. Only when the
customer opens feedback, supplies a contact, accepts the explicit data-processing consent, and
submits does the browser upload its local copy as a separate private feedback attachment.

There are two feedback experiences:

1. A compact problem report for a search that failed or produced no visible result photos. It
   contains the automatically attached search selfie, a contact, and consent.
2. A result-marking mode for a search with visible result photos. It adds `Я есть` / `Меня нет`
   controls to the existing result cards, reports marking progress, and submits any completed
   marks with the same selfie, contact, and consent.

One search accepts at most one feedback record. A feedback record evaluates only that search; it
cannot combine several searches or several selfies.

## Success criteria

The feature succeeds when:

- a customer can report a terminal failed or empty search without answering questions about a
  result that does not exist;
- a customer can mark any subset of a non-empty result as `Я есть` or `Меня нет` directly on the
  original result cards, without a duplicate photo grid;
- the form displays `Размечено N из M фотографий` and allows submission at `0 из M`;
- the attachment is the browser's local copy of the selfie selected for this search, with no file
  picker or second upload decision;
- the existing server-side search selfie cleanup and non-persisted query-vector contract remain
  unchanged;
- the customer gives explicit consent before the feedback selfie and contact become server-side
  records;
- the customer can explicitly disable all future feedback prompts in the current browser without
  contacting the operator or creating a server-side preference;
- staff can inspect one bounded feedback record and its private attachment without receiving a
  permanent public media URL; and
- the bucket lifecycle removes feedback selfies on schedule without a new application cleanup
  workflow.

## Scope

### Included

- Local browser preservation of the selected search selfie for seven days.
- One feedback entry point on every terminal bearer-result page when the matching local selfie is
  available in that browser.
- A compact problem-report form for terminal failures and zero visible result photos.
- An optional card-marking mode for non-empty ready results.
- Exactly two labels per result photo: `Я есть` and `Меня нет`; unmarked is a distinct initial
  state, not a third answer.
- One contact field accepting a phone number, Telegram handle, or email as plain text.
- One mandatory, initially unchecked data-processing consent with a link to the published personal
  data policy.
- One explicit `Не спрашивать больше на этом устройстве` action backed by a versioned browser-local
  preference.
- A separate lifecycle-bound private feedback-selfie object, durable structured feedback rows and
  contact, and staff-only Django Admin inspection.
- Critical-path browser, Django, storage, privacy, authorization, accessibility, and lifecycle
  coverage.

### Excluded

- A manual selfie picker in the feedback form.
- More than one feedback record or more than one selfie per search.
- Feedback spanning several searches, events, or devices.
- Editing or replacing submitted feedback.
- Public display of feedback, aggregate ratings, comments, free-form complaint text, or a general
  support ticket system.
- `Не уверен`, star ratings, ranking sliders, or a separate grid of result thumbnails inside the
  form.
- Automatically changing thresholds, ranking, embeddings, model weights, or saved result
  membership from one feedback record.
- Treating a submitted label as verified identity or using it for named identification.
- Keeping the search pipeline's temporary server-side selfie beyond its existing cleanup gate.
- Feedback submission when the matching local browser selfie is unavailable; there is no manual
  upload fallback in this increment.

## Feedback eligibility and variants

Feedback is available only after a search reaches one of its existing terminal states. Queued,
processing, and cleanup-pending pages do not show the entry point.

The server selects the variant from authoritative current presentation state:

| Search outcome | Feedback variant |
| --- | --- |
| `ready` with at least one currently visible saved-result photo | Result-marking feedback |
| `ready` with zero currently visible saved-result photos | Compact problem report |
| `no_face`, `multiple_faces`, `quality_rejected`, `search_unavailable`, or `failed` | Compact problem report |

“Visible” uses the same current event/photo eligibility rules as the bearer result. The browser
does not choose the variant and cannot label a photo outside the search's saved result.

If feedback already exists for the search, the page shows a submitted confirmation and no new
form. If the local selfie is missing, expired, cleared, or inaccessible, the page does not offer a
file picker and explains that feedback for this search can only be sent from the browser where the
search was started while its local copy remains available.

Before rendering or activating a feedback entry point, the browser checks the opt-out preference.
When opt-out is active, terminal result pages do not show a feedback invitation, form, marking
mode, or missing-local-selfie explanation.

## Preserve the selected selfie locally

### Browser lifecycle

Immediately before the existing search POST, JavaScript stores the exact selected file bytes and
canonical bounded metadata in IndexedDB under a random local identifier. The identifier is kept in
that tab's `sessionStorage`. After the successful redirect, the result page associates the pending
local record with the current bearer-result identity and removes the pending tab entry.

This ordering supports simultaneous searches in different tabs without assigning one tab's selfie
to another result. Failure to use IndexedDB never blocks the search submission: the customer still
receives the existing search experience, but feedback requiring the selfie is unavailable.

The browser checks the feedback opt-out before preserving a new search selfie. When opt-out is
active, it stores no feedback-local selfie or pending association. This check does not change the
ordinary search upload or result flow.

The local record contains only:

- selected file bytes;
- canonical media type;
- byte count;
- creation and expiry timestamps; and
- a SHA-256 digest of the public result token used only to associate the local record with this
  result page.

It contains no query embedding, match scores, contact, labels, signed Object Storage URL, or
analytics data. It expires seven days after selection and is deleted immediately after successful
feedback submission. Page startup opportunistically deletes expired records. Clearing browser
storage, private-browsing eviction, another browser profile, or another device can remove access
earlier.

The local copy does not replace or delay ADR 0019 cleanup. The search upload continues through the
existing temporary private Object Storage path and is deleted before terminal publication exactly
as it is today.

## Explicit browser opt-out

Every compact form and result-marking form includes a secondary action:

> Не спрашивать больше на этом устройстве

Activating it requires no contact, consent checkbox, or server request. The browser stores the
following versioned preference in `localStorage`:

```text
key: findme_selfie_feedback_prompt
value: disabled:2026-08-04
```

The action immediately:

1. exits marking mode and closes the current feedback form;
2. deletes every locally retained feedback selfie and pending association owned by this feature;
3. discards unsent marks; and
4. suppresses feedback invitations and local feedback-selfie preservation for all later searches
   in the same browser profile.

The preference is browser-wide, not limited to one event or search. It is not sent to Django,
stored in PostgreSQL, synchronized across devices, or treated as withdrawal of consent for an
already submitted feedback record. Clearing site data or using another browser/profile removes the
preference and may allow invitations again. There is no account setting or in-product opt-in toggle
in this increment.

If `localStorage` read fails, the feature fails privacy-first for that page: it does not preserve a
new local feedback selfie and does not show a feedback invitation. If writing the opt-out fails,
the current page still closes the form and deletes accessible local feedback data, but it clearly
states that the preference could not be saved and future pages may ask again.

### Submission validation

The feedback endpoint treats browser storage as untrusted input. It revalidates the attachment
using the same accepted decoded formats, byte limit, pixel limit, and integrity checks as the
search upload contract. The endpoint rejects an attachment that does not satisfy the active search
input contract and creates no partial feedback record.

The product promises that its own browser flow reuses the file selected for this search; it does
not make a biometric identity claim about the depicted person.

## Compact problem report

The compact form is used only when no result set can be meaningfully marked. It contains:

- the heading `Помогите улучшить поиск`;
- a short outcome-aware sentence stating that the search failed or found no photographs;
- one required contact field;
- the selfie disclosure;
- the mandatory consent; and
- the submit action.

It does not ask whether the customer was found, whether someone else was found, or how many photos
were correct. Search status, visible-result count, event, model/threshold configuration, and other
existing bounded diagnostic fields come from the linked search record rather than customer input.

The selfie disclosure is:

> К отзыву приложим ваше селфи — то самое, которое вы использовали для этого поиска. Повторно
> выбирать файл не нужно.

## Result-marking feedback

### Entering and leaving marking mode

The non-empty ready-result page offers `Оценить качество поиска`. Activating it opens the feedback
form and switches the existing result gallery into marking mode. Closing the form leaves marking
mode and preserves unsent marks in browser state for the life of the page. Reloading may discard
unsent marks; the server stores nothing before final submission.

There is no second result grid. The ordinary cards, lightbox links, pagination, and download actions
remain the presentation source of truth.

### Per-photo controls

In marking mode, each visible saved-result card gains a control at its lower-left edge, opposite the
existing lower-right download action. It contains two styled choices:

- `Я есть`; and
- `Меня нет`.

Neither is selected initially. Selecting one deselects the other. Selecting the active choice again
returns the card to unmarked. The controls are real buttons with pressed state and an accessible
name tied to that photograph; activating them must not open the lightbox or start a download.

The control labels describe whether the customer appears anywhere in the photograph. A group photo
containing the customer is `Я есть`; `Меня нет` means a false-positive result in which the customer
does not appear. The system stores the label against the immutable `SelfieSearchResult` membership,
not against an arbitrary event photo.

### Progress and optional completion

The form displays:

> Размечено N из M фотографий

`N` is the number of unique result members with either label. `M` is the number of currently
visible saved-result members across all numbered result pages when marking mode began. Labels made
on different numbered pages remain available in browser state until feedback is submitted.

The explanatory copy is:

> Можно отправить отзыв и без полной разметки, но каждая отметка очень поможет улучшить поиск.

Submission is allowed at `0 из M`. The server stores only explicitly marked rows. It must not infer
that an unmarked photo is correct or incorrect. For a complete marking, the count of `Я есть`
answers provides the number of correct photos and the count of `Меня нет` answers provides the
number of false positives. For a partial marking, both counts are explicitly partial and cannot be
reported as full-result precision.

The original high-level questions — whether the customer was found, whether another person was
returned, and how many photos contained the customer — are therefore not separate fields. Complete
per-photo marks answer them without contradiction; partial marks remain useful labelled evidence
without pretending to describe the whole result.

## Contact and consent

The contact is required and is one trimmed plain-text value of at most 254 characters. The UI label
is `Контакт для связи`, with the hint `Телефон, Telegram или email`. The server rejects empty,
control-character, or over-limit values but does not guess the contact channel or require a
channel-specific format.

The contact is stored as plaintext in the private PostgreSQL database. This intentionally avoids a
separate application-encryption and key-management path for a short-lived operational field. It
must not appear in database identifiers, additional indexes, search features, list displays,
exports, metrics, traces, or logs. Database access remains restricted to the application and
authorized operators.

The consent is a required unchecked checkbox placed immediately before submission. Its approved
product copy is:

> Я согласен на обработку моего селфи, контактных данных и оценки результатов поиска для анализа
> качества поиска и связи со мной в соответствии с Политикой обработки персональных данных.

`Политикой обработки персональных данных` links to the packaged and published
`ui/legal/personal-data-policy.pdf`, which the current `/legal/` page names `Согласие на обработку
данных и политика в отношении обработки персональных данных`. The UI also states that the feedback
selfie is automatically deleted by the private bucket lifecycle. The contact, consent record,
structured labels, and bounded search-quality evidence remain stored with the feedback without an
automatic expiry.

The server records the consent fact in a dedicated non-null Boolean column
`personal_data_consent`, as well as the exact consent-text version and acceptance timestamp in
separate columns. A stored feedback row is valid only when `personal_data_consent = true`; a
database check constraint enforces that invariant. A missing or false consent rejects the request
before any feedback selfie or row is created. A generic cookie acknowledgement, use of the search,
or possession of the bearer URL is not feedback consent.

This specification defines the product and storage contract; it does not claim legal review of the
copy. The new ADR and implementation review must verify that the published policy covers these
purposes and periods before activation.

## Data model and invariants

PostgreSQL remains authoritative. One `SelfieSearchFeedback` belongs one-to-one to one
`SelfieSearch` and records:

- immutable feedback UUID;
- search reference and selected feedback variant;
- one plaintext contact value accessible only through the restricted feedback workflow;
- dedicated `personal_data_consent` Boolean fact, constrained to `true` for every stored feedback;
- consent text version and acceptance timestamp in separate columns;
- source search status, matched count, visible result count, and bounded search configuration
  snapshot needed to interpret the report;
- private feedback-selfie object key, canonical media type, byte count, and upload time; and
- creation time.

Each `SelfieSearchFeedbackLabel` belongs to the feedback and one saved `SelfieSearchResult`, with
exactly one value: `present` (`Я есть`) or `absent` (`Меня нет`). A uniqueness constraint permits at
most one label per result member. Database and service validation require the label's result to
belong to the feedback's search.

The database enforces at most one feedback per search. Repeated or concurrent submission returns
the already-submitted outcome and cannot create a second object, consent record, or label set.
Feedback is immutable after successful creation.

The feedback stores no query embedding and does not copy event-photo media. Its link to existing
saved result rows preserves the exact result membership and rank being evaluated.

## Private media and staff access

The feedback selfie is written to a dedicated private Object Storage bucket separate from the
temporary search prefix and permanent event originals. The bucket has no public access, uses
server-side encryption with a dedicated Yandex KMS key, has neither versioning nor Object Lock, and
has a 30-day deletion lifecycle as the authoritative expiry mechanism. Django generates an opaque
random key and owns the feedback metadata. The object key and Object Storage metadata contain
no contact, filename, event slug, search token, or other customer-supplied identifier. No public or
bearer-result media route serves it.

There is no scheduled Django cleanup job or `cleanup_pending` state for a successfully submitted
feedback selfie. After the lifecycle deadline, a missing feedback object is the expected state and
the admin shows `Селфи удалено` without retrying or treating it as an incident. Lifecycle
configuration and real-bucket verification are deployment gates.

Only authorized staff may inspect feedback in Django Admin. Contact and feedback-selfie
access require an explicit view action and create an audit event containing staff identity,
feedback UUID, action, and timestamp. The admin otherwise shows the search outcome, progress,
labels, model/threshold evidence, and timestamps without showing the contact or resolving the
selfie. An authorized detail action reveals the contact and may issue one short-lived exact-object
inline view or download. It never exposes the permanent object key or a reusable public URL.
Contact is absent from list pages and is not searchable, sortable, or exportable.
The existing ML worker receives no feedback object grant and cannot read feedback records.

Ordinary application logs exclude selfie bytes, contact, object key, bearer token, result URL,
signed URL, and query data. Bounded operational events may include feedback UUID, search database
ID, variant, marked count, and visible count.

## Submission flow

1. The terminal result page resolves the bearer search and authoritative feedback variant.
2. JavaScript confirms that IndexedDB contains the local selfie associated with this result.
3. For non-empty ready results, the customer may enter marking mode and label any subset of saved
   result cards across numbered pages.
4. The customer enters a contact, accepts consent, and submits. The browser sends the selfie,
   contact, consent version, and any explicit result-member labels in one multipart request.
5. Django re-resolves the bearer search, rejects non-terminal or already-submitted searches,
   revalidates the selfie, validates every labelled saved-result membership, and checks the
   selected variant against current presentation state.
6. Django uploads the selfie to the feedback prefix and atomically creates the feedback and labels.
   A database failure after upload triggers an exact-object compensating delete.
7. The response confirms submission. Only then does the browser delete the local IndexedDB selfie
   and unsent label state.
8. Object Storage deletes the feedback selfie through the bucket's 30-day lifecycle. PostgreSQL
   retains the feedback, contact, consent, search evidence, and labels without automatic expiry.

The endpoint is CSRF-protected in addition to requiring the unguessable bearer result identity.
Possessing a result link without the matching local selfie does not enable the normal UI flow, but
the server still validates every submitted field and attachment.

## Failure semantics

- Missing or expired local selfie: no manual picker appears; the page explains why feedback is not
  available from this browser.
- IndexedDB unavailable or quota exceeded: the search proceeds unchanged and feedback is
  unavailable for that search.
- Invalid contact or unchecked consent: the form remains open, preserves marks, and shows an
  actionable field error; no object or feedback row is created.
- Invalid, corrupt, or over-limit feedback attachment: reject the submission without a feedback
  row and preserve the search result.
- A label outside the linked saved result, duplicated labels, or an unsupported value: reject the
  complete request without partial labels.
- Feedback-object upload failure: return a retryable error and create no feedback row.
- Database failure after object upload: attempt immediate exact-object deletion. If deletion cannot
  be confirmed, the object is an unreferenced private orphan bounded by the same bucket lifecycle;
  no public state or cleanup record is created.
- Network interruption or ambiguous response: retrying is safe; the one-to-one invariant returns
  the already-created submission instead of duplicating it.
- Opt-out preference active: do not preserve new feedback selfies or render feedback UI in that
  browser profile.
- Opt-out storage write failure: close the current form, delete accessible local feedback state,
  and disclose that suppression could not be saved for future pages.
- Lifecycle configuration or real-bucket verification failure: block feature activation rather
  than adding an application deletion fallback.
- A result photo becoming ineligible before submission: ignore no label silently. Re-render the
  changed result and require the customer to submit only labels that remain current.

## Alternatives considered

### Ask the customer to select the selfie again

This preserves the simplest browser state but adds friction exactly when the customer is reporting
a poor experience and can attach the wrong attempt. Rejected because one search must produce one
unambiguous feedback record without repeating the upload decision.

### Preserve the search selfie on the server until feedback is accepted or declined

This would make feedback available across devices, but it would delay ADR 0019's deletion gate and
change terminal result publication for every search, including customers who never give feedback
consent. Rejected because it expands the existing security model unnecessarily.

### Store the selected selfie locally and upload it only with explicit feedback consent

Selected. It avoids a second picker and preserves server-side search cleanup. The accepted limits
are same-browser/same-device availability, a seven-day local window, and no feedback fallback when
browser storage is missing.

### Ask only three aggregate quality questions

Aggregate answers are quick but can contradict each other and do not identify false-positive
photos. Rejected for non-empty results in favor of optional per-photo marks. Failed and empty
searches retain the compact form because no output exists to label.

### Add a second thumbnail grid inside the form

Rejected because it duplicates the result presentation, complicates pagination and accessibility,
and separates the marking action from the photograph being judged. Marking controls belong on the
existing cards in an explicit mode.

## Acceptance criteria

1. Starting a search with working browser storage and no active opt-out preserves the exact selected
   selfie locally, associates it with only that redirected result in the same tab, and never delays
   or changes server-side search-selfie cleanup.
2. Local preservation failure leaves the existing search fully functional and makes no false claim
   that feedback can include the selfie.
3. Terminal failure and zero-visible-result outcomes render only the compact problem report; they
   ask no result-quality questions.
4. A non-empty ready result enters an explicit marking mode that adds styled `Я есть` / `Меня нет`
   controls to the lower-left of existing cards, opposite download, without adding another photo
   grid or breaking lightbox/download behavior.
5. Each card begins unmarked, supports exactly the two labels, permits clearing a selection, and
   exposes correct keyboard, pressed-state, and accessible-name behavior.
6. Progress reports the unique number marked out of the current visible saved-result membership
   across numbered pages. Submission succeeds with zero, some, or all photos marked.
7. The form clearly says that it will attach the selfie used for this search and provides no file
   picker or multi-search path.
8. Empty contact, unchecked consent, invalid attachment, invalid result membership, and duplicate
   submission produce the specified all-or-nothing behavior. Every stored feedback has an explicit
   `personal_data_consent = true`, consent-text version, and acceptance timestamp; the database
   rejects a false or missing fact.
9. A successful submission creates exactly one immutable feedback record, one separate private
   selfie object, and only explicit valid labels for the linked search; it stores no query vector
   and copies no result photo.
10. The successful response removes the local selfie. Failed or ambiguous submission retains it
    until retry, explicit browser clearing, or seven-day expiry.
11. Public and bearer-result media routes cannot serve a feedback selfie. Authorized staff receive
    only a short-lived exact-object view after Django authorization, and the worker receives no
    access.
12. The dedicated bucket's verified lifecycle deletes feedback selfies after 30 days, while
    PostgreSQL retains the contact, consent, search evidence, and explicit labels without automatic
    expiry. Missing expired selfie objects render as the expected `Селфи удалено` admin state.
13. Logs and errors contain no selfie bytes, contact, object key, bearer token, result URL, signed
    URL, filename, query embedding, or raw sensitive exception payload.
14. Desktop and mobile visual regression confirms that marking controls do not cover download
    controls or essential photo content, progress is readable, and consent and submission remain
    operable at narrow widths.
15. Search submission, terminal publication, result pagination, media authorization, downloads,
    and the normal paid gallery retain their existing behavior when no feedback is submitted.
16. Activating `Не спрашивать больше на этом устройстве` requires no form submission, stores the
    exact versioned preference, exits marking mode, removes all feature-owned local selfies and
    unsent marks, and suppresses both future prompts and future local feedback-selfie preservation
    in that browser profile.
17. An active opt-out produces no server-side preference or analytics event and does not delete or
    alter an already submitted feedback record. Clearing browser data or using another profile
    restores the default eligible behavior.
18. `localStorage` read and write failures follow the privacy-first behavior without blocking the
    ordinary selfie search.

## Delivery boundary

Specification approval selects this product design under accepted ADR 0023 but does not constitute
legal activation. The published personal-data policy must be reconciled with the approved purpose
and retention behavior before the feature is enabled.
