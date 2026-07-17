# Product Jobs

This registry tracks customer-facing jobs for FindMe Photo. Product jobs use only these actors:
Visitor, Customer, Photographer, and Operator.

## Job format

Each job has a stable `PJ-NNN` identifier and uses this Jobs-to-be-Done form:

> When &lt;situation&gt;, I want to &lt;motivation&gt;, so I can &lt;expected outcome&gt;.

Every job records its current status, supporting evidence, and last-updated date. Status must not
advance from a proposal alone; an advance requires evidence appropriate to the new status.

## Statuses

| Status | Definition |
| --- | --- |
| Candidate | The job is recognized as potentially valuable, but has not been committed to a delivery plan. |
| Planned | The job is committed to a decision-complete delivery plan, but implementation has not started. |
| In progress | Implementation of the planned job has started but is not yet delivered. |
| Delivered | The job is available in the product, but its expected outcome has not yet been validated with sufficient evidence. |
| Validated | Evidence shows that the delivered job works and supports its expected outcome. |
| Deferred | Work on the job is intentionally postponed, with the reason recorded. |

## Current state

| Job | Actor | Summary | Status | Last updated |
| --- | --- | --- | --- | --- |
| PJ-001 | Operator | Publish an event | Validated | 2026-07-17 |
| PJ-002 | Visitor | Discover published events | Validated | 2026-07-17 |
| PJ-003 | Visitor | Review event details | Validated | 2026-07-17 |
| PJ-004 | Photographer | Upload an event batch | Candidate | 2026-07-17 |
| PJ-005 | Visitor | Browse an event gallery | Candidate | 2026-07-17 |
| PJ-006 | Operator | Review processing results | Candidate | 2026-07-17 |
| PJ-007 | Customer | Find photos by bib | Candidate | 2026-07-17 |
| PJ-008 | Customer | Find photos by face | Candidate | 2026-07-17 |
| PJ-009 | Visitor | Receive a free-event original | Candidate | 2026-07-17 |
| PJ-010 | Customer | Purchase selected photos | Candidate | 2026-07-17 |
| PJ-011 | Customer | Download purchased photos | Candidate | 2026-07-17 |

## Job details

### PJ-001 — Operator — Publish an event

When an event is ready for customers, I want to create and publish its catalog record, so I can make
it discoverable without developer assistance.

- Status: Validated
- Evidence: [`EventAdminTests.test_admin_creates_and_publishes_event`](../src/backend/picflow/tests/test_admin.py)
- Last updated: 2026-07-17

### PJ-002 — Visitor — Discover published events

When I arrive at FindMe Photo, I want to browse only published events in a useful order, so I can
choose the event I attended.

- Status: Validated
- Evidence: [`PageTests.test_catalog_only_shows_published_events` and `PageTests.test_catalog_orders_upcoming_then_past`](../src/backend/picflow/tests/test_views.py)
- Last updated: 2026-07-17

### PJ-003 — Visitor — Review event details

When I find an event, I want to open its public details, so I can confirm it is the event I attended.

- Status: Validated
- Evidence: [`PageTests.test_event_detail_renders_published_event` and `PageTests.test_event_detail_returns_404_for_draft_event`](../src/backend/picflow/tests/test_views.py)
- Last updated: 2026-07-17

### PJ-004 — Photographer — Upload an event batch

When I finish photographing an event, I want to upload a batch with event and capture context, so I
can submit it for processing.

