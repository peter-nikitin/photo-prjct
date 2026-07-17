'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');

const {
  UploadCoordinator,
  bindUploadPage,
  chunkItems,
  prepareSelection,
  summarize,
  visibleItems,
} = require('../../src/backend/static/ui/upload-coordinator.js');

test('browser binder uses the environment passed to the module factory', () => {
  const eventSelect = { value: '', focus() {} };
  const root = {
    dataset: {
      createBatchUrl: '/batches/',
      registerUrlTemplate: '/{batch}/items/',
      authorizeUrlTemplate: '/{batch}/items/{item}/authorize/',
      retryUrlTemplate: '/{batch}/items/{item}/retry/',
      confirmUrlTemplate: '/{batch}/items/{item}/confirm/',
      failedUrlTemplate: '/{batch}/items/{item}/failed/',
      finalizeUrlTemplate: '/{batch}/finalize/',
      csrfToken: 'csrf',
      maxFiles: '10',
      maxFileBytes: '10',
      registrationChunk: '10',
      concurrency: '4',
      queueWindowSize: '20',
    },
    querySelector(selector) {
      return selector === '#upload-event' ? eventSelect : null;
    },
  };

  assert.doesNotThrow(() =>
    bindUploadPage(root, {
      fetch: async () => response(200),
      XMLHttpRequest: class {},
      FormData: FakeFormData,
      crypto: { randomUUID: () => 'id' },
      setTimeout,
      addEventListener() {},
    }),
  );
});

class FakeFormData {
  constructor() {
    this.entries = [];
  }

  append(name, value) {
    this.entries.push([name, value]);
  }
}

