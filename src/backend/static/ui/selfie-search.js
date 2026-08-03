(function selfieSearchModule(globalScope, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (globalScope) globalScope.FindMeSelfieSearch = api;
})(typeof globalThis === 'undefined' ? this : globalThis, function buildSelfieSearch() {
  'use strict';

  const ACTIVE_STATES = new Set(['queued', 'processing', 'cleanup_pending']);
  const POLL_DELAY_MS = 2000;
  const MAX_BACKOFF_MS = 30000;

  class SelfieSearchPoller {
    constructor({ url, fetch, setTimeout, clearTimeout, reload }) {
      this.url = url;
      this.fetch = fetch;
      this.setTimeout = setTimeout;
      this.clearTimeout = clearTimeout;
      this.reload = reload;
      this.timer = null;
      this.inFlight = false;
      this.stopped = false;
      this.failureCount = 0;
    }

    schedule(delay) {
      if (this.stopped || this.timer) return;
      this.timer = this.setTimeout(async () => {
        this.timer = null;
        await this.poll();
      }, delay);
    }

    async poll() {
      if (this.stopped || this.inFlight) return;
      this.inFlight = true;
      try {
        const response = await this.fetch(this.url, {
          credentials: 'same-origin',
          headers: { Accept: 'application/json' },
        });
        if (!response.ok) throw new Error(`Unexpected status: ${response.status}`);
        const payload = await response.json();
        if (!ACTIVE_STATES.has(payload.status)) {
          this.stop();
          this.reload();
          return;
        }
        this.failureCount = 0;
        this.schedule(POLL_DELAY_MS);
      } catch (_error) {
        this.failureCount += 1;
        this.schedule(Math.min(POLL_DELAY_MS * 2 ** this.failureCount, MAX_BACKOFF_MS));
      } finally {
        this.inFlight = false;
      }
    }

    start() {
      this.poll();
      return this;
    }

    stop() {
      this.stopped = true;
      if (this.timer) this.clearTimeout(this.timer);
      this.timer = null;
    }
  }

  function bindSelfieSearchForm(form) {
    if (!form) return;
    form.addEventListener('submit', () => {
      const button = form.querySelector('button[type="submit"]');
      if (!button || button.disabled) return;
      button.disabled = true;
      button.textContent = 'Ищем фотографии…';
    });
  }

  function focusSelfieSearchError(document) {
    const error = document.querySelector('[data-selfie-search-error]');
    if (error && typeof error.focus === 'function') error.focus();
  }

  function startBrowserUi(document, window) {
    bindSelfieSearchForm(document.querySelector('[data-selfie-search-form]'));
    focusSelfieSearchError(document);
    const result = document.querySelector('[data-selfie-search-result]');
    if (!result) return null;
    return new SelfieSearchPoller({
      url: result.dataset.statusUrl,
      fetch: window.fetch.bind(window),
      setTimeout: window.setTimeout.bind(window),
      clearTimeout: window.clearTimeout.bind(window),
      reload: () => window.location.reload(),
    }).start();
  }

  return {
    SelfieSearchPoller,
    bindSelfieSearchForm,
    focusSelfieSearchError,
    startBrowserUi,
  };
});

if (typeof document !== 'undefined') {
  const start = () => globalThis.FindMeSelfieSearch.startBrowserUi(document, globalThis);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}
