'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const {
  EventPhotoStatusController,
  IMPORT_PROGRESS_EVENT,
  MANUAL_REFRESH_EVENT,
  UPLOAD_ACTIVITY_EVENT,
  bindEventPhotoStatus,
} = require('../../src/backend/static/ui/event-photo-status.js');

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, reject, resolve };
}

async function settle() {
  for (let index = 0; index < 10; index += 1) await Promise.resolve();
}

class FakeTimers {
  constructor() {
    this.entries = [];
  }

  setTimeout(callback, delay) {
    const entry = { callback, delay, cleared: false };
    this.entries.push(entry);
    return entry;
  }

  clearTimeout(entry) {
    entry.cleared = true;
  }

  pending() {
    return this.entries.filter((entry) => !entry.cleared);
  }

  async runNext() {
    const entry = this.pending()[0];
    assert.ok(entry, 'expected a pending timer');
    entry.cleared = true;
    entry.callback();
    await settle();
  }
}

class FakeEventTarget {
  constructor() {
    this.listeners = new Map();
    this.events = [];
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  dispatch(type, event = {}) {
    for (const listener of this.listeners.get(type) || []) listener(event);
  }

  dispatchEvent(event) {
    this.events.push(event);
    this.dispatch(event.type, event);
    return true;
  }
}

class FakeNode {
  constructor(dataset = {}) {
    this.dataset = dataset;
    this.textContent = '';
    this.hidden = false;
    this.nodes = new Map();
    this.nodeLists = new Map();
    this.listeners = new Map();
  }

  querySelector(selector) {
    return this.nodes.get(selector) || null;
  }

  querySelectorAll(selector) {
    return this.nodeLists.get(selector) || [];
  }

  set(selector, node) {
    this.nodes.set(selector, node);
    return node;
  }

  setAll(selector, nodes) {
    this.nodeLists.set(selector, nodes);
    return nodes;
  }

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }

  dispatch(type, event) {
    return this.listeners.get(type)?.(event);
  }

  removeAttribute(name) {
    if (name === 'data-unfinished-upload') delete this.dataset.unfinishedUpload;
  }

  remove() {
    this.removed = true;
  }
}

function statusResponse(body, status = 200, html = '') {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => html,
  };
}

function payload({ active = true, changed = false } = {}) {
  return {
    server_timestamp: '2026-09-07T10:00:00.000Z',
    has_active_work: active,
    capabilities: { can_inspect: true, can_upload: true },
    admin: {
      summary: {
        total: 2,
        categories: { processing: active ? 1 : 0, failed: active ? 0 : 1 },
      },
      filtered_result_count: changed ? 1 : 2,
      result_list_changed: changed,
      photos: [
        {
          photo_id: 'photo-1',
          category_label: active ? 'Обрабатывается' : 'Ошибка',
          stages: [{ label: 'Метаданные', status_label: active ? 'В очереди' : 'Ошибка' }],
        },
      ],
    },
    batches: [
      {
        id: 'batch-1',
        status: 'uploading',
        confirmed_count: 1,
        expected_count: 2,
        failed_count: 0,
        unresolved_count: 1,
        can_close: false,
        has_active_work: active,
        processing: { total: 1, categories: { processing: active ? 1 : 0 } },
      },
    ],
  };
}