function response(status, body = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function makeHarness({
  xhrResults = [],
  onAuthorizeControl = null,
  onRetryControl = null,
  retryTimer = null,
} = {}) {
  const calls = [];
  const delays = [];
  let itemSequence = 0;
  let active = 0;
  let maxActive = 0;
  const ids = Array.from({ length: 220 }, (_, index) =>
    `00000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`,
  );
  const fetch = async (url, options) => {
    const body = JSON.parse(options.body);
    calls.push({ url, body, headers: options.headers });
    if (url === '/batches/') {
      return response(201, { batch: { id: 'batch-1' } });
    }
    if (url === '/batch-1/items/') {
      return response(201, {
        items: body.items.map((item) => ({
          id: `item-${++itemSequence}`,
          client_item_id: item.client_item_id,
          status: 'pending',
        })),
      });
    }
    if (url.endsWith('/authorize/')) {
      if (onAuthorizeControl) {
        return onAuthorizeControl({ body, url });
      }
      return response(200, {
        item: { id: url.split('/')[3], status: 'authorized', attempt: 1 },
        grant: { url: 'https://storage.example/', fields: { policy: 'secret' } },
      });
    }
    if (url.endsWith('/retry/')) {
      if (onRetryControl) {
        return onRetryControl({ body, url });
      }
      return response(200, {
        item: { id: url.split('/')[3], status: 'authorized', attempt: 1 },
        grant: { url: 'https://storage.example/', fields: { policy: 'retry-secret' } },
      });
    }
    if (url.endsWith('/confirm/')) {
      return response(200, { item: { id: url.split('/')[3], status: 'uploaded' } });
    }
    if (url.endsWith('/failed/')) {
      return response(200, { item: { id: url.split('/')[3], status: 'failed' } });
    }
    if (url === '/batch-1/finalize/') {
      return response(200, { batch: { id: 'batch-1', status: 'complete' } });
    }
    throw new Error(`Unexpected control URL ${url}`);
  };

  class FakeXHR {
    constructor() {
      this.upload = {};
      this.status = 0;
    }

    open(method, url) {
      this.method = method;
      this.url = url;
    }

    send(form) {
      this.form = form;
      active += 1;
      maxActive = Math.max(maxActive, active);
      queueMicrotask(() => {
        const result = xhrResults.shift() || { type: 'load', status: 204 };
        active -= 1;
        this.status = result.status || 0;
        this.upload.onprogress?.({ lengthComputable: true, loaded: 4, total: 4 });
        this[`on${result.type}`]?.();
      });
    }

    abort() {
      this.onabort?.();
    }
  }

  const coordinator = new UploadCoordinator({
    config: {
      createBatchUrl: '/batches/',
      registerUrl: '/{batch}/items/',
      authorizeUrl: '/{batch}/items/{item}/authorize/',
      retryUrl: '/{batch}/items/{item}/retry/',
      confirmUrl: '/{batch}/items/{item}/confirm/',
      failedUrl: '/{batch}/items/{item}/failed/',
      finalizeUrl: '/{batch}/finalize/',
      csrfToken: 'csrf-value',
      maxFiles: 220,
      maxFileBytes: 10,
      registrationChunk: 100,
      concurrency: 4,
      queueWindow: 20,
    },
    fetch,
    XMLHttpRequest: FakeXHR,
    FormData: FakeFormData,
    crypto: { randomUUID: () => ids.shift() },
    setTimeout: retryTimer?.setTimeout || ((callback, delay) => {
      delays.push(delay);
      queueMicrotask(callback);
      return 1;
    }),
    clearTimeout: retryTimer?.clearTimeout || clearTimeout,
  });
  return {
    calls,
    coordinator,
    delays,
    getMaxActive: () => maxActive,
    resetMaxActive: () => {
      maxActive = active;
    },
  };
}

test('prepareSelection validates JPEG files and assigns one stable UUID per accepted file', () => {
  const ids = ['00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000002'];
  const crypto = { randomUUID: () => ids.shift() };
  const files = [
    { name: 'one.jpg', type: 'image/jpeg', size: 4 },
    { name: 'two.jpeg', type: 'image/jpeg', size: 8 },
  ];

  const selection = prepareSelection(files, { maxFiles: 2, maxFileBytes: 8, crypto });

  assert.deepEqual(
    selection.items.map(({ clientItemId, file }) => [clientItemId, file.name]),
    [
      ['00000000-0000-4000-8000-000000000001', 'one.jpg'],
      ['00000000-0000-4000-8000-000000000002', 'two.jpeg'],
    ],
  );
  assert.equal(selection.items[0].clientItemId, selection.items[0].clientItemId);
  assert.throws(
    () => prepareSelection([{ name: 'bad.png', type: 'image/png', size: 4 }], { maxFiles: 2, maxFileBytes: 8, crypto }),
    /JPEG/,
  );
  assert.throws(() => prepareSelection(files, { maxFiles: 1, maxFileBytes: 8, crypto }), /1/);
  assert.throws(
    () => prepareSelection([{ name: 'huge.jpg', type: 'image/jpeg', size: 9 }], { maxFiles: 2, maxFileBytes: 8, crypto }),
    /8/,
  );
});

test('network and retryable HTTP failures use exactly three delayed automatic retries', async () => {
  const { calls, coordinator, delays } = makeHarness({
    xhrResults: [
      { type: 'error' },
      { type: 'load', status: 408 },
      { type: 'load', status: 500 },
      { type: 'load', status: 204 },
    ],
  });

  await coordinator.start([{ name: 'retry.jpg', type: 'image/jpeg', size: 4 }], '42');

  assert.deepEqual(delays, [1000, 3000, 7000]);
  assert.deepEqual(
    calls.filter(({ url }) => url.endsWith('/authorize/')).map(({ body }) => body.reason),
    ['data_attempt', 'data_attempt', 'data_attempt', 'data_attempt'],
  );
  assert.equal(coordinator.items[0].status, 'uploaded');
});

test('one 403 refreshes the grant without consuming a data attempt', async () => {
  const { calls, coordinator, delays } = makeHarness({
    xhrResults: [
      { type: 'load', status: 403 },
      { type: 'load', status: 204 },
    ],
  });

  await coordinator.start([{ name: 'expired.jpg', type: 'image/jpeg', size: 4 }], '42');

  assert.deepEqual(
    calls.filter(({ url }) => url.endsWith('/authorize/')).map(({ body }) => body.reason),
    ['data_attempt', 'grant_refresh'],
  );
  assert.deepEqual(delays, []);
  assert.equal(coordinator.items[0].status, 'uploaded');
});

test('a repeated 403 or another 4xx is terminal and still allows batch finalization', async () => {
  for (const statuses of [[403, 403], [400]]) {
    const { calls, coordinator } = makeHarness({
      xhrResults: statuses.map((status) => ({ type: 'load', status })),
    });

    await coordinator.start([{ name: 'bad.jpg', type: 'image/jpeg', size: 4 }], '42');

    assert.equal(coordinator.items[0].status, 'failed');
    assert.equal(calls.filter(({ url }) => url.endsWith('/failed/')).length, 1);
    assert.equal(calls.filter(({ url }) => url.endsWith('/confirm/')).length, 0);
    assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
  }
});

test('cancellation is terminal for the cycle and manual retry reuses the item and file', async () => {
  const { calls, coordinator, delays } = makeHarness({ xhrResults: [{ type: 'abort' }] });

  await coordinator.start([{ name: 'cancel.jpg', type: 'image/jpeg', size: 4 }], '42');
  const original = coordinator.items[0];

  assert.equal(original.status, 'failed');
  assert.deepEqual(delays, []);
  assert.equal(calls.filter(({ url }) => url.endsWith('/authorize/')).length, 1);

  await coordinator.manualRetry(original.clientItemId);

  assert.equal(coordinator.items[0], original);
  assert.equal(original.status, 'uploaded');
  assert.equal(calls.filter(({ url }) => url.endsWith('/retry/')).length, 1);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 2);
});

