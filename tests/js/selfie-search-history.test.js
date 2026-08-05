'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const {
  HISTORY_STORAGE_KEY,
  SelfieSearchHistoryStore,
  isCanonicalSelfieSearchResultPath,
  startSelfieSearchHistory,
} = require('../../src/backend/static/ui/selfie-search-history.js');

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
}

class ThrowingStorage {
  constructor({ read = false, write = false } = {}) {
    this.read = read;
    this.write = write;
  }

  getItem() {
    if (this.read) throw new Error('storage read blocked');
    return null;
  }

  setItem() {
    if (this.write) throw new Error('storage write blocked');
  }
}

function resultPath(eventSlug, token) {
  return `/events/${encodeURIComponent(eventSlug)}/selfie-search/${token}/`;
}

function makeStore({ storage = new MemoryStorage(), now = 1_000 } = {}) {
  return new SelfieSearchHistoryStore({ localStorage: storage, now: () => now });
}

test('lists an empty store and saves the first result with an ISO timestamp', () => {
  const storage = new MemoryStorage();
  const store = makeStore({ storage, now: 1_000 });

  assert.deepEqual(store.list('city-run'), []);
  assert.deepEqual(store.save({ eventSlug: 'city-run', resultPath: resultPath('city-run', 'token-a') }), {
    saved: true,
  });
  assert.deepEqual(store.list('city-run'), [
    {
      eventSlug: 'city-run',
      resultPath: '/events/city-run/selfie-search/token-a/',
      openedAt: '1970-01-01T00:00:01.000Z',
    },
  ]);
});

test('keeps multiple entries in deterministic newest-first order and deduplicates a reopened result', () => {
  const storage = new MemoryStorage();
  let currentTime = 1_000;
  const store = new SelfieSearchHistoryStore({ localStorage: storage, now: () => currentTime });

  store.save({ eventSlug: 'city-run', resultPath: resultPath('city-run', 'token-b') });
  currentTime = 3_000;
  store.save({ eventSlug: 'city-run', resultPath: resultPath('city-run', 'token-a') });
  currentTime = 2_000;
  store.save({ eventSlug: 'city-run', resultPath: resultPath('city-run', 'token-c') });

  assert.deepEqual(store.list('city-run').map((entry) => [entry.resultPath, entry.openedAt]), [
    ['/events/city-run/selfie-search/token-a/', '1970-01-01T00:00:03.000Z'],
    ['/events/city-run/selfie-search/token-c/', '1970-01-01T00:00:02.000Z'],
    ['/events/city-run/selfie-search/token-b/', '1970-01-01T00:00:01.000Z'],
  ]);

  currentTime = 4_000;
  assert.deepEqual(store.save({ eventSlug: 'city-run', resultPath: resultPath('city-run', 'token-b') }), {
    saved: true,
  });
  assert.deepEqual(store.list('city-run').map((entry) => [entry.resultPath, entry.openedAt]), [
    ['/events/city-run/selfie-search/token-b/', '1970-01-01T00:00:04.000Z'],
    ['/events/city-run/selfie-search/token-a/', '1970-01-01T00:00:03.000Z'],
    ['/events/city-run/selfie-search/token-c/', '1970-01-01T00:00:02.000Z'],
  ]);
});

test('persists valid entries for a second store instance over the same localStorage', () => {
  const storage = new MemoryStorage();
  const firstStore = makeStore({ storage, now: 5_000 });
  firstStore.save({ eventSlug: 'city-run', resultPath: resultPath('city-run', 'token-a') });

  const secondStore = makeStore({ storage, now: 6_000 });
  assert.deepEqual(secondStore.list('city-run'), [
    {
      eventSlug: 'city-run',
      resultPath: '/events/city-run/selfie-search/token-a/',
      openedAt: '1970-01-01T00:00:05.000Z',
    },
  ]);
});

test('filters by exact event and removes one entry without mutating another event', () => {
  const storage = new MemoryStorage();
  const store = makeStore({ storage, now: 1_000 });
  const cityPath = resultPath('city-run', 'token-city');
  const beachPath = resultPath('beach-run', 'token-beach');
  store.save({ eventSlug: 'city-run', resultPath: cityPath });
  store.save({ eventSlug: 'beach-run', resultPath: beachPath });

  assert.deepEqual(store.list('city-run').map((entry) => entry.resultPath), [cityPath]);
  assert.deepEqual(store.list('other-run'), []);
  assert.deepEqual(store.remove({ eventSlug: 'city-run', resultPath: cityPath }), { removed: true });
  assert.deepEqual(store.list('city-run'), []);
  assert.deepEqual(store.list('beach-run').map((entry) => entry.resultPath), [beachPath]);
  assert.deepEqual(JSON.parse(storage.getItem(HISTORY_STORAGE_KEY)), [
    {
      eventSlug: 'beach-run',
      resultPath: beachPath,
      openedAt: '1970-01-01T00:00:01.000Z',
    },
  ]);
});

