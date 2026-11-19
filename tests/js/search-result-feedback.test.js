const { test } = require('node:test');
const assert = require('node:assert/strict');

const { submitResultFeedback } = require('../../src/backend/static/ui/search-result-feedback.js');

test('shows confirmation only after persistence and lets the visitor retry either failure', async () => {
  const button = { disabled: false };
  const saved = { hidden: true };
  const error = { hidden: true };
  const submissionId = { value: '05945c04-ee54-4d7f-a5c1-a8ebfef01fb0', dataset: {} };
  const form = {
    action: '/search-feedback/',
    querySelector(selector) {
      return { button, '[data-feedback-saved]': saved, '[data-feedback-error]': error,
        '[name="submission_id"]': submissionId }[selector];
    },
  };
  const body = new FormData();
  await submitResultFeedback(form, body, async () => ({ ok: false }));
  const firstId = body.get('submission_id');
  assert.match(firstId, /^[0-9a-f-]{36}$/i);
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
    assert.equal(options.body.get('submission_id'), firstId);
    assert.equal(button.disabled, true);
    assert.equal(error.hidden, true);
    assert.equal(saved.hidden, true);
    return { ok: true, async json() { return { status: 'saved' }; } };
  });
  assert.equal(saved.hidden, false);
  assert.equal(error.hidden, true);
  assert.equal(button.hidden, true);
});