test('simultaneous manual retries share the four-transfer concurrency limit', async () => {
  const { calls, coordinator, getMaxActive, resetMaxActive } = makeHarness({
    xhrResults: [
      ...Array.from({ length: 6 }, () => ({ type: 'load', status: 400 })),
      ...Array.from({ length: 6 }, () => ({ type: 'load', status: 204 })),
    ],
  });
  await coordinator.start(
    Array.from({ length: 6 }, (_, index) => ({
      name: `${index}.jpg`,
      type: 'image/jpeg',
      size: 4,
    })),
    '42',
  );
  resetMaxActive();

  await Promise.all(coordinator.items.map((item) => coordinator.manualRetry(item.clientItemId)));

  assert.equal(getMaxActive(), 4);
  assert.equal(calls.filter(({ url }) => url.endsWith('/retry/')).length, 6);
});

test('a queued manual retry is nonterminal immediately and duplicate clicks share one cycle', async () => {
  let releaseSecondRetry;
  const secondRetry = new Promise((resolve) => {
    releaseSecondRetry = () => resolve(response(200, {
      item: { id: 'item-2', status: 'authorized', attempt: 1 },
      grant: { url: 'https://storage.example/', fields: { policy: 'retry-secret' } },
    }));
  });
  const { calls, coordinator } = makeHarness({
    xhrResults: [
      { type: 'load', status: 400 },
      { type: 'load', status: 400 },
      { type: 'load', status: 204 },
      { type: 'load', status: 204 },
    ],
    onRetryControl: ({ url }) => {
      if (url.includes('/item-2/')) return secondRetry;
      return response(200, {
        item: { id: 'item-1', status: 'authorized', attempt: 1 },
        grant: { url: 'https://storage.example/', fields: { policy: 'retry-secret' } },
      });
    },
  });
  await coordinator.start(
    [
      { name: 'one.jpg', type: 'image/jpeg', size: 4 },
      { name: 'two.jpg', type: 'image/jpeg', size: 4 },
    ],
    '42',
  );

  const first = coordinator.manualRetry(coordinator.items[0].clientItemId);
  const second = coordinator.manualRetry(coordinator.items[1].clientItemId);
  const duplicate = coordinator.manualRetry(coordinator.items[1].clientItemId);
  await first;

  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
  releaseSecondRetry();
  await Promise.all([second, duplicate]);
  assert.equal(calls.filter(({ url }) => url.endsWith('/retry/')).length, 2);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 2);
});