test('preserves every valid entry without an implicit cap or expiry', () => {
  const storage = new MemoryStorage();
  const store = makeStore({ storage, now: 10_000 });
  for (let index = 0; index < 120; index += 1) {
    store.save({ eventSlug: 'city-run', resultPath: resultPath('city-run', `token-${index}`) });
  }

  assert.equal(store.list('city-run').length, 120);
  assert.equal(JSON.parse(storage.getItem(HISTORY_STORAGE_KEY)).length, 120);
});

test('normalizes valid stored entries and ignores malformed JSON entries', () => {
  const storage = new MemoryStorage({
    [HISTORY_STORAGE_KEY]: JSON.stringify([
      {
        eventSlug: 'city-run',
        resultPath: resultPath('city-run', 'valid-token'),
        openedAt: '2026-08-04T12:34:56Z',
        obsolete: 'must be dropped',
      },
      null,
      'wrong type',
      {
        eventSlug: 'city-run',
        resultPath: resultPath('city-run', 'invalid-time'),
        openedAt: 'not-a-date',
      },
    ]),
  });
  const store = makeStore({ storage });

  assert.deepEqual(store.list('city-run'), [
    {
      eventSlug: 'city-run',
      resultPath: resultPath('city-run', 'valid-token'),
      openedAt: '2026-08-04T12:34:56.000Z',
    },
  ]);
  assert.deepEqual(store.save({ eventSlug: 'city-run', resultPath: resultPath('city-run', 'new-token') }), {
    saved: true,
  });
  assert.deepEqual(JSON.parse(storage.getItem(HISTORY_STORAGE_KEY)), [
    {
      eventSlug: 'city-run',
      resultPath: resultPath('city-run', 'new-token'),
      openedAt: '1970-01-01T00:00:01.000Z',
    },
    {
      eventSlug: 'city-run',
      resultPath: resultPath('city-run', 'valid-token'),
      openedAt: '2026-08-04T12:34:56.000Z',
    },
  ].sort((left, right) => right.openedAt.localeCompare(left.openedAt)));
});

test('reads malformed JSON as an empty history and repairs it on the next successful save', () => {
  const storage = new MemoryStorage({ [HISTORY_STORAGE_KEY]: '{not-json' });
  const store = makeStore({ storage, now: 2_000 });
  const path = resultPath('city-run', 'repaired-token');

  assert.deepEqual(store.list('city-run'), []);
  assert.deepEqual(store.save({ eventSlug: 'city-run', resultPath: path }), { saved: true });
  assert.deepEqual(JSON.parse(storage.getItem(HISTORY_STORAGE_KEY)), [
    {
      eventSlug: 'city-run',
      resultPath: path,
      openedAt: '1970-01-01T00:00:02.000Z',
    },
  ]);
});

test('rejects malformed and non-canonical result paths', () => {
  const validEvent = 'city-run';
  const validPath = resultPath(validEvent, 'opaque-token');
  const rejectedPaths = [
    ['absolute URL', `https://photos.example${validPath}`],
    ['query string', `${validPath}?from=history`],
    ['fragment', `${validPath}#photos`],
    ['extra path segment', `${validPath}photos/`],
    ['missing token', '/events/city-run/selfie-search//'],
    ['slug mismatch', '/events/other-run/selfie-search/opaque-token/'],
    ['encoded traversal', '/events/city-run/selfie-search/%2e%2e/'],
    ['non-selfie-search path', '/events/city-run/gallery/opaque-token/'],
  ];

  for (const [label, path] of rejectedPaths) {
    assert.equal(isCanonicalSelfieSearchResultPath(validEvent, path), false, label);
  }
});

