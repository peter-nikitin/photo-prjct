'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const modulePath = '../../src/backend/static/ui/commerce-cart.js';

function makeControl({ photoId, selected = false } = {}) {
  const attributes = new Map();
  const icon = { attributes: new Map(), setAttribute(name, value) { this.attributes.set(name, value); } };
  const button = {
    disabled: false,
    attributes,
    setAttribute(name, value) { attributes.set(name, value); },
    getAttribute(name) { return attributes.get(name); },
  };
  const selectedInput = { value: selected ? '0' : '1' };
  return {
    dataset: { cartForm: '', photoId },
    action: '/events/london-10k/cart/photos/',
    method: 'post',
    button,
    selectedInput,
    csrf: { value: 'csrf-token' },
    icon,
    querySelector(selector) {
      if (selector === '[data-cart-button]') return button;
      if (selector === '[name="selected"]') return selectedInput;
      if (selector === '[name="csrfmiddlewaretoken"]') return this.csrf;
      if (selector === '[data-cart-icon] use') return icon;
      return null;
    },
  };
}

function makeRoot({ forms, counters = [], total = null, items = [], prices = [] }) {
  const listeners = new Map();
  const error = { hidden: true, textContent: '' };
  return {
    forms,
    counters,
    total,
    items,
    prices,
    error,
    addEventListener(type, listener) { listeners.set(type, listener); },
    submit(form, { defaultPrevented = false } = {}) {
      listeners.get('submit')?.({
        target: form,
        defaultPrevented,
        preventDefault() { this.prevented = true; },
      });
    },
    querySelectorAll(selector) {
      if (selector === '[data-cart-form]') return forms;
      if (selector === '[data-cart-count]') return counters;
      if (selector === '[data-cart-item][data-photo-id]') return items;
      if (selector === '[data-cart-price][data-photo-id]') return prices;
      return [];
    },
    querySelector(selector) {
      if (selector === '[data-cart-total]') return total;
      if (selector === '[data-cart-error]') return error;
      return null;
    },
  };
}

function loadModule() {
  delete require.cache[require.resolve(modulePath)];
  return require(modulePath);
}

test('waits for the authoritative same-origin response before synchronizing matching controls and counters', async () => {
  const first = makeControl({ photoId: 'photo-1' });
  const duplicate = makeControl({ photoId: 'photo-1' });
  const other = makeControl({ photoId: 'photo-2', selected: true });
  const counter = { textContent: '0' };
  const total = { textContent: '0 ₽' };
  const root = makeRoot({ forms: [first, duplicate, other], counters: [counter], total });
  let resolveResponse;
  const calls = [];
  const fetch = (...args) => {
    calls.push(args);
    return new Promise((resolve) => { resolveResponse = resolve; });
  };
  const { initializeCommerceCart } = loadModule();
  initializeCommerceCart(root, { fetch, FormData: class { constructor(form) { this.form = form; } } });

  root.submit(first);
  assert.equal(first.button.disabled, true);
  assert.equal(duplicate.selectedInput.value, '1');
  assert.equal(counter.textContent, '0');

  resolveResponse({ ok: true, json: async () => ({ photo_id: 'photo-1', selected: true, item_count: 1, total_display: '300 ₽' }) });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], '/events/london-10k/cart/photos/');
  assert.equal(calls[0][1].credentials, 'same-origin');
  assert.equal(calls[0][1].headers.Accept, 'application/json');
  assert.equal(calls[0][1].headers['X-CSRFToken'], 'csrf-token');
  assert.equal(first.button.disabled, false);
  assert.equal(first.selectedInput.value, '0');
  assert.equal(duplicate.selectedInput.value, '0');
  assert.equal(other.selectedInput.value, '0');
  assert.equal(counter.textContent, '1');
  assert.equal(total.textContent, '300 ₽');
  assert.equal(first.button.getAttribute('aria-label'), 'Удалить из корзины');
  assert.equal(first.icon.attributes.get('href'), '/static/ui/icons.svg#cart-check');
});

