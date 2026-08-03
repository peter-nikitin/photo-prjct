# Gallery Page Jump Design

## Goal

Let a user who knows a page number jump directly to that page from every server-paginated photo gallery.

## Scope

- Event galleries at `/events/<slug>/`.
- Ready selfie-search result galleries.
- A shared server-rendered pagination partial used by both screens.
- No change to the photographer upload queue, whose pagination is client-local and does not represent URL-addressable photo pages.

## Interaction

When a gallery has more than one page, its pagination row contains the existing previous/next links and page status plus a compact GET form. The form has a numeric `page` field constrained to `1…num_pages`, prefilled with the current page, and a visible `Перейти` submit button. Submitting navigates to `?page=N`; existing Django view validation remains authoritative and returns `404` for invalid pages.

The field has a visible or accessible Russian label, keeps touch targets usable, and fits the existing responsive pagination row. No JavaScript is required.

## Reuse and boundaries

The shared partial receives the Django `Page` object and an accessible navigation label. It owns the complete pagination markup so the two gallery templates cannot drift. Existing gallery ordering, authorization, page size, and media behavior remain unchanged.

## Verification

Extend the two existing numbered-pagination view tests with minimal assertions that the shared GET form, numeric bounds, current value, and submit copy are rendered. Run only those focused Django tests plus `manage.py check` and `git diff --check`.
