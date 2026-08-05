'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const {
  BrowserStorageAdapter,
  FEEDBACK_PENDING_KEY,
  FeedbackUiController,
  FeedbackMarkStore,
  SELFIE_RETENTION_MS,
  SelfieSearchPoller,
  bindSelfieSearchForm,
  initializeBrowserStorage,
  initializeFeedbackCleanupUi,
  initializeFeedbackUi,
  startBrowserUi,
} = require('../../src/backend/static/ui/selfie-search.js');

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

class MemoryDatabase {
  constructor() {
    this.records = new Map();
  }

  async put(record) {
    this.records.set(record.handle, structuredClone(record));
  }

  async get(handle) {
    const record = this.records.get(handle);
    return record ? structuredClone(record) : undefined;
  }

  async getAll() {
    return [...this.records.values()].map((record) => structuredClone(record));
  }

  async delete(handle) {
    this.records.delete(handle);
  }
}

class MemoryStorage {
  constructor(initial = {}) {
    this.values = new Map(Object.entries(initial));
  }

  getItem(key) {
    return this.values.has(key) ? this.values.get(key) : null;
  }

  setItem(key, value) {
    this.values.set(key, String(value));
  }

  removeItem(key) {
    this.values.delete(key);
  }

  get length() {
    return this.values.size;
  }

  key(index) {
    return [...this.values.keys()][index] || null;
  }
}

function makeFile(bytes = new Uint8Array([1, 2, 3]), type = 'image/jpeg') {
  return {
    type,
    size: bytes.byteLength,
    async arrayBuffer() {
      return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    },
  };
}

function makeAdapter({ now = 1_000, randomUUID = () => 'handle-1', ...overrides } = {}) {
  return new BrowserStorageAdapter({
    db: new MemoryDatabase(),
    sessionStorage: new MemoryStorage(),
    now: () => now,
    randomUUID,
    ...overrides,
  });
}

class FakeElement {
  constructor({ dataset = {}, value = '', checked = false, hidden = false } = {}) {
    this.dataset = dataset;
    this.value = value;
    this.checked = checked;
    this.hidden = hidden;
    this.disabled = false;
    this.textContent = '';
    this.innerHTML = '';
    this.listeners = new Map();
    this.attributes = new Map();
    this.queries = new Map();
    this.queryLists = new Map();
    this.queriedSelectors = [];
    this.focusCount = 0;
  }

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }

  async trigger(type) {
    const listener = this.listeners.get(type);
    return listener?.({
      preventDefault() {},
      stopPropagation() {},
    });
  }

  querySelector(selector) {
    this.queriedSelectors.push(selector);
    return this.queries.get(selector) || null;
  }

  querySelectorAll(selector) {
    return this.queryLists.get(selector) || [];
  }

  setAttribute(name, value) {
    this.attributes.set(name, value);
    if (name === 'hidden') this.hidden = true;
  }

  removeAttribute(name) {
    this.attributes.delete(name);
    if (name === 'hidden') this.hidden = false;
  }

  focus() {
    this.focusCount += 1;
  }
}

