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
  const root = {
    dataset: {
      eventId: '42',
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
      return null;
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
    ['[data-summary-background]', {}],
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
  onCreateControl = null,
  onAuthorizeControl = null,
  onConfirmControl = null,
  onRetryControl = null,
  onFailedControl = null,
  onFinalizeControl = null,
  retryTimer = null,
} = {}) {
  const calls = [];
  const delays = [];
  let batchSequence = 0;
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
      const id = `batch-${++batchSequence}`;
      return onCreateControl ? onCreateControl({ id, body }) : response(201, { batch: { id } });
    }
    if (/^\/batch-\d+\/items\/$/.test(url)) {
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
    if (/^\/batch-\d+\/finalize\/$/.test(url)) {
      if (onFinalizeControl) {
        return onFinalizeControl({ body, url });
      }
      return response(200, { batch: { id: url.split('/')[1], status: 'complete' } });
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
      closest() { return this; },
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
  const targets = [plain, finish];
  const root = {
    dataset: {},
    querySelectorAll() { return targets; },
    contains(target) { return targets.includes(target); },
    addEventListener(type, listener) { listeners.set(`root:${type}`, listener); },
  };
  const selections = [];
  bindFolderTargets(root, (files, folder) => selections.push({ files, folder }));
  const drag = (type, target, relatedTarget = null) => listeners.get(`root:${type}`)({
    target,
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

  const cancelled = await coordinator.cancel(coordinator.items[0].clientItemId);
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

  assert.equal(await coordinator.cancel(coordinator.items[0].clientItemId), true);
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

  assert.equal(await coordinator.cancel(coordinator.items[0].clientItemId), true);
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
  assert.equal(await coordinator.cancel(coordinator.items[0].clientItemId), true);
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
  assert.equal(coordinator.items[0].error, 'Не удалось подтвердить загрузку. Повторите подтверждение.');
  assert.equal(coordinator.active, false);
  assert.equal(calls.filter(({ url }) => url.endsWith('/failed/')).length, 1);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
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
  assert.equal(failed.error, 'Не удалось подтвердить загрузку. Повторите подтверждение.');
  assert.equal(pending.status, 'uploaded');
  assert.equal(coordinator.active, false);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 0);
});

test('resume retries a temporary confirmation failure without reopening or retransferring', async () => {
  let confirmations = 0;
  const { calls, coordinator } = makeHarness({
    onConfirmControl: ({ url }) => ++confirmations === 1
      ? response(503, { error: { code: 'storage_unavailable' } })
      : response(200, { item: { id: url.split('/')[3], status: 'uploaded' } }),
  });
  await coordinator.resume([file('failed.jpg', 4, 1000)], {
    batch: { id: 'batch-1', event: { id: 42, name: 'Race' } },
    items: [manifestItem({ id: 'failed', filename: 'failed.jpg', status: 'failed' })],
  });
  assert.equal(coordinator.items[0].status, 'uploaded');
  assert.equal(confirmations, 2);
  assert.equal(calls.filter(({ url }) => url.endsWith('/retry/')).length, 1);
  assert.equal(calls.filter(({ url }) => url.endsWith('/failed/')).length, 0);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
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

test('departure warning covers selected and recoverable files but clears after confirmation', () => {
  const { coordinator } = makeHarness();
  coordinator.items = [{ status: 'pending' }];
  assert.equal(coordinator.shouldWarnBeforeUnload(), true);
  coordinator.items[0].status = 'failed';
  assert.equal(coordinator.shouldWarnBeforeUnload(), true);
  coordinator.items[0].status = 'waiting';
  assert.equal(coordinator.shouldWarnBeforeUnload(), true);
  coordinator.items[0].status = 'uploaded';
  coordinator.active = true; // Server finalization is outstanding; originals are confirmed.
  assert.equal(coordinator.shouldWarnBeforeUnload(), false);
});

function mountedUpload(harness = makeHarness(), nodes = {}) {
  const listeners = new Map();
  const events = [];
  const root = {
    dataset: {
      eventId: '42', createBatchUrl: '/batches/', registerUrlTemplate: '/{batch}/items/',
      authorizeUrlTemplate: '/{batch}/items/{item}/authorize/', retryUrlTemplate: '/{batch}/items/{item}/retry/',
      confirmUrlTemplate: '/{batch}/items/{item}/confirm/', failedUrlTemplate: '/{batch}/items/{item}/failed/',
      finalizeUrlTemplate: '/{batch}/finalize/', resumeManifestUrlTemplate: '/{batch}/resume/',
      maxFiles: '220', maxFileBytes: '10', registrationChunk: '100', concurrency: '4',
    },
    ownerDocument: { dispatchEvent(event) { events.push(event); } },
    querySelector(selector) { return nodes[selector] || null; },
    contains() { return true; },
    querySelectorAll() { return []; },
    addEventListener(type, callback) { listeners.set(type, callback); },
  };
  const coordinator = bindUploadPage(root, harness.coordinator);
  const select = (files, folder = {}) => {
    const target = { dataset: folder };
    const input = { files, closest: () => target, matches: () => true };
    listeners.get('change')({ target: input });
    assert.equal(input.value, '');
  };
  return { root, coordinator, events, select, harness };
}

test('fixed workspace starts selections automatically and emits activity for each actual batch', async () => {
  const mounted = mountedUpload();
  assert.equal(bindUploadPage(mounted.root, mounted.harness.coordinator), mounted.coordinator);
  mounted.select([file('one.jpg', 4)], { folderId: '8', folderName: 'Финиш' });
  assert.equal(mounted.coordinator.shouldWarnBeforeUnload(), true);
  await new Promise(setImmediate);
  assert.equal(mounted.events[0]?.detail.active, true);
  assert.equal(mounted.events[0].detail.batchId, null);
  assert.equal(mounted.harness.calls[0].body.event_id, 42);
  assert.equal(mounted.harness.calls[1].body.items[0].folder_id, 8);
  assert.ok(mounted.events.some((event) => event.detail.active && event.detail.batchId === 'batch-1'));
  assert.deepEqual(mounted.events.at(-1).detail, { active: false, batchId: 'batch-1' });
  assert.ok(mounted.events.every((event) => event.type === 'findme:event-photo-upload-activity'));

  mounted.select([file('two.jpg', 4)]);
  await new Promise(setImmediate);
  assert.deepEqual(mounted.coordinator.items.map((item) => item.status), ['uploaded', 'uploaded']);
  assert.deepEqual(mounted.events.at(-1).detail, { active: false, batchId: 'batch-2' });
});

test('upload activity identifies a new batch only after its first files are registered', async () => {
  let releaseRegistration;
  const registrationGate = new Promise((resolve) => { releaseRegistration = resolve; });
  const harness = makeHarness();
  const fetch = harness.coordinator.fetch;
  harness.coordinator.fetch = async (url, options) => {
    if (url === '/batch-1/items/') await registrationGate;
    return fetch(url, options);
  };
  const mounted = mountedUpload(harness);
  mounted.select([file('one.jpg', 4)]);
  await new Promise(setImmediate);
  const activityBeforeRegistration = mounted.events.map((event) => event.detail);
  releaseRegistration();
  await new Promise(setImmediate);
  assert.deepEqual(activityBeforeRegistration, [{ active: true, batchId: null }]);
  assert.deepEqual(mounted.events[1].detail, { active: true, batchId: 'batch-1' });
});

test('a queued selection follows the active batch and an old item retries against its own batch', async () => {
  let releaseFirstBatch;
  const firstBatchGate = new Promise((resolve) => { releaseFirstBatch = resolve; });
  const harness = makeHarness({
    xhrResults: [{ type: 'load', status: 400 }],
    onAuthorizeControl: async ({ url }) => {
      if (url.includes('/batch-1/')) await firstBatchGate;
      return response(200, { grant: { url: 'https://storage.example/', fields: {} } });
    },
  });
  let queueClick;
  const mounted = mountedUpload(harness, {
    '[data-upload-queue]': { addEventListener(_type, callback) { queueClick = callback; } },
  });
  mounted.select(Array.from({ length: 5 }, (_, index) => file(`first-${index}.jpg`, 4)));
  await new Promise(setImmediate);
  mounted.select([file('second.jpg', 4)]);
  assert.equal(mounted.coordinator.items.length, 6);
  await new Promise(setImmediate);
  assert.equal(harness.calls.filter(({ url }) => url === '/batches/').length, 1);
  releaseFirstBatch();
  await new Promise(setImmediate);
  assert.ok(harness.getMaxActive() <= 4);
  assert.equal(mounted.coordinator.items.at(-1).status, 'uploaded');
  const failed = mounted.coordinator.items.find((item) => item.status === 'failed');
  const retry = { dataset: { clientItemId: failed.clientItemId } };
  await queueClick({ target: { closest: (selector) => selector === '[data-retry-item]' ? retry : null } });
  assert.ok(harness.calls.some(({ url }) => url === `/batch-1/items/${failed.id}/retry/`));
  assert.equal(failed.status, 'uploaded');
});

test('a control failure in one item waits for the other transfers before starting the next batch', async () => {
  let releaseFirstBatch;
  const firstBatchGate = new Promise((resolve) => { releaseFirstBatch = resolve; });
  const harness = makeHarness({
    onAuthorizeControl: async ({ url }) => {
      if (url === '/batch-1/items/item-1/authorize/') return response(503);
      if (url === '/batch-1/items/item-2/authorize/') await firstBatchGate;
      return response(200, { grant: { url: 'https://storage.example/', fields: {} } });
    },
  });
  const mounted = mountedUpload(harness);
  mounted.select([file('first.jpg', 4), file('other.jpg', 4)]);
  mounted.select([file('next.jpg', 4)]);
  await new Promise(setImmediate);
  const createdWhileHeld = harness.calls.filter(({ url }) => url === '/batches/').length;
  releaseFirstBatch();
  await new Promise(setImmediate);
  assert.equal(createdWhileHeld, 1);
  assert.deepEqual(mounted.coordinator.items.map((item) => item.status), ['needs_attention', 'uploaded', 'uploaded']);
});

test('a failed batch creation allows the next selection and can be retried afterwards', async () => {
  const harness = makeHarness({
    onCreateControl: ({ id }) => id === 'batch-1' ? response(503) : response(201, { batch: { id } }),
  });
  let queueClick;
  const mounted = mountedUpload(harness, {
    '[data-upload-queue]': { addEventListener(_type, callback) { queueClick = callback; } },
  });
  mounted.select([file('first.jpg', 4)]);
  mounted.select([file('second.jpg', 4)]);
  await new Promise(setImmediate);
  assert.ok(harness.calls.some(({ url }) => url === '/batch-2/finalize/'));
  assert.deepEqual(mounted.coordinator.items.map((item) => item.status), ['failed', 'uploaded']);

  const retry = { dataset: { clientItemId: mounted.coordinator.items[0].clientItemId } };
  await queueClick({ target: { closest: (selector) => selector === '[data-retry-item]' ? retry : null } });
  assert.ok(harness.calls.some(({ url }) => url === '/batch-3/finalize/'));
  assert.deepEqual(mounted.coordinator.items.map((item) => item.status), ['uploaded', 'uploaded']);
});

test('success copy requires confirmation, never transfer progress or terminal failure', () => {
  const root = summaryRoot();
  const coordinator = { active: false, items: [{ file: { size: 4 }, status: 'failed', progress: 100 }] };
  renderPage(root, coordinator);
  assert.doesNotMatch(root.node('[data-summary-message]').textContent, /Можно закрыть/);
  coordinator.active = true;
  coordinator.items[0].status = 'uploading';
  renderPage(root, coordinator);
  assert.equal(root.node('[data-summary-message]').textContent, 'Держите страницу открытой — идёт загрузка');
  assert.equal(root.node('[data-summary-background]').hidden, true);
  coordinator.items[0].status = 'uploaded';
  renderPage(root, coordinator);
  assert.equal(root.node('[data-summary-message]').textContent, 'Все фотографии загружены. Можно закрыть страницу');
  assert.equal(root.node('[data-summary-background]').hidden, false);
});

test('resume reports active browser work while asynchronous file matching is pending', async () => {
  const { coordinator } = makeHarness();
  const manifest = { batch: { id: 'batch-1', event: { id: 42 } }, items: [manifestItem({ id: 'item-1', filename: 'one.jpg' })] };
  const completion = coordinator.resume([file('one.jpg', 4, 1000)], manifest);
  assert.equal(coordinator.active, true);
  await completion;
});


test('fixed workspace rejects another event resume manifest before opening files or replacing queue', async () => {
  let resumeClick;
  let pickerOpened = false;
  const harness = makeHarness();
  harness.coordinator.fetch = async () => response(200, { batch: { id: 'other', event: { id: 43 } }, items: [] });
  const mounted = mountedUpload(harness, {
    '[data-unfinished-uploads]': { addEventListener(_type, callback) { resumeClick = callback; } },
    '#resume-upload-files': { addEventListener() {}, click() { pickerOpened = true; } },
  });
  await resumeClick({ target: { closest: () => ({ dataset: { resumeBatchId: 'other' } }) } });
  assert.equal(pickerOpened, false);
  assert.deepEqual(mounted.coordinator.items, []);
});


test('slow confirmations release POST slots but bound the whole unfinished pipeline', async () => {
  const waiting = [];
  const { calls, coordinator, getMaxActive } = makeHarness({
    onConfirmControl: ({ url }) => new Promise((resolve) => waiting.push(() => resolve(response(200, { item: { id: url.split('/')[3], status: 'uploaded' } })))),
  });
  const completion = startUpload(coordinator, Array.from({ length: 12 }, (_, i) => file(`${i}.jpg`, 4)), '42');
  await new Promise(setImmediate);
  const posted = coordinator.items.filter((item) => item.progress === 100).length;
  const confirms = waiting.length;
  const finalizes = calls.filter(({ url }) => url.endsWith('/finalize/')).length;
  // Release every wave before assertions so a regression leaves no dangling work.
  for (let wave = 0; wave < 20 && coordinator.items.some((item) => item.status !== 'uploaded'); wave += 1) {
    waiting.splice(0).forEach((release) => release());
    await new Promise(setImmediate);
  }
  assert.ok(coordinator.items.every((item) => item.status === 'uploaded'));
  await completion;
  assert.equal(posted, 8);
  assert.equal(confirms, 4);
  assert.equal(finalizes, 0);
  assert.equal(getMaxActive(), 4);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
});

test('terminal transfer failures retain distinct safe codes and actual POST diagnostics', async () => {
  for (const [results, code, status, attempts] of [
    [[{ type: 'load', status: 400 }], 'transfer_http_error', 400, 1],
    [[{ type: 'load', status: 403 }, { type: 'load', status: 403 }], 'transfer_http_error', 403, 2],
    [Array.from({ length: 4 }, () => ({ type: 'error' })), 'transfer_network_error', 0, 4],
    [Array.from({ length: 4 }, () => ({ type: 'timeout' })), 'transfer_timeout', 0, 4],
  ]) {
    const { calls, coordinator } = makeHarness({ xhrResults: results });
    await startUpload(coordinator, [file('private-name.jpg', 4)], '42');
    const report = calls.find(({ url }) => url.endsWith('/failed/')).body;
    assert.deepEqual(Object.keys(report).sort(), ['code', 'http_status', 'post_attempts', 'post_duration_ms']);
    assert.equal(report.code, code);
    assert.equal(report.http_status, status);
    assert.equal(report.post_attempts, attempts);
    assert.ok(Number.isInteger(report.post_duration_ms) && report.post_duration_ms >= 0);
    assert.equal(coordinator.items[0].status, 'failed');
  }
});

test('lost confirmation responses recover by confirmation without another POST', async () => {
  let attempts = 0;
  const { calls, coordinator } = makeHarness({
    onConfirmControl: ({ url }) => {
      attempts += 1;
      if (attempts === 1) throw new Error('response lost after commit');
      return response(200, { item: { id: url.split('/')[3], status: 'uploaded' } });
    },
  });
  await startUpload(coordinator, [file('one.jpg', 4)], '42');
  assert.equal(attempts, 2);
  assert.equal(calls.filter(({ url }) => url.endsWith('/authorize/')).length, 1);
  assert.equal(calls.filter(({ url }) => url.endsWith('/failed/')).length, 0);
  assert.equal(coordinator.items[0].status, 'uploaded');
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
});


test('exhausted confirmation retries can be retried locally without another transfer', async () => {
  let available = false;
  const { calls, coordinator } = makeHarness({
    onConfirmControl: ({ url }) => available
      ? response(200, { item: { id: url.split('/')[3], status: 'uploaded' } })
      : response(503, { error: { code: 'storage_unavailable' } }),
  });
  await startUpload(coordinator, [file('one.jpg', 4)], '42');
  assert.equal(coordinator.items[0].status, 'failed');
  assert.equal(coordinator.active, false);
  assert.equal(calls.filter(({ url }) => url.endsWith('/confirm/')).length, 4);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 0);
  assert.equal(calls.filter(({ url }) => url.endsWith('/failed/')).length, 0);
  available = true;
  assert.equal(await coordinator.manualRetry(coordinator.items[0].clientItemId), true);
  assert.equal(coordinator.items[0].status, 'uploaded');
  assert.equal(calls.filter(({ url }) => url.endsWith('/authorize/')).length, 1);
  assert.equal(calls.filter(({ url }) => url.endsWith('/retry/')).length, 0);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
});

test('resume probes an authorized upload before transferring and only sends missing bytes', async () => {
  for (const present of [true, false]) {
    let confirmations = 0;
    const { calls, coordinator } = makeHarness({
      onConfirmControl: ({ url }) => !present && ++confirmations === 1
        ? response(503, { error: { code: 'object_missing' } })
        : response(200, { item: { id: url.split('/')[3], status: 'uploaded' } }),
    });
    await coordinator.resume([file('one.jpg', 4, 1000)], {
      batch: { id: 'batch-1', event: { id: 42 } },
      items: [manifestItem({ id: 'one', filename: 'one.jpg', status: 'authorized' })],
    });
    assert.equal(coordinator.items[0].status, 'uploaded');
    assert.equal(calls.filter(({ url }) => url.endsWith('/authorize/')).length, present ? 0 : 1);
    assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
  }
});

test('backoff keeps at most eight unfinished lifecycles and cancellation drains queued confirmations', async () => {
  const timers = [];
  const { calls, coordinator } = makeHarness({
    xhrResults: Array.from({ length: 8 }, () => ({ type: 'error' })),
    retryTimer: { setTimeout(callback) { timers.push(callback); return timers.length; }, clearTimeout() {} },
  });
  const completion = startUpload(coordinator, Array.from({ length: 12 }, (_, i) => file(`${i}.jpg`, 4)), '42');
  await new Promise(setImmediate);
  const authorized = calls.filter(({ url }) => url.endsWith('/authorize/')).length;
  const backoffs = timers.length;
  timers.splice(0).forEach((callback) => callback());
  await completion;
  assert.equal(authorized, 8);
  assert.equal(backoffs, 8);
  assert.equal(coordinator.items.filter((item) => item.status === 'uploaded').length, 12);
});

test('cancelling a queued confirmation settles the item and still finalizes siblings', async () => {
  const waiting = [];
  const { calls, coordinator } = makeHarness({
    onConfirmControl: ({ url }) => new Promise((resolve) => waiting.push(() => resolve(response(200, { item: { id: url.split('/')[3], status: 'uploaded' } })))),
  });
  const completion = startUpload(coordinator, Array.from({ length: 9 }, (_, i) => file(`${i}.jpg`, 4)), '42');
  await new Promise(setImmediate);
  const cancelled = coordinator.items[4];
  await coordinator.cancel(cancelled.clientItemId);
  for (let wave = 0; wave < 20 && coordinator.items.some((item) => !['uploaded', 'failed'].includes(item.status)); wave += 1) {
    waiting.splice(0).forEach((release) => release());
    await new Promise(setImmediate);
  }
  assert.ok(coordinator.items.every((item) => ['uploaded', 'failed'].includes(item.status)));
  await completion;
  assert.equal(cancelled.status, 'failed');
  const reports = calls.filter(({ url }) => url.endsWith('/failed/'));
  assert.equal(reports.length, 1);
  assert.equal(reports[0].body.code, 'transfer_cancelled');
  assert.equal(reports[0].body.http_status, 0);
  assert.equal(calls.filter(({ url }) => url.endsWith('/confirm/')).length, 8);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
});

test('a confirmation outage pauses later transfers until local confirmation retry recovers', async () => {
  let available = false;
  const { calls, coordinator } = makeHarness({
    onConfirmControl: ({ url }) => available
      ? response(200, { item: { id: url.split('/')[3], status: 'uploaded' } })
      : response(503, { error: { code: 'storage_unavailable' } }),
  });
  await startUpload(coordinator, Array.from({ length: 20 }, (_, i) => file(`${i}.jpg`, 4)), '42');
  assert.equal(calls.filter(({ url }) => url.endsWith('/authorize/')).length, 8);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 0);
  available = true;
  await Promise.all(coordinator.items.filter((item) => item.status === 'failed').map((item) => coordinator.manualRetry(item.clientItemId)));
  assert.equal(coordinator.items.filter((item) => item.status === 'uploaded').length, 20);
  assert.equal(calls.filter(({ url }) => url.endsWith('/authorize/')).length, 20);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
});

test('retrying a transfer failure waits behind unresolved confirmations', async () => {
  let available = false;
  const { calls, coordinator } = makeHarness({
    xhrResults: [{ type: 'load', status: 400 }],
    onConfirmControl: ({ url }) => available
      ? response(200, { item: { id: url.split('/')[3], status: 'uploaded' } })
      : response(503, { error: { code: 'storage_unavailable' } }),
  });
  await startUpload(coordinator, Array.from({ length: 20 }, (_, i) => file(`${i}.jpg`, 4)), '42');
  const transferFailure = coordinator.items.find((item) => item.status === 'failed' && !item.confirmationPending);
  await coordinator.manualRetry(transferFailure.clientItemId);
  assert.equal(calls.filter(({ url }) => url.endsWith('/retry/')).length, 0);
  available = true;
  await Promise.all(coordinator.items.filter((item) => item.confirmationPending).map((item) => coordinator.manualRetry(item.clientItemId)));
  assert.equal(coordinator.items.filter((item) => item.status === 'uploaded').length, 20);
  assert.equal(calls.filter(({ url }) => url.endsWith('/retry/')).length, 1);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
});

test('POST diagnostics sum transfer durations and exclude time between attempts', async (t) => {
  const ticks = [100, 140, 300, 390];
  t.mock.method(performance, 'now', () => ticks.shift());
  const { calls, coordinator } = makeHarness({ xhrResults: [
    { type: 'load', status: 500 }, { type: 'load', status: 400 },
  ] });
  await startUpload(coordinator, [file('one.jpg', 4)], '42');
  const report = calls.find(({ url }) => url.endsWith('/failed/')).body;
  assert.equal(report.post_attempts, 2);
  assert.equal(report.post_duration_ms, 130);
  assert.equal(report.http_status, 400);
});

test('manual retry preserves the authorized resume probe until missing bytes are established', async () => {
  let available = false;
  let transferred = false;
  const { calls, coordinator } = makeHarness({
    onAuthorizeControl: () => {
      transferred = true;
      return response(200, { grant: { url: 'https://storage.example/', fields: {} } });
    },
    onConfirmControl: ({ url }) => !available
      ? response(503, { error: { code: 'storage_unavailable' } })
      : !transferred
        ? response(503, { error: { code: 'object_missing' } })
        : response(200, { item: { id: url.split('/')[3], status: 'uploaded' } }),
  });
  await coordinator.resume([file('one.jpg', 4, 1000)], {
    batch: { id: 'batch-1', event: { id: 42 } },
    items: [manifestItem({ id: 'one', filename: 'one.jpg', status: 'authorized' })],
  });
  assert.equal(coordinator.items[0].status, 'failed');
  available = true;
  await coordinator.manualRetry(coordinator.items[0].clientItemId);
  assert.equal(coordinator.items[0].status, 'uploaded');
  assert.equal(calls.filter(({ url }) => url.endsWith('/authorize/')).length, 1);
  assert.equal(calls.filter(({ url }) => url.endsWith('/retry/')).length, 0);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
});

test('offline failure reporting keeps the same diagnostics and a working item retry', async () => {
  let connected = false;
  const { calls, coordinator } = makeHarness({
    xhrResults: Array.from({ length: 4 }, () => ({ type: 'error' })),
    onFailedControl: ({ url }) => {
      if (!connected) throw new TypeError('offline private detail');
      return response(200, { item: { id: url.split('/')[3], status: 'failed' } });
    },
  });
  await startUpload(coordinator, [file('one.jpg', 4)], '42');
  const item = coordinator.items[0];
  assert.equal(item.status, 'failed');
  assert.match(item.error, /Соединение/);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 0);
  const report = calls.find(({ url }) => url.endsWith('/failed/')).body;
  connected = true;
  assert.equal(await coordinator.manualRetry(item.clientItemId), true);
  const reports = calls.filter(({ url }) => url.endsWith('/failed/'));
  assert.equal(reports.length, 2);
  assert.deepEqual(reports[1].body, report);
  assert.equal(report.code, 'transfer_network_error');
  assert.equal(report.post_attempts, 4);
  assert.equal(item.status, 'uploaded');
  assert.ok(calls.indexOf(reports[1]) < calls.findIndex(({ url }) => url.endsWith('/retry/')));
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
});

