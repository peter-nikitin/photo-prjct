# iPhone Selfie Upload and Rejection Feedback Design

- **Status:** Approved in conversation on 2026-08-04
- **Date:** 2026-08-03
- **Owner:** FindMe Photo
- **Related architecture:** [`docs/architecture.md`](../../architecture.md), public event-scoped
  selfie search, temporary private selfie storage, and the Django-polled worker boundary
- **Related product job:**
  [`PJ-008 — Customer — Find photos by face`](../../product-jobs.md#pj-008--customer--find-photos-by-face)
- **Related specifications:**
  [`2026-07-30-public-selfie-search-design.md`](2026-07-30-public-selfie-search-design.md),
  [`2026-08-02-asynchronous-selfie-search-submission-design.md`](2026-08-02-asynchronous-selfie-search-submission-design.md),
  and
  [`2026-08-02-selfie-upload-guidance-design.md`](2026-08-02-selfie-upload-guidance-design.md)
- **Related ADRs:** [ADR 0019](../../adr/0019-use-public-event-selfie-search.md)
- **ADR impact:** Conforms to ADR 0019. Django still stores one bounded JPEG or PNG under the
  existing private temporary-object contract and the worker still receives that representation.
  Accepting HEIC/HEIF as a source format and normalizing it before storage is a reversible input
  implementation detail; it does not change query retention, worker authority, event isolation,
  bearer access, or result publication.

## Incident Evidence

On 2026-08-03, users reported that selecting a selfie appeared to reload the event page without
starting a search. Read-only staging inspection found that, during the sampled interval, successful
submissions returned the expected HTTP `302`, while repeated submissions from iPhone Safari,
Samsung Browser, and desktop browsers returned HTTP `200` with the event page and created no
search. A controlled unsupported-format submission reproduced the same response signature.

The current form accepts only an exact JPEG or PNG decoder format whose browser-declared MIME type
matches that format. Client-side file selection does not guarantee this contract. In particular,
ordinary iPhone library photos may be HEIC/HEIF or may arrive with a generic or inconsistent MIME
type. The rejected form is rendered in its original position below a large event gallery, so the
correction message is easy to miss and the response appears to be an unexplained reload.

Current logs show HTTP status and worker lifecycle, but they do not record a bounded reason when a
submission is rejected before a search exists. Therefore the incident evidence identifies the
pre-search validation boundary and strongly implicates source-format handling, but it cannot recover
the exact source format of historical requests.

## Outcome

A visitor can select a normal iPhone HEIC/HEIF photo, as well as a JPEG or PNG photo, and reach the
existing selfie-search result URL without converting the file manually. Django determines the
actual decoded format rather than requiring the browser-declared MIME type or filename extension to
match.

If Django cannot accept the file or temporarily cannot store it, the returned event page moves the
visitor to the selfie form, presents one prominent and actionable message, restores an enabled
submission action, and preserves the existing privacy guidance. No failed initial submission
creates a temporary object, search, job, bearer token, or query embedding.

Operators can distinguish successful submission, customer-correctable rejection, and temporary
storage failure from HTTP status and a bounded structured reason without logging the filename,
image bytes, object key, bearer token, or exception payload.

## Scope

### Included

- Source JPEG, PNG, HEIC, and HEIF images selected from supported desktop and mobile browsers.
- Actual-format detection and full decode before accepting a submission.
- Server-side HEIC/HEIF orientation correction and normalization to a bounded RGB JPEG.
- Canonical content types for accepted JPEG and PNG input even when the browser sends a generic or
  inconsistent declared MIME type.
- Existing encoded-source byte and decoded-pixel limits, plus the same byte limit for a normalized
  temporary object.
- A visible, accessible, actionable rejection block at the existing selfie form.
- Distinct HTTP outcomes for success, customer-correctable rejection, and temporary storage
  failure.
- Bounded structured rejection logging before a search exists.
- Critical-path form, submission, template, JavaScript, dependency/runtime, and visual-regression
  coverage.

### Excluded

- Browser-side image conversion.
- AVIF, GIF, WebP, RAW, Live Photo video components, or arbitrary document formats.
- Multi-file submission or choosing a frame from a video.
- Cropping, face detection, or image-quality scoring in the web request.
- Changes to worker models, face ranking, search state, result URLs, bearer authorization, result
  retention, or temporary-object lifecycle.
- Increasing the 20 MiB source/object limit or 25,000,000-pixel limit.
- Persisting original HEIC/HEIF bytes, EXIF, GPS, device metadata, or filenames.
- A general media-conversion service or reuse for photographer ingestion.

## Source Image Contract

### Identification

Django reads at most the configured 20 MiB source limit and asks the installed image decoder to
identify and decode the content. Filename extension and browser-declared MIME type are diagnostic
inputs only; neither is authoritative and neither can make undecodable or unsupported content
valid.

The accepted actual formats are:

| Actual decoded source | Temporary representation | Canonical content type |
| --- | --- | --- |
| JPEG | Original verified JPEG bytes | `image/jpeg` |
| PNG | Original verified PNG bytes | `image/png` |
| HEIC or HEIF | Normalized RGB JPEG bytes | `image/jpeg` |

An accepted JPEG or PNG with a missing, generic, or inconsistent declared MIME type uses the
canonical content type derived from actual content. A file whose actual content is GIF, WebP, AVIF,
RAW, a document, or non-image data is rejected even if its extension or declared MIME type says
JPEG, PNG, HEIC, or HEIF.

### Decode and limits

Every accepted source must satisfy all of these conditions before storage or database creation:

- encoded source size is between 1 byte and 20 MiB inclusive;
- the decoder recognizes an explicitly supported actual format;
- width and height are positive and their product is at most 25,000,000 pixels;
- full pixel decode and integrity verification succeeds; and
- the resulting temporary representation is between 1 byte and 20 MiB inclusive.

Pixel-count rejection must occur before allocating the complete decoded raster whenever the decoder
exposes dimensions safely. Decoder decompression-bomb signals map to the pixel-limit rejection.
Malformed containers, truncated payloads, decode failures, and unsafe dimensions fail closed.

### HEIC/HEIF normalization

The Django runtime uses the maintained `pillow-heif` integration with the existing Pillow stack to
decode HEIC/HEIF. Normalization:

1. applies the source orientation so the stored pixels are upright;
2. converts the decoded frame to RGB, compositing transparency onto a neutral white background if
   necessary;
3. encodes one JPEG at quality `90` with standard optimized encoding;
4. preserves decoded pixel dimensions and does not crop or resize;
5. strips EXIF, GPS, thumbnails, filenames, and other source metadata; and
6. rejects the submission before storage if the normalized JPEG exceeds 20 MiB.

Only the primary still image is used. A Live Photo motion component is neither uploaded separately
nor processed.

## User Experience

The production event page keeps the existing selfie-search block and guidance. The file input's
browser hint includes JPEG, PNG, HEIC, and HEIF, but the server remains authoritative.

On an unsuccessful POST, the response targets the selfie-search section and moves keyboard focus to
one error summary with `role="alert"`. The summary appears before the file input, uses the existing
design system's error treatment, and remains readable without JavaScript. JavaScript may perform the
focus movement but must not be required to display the message. The new document contains an
enabled button with its original label, so a visitor can immediately choose another photo.

The customer-facing messages are:

| Rejection category | Message |
| --- | --- |
| Missing or empty file | `Выберите фотографию для поиска.` |
| Unsupported or inconsistent actual content | `Не удалось прочитать фотографию. Выберите JPEG, PNG, HEIC или HEIF.` |
| Corrupt or truncated supported image | `Фотография повреждена. Выберите другой файл.` |
| Source or normalized object over 20 MiB | `Размер фотографии не должен превышать 20 МиБ.` |
| More than 25,000,000 pixels | `Изображение слишком большое. Уменьшите его так, чтобы ширина × высота были не больше 25 млн пикселей — например, 5000 × 5000.` |
| Temporary Object Storage failure | `Не удалось загрузить фотографию. Попробуйте ещё раз.` |

The response must not claim that a search started. A successful accepted submission retains the
existing immediate redirect and progress/result experience.

## HTTP and Data Flow

1. The visitor selects one source file and submits the existing multipart form.
2. Django enforces the source byte bound, identifies actual content, checks dimensions, and fully
   decodes the image.
3. JPEG and PNG receive their canonical content type. HEIC/HEIF is normalized to JPEG and checked
   against the temporary-object byte bound.
4. If validation fails, Django logs one bounded reason and returns the event page with HTTP `422`,
   the visible correction message, and no storage or database side effect.
5. Django submits the canonical bytes and canonical content type to the existing temporary selfie
   storage boundary.
6. If storage is temporarily unavailable, Django logs `storage_unavailable` and returns the same
   focused form with HTTP `503` and no search or job.
7. After storage succeeds, the existing submission service creates the queued search and job and
   returns the existing HTTP `302` bearer-result redirect.
8. Worker processing, temporary selfie deletion, ranking, terminal publication, and result display
   follow the existing accepted contracts unchanged.

The three expected public submission outcomes are therefore:

- `302`: accepted and queued;
- `422`: customer-correctable file rejection; and
- `503`: temporary storage failure.

CSRF, unpublished-event, and unexpected server failures retain their existing `403`, `404`, and
`5xx` behavior.

## Observability and Privacy

Each expected pre-search rejection emits one structured event named
`selfie_submission_rejected`. Its bounded fields are:

- published event ID;
- reason code;
- encoded source byte count when available;
- canonical actual-format label when decoding identified one; and
- browser-declared content type normalized to a bounded allowlisted label or `other`.

Allowed reason codes are:

- `missing_or_empty`;
- `unsupported_format`;
- `corrupt_image`;
- `source_too_large`;
- `normalized_too_large`;
- `pixel_limit_exceeded`; and
- `storage_unavailable`.

Customer-correctable reasons log at `INFO`; `storage_unavailable` logs at `WARNING`. The event must
not include IP address, user-agent, filename, source bytes, decoded pixels, metadata, storage key,
public token, signed URL, query vector, raw exception text, or traceback for an expected rejection.
Nginx may retain its existing access-log fields and safe bearer-route redaction.

This logging is diagnostic evidence, not analytics or a new long-term biometric record. No
successful-submission log is required beyond existing HTTP, database, and worker evidence.

## Failure Semantics

- An unsupported or corrupt file cannot create a temporary object, search, or job.
- A normalization failure is a customer-correctable corrupt-image rejection unless it represents an
  unexpected application defect, which remains an ordinary server error with sanitized exception
  logging.
- A successfully written temporary object followed by database failure retains the existing
  compensating-delete behavior.
- A temporary storage failure is retryable by submitting the file again; it must not be described as
  a file-format problem.
- A normalized HEIC/HEIF image is indistinguishable from an accepted JPEG to the worker. The worker
  receives no new decoder dependency or source metadata.
- Dependency or decoder initialization failure must fail deployment/runtime checks rather than
  silently removing HEIC/HEIF support from the advertised form.

## Acceptance Criteria

1. A real HEIC/HEIF fixture within both limits is accepted, oriented correctly, stripped of source
   metadata, stored as a bounded `image/jpeg` object, and produces the existing queued-search
   redirect.
2. Real JPEG and PNG fixtures retain their original verified bytes, use canonical content types,
   and produce the existing queued-search redirect even when the browser-declared MIME type or
   filename extension is missing, generic, or inconsistent.
3. Unsupported, corrupt, empty, oversized, excessive-pixel, and normalized-oversize inputs return
   HTTP `422`, show the specified message at the focused selfie form, restore an enabled submission
   action, and create no object, search, or job.
4. Temporary Object Storage failure returns HTTP `503`, shows its distinct retry message at the
   focused form, and creates no search or job.
5. Every expected rejection writes exactly one bounded structured reason with none of the forbidden
   sensitive fields.
6. The file-input hint advertises JPEG, PNG, HEIC, and HEIF; server acceptance remains based on
   decoded content rather than the hint, extension, or declared MIME type.
7. Existing worker request configuration receives only canonical `image/jpeg` or `image/png`, and
   search processing, cleanup-before-terminal-publication, ranking, bearer authorization, and
   result rendering remain unchanged.
8. Focused automated tests cover the source-format matrix, normalization properties, every rejection
   category, HTTP outcomes, storage/database side effects, logging redaction, and JavaScript-enhanced
   focus behavior.
9. Production-screen visual regression covers the error state on mobile and desktop and confirms
   that the alert, input, and enabled action are visible without obscuring existing privacy copy.
10. The production web image can import and decode the pinned HEIC/HEIF dependency in a containerized
    smoke test; ordinary JPEG/PNG behavior and the existing selfie-search end-to-end path remain
    green.

## Rejected Alternatives

### Browser-side HEIC conversion

This adds a large client dependency, consumes memory and battery on the user's phone, varies across
Safari and embedded browsers, and still requires authoritative server validation. It is not the
reliable critical path.

### MIME allowlist expansion without decoding

Trusting `image/heic`, `image/heif`, or a filename would accept spoofed content and send bytes the
existing worker cannot decode. The worker contract requires a verified canonical representation.

### Add HEIC decoding to the worker

Persisting the source HEIC/HEIF and teaching the worker to decode it widens the private job contract,
keeps unnecessary metadata, and delays customer feedback until after queueing. Normalizing before
storage preserves the existing worker and cleanup boundaries.

### Show only a generic upload error

A single generic message does not tell the visitor whether to choose another file or retry the same
one and leaves operators unable to distinguish customer input from Object Storage failure. Bounded
reason categories provide useful feedback without retaining personal image data.
