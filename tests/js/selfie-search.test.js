'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const { SelfieSearchPoller, bindSelfieSearchForm } = require('../../src/backend/static/ui/selfie-search.js');

function clock() {
  const scheduled = [];
  return {
    scheduled,
    clearTimeout(timer) {
      timer.cleared = true;
    },
    setTimeout(callback, delay) {
      const timer = { callback, delay, cleared: false };
      scheduled.push(timer);
      return timer;
    },
  };
}

test('disables the submit control immediately to prevent a duplicate selfie upload', () => {
  let submitListener;
  const button = { disabled: false, textContent: 'Найти мои фото' };
  const form = {
    querySelector(selector) {
      return selector === 'button[type="submit"]' ? button : null;
    },
    addEventListener(type, listener) {
      if (type === 'submit') submitListener = listener;
    },
  };

  bindSelfieSearchForm(form);
  submitListener();

  assert.equal(button.disabled, true);
  assert.equal(button.textContent, 'Ищем фотографии…');
});

test('polls queued, processing, and cleanup states every two seconds before terminal refresh', async () => {
  const timers = clock();
  const statuses = [
    { status: 'queued' },
    { status: 'processing' },
    { status: 'cleanup_pending' },
    { status: 'ready' },
  ];
  let reloads = 0;
  const poller = new SelfieSearchPoller({
    url: '/status/',
    fetch: async () => ({ ok: true, json: async () => statuses.shift() }),
    setTimeout: timers.setTimeout,
    clearTimeout: timers.clearTimeout,
    reload: () => {
      reloads += 1;
    },
  });

  await poller.poll();
  assert.equal(timers.scheduled[0].delay, 2000);
  await timers.scheduled[0].callback();
  assert.equal(timers.scheduled[1].delay, 2000);
  await timers.scheduled[1].callback();
  assert.equal(timers.scheduled[2].delay, 2000);
  await timers.scheduled[2].callback();

  assert.equal(reloads, 1);
  assert.equal(timers.scheduled.length, 3);
});

test('backs off after a network failure without creating duplicate polling timers', async () => {
  const timers = clock();
  const poller = new SelfieSearchPoller({
    url: '/status/',
    fetch: async () => {
      throw new Error('offline');
    },
    setTimeout: timers.setTimeout,
    clearTimeout: timers.clearTimeout,
    reload() {},
  });

  await Promise.all([poller.poll(), poller.poll()]);

  assert.equal(timers.scheduled.length, 1);
  assert.equal(timers.scheduled[0].delay, 4000);
});
