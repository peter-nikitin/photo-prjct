const assert = require('node:assert/strict');
const test = require('node:test');

const {
  SelectionModel,
  bindEventPhotoManagement,
  replaceManagementFragment,
  refreshFolderDestinations,
} = require('../../src/backend/static/ui/event-photo-management.js');

class FakeFormData {
  constructor(form) {
    this.values = [...(form?.formEntries || [])];
  }

  set(name, value) {
    this.delete(name);
    this.values.push([name, String(value)]);
  }

  append(name, value) {
    this.values.push([name, String(value)]);
  }

  delete(name) {
    this.values = this.values.filter(([key]) => key !== name);
  }

  getAll(name) {
    return this.values.filter(([key]) => key === name).map(([, value]) => value);
  }

  [Symbol.iterator]() {
    return this.values[Symbol.iterator]();
  }
}

function selectorMatches(ownSelector, requested) {
  return requested.split(',').some((value) => value.trim() === ownSelector);
}

function control(selector, values = {}) {
  return {
    disabled: false,
    ...values,
    matches(requested) {
      return selectorMatches(selector, requested);
    },
    closest(requested) {
      if (requested === 'a, button, input') return this;
      return null;
    },
  };
}

function form(selector, formEntries = []) {
  const errors = { textContent: '' };
  return {
    action: '/manage/events/42/photos/actions/',
    formEntries,
    matches(requested) {
      return selectorMatches(selector, requested);
    },
    querySelector(requested) {
      return requested === '[data-form-errors]' ? errors : null;
    },
    errors,
  };
}

function fragment({ canonicalQuery = '', count = 1, photoIds = ['photo-a'], valid = true } = {}) {
  const checkboxes = photoIds.map((photoId) => control('[data-photo-select]', {
    checked: false,
    value: photoId,
  }));
  const actionButtons = [control('[data-photo-action-form] button[type="submit"]')];
  const selectPage = control('[data-select-page]');
  const selectAll = control('[data-select-all-filtered]');
  const clear = control('[data-clear-selection]');
  const actionForm = form('[data-photo-action-form]');
  const summary = { textContent: '' };
  return {
    dataset: {
      canonicalQuery,
      filteredCount: String(count),
      filterValid: valid ? 'true' : 'false',
    },
    checkboxes,
    actionButtons,
    actionForm,
    selectPage,
    selectAll,
    clear,
    summary,
    querySelector(requested) {
      if (requested === '[data-selection-summary]') return summary;
      return null;
    },
    querySelectorAll(requested) {
      if (requested === '[data-photo-select]') return checkboxes;
      if (requested === '[data-photo-action-form] button[type="submit"]') return actionButtons;
      if (requested === '[data-select-page], [data-select-all-filtered]') {
        return [selectPage, selectAll];
      }
      return [];
    },
  };
}

function controllerHarness(initialFragment, initialUrl = 'https://example.test/manage/events/42/photos/') {
  const rootListeners = new Map();
  const windowListeners = new Map();
  const requests = [];
  const responses = [];
  const pendingFragments = [];
  const history = { pushes: [], replacements: [] };
  const location = { href: initialUrl };
  let currentFragment;

  const install = (value) => {
    currentFragment = value;
    value.replaceWith = install;
    return value;
  };
  install(initialFragment);

  const document = {
    events: [],
    listeners: new Map(),
    addEventListener(type, listener) {
      this.listeners.set(type, listener);
    },
    createElement(tag) {
      assert.equal(tag, 'template');
      return {
        content: { firstElementChild: null },
        set innerHTML(_value) {
          this.content.firstElementChild = pendingFragments.shift();
        },
      };
    },
    dispatchEvent(event) {
      this.events.push(event.type);
      this.listeners.get(event.type)?.(event);
    },
    querySelector() {
      return null;
    },
  };
  const root = {
    dataset: { resultsUrl: '/manage/events/42/photos/results/' },
    ownerDocument: document,
    addEventListener(type, listener) {
      rootListeners.set(type, listener);
    },
    querySelector(requested) {
      return requested === '[data-event-photo-fragment]' ? currentFragment : null;
    },
  };
  const updateLocation = (value) => {
    location.href = new URL(value, location.href).href;
  };
  const environment = {
    Event,
    FormData: FakeFormData,
    URL,
    URLSearchParams,
    document,
    history: {
      pushState(_state, _title, value) {
        history.pushes.push(value);
        updateLocation(value);
      },
      replaceState(_state, _title, value) {
        history.replacements.push(value);
        updateLocation(value);
      },
    },
    location,
    addEventListener(type, listener) {
      windowListeners.set(type, listener);
    },
    async fetch(url, options = {}) {
      requests.push({ url: String(url), options });
      const response = responses.shift();
      assert.ok(response, `Unexpected request to ${url}`);
      return response;
    },
  };
  const response = ({ status = 200, canonical = null, json = {} } = {}) => ({
    status,
    ok: status >= 200 && status < 300,
    headers: { get: (name) => name === 'X-Event-Photo-Canonical-Url' ? canonical : null },
    async json() { return json; },
    async text() { return '<div data-event-photo-fragment></div>'; },
  });

  return {
    bind() {
      return bindEventPhotoManagement(root, environment);
    },
    currentFragment: () => currentFragment,
    document,
    environment,
    history,
    requests,
    rootListeners,
    windowListeners,
    queueFragment(value, options = {}) {
      pendingFragments.push(value);
      responses.push(response(options));
    },
    queueJson(json, status = 200) {
      responses.push(response({ json, status }));
    },
  };
}