function harness(fetch) {
  const timers = new FakeTimers();
  const document = new FakeEventTarget();
  document.visibilityState = 'visible';
  document.activeElement = null;
  document.nextFragment = null;
  document.createElement = (tag) => {
    assert.equal(tag, 'template');
    return {
      content: { firstElementChild: null },
      set innerHTML(_value) {
        this.content.firstElementChild = document.nextFragment;
      },
    };
  };
  const window = new FakeEventTarget();
  window.location = {
    href: 'https://photos.test/manage/events/7/photos/?visibility=hidden&page=2',
  };
  window.history = { replaceState() {} };
  window.scrollX = 0;
  window.scrollY = 0;
  window.scrollTo = () => {};
  window.CustomEvent = class {
    constructor(type, options = {}) {
      this.type = type;
      this.detail = options.detail;
    }
  };
  window.setTimeout = timers.setTimeout.bind(timers);
  window.clearTimeout = timers.clearTimeout.bind(timers);
  window.fetch = fetch;

  const root = new FakeNode({
    batchHistoryUrl: '/manage/events/7/photos/batch-history/',
    eventId: '7',
    statusUrl: '/manage/events/7/photos/status/',
  });
  root.ownerDocument = document;
  root.replaceChildren = () => assert.fail('status polling must not replace the workspace root');
  const message = root.set('[data-event-photo-status-message]', new FakeNode());
  const updated = root.set('[data-event-photo-status-updated-at]', new FakeNode());
  const changed = root.set('[data-event-photo-result-list-changed]', new FakeNode());
  changed.hidden = true;
  const total = root.set('[data-event-photo-summary-total]', new FakeNode());
  const found = root.set('[data-event-photo-filtered-count]', new FakeNode());
  const processing = new FakeNode({ eventPhotoSummaryCategory: 'processing' });
  root.setAll('[data-event-photo-summary-category]', [processing]);

  const photo = new FakeNode({ photoStatusId: 'photo-1' });
  const photoCategory = photo.set('[data-photo-status-category]', new FakeNode());
  const photoStages = photo.set('[data-photo-status-stages]', new FakeNode());
  root.setAll('[data-photo-status-id]', [photo]);

  const fragment = root.set('[data-event-photo-fragment]', new FakeNode({
    canonicalQuery: 'visibility=hidden',
    canonicalUrl: '/manage/events/7/photos/?visibility=hidden&page=2',
    filterValid: 'true',
  }));

  const batch = new FakeNode({ batchStatusId: 'batch-1' });
  const batchState = batch.set('[data-batch-status-state]', new FakeNode());
  const batchProgress = batch.set('[data-batch-status-progress]', new FakeNode());
  const batchProcessing = batch.set('[data-batch-status-processing]', new FakeNode());
  root.setAll('[data-batch-status-id]', [batch]);
  let historyFragment = new FakeNode({ batchPage: '1' });
  historyFragment.replaceWith = (replacement) => {
    historyFragment = replacement;
    root.set('[data-batch-history-fragment]', replacement);
  };
  root.set('[data-batch-history-fragment]', historyFragment);
  const resumeInput = new FakeNode();
  root.set('#resume-upload-files', resumeInput);
  const adminRoot = root.set('[data-event-photo-admin]', new FakeNode());
  root.set('[data-event-photo-management-root]', adminRoot);
  const uploadRoot = root.set('[data-event-photo-upload]', new FakeNode());
  const uploadQueue = root.set('[data-upload-queue]', new FakeNode());
  uploadRoot.uploadCoordinator = { id: 'upload-coordinator' };
  const privateNodes = [adminRoot, uploadRoot];
  root.setAll('[data-event-photo-private-status]', privateNodes);

  const controller = bindEventPhotoStatus(root, { document, window });
  return {
    batch,
    batchProcessing,
    batchProgress,
    batchState,
    adminRoot,
    changed,
    controller,
    document,
    fragment,
    found,
    message,
    photoCategory,
    photoStages,
    privateNodes,
    processing,
    resumeInput,
    root,
    timers,
    total,
    uploadQueue,
    uploadRoot,
    updated,
    window,
  };
}

test('completed confirmation retires resume while an unresolved batch stays resumable', async () => {
  const view = harness(async () => statusResponse(payload({ active: false })));
  const resume = view.batch.set('[data-resume-batch]', new FakeNode());
  view.batch.dataset.unfinishedUpload = '';
  await settle();

  assert.equal(view.batch.dataset.unfinishedUpload, '');
  assert.equal(resume.removed, undefined);

  view.controller.renderBatches([{
    id: 'batch-1',
    confirmed_count: 1,
    expected_count: 1,
    failed_count: 0,
    unresolved_count: 0,
    can_close: true,
    processing: { categories: { failed: 1 } },
  }]);

  assert.equal(view.batch.dataset.unfinishedUpload, undefined);
  assert.equal(resume.removed, true);
  assert.equal(view.batchState.textContent, 'Все фотографии загружены. Можно закрыть страницу');
  assert.match(view.batchProcessing.textContent, /Ошибки: 1/);
});

