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
});

test('restores focus to the pointer-opened card after close', () => {
  let clickListener;
  let options;
  let focusCalls = 0;
  const card = {
    focus: () => {
      focusCalls += 1;
    },
  };
  const root = {
    addEventListener: (type, listener) => {
      if (type === 'click') clickListener = listener;
    },
  };

  loadGalleryModule({
    root,
    glightbox: (receivedOptions) => {
      options = receivedOptions;
      return {};
    },
  });

  clickListener({ target: { closest: () => card } });
  options.onClose();

  assert.equal(focusCalls, 1);
});

test('does nothing without root or GLightbox', () => {
  const calls = [];

  assert.doesNotThrow(() => loadGalleryModule({ glightbox: (options) => calls.push(options) }));
  assert.deepEqual(calls, []);
  assert.doesNotThrow(() => loadGalleryModule({ root: {} }));
});
