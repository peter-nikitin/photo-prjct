(function () {
  'use strict';

  async function submitResultFeedback(form, body, fetch) {
    const button = form.querySelector('button');
    const saved = form.querySelector('[data-feedback-saved]');
    const error = form.querySelector('[data-feedback-error]');
    if (button.disabled || button.hidden) return;

    const submissionId = form.querySelector('[name="submission_id"]');
    if (!submissionId.dataset.started) {
      submissionId.value = globalThis.crypto?.randomUUID?.() ?? submissionId.value;
      submissionId.dataset.started = 'true';
    }
    body.set('submission_id', submissionId.value);
    button.disabled = true;
    error.hidden = true;
    try {
      const response = await fetch(form.action, { method: 'POST', body, credentials: 'same-origin' });
      if (!response.ok || (await response.json()).status !== 'saved') {
        throw new Error('Feedback was not saved');
      }
      saved.hidden = false;
      button.hidden = true;
    } catch (_error) {
      error.hidden = false;
    } finally {
      button.disabled = false;
    }
  }

  if (typeof document !== 'undefined') {
    for (const form of document.querySelectorAll('[data-result-feedback]')) {
      form.addEventListener('submit', (event) => {
        event.preventDefault();
        submitResultFeedback(form, new FormData(form), window.fetch.bind(window));
      });
    }
  }

  if (typeof module !== 'undefined') module.exports = { submitResultFeedback };
})();