test('joined history pagination uses the latest canonical photo scope and only changes batch page', async () => {
  const view = harness(async () => statusResponse(payload({ active: false })));
  await settle();
  const link = {
    dataset: { batchHistoryPage: '3' },
    href: '?batch_id=transport-batch&batch_page=3',
  };
  const target = {
    closest(selector) {
      return selector === '[data-batch-history-page]' ? link : null;
    },
  };

  view.fragment.dataset.canonicalUrl =
    '/manage/events/7/photos/?visibility=hidden&folder=finish&page=2';
  await view.root.dispatch('click', { target });
  let destination = new URL(link.href, view.window.location.href);
  assert.equal(destination.searchParams.get('visibility'), 'hidden');
  assert.equal(destination.searchParams.get('folder'), 'finish');
  assert.equal(destination.searchParams.get('page'), '2');
  assert.equal(destination.searchParams.get('batch_page'), '3');
  assert.equal(destination.searchParams.has('batch_id'), false);

  view.fragment.dataset.canonicalUrl =
    '/manage/events/7/photos/?visibility=visible&uploader=17&page=4';
  link.dataset.batchHistoryPage = '2';
  await view.root.dispatch('click', { target });
  destination = new URL(link.href, view.window.location.href);
  assert.equal(destination.searchParams.get('visibility'), 'visible');
  assert.equal(destination.searchParams.get('uploader'), '17');
  assert.equal(destination.searchParams.get('page'), '4');
  assert.equal(destination.searchParams.get('batch_page'), '2');
  assert.equal(destination.searchParams.has('batch_id'), false);
});

test('bound controller sends displayed IDs, updates status nodes in place, and waits five seconds after completion', async () => {
  const request = deferred();
  const calls = [];
  const view = harness((url, options) => {
    calls.push({ options, url });
    return request.promise;
  });

  assert.equal(calls.length, 1);
  assert.equal(view.timers.pending().length, 0);
  view.controller.refresh();
  assert.equal(calls.length, 1, 'an in-flight request is never overlapped');

  request.resolve(statusResponse(payload()));
  await settle();

  const requestUrl = new URL(calls[0].url);
  assert.equal(requestUrl.searchParams.get('visibility'), 'hidden');
  assert.equal(requestUrl.searchParams.get('page'), '2');
  assert.deepEqual(requestUrl.searchParams.getAll('photo_id'), ['photo-1']);
  assert.deepEqual(requestUrl.searchParams.getAll('batch_id'), ['batch-1']);
  assert.equal(calls[0].options.credentials, 'same-origin');
  assert.equal(view.total.textContent, '2');
  assert.equal(view.found.textContent, '2');
  assert.equal(view.processing.textContent, '1');
  assert.equal(view.photoCategory.textContent, 'Обрабатывается');
  assert.equal(view.photoStages.textContent, 'Метаданные: В очереди');
  assert.equal(view.batchState.textContent, 'Загрузка не завершена.');
  assert.equal(view.batchProgress.textContent, '1 из 2 загружено · осталось 1');
  assert.equal(
    view.batchProcessing.textContent,
    'Обработано: 0 · Обрабатывается: 1 · Ожидает обработки: 0 · Ошибки: 0',
  );
  assert.equal(view.updated.textContent, '2026-09-07T10:00:00.000Z');
  assert.equal(view.changed.hidden, true);
  assert.deepEqual(view.timers.pending().map((entry) => entry.delay), [5_000]);
});

