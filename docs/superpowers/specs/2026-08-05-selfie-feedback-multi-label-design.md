# Selfie-search multi-photo feedback

## Goal

Let a customer label any number of photos from each selfie-search result before submitting one final, immutable feedback record. Make contact details optional and keep them out of the primary flow.

## User flow

For every completed search with visible results, each result card offers the existing mutually exclusive choices `Я есть` and `Меня нет`. Selecting a choice only updates the browser-local draft. The customer may label any number of photos, leave some unlabelled, move between result pages, and change draft choices before submission.

The customer submits the complete draft once with `Отправить отзыв`. After a successful submission, the labels are immutable and the page shows `Спасибо, отзыв отправлен.` instead of the form and marking controls. A later selfie search has its own independent draft and may be submitted separately.

Existing problem feedback for searches without visible results remains a single final submission without photo labels.

## Optional contact

The primary form does not show a contact field. A collapsed native disclosure labelled `Оставить контакт для связи — необязательно` reveals one text field with the hint `Телефон, Telegram или email`.

The field accepts an empty value. When supplied, it keeps the existing length and control-character validation. Consent remains mandatory because the feedback still includes the search selfie. Customer copy must describe contact processing conditionally rather than imply that contact is always supplied.

## Data and privacy contract

The server continues to store at most one immutable `SelfieSearchFeedback` per search and zero or more labels attached only to results from that search. No update endpoint, autosave request, compatibility path, or second feedback record is added.

An absent contact is stored as an empty string in the existing column. The existing private selfie storage, consent capture, retention, bearer authorization, and post-submission browser cleanup remain unchanged.

## Failure handling

Draft labels remain browser-local after validation, network, or storage failures so the customer can retry. A failed submission does not lock the search. Concurrent or repeated successful submission keeps the existing idempotent `already_submitted` behavior and does not permit editing.

## Verification

Focused tests must cover multiple labels in one submission, labels retained across result pages, a blank contact accepted, an unsafe non-empty contact rejected, and the immutable post-submission state. Desktop and mobile feedback snapshots must cover the collapsed optional-contact control and multi-photo marking state.

