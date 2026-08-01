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
    root.addEventListener('click', (event) => {
      lastTrigger = event.target.closest?.('.gallery-card-link') ?? null;
    });
    return GLightbox({
      selector: '.event-gallery .glightbox',
      touchNavigation: true,
      loop: false,
      onClose: () => lastTrigger?.focus(),
    });
  }

  function initializeProgressivePagination(root, { fetchPage, parsePage, onAppend, createObserver } = {}) {
    if (!root || typeof root.querySelector !== 'function') return;
    const requestPage = fetchPage ?? globalThis.fetch?.bind(globalThis);
    const parse =
      parsePage ??
      ((html) => new globalThis.DOMParser().parseFromString(html, 'text/html'));
    const observerFactory =
      createObserver ??
      (typeof globalThis.IntersectionObserver === 'function'
        ? (callback) => new globalThis.IntersectionObserver(callback, { rootMargin: '600px 0px' })
        : null);
    const link = root.querySelector('.event-gallery-next');
    if (typeof requestPage !== 'function' || typeof observerFactory !== 'function' || !link?.href) return;
    let loading = false;
    const observer = observerFactory((entries) => {
      if (!entries.some((entry) => entry.isIntersecting) || loading) return;

      loading = true;
      let shouldContinue = false;
      observer.disconnect();
      root.setAttribute?.('aria-busy', 'true');
      requestPage(link.href, {
        credentials: 'same-origin',
        headers: { Accept: 'text/html', 'X-Requested-With': 'FindMeEventGallery' },
      })
        .then((response) => {
          if (!response.ok) throw new Error('Could not load the next gallery page.');
          return response.text();
        })
        .then((html) => {
          const nextRoot = parse(html).querySelector('[data-event-gallery]');
          const nextGrid = nextRoot?.querySelector('.event-gallery-grid');
          const grid = root.querySelector('.event-gallery-grid');
          if (!nextGrid || !grid) throw new Error('Could not find the next gallery fragment.');

          grid.insertAdjacentHTML('beforeend', nextGrid.innerHTML);
          const nextLink = nextRoot.querySelector('.event-gallery-next');
          if (nextLink) {
            link.href = nextLink.href;
            shouldContinue = true;
          }
          else {
            link.remove();
            link.href = '';
          }
          onAppend?.();
        })
        .catch(() => {})
        .finally(() => {
          loading = false;
          root.removeAttribute?.('aria-busy');
          if (shouldContinue) observer.observe(link);
        });
    });
    observer.observe(link);
  }

  return { initializeEventGallery, initializeProgressivePagination };
});

if (typeof document !== 'undefined') {
  const start = () => {
    const root = document.querySelector('[data-event-gallery]') ?? document.querySelector('.event-gallery');
    const lightbox = globalThis.FindMeEventGallery.initializeEventGallery(root, globalThis.GLightbox);
    globalThis.FindMeEventGallery.initializeProgressivePagination(root, {
      onAppend: () => lightbox?.reload?.(),
    });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}