test('communication failures preserve values, mark stale, and back off to thirty seconds before resetting', async () => {
  const outcomes = [
    () => Promise.resolve(statusResponse(payload())),
    () => Promise.reject(new Error('offline one')),
    () => Promise.reject(new Error('offline two')),
    () => Promise.reject(new Error('offline three')),
    () => Promise.resolve(statusResponse(payload())),
  ];
  const view = harness(() => outcomes.shift()());
  await settle();
  assert.equal(view.photoCategory.textContent, 'Обрабатывается');

  await view.timers.runNext();
  assert.equal(view.message.textContent, 'Не удалось обновить статус');
  assert.equal(view.root.dataset.statusStale, 'true');
  assert.equal(view.photoCategory.textContent, 'Обрабатывается');
  assert.equal(view.updated.textContent, '2026-09-07T10:00:00.000Z');
  assert.deepEqual(view.timers.pending().map((entry) => entry.delay), [10_000]);

  await view.timers.runNext();
  assert.deepEqual(view.timers.pending().map((entry) => entry.delay), [20_000]);
  await view.timers.runNext();
  assert.deepEqual(view.timers.pending().map((entry) => entry.delay), [30_000]);
  await view.timers.runNext();
  assert.equal(view.root.dataset.statusStale, 'false');
  assert.equal(view.message.textContent, '');
  assert.deepEqual(view.timers.pending().map((entry) => entry.delay), [5_000]);
});

test('terminal state stops while local activity starts and ends with immediate confirming reads', async () => {
  const bodies = [payload({ active: false }), payload({ active: false }), payload({ active: false })];
  let calls = 0;
  const view = harness(async () => {
    calls += 1;
    return statusResponse(bodies.shift());
  });
  await settle();
  assert.equal(view.timers.pending().length, 0);

  view.document.dispatch(UPLOAD_ACTIVITY_EVENT, { detail: { active: true } });
  await settle();
  assert.equal(calls, 2);
  assert.deepEqual(view.timers.pending().map((entry) => entry.delay), [5_000]);

  view.document.dispatch(UPLOAD_ACTIVITY_EVENT, { detail: { active: false } });
  await settle();
  assert.equal(calls, 3);
  assert.equal(view.timers.pending().length, 0);
});

test('coalesces in-flight wakeups into one follow-up using latest query and displayed IDs', async () => {
  const requests = [deferred(), deferred()];
  const calls = [];
  const view = harness((url) => {
    calls.push(url);
    return requests[calls.length - 1].promise;
  });

  view.document.dispatch(UPLOAD_ACTIVITY_EVENT, { detail: { active: true } });
  view.document.dispatch(UPLOAD_ACTIVITY_EVENT, { detail: { active: false } });
  view.window.location.href =
    'https://photos.test/manage/events/7/photos/?visibility=visible&page=3';
  view.fragment.dataset.canonicalQuery = 'visibility=visible';
  view.fragment.dataset.canonicalUrl = '/manage/events/7/photos/?visibility=visible&page=3';
  view.root.setAll('[data-photo-status-id]', [new FakeNode({ photoStatusId: 'photo-latest' })]);
  view.document.dispatch(MANUAL_REFRESH_EVENT);
  assert.equal(calls.length, 1, 'wakeups remain coalesced while the first request is active');

  requests[0].resolve(statusResponse(payload({ active: false })));
  await settle();

  assert.equal(calls.length, 2);
  const followUpUrl = new URL(calls[1]);
  assert.equal(followUpUrl.searchParams.get('visibility'), 'visible');
  assert.equal(followUpUrl.searchParams.get('page'), '3');
  assert.deepEqual(followUpUrl.searchParams.getAll('photo_id'), ['photo-latest']);
  requests[1].resolve(statusResponse(payload({ active: false })));
  await settle();
  assert.equal(calls.length, 2, 'all in-flight wakeups produce only one follow-up');
  assert.equal(view.timers.pending().length, 0);
});