function clickEvent(target) {
  return { target, preventDefault() {} };
}

function filterInput(filterForm) {
  return {
    closest(requested) {
      return requested === '[data-event-photo-filter-form]' ? filterForm : null;
    },
  };
}

test('selection keeps explicit IDs across pages and page selection unions the current page', () => {
  const selection = new SelectionModel();
  selection.toggle('photo-a', true);
  selection.toggle('photo-b', true);
  assert.deepEqual(selection.payload(), {
    selection_mode: 'explicit',
    photo_id: ['photo-a', 'photo-b'],
  });

  selection.selectPage(['photo-c', 'photo-d']);
  assert.deepEqual(selection.payload(), {
    selection_mode: 'explicit',
    photo_id: ['photo-a', 'photo-b', 'photo-c', 'photo-d'],
  });
});

test('bound pagination and same-filter popstate preserve explicit IDs across pages', async () => {
  const pageOne = fragment({ canonicalQuery: 'visibility=visible', photoIds: ['photo-a'] });
  const harness = controllerHarness(
    pageOne,
    'https://example.test/manage/events/42/photos/?visibility=visible&batch_page=4',
  );
  const controller = harness.bind();

  pageOne.checkboxes[0].checked = true;
  await harness.rootListeners.get('click')(clickEvent(pageOne.checkboxes[0]));
  const pageTwo = fragment({ canonicalQuery: 'visibility=visible', photoIds: ['photo-b'] });
  harness.queueFragment(pageTwo, {
    canonical: '/manage/events/42/photos/?visibility=visible&page=2',
  });
  const nextPage = control('[data-event-photo-page]', {
    href: '/manage/events/42/photos/?visibility=visible&page=2',
  });
  await harness.rootListeners.get('click')(clickEvent(nextPage));

  assert.deepEqual(controller.selection.payload().photo_id, ['photo-a']);
  assert.deepEqual(harness.history.pushes, [
    '/manage/events/42/photos/?visibility=visible&page=2&batch_page=4',
  ]);
  await harness.rootListeners.get('click')(clickEvent(pageTwo.selectPage));
  assert.deepEqual(controller.selection.payload().photo_id, ['photo-a', 'photo-b']);

  harness.environment.location.href =
    'https://example.test/manage/events/42/photos/?visibility=visible&batch_page=4';
  const returnedPageOne = fragment({
    canonicalQuery: 'visibility=visible',
    photoIds: ['photo-a'],
  });
  harness.queueFragment(returnedPageOne, {
    canonical: '/manage/events/42/photos/?visibility=visible',
  });
  await harness.windowListeners.get('popstate')();

  assert.deepEqual(controller.selection.payload().photo_id, ['photo-a', 'photo-b']);
  assert.equal(returnedPageOne.checkboxes[0].checked, true);
  assert.deepEqual(harness.history.replacements, [
    '/manage/events/42/photos/?visibility=visible&batch_page=4',
  ]);
});