test('cancelling an outage-deferred item reports zero POST immediately and excludes continuation', async () => {
  let connected = false;
  const { calls, coordinator } = makeHarness({
    onConfirmControl: ({ url }) => connected
      ? response(200, { item: { id: url.split('/')[3], status: 'uploaded' } })
      : response(503, { error: { code: 'storage_unavailable' } }),
  });
  await startUpload(coordinator, Array.from({ length: 9 }, (_, i) => file(`${i}.jpg`, 4)), '42');
  const deferred = coordinator.items[8];
  assert.equal(await coordinator.cancel(deferred.clientItemId), true);
  await new Promise(setImmediate);
  assert.equal(deferred.status, 'failed');
  const report = calls.find(({ url }) => url.endsWith('/failed/'));
  assert.equal(report.body.code, 'transfer_cancelled');
  assert.equal(report.body.post_attempts, 0);
  connected = true;
  await Promise.all(coordinator.items.filter((item) => item.confirmationPending).map((item) => coordinator.manualRetry(item.clientItemId)));
  assert.equal(deferred.status, 'failed');
  assert.equal(calls.filter(({ url }) => url.endsWith('/authorize/')).length, 8);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
});

test('cancelling an authorized resume before its first confirm does not publish it', async () => {
  const waiting = [];
  const { calls, coordinator } = makeHarness({
    onConfirmControl: ({ url }) => new Promise((resolve) => waiting.push(() => resolve(response(200, { item: { id: url.split('/')[3], status: 'uploaded' } })))),
  });
  const completion = coordinator.resume(Array.from({ length: 5 }, (_, i) => file(`${i}.jpg`, 4, 1000)), {
    batch: { id: 'batch-1', event: { id: 42 } },
    items: Array.from({ length: 5 }, (_, i) => manifestItem({ id: `item-${i}`, filename: `${i}.jpg`, status: 'authorized' })),
  });
  await new Promise(setImmediate);
  const queued = coordinator.items[4];
  await coordinator.cancel(queued.clientItemId);
  for (let wave = 0; wave < 20 && coordinator.items.some((item) => !['uploaded', 'failed'].includes(item.status)); wave += 1) {
    waiting.splice(0).forEach((release) => release());
    await new Promise(setImmediate);
  }
  assert.ok(coordinator.items.every((item) => ['uploaded', 'failed'].includes(item.status)));
  await completion;
  assert.equal(queued.status, 'failed');
  assert.equal(calls.filter(({ url }) => url.endsWith('/confirm/')).length, 4);
  assert.equal(calls.filter(({ url }) => url.endsWith('/failed/')).length, 1);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
});