test('hidden tabs pause timers and visibility or focus performs an immediate non-overlapping recheck', async () => {
  const requests = [deferred(), deferred(), deferred()];
  let calls = 0;
  const view = harness(() => requests[calls++].promise);
  requests[0].resolve(statusResponse(payload()));
  await settle();
  assert.equal(view.timers.pending().length, 1);

  view.document.visibilityState = 'hidden';
  view.document.dispatch('visibilitychange');
  assert.equal(view.timers.pending().length, 0);

  view.document.visibilityState = 'visible';
  view.document.dispatch('visibilitychange');
  view.window.dispatch('focus');
  assert.equal(calls, 2, 'visibility and focus cannot overlap the same request');
  requests[1].resolve(statusResponse(payload({ active: false })));
  await settle();
  assert.equal(calls, 3, 'focus received in flight becomes one follow-up request');
  assert.equal(view.timers.pending().length, 0);

  requests[2].resolve(statusResponse(payload({ active: false })));
  await settle();
  assert.equal(calls, 3);
  assert.equal(view.timers.pending().length, 0);
});

test('lost access hides private status nodes and permanently stops the scheduler', async () => {
  let calls = 0;
  const view = harness(async () => {
    calls += 1;
    return statusResponse({}, 403);
  });
  await settle();

  assert.equal(view.message.textContent, 'Доступ к статусам потерян');
  assert.equal(view.root.dataset.statusAccessLost, 'true');
  assert.ok(view.privateNodes.every((node) => node.hidden));
  assert.equal(view.timers.pending().length, 0);
  view.window.dispatch('focus');
  view.document.dispatch(UPLOAD_ACTIVITY_EVENT, { detail: { active: true } });
  await settle();
  assert.equal(calls, 1);
});

test('result changes show a refresh indicator without moving or replacing cards', async () => {
  const view = harness(async () => statusResponse(payload({ changed: true, active: false })));
  await settle();

  assert.equal(view.changed.hidden, false);
  assert.equal(view.changed.textContent, 'Список фотографий изменился. Обновите его.');
  assert.equal(view.photoCategory.textContent, 'Ошибка');
  assert.equal(view.timers.pending().length, 0);
});

test('validated fragment query is authoritative and invalid filter UI requests summary only', async () => {
  const calls = [];
  const view = harness(async (url) => {
    calls.push(url);
    return statusResponse(payload({ active: false }));
  });
  view.window.location.href =
    'https://photos.test/manage/events/7/photos/?folder=invalid&page=99&batch_page=3';
  await settle();

  const initial = new URL(calls[0]);
  assert.equal(initial.searchParams.get('visibility'), 'hidden');
  assert.equal(initial.searchParams.get('page'), '2');
  assert.equal(initial.searchParams.has('folder'), false);
  assert.equal(initial.searchParams.has('batch_page'), false);

  view.fragment.dataset.filterValid = 'false';
  view.fragment.dataset.canonicalQuery = '';
  view.fragment.dataset.canonicalUrl = '/manage/events/7/photos/';
  view.document.dispatch(MANUAL_REFRESH_EVENT);
  await settle();

  const invalid = new URL(calls[1]);
  assert.equal(invalid.searchParams.get('include_results'), '0');
  assert.equal(invalid.searchParams.has('folder'), false);
  assert.equal(invalid.searchParams.has('page'), false);
});

test('a rejected status query stops retries until a committed fragment refresh', async () => {
  const responses = [statusResponse({}, 400), statusResponse(payload({ active: false }))];
  let calls = 0;
  const view = harness(async () => responses[calls++]);
  await settle();

  assert.equal(calls, 1);
  assert.equal(view.timers.pending().length, 0);
  view.window.dispatch('focus');
  await settle();
  assert.equal(calls, 1, 'focus does not repeat a rejected query');

  view.document.dispatch(MANUAL_REFRESH_EVENT);
  await settle();
  assert.equal(calls, 2);
});

test('partial capability changes hide only the lost role and keep the other role live', async () => {
  const responses = [
    statusResponse({
      server_timestamp: '2026-09-07T10:00:00Z',
      has_active_work: false,
      capabilities: { can_inspect: true, can_upload: false },
      admin: { summary: { total: 0, categories: {} } },
    }),
    statusResponse({
      server_timestamp: '2026-09-07T10:00:05Z',
      has_active_work: false,
      capabilities: { can_inspect: false, can_upload: true },
      batches: [],
    }),
  ];
  const view = harness(async () => responses.shift());
  await settle();

  assert.equal(view.adminRoot.hidden, false);
  assert.equal(view.uploadRoot.hidden, true);
  view.document.dispatch(MANUAL_REFRESH_EVENT);
  await settle();
  assert.equal(view.adminRoot.hidden, true);
  assert.equal(view.uploadRoot.hidden, false);
  assert.equal(view.root.dataset.statusAccessLost, undefined);
});

