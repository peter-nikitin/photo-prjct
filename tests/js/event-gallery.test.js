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

  loadGalleryModule({ root: {}, glightbox: (options) => calls.push(options) });

  assert.deepEqual(calls, [
    {
      selector: '.event-gallery .glightbox',
      touchNavigation: true,
      loop: false,
    },
  ]);
});

test('does nothing without root or GLightbox', () => {
  assert.doesNotThrow(() => loadGalleryModule({ glightbox: () => {} }));
  assert.doesNotThrow(() => loadGalleryModule({ root: {} }));
});