function makeFeedbackFixture({
  resultDigest = 'a'.repeat(64),
  variant = 'result_labels',
  total = 2,
  hidden = false,
} = {}) {
  const root = new FakeElement({
    dataset: {
      feedbackVariant: variant,
      feedbackTotal: String(total),
      feedbackUrl: '/selfie-search/feedback/',
    },
    hidden,
  });
  const form = new FakeElement({ hidden });
  const contact = new FakeElement();
  const consent = new FakeElement();
  const contactError = new FakeElement({ hidden: true });
  const consentError = new FakeElement({ hidden: true });
  const submitError = new FakeElement({ hidden: true });
  const submitButton = new FakeElement();
  const csrf = new FakeElement({ value: 'csrf-token' });
  const controls = [new FakeElement({ hidden }), new FakeElement({ hidden })];
  const labelButtons = [
    new FakeElement({ dataset: { feedbackLabel: 'present', feedbackResultId: 'photo-a' } }),
    new FakeElement({ dataset: { feedbackLabel: 'absent', feedbackResultId: 'photo-b' } }),
  ];

  root.queries.set('[data-feedback-form]', form);
  root.queries.set('[data-feedback-contact]', contact);
  root.queries.set('[name="contact"]', contact);
  root.queries.set('[data-feedback-consent]', consent);
  root.queries.set('[name="personal_data_consent"]', consent);
  root.queries.set('[data-feedback-contact-error]', contactError);
  root.queries.set('[data-feedback-consent-error]', consentError);
  root.queries.set('[data-feedback-submit-error]', submitError);
  root.queries.set('[data-feedback-submit]', submitButton);
  form.queries.set('[name=csrfmiddlewaretoken]', csrf);
  form.queries.set('[name="csrfmiddlewaretoken"]', csrf);
  form.queries.set('[data-feedback-submit]', submitButton);

  const document = {
    queries: new Map(),
    querySelector(selector) {
      if (selector === '[data-selfie-feedback]') return root;
      return this.queries.get(selector) || null;
    },
    querySelectorAll(selector) {
      if (selector === '[data-feedback-card-controls]') return controls;
      if (selector === '[data-feedback-label]') return labelButtons;
      return [];
    },
  };
  root.ownerDocument = document;

  return {
    root,
    form,
    contact,
    consent,
    contactError,
    consentError,
    submitError,
    submitButton,
    controls,
    labelButtons,
    document,
    resultDigest,
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

test('focuses an existing selfie rejection summary when the page starts', () => {
  let focusCount = 0;
  const form = { addEventListener() {} };
  const error = { focus() { focusCount += 1; } };
  const document = {
    querySelector(selector) {
      if (selector === '[data-selfie-search-form]') return form;
      if (selector === '[data-selfie-search-error]') return error;
      return null;
    },
  };

  startBrowserUi(document, {});

  assert.equal(focusCount, 1);
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

test('preserves the exact selected bytes with bounded canonical metadata and a tab-local handle', async () => {
  const sessionStorage = new MemoryStorage();
  const db = new MemoryDatabase();
  const storage = new BrowserStorageAdapter({
    db,
    sessionStorage,
    now: () => 10_000,
    randomUUID: () => 'handle-1',
  });
  const file = makeFile(new Uint8Array([9, 8, 7]), 'image/jpg');

  const result = await storage.preserveSelectedSelfie(file, { correlation: 'a'.repeat(32) });

  assert.equal(result.stored, true);
  assert.equal(result.handle, 'handle-1');
  assert.equal(sessionStorage.getItem(storage.pendingKey), 'handle-1');
  const record = await db.get('handle-1');
  assert.deepEqual([...new Uint8Array(record.bytes)], [9, 8, 7]);
  assert.equal(record.mediaType, 'image/jpeg');
  assert.equal(record.byteCount, 3);
  assert.equal(record.createdAt, 10_000);
  assert.equal(record.expiresAt, 10_000 + SELFIE_RETENTION_MS);
  assert.equal(record.resultTokenDigest, '');
  assert.equal(record.feedbackCorrelation, 'a'.repeat(32));
  assert.equal(sessionStorage.values.size, 1);
});

test('disabled feedback clears stale local records and submits search without preserving new bytes', async () => {
  let submitListener;
  let nativeSubmitCount = 0;
  let preserved = 0;
  let cleared = 0;
  const form = {
    dataset: { selfieFeedbackEnabled: 'false' },
    addEventListener(type, listener) {
      if (type === 'submit') submitListener = listener;
    },
    querySelector(selector) {
      if (selector === 'button[type="submit"]') return { disabled: false, textContent: '' };
      if (selector === 'input[type="file"]') return { files: [makeFile()] };
      return null;
    },
    submit() { nativeSubmitCount += 1; },
  };
  const storage = {
    async clearAll() { cleared += 1; throw new Error('blocked'); },
    async preserveSelectedSelfie() { preserved += 1; },
  };
  const document = {
    querySelector(selector) {
      if (selector === '[data-selfie-search-form]') return form;
      return null;
    },
  };

  startBrowserUi(document, {}, { storage });
  await Promise.resolve();
  await submitListener({ preventDefault() {} });

  assert.equal(cleared, 1);
  assert.equal(preserved, 0);
  assert.equal(nativeSubmitCount, 1);
});

test('simultaneous search tabs generate and preserve independent browser correlations', async () => {
  function tab(correlation) {
    let submitListener;
    let nativeSubmitCount = 0;
    const hidden = { value: '' };
    const form = {
      dataset: { selfieFeedbackEnabled: 'true' },
      addEventListener(type, listener) {
        if (type === 'submit') submitListener = listener;
      },
      querySelector(selector) {
        if (selector === 'button[type="submit"]') return { disabled: false, textContent: '' };
        if (selector === 'input[type="file"]') return { files: [makeFile()] };
        if (selector === '[name="feedback_correlation"]') return hidden;
        return null;
      },
      submit() { nativeSubmitCount += 1; },
    };
    let preservedCorrelation = '';
    const storage = {
      createFeedbackCorrelation() { return correlation; },
      async preserveSelectedSelfie(_file, options) {
        preservedCorrelation = options.correlation;
      },
    };
    bindSelfieSearchForm(form, { storage });
    return {
      correlation,
      form,
      hidden,
      submit: () => submitListener({ preventDefault() {} }),
      preservedCorrelation: () => preservedCorrelation,
      nativeSubmitCount: () => nativeSubmitCount,
    };
  }

  const first = tab('a'.repeat(32));
  const second = tab('b'.repeat(32));

  await Promise.all([first.submit(), second.submit()]);

  for (const current of [first, second]) {
    assert.equal(current.hidden.value, current.correlation);
    assert.equal(current.preservedCorrelation(), current.correlation);
    assert.equal(current.nativeSubmitCount(), 1);
  }
});

test('failed search page clears its pending selfie and unrelated result cannot consume it', async () => {
  const db = new MemoryDatabase();
  const sessionStorage = new MemoryStorage();
  const storage = new BrowserStorageAdapter({
    db,
    sessionStorage,
    now: () => 100,
    randomUUID: () => 'failed-search',
  });
  await storage.preserveSelectedSelfie(makeFile(), { correlation: 'a'.repeat(32) });

  await initializeBrowserStorage({ storage, result: null, window: {} });

  assert.equal(sessionStorage.getItem(FEEDBACK_PENDING_KEY), null);
  assert.equal((await db.getAll()).length, 0);

  await storage.preserveSelectedSelfie(makeFile(), { correlation: 'a'.repeat(32) });
  await initializeBrowserStorage({
    storage,
    result: {
      dataset: {
        resultDigest: 'b'.repeat(64),
        feedbackCorrelation: 'c'.repeat(32),
      },
    },
    window: {},
  });

  assert.equal(sessionStorage.getItem(FEEDBACK_PENDING_KEY), null);
  assert.equal((await db.getAll()).length, 0);
});

test('associates a pending selfie only when the successful redirect correlation matches', async () => {
  const db = new MemoryDatabase();
  const storage = new BrowserStorageAdapter({
    db,
    sessionStorage: new MemoryStorage(),
    now: () => 100,
    randomUUID: () => 'matching-search',
  });
  await storage.preserveSelectedSelfie(makeFile(), { correlation: 'd'.repeat(32) });

  await initializeBrowserStorage({
    storage,
    result: {
      dataset: {
        resultDigest: 'e'.repeat(64),
        feedbackCorrelation: 'd'.repeat(32),
      },
    },
    window: {},
  });

  assert.equal((await storage.getAssociatedSelfie('e'.repeat(64))).handle, 'matching-search');
});

test('keeps simultaneous tab pending handles associated with their own result digest', async () => {
  const db = new MemoryDatabase();
  const first = new BrowserStorageAdapter({
    db,
    sessionStorage: new MemoryStorage(),
    now: () => 100,
    randomUUID: () => 'first-handle',
  });
  const second = new BrowserStorageAdapter({
    db,
    sessionStorage: new MemoryStorage(),
    now: () => 100,
    randomUUID: () => 'second-handle',
  });

  await first.preserveSelectedSelfie(makeFile(new Uint8Array([1])), { correlation: 'a'.repeat(32) });
  await second.preserveSelectedSelfie(makeFile(new Uint8Array([2])), { correlation: 'b'.repeat(32) });
  await first.associatePendingSelfie({ resultDigest: 'a'.repeat(64), correlation: 'a'.repeat(32) });
  await second.associatePendingSelfie({ resultDigest: 'b'.repeat(64), correlation: 'b'.repeat(32) });

  assert.equal((await db.get('first-handle')).resultTokenDigest, 'a'.repeat(64));
  assert.equal((await db.get('second-handle')).resultTokenDigest, 'b'.repeat(64));
  assert.equal(first.getPendingHandle(), null);
  assert.equal(second.getPendingHandle(), null);
});

test('opportunistically deletes records at the seven-day expiry boundary', async () => {
  let now = 1_000;
  const db = new MemoryDatabase();
  const storage = new BrowserStorageAdapter({
    db,
    sessionStorage: new MemoryStorage(),
    now: () => now,
    randomUUID: () => 'expired-handle',
  });

  await storage.preserveSelectedSelfie(makeFile());
  now += SELFIE_RETENTION_MS;

  const removed = await storage.cleanupExpired();

  assert.equal(removed, 1);
  assert.equal(await db.get('expired-handle'), undefined);
});

test('successful feedback cleanup removes the associated local selfie', async () => {
  const db = new MemoryDatabase();
  const storage = new BrowserStorageAdapter({
    db,
    sessionStorage: new MemoryStorage(),
    now: () => 100,
    randomUUID: () => 'feedback-handle',
  });
  await storage.preserveSelectedSelfie(makeFile(), { correlation: 'c'.repeat(32) });
  await storage.associatePendingSelfie({ resultDigest: 'c'.repeat(64), correlation: 'c'.repeat(32) });

  const removed = await storage.clearAssociatedSelfie('c'.repeat(64));

  assert.equal(removed, true);
  assert.equal(await db.get('feedback-handle'), undefined);
});

test('feedback cleanup treats a successfully inspected empty database as already clean', async () => {
  const storage = makeAdapter({ db: new MemoryDatabase() });

  const cleaned = await storage.clearAssociatedSelfie('d'.repeat(64));

  assert.equal(cleaned, true);
});

test('IndexedDB write failure does not block the ordinary search path', async () => {
  const storage = makeAdapter({
    db: {
      async put() {
        throw new Error('quota');
      },
      async delete() {},
    },
  });

  const result = await storage.preserveSelectedSelfie(makeFile());

  assert.equal(result.stored, false);
  assert.equal(result.reason, 'storage_unavailable');
});

test('association read failure leaves the tab pending handle available for a later retry', async () => {
  const sessionStorage = new MemoryStorage({ [FEEDBACK_PENDING_KEY]: 'pending' });
  const storage = new BrowserStorageAdapter({
    db: {
      async get() {
        throw new Error('blocked');
      },
    },
    sessionStorage,
    now: () => 100,
  });

  const result = await storage.associatePendingSelfie({
    resultDigest: 'd'.repeat(64),
    correlation: 'd'.repeat(32),
  });

  assert.equal(result.associated, false);
  assert.equal(result.reason, 'storage_unavailable');
  assert.equal(sessionStorage.getItem(FEEDBACK_PENDING_KEY), 'pending');
});

test('search submission continues after every storage error before the native POST', async () => {
  let submitListener;
  let nativeSubmitCount = 0;
  let prevented = false;
  const button = { disabled: false, textContent: 'Найти мои фото' };
  const form = {
    dataset: { selfieFeedbackEnabled: 'true' },
    querySelector(selector) {
      if (selector === 'button[type="submit"]') return button;
      if (selector === 'input[type="file"]') return { files: [makeFile()] };
      if (selector === '[name="feedback_correlation"]') return { value: 'e'.repeat(32) };
      return null;
    },
    addEventListener(type, listener) {
      if (type === 'submit') submitListener = listener;
    },
    submit() {
      nativeSubmitCount += 1;
    },
  };
  const storage = {
    async preserveSelectedSelfie() {
      throw new Error('storage unavailable');
    },
  };

  bindSelfieSearchForm(form, { storage });
  await submitListener({ preventDefault() { prevented = true; } });

  assert.equal(prevented, true);
  assert.equal(nativeSubmitCount, 1);
  assert.equal(button.disabled, true);
});

test('a repeated submit is prevented while preservation is pending and produces one native POST', async () => {
  let submitListener;
  let nativeSubmitCount = 0;
  let resolvePreservation;
  const button = { disabled: false, textContent: 'Найти мои фото' };
  const form = {
    dataset: { selfieFeedbackEnabled: 'true' },
    querySelector(selector) {
      if (selector === 'button[type="submit"]') return button;
      if (selector === 'input[type="file"]') return { files: [makeFile()] };
      if (selector === '[name="feedback_correlation"]') return { value: 'f'.repeat(32) };
      return null;
    },
    addEventListener(type, listener) {
      if (type === 'submit') submitListener = listener;
    },
    submit() {
      nativeSubmitCount += 1;
    },
  };
  const storage = {
    preserveSelectedSelfie() {
      return new Promise((resolve) => {
        resolvePreservation = resolve;
      });
    },
  };
  let firstPrevented = false;
  let secondPrevented = false;

  bindSelfieSearchForm(form, { storage });
  const firstSubmit = submitListener({ preventDefault() { firstPrevented = true; } });
  const secondSubmit = submitListener({ preventDefault() { secondPrevented = true; } });
  await Promise.resolve();

  assert.equal(firstPrevented, true);
  assert.equal(secondPrevented, true);
  assert.equal(nativeSubmitCount, 0);
  resolvePreservation({ stored: true });
  await firstSubmit;
  await secondSubmit;

  assert.equal(nativeSubmitCount, 1);
});

test('keeps optional result marks keyed by digest across numbered result pages and clears an active choice', () => {
  const sessionStorage = new MemoryStorage();
  const digest = 'f'.repeat(64);
  const firstPage = new FeedbackMarkStore({ sessionStorage, resultDigest: digest });

  assert.deepEqual(firstPage.getMarks(), {});
  assert.equal(firstPage.toggle('11111111-1111-4111-8111-111111111111', 'present'), 'present');
  assert.equal(firstPage.toggle('22222222-2222-4222-8222-222222222222', 'absent'), 'absent');
  assert.equal(firstPage.count(), 2);

  const secondPage = new FeedbackMarkStore({ sessionStorage, resultDigest: digest });
  assert.deepEqual(secondPage.getMarks(), {
    '11111111-1111-4111-8111-111111111111': 'present',
    '22222222-2222-4222-8222-222222222222': 'absent',
  });
  assert.equal(secondPage.toggle('11111111-1111-4111-8111-111111111111', 'present'), null);
  assert.equal(secondPage.count(), 1);
});

test('clears stale result marks before reload so the refreshed result can submit', async () => {
  const sessionStorage = new MemoryStorage();
  const digest = 'e'.repeat(64);
  const selfie = { bytes: new Uint8Array([1, 2, 3]), mediaType: 'image/jpeg' };
  const first = makeFeedbackFixture({ resultDigest: digest });
  let reloads = 0;
  const firstWindow = {
    sessionStorage,
    location: { reload() { reloads += 1; } },
    async fetch() {
      return { json: async () => ({ status: 'result_changed' }) };
    },
  };
  const storage = {
    async getAssociatedSelfie() { return selfie; },
    async clearAssociatedSelfie() { return true; },
  };
  const firstController = new FeedbackUiController({
    root: first.root,
    storage,
    resultDigest: digest,
    window: firstWindow,
    document: first.document,
  });
  firstController.marks.toggle('photo-a', 'present');
  assert.equal(sessionStorage.getItem(`findme_selfie_feedback_marks:${digest}`), '{"photo-a":"present"}');
  first.contact.value = 'contact';
  first.consent.checked = true;

  await firstController.submit({ preventDefault() {} });

  assert.equal(sessionStorage.getItem(`findme_selfie_feedback_marks:${digest}`), null);
  assert.equal(reloads, 1);

  const refreshed = makeFeedbackFixture({ resultDigest: digest });
  let submittedLabels;
  const refreshedController = new FeedbackUiController({
    root: refreshed.root,
    storage,
    resultDigest: digest,
    document: refreshed.document,
    window: {
      sessionStorage,
      location: { reload() {} },
      async fetch(_url, options) {
        submittedLabels = options.body.get('labels');
        return { json: async () => ({ status: 'submitted' }) };
      },
    },
  });
  refreshed.contact.value = 'contact';
  refreshed.consent.checked = true;

  await refreshedController.submit({ preventDefault() {} });

  assert.equal(submittedLabels, '{}');
  assert.match(refreshed.root.innerHTML, /Спасибо, отзыв отправлен/);
});

test('does not expose a feedback form when the associated local selfie is missing, expired, or inaccessible', async () => {
  for (const condition of ['missing', 'expired', 'inaccessible']) {
    const fixture = makeFeedbackFixture({ hidden: true });
    const unavailable = new FakeElement({ hidden: true });
    fixture.document.queries.set('[data-feedback-unavailable]', unavailable);

    const controller = await initializeFeedbackUi({
      document: fixture.document,
      window: {},
      storage: { async getAssociatedSelfie() { return null; } },
      result: { dataset: { resultDigest: fixture.resultDigest } },
    });

    assert.equal(controller, null, condition);
    assert.equal(fixture.root.hidden, true, condition);
    assert.equal(unavailable.hidden, false, condition);
  }
});

test('submits multipart feedback with CSRF, accepts zero marks, and removes the local selfie on success', async () => {
  const fixture = makeFeedbackFixture();
  fixture.contact.value = 'telegram: @findme';
  fixture.consent.checked = true;
  const selfie = { bytes: new Uint8Array([7, 8, 9]), mediaType: 'image/jpeg' };
  let request;
  let cleared = 0;
  const controller = new FeedbackUiController({
    root: fixture.root,
    resultDigest: fixture.resultDigest,
    document: fixture.document,
    storage: {
      async getAssociatedSelfie() { return selfie; },
      async clearAssociatedSelfie() { cleared += 1; return true; },
    },
    window: {
      sessionStorage: new MemoryStorage(),
      async fetch(url, options) {
        request = { url, options };
        return { json: async () => ({ status: 'submitted' }) };
      },
    },
  });

  await controller.submit({ preventDefault() {} });

  assert.equal(request.url, '/selfie-search/feedback/');
  assert.equal(request.options.method, 'POST');
  assert.equal(request.options.credentials, 'same-origin');
  assert.equal(request.options.headers['X-CSRFToken'], 'csrf-token');
  assert.equal(request.options.body.get('contact'), 'telegram: @findme');
  assert.equal(request.options.body.get('personal_data_consent'), 'true');
  assert.equal(request.options.body.get('labels'), '{}');
  assert.equal(request.options.body.get('csrfmiddlewaretoken'), 'csrf-token');
  assert.equal((await request.options.body.get('selfie').arrayBuffer()).byteLength, 3);
  assert.equal(cleared, 1);
  assert.match(fixture.root.innerHTML, /Спасибо, отзыв отправлен/);
});

test('does not claim completion until local selfie cleanup succeeds and permits an idempotent retry', async () => {
  const fixture = makeFeedbackFixture();
  fixture.contact.value = 'contact';
  fixture.consent.checked = true;
  let cleanupAttempts = 0;
  let submissions = 0;
  const controller = new FeedbackUiController({
    root: fixture.root,
    resultDigest: fixture.resultDigest,
    document: fixture.document,
    storage: {
      async getAssociatedSelfie() {
        return { bytes: new Uint8Array([1]), mediaType: 'image/jpeg' };
      },
      async clearAssociatedSelfie() {
        cleanupAttempts += 1;
        return cleanupAttempts > 1;
      },
    },
    window: {
      sessionStorage: new MemoryStorage(),
      async fetch() {
        submissions += 1;
        return { json: async () => ({ status: submissions === 1 ? 'submitted' : 'already_submitted' }) };
      },
    },
  });

  await controller.submit({ preventDefault() {} });

  assert.doesNotMatch(fixture.root.innerHTML, /Спасибо, отзыв отправлен/);
  assert.equal(fixture.submitError.hidden, false);
  assert.match(fixture.submitError.textContent, /повторите отправку/i);
  assert.equal(fixture.submitButton.disabled, false);

  await controller.submit({ preventDefault() {} });

  assert.equal(submissions, 2);
  assert.equal(cleanupAttempts, 2);
  assert.match(fixture.root.innerHTML, /Спасибо, отзыв отправлен/);
});

test('reload after delete failure reports cleanup error without exposing a retry action', async () => {
  assert.equal(typeof initializeFeedbackCleanupUi, 'function');
  const initial = makeFeedbackFixture();
  initial.contact.value = 'contact';
  initial.consent.checked = true;
  let cleanupAttempts = 0;
  const storage = {
    async getAssociatedSelfie() {
      return { bytes: new Uint8Array([1]), mediaType: 'image/jpeg' };
    },
    async clearAssociatedSelfie() {
      cleanupAttempts += 1;
      return cleanupAttempts > 2;
    },
  };
  const sessionStorage = new MemoryStorage();
  const controller = new FeedbackUiController({
    root: initial.root,
    resultDigest: initial.resultDigest,
    document: initial.document,
    storage,
    window: {
      sessionStorage,
      async fetch() { return { json: async () => ({ status: 'submitted' }) }; },
    },
  });

  await controller.submit({ preventDefault() {} });
  assert.doesNotMatch(initial.root.innerHTML, /Спасибо, отзыв отправлен/);

  const cleanupRoot = new FakeElement();
  const cleanupError = new FakeElement({ hidden: true });
  const cleanupSuccess = new FakeElement({ hidden: true });
  cleanupRoot.queries.set('[data-feedback-cleanup-error]', cleanupError);
  cleanupRoot.queries.set('[data-feedback-cleanup-success]', cleanupSuccess);
  const document = {
    querySelector(selector) {
      if (selector === '[data-feedback-cleanup]') return cleanupRoot;
      return null;
    },
  };
  const result = { dataset: { resultDigest: initial.resultDigest } };

  const cleanupController = await initializeFeedbackCleanupUi({
    document,
    storage,
    result,
    window: { sessionStorage },
  });

  assert.equal(cleanupAttempts, 2);
  assert.equal(cleanupSuccess.hidden, true);
  assert.equal(cleanupError.hidden, false);
  assert.match(cleanupError.textContent, /Повторите очистку/);
  assert.equal(cleanupController.retry, undefined);
  assert.equal(cleanupRoot.queriedSelectors.includes('[data-feedback-cleanup-retry]'), false);
});

function makeSubmittedStartupFixture({ storage }) {
  const resultDigest = 'e'.repeat(64);
  const cleanupRoot = new FakeElement();
  const pending = new FakeElement();
  const error = new FakeElement({ hidden: true });
  const success = new FakeElement({ hidden: true });
  cleanupRoot.queries.set('[data-feedback-cleanup-pending]', pending);
  cleanupRoot.queries.set('[data-feedback-cleanup-error]', error);
  cleanupRoot.queries.set('[data-feedback-cleanup-success]', success);
  const result = new FakeElement({
    dataset: {
      selfieFeedbackEnabled: 'true',
      resultDigest,
    },
  });
  const document = {
    querySelector(selector) {
      if (selector === '[data-selfie-search-result]') return result;
      if (selector === '[data-feedback-cleanup]') return cleanupRoot;
      return null;
    },
  };
  startBrowserUi(document, { sessionStorage: new MemoryStorage() }, { storage });
  return { error, pending, success };
}

test('submitted result completes cleanup before revealing confirmation', async () => {
  let cleanupAttempts = 0;
  const fixture = makeSubmittedStartupFixture({
    storage: {
      async cleanupExpired() {},
      async clearAll() {},
      async clearAssociatedSelfie() { cleanupAttempts += 1; return true; },
    },
  });

  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(cleanupAttempts, 1);
  assert.equal(fixture.pending.hidden, true);
  assert.equal(fixture.success.hidden, false);
});

test('real associated selfie delete failure reports a cleanup failure', async () => {
  const db = new MemoryDatabase();
  await db.put({ handle: 'undeletable', resultTokenDigest: 'f'.repeat(64) });
  db.delete = async () => { throw new Error('transaction failed'); };
  const storage = makeAdapter({ db });

  const cleaned = await storage.clearAssociatedSelfie('f'.repeat(64));

  assert.equal(cleaned, false);
});

test('keeps contact and selected labels available after validation, server, and network failures', async () => {
  const fixture = makeFeedbackFixture();
  const failures = [
    { json: async () => ({ status: 'invalid' }) },
    new Error('offline'),
  ];
  const controller = new FeedbackUiController({
    root: fixture.root,
    resultDigest: fixture.resultDigest,
    document: fixture.document,
    storage: { async getAssociatedSelfie() { return { bytes: new Uint8Array([1]), mediaType: 'image/jpeg' }; } },
    window: {
      sessionStorage: new MemoryStorage(),
      async fetch() {
        const failure = failures.shift();
        if (failure instanceof Error) throw failure;
        return failure;
      },
    },
  });
  controller.marks.toggle('photo-a', 'present');

  await controller.submit({ preventDefault() {} });
  assert.equal(fixture.contactError.hidden, false);

  fixture.contact.value = 'contact';
  await controller.submit({ preventDefault() {} });
  assert.equal(fixture.consentError.hidden, false);

  fixture.consent.checked = true;
  await controller.submit({ preventDefault() {} });
  assert.equal(fixture.submitError.hidden, false);
  assert.equal(fixture.contact.value, 'contact');
  assert.deepEqual(controller.marks.getMarks(), { 'photo-a': 'present' });
  assert.equal(fixture.submitButton.disabled, false);

  await controller.submit({ preventDefault() {} });
  assert.equal(fixture.submitError.hidden, false);
  assert.equal(fixture.contact.value, 'contact');
  assert.deepEqual(controller.marks.getMarks(), { 'photo-a': 'present' });
});

test('feedback marking is initialized expanded without disclosure controls', () => {
  const fixture = makeFeedbackFixture();
  const controller = new FeedbackUiController({
    root: fixture.root,
    resultDigest: fixture.resultDigest,
    document: fixture.document,
    storage: {},
    window: { sessionStorage: new MemoryStorage() },
  });
  controller.bind();

  assert.equal(fixture.form.hidden, false);
  assert.equal(fixture.controls.every((control) => !control.hidden), true);
  assert.equal(controller.open, undefined);
  assert.equal(controller.close, undefined);
  assert.equal(controller.optOut, undefined);
});

test('feedback storage initialization uses no browser preference', async () => {
  const storage = {
    async cleanupExpired() {},
    async associatePendingSelfie() {
      return { associated: true };
    },
  };

  const state = await initializeBrowserStorage({
    storage,
    result: { dataset: { resultDigest: 'a'.repeat(64) } },
    window: {},
  });

  assert.deepEqual(state, { available: true });
});