test('status count event refreshes the Task 4 live scope without touching explicit selection itself', async () => {
  const view = harness(async () => statusResponse(payload({ active: false, changed: true })));
  await settle();

  const event = view.document.events.find((item) => item.type === 'findme:event-photo-filter-count');
  assert.ok(event);
  assert.deepEqual(event.detail, { count: 1 });
});

test('new local batch identity installs bounded history without replacing queue or resume input', async () => {
  const calls = [];
  const view = harness(async (url) => {
    calls.push(String(url));
    if (new URL(url).pathname.endsWith('/batch-history/')) {
      return statusResponse({}, 200, '<div data-batch-history-fragment></div>');
    }
    return statusResponse(payload({ active: false }));
  });
  await settle();
  const resumeInput = view.resumeInput;
  const uploadRoot = view.uploadRoot;
  const uploadQueue = view.uploadQueue;
  const batch = new FakeNode({ batchStatusId: 'batch-2' });
  batch.set('[data-batch-status-state]', new FakeNode());
  batch.set('[data-batch-status-progress]', new FakeNode());
  batch.set('[data-batch-status-processing]', new FakeNode());
  const replacement = new FakeNode({ batchPage: '1' });
  view.document.nextFragment = replacement;
  view.root.querySelector('[data-batch-history-fragment]').replaceWith = (node) => {
    view.root.set('[data-batch-history-fragment]', node);
    view.root.setAll('[data-batch-status-id]', [batch]);
  };

  view.document.dispatch(UPLOAD_ACTIVITY_EVENT, {
    detail: { active: true, batchId: 'batch-2' },
  });
  await settle();

  const historyCall = calls.find((url) => new URL(url).pathname.endsWith('/batch-history/'));
  assert.ok(historyCall);
  assert.equal(new URL(historyCall).searchParams.get('batch_id'), 'batch-2');
  assert.equal(view.root.querySelector('#resume-upload-files'), resumeInput);
  assert.equal(view.root.querySelector('[data-event-photo-upload]'), uploadRoot);
  assert.equal(view.root.querySelector('[data-upload-queue]'), uploadQueue);
  assert.ok(calls.some((url) => new URL(url).searchParams.getAll('batch_id').includes('batch-2')));
});

test('a transient history failure retains the new batch and retries on the status scheduler', async () => {
  let historyAttempts = 0;
  const view = harness(async (url) => {
    if (new URL(url).pathname.endsWith('/batch-history/')) {
      historyAttempts += 1;
      if (historyAttempts === 1) throw new Error('temporary history failure');
      return statusResponse({}, 200, '<div data-batch-history-fragment></div>');
    }
    return statusResponse(payload({ active: false }));
  });
  await settle();
  const batch = new FakeNode({ batchStatusId: 'batch-retry' });
  batch.set('[data-batch-status-state]', new FakeNode());
  batch.set('[data-batch-status-progress]', new FakeNode());
  batch.set('[data-batch-status-processing]', new FakeNode());
  view.document.nextFragment = new FakeNode({ batchPage: '1' });
  view.root.querySelector('[data-batch-history-fragment]').replaceWith = (node) => {
    view.root.set('[data-batch-history-fragment]', node);
    view.root.setAll('[data-batch-status-id]', [batch]);
  };

  view.document.dispatch(UPLOAD_ACTIVITY_EVENT, {
    detail: { active: false, batchId: 'batch-retry' },
  });
  const failedHistoryRequest = view.controller.historyInFlight;
  await assert.doesNotReject(failedHistoryRequest);
  await settle();

  assert.equal(view.controller.pendingHistoryBatchId, 'batch-retry');
  assert.equal(historyAttempts, 1);
  assert.deepEqual(view.timers.pending().map((entry) => entry.delay), [5_000]);
  await view.timers.runNext();

  assert.equal(historyAttempts, 2);
  assert.equal(view.controller.pendingHistoryBatchId, null);
  assert.equal(view.controller.hasBatch('batch-retry'), true);
});