test('cancel during automatic backoff clears the timer and reports one terminal cancellation', async () => {
  let notifyBackoff;
  const backoffStarted = new Promise((resolve) => {
    notifyBackoff = resolve;
  });
  const scheduled = new Map();
  let timerSequence = 0;
  const retryTimer = {
    setTimeout(callback, delay) {
      const id = ++timerSequence;
      scheduled.set(id, callback);
      notifyBackoff(delay);
      return id;
    },
    clearTimeout(id) {
      scheduled.delete(id);
    },
  };
  const { calls, coordinator } = makeHarness({
    xhrResults: [{ type: 'error' }, { type: 'load', status: 204 }],
    retryTimer,
  });
  const completion = coordinator.start(
    [{ name: 'cancel-backoff.jpg', type: 'image/jpeg', size: 4 }],
    '42',
  );
  const delay = await backoffStarted;

  const cancelled = coordinator.cancel(coordinator.items[0].clientItemId);
  const remainingAfterCancel = scheduled.size;
  for (const [id, callback] of scheduled) {
    scheduled.delete(id);
    callback();
  }
  await completion;

  assert.equal(delay, 1000);
  assert.equal(cancelled, true);
  assert.equal(remainingAfterCancel, 0);
  assert.equal(calls.filter(({ url }) => url.endsWith('/authorize/')).length, 1);
  assert.equal(calls.filter(({ url }) => url.endsWith('/failed/')).length, 1);
  assert.equal(coordinator.items[0].status, 'failed');
  assert.equal(coordinator.items[0].error, 'Передача отменена.');
});

test('cancel wins a grant-refresh network race and reports cancellation once', async () => {
  let notifyRefresh;
  const refreshStarted = new Promise((resolve) => {
    notifyRefresh = resolve;
  });
  let rejectRefresh;
  const refreshResponse = new Promise((_, reject) => {
    rejectRefresh = reject;
  });
  const { calls, coordinator } = makeHarness({
    xhrResults: [{ type: 'load', status: 403 }],
    onAuthorizeControl: ({ body }) => {
      if (body.reason === 'grant_refresh') {
        notifyRefresh();
        return refreshResponse;
      }
      return response(200, {
        item: { id: 'item-1', status: 'authorized', attempt: 1 },
        grant: { url: 'https://storage.example/', fields: { policy: 'secret' } },
      });
    },
  });
  const completion = coordinator.start(
    [{ name: 'cancel-refresh.jpg', type: 'image/jpeg', size: 4 }],
    '42',
  );
  await refreshStarted;

  assert.equal(coordinator.cancel(coordinator.items[0].clientItemId), true);
  rejectRefresh(new Error('refresh network failed'));
  await completion;

  assert.equal(calls.filter(({ url }) => url.endsWith('/failed/')).length, 1);
  assert.equal(calls.filter(({ url }) => url.endsWith('/confirm/')).length, 0);
  assert.equal(coordinator.items[0].status, 'failed');
  assert.equal(coordinator.items[0].error, 'Передача отменена.');
});

test('cancel during manual retry authorization closes the reopened batch once', async () => {
  let notifyRetry;
  const retryStarted = new Promise((resolve) => {
    notifyRetry = resolve;
  });
  let releaseRetry;
  const retryResponse = new Promise((resolve) => {
    releaseRetry = () => resolve(response(200, {
      item: { id: 'item-1', status: 'authorized', attempt: 1 },
      grant: { url: 'https://storage.example/', fields: { policy: 'retry-secret' } },
    }));
  });
  const { calls, coordinator } = makeHarness({
    xhrResults: [{ type: 'load', status: 400 }],
    onRetryControl: () => {
      notifyRetry();
      return retryResponse;
    },
  });
  await coordinator.start(
    [{ name: 'cancel-retry-authorization.jpg', type: 'image/jpeg', size: 4 }],
    '42',
  );
  const retry = coordinator.manualRetry(coordinator.items[0].clientItemId);
  await retryStarted;

  assert.equal(coordinator.cancel(coordinator.items[0].clientItemId), true);
  releaseRetry();
  await retry;

  assert.equal(calls.filter(({ url }) => url.endsWith('/failed/')).length, 2);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 2);
  assert.equal(coordinator.items[0].status, 'failed');
  assert.equal(coordinator.items[0].error, 'Передача отменена.');
});