- Status: Candidate
- Evidence: [Target MVP architecture — Ingestion](architecture.md#target-mvp-architecture--proposed)
- Last updated: 2026-07-17

### PJ-005 — Visitor — Browse an event gallery

When an event has published photos, I want to browse its gallery, so I can inspect photos from that
event.

- Status: Candidate
- Evidence: [Architecture evolution stages — Photo-bank core](architecture.md#evolution-stages)
- Last updated: 2026-07-17

### PJ-006 — Operator — Review processing results

When automated processing fails or produces uncertain metadata, I want to inspect and correct the
result, so I can keep published search data reliable.

- Status: Candidate
- Evidence: [Target MVP architecture — Moderation](architecture.md#target-mvp-architecture--proposed)
- Last updated: 2026-07-17

### PJ-007 — Customer — Find photos by bib

When I know a participant bib number, I want to search within one event, so I can find likely photos
quickly.

- Status: Candidate
- Evidence: [Target MVP architecture — Search](architecture.md#search)
- Last updated: 2026-07-17

### PJ-008 — Customer — Find photos by face

When I have an appropriate reference image and the required consent applies, I want to search within
one event, so I can review probable matches.

- Status: Candidate
- Evidence: [Target MVP architecture — Search](architecture.md#search) and [Security, privacy, and legal boundaries](architecture.md#security-privacy-and-legal-boundaries)
- Last updated: 2026-07-17

### PJ-009 — Visitor — Receive a free-event original

When an event offers free photos, I want a controlled anonymous download, so I can receive the
original without exposing its permanent storage key.

- Status: Candidate
- Evidence: [Security, privacy, and legal boundaries](architecture.md#security-privacy-and-legal-boundaries)
- Last updated: 2026-07-17

### PJ-010 — Customer — Purchase selected photos

When I select paid photos, I want to complete an order and payment, so I can obtain download
entitlement.

- Status: Candidate
- Evidence: [Target MVP architecture — Purchase and download](architecture.md#purchase-and-download)
- Last updated: 2026-07-17

### PJ-011 — Customer — Download purchased photos

When my order is paid, I want to download only its entitled photos, so I can receive the files I
purchased securely.

- Status: Candidate
- Evidence: [Target MVP architecture — Purchase and download](architecture.md#purchase-and-download)
- Last updated: 2026-07-17

Visual design-reference screens are not delivery evidence.

## Status log

This log is append-only.

| Date | Job | Previous status | New status | Evidence or reason |
| --- | --- | --- | --- | --- |
| 2026-07-17 | PJ-001 | Not recorded | Validated | [`EventAdminTests.test_admin_creates_and_publishes_event`](../src/backend/picflow/tests/test_admin.py) |
| 2026-07-17 | PJ-002 | Not recorded | Validated | [`PageTests.test_catalog_only_shows_published_events` and `PageTests.test_catalog_orders_upcoming_then_past`](../src/backend/picflow/tests/test_views.py) |
| 2026-07-17 | PJ-003 | Not recorded | Validated | [`PageTests.test_event_detail_renders_published_event` and `PageTests.test_event_detail_returns_404_for_draft_event`](../src/backend/picflow/tests/test_views.py) |
| 2026-07-17 | PJ-004 | Not recorded | Candidate | [Target MVP architecture — Ingestion](architecture.md#target-mvp-architecture--proposed) |
| 2026-07-17 | PJ-005 | Not recorded | Candidate | [Architecture evolution stages — Photo-bank core](architecture.md#evolution-stages) |
| 2026-07-17 | PJ-006 | Not recorded | Candidate | [Target MVP architecture — Moderation](architecture.md#target-mvp-architecture--proposed) |
| 2026-07-17 | PJ-007 | Not recorded | Candidate | [Target MVP architecture — Search](architecture.md#search) |
| 2026-07-17 | PJ-008 | Not recorded | Candidate | [Target MVP architecture — Search](architecture.md#search) and [Security, privacy, and legal boundaries](architecture.md#security-privacy-and-legal-boundaries) |
| 2026-07-17 | PJ-009 | Not recorded | Candidate | [Security, privacy, and legal boundaries](architecture.md#security-privacy-and-legal-boundaries) |
| 2026-07-17 | PJ-010 | Not recorded | Candidate | [Target MVP architecture — Purchase and download](architecture.md#purchase-and-download) |
| 2026-07-17 | PJ-011 | Not recorded | Candidate | [Target MVP architecture — Purchase and download](architecture.md#purchase-and-download) |