test('bound dirty filter blocks reselect and bulk submit from using a stale scope', async () => {
  const initial = fragment({ canonicalQuery: '', count: 10 });
  const harness = controllerHarness(initial);
  const controller = harness.bind();
  const filterForm = form('[data-event-photo-filter-form]', [
    ['from', '2026-09-07T12:00'],
    ['to', '2026-09-07T11:00'],
  ]);

  await harness.rootListeners.get('input')({ target: filterInput(filterForm) });
  await harness.rootListeners.get('click')(clickEvent(initial.selectAll));
  await harness.rootListeners.get('submit')({
    target: initial.actionForm,
    submitter: { value: 'hide' },
    preventDefault() {},
  });

  assert.equal(controller.selection.count, 0);
  assert.equal(initial.selectAll.disabled, true);
  assert.equal(initial.actionButtons[0].disabled, true);
  assert.deepEqual(harness.requests, []);
});

test('bound invalid refresh stays blocked until a successful valid filter refresh', async () => {
  const initial = fragment({ count: 10 });
  const harness = controllerHarness(initial);
  const controller = harness.bind();
  const filterForm = form('[data-event-photo-filter-form]', [
    ['from', '2026-09-07T12:00'],
    ['to', '2026-09-07T11:00'],
  ]);

  await harness.rootListeners.get('input')({ target: filterInput(filterForm) });
  const invalid = fragment({ valid: false, count: 0, photoIds: [] });
  harness.queueFragment(invalid, { status: 422 });
  await harness.rootListeners.get('submit')({
    target: filterForm,
    preventDefault() {},
  });
  assert.equal(controller.filtersDirty, true);
  assert.equal(invalid.selectAll.disabled, true);
  assert.deepEqual(harness.history.pushes, []);

  filterForm.formEntries = [
    ['from', '2026-09-07T10:00'],
    ['to', '2026-09-07T11:00'],
  ];
  await harness.rootListeners.get('input')({ target: filterInput(filterForm) });
  const valid = fragment({
    canonicalQuery: 'from=2026-09-07T10%3A00&to=2026-09-07T11%3A00',
    count: 3,
  });
  harness.queueFragment(valid, {
    canonical:
      '/manage/events/42/photos/?from=2026-09-07T10%3A00&to=2026-09-07T11%3A00',
  });
  await harness.rootListeners.get('submit')({
    target: filterForm,
    preventDefault() {},
  });

  assert.equal(controller.filtersDirty, false);
  assert.equal(valid.selectAll.disabled, false);
  assert.deepEqual(harness.history.pushes, [
    '/manage/events/42/photos/?from=2026-09-07T10%3A00&to=2026-09-07T11%3A00',
  ]);
});

test('fresh filtered counts update only the live all-filtered selection scope', async () => {
  const initial = fragment({ canonicalQuery: 'visibility=visible', count: 4 });
  const harness = controllerHarness(initial);
  const controller = harness.bind();

  initial.checkboxes[0].checked = true;
  await harness.rootListeners.get('click')(clickEvent(initial.checkboxes[0]));
  harness.document.dispatchEvent({
    type: 'findme:event-photo-filter-count',
    detail: { count: 9 },
  });
  assert.equal(initial.dataset.filteredCount, '9');
  assert.equal(controller.selection.mode, 'explicit');
  assert.equal(controller.selection.count, 1);

  controller.clearSelection();
  await harness.rootListeners.get('click')(clickEvent(initial.selectAll));
  assert.equal(controller.selection.mode, 'all_filtered');
  assert.equal(controller.selection.count, 9);
  harness.document.dispatchEvent({
    type: 'findme:event-photo-filter-count',
    detail: { count: 12 },
  });
  assert.equal(controller.selection.mode, 'all_filtered');
  assert.equal(controller.selection.count, 12);
  assert.equal(controller.selection.filterQuery, 'visibility=visible');
  assert.equal(initial.summary.textContent, 'Выбрано 12 фотографий во всех результатах на момент действия');
});