test('manual retry control failures restore a stable retryable terminal state', async () => {
  for (const failure of ['503', 'network']) {
    const { calls, coordinator } = makeHarness({
      xhrResults: [{ type: 'load', status: 400 }],
      onRetryControl: () => {
        if (failure === 'network') throw new Error('private network detail');
        return response(503, {
          error: { code: 'storage_unavailable', message: 'Object storage unavailable.' },
        });
      },
    });
    await coordinator.start(
      [{ name: `${failure}.jpg`, type: 'image/jpeg', size: 4 }],
      '42',
    );

    const retried = await coordinator.manualRetry(coordinator.items[0].clientItemId);

    assert.equal(retried, false);
    assert.equal(coordinator.items[0].status, 'failed');
    assert.equal(coordinator.items[0].error, 'Не удалось повторить загрузку. Повторите попытку.');
    assert.equal(coordinator.active, false);
    assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
  }
});

test('close warning is active only while registered work is unfinished', async () => {
  const { coordinator } = makeHarness();
  assert.equal(coordinator.shouldWarnBeforeUnload(), false);

  const completion = coordinator.start([{ name: 'one.jpg', type: 'image/jpeg', size: 4 }], '42');
  assert.equal(coordinator.shouldWarnBeforeUnload(), true);
  await completion;

  assert.equal(coordinator.shouldWarnBeforeUnload(), false);
});

test('chunkItems never registers more than one hundred files in a control call', () => {
  const chunks = chunkItems(Array.from({ length: 205 }, (_, index) => index), 100);
  assert.deepEqual(chunks.map((chunk) => chunk.length), [100, 100, 5]);
});

test('summary and queue rendering stay bounded instead of creating one row per file', () => {
  const items = Array.from({ length: 30 }, (_, index) => ({
    file: { size: 10 },
    status: index < 3 ? 'uploaded' : index < 5 ? 'failed' : 'uploading',
    progress: index < 3 ? 100 : 50,
  }));

  assert.equal(visibleItems(items, 20).length, 20);
  assert.equal(visibleItems(items, 20)[0], items[10]);
  assert.deepEqual(summarize(items), {
    total: 30,
    uploaded: 3,
    failed: 2,
    totalBytes: 300,
    progress: 55,
  });
});

test('coordinator source keeps files and grants in page memory only', () => {
  const source = fs.readFileSync(
    require.resolve('../../src/backend/static/ui/upload-coordinator.js'),
    'utf8',
  );
  assert.equal(source.includes('localStorage'), false);
  assert.equal(source.includes('indexedDB'), false);
  assert.equal(source.includes('incoming/'), false);
  assert.equal(source.includes('originals/'), false);
});

test('coordinator registers every file, uploads at most four at once, confirms, and finalizes', async () => {
  const { calls, coordinator, getMaxActive } = makeHarness();
  const files = Array.from({ length: 205 }, (_, index) => ({
    name: `${index}.jpg`,
    type: 'image/jpeg',
    size: 4,
  }));

  await coordinator.start(files, '42');

  const registrations = calls.filter(({ url }) => url === '/batch-1/items/');
  assert.deepEqual(registrations.map(({ body }) => body.items.length), [100, 100, 5]);
  assert.equal(calls.filter(({ url }) => url.endsWith('/authorize/')).length, 205);
  assert.equal(calls.filter(({ url }) => url.endsWith('/confirm/')).length, 205);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
  assert.equal(getMaxActive(), 4);
  assert.ok(calls.every(({ headers }) => headers['X-CSRFToken'] === 'csrf-value'));
  assert.ok(coordinator.items.every(({ status }) => status === 'uploaded'));
});
