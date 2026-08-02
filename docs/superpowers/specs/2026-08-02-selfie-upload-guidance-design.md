# Selfie Upload Guidance Design

## Goal

Help visitors choose a selfie that maximizes the chance of finding their event photos without overstating what the face-search model uses.

## Design

Add one short recommendation to the existing selfie-search entry block:

> Загрузите чёткую фотографию, где лицо хорошо видно. Лучше использовать фото с дня мероприятия, особенно если на мероприятии вы были в очках или головном уборе.

Keep the existing privacy and bearer-link disclosures unchanged. Do not mention clothing because the current search compares face embeddings rather than clothing.

## Verification

Assert the guidance on both free and paid published event pages, update the two existing selfie-entry visual snapshots, inspect them, and run the focused Django and Playwright checks.
