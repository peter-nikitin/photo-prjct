# 0023: Store consented selfie-search quality feedback

- Status: Accepted
- Date: 2026-08-04
- Deciders: project maintainers
- Supersedes: none
- Superseded by: none

## Context

Customers need to report failed, empty, and inaccurate public selfie searches. Useful investigation
evidence includes the exact selfie selected for one search, a contact, explicit result-photo labels,
the search configuration, and proof of personal-data consent.

ADR 0019 requires the search pipeline to delete its temporary server-side selfie before publishing
a terminal result and never persist the query embedding. Delaying that cleanup for optional
feedback would expand the exposure of every search and make result publication depend on a later
customer decision. Asking the customer to select the same selfie again avoids that change but adds
friction and can attach the wrong search attempt.

The feedback copy, contact, labels, consent evidence, staff access, and retention behavior are a
new biometric-governance boundary. ADRs 0001, 0002, and 0006 establish Django, PostgreSQL, and
Yandex Object Storage as the existing product-state and media boundaries but do not decide this
feedback-specific use.

## Decision drivers

- Preserve ADR 0019's cleanup-before-terminal-publication and non-persisted query-vector contract.
- Reuse the exact customer-selected selfie without a second file picker.
- Store no server-side feedback selfie before explicit feedback consent.
- Keep one feedback record attributable to exactly one immutable search result.
- Give authorized staff enough evidence to investigate quality without creating a public media
  path or worker access.
- Use native bucket lifecycle enforcement for selfie expiry instead of a new application cleanup
  state machine.
- Keep the first implementation on the existing Django, PostgreSQL, and Yandex Cloud stack.

## Considered options

1. Keep a short-lived local browser copy, upload it only with explicit feedback consent, store the
   selfie in a dedicated lifecycle-bound private bucket, and store feedback metadata in PostgreSQL.
2. Keep the search pipeline's temporary selfie on the server until the customer submits or declines
   feedback.
3. Delete the search selfie as today and require the customer to select a feedback attachment
   manually.
4. Collect aggregate feedback without a selfie, contact, or per-result labels.

## Decision

Select option 1.

The browser may retain the exact selected search file in origin-scoped IndexedDB for at most seven
days. It associates the local record with one bearer result without sending the local copy to a new
server store. Successful feedback submission and the browser-wide opt-out both delete the local
copy. An active opt-out prevents future feedback-local preservation. Browser storage failure never
blocks the ordinary search.

ADR 0019 remains unchanged. Django still deletes the search pipeline's temporary selfie before a
terminal result becomes public and never persists the query embedding. The feedback selfie is a
separate object uploaded only when the customer submits feedback with the mandatory, initially
unchecked personal-data consent.

Django and PostgreSQL remain authoritative for feedback. One immutable feedback belongs to one
`SelfieSearch`; PostgreSQL stores its result labels, bounded search evidence, plaintext contact,
and consent evidence. Consent includes a dedicated non-null Boolean fact constrained to `true`,
the consent-text version, and the acceptance timestamp. Contact and structured feedback have no
automatic expiry.

The feedback selfie uses a dedicated private Yandex Object Storage bucket with no public access,
server-side encryption under a dedicated Yandex KMS key, no versioning, no Object Lock, and a
30-day deletion lifecycle. The lifecycle is the authoritative expiry mechanism for successfully
stored feedback selfies; Django adds no scheduled feedback-media cleanup state machine. Object keys
and metadata contain no contact, filename, event slug, bearer token, or other customer-supplied
identifier.

Only explicitly authorized staff may inspect feedback through Django Admin. Contact and selfie
access require an explicit audited action. The selfie is exposed only by a short-lived exact-object
authorization and never by a public or bearer-result media route. The existing ML worker receives
no feedback bucket or database access.

The feedback endpoint remains bound to the existing public result and accepts at most one immutable
feedback per search. It is CSRF-protected and validates every submitted label against that search's
saved result membership. Per-photo labels are customer-provided quality evidence, not verified
identity assertions and do not automatically change ranking, thresholds, embeddings, or model
weights.

## Consequences

### Positive

- Search terminal publication and temporary-selfie cleanup remain unchanged.
- Customers do not select the same selfie twice.
- No feedback selfie reaches server-side feedback storage before explicit consent.
- PostgreSQL constraints preserve one-search/one-feedback and label membership integrity.
- A separate KMS-encrypted bucket prevents feedback media from inheriting public or permanent-media
  access paths.
- Native lifecycle expiry bounds selfie storage without another cleanup worker or durable retry
  state.

### Negative

- Feedback is available only from the browser profile that retains the local selfie and only within
  the seven-day local window.
- IndexedDB is origin-scoped browser storage, not an independent protected vault; XSS prevention
  remains part of the security boundary.
- Plaintext contact and structured feedback remain in PostgreSQL without automatic expiry.
- Staff with authorized database or admin access can read the contact, and authorized selfie access
  necessarily permits saving the viewed file.
- Bucket lifecycle expiry is asynchronous; the application does not prove exact deletion time for
  each individual object.
- A dedicated bucket, KMS key, IAM grants, lifecycle rule, and access audit add operational state.

### Follow-up

- Reconcile the published personal-data policy with the feedback purpose, consent copy, retained
  contact/labels, and lifecycle-bound selfie before environment activation.
- Add account-level consent withdrawal or deletion only when the product gains an authenticated
  customer identity capable of authorizing such requests.
- Revisit plaintext contact retention if access scope, operator count, regulatory requirements, or
  incident evidence makes application-level encryption or explicit deletion necessary.
- Treat feedback as labelled evaluation evidence only through a separately approved model-quality
  workflow; this decision does not authorize automated training or threshold changes.

## Validation and rollback

Validate that browser opt-out and local expiry delete feedback-local state; search cleanup still
precedes terminal publication; unchecked consent creates no object or row; every stored feedback
has explicit consent evidence; labels cannot cross search membership; the feedback bucket denies
public access and uses the accepted KMS/lifecycle configuration; worker and ordinary media routes
cannot access feedback objects; staff access is authorized and audited; and an expired object is
presented as the expected missing-selfie state.

Roll back by disabling the feedback entry point and submission endpoint without disabling selfie
search. Existing PostgreSQL feedback remains restricted, and the feedback bucket lifecycle
continues deleting stored selfies. Reconsider this decision if browser-local preservation causes
material loss or leakage, lifecycle enforcement cannot be verified, staff access cannot be audited,
or indefinite plaintext contact retention becomes unacceptable.

## References

- [Selfie search quality feedback design](../superpowers/specs/2026-08-04-selfie-search-quality-feedback-design.md)
- [Architecture: security, privacy, and legal boundaries](../architecture.md#security-privacy-and-legal-boundaries)
- [ADR 0001: Use a Django modular monolith](0001-django-modular-monolith.md)
- [ADR 0002: Use PostgreSQL as the system of record](0002-postgresql-system-of-record.md)
- [ADR 0006: Use Yandex Object Storage for media](0006-yandex-object-storage-media.md)
- [ADR 0019: Use public event-scoped selfie search](0019-use-public-event-selfie-search.md)
