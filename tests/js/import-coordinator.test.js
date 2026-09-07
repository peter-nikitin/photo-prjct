'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const {
  ImportCoordinator,
  bindImportPage,
  importPresentation,
  itemPresentation,
  updateCard,
} = require('../../src/backend/static/ui/import-coordinator.js');

function response(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function batch(status = 'queued', counts = {}) {
  return {
    contract_version: 1,
    id: 'import-1',
    status,
    event: { id: 7, name: 'Забег' },
    folder: { id: 11, name: 'Финиш' },
    created_at: '2026-09-07T08:00:00+00:00',
    completed_at: null,
    counts: {
      jpeg: 0,
      directory: 0,
      unsupported: 0,
      imported: 0,
      duplicate: 0,
      error: 0,
      pending: 0,
      ...counts,
    },
    error_code: '',
    processing_active: false,
  };
}

class FakeNode {
  constructor({ value = '', dataset = {} } = {}) {
    this.value = value;
    this.dataset = dataset;
    this.hidden = false;
    this.disabled = false;
    this.textContent = '';
    this.listeners = new Map();
    this.nodes = new Map();
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  async dispatch(type, event = {}) {
    for (const listener of this.listeners.get(type) || []) {
      await listener({ preventDefault() {}, target: this, ...event });
    }
  }

  querySelector(selector) {
    return this.nodes.get(selector) || null;
  }

  querySelectorAll() {
    return [];
  }

  replaceChildren() {}

  replaceWith(node) {
    this.replacement = node;
  }

  focus() {
    global.document.activeElement = this;
  }
}

function folderOption(select, eventId, value) {
  const option = new FakeNode({ value, dataset: { eventId } });
  Object.defineProperty(option, 'selected', {
    get() { return select.value === value; },
    set(selected) { if (selected) select.value = value; },
  });
  return option;
}

function fakePagination(page = 2) {
  const nav = new FakeNode({ dataset: { page: String(page) } });
  nav.nodes.set('[data-import-items-page-status]', new FakeNode());
  nav.nodes.set('[data-import-items-previous]', new FakeNode());
  nav.nodes.set('[data-import-items-next]', new FakeNode());
  return nav;
}

function fakeCard(record = batch('transferring', { jpeg: 4, pending: 4 })) {
  const card = new FakeNode({ dataset: { importId: record.id } });
  for (const selector of [
    '[data-import-event]',
    '[data-import-folder-name]',
    '[data-import-created]',
    '[data-import-status]',
    '[data-import-accepted]',
    '[data-import-message]',
    '[data-import-subfolder-warning]',
    '[data-import-error-message]',
    '[data-import-retry]',
    '[data-import-action-status]',
    '[data-import-item-list]',
  ]) card.nodes.set(selector, new FakeNode());
  for (const key of ['jpeg', 'imported', 'duplicate', 'error', 'pending', 'unsupported']) {
    card.nodes.set(`[data-import-${key}]`, new FakeNode());
  }
  const items = new FakeNode();
  items.hidden = false;
  card.nodes.set('[data-import-items]', items);
  card.nodes.set('[data-import-items-pagination]', fakePagination());
  return card;
}

function flushPromises() {
  return new Promise((resolve) => setImmediate(resolve));
}

test('submission keeps the chosen event and folder and retries a lost create with one key', async () => {
  const calls = [];
  let attempt = 0;
  const coordinator = new ImportCoordinator({
    fetch: async (url, options) => {
      if (!options.body) {
        return response(200, { contract_version: 1, imports: [], pagination: { page: 1, page_size: 20, total: 0, pages: 0 } });
      }
      calls.push({ url, body: JSON.parse(options.body) });
      attempt += 1;
      if (attempt === 1) throw new TypeError('connection lost');
      return response(200, batch());
    },
    urls: { collection: '/photographer/uploads/imports/' },
    csrfToken: 'csrf',
    randomUUID: () => 'submission-1',
    render() {},
  });

  const selection = { eventId: '7', folderId: '11', sourceUrl: 'https://disk.yandex.ru/d/key' };
  await assert.rejects(coordinator.submit(selection));
  await coordinator.submit(selection);

  assert.equal(calls.length, 2);
  assert.deepEqual(calls[0].body, calls[1].body);
  assert.deepEqual(calls[1].body, {
    contract_version: 1,
    event_id: 7,
    folder_id: 11,
    source_url: 'https://disk.yandex.ru/d/key',
    submission_key: 'submission-1',
  });
});

test('definitive rejection permits a corrected link and destination with a new key', async () => {
  const bodies = [];
  let sequence = 0;
  const coordinator = new ImportCoordinator({
    fetch: async (_url, options) => {
      if (!options.body) {
        return response(200, { contract_version: 1, imports: [], pagination: { page: 1, page_size: 20, total: 0, pages: 0 } });
      }
      bodies.push(JSON.parse(options.body));
      if (bodies.length === 1) {
        return response(400, { contract_version: 1, error: { code: 'source_not_folder' } });
      }
      return response(201, batch());
    },
    urls: { collection: '/imports/' },
    csrfToken: 'csrf',
    randomUUID: () => `submission-${++sequence}`,
    render() {},
  });

  await assert.rejects(coordinator.submit({
    eventId: '7', folderId: '11', sourceUrl: 'https://disk.yandex.ru/d/invalid',
  }));
  await coordinator.submit({
    eventId: '8', folderId: '22', sourceUrl: 'https://disk.yandex.ru/d/corrected',
  });

  assert.deepEqual(bodies.map(({ event_id, folder_id, source_url, submission_key }) => ({
    event_id, folder_id, source_url, submission_key,
  })), [
    { event_id: 7, folder_id: 11, source_url: 'https://disk.yandex.ru/d/invalid', submission_key: 'submission-1' },
    { event_id: 8, folder_id: 22, source_url: 'https://disk.yandex.ru/d/corrected', submission_key: 'submission-2' },
  ]);
});

test('changed visible selection after uncertainty never silently replays the old destination', async () => {
  const bodies = [];
  let sequence = 0;
  const coordinator = new ImportCoordinator({
    fetch: async (_url, options) => {
      if (!options.body) {
        return response(200, { contract_version: 1, imports: [], pagination: { page: 1, page_size: 20, total: 0, pages: 0 } });
      }
      bodies.push(JSON.parse(options.body));
      if (bodies.length === 1) throw new TypeError('connection lost');
      return response(201, batch());
    },
    urls: { collection: '/imports/' },
    csrfToken: 'csrf',
    randomUUID: () => `submission-${++sequence}`,
    render() {},
  });

  await assert.rejects(coordinator.submit({
    eventId: '7', folderId: '11', sourceUrl: 'https://disk.yandex.ru/d/uncertain',
  }));
  await coordinator.submit({
    eventId: '8', folderId: '22', sourceUrl: 'https://disk.yandex.ru/d/new-intent',
  });

  assert.equal(bodies[1].event_id, 8);
  assert.equal(bodies[1].folder_id, 22);
  assert.equal(bodies[1].source_url, 'https://disk.yandex.ru/d/new-intent');
  assert.equal(bodies[1].submission_key, 'submission-2');
});

test('accepted create remains accepted when the optional history refresh fails', async () => {
  const changes = [];
  let posts = 0;
  const coordinator = new ImportCoordinator({
    fetch: async (_url, options = {}) => {
      if (options.method === 'POST') {
        posts += 1;
        return response(201, batch());
      }
      throw new TypeError('history unavailable');
    },
    urls: { collection: '/imports/' },
    csrfToken: 'csrf',
    randomUUID: () => 'submission-1',
    render(change) { changes.push(change.type); },
  });

  const created = await coordinator.submit({
    eventId: '7', folderId: '11', sourceUrl: 'https://disk.yandex.ru/d/accepted',
  });

  assert.equal(created.id, 'import-1');
  assert.equal(posts, 1);
  assert.deepEqual(changes, ['created', 'history-error']);
  assert.equal(coordinator.pendingSubmission, null);
});

test('changing the event starts a new submission and supports Без папки', async () => {
  const bodies = [];
  let sequence = 0;
  const coordinator = new ImportCoordinator({
    fetch: async (_url, options) => {
      if (!options.body) {
        return response(200, { contract_version: 1, imports: [], pagination: { page: 1, page_size: 20, total: 0, pages: 0 } });
      }
      bodies.push(JSON.parse(options.body));
      return response(201, batch());
    },
    urls: { collection: '/imports/' },
    csrfToken: 'csrf',
    randomUUID: () => `submission-${++sequence}`,
    render() {},
  });

  await coordinator.submit({ eventId: '7', folderId: '11', sourceUrl: 'https://disk.yandex.ru/d/a' });
  await coordinator.submit({ eventId: '8', folderId: '', sourceUrl: 'https://disk.yandex.ru/d/b' });

  assert.equal(bodies[0].submission_key, 'submission-1');
  assert.equal(bodies[1].submission_key, 'submission-2');
  assert.equal(bodies[1].event_id, 8);
  assert.equal(bodies[1].folder_id, null);
});

test('fixed event and chosen folder survive pasted link without an event selector', async () => {
  const bodies = [];
  const eventSelect = new FakeNode();
  const form = new FakeNode();
  const folderSelect = new FakeNode();
  const source = new FakeNode();
  const submit = new FakeNode();
  const list = new FakeNode();
  const listStatus = new FakeNode();
  const listPagination = new FakeNode();
  const listPageStatus = new FakeNode();
  const listPrevious = new FakeNode();
  const listNext = new FakeNode();
  listPagination.nodes.set('[data-import-list-page-status]', listPageStatus);
  listPagination.nodes.set('[data-import-list-previous]', listPrevious);
  listPagination.nodes.set('[data-import-list-next]', listNext);

  const withoutFolder = folderOption(folderSelect, '7', '');
  const start = folderOption(folderSelect, '7', '11');
  const anotherEvent = folderOption(folderSelect, '8', '22');
  folderSelect.querySelectorAll = () => [withoutFolder, start, anotherEvent];

  const root = new FakeNode({
    dataset: {
      eventId: '7',
      importHistoryEnabled: 'true',
      importEnabled: 'true',
      importCollectionUrl: '/imports/',
      importDetailUrlTemplate: '/imports/{batch}/',
      importItemsUrlTemplate: '/imports/{batch}/items/',
      importRetryUrlTemplate: '/imports/{batch}/retry/',
      csrfToken: 'csrf',
    },
  });
  root.nodes.set('[data-import-list]', list);
  root.nodes.set('[data-import-list-status]', listStatus);
  root.nodes.set('[data-import-list-pagination]', listPagination);
  root.nodes.set('[data-import-list-previous]', listPrevious);
  root.nodes.set('[data-import-list-next]', listNext);
  root.nodes.set('[data-import-form]', form);
  root.nodes.set('[data-import-folder]', folderSelect);
  root.nodes.set('[data-import-source-url]', source);
  root.nodes.set('[data-import-submit]', submit);

  const previousDocument = global.document;
  global.document = { querySelector: () => eventSelect };
  try {
    bindImportPage(root, {
      fetch: async (_url, options = {}) => {
        if (options.method === 'POST') {
          bodies.push(JSON.parse(options.body));
          return response(201, batch());
        }
        return response(200, {
          contract_version: 1,
          imports: [],
          pagination: { page: 1, page_size: 20, total: 0, pages: 0 },
        });
      },
      crypto: { randomUUID: () => 'submission-1' },
      addEventListener() {},
      setTimeout() {},
      clearTimeout() {},
    });

    start.selected = true;
    source.value = 'https://disk.yandex.ru/d/key';
    await source.dispatch('input');
    await form.dispatch('submit');
  } finally {
    global.document = previousDocument;
  }

  assert.equal(bodies.length, 1);
  assert.equal(bodies[0].event_id, 7);
  assert.equal(bodies[0].folder_id, 11);
});

test('progress update preserves an expanded item page and focused action control', () => {
  const previousDocument = global.document;
  global.document = { activeElement: null };
  try {
    const card = fakeCard();
    const items = card.querySelector('[data-import-items]');
    const pagination = card.querySelector('[data-import-items-pagination]');
    const focused = pagination.querySelector('[data-import-items-next]');
    focused.focus();

    updateCard(card, batch('transferring', { jpeg: 8, imported: 3, pending: 5 }));

    assert.equal(card.querySelector('[data-import-items]'), items);
    assert.equal(items.hidden, false);
    assert.equal(pagination.dataset.page, '2');
    assert.equal(global.document.activeElement, focused);
    assert.equal(card.querySelector('[data-import-imported]').textContent, '3');
  } finally {
    global.document = previousDocument;
  }
});

test('cancelled departure preserves import polling; gate-off errors recover and pagehide stops it', async () => {
  const card = fakeCard();
  const actionStatus = card.querySelector('[data-import-action-status]');
  const list = new FakeNode();
  list.nodes.set('[data-import-id="import-1"]', card);
  const listStatus = new FakeNode();
  const listPagination = new FakeNode();
  listPagination.nodes.set('[data-import-list-page-status]', new FakeNode());
  const listPrevious = new FakeNode();
  const listNext = new FakeNode();
  listPagination.nodes.set('[data-import-list-previous]', listPrevious);
  listPagination.nodes.set('[data-import-list-next]', listNext);
  const root = new FakeNode({
    dataset: {
      eventId: '7',
      importHistoryEnabled: 'true',
      importEnabled: 'false',
      importCollectionUrl: '/imports/',
      importDetailUrlTemplate: '/imports/{batch}/',
      importItemsUrlTemplate: '/imports/{batch}/items/',
      importRetryUrlTemplate: '/imports/{batch}/retry/',
      csrfToken: 'csrf',
    },
  });
  root.nodes.set('[data-import-list]', list);
  root.nodes.set('[data-import-list-status]', listStatus);
  root.nodes.set('[data-import-list-pagination]', listPagination);
  root.nodes.set('[data-import-list-previous]', listPrevious);
  root.nodes.set('[data-import-list-next]', listNext);
  root.nodes.set('[data-import-item-template]', new FakeNode());

  const workspaceEvents = [];
  const lifecycle = new Map();
  root.ownerDocument = { dispatchEvent(event) { workspaceEvents.push(event.type); } };
  let retrySucceeds = false;
  let itemsSucceed = false;
  let detailSucceeds = false;
  const environment = {
    fetch: async (url, options = {}) => {
      if (url.includes('/retry/')) {
        if (retrySucceeds) return response(200, { contract_version: 1, batch: batch() });
        return response(503, { contract_version: 1, error: { code: 'feature_paused' } });
      }
      if (url.includes('/items/')) {
        if (!itemsSucceed) throw new TypeError('private file service detail');
        return response(200, {
          contract_version: 1,
          items: [],
          pagination: { page: 2, page_size: 20, total: 21, pages: 2 },
        });
      }
      if (url === '/imports/import-1/') {
        if (!detailSucceeds) throw new TypeError('private poll detail');
        return response(200, batch('completed'));
      }
      if (!options.method) {
        return response(200, {
          contract_version: 1,
          imports: [],
          pagination: { page: 1, page_size: 20, total: 0, pages: 0 },
        });
      }
      throw new Error('unexpected request');
    },
    crypto: { randomUUID: () => 'submission-1' },
    addEventListener(type, listener) { lifecycle.set(type, listener); },
    CustomEvent: class { constructor(type) { this.type = type; } },
    setTimeout() { return 1; },
    clearTimeout() {},
  };
  const previousDocument = global.document;
  global.document = { querySelector: () => new FakeNode(), activeElement: null };
  try {
    const coordinator = bindImportPage(root, environment);
    await flushPromises();

    const retryTarget = { closest: (selector) => (
      selector === '[data-import-card]' ? card : selector === '[data-import-retry]' ? retryTarget : null
    ) };
    await root.dispatch('click', { target: retryTarget });
    await flushPromises();
    assert.equal(actionStatus.textContent, 'Импорт временно приостановлен.');
    assert.equal(actionStatus.hidden, false);

    const nextTarget = { closest: (selector) => (
      selector === '[data-import-card]' ? card : selector === '[data-import-items-next]' ? nextTarget : null
    ) };
    await root.dispatch('click', { target: nextTarget });
    await flushPromises();
    assert.equal(actionStatus.textContent, 'Не удалось загрузить список файлов. Попробуйте ещё раз.');
    assert.doesNotMatch(actionStatus.textContent, /private/);

    itemsSucceed = true;
    await root.dispatch('click', { target: nextTarget });
    await flushPromises();
    assert.equal(actionStatus.textContent, '');
    assert.equal(actionStatus.hidden, true);

    coordinator.activeIds.add('import-1');
    await coordinator.poll();
    assert.equal(actionStatus.textContent, 'Не удалось обновить прогресс. Попробуйте ещё раз.');
    assert.doesNotMatch(actionStatus.textContent, /private/);

    detailSucceeds = true;
    workspaceEvents.length = 0;
    coordinator.activeIds.add('import-1');
    await coordinator.poll();
    assert.deepEqual(workspaceEvents, ['findme:event-photo-import-progress']);
    assert.equal(lifecycle.has('beforeunload'), false);
    assert.equal(coordinator.stopped, false);
    assert.equal(actionStatus.textContent, '');
    assert.equal(actionStatus.hidden, true);

    assert.equal(typeof lifecycle.get('pagehide'), 'function');
    retrySucceeds = true;
    await root.dispatch('click', { target: retryTarget });
    await flushPromises();
    assert.equal(actionStatus.textContent, '');
    assert.equal(actionStatus.hidden, true);
    lifecycle.get('pagehide')();
    assert.equal(coordinator.stopped, true);
  } finally {
    global.document = previousDocument;
  }
});

test('presentation uses exact discovery warning and distinguishes durable outcomes', () => {
  assert.equal(
    importPresentation(batch('transferring', { jpeg: 8, directory: 2, unsupported: 3 })).warning,
    'В папке по ссылке есть вложенные папки. Фотографии из них загружены не будут.',
  );
  assert.equal(importPresentation(batch('completed')).message, 'В указанной папке нет JPEG-файлов для загрузки');
  assert.equal(
    importPresentation(batch('completed', { jpeg: 4, duplicate: 4 })).message,
    'Новых фотографий нет',
  );
  const processing = batch('completed', { jpeg: 4, imported: 4 });
  processing.processing_active = true;
  assert.equal(
    importPresentation(processing).message,
    'Загрузка завершена. Обработка фотографий продолжается',
  );
  assert.equal(importPresentation(batch('completed', { jpeg: 4, imported: 4 })).message, 'Фотографии переданы в стандартную обработку.');
  assert.equal(importPresentation(batch('paused')).label, 'Приостановлено');
  assert.equal(importPresentation(batch('partial')).label, 'Завершено с ошибками');
  assert.equal(importPresentation(batch('failed')).label, 'Не удалось загрузить');
});

test('item errors are rendered from allowlisted codes and never from server exception text', () => {
  assert.equal(itemPresentation({ status: 'error', error_code: 'file_too_large' }).error, 'Файл больше 50 МБ.');
  assert.equal(itemPresentation({ status: 'error', error_code: 'private traceback' }).error, 'Не удалось загрузить файл.');
});

test('list and item pagination stay bounded and polling continues only while import or processing is active', async () => {
  const urls = [];
  const scheduled = [];
  const coordinator = new ImportCoordinator({
    fetch: async (url) => {
      urls.push(url);
      if (url.includes('/items/')) {
        return response(200, { contract_version: 1, items: [], pagination: { page: 2, page_size: 20, total: 10000, pages: 500 } });
      }
      if (url === '/imports/import-1/') return response(200, batch('completed', { imported: 1, jpeg: 1 }));
      return response(200, { contract_version: 1, imports: [batch('transferring', { pending: 1, jpeg: 1 })], pagination: { page: 1, page_size: 20, total: 10000, pages: 500 } });
    },
    urls: {
      collection: '/imports/',
      detail: '/imports/{batch}/',
      items: '/imports/{batch}/items/',
    },
    csrfToken: 'csrf',
    randomUUID: () => 'submission',
    render() {},
    schedule(callback, delay) { scheduled.push({ callback, delay }); return scheduled.length; },
    cancel() {},
  });

  await coordinator.loadPage(1);
  await coordinator.loadItems('import-1', 2);
  assert.equal(urls[0], '/imports/?page=1&page_size=20');
  assert.equal(urls[1], '/imports/import-1/items/?page=2&page_size=20');
  assert.equal(scheduled.length, 1);
  assert.equal(scheduled[0].delay, 4000);

  await coordinator.poll();
  assert.equal(scheduled.length, 1);
  assert.equal(coordinator.activeIds.size, 0);
  coordinator.stop();
  assert.equal(coordinator.stopped, true);
});

test('a terminal import remains polled while server processing is active', async () => {
  const scheduled = [];
  const processing = batch('completed', { imported: 1, jpeg: 1 });
  processing.processing_active = true;
  const coordinator = new ImportCoordinator({
    fetch: async () => response(200, {
      contract_version: 1,
      imports: [processing],
      pagination: { page: 1, page_size: 20, total: 1, pages: 1 },
    }),
    urls: { collection: '/imports/' },
    csrfToken: 'csrf',
    randomUUID: () => 'submission',
    render() {},
    schedule(callback, delay) { scheduled.push({ callback, delay }); return scheduled.length; },
    cancel() {},
  });

  await coordinator.loadPage(1);

  assert.deepEqual([...coordinator.activeIds], ['import-1']);
  assert.equal(scheduled.length, 1);
});


test('fixed event history keeps its scope on every paginated request', async () => {
  const requested = [];
  const coordinator = new ImportCoordinator({
    eventId: 42, urls: { collection: '/imports/' }, render() {},
    fetch: async (url) => {
      requested.push(new URL(url, 'https://app.example'));
      return response(200, { contract_version: 1, imports: [], pagination: { page: 1, page_size: 20, total: 0, pages: 0 } });
    },
  });
  await coordinator.loadPage(1);
  await coordinator.loadPage(2);
  assert.deepEqual(requested.map((url) => url.searchParams.get('event_id')), ['42', '42']);
  assert.deepEqual(requested.map((url) => url.searchParams.get('page')), ['1', '2']);
});
