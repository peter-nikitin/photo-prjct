# Role-aware service navigation design

- Date: 2026-07-19
- Status: Awaiting written review
- Related architecture: [Accepted constraints](../../architecture.md#accepted-constraints),
  [Security, privacy, and legal boundaries](../../architecture.md#security-privacy-and-legal-boundaries)
- Related ADRs: [ADR 0012 — Use Django photographer permissions](../../adr/0012-use-django-photographer-permissions.md)
- ADR impact: Conforms to ADR 0012

## Goal

Keep public navigation focused on visitors who came to find event photos. Do not expose links for
photographer upload or Django administration to anonymous visitors or to authenticated users who
cannot use those capabilities.

## Selected design

The shared production navigation derives visibility from Django's existing user state:

| User state | Photographer upload | Administration |
| --- | --- | --- |
| Anonymous | Hidden | Hidden |
| Authenticated without upload permission or staff status | Hidden | Hidden |
| Authenticated with `ingestion.upload_photos` | Visible when uploads are enabled | Hidden |
| Authenticated staff without upload permission | Hidden | Visible |
| Authenticated staff with upload permission | Visible when uploads are enabled | Visible |

The existing `photographer_upload_navigation` context value remains the source of truth for the
upload link. The administration link is rendered only when `request.user.is_staff` is true.
Superusers follow Django's standard staff behavior.

## Boundaries

- This changes navigation visibility only. It does not alter URL routing, authentication,
  authorization, login, logout, groups, permissions, feature flags, or Django Admin access checks.
- The public event catalog, event detail, and legal links remain visible to everyone.
- No new context processor, template tag, role model, or persistent data is introduced.

## Verification

Behavioral Django response tests cover anonymous, ordinary authenticated, photographer, staff, and
staff-plus-photographer navigation. Existing photographer navigation tests continue to prove that
the upload feature flag and `ingestion.upload_photos` permission are both required.

## Acceptance criteria

- Anonymous public pages contain neither the upload link nor the administration link.
- An ordinary authenticated user contains neither service link.
- A permitted photographer sees the upload link only when uploads are enabled and does not see the
  administration link unless also staff.
- A staff user sees the administration link and does not see the upload link unless also permitted.
- A staff user with upload permission sees both service links when uploads are enabled.
- Public navigation links and server-side authorization behavior remain unchanged.
