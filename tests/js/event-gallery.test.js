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

function makeFaceChooser() {
  const listeners = new Map();
  const trigger = {
    focusCalls: 0,
    focus() {
      this.focusCalls += 1;
    },
  };
  const tile = {
    focusCalls: 0,
    focus() {
      this.focusCalls += 1;
    },
  };
  const details = {
    open: false,
    addEventListener(type, listener) {
      listeners.set(type, listener);
    },
    querySelector(selector) {
      if (selector === '[data-face-chooser-trigger]') return trigger;
      if (selector === '[data-face-choice]') return tile;
      return null;
    },
    contains(target) {
      return target === details || target === trigger || target === tile;
    },
    trigger(type) {
      listeners.get(type)?.({ target: details });
    },
  };
  return { details, tile, trigger };
}

test('keeps one face chooser open and restores its trigger after outside close or Escape', () => {
  const documentListeners = new Map();
  const first = makeFaceChooser();
  const second = makeFaceChooser();
  const root = {
    ownerDocument: {
      addEventListener(type, listener) {
        documentListeners.set(type, listener);
      },
    },
    querySelectorAll(selector) {
      assert.equal(selector, '[data-face-chooser]');
      return [first.details, second.details];
    },
  };
  const { initializeFaceChoosers } = loadGalleryModule();

  initializeFaceChoosers(root);
  first.details.open = true;
  first.details.trigger('toggle');

  assert.equal(first.tile.focusCalls, 0);
  assert.equal(second.details.open, false);

  second.details.open = true;
  second.details.trigger('toggle');

  assert.equal(first.details.open, false);
  assert.equal(first.trigger.focusCalls, 0);
  assert.equal(second.tile.focusCalls, 0);

  documentListeners.get('click')({ target: {} });

  assert.equal(second.details.open, false);
  assert.equal(second.trigger.focusCalls, 1);

  first.details.open = true;
  first.details.trigger('toggle');
  documentListeners.get('keydown')({ key: 'Escape' });

  assert.equal(first.details.open, false);
  assert.equal(first.trigger.focusCalls, 1);
});

test('leaves a face form click outside the GLightbox trigger', () => {
  const clickListeners = [];
  let options;
  const root = {
    addEventListener(type, listener) {
      if (type === 'click') clickListeners.push(listener);
    },
  };
  const glightbox = (receivedOptions) => {
    options = receivedOptions;
    return {};
  };
  const eventGallery = loadGalleryModule({ root, glightbox });
  const faceFormControl = {
    closest(selector) {
      assert.equal(selector, '.gallery-card-link');
      return null;
    },
  };

  eventGallery.initializeEventGallery(root, glightbox);
  clickListeners.forEach((listener) => listener({ target: faceFormControl }));
  options.onClose();

  assert.equal(options.onClose(), undefined);
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
