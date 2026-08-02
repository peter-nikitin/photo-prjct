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
    let lastTrigger = null;
    const descriptionDownload = (slide) => slide?.querySelector('.gallery-lightbox-download');
    const removeDescriptionDownloadFromKeyboardOrder = (slide) => {
      const download = descriptionDownload(slide);
      if (!download) return;
      download.classList.remove('gbtn');
      download.removeAttribute('data-taborder');
    };
    const addDescriptionDownloadToKeyboardOrder = (slide) => {
      const download = descriptionDownload(slide);
      if (!download) return;
      download.classList.add('gbtn');
      download.setAttribute('data-taborder', '4');
    };
    root.addEventListener('click', (event) => {
      lastTrigger = event.target.closest?.('.gallery-card-link') ?? null;
    });
    return GLightbox({
      selector: '.event-gallery .glightbox',
      touchNavigation: true,
      loop: false,
      descPosition: 'bottom',
      afterSlideLoad: ({ slide }) => {
        removeDescriptionDownloadFromKeyboardOrder(slide);
      },
      beforeSlideChange: (previous, current) => {
        removeDescriptionDownloadFromKeyboardOrder(previous?.slide);
        removeDescriptionDownloadFromKeyboardOrder(current?.slide);
      },
      afterSlideChange: (previous, current) => {
        removeDescriptionDownloadFromKeyboardOrder(previous?.slide);
        addDescriptionDownloadToKeyboardOrder(current?.slide);
      },
      onClose: () => lastTrigger?.focus(),
    });
  }

  return { initializeEventGallery };
});

if (typeof document !== 'undefined') {
  const start = () => {
    const root = document.querySelector('[data-event-gallery]') ?? document.querySelector('.event-gallery');
    globalThis.FindMeEventGallery.initializeEventGallery(root, globalThis.GLightbox);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}