test('rejects malformed entries and accepts a Unicode slug only in its canonical encoded pathname', () => {
  const unicodeSlug = 'cyclingrace-олимпия';
  const canonicalPath = resultPath(unicodeSlug, 'opaque-token');
  assert.equal(isCanonicalSelfieSearchResultPath(unicodeSlug, canonicalPath), true);
  assert.equal(
    isCanonicalSelfieSearchResultPath(unicodeSlug, `/events/${unicodeSlug}/selfie-search/opaque-token/`),
    false,
  );

  const storage = new MemoryStorage({
    [HISTORY_STORAGE_KEY]: JSON.stringify([
      { eventSlug: 7, resultPath: canonicalPath, openedAt: '2026-08-04T12:00:00.000Z' },
      { eventSlug: unicodeSlug, resultPath: 7, openedAt: '2026-08-04T12:00:00.000Z' },
      { eventSlug: unicodeSlug, resultPath: canonicalPath, openedAt: 7 },
      { eventSlug: unicodeSlug, resultPath: canonicalPath, openedAt: '2026-08-04T12:00:00.000Z' },
    ]),
  });
  const store = makeStore({ storage });
  assert.deepEqual(store.list(unicodeSlug), [
    {
      eventSlug: unicodeSlug,
      resultPath: canonicalPath,
      openedAt: '2026-08-04T12:00:00.000Z',
    },
  ]);
});

test('returns empty lists when localStorage reads throw', () => {
  const store = makeStore({ storage: new ThrowingStorage({ read: true }) });

  assert.doesNotThrow(() => store.list('city-run'));
  assert.deepEqual(store.list('city-run'), []);
});

test('reports failed saves and removals when localStorage writes throw', () => {
  const storage = new ThrowingStorage({ write: true });
  const store = makeStore({ storage });
  const path = resultPath('city-run', 'opaque-token');

  assert.deepEqual(store.save({ eventSlug: 'city-run', resultPath: path }), {
    saved: false,
    reason: 'storage_unavailable',
  });
  assert.deepEqual(store.remove({ eventSlug: 'city-run', resultPath: path }), {
    removed: false,
    reason: 'storage_unavailable',
  });
});

test('reports invalid save and removal requests without touching storage', () => {
  const storage = new MemoryStorage();
  const store = makeStore({ storage });

  assert.deepEqual(store.save({ eventSlug: 'city-run', resultPath: '/not-a-result/' }), {
    saved: false,
    reason: 'invalid_entry',
  });
  assert.deepEqual(store.remove({ eventSlug: 'city-run', resultPath: '/not-a-result/' }), {
    removed: false,
    reason: 'invalid_entry',
  });
  assert.equal(storage.getItem(HISTORY_STORAGE_KEY), null);
});

class HistoryFakeElement {
  constructor({ tagName = 'DIV', dataset = {}, hidden = false } = {}) {
    this.tagName = tagName;
    this.dataset = dataset;
    this.hidden = hidden;
    this.open = false;
    this.className = '';
    this.textContent = '';
    this.children = [];
    this.attributes = new Map();
    this.listeners = new Map();
    this.focusCount = 0;
    this.type = '';
  }

  append(...children) {
    for (const child of children) {
      child.parentElement = this;
      this.children.push(child);
    }
  }

  replaceChildren(...children) {
    this.children = [];
    this.append(...children);
  }

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }

  trigger(type) {
    return this.listeners.get(type)?.({ preventDefault() {} });
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === 'hidden') this.hidden = true;
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  hasAttribute(name) {
    return this.attributes.has(name);
  }

  removeAttribute(name) {
    this.attributes.delete(name);
    if (name === 'hidden') this.hidden = false;
  }

  focus() {
    this.focusCount += 1;
  }

  querySelector(selector) {
    if (selector === 'details') return historyElements(this, 'DETAILS')[0] ?? null;
    return null;
  }
}

function historyText(element) {
  return [element.textContent, ...element.children.map(historyText)].join('');
}

function historyAttributes(element) {
  return [
    ...element.attributes.values(),
    ...element.children.flatMap(historyAttributes),
  ];
}

function historyButtons(element) {
  return element.children.flatMap((child) => [
    ...(child.type === 'button' ? [child] : []),
    ...historyButtons(child),
  ]);
}

function historyElements(element, tagName) {
  return [
    ...(element.tagName === tagName ? [element] : []),
    ...element.children.flatMap((child) => historyElements(child, tagName)),
  ];
}

