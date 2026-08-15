'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');

const {
  ControlError,
  UploadCoordinator,
  bindUploadPage,
  bindFolderTargets,
  chunkItems,
  groupItems,
  matchingKey,
  matchResumeSelection,
  prepareAmbiguousFingerprints,
  prepareSelection,
  renderPage,
  summarize,
  uploadErrorMessage,
  visibleGroupItems,
} = require('../../src/backend/static/ui/upload-coordinator.js');

function file(name, size, lastModified, contents = name) {
  const bytes = new TextEncoder().encode(contents);
  return {
    name,
    type: 'image/jpeg',
    size,
    lastModified,
    arrayBuffer: async () => bytes.buffer,
  };
}

function digestRecorder() {
  const calls = [];
  return {
    calls,
    subtle: {
      async digest(algorithm, bytes) {
        calls.push({ algorithm, bytes });
        const content = Buffer.from(bytes).toString();
        const fill = content === 'first' ? 0xab : content === 'second' ? 0xcd : 0xef;
        return Uint8Array.from({ length: 32 }, () => fill).buffer;
      },
    },
  };
}

function manifestItem({
  id,
  filename,
  size = 4,
  lastModified = 1000,
  sha256 = null,
  status = 'pending',
  confirmed = false,
}) {
  return {
    id,
    filename,
    size,
    last_modified_ms: lastModified,
    ambiguous_sha256: sha256,
    status,
    confirmed,
  };
}

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

function summaryRoot() {
  const nodes = new Map([
    ['#upload-summary-title', {}],
    ['[data-summary-message]', {}],
    ['[data-summary-percent]', {}],
    ['[data-upload-progress]', {}],
    ['[data-total-count]', {}],
    ['[data-uploaded-count]', {}],
    ['[data-failed-count]', {}],
    ['[data-total-bytes]', {}],
  ]);
  return {
    dataset: {},
    querySelector(selector) {
      return nodes.get(selector) || null;
    },
    node(selector) {
      return nodes.get(selector);
    },
  };
}