test('a stale confirmation outcome is reconciled before finalization or failed-only retry', async () => {
  let stale = true;
  const { calls, coordinator } = makeHarness({
    onConfirmControl: ({ url }) => {
      if (stale) {
        stale = false;
        return response(503, { error: { code: 'confirmation_in_progress' } });
      }
      return response(200, { item: { id: url.split('/')[3], status: 'uploaded' } });
    },
  });
  await startUpload(coordinator, [file('one.jpg', 4)], '42');
  assert.equal(coordinator.items[0].status, 'uploaded');
  assert.equal(calls.filter(({ url }) => url.endsWith('/confirm/')).length, 2);
  assert.equal(calls.filter(({ url }) => url.endsWith('/authorize/')).length, 1);
  assert.equal(calls.filter(({ url }) => url.endsWith('/retry/')).length, 0);
  assert.equal(calls.filter(({ url }) => url.endsWith('/failed/')).length, 0);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
});

test('queued resume cancellations reconcile concurrent commits without retaining confirm slots', async () => {
  const waiting = [];
  const { calls, coordinator } = makeHarness({
    onConfirmControl: ({ url }) => new Promise((resolve) => waiting.push(() => resolve(response(200, { item: { id: url.split('/')[3], status: 'uploaded' } })))),
    onFailedControl: () => response(409, { error: { code: 'item_uploaded' } }),
  });
  const completion = coordinator.resume(Array.from({ length: 8 }, (_, i) => file(`${i}.jpg`, 4, 1000)), {
    batch: { id: 'batch-1', event: { id: 42 } },
    items: Array.from({ length: 8 }, (_, i) => manifestItem({ id: `item-${i}`, filename: `${i}.jpg`, status: 'authorized' })),
  });
  await new Promise(setImmediate);
  await Promise.all(coordinator.items.slice(4).map((item) => coordinator.cancel(item.clientItemId)));
  for (let wave = 0; wave < 20 && coordinator.items.some((item) => item.status !== 'uploaded'); wave += 1) {
    waiting.splice(0).forEach((release) => release());
    await new Promise(setImmediate);
  }
  assert.ok(coordinator.items.every((item) => item.status === 'uploaded'));
  await completion;
  assert.equal(calls.filter(({ url }) => url.endsWith('/confirm/')).length, 8);
  assert.equal(calls.filter(({ url }) => url.endsWith('/failed/')).length, 4);
  assert.equal(calls.filter(({ url }) => url.endsWith('/authorize/')).length, 0);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
});

