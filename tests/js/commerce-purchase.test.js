'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const modulePath = '../../src/backend/static/ui/commerce-purchase.js';

function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

function makeStatusElement(initial = 'Проверяем оплату') {
  return {
    dataset: { orderStatus: 'pending' },
    textContent: initial,
    focusCalls: 0,
    focus() { this.focusCalls += 1; },
  };
}

function makePurchaseRoot({ status = 'pending', statusUrl = '/orders/FM-ABCDEFGH/status/' } = {}) {
  const listeners = new Map();
  const statusElement = makeStatusElement();
  const pollMessage = { hidden: true, textContent: '' };
  const resendMessage = { hidden: true, textContent: '' };
  const resendButton = { disabled: false };
  const resendForm = {
    dataset: { orderResend: '' },
    action: '/orders/FM-ABCDEFGH/resend/',
    method: 'post',
    querySelector(selector) {
      if (selector === '[data-order-resend-button]') return resendButton;
      if (selector === '[name="csrfmiddlewaretoken"]') return { value: 'csrf-token' };
      return null;
    },
  };
  const root = {
    dataset: {
      orderStatus: status,
      orderStatusUrl: statusUrl,
      orderUrl: '/orders/FM-ABCDEFGH/',
    },
    addEventListener(type, listener) { listeners.set(type, listener); },
    querySelector(selector) {
      if (selector === '[data-order-status]') return statusElement;
      if (selector === '[data-order-poll-message]') return pollMessage;
      if (selector === '[data-order-resend-message]') return resendMessage;
      return null;
    },
    submit(form = resendForm) {
      const event = {
        target: form,
        defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; },
      };
      listeners.get('submit')?.(event);
      return event;
    },
  };
  return { root, statusElement, pollMessage, resendMessage, resendButton, resendForm };
}

function loadModule() {
  delete require.cache[require.resolve(modulePath)];
  return require(modulePath);
}

test('polls only a same-origin pending Order a bounded number of times and stops after a terminal response', async () => {
  const { root, statusElement } = makePurchaseRoot();
  const scheduled = [];
  const calls = [];
  const location = {
    origin: 'https://findme.test',
    href: 'https://findme.test/orders/FM-ABCDEFGH/',
    assigned: [],
    assign(url) { this.assigned.push(url); },
  };
  const responses = ['pending', 'pending', 'paid'];
  const { initializeCommercePurchase } = loadModule();

  initializeCommercePurchase(root, {
    fetch: async (...args) => {
      calls.push(args);
      return { ok: true, json: async () => ({ status: responses.shift() }) };
    },
    location,
    setTimeout(callback) { scheduled.push(callback); return scheduled.length; },
  });
  await flush();
  await scheduled.shift()();
  await flush();
  await scheduled.shift()();
  await flush();

  assert.equal(calls.length, 3);
  assert.equal(calls[0][0], '/orders/FM-ABCDEFGH/status/');
  assert.equal(calls[0][1].credentials, 'same-origin');
  assert.equal(calls[0][1].headers.Accept, 'application/json');
  assert.equal(statusElement.dataset.orderStatus, 'paid');
  assert.equal(statusElement.textContent, 'Заказ оплачен. Обновляем страницу.');
  assert.equal(statusElement.focusCalls, 1);
  assert.deepEqual(location.assigned, ['/orders/FM-ABCDEFGH/']);
  assert.equal(scheduled.length, 0);
});

test('caps a same-origin pending Order at exactly the configured status request limit', async () => {
  const { root } = makePurchaseRoot();
  const scheduled = [];
  let calls = 0;
  const { MAX_STATUS_REQUESTS, initializeCommercePurchase } = loadModule();

  initializeCommercePurchase(root, {
    fetch: async () => {
      calls += 1;
      return { ok: true, json: async () => ({ status: 'pending' }) };
    },
    location: { origin: 'https://findme.test', href: 'https://findme.test/orders/FM-ABCDEFGH/' },
    setTimeout(callback) { scheduled.push(callback); return scheduled.length; },
  });
  await flush();
  while (scheduled.length) {
    await scheduled.shift()();
    await flush();
  }

  assert.equal(calls, MAX_STATUS_REQUESTS);
  assert.equal(scheduled.length, 0);
  await flush();
  assert.equal(calls, MAX_STATUS_REQUESTS);
});

test('does not poll a foreign status URL or promote an Order without an authoritative paid response', async () => {
  const { root, statusElement } = makePurchaseRoot({ statusUrl: 'https://elsewhere.test/status/' });
  let fetchCalls = 0;
  const { initializeCommercePurchase } = loadModule();

  initializeCommercePurchase(root, {
    fetch: async () => { fetchCalls += 1; return { ok: true, json: async () => ({ status: 'paid' }) }; },
    location: { origin: 'https://findme.test', href: 'https://findme.test/orders/FM-ABCDEFGH/' },
  });
  await flush();

  assert.equal(fetchCalls, 0);
  assert.equal(statusElement.dataset.orderStatus, 'pending');
  assert.equal(statusElement.textContent, 'Проверяем оплату');
});

test('keeps the pending state and safely announces a failed status request', async () => {
  const { root, statusElement, pollMessage } = makePurchaseRoot();
  const scheduled = [];
  const { initializeCommercePurchase } = loadModule();

  initializeCommercePurchase(root, {
    fetch: async () => { throw new Error('offline'); },
    location: { origin: 'https://findme.test', href: 'https://findme.test/orders/FM-ABCDEFGH/' },
    setTimeout(callback) { scheduled.push(callback); return scheduled.length; },
  });
  await flush();

  assert.equal(statusElement.dataset.orderStatus, 'pending');
  assert.equal(statusElement.textContent, 'Проверяем оплату');
  assert.equal(pollMessage.hidden, false);
  assert.equal(pollMessage.textContent, 'Не удалось проверить оплату. Обновите страницу позднее.');
  assert.equal(scheduled.length, 1);
});

test('updates resend feedback only after the server response and leaves failed resends retryable', async () => {
  const { root, resendMessage, resendButton, resendForm } = makePurchaseRoot({ status: 'paid' });
  const responses = [{ ok: false, status: 429 }, { ok: true, status: 200 }];
  const calls = [];
  const { initializeCommercePurchase } = loadModule();
  initializeCommercePurchase(root, {
    fetch: async (...args) => {
      calls.push(args);
      return responses.shift();
    },
    FormData: class { constructor(form) { this.form = form; } },
    location: { origin: 'https://findme.test', href: 'https://findme.test/orders/FM-ABCDEFGH/' },
  });

  const first = root.submit(resendForm);
  assert.equal(first.defaultPrevented, true);
  assert.equal(resendButton.disabled, true);
  assert.equal(resendMessage.hidden, true);
  await flush();
  assert.equal(resendButton.disabled, false);
  assert.equal(resendMessage.hidden, false);
  assert.equal(resendMessage.textContent, 'Подождите минуту перед повторной отправкой письма.');

  root.submit(resendForm);
  await flush();
  assert.equal(resendMessage.textContent, 'Письмо со ссылкой поставлено в очередь.');
  assert.equal(calls.length, 2);
  assert.equal(calls[0][0], '/orders/FM-ABCDEFGH/resend/');
  assert.equal(calls[0][1].credentials, 'same-origin');
  assert.equal(calls[0][1].headers['X-CSRFToken'], 'csrf-token');
});
