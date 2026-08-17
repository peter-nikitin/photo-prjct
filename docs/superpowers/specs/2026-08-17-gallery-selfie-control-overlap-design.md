# Gallery selfie control overlap

## Problem

The selfie upload control and its submit button share one grid row on wide desktop viewports.
When device-local search history appears after page load, it occupies the second half of the selfie
area and narrows the form without changing the viewport. The `1200px` viewport breakpoint therefore
does not activate, and the submit button overlaps the native file input.

## Design

Keep the two discovery columns side by side. Make the selfie form a single-column grid at every
viewport width so its label, file input, and submit button always appear in that order on separate
rows. Loading or removing device-local search history must not change this ordering. The existing
mobile layout remains unchanged.

This avoids coupling the form layout to viewport width when the actual available width also depends
on dynamic history content. It leaves the folder and time controls in their separate discovery
column and gives the native file input the full form width.

## Verification

- Add a Playwright geometry regression at `1440px` after device-local history becomes visible and
  require the selfie input to appear above the submit button without overlap.
- Require the same vertical ordering before history is present and at existing mobile coverage.
- Run the focused geometry test, the relevant visual suite, and inspect the history-loaded desktop
  result in a browser.
