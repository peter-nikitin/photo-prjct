const { test } = require('node:test');
const assert = require('node:assert/strict');

const { submitResultFeedback } = require('../../src/backend/static/ui/search-result-feedback.js');

test('shows confirmation only after persistence and lets the visitor retry either failure', async () => {
  const button = { disabled: false };
  const saved = { hidden: true };
  const error = { hidden: true };
  const form = {
    action: '/result-feedback/',
    querySelector(selector) {
      return { button, '[data-feedback-saved]': saved, '[data-feedback-error]': error }[selector];
    },
  };
  const body = new FormData();
  await submitResultFeedback(form, body, async () => ({ ok: false }));
  assert.equal(saved.hidden, true);
  assert.equal(error.hidden, false);
  assert.equal(button.disabled, false);

  await submitResultFeedback(form, body, async () => { throw new Error('offline'); });
  assert.equal(saved.hidden, true);
  assert.equal(error.hidden, false);
  assert.equal(button.disabled, false);

  await submitResultFeedback(form, body, async (url, options) => {
    assert.equal(url, form.action);
    assert.equal(options.body, body);
    assert.equal(button.disabled, true);
    assert.equal(error.hidden, true);
    assert.equal(saved.hidden, true);
    return { ok: true, async json() { return { status: 'saved' }; } };
  });
  assert.equal(saved.hidden, false);
  assert.equal(error.hidden, true);
  assert.equal(button.hidden, true);
});