test('cancelling a deferred failed resume retry preserves its terminal state and later retry', async () => {
  let connected = false;
  const { calls, coordinator } = makeHarness({
    onConfirmControl: ({ url }) => connected
      ? response(200, { item: { id: url.split('/')[3], status: 'uploaded' } })
      : response(503, { error: { code: 'storage_unavailable' } }),
    onFailedControl: () => response(409, { error: { code: 'item_failed' } }),
  });
  await coordinator.resume(Array.from({ length: 9 }, (_, i) => file(`${i}.jpg`, 4, 1000)), {
    batch: { id: 'batch-1', event: { id: 42 } },
    items: Array.from({ length: 9 }, (_, i) => manifestItem({ id: `item-${i}`, filename: `${i}.jpg`, status: i === 8 ? 'failed' : 'authorized' })),
  });
  const deferredRetry = coordinator.items[8];
  assert.equal(await coordinator.cancel(deferredRetry.clientItemId), true);
  await new Promise(setImmediate);
  assert.equal(calls.filter(({ url }) => url.endsWith('/failed/')).length, 0);
  assert.equal(calls.filter(({ url }) => url.endsWith('/retry/')).length, 0);
  assert.equal(deferredRetry.status, 'failed');
  connected = true;
  await Promise.all(coordinator.items.slice(0, 8).map((item) => coordinator.manualRetry(item.clientItemId)));
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
  await coordinator.manualRetry(deferredRetry.clientItemId);
  assert.equal(deferredRetry.status, 'uploaded');
  assert.equal(calls.filter(({ url }) => url.endsWith('/retry/')).length, 1);
  assert.equal(calls.filter(({ url }) => url.endsWith('/finalize/')).length, 2);
});

