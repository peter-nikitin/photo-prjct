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

  function initializeFaceChoosers(root) {
    if (!root?.querySelectorAll) return;
    const choosers = [...root.querySelectorAll('[data-face-chooser]')];
    if (!choosers.length) return;
    const ownerDocument = root.ownerDocument ?? globalThis.document;
    let openChooser = null;
    const closeChooser = (chooser, { restoreFocus = true } = {}) => {
      if (!chooser || !chooser.open) return;
      chooser.open = false;
      if (openChooser === chooser) openChooser = null;
      if (restoreFocus) chooser.querySelector('[data-face-chooser-trigger]')?.focus();
    };
    const closeOpenChooser = (options) => closeChooser(openChooser, options);

    choosers.forEach((chooser) => {
      chooser.addEventListener('toggle', () => {
        if (!chooser.open) {
          if (openChooser === chooser) openChooser = null;
          return;
        }
        if (openChooser && openChooser !== chooser) {
          closeChooser(openChooser, { restoreFocus: false });
        }
        openChooser = chooser;
      });
    });

    ownerDocument?.addEventListener('click', (event) => {
      if (openChooser && !openChooser.contains(event.target)) closeOpenChooser();
    });
    ownerDocument?.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeOpenChooser();
    });
  }

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

  return { initializeEventGallery, initializeFaceChoosers };
});

if (typeof document !== 'undefined') {
  const start = () => {
    const root = document.querySelector('[data-event-gallery]') ?? document.querySelector('.event-gallery');
    globalThis.FindMeEventGallery.initializeFaceChoosers(root);
    globalThis.FindMeEventGallery.initializeEventGallery(root, globalThis.GLightbox);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}