test('bound action installs last-page fallback and replaces the URL with canonical state', async () => {
  const lastPage = fragment({
    canonicalQuery: 'visibility=visible',
    photoIds: ['last-photo'],
  });
  const harness = controllerHarness(
    lastPage,
    'https://example.test/manage/events/42/photos/?visibility=visible&page=2&batch_page=3',
  );
  harness.bind();
  lastPage.checkboxes[0].checked = true;
  await harness.rootListeners.get('click')(clickEvent(lastPage.checkboxes[0]));

  harness.queueJson({ changed_count: 1 });
  const firstPage = fragment({ canonicalQuery: 'visibility=visible', photoIds: ['first-photo'] });
  harness.queueFragment(firstPage, {
    canonical: '/manage/events/42/photos/?visibility=visible',
  });
  await harness.rootListeners.get('submit')({
    target: lastPage.actionForm,
    submitter: { value: 'hide' },
    preventDefault() {},
  });

  assert.equal(harness.currentFragment(), firstPage);
  assert.deepEqual(harness.history.pushes, []);
  assert.deepEqual(harness.history.replacements, [
    '/manage/events/42/photos/?visibility=visible&batch_page=3',
  ]);
  assert.equal(
    harness.environment.location.href,
    'https://example.test/manage/events/42/photos/?visibility=visible&batch_page=3',
  );
});

test('all-filtered is a distinct live scope with no per-photo exclusion mode', () => {
  const selection = new SelectionModel();
  selection.selectAllFiltered(1240, 'folder=7&visibility=visible');
  assert.equal(selection.mode, 'all_filtered');
  assert.equal(selection.count, 1240);
  assert.equal(selection.perPhotoDisabled, true);
  assert.deepEqual(selection.payload(), {
    selection_mode: 'all_filtered',
    filter_query: 'folder=7&visibility=visible',
  });
  assert.throws(() => selection.toggle('photo-a', false), /all-filtered/);

  selection.selectPage(['photo-a']);
  assert.equal(selection.mode, 'explicit');
  assert.equal(selection.perPhotoDisabled, false);
  assert.deepEqual(selection.payload().photo_id, ['photo-a']);
});

test('draft filter edits clear both explicit and all-filtered selection', () => {
  const selection = new SelectionModel();
  selection.toggle('photo-a', true);
  selection.clearForFilterEdit();
  assert.equal(selection.mode, 'none');
  assert.equal(selection.count, 0);

  selection.selectAllFiltered(10, 'processing=failed');
  selection.clearForFilterEdit();
  assert.equal(selection.mode, 'none');
  assert.equal(selection.filterQuery, '');
});

test('fragment replacement changes only the administrative child', () => {
  const uploadRoot = { coordinator: { active: true } };
  const importRoot = { importCoordinator: { pendingSubmissionKey: 'stable' } };
  let installed = null;
  const current = { replaceWith(value) { installed = value; } };
  const managementRoot = { querySelector: () => current };
  const replacement = { id: 'new-fragment' };

  replaceManagementFragment(managementRoot, replacement);

  assert.equal(installed, replacement);
  assert.deepEqual(uploadRoot.coordinator, { active: true });
  assert.deepEqual(importRoot.importCoordinator, { pendingSubmissionKey: 'stable' });
});

test('folder refresh updates descendants and dispatches the import coordinator seam', () => {
  const targetContainer = {
    rendered: null,
    replaceChildren(...children) { this.rendered = children; },
  };
  const importRoot = {
    events: [],
    dispatchEvent(event) { this.events.push(event.type); },
  };
  const select = {
    value: '8',
    options: null,
    replaceChildren(...options) { this.options = options; },
  };
  const document = {
    defaultView: { Event },
    querySelectorAll(selector) {
      if (selector === '[data-folder-targets]') return [targetContainer];
      if (selector === '[data-import-folder]') return [select];
      if (selector === '[data-import-root]') return [importRoot];
      return [];
    },
    createElement(tag) {
      return {
        tag,
        dataset: {},
        textContent: '',
        value: '',
        append() {},
        setAttribute() {},
      };
    },
  };

  refreshFolderDestinations(document, [{ id: 7, name: 'Старт' }, { id: 8, name: 'Финиш' }]);

  assert.equal(targetContainer.rendered.length, 3);
  assert.deepEqual(select.options.map((option) => option.value), ['', '7', '8']);
  assert.equal(select.value, '8');
  assert.deepEqual(importRoot.events, ['findme:event-photo-folders-refreshed']);
});