test('import progress for this event wakes zero-photo status without local upload ownership', async () => {
  let calls = 0;
  const view = harness(async () => {
    calls += 1;
    return statusResponse(payload({ active: false }));
  });
  await settle();
  assert.equal(calls, 1);

  view.document.dispatch(IMPORT_PROGRESS_EVENT, { detail: { eventId: 8 } });
  await settle();
  assert.equal(calls, 1);
  view.document.dispatch(IMPORT_PROGRESS_EVENT, { detail: { eventId: 7 } });
  await settle();
  assert.equal(calls, 2);
  assert.equal(view.controller.localUploadActive, false);
});

test('user list refresh delegates to Task 4 and restores scroll outside the replaced fragment', async () => {
  const view = harness(async () => statusResponse(payload({ active: false, changed: true })));
  const calls = [];
  view.adminRoot.eventPhotoManagementController = {
    async refreshFromManagementUrl(...args) { calls.push(args); },
  };
  view.window.scrollX = 11;
  view.window.scrollY = 420;
  const scrolls = [];
  view.window.scrollTo = (...args) => scrolls.push(args);
  view.document.activeElement = view.uploadQueue;
  await settle();
  const button = {
    closest(selector) {
      return selector === '[data-event-photo-result-list-refresh]' ? this : null;
    },
  };

  await view.root.dispatch('click', { target: button, preventDefault() {} });

  assert.deepEqual(calls, [[view.fragment.dataset.canonicalUrl, 'replace', true]]);
  assert.deepEqual(scrolls, [[11, 420]]);
  assert.equal(view.document.activeElement, view.uploadQueue);
});

test('user list refresh preserves an unapplied filter draft instead of replacing the fragment', async () => {
  const view = harness(async () => statusResponse(payload({ active: false, changed: true })));
  const calls = [];
  view.adminRoot.eventPhotoManagementController = {
    filtersDirty: true,
    async refreshFromManagementUrl(...args) { calls.push(args); },
  };
  await settle();
  const button = {
    closest(selector) {
      return selector === '[data-event-photo-result-list-refresh]' ? this : null;
    },
  };

  await view.root.dispatch('click', { target: button, preventDefault() {} });

  assert.deepEqual(calls, []);
  assert.equal(
    view.message.textContent,
    'Примените или сбросьте изменения фильтров перед обновлением списка.',
  );
});

test('an old status response cannot update the replacement canonical result scope', async () => {
  const requests = [deferred(), deferred()];
  let calls = 0;
  const view = harness(() => requests[calls++].promise);
  view.fragment.dataset.canonicalQuery = 'visibility=visible';
  view.fragment.dataset.canonicalUrl = '/manage/events/7/photos/?visibility=visible&page=1';
  view.root.setAll('[data-photo-status-id]', [new FakeNode({ photoStatusId: 'photo-latest' })]);
  view.document.dispatch(MANUAL_REFRESH_EVENT);

  requests[0].resolve(statusResponse(payload({ active: false, changed: true })));
  await settle();

  assert.equal(calls, 2);
  assert.equal(view.total.textContent, '2', 'event-wide summary can still update');
  assert.equal(view.found.textContent, '', 'old filtered count is ignored');
  assert.equal(view.photoCategory.textContent, '', 'old displayed photo state is ignored');

  const latest = payload({ active: false });
  latest.admin.filtered_result_count = 9;
  requests[1].resolve(statusResponse(latest));
  await settle();
  assert.equal(view.found.textContent, '9');
});

test('controller rejects an invalid mount contract instead of creating an unused scheduler', () => {
  assert.throws(
    () => new EventPhotoStatusController(new FakeNode(), {}),
    /status URL/i,
  );
});
