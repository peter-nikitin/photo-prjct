(function () {
  'use strict';

  const pendingScopes = new WeakSet();

  function applySearchFeedback(scope, result) {
    const manual = new Set(result.manual_photo_ids);
    const similar = new Set(result.similar_photo_ids);
    if (Number(scope.dataset.excludedPhotoCount) !== result.excluded_photo_count) {
      window.location.reload();
      return;
    }
    for (const card of scope.querySelectorAll('.gallery-card[data-photo-id]')) {
      const id = card.dataset.photoId;
      const mark = card.querySelector('[data-reversible-feedback] button');
      if (mark) mark.setAttribute('aria-pressed', String(manual.has(id)));
      const search = card.querySelector('button[name="photo_id"][data-continuation-control]');
      if (search) {
        search.disabled = manual.has(id)
          || Boolean(scope.closest('[data-continuation-pending]'));
      }
      if (manual.has(id)) card.dataset.negativeFeedback = 'manual';
      else if (similar.has(id)) card.dataset.negativeFeedback = 'similar';
      else delete card.dataset.negativeFeedback;
    }
    const archive = scope.querySelector('.selfie-search-archive-action');
    if (archive) archive.hidden = manual.size === Number(scope.dataset.resultPhotoCount);
  }

  async function submitResultFeedback(form, body, fetch) {
    const button = form.querySelector('button');
    const saved = form.querySelector('[data-feedback-saved]');
    const error = form.querySelector('[data-feedback-error]');
    if (button.disabled || button.hidden) return;
    const scope = form.hasAttribute?.('data-reversible-feedback')
      ? form.closest('[data-feedback-scope]') : null;
    if (scope && pendingScopes.has(scope)) return;
    const search = scope && form.closest('.gallery-card')
      ?.querySelector('button[name="photo_id"][data-continuation-control]');
    const wasSearchDisabled = search?.disabled;
    if (scope) {
      pendingScopes.add(scope);
      const marking = button.getAttribute('aria-pressed') !== 'true';
      body.set('action', marking ? 'mark' : 'unmark');
      if (search && marking) search.disabled = true;
    }

    const submissionId = form.querySelector('[name="submission_id"]');
    if (!submissionId.dataset.started) {
      submissionId.value = globalThis.crypto?.randomUUID?.() ?? submissionId.value;
      submissionId.dataset.started = 'true';
    }
    body.set('submission_id', submissionId.value);
    button.disabled = true;
    error.hidden = true;
    try {
      const response = await fetch(form.action, {
        method: 'POST', body, mode: 'same-origin', referrerPolicy: 'origin',
      });
      if (!response.ok) throw new Error('Feedback was not saved');
      const result = await response.json();
      if (result.status !== 'saved') throw new Error('Feedback was not saved');
      if (scope) {
        delete submissionId.dataset.started;
        applySearchFeedback(scope, result);
      } else {
        saved.hidden = false;
        button.hidden = true;
      }
    } catch (_error) {
      if (search) search.disabled = wasSearchDisabled;
      error.hidden = false;
    } finally {
      button.disabled = false;
      if (scope) pendingScopes.delete(scope);
    }
  }

  if (typeof document !== 'undefined') {
    document.addEventListener('submit', (event) => {
      const form = event.target;
      if (!form.matches?.('[data-result-feedback]')) return;
      event.preventDefault();
      submitResultFeedback(form, new FormData(form), window.fetch.bind(window));
    });
  }

  if (typeof module !== 'undefined') module.exports = { submitResultFeedback };
})();