test('keeps the current state after a failed enhanced mutation and announces the retry message', async () => {
  const form = makeControl({ photoId: 'photo-1', selected: true });
  const counter = { textContent: '1' };
  const root = makeRoot({ forms: [form], counters: [counter] });
  const { initializeCommerceCart } = loadModule();
  initializeCommerceCart(root, {
    fetch: async () => ({ ok: false }),
    FormData: class { constructor(formElement) { this.form = formElement; } },
  });

  root.submit(form);
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(form.selectedInput.value, '0');
  assert.equal(counter.textContent, '1');
  assert.equal(form.button.disabled, false);
  assert.equal(root.error.hidden, false);
  assert.equal(root.error.textContent, 'Не удалось обновить корзину. Попробуйте ещё раз.');
});

test('does not enhance a cart form submission cancelled by its native confirmation', () => {
  const form = makeControl({ photoId: 'photo-1', selected: true });
  const root = makeRoot({ forms: [form] });
  let fetchCalls = 0;
  const { initializeCommerceCart } = loadModule();
  initializeCommerceCart(root, {
    fetch: async () => { fetchCalls += 1; return { ok: true, json: async () => ({}) }; },
    FormData: class { constructor(formElement) { this.form = formElement; } },
  });

  root.submit(form, { defaultPrevented: true });

  assert.equal(fetchCalls, 0);
  assert.equal(form.button.disabled, false);
  assert.equal(form.selectedInput.value, '0');
});

test('removes the confirmed cart row and renders the server-confirmed empty state after the final removal', async () => {
  const form = makeControl({ photoId: 'photo-1', selected: true });
  const item = { dataset: { photoId: 'photo-1' }, removed: false, remove() { this.removed = true; } };
  const empty = { hidden: true };
  const root = makeRoot({ forms: [form], items: [item] });
  root.querySelector = (selector) => {
    if (selector === '[data-cart-empty]') return empty;
    if (selector === '[data-cart-error]') return root.error;
    return null;
  };
  const { initializeCommerceCart } = loadModule();
  initializeCommerceCart(root, {
    fetch: async () => ({ ok: true, json: async () => ({ photo_id: 'photo-1', selected: false, item_count: 0, total_display: '0 ₽' }) }),
    FormData: class { constructor(formElement) { this.form = formElement; } },
  });

  root.submit(form);
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(item.removed, true);
  assert.equal(empty.hidden, false);
});

test('uses the event-wide authoritative price and count for every current-event cart surface', () => {
  const card = makeControl({ photoId: 'photo-1' });
  const cartLinkAttributes = new Map();
  const cartLink = {
    setAttribute(name, value) { cartLinkAttributes.set(name, value); },
  };
  const counter = {
    dataset: {},
    textContent: '0',
    closest(selector) { return selector === '.event-cart-link' ? cartLink : null; },
  };
  const price = (photoId, kind) => ({ dataset: { photoId, kind }, textContent: '300 ₽' });
  const prices = [
    price('photo-1', 'card'),
    price('photo-1', 'lightbox-source'),
    price('photo-2', 'card'),
    price('photo-2', 'cart-item'),
  ];
  const root = makeRoot({ forms: [card], counters: [counter], prices });
  const { applySnapshot } = loadModule();

  applySnapshot(root, {
    photo_id: 'photo-1',
    selected: true,
    item_count: 1,
    unit_price_display: '450,75 ₽',
    total_display: '450,75 ₽',
  });

  assert.deepEqual(prices.map((surface) => surface.textContent), [
    '450,75 ₽',
    '450,75 ₽',
    '450,75 ₽',
    '450,75 ₽',
  ]);
  assert.equal(counter.textContent, '1');
  assert.equal(cartLinkAttributes.get('aria-label'), 'Корзина: 1');
});
