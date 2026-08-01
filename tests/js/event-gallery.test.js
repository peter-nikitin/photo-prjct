'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const modulePath = '../../src/backend/static/ui/event-gallery.js';

function loadGalleryModule({ root = null, glightbox = null } = {}) {
  const originalDocument = global.document;
  const originalGLightbox = global.GLightbox;
  delete require.cache[require.resolve(modulePath)];
  global.document = {
    readyState: 'complete',
    querySelector: (selector) => (selector === '.event-gallery' ? root : null),
    addEventListener() {
      throw new Error('The complete document must initialize synchronously.');
    },
  };
  if (glightbox) {
    global.GLightbox = glightbox;
  } else {
    delete global.GLightbox;
  }
  try {
    return require(modulePath);
  } finally {
    if (originalDocument === undefined) delete global.document;
    else global.document = originalDocument;
    if (originalGLightbox === undefined) delete global.GLightbox;
    else global.GLightbox = originalGLightbox;
  }
}

test('initializes GLightbox once with local gallery options', () => {
  const calls = [];

  loadGalleryModule({
    root: { addEventListener() {} },
    glightbox: (options) => calls.push(options),
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].selector, '.event-gallery .glightbox');
  assert.equal(calls[0].touchNavigation, true);
  assert.equal(calls[0].loop, false);
  assert.equal(calls[0].descPosition, 'bottom');
});

test('keeps only the active built-in description download in GLightbox keyboard order', () => {
  const calls = [];
  const makeAction = () => {
    const classes = new Set();
    const attributes = new Map();
    return {
      classes,
      attributes,
      classList: {
        add: (className) => classes.add(className),
        remove: (className) => classes.delete(className),
      },
      setAttribute: (name, value) => attributes.set(name, value),
      removeAttribute: (name) => attributes.delete(name),
    };
  };
  const firstAction = makeAction();
  const secondAction = makeAction();
  const firstSlide = {
    querySelector: (selector) => (selector === '.gallery-lightbox-download' ? firstAction : null),
  };
  const secondSlide = {
    querySelector: (selector) => (selector === '.gallery-lightbox-download' ? secondAction : null),
  };

  loadGalleryModule({
    root: { addEventListener() {} },
    glightbox: (options) => calls.push(options),
  });

  calls[0].afterSlideLoad({ slide: firstSlide });
  calls[0].afterSlideLoad({ slide: secondSlide });
  calls[0].afterSlideChange({ slide: null }, { slide: firstSlide });

  assert.deepEqual([...firstAction.classes], ['gbtn']);
  assert.equal(firstAction.attributes.get('data-taborder'), '4');
  assert.deepEqual([...secondAction.classes], []);
  assert.equal(secondAction.attributes.has('data-taborder'), false);

  calls[0].afterSlideChange({ slide: firstSlide }, { slide: secondSlide });

  assert.deepEqual([...firstAction.classes], []);
  assert.equal(firstAction.attributes.has('data-taborder'), false);
  assert.deepEqual([...secondAction.classes], ['gbtn']);
  assert.equal(secondAction.attributes.get('data-taborder'), '4');
});

test('restores focus to the pointer-opened card after close', () => {
  const clickListeners = [];
  let options;
  let focusCalls = 0;
  const card = {
    focus: () => {
      focusCalls += 1;
    },
  };
  const root = {
    addEventListener: (type, listener) => {
      if (type === 'click') clickListeners.push(listener);
    },
  };

  loadGalleryModule({
    root,
    glightbox: (receivedOptions) => {
      options = receivedOptions;
      return {};
    },
  });

  clickListeners.forEach((listener) => listener({ target: { closest: () => card } }));
  options.onClose();

  assert.equal(focusCalls, 1);
});

test('does nothing without root or GLightbox', () => {
  const calls = [];

  assert.doesNotThrow(() => loadGalleryModule({ glightbox: (options) => calls.push(options) }));
  assert.deepEqual(calls, []);
  assert.doesNotThrow(() => loadGalleryModule({ root: {} }));
});

test('appends the next gallery fragment, advances the link, and refreshes lightbox', async () => {
  let clickListener;
  const appended = [];
  const nextLink = {
    href: 'https://findme.test/events/run?cursor=first',
    addEventListener() {},
    remove() {
      this.removed = true;
    },
  };
  const grid = {
    insertAdjacentHTML: (position, markup) => appended.push({ position, markup }),
  };
  const root = {
    querySelector: (selector) =>
      selector === '.event-gallery-grid' ? grid : selector === '.event-gallery-next' ? nextLink : null,
    addEventListener: (type, listener) => {
      if (type === 'click') clickListener = listener;
    },
    setAttribute() {},
    removeAttribute() {},
  };
  const parsedNextLink = { href: 'https://findme.test/events/run?cursor=second' };
  const fragmentGrid = { innerHTML: '<figure class="gallery-card">next</figure>' };
  const parsedRoot = {
    querySelector: (selector) =>
      selector === '.event-gallery-grid'
        ? fragmentGrid
        : selector === '.event-gallery-next'
          ? parsedNextLink
          : null,
  };
  const eventGallery = loadGalleryModule();
  let reloads = 0;
  eventGallery.initializeProgressivePagination(root, {
    fetchPage: async () => ({ ok: true, text: async () => '<html>next</html>' }),
    parsePage: () => ({ querySelector: (selector) => (selector === '[data-event-gallery]' ? parsedRoot : null) }),
    onAppend: () => {
      reloads += 1;
    },
  });

  let prevented = false;
  clickListener({
    target: { closest: (selector) => (selector === '.event-gallery-next' ? nextLink : null) },
    preventDefault: () => {
      prevented = true;
    },
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(prevented, true);
  assert.deepEqual(appended, [
    { position: 'beforeend', markup: '<figure class="gallery-card">next</figure>' },
  ]);
  assert.equal(nextLink.href, parsedNextLink.href);
  assert.equal(reloads, 1);
});

test('keeps the next-page link after a failed progressive request', async () => {
  let clickListener;
  const nextLink = { href: 'https://findme.test/events/run?cursor=first' };
  const root = {
    querySelector: (selector) => (selector === '.event-gallery-next' ? nextLink : {}),
    addEventListener: (type, listener) => {
      if (type === 'click') clickListener = listener;
    },
    setAttribute() {},
    removeAttribute() {},
  };
  const eventGallery = loadGalleryModule();
  eventGallery.initializeProgressivePagination(root, {
    fetchPage: async () => ({ ok: false }),
    parsePage: () => {
      throw new Error('must not parse an unsuccessful response');
    },
  });

  clickListener({
    target: { closest: (selector) => (selector === '.event-gallery-next' ? nextLink : null) },
    preventDefault() {},
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(nextLink.href, 'https://findme.test/events/run?cursor=first');
  assert.equal(nextLink.removed, undefined);
});