function makeHarness({
  xhrResults = [],
  onAuthorizeControl = null,
  onConfirmControl = null,
  onRetryControl = null,
  onFailedControl = null,
  onFinalizeControl = null,
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
        return onAuthorizeControl({ body, options, url });
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
      if (onConfirmControl) {
        return onConfirmControl({ body, options, url });
      }
      return response(200, { item: { id: url.split('/')[3], status: 'uploaded' } });
    }
    if (url.endsWith('/failed/')) {
      if (onFailedControl) {
        return onFailedControl({ body, url });
      }
      return response(200, { item: { id: url.split('/')[3], status: 'failed' } });
    }
    if (url === '/batch-1/finalize/') {
      if (onFinalizeControl) {
        return onFinalizeControl({ body, url });
      }
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
    },
    fetch,
    XMLHttpRequest: FakeXHR,
    FormData: FakeFormData,
    crypto: { randomUUID: () => ids.shift() },
    AbortController,
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

async function startUpload(coordinator, files, eventId) {
  coordinator.stage(files);
  return coordinator.start(eventId);
}

test('prepareSelection validates JPEG files and assigns one stable UUID per accepted file', () => {
  const ids = ['00000000-0000-4000-8000-000000000001', '00000000-0000-4000-8000-000000000002'];
  const crypto = { randomUUID: () => ids.shift() };
  const files = [
    { name: 'one.jpg', type: 'image/jpeg', size: 4 },
    { name: 'two.jpeg', type: 'image/jpeg', size: 8 },
  ];

  const selection = prepareSelection(files, {
    maxFiles: 2,
    maxFileBytes: 8,
    crypto,
    folder: { id: 4, name: 'Старт' },
  });

  assert.deepEqual(
    selection.items.map(({ clientItemId, file }) => [clientItemId, file.name]),
    [
      ['00000000-0000-4000-8000-000000000001', 'one.jpg'],
      ['00000000-0000-4000-8000-000000000002', 'two.jpeg'],
    ],
  );
  assert.equal(selection.items[0].clientItemId, selection.items[0].clientItemId);
  assert.equal(selection.items[0].folder.id, 4);
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

test('mixed queue keeps three immutable folder assignments in one batch', async () => {
  const { calls, coordinator } = makeHarness();

  coordinator.stage([file('start.jpg', 4, 1000)], { id: 4, name: 'Старт' });
  coordinator.stage([file('finish.jpg', 4, 1001)], { id: 8, name: 'Финиш' });
  coordinator.stage([file('plain.jpg', 4, 1002)], null);
  await coordinator.start('42');

  const registration = calls.find(({ url }) => url === '/batch-1/items/');
  assert.equal(calls.filter(({ url }) => url === '/batches/').length, 1);
  assert.deepEqual(
    registration.body.items.map(({ filename, folder_id: folderId }) => [filename, folderId]),
    [['start.jpg', 4], ['finish.jpg', 8], ['plain.jpg', null]],
  );
  assert.deepEqual(
    coordinator.items.map(({ folder }) => folder),
    [{ id: 4, name: 'Старт' }, { id: 8, name: 'Финиш' }, null],
  );
  assert.throws(
    () => coordinator.stage([file('too-many.jpg', 4, 1003)], { id: 4, name: 'Старт' }),
    /начата/,
  );
});

test('concurrent starts share one batch creation while the first start is pending', async () => {
  const { calls, coordinator } = makeHarness();
  coordinator.stage([file('start.jpg', 4, 1000)]);

  await Promise.all([coordinator.start('42'), coordinator.start('42')]);

  assert.equal(calls.filter(({ url }) => url === '/batches/').length, 1);
  assert.equal(calls.filter(({ url }) => url === '/batch-1/items/').length, 1);
});

test('folder registration conflict tells the photographer to add files again from a current target', () => {
  const error = new ControlError(409, {
    error: { code: 'folder_not_found', message: 'The selected event folder is no longer available.' },
  });

  assert.equal(
    uploadErrorMessage(error),
    'Выбранная папка больше недоступна. Обновите страницу и добавьте эти файлы заново через актуальную папку события.',
  );
  assert.equal(
    uploadErrorMessage(new ControlError(503, { error: { code: 'unavailable' } })),
    'Не удалось продолжить загрузку. Повторите попытку.',
  );
});

test('staging selections enforces the total max-file limit across folders', () => {
  const { coordinator } = makeHarness();
  coordinator.config.maxFiles = 2;

  coordinator.stage([file('start.jpg', 4, 1000)], { id: 4, name: 'Старт' });
  assert.throws(
    () => coordinator.stage(
      [file('finish.jpg', 4, 1001), file('plain.jpg', 4, 1002)],
      { id: 8, name: 'Финиш' },
    ),
    /1/,
  );
  assert.equal(coordinator.items.length, 1);
});

test('folder drop target keeps one active zone across nested leaves and clears on completion', () => {
  const listeners = new Map();
  const makeTarget = (id, name) => {
    const copy = { textContent: 'Перетащите JPEG сюда' };
    const input = { addEventListener() {} };
    return {
      dataset: { folderId: id, folderName: name, defaultCopy: 'Перетащите JPEG сюда' },
      addEventListener(type, listener) { listeners.set(`${id}:${type}`, listener); },
      contains(node) { return node === child; },
      querySelector(selector) {
        if (selector === '[data-folder-target-copy]') return copy;
        if (selector === '[data-folder-target-input]') return input;
        return null;
      },
      copy,
    };
  };
  const child = {};
  const plain = makeTarget('', 'Без папки');
  const finish = makeTarget('8', 'Финиш');
  const root = {
    dataset: {},
    querySelectorAll() { return [plain, finish]; },
    addEventListener(type, listener) { listeners.set(`root:${type}`, listener); },
  };
  const selections = [];
  bindFolderTargets(root, (files, folder) => selections.push({ files, folder }));
  const drag = (type, target, relatedTarget = null) => listeners.get(`${target.dataset.folderId}:${type}`)({
    preventDefault() {}, dataTransfer: { files: ['finish.jpg'] }, relatedTarget,
  });

  drag('dragenter', finish);
  drag('dragleave', finish, child);
  assert.equal(finish.dataset.dragActive, 'true');
  assert.equal(plain.dataset.dragActive, undefined);
  assert.equal(root.dataset.dragActive, 'true');
  assert.equal(finish.copy.textContent, 'Загрузить в «Финиш»');
  drag('drop', finish);
  assert.equal(finish.dataset.dragActive, undefined);
  assert.equal(root.dataset.dragActive, undefined);
  assert.deepEqual(selections, [{ files: ['finish.jpg'], folder: { id: 8, name: 'Финиш' } }]);

  drag('dragenter', plain);
  assert.equal(plain.copy.textContent, 'Загрузить без папки');
  listeners.get('root:dragend')();
  assert.equal(plain.dataset.dragActive, undefined);
  assert.equal(root.dataset.dragActive, undefined);
});

test('prepareSelection accepts a JPEG extension when drag-and-drop omits the MIME type', () => {
  const file = { name: 'dropped.JPEG', type: '', size: 4 };
  const options = {
    maxFiles: 2,
    maxFileBytes: 8,
    crypto: { randomUUID: () => '00000000-0000-4000-8000-000000000001' },
  };

  const selection = prepareSelection([file], options);

  assert.equal(selection.items[0].file, file);
  assert.equal(selection.items[0].contentType, 'image/jpeg');
  assert.throws(
    () => prepareSelection([{ name: 'bad.png', type: '', size: 4 }], options),
    /JPEG/,
  );
});

test('registration records last-modified metadata without hashing a unique selection', async () => {
  const { calls, coordinator } = makeHarness();
  const unique = file('unique.jpg', 4, 1_722_500_123_456, 'unique');

  await startUpload(coordinator, [unique], '42');

  const registration = calls.find(({ url }) => url === '/batch-1/items/');
  assert.deepEqual(registration.body.items[0], {
    client_item_id: registration.body.items[0].client_item_id,
    filename: 'unique.jpg',
    content_type: 'image/jpeg',
    size: 4,
    folder_id: null,
    last_modified_ms: 1_722_500_123_456,
    ambiguous_sha256: null,
  });
});

test('only duplicate metadata groups receive lowercase SHA-256 fingerprints', async () => {
  const digest = digestRecorder();
  const first = { clientItemId: 'first', file: file('same.jpg', 4, 1000, 'first') };
  const second = { clientItemId: 'second', file: file('same.jpg', 4, 1000, 'second') };
  const unique = { clientItemId: 'unique', file: file('unique.jpg', 4, 1001, 'unique') };

  await prepareAmbiguousFingerprints([first, second, unique], digest.subtle);

  assert.equal(matchingKey(first.file), matchingKey(second.file));
  assert.equal(digest.calls.length, 2);
  assert.equal(first.ambiguousSha256, 'ab'.repeat(32));
  assert.equal(second.ambiguousSha256, 'cd'.repeat(32));
  assert.equal(unique.ambiguousSha256, null);
});

test('registration sends lowercase ambiguous SHA-256 values for duplicate selections', async () => {
  const digest = digestRecorder();
  const { calls, coordinator } = makeHarness();
  coordinator.crypto.subtle = digest.subtle;

  await startUpload(coordinator,
    [file('same.jpg', 4, 1000, 'first'), file('same.jpg', 4, 1000, 'second')],
    '42',
  );

  const registration = calls.find(({ url }) => url === '/batch-1/items/');
  assert.deepEqual(
    registration.body.items.map(({ filename, ambiguous_sha256: digestValue }) => [filename, digestValue]),
    [
      ['same.jpg', 'ab'.repeat(32)],
      ['same.jpg', 'cd'.repeat(32)],
    ],
  );
});

test('resume matching binds unique modern and legacy manifest entries without hashing', async () => {
  const digest = digestRecorder();
  const modern = file('modern.jpg', 4, 1000, 'modern');
  const legacy = file('legacy.jpg', 4, 9000, 'legacy');
  const manifest = {
    batch: { id: 'batch-1', event: { id: 42, name: 'Race' } },
    items: [
      manifestItem({ id: 'modern', filename: 'modern.jpg', lastModified: 1000 }),
      manifestItem({ id: 'legacy', filename: 'legacy.jpg', lastModified: null }),
    ],
  };

  const matched = await matchResumeSelection([modern, legacy], manifest, {
    crypto: { randomUUID: () => 'client-id' },
    subtle: digest.subtle,
  });

  assert.deepEqual(
    matched.matches.map(({ manifestItem: item, file: selected }) => [item.id, selected.name]),
    [['modern', 'modern.jpg'], ['legacy', 'legacy.jpg']],
  );
  assert.deepEqual(matched.unmatched, []);
  assert.equal(digest.calls.length, 0);
});

test('resume matching consumes confirmed duplicate hashes before unfinished duplicates as a multiset', async () => {
  const digest = digestRecorder();
  const first = file('same.jpg', 4, 1000, 'first');
  const second = file('same.jpg', 4, 1000, 'first');
  const manifest = {
    batch: { id: 'batch-1', event: { id: 42, name: 'Race' } },
    items: [
      manifestItem({ id: 'confirmed', filename: 'same.jpg', sha256: 'ab'.repeat(32), confirmed: true, status: 'uploaded' }),
      manifestItem({ id: 'pending', filename: 'same.jpg', sha256: 'ab'.repeat(32) }),
    ],
  };

  const matched = await matchResumeSelection([first, second], manifest, { subtle: digest.subtle });

  assert.deepEqual(matched.matches.map(({ manifestItem: item }) => item.id), ['confirmed', 'pending']);
  assert.deepEqual(matched.unmatched, []);
  assert.equal(digest.calls.length, 2);
});

test('resume hashes a single reselected duplicate to retry its identified unfinished item', async () => {
  const digest = digestRecorder();
  const { calls, coordinator } = makeHarness();
  coordinator.crypto.subtle = digest.subtle;
  const manifest = {
    batch: { id: 'batch-1', event: { id: 42, name: 'Race' } },
    items: [
      manifestItem({
        id: 'confirmed',
        filename: 'same.jpg',
        sha256: 'ab'.repeat(32),
        confirmed: true,
        status: 'uploaded',
      }),
      manifestItem({ id: 'failed', filename: 'same.jpg', sha256: 'cd'.repeat(32), status: 'failed' }),
    ],
  };

  await coordinator.resume([file('same.jpg', 4, 1000, 'second')], manifest);

  assert.equal(digest.calls.length, 1);
  assert.equal(coordinator.items.find((item) => item.id === 'failed').status, 'uploaded');
  assert.equal(calls.filter(({ url }) => url.includes('/items/confirmed/')).length, 0);
  assert.equal(calls.filter(({ url }) => url.endsWith('/items/failed/retry/')).length, 1);
  assert.equal(calls.filter(({ url }) => url.endsWith('/items/failed/confirm/')).length, 1);
});

test('resume matching leaves unresolved duplicate metadata and extra files needing attention', async () => {
  const first = file('same.jpg', 4, 1000, 'first');
  const second = file('same.jpg', 4, 1000, 'second');
  const extra = file('extra.jpg', 4, 1000, 'extra');
  const manifest = {
    batch: { id: 'batch-1', event: { id: 42, name: 'Race' } },
    items: [
      manifestItem({ id: 'one', filename: 'same.jpg' }),
      manifestItem({ id: 'two', filename: 'same.jpg' }),
    ],
  };

  const matched = await matchResumeSelection([first, second, extra], manifest, {
    subtle: { digest: async () => { throw new Error('legacy duplicates must not hash'); } },
  });

  assert.deepEqual(matched.matches, []);
  assert.deepEqual(
    matched.unmatched.map(({ file: selected, reason }) => [selected.name, reason]),
    [['same.jpg', 'ambiguous'], ['same.jpg', 'ambiguous'], ['extra.jpg', 'extra']],
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

  await startUpload(coordinator, [{ name: 'retry.jpg', type: 'image/jpeg', size: 4 }], '42');

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

  await startUpload(coordinator, [{ name: 'expired.jpg', type: 'image/jpeg', size: 4 }], '42');

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

    await startUpload(coordinator, [{ name: 'bad.jpg', type: 'image/jpeg', size: 4 }], '42');

    assert.equal(coordinator.items[0].status, 'failed');
    assert.equal(calls.filter(({ url }) => url.endsWith('/failed/')).length, 1);
    assert.equal(calls.filter(({ url }) => url.endsWith('/confirm/')).length, 0);
    assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
  }
});

test('cancellation is terminal for the cycle and manual retry reuses the item and file', async () => {
  const { calls, coordinator, delays } = makeHarness({ xhrResults: [{ type: 'abort' }] });

  await startUpload(coordinator, [{ name: 'cancel.jpg', type: 'image/jpeg', size: 4 }], '42');
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
  await startUpload(coordinator,
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

test('manual retry grants are requested just in time inside the bounded work queue', async () => {
  let activeRetryGrants = 0;
  let maxActiveRetryGrants = 0;
  const { coordinator } = makeHarness({
    xhrResults: Array.from({ length: 20 }, () => ({ type: 'load', status: 400 })),
    onRetryControl: ({ url }) => {
      activeRetryGrants += 1;
      maxActiveRetryGrants = Math.max(maxActiveRetryGrants, activeRetryGrants);
      return new Promise((resolve) => {
        setImmediate(() => {
          activeRetryGrants -= 1;
          resolve(response(200, {
            item: { id: url.split('/')[3], status: 'authorized', attempt: 1 },
            grant: { url: 'https://storage.example/', fields: { policy: 'retry-secret' } },
          }));
        });
      });
    },
  });
  await startUpload(coordinator,
    Array.from({ length: 20 }, (_, index) => ({
      name: `${index}.jpg`,
      type: 'image/jpeg',
      size: 4,
    })),
    '42',
  );

  await Promise.all(coordinator.items.map((item) => coordinator.manualRetry(item.clientItemId)));

  assert.equal(maxActiveRetryGrants, 4);
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
  await startUpload(coordinator,
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
  const completion = startUpload(coordinator,
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
  const completion = startUpload(coordinator,
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

test('cancel aborts a pending authorization without waiting for its response', async () => {
  let notifyAuthorization;
  const authorizationStarted = new Promise((resolve) => {
    notifyAuthorization = resolve;
  });
  let rejectAuthorization;
  const authorizationResponse = new Promise((_, reject) => {
    rejectAuthorization = reject;
  });
  const { calls, coordinator } = makeHarness({
    onAuthorizeControl: ({ options }) => {
      options.signal?.addEventListener('abort', () => {
        rejectAuthorization(new DOMException('Aborted', 'AbortError'));
      });
      notifyAuthorization();
      return authorizationResponse;
    },
  });
  const completion = startUpload(coordinator,
    [{ name: 'cancel-authorization.jpg', type: 'image/jpeg', size: 4 }],
    '42',
  );
  await authorizationStarted;

  assert.equal(coordinator.cancel(coordinator.items[0].clientItemId), true);
  const converged = await Promise.race([
    completion.then(() => true, () => false),
    new Promise((resolve) => setTimeout(() => resolve(false), 25)),
  ]);
  if (!converged) {
    rejectAuthorization(new Error('test cleanup'));
    await completion.catch(() => {});
  }

  assert.equal(converged, true);
  assert.equal(calls.filter(({ url }) => url.endsWith('/failed/')).length, 1);
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
  await startUpload(coordinator,
    [{ name: 'cancel-retry-authorization.jpg', type: 'image/jpeg', size: 4 }],
    '42',
  );
  const retry = coordinator.manualRetry(coordinator.items[0].clientItemId);
  await retryStarted;

  assert.equal(coordinator.items[0].status, 'uploading');
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
    await startUpload(coordinator,
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

test('a confirm network failure is contained across the whole manual retry cycle', async () => {
  const { calls, coordinator } = makeHarness({
    xhrResults: [
      { type: 'load', status: 400 },
      { type: 'load', status: 204 },
    ],
    onConfirmControl: () => {
      throw new Error('private confirm network detail');
    },
  });
  await startUpload(coordinator,
    [{ name: 'confirm-network.jpg', type: 'image/jpeg', size: 4 }],
    '42',
  );

  const retried = await coordinator.manualRetry(coordinator.items[0].clientItemId);

  assert.equal(retried, false);
  assert.equal(coordinator.items[0].status, 'failed');
  assert.equal(coordinator.items[0].error, 'Не удалось повторить загрузку. Повторите попытку.');
  assert.equal(coordinator.active, false);
  assert.equal(calls.filter(({ url }) => url.endsWith('/failed/')).length, 1);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 2);
});

test('a failed pending retry finalizes once after a sibling retry succeeds', async () => {
  let releaseSecondRetry;
  const secondRetry = new Promise((resolve) => {
    releaseSecondRetry = () => resolve(response(503, {
      error: { code: 'storage_unavailable', message: 'Private detail.' },
    }));
  });
  const { calls, coordinator } = makeHarness({
    xhrResults: [
      { type: 'load', status: 400 },
      { type: 'load', status: 400 },
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
  await startUpload(coordinator,
    [
      { name: 'one.jpg', type: 'image/jpeg', size: 4 },
      { name: 'two.jpg', type: 'image/jpeg', size: 4 },
    ],
    '42',
  );

  const first = coordinator.manualRetry(coordinator.items[0].clientItemId);
  const second = coordinator.manualRetry(coordinator.items[1].clientItemId);
  await first;
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
  releaseSecondRetry();

  assert.deepEqual(await Promise.all([first, second]), [true, false]);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 2);
  assert.equal(calls.filter(({ url }) => url.endsWith('/failed/')).length, 2);
  assert.equal(coordinator.active, false);
});

test('resume contains a retry endpoint 503 and settles the other queued item', async () => {
  const { calls, coordinator } = makeHarness({
    onRetryControl: () => response(503, {
      error: { code: 'storage_unavailable', message: 'Private detail.' },
    }),
  });
  const manifest = {
    batch: { id: 'batch-1', event: { id: 42, name: 'Race' } },
    items: [
      manifestItem({ id: 'failed', filename: 'failed.jpg', status: 'failed' }),
      manifestItem({ id: 'pending', filename: 'pending.jpg', status: 'pending' }),
    ],
  };

  await coordinator.resume(
    [file('failed.jpg', 4, 1000), file('pending.jpg', 4, 1000)],
    manifest,
  );

  const failed = coordinator.items.find((item) => item.id === 'failed');
  const pending = coordinator.items.find((item) => item.id === 'pending');
  assert.equal(failed.status, 'failed');
  assert.equal(failed.error, 'Не удалось повторить загрузку. Повторите попытку.');
  assert.equal(pending.status, 'uploaded');
  assert.equal(coordinator.active, false);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
});

test('resume contains a retry confirmation failure and settles the other queued item', async () => {
  const { calls, coordinator } = makeHarness({
    onConfirmControl: ({ url }) => url.includes('/failed/')
      ? response(503, { error: { code: 'storage_unavailable', message: 'Private detail.' } })
      : response(200, { item: { id: url.split('/')[3], status: 'uploaded' } }),
  });
  const manifest = {
    batch: { id: 'batch-1', event: { id: 42, name: 'Race' } },
    items: [
      manifestItem({ id: 'failed', filename: 'failed.jpg', status: 'failed' }),
      manifestItem({ id: 'pending', filename: 'pending.jpg', status: 'pending' }),
    ],
  };

  await coordinator.resume(
    [file('failed.jpg', 4, 1000), file('pending.jpg', 4, 1000)],
    manifest,
  );

  const failed = coordinator.items.find((item) => item.id === 'failed');
  const pending = coordinator.items.find((item) => item.id === 'pending');
  assert.equal(failed.status, 'failed');
  assert.equal(failed.error, 'Не удалось повторить загрузку. Повторите попытку.');
  assert.equal(pending.status, 'uploaded');
  assert.equal(coordinator.active, false);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
});

test('resume confirmation failure restores the durable retry state before finalizing', async () => {
  let durableStatus = 'failed';
  let confirmationAttempts = 0;
  const finalizeStatuses = [];
  const { calls, coordinator } = makeHarness({
    onRetryControl: ({ url }) => {
      assert.equal(durableStatus, 'failed');
      durableStatus = 'authorized';
      return response(200, {
        item: { id: url.split('/')[3], status: 'authorized', attempt: 1 },
        grant: { url: 'https://storage.example/', fields: { policy: 'retry-secret' } },
      });
    },
    onConfirmControl: ({ url }) => {
      confirmationAttempts += 1;
      if (confirmationAttempts === 1) {
        assert.equal(durableStatus, 'authorized');
        return response(503, {
          error: { code: 'storage_unavailable', message: 'Private detail.' },
        });
      }
      durableStatus = 'uploaded';
      return response(200, { item: { id: url.split('/')[3], status: 'uploaded' } });
    },
    onFailedControl: ({ url }) => {
      assert.equal(durableStatus, 'authorized');
      durableStatus = 'failed';
      return response(200, { item: { id: url.split('/')[3], status: 'failed' } });
    },
    onFinalizeControl: () => {
      finalizeStatuses.push(durableStatus);
      if (!['failed', 'uploaded'].includes(durableStatus)) {
        return response(409, {
          error: { code: 'items_not_terminal', message: 'Items are still active.' },
        });
      }
      return response(200, { batch: { id: 'batch-1', status: 'complete' } });
    },
  });
  const manifest = {
    batch: { id: 'batch-1', event: { id: 42, name: 'Race' } },
    items: [manifestItem({ id: 'failed', filename: 'failed.jpg', status: 'failed' })],
  };

  await coordinator.resume([file('failed.jpg', 4, 1000)], manifest);

  assert.equal(durableStatus, 'failed');
  assert.equal(coordinator.items[0].status, 'failed');
  assert.equal(calls.filter(({ url }) => url.endsWith('/failed/')).length, 1);
  assert.deepEqual(finalizeStatuses, ['failed']);

  await coordinator.manualRetry(coordinator.items[0].clientItemId);

  assert.equal(durableStatus, 'uploaded');
  assert.equal(coordinator.items[0].status, 'uploaded');
  assert.deepEqual(finalizeStatuses, ['failed', 'uploaded']);
});

test('close warning is active only while registered work is unfinished', async () => {
  const { coordinator } = makeHarness();
  assert.equal(coordinator.shouldWarnBeforeUnload(), false);

  const completion = startUpload(coordinator, [{ name: 'one.jpg', type: 'image/jpeg', size: 4 }], '42');
  assert.equal(coordinator.shouldWarnBeforeUnload(), true);
  await completion;

  assert.equal(coordinator.shouldWarnBeforeUnload(), false);
});

test('chunkItems never registers more than one hundred files in a control call', () => {
  const chunks = chunkItems(Array.from({ length: 205 }, (_, index) => index), 100);
  assert.deepEqual(chunks.map((chunk) => chunk.length), [100, 100, 5]);
});

test('grouped queue prioritizes actionable work and bounds every expanded page', () => {
  const items = [
    { clientItemId: 'failed', file: { name: 'failed.jpg', size: 10 }, status: 'failed', progress: 50 },
    { clientItemId: 'attention', file: { name: 'attention.jpg', size: 10 }, status: 'needs_attention', progress: 0 },
    { clientItemId: 'active', file: { name: 'active.jpg', size: 10 }, status: 'uploading', progress: 50 },
    { clientItemId: 'registered', file: { name: 'registered.jpg', size: 10 }, status: 'registered', progress: 0 },
    { clientItemId: 'retry', file: { name: 'retry.jpg', size: 10 }, status: 'retry_pending', progress: 0 },
    { clientItemId: 'waiting', file: { name: 'waiting.jpg', size: 10 }, status: 'waiting', progress: 0 },
    ...Array.from({ length: 9_994 }, (_, index) => ({
      clientItemId: `uploaded-${index}`,
      file: { name: `uploaded-${index}.jpg`, size: 10 },
      status: 'uploaded',
      progress: 100,
    })),
  ];

  const groups = groupItems(items);

  assert.deepEqual(groups.map(({ key }) => key), [
    'needs_attention',
    'uploading',
    'waiting',
    'uploaded',
  ]);
  assert.deepEqual(groups.map(({ count }) => count), [2, 1, 3, 9_994]);
  assert.deepEqual(
    groups.slice(0, 2).map(({ expanded }) => expanded),
    [true, true],
  );
  assert.deepEqual(
    groups.slice(2).map(({ expanded }) => expanded),
    [false, false],
  );
  assert.deepEqual(
    visibleGroupItems(groups[3], 0, 20).map(({ clientItemId }) => clientItemId),
    Array.from({ length: 20 }, (_, index) => `uploaded-${index}`),
  );
  assert.deepEqual(
    visibleGroupItems(groups[3], 9_980, 20).map(({ clientItemId }) => clientItemId),
    Array.from({ length: 14 }, (_, index) => `uploaded-${index + 9_980}`),
  );
  assert.deepEqual(summarize(items), {
    total: 10_000,
    uploaded: 9_994,
    failed: 1,
    totalBytes: 100_000,
    progress: 100,
  });
});

test('resume summary presents waiting and attention items as incomplete work', () => {
  const root = summaryRoot();
  const coordinator = {
    active: false,
    items: [
      { file: { name: 'missing.jpg', size: 4 }, status: 'waiting', progress: 0 },
      { file: { name: 'extra.jpg', size: 4 }, status: 'needs_attention', progress: 0 },
    ],
  };

  renderPage(root, coordinator);

  assert.equal(root.dataset.state, 'partial');
  assert.equal(root.node('#upload-summary-title').textContent, 'Требуется действие');
  assert.equal(
    root.node('[data-summary-message]').textContent,
    'Загрузка не завершена: выберите недостающие файлы и проверьте файлы, требующие внимания.',
  );
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

  await startUpload(coordinator, files, '42');

  const registrations = calls.filter(({ url }) => url === '/batch-1/items/');
  assert.deepEqual(registrations.map(({ body }) => body.items.length), [100, 100, 5]);
  assert.equal(calls.filter(({ url }) => url.endsWith('/authorize/')).length, 205);
  assert.equal(calls.filter(({ url }) => url.endsWith('/confirm/')).length, 205);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
  assert.equal(getMaxActive(), 4);
  assert.ok(calls.every(({ headers }) => headers['X-CSRFToken'] === 'csrf-value'));
  assert.ok(coordinator.items.every(({ status }) => status === 'uploaded'));
});

test('resume skips confirmed items, retries failed items, and keeps missing manifest items waiting', async () => {
  const { calls, coordinator, getMaxActive } = makeHarness();
  const manifest = {
    batch: { id: 'batch-1', event: { id: 42, name: 'Race' } },
    items: [
      manifestItem({ id: 'confirmed', filename: 'confirmed.jpg', confirmed: true, status: 'uploaded' }),
      manifestItem({ id: 'pending', filename: 'pending.jpg', status: 'pending' }),
      manifestItem({ id: 'failed', filename: 'failed.jpg', status: 'failed' }),
      manifestItem({ id: 'missing', filename: 'missing.jpg', status: 'authorized' }),
    ],
  };

  await coordinator.resume(
    [file('confirmed.jpg', 4, 1000), file('pending.jpg', 4, 1000), file('failed.jpg', 4, 1000)],
    manifest,
  );

  assert.equal(coordinator.items.find((item) => item.id === 'confirmed').status, 'uploaded');
  assert.equal(coordinator.items.find((item) => item.id === 'missing').status, 'waiting');
  assert.equal(calls.filter(({ url }) => url.includes('/items/confirmed/')).length, 0);
  assert.equal(calls.filter(({ url }) => url.endsWith('/authorize/')).length, 1);
  assert.equal(calls.filter(({ url }) => url.endsWith('/retry/')).length, 1);
  assert.equal(calls.filter(({ url }) => url.endsWith('/confirm/')).length, 2);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 0);
  assert.equal(getMaxActive(), 2);
});

test('resume finalizes only after every durable manifest item reaches a terminal state', async () => {
  const { calls, coordinator, getMaxActive } = makeHarness();
  const files = Array.from({ length: 6 }, (_, index) => file(`${index}.jpg`, 4, 1000));
  const manifest = {
    batch: { id: 'batch-1', event: { id: 42, name: 'Race' } },
    items: files.map((selected, index) => manifestItem({ id: `item-${index + 1}`, filename: selected.name })),
  };

  await coordinator.resume(files, manifest);

  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
  assert.equal(getMaxActive(), 4);
});
