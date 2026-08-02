# Contacts, Legal Documents, Cookie Notice, and Yandex Metrika Design

**Status:** Approved design, pending written-spec review

**Date:** 2026-08-02

**Related architecture:** `docs/architecture.md` — Current architecture: Django-rendered canonical
production UI and packaged static assets

**Related ADRs:** none

**ADR impact:** None — reversible implementation detail

## Outcome

Give every visitor one clear FindMe Photo page for contacting the operator and opening the current
legal documents. Add a site-wide informational cookie notice and collect public user-interface
analytics with the supplied Yandex Metrika counter.

The shortest safe delivery reuses the existing `/legal/` route, shared `ui/base.html` template,
static-file pipeline, and visual language. It adds no database model, CMS, external document store,
or new service.

## Accepted inputs

The release publishes the following three supplied files from
`/Users/petrnikitin/Downloads/Фотобанк 2` without editing, re-exporting, or correcting their contents:

- `ДОГОВОР ПУБЛИЧНОЙ ОФЕРТЫ (FMP).pdf` — SHA-256
  `33a64514790b8193ad1704cbfaa606504ba73f71d2aaf4c0331480895d494371`;
- `Пользовательское соглашение (FMP).pdf` — SHA-256
  `8da40d74391781495753c14d380ba43ea60d6e510da727ac98e428b7e035a07d`;
- `Согласие на обработку данных Политика в отношении обработки персональных данных (FMP).pdf`
  — SHA-256 `7b8be1e72e3d8f939b48cf1458375a8b7635942a06fed918967476e22a77c68d`.

The PDFs contain email addresses and analytics language. Publishing them unchanged is an explicit
content decision. The surrounding contacts page does not separately display an email address.

## Contacts and documents page

The existing `/legal/` page becomes **«Контакты и документы»** while retaining its public route and
the existing **«Документы»** navigation entry.

The page shows:

- the public telephone number `+7 (903) 127-57-66` as a `tel:+79031275766` link;
- no standalone email contact;
- one clearly named link for each of the three supplied PDFs;
- a short indication that each linked document is a PDF and opens as a document.

The PDFs ship as packaged Django static assets. Templates resolve their URLs through Django's
static-file mechanism so production's manifest storage and WhiteNoise delivery continue to work.
The implementation uses stable descriptive ASCII asset filenames rather than exposing the supplied
local filenames or the local Downloads path.

Each document link opens the complete supplied file. The page does not reproduce legal text as
HTML, create placeholder documents, or split the combined personal-data consent and policy PDF.

## Cookie notice

Every user-facing HTML page that extends `ui/base.html` includes a fixed, responsive notice near the
bottom of the viewport. It is non-modal, does not prevent navigation or interaction, and follows the
existing FindMe Photo visual system.

The notice uses this exact copy:

> Мы используем файлы cookie, чтобы обеспечить работу нашего сайта и проанализировать его
> использование. Продолжая использовать этот сайт, вы даете согласие на использование файлов
> cookie.

The notice includes:

- an `OK` button;
- a contextual link to the published combined consent and personal-data-policy PDF;
- responsive layout that remains readable and operable on narrow mobile screens.

On `OK`, the browser stores a versioned acknowledgement in `localStorage`. The initial contract is:

```text
key: findme_cookie_notice
value: 2026-08-02
```

The notice remains hidden on later page loads in the same browser profile when that exact value is
present. A missing value, a different value, cleared browser storage, or another browser/device
causes the notice to appear again. If reading or writing `localStorage` throws, the page continues to
work and the notice remains available; acknowledgement is not sent to Django or stored in
PostgreSQL.

This is an informational acknowledgement, not a preference manager. There is no reject button and
the acknowledgement does not control analytics loading.

## Yandex Metrika

The supplied Yandex Metrika counter `111239706` is included once through the shared production base
template. Its initialization contract is unchanged:

```javascript
ym(111239706, "init", {
  ssr: true,
  webvisor: true,
  clickmap: true,
  ecommerce: "dataLayer",
  referrer: document.referrer,
  url: location.href,
  accurateTrackBounce: true,
  trackLinks: true,
});
```

The asynchronous tag loads immediately on every user-facing page that uses `ui/base.html`, whether
or not the visitor has pressed `OK`. The supplied `noscript` tracking image is also present for those
pages when JavaScript is disabled.

The counter is intentionally absent from Django Admin, `/health/`, internal processing APIs, media
responses, redirects, and any other non-HTML response. Test-only visual-reference pages must not
send real analytics traffic; their settings or templates suppress the production counter while
retaining a deterministic cookie-notice state for visual testing.

## Failure and compatibility behavior

- A failed or blocked Metrika request does not block rendering or any FindMe Photo workflow.
- The counter bootstrap appears at most once in each rendered page.
- Missing PDF assets are a release failure rather than a reason to display dead placeholder links.
- Existing `/legal/` bookmarks continue to work.
- The cookie notice works independently of Django authentication and does not contain personal
  account state.
- Keyboard users can reach the policy link and `OK` button, visible focus is preserved, and the
  notice does not cover focused content without a way to dismiss it.

## Scope boundaries

This delivery does not:

- edit or legally review the supplied documents;
- create HTML versions of the PDFs;
- add a CMS or database-managed document registry;
- add an email contact outside the PDFs;
- add analytics consent categories, a reject action, or delayed analytics loading;
- add another analytics provider;
- track Django Admin or internal/API traffic;
- change event, gallery, upload, selfie-search, payment, or media authorization behavior.

## Alternatives considered

### Separate HTML legal pages

HTML would improve mobile reading and allow deep links, but transcribing and maintaining the supplied
21 pages creates avoidable editorial risk. It is deferred until approved HTML source exists.

### External document hosting

Object Storage, cloud documents, or a CMS would add access, versioning, and availability concerns.
Packaged static PDFs are sufficient for the current three version-controlled documents.

### Consent-gated analytics

Loading Metrika only after an accept/reject choice would require a preference manager and a
different user contract. The accepted first release instead loads Metrika immediately and uses the
notice as an informational acknowledgement.

## Acceptance criteria

1. `/legal/` presents the telephone link, no standalone email, and working links to the three exact
   supplied PDFs.
2. Each packaged PDF matches its accepted SHA-256 checksum.
3. The notice appears on a fresh public browser profile with the exact approved copy, policy link,
   and `OK` button.
4. Pressing `OK` stores `findme_cookie_notice=2026-08-02` and suppresses the notice on later public
   page loads in that browser profile.
5. Missing, stale, cleared, or unavailable local storage produces the specified safe behavior
   without breaking the page.
6. Public production HTML includes one counter `111239706` bootstrap and the supplied no-JavaScript
   tracking fallback; analytics loading does not wait for `OK`.
7. Django Admin, test-only visual references, health/API/media/non-HTML routes do not emit real
   Metrika traffic.
8. Desktop and mobile browser checks confirm readable document links, a non-blocking notice,
   keyboard operation, acknowledgement persistence, and no regression in existing public
   navigation.
