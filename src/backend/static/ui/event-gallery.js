(function eventGalleryModule(globalScope, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  if (globalScope) {
    globalScope.FindMeEventGallery = api;
  }
})(typeof globalThis === 'undefined' ? this : globalThis, function buildEventGallery() {
  'use strict';

  function initializeEventGallery(root, GLightbox) {
    if (!root || typeof GLightbox !== 'function') return null;
    return GLightbox({
      selector: '.event-gallery .glightbox',
      touchNavigation: true,
      loop: false,
    });
  }

  return { initializeEventGallery };
});

if (typeof document !== 'undefined') {
  const start = () =>
    globalThis.FindMeEventGallery.initializeEventGallery(
      document.querySelector('.event-gallery'),
      globalThis.GLightbox,
    );
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}