test('the page contains a delayed deferred-cancel finalization failure and shows recovery guidance', async () => {
  let connected = false;
  let releaseFailed;
  const failedResponse = new Promise((resolve) => { releaseFailed = resolve; });
  const harness = makeHarness({
    onConfirmControl: ({ url }) => connected
      ? response(200, { item: { id: url.split('/')[3], status: 'uploaded' } })
      : response(503, { error: { code: 'storage_unavailable' } }),
    onFailedControl: () => failedResponse,
    onFinalizeControl: () => { throw new TypeError('private finalize offline detail'); },
  });
  let queueClick;
  const summaryTitle = {};
  const summaryMessage = {};
  const mounted = mountedUpload(harness, {
    '[data-upload-queue]': { addEventListener(_type, callback) { queueClick = callback; } },
    '#upload-summary-title': summaryTitle,
    '[data-summary-message]': summaryMessage,
  });
  const click = (action, item) => queueClick({ target: { closest: (selector) => selector === `[data-${action}-item]` ? { dataset: { clientItemId: item.clientItemId } } : null } });
  mounted.select(Array.from({ length: 9 }, (_, i) => file(`${i}.jpg`, 4)));
  await new Promise(setImmediate);
  const cancelled = mounted.coordinator.items[8];
  const cancellation = click('cancel', cancelled);
  await new Promise(setImmediate);
  connected = true;
  await Promise.all(mounted.coordinator.items.slice(0, 8).map((item) => click('retry', item)));
  assert.equal(harness.calls.filter(({ url }) => url.endsWith('/finalize/')).length, 0);
  releaseFailed(response(200, { item: { id: cancelled.id, status: 'failed' } }));
  await cancellation;
  await new Promise(setImmediate);
  assert.equal(cancelled.status, 'failed');
  assert.equal(mounted.coordinator.active, false);
  assert.equal(summaryTitle.textContent, 'Загрузка остановлена');
  assert.match(summaryMessage.textContent, /Обновите страницу/);
  assert.doesNotMatch(summaryMessage.textContent, /private/);
  assert.equal(harness.calls.filter(({ url }) => url.endsWith('/finalize/')).length, 1);
});