function makeHistoryDocument({ eventSlug = '', result = false, submit = false, spriteHref = null } = {}) {
  const history = new HistoryFakeElement({
    dataset: { eventSlug },
    hidden: !result,
  });
  const list = new HistoryFakeElement();
  const submitButton = submit ? new HistoryFakeElement() : null;
  const spriteUse = spriteHref ? new HistoryFakeElement({ tagName: 'USE' }) : null;
  spriteUse?.setAttribute('href', spriteHref);
  history.append(list);
  history.querySelector = (selector) =>
    selector === '[data-selfie-search-history-list]' ? list : null;
  const document = {
    createElement(tagName) {
      return new HistoryFakeElement({ tagName: tagName.toUpperCase() });
    },
    createElementNS(_namespace, tagName) {
      return new HistoryFakeElement({ tagName: tagName.toUpperCase() });
    },
    querySelector(selector) {
      if (selector === '[data-selfie-search-result]') return result ? history : null;
      if (selector === '[data-selfie-search-history]') return result ? null : history;
      if (selector === '[data-selfie-search-history-list]') return result ? null : list;
      if (selector === '[data-selfie-search-form] button[type="submit"]') return submitButton;
      if (selector === 'use[href*="icons"][href*="#"]') return spriteUse;
      return null;
    },
  };
  return { document, history, list, submitButton };
}

function historyWindow({ pathname, storage = new MemoryStorage() } = {}) {
  return {
    location: {
      pathname,
      assign(path) {
        this.assignedPath = path;
      },
    },
    localStorage: storage,
  };
}

test('result bootstrap saves only the current canonical pathname and ignores unavailable storage', () => {
  const path = resultPath('city-run', 'result-token');
  const storage = new MemoryStorage();
  const { document } = makeHistoryDocument({ eventSlug: 'city-run', result: true });
  const window = historyWindow({ pathname: path, storage });
  window.location.search = '?ignored=yes';
  window.location.hash = '#fragment';

  startSelfieSearchHistory(document, window, { now: () => 5_000 });

  assert.deepEqual(JSON.parse(storage.getItem(HISTORY_STORAGE_KEY)), [
    { eventSlug: 'city-run', resultPath: path, openedAt: '1970-01-01T00:00:05.000Z' },
  ]);
  let unavailableStorageReadAttempted = false;
  const unavailableStorage = {
    getItem() {
      unavailableStorageReadAttempted = true;
      throw new Error('read blocked');
    },
  };
  assert.doesNotThrow(() => {
    startSelfieSearchHistory(
      document,
      historyWindow({ pathname: path, storage: unavailableStorage }),
    );
  });
  assert.equal(unavailableStorageReadAttempted, true);
});

test('event bootstrap hides an empty list and renders a closed counted disclosure without token DOM leaks', () => {
  const firstPath = resultPath('city-run', 'first-token');
  const secondPath = resultPath('city-run', 'second-token');
  const otherPath = resultPath('beach-run', 'other-token');
  const storage = new MemoryStorage({
    [HISTORY_STORAGE_KEY]: JSON.stringify([
      { eventSlug: 'city-run', resultPath: firstPath, openedAt: '2026-08-04T10:00:00.000Z' },
      { eventSlug: 'city-run', resultPath: secondPath, openedAt: '2026-08-04T12:00:00.000Z' },
      { eventSlug: 'beach-run', resultPath: otherPath, openedAt: '2026-08-04T13:00:00.000Z' },
    ]),
  });
  const empty = makeHistoryDocument({ eventSlug: 'city-run' });
  startSelfieSearchHistory(empty.document, historyWindow({ pathname: '/', storage: new MemoryStorage() }));
  assert.equal(empty.history.hidden, true);

  const fixture = makeHistoryDocument({ eventSlug: 'city-run' });
  startSelfieSearchHistory(fixture.document, historyWindow({ pathname: '/', storage }), {
    formatOpenedAt: (openedAt) => `LOCAL ${openedAt}`,
  });

  assert.equal(fixture.history.hidden, false);
  const details = historyElements(fixture.history, 'DETAILS')[0];
  const summary = historyElements(fixture.history, 'SUMMARY')[0];
  assert.ok(details);
  assert.ok(summary);
  assert.equal(details.open, false);
  assert.equal(summary.textContent, 'Мои результаты поиска · 2');
  assert.equal(historyText(fixture.history), [
    'Мои результаты поиска · 2',
    'Ссылки сохранены только в этом браузере. Любой, у кого есть ссылка, сможет открыть результат.',
    'LOCAL 2026-08-04T12:00:00.000Z',
    'LOCAL 2026-08-04T10:00:00.000Z',
  ].join(''));
  assert.equal(fixture.list.children.length, 1);
  assert.equal(historyElements(fixture.list, 'DETAILS').length, 1);
  assert.equal(historyElements(fixture.list, 'DIV').filter((element) => element.className === 'selfie-search-history-row').length, 2);
  assert.equal(historyText(fixture.history).includes('token'), false);
  assert.equal(historyAttributes(fixture.history).join(' ').includes('token'), false);
  assert.deepEqual(
    historyButtons(fixture.history).map((button) => button.getAttribute('aria-label')),
    [
      'Открыть результат от LOCAL 2026-08-04T12:00:00.000Z',
      'Удалить результат с устройства',
      'Открыть результат от LOCAL 2026-08-04T10:00:00.000Z',
      'Удалить результат с устройства',
    ],
  );
});

