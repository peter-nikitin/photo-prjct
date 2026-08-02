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

test('does not expose progressive pagination', () => {
  const eventGallery = loadGalleryModule();

  assert.equal(eventGallery.initializeProgressivePagination, undefined);
});
