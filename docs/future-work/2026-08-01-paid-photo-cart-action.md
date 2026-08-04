# Paid-photo cart action

## Observed gap

Every currently rendered card offers direct original download; there is no paid-photo cart or
purchase entitlement.

The gallery-photo similarity POST currently has no source-event `access_type`/visible-gallery
check.

## Why this is non-blocking

This increment's accepted requirement is one-click download, and the product has no implemented
commerce path to substitute.

This is non-blocking because no normal paid gallery/event production path exists and the action is
only rendered on existing galleries.

## Revisit trigger

Implementation begins for commerce or the first normal paid event is prepared for publication,
whichever happens first.

Before the first normal paid event/gallery is prepared, decide and enforce whether a gallery-origin
search source must belong to a currently visible gallery, alongside commerce/media entitlement
policy.

## Likely scope

Replace the direct action for paid media with add-to-cart behavior, define entitlement-backed
purchased downloads, decide ready-result paid behavior, and add the realistic free/paid validation
matrix at that time.