test('delete icons reuse the manifest-hashed shared sprite URL', () => {
  const storage = new MemoryStorage({
    [HISTORY_STORAGE_KEY]: JSON.stringify([
      {
        eventSlug: 'city-run',
        resultPath: resultPath('city-run', 'saved-token'),
        openedAt: '2026-08-04T12:00:00.000Z',
      },
    ]),
  });
  const fixture = makeHistoryDocument({
    eventSlug: 'city-run',
    spriteHref: '/static/ui/icons.abc123.svg#calendar',
  });

  startSelfieSearchHistory(fixture.document, historyWindow({ pathname: '/', storage }), {
    formatOpenedAt: (openedAt) => openedAt,
  });

  const removeButton = historyButtons(fixture.history)[1];
  const icon = removeButton.children.find((child) => child.tagName === 'SVG');
  const use = icon?.children.find((child) => child.tagName === 'USE');
  assert.equal(use?.getAttribute('href'), '/static/ui/icons.abc123.svg#trash');
});

test('event controls navigate and move deletion focus to next, previous, and form submit controls', () => {
  const firstPath = resultPath('city-run', 'first-token');
  const secondPath = resultPath('city-run', 'second-token');
  const thirdPath = resultPath('city-run', 'third-token');
  const otherPath = resultPath('beach-run', 'other-token');
  const storage = new MemoryStorage({
    [HISTORY_STORAGE_KEY]: JSON.stringify([
      { eventSlug: 'city-run', resultPath: firstPath, openedAt: '2026-08-04T10:00:00.000Z' },
      { eventSlug: 'city-run', resultPath: secondPath, openedAt: '2026-08-04T12:00:00.000Z' },
      { eventSlug: 'city-run', resultPath: thirdPath, openedAt: '2026-08-04T13:00:00.000Z' },
      { eventSlug: 'beach-run', resultPath: otherPath, openedAt: '2026-08-04T13:00:00.000Z' },
    ]),
  });
  const fixture = makeHistoryDocument({ eventSlug: 'city-run', submit: true });
  const window = historyWindow({ pathname: '/', storage });
  startSelfieSearchHistory(fixture.document, window, { formatOpenedAt: (value) => value });

  let buttons = historyButtons(fixture.history);
  assert.equal(buttons.length, 6);
  buttons[0].trigger('click');
  assert.equal(window.location.assignedPath, thirdPath);

  buttons[1].trigger('click');
  assert.equal(fixture.list.children.length, 1);
  buttons = historyButtons(fixture.history);
  assert.equal(buttons[0].focusCount, 1);
  assert.deepEqual(
    JSON.parse(storage.getItem(HISTORY_STORAGE_KEY)).map((entry) => entry.resultPath).sort(),
    [firstPath, secondPath, otherPath].sort(),
  );

  buttons[3].trigger('click');
  buttons = historyButtons(fixture.history);
  assert.equal(buttons[0].focusCount, 1);
  assert.deepEqual(
    JSON.parse(storage.getItem(HISTORY_STORAGE_KEY)).map((entry) => entry.resultPath).sort(),
    [secondPath, otherPath].sort(),
  );

  buttons[1].trigger('click');
  assert.equal(fixture.history.hidden, true);
  assert.equal(historyButtons(fixture.history).length, 0);
  assert.equal(fixture.submitButton.focusCount, 1);
  assert.deepEqual(JSON.parse(storage.getItem(HISTORY_STORAGE_KEY)).map((entry) => entry.resultPath), [
    otherPath,
  ]);
});

test('open control refuses a path changed after its row was rendered', () => {
  const entry = {
    eventSlug: 'city-run',
    resultPath: resultPath('city-run', 'safe-token'),
    openedAt: '2026-08-04T12:00:00.000Z',
  };
  const store = {
    list() {
      return [entry];
    },
    remove() {
      return { removed: true };
    },
  };
  const fixture = makeHistoryDocument({ eventSlug: 'city-run' });
  const window = historyWindow({ pathname: '/' });

  startSelfieSearchHistory(fixture.document, window, { store });
  entry.resultPath = '/events/city-run/selfie-search/not-a-canonical-path';
  historyButtons(fixture.history)[0].trigger('click');

  assert.equal(window.location.assignedPath, undefined);
});
