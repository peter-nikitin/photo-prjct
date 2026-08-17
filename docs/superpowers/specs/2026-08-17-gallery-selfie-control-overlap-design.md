# Gallery selfie control overlap

## Problem

On intermediate desktop widths, the selfie upload control and its submit button share one narrow
grid row. The native file input becomes visually crowded by the button even though the discovery
area itself still has enough room for the selfie and manual-search columns.

## Design

Keep the two discovery columns side by side. At widths up to `1200px`, stack only the selfie file
input and submit button into one column. At wider desktop widths, retain their compact side-by-side
layout. The existing mobile layout remains unchanged.

This avoids moving the folder and time controls below the selfie search, preserves the compact
wide-desktop presentation, and gives the native file input enough horizontal space at the width
shown in the reported screenshot.

## Verification

- Add a Playwright geometry regression at an intermediate desktop viewport that requires the
  selfie input to appear above the submit button without overlap.
- Preserve the existing 1440px contract that keeps the controls side by side.
- Run the focused geometry test, the relevant visual suite, and inspect the intermediate-width
  result in a browser.

