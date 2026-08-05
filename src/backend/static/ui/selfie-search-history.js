(function selfieSearchHistoryModule(globalScope, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (globalScope) globalScope.FindMeSelfieSearchHistory = api;
})(typeof globalThis === 'undefined' ? this : globalThis, function buildSelfieSearchHistory() {
  'use strict';

  const HISTORY_STORAGE_KEY = 'findme_selfie_search_history:v1';
  const SELFIE_SEARCH_TOKEN = /^[A-Za-z0-9_-]+$/;
  const EVENT_SLUG_FORBIDDEN = /[\\/?#\u0000-\u001f\u007f]/;

  const rootScope = typeof globalThis === 'undefined' ? {} : globalThis;

  function browserNow() {
    return Date.now();
  }

  function safeGlobalValue(name) {
    try {
      return rootScope[name];
    } catch (_error) {
      return undefined;
    }
  }

  function safeObjectValue(object, name) {
    try {
      return object?.[name];
    } catch (_error) {
      return undefined;
    }
  }

  function encodedEventSlug(eventSlug) {
    if (
      typeof eventSlug !== 'string' ||
      eventSlug.length === 0 ||
      eventSlug === '.' ||
      eventSlug === '..' ||
      EVENT_SLUG_FORBIDDEN.test(eventSlug)
    ) {
      return null;
    }
    try {
      return encodeURIComponent(eventSlug);
    } catch (_error) {
      return null;
    }
  }

  function isCanonicalSelfieSearchResultPath(eventSlug, resultPath) {
    const encodedSlug = encodedEventSlug(eventSlug);
    if (encodedSlug === null || typeof resultPath !== 'string') return false;

    const prefix = `/events/${encodedSlug}/selfie-search/`;
    if (!resultPath.startsWith(prefix) || !resultPath.endsWith('/')) return false;
    const token = resultPath.slice(prefix.length, -1);
    return SELFIE_SEARCH_TOKEN.test(token) && resultPath === `${prefix}${token}/`;
  }

  function normalizedOpenedAt(value) {
    if (typeof value !== 'string' || value.length === 0) return null;
    const milliseconds = Date.parse(value);
    if (!Number.isFinite(milliseconds)) return null;
    try {
      return new Date(milliseconds).toISOString();
    } catch (_error) {
      return null;
    }
  }

  function normalizeEntry(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const { eventSlug, resultPath } = value;
    if (!isCanonicalSelfieSearchResultPath(eventSlug, resultPath)) return null;
    const openedAt = normalizedOpenedAt(value.openedAt);
    if (openedAt === null) return null;
    return { eventSlug, resultPath, openedAt };
  }

  function compareEntries(left, right) {
    if (left.openedAt !== right.openedAt) return left.openedAt > right.openedAt ? -1 : 1;
    if (left.eventSlug !== right.eventSlug) return left.eventSlug < right.eventSlug ? -1 : 1;
    if (left.resultPath === right.resultPath) return 0;
    return left.resultPath < right.resultPath ? -1 : 1;
  }

  function normalizedEntries(values) {
    const byPath = new Map();
    for (const value of values) {
      const entry = normalizeEntry(value);
      if (!entry) continue;
      const existing = byPath.get(entry.resultPath);
      if (!existing || compareEntries(entry, existing) < 0) byPath.set(entry.resultPath, entry);
    }
    return [...byPath.values()].sort(compareEntries);
  }

  class SelfieSearchHistoryStore {
    constructor({ localStorage = safeGlobalValue('localStorage'), now = browserNow } = {}) {
      this.localStorage = localStorage;
      this.now = typeof now === 'function' ? now : browserNow;
    }

    _read() {
      if (!this.localStorage || typeof this.localStorage.getItem !== 'function') {
        return { available: false, entries: [] };
      }

      let raw;
      try {
        raw = this.localStorage.getItem(HISTORY_STORAGE_KEY);
      } catch (_error) {
        return { available: false, entries: [] };
      }
      if (raw === null || raw === undefined || raw === '') return { available: true, entries: [] };

      let parsed;
      try {
        parsed = JSON.parse(raw);
      } catch (_error) {
        // A later successful write is allowed to replace malformed storage.
        return { available: true, entries: [] };
      }
      if (!Array.isArray(parsed)) return { available: true, entries: [] };
      return { available: true, entries: normalizedEntries(parsed) };
    }

    _write(entries) {
      if (!this.localStorage || typeof this.localStorage.setItem !== 'function') return false;
      try {
        this.localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(normalizedEntries(entries)));
        return true;
      } catch (_error) {
        return false;
      }
    }

    _openedAt() {
      let value;
      try {
        value = this.now();
      } catch (_error) {
        return null;
      }
      if (value instanceof Date) value = value.getTime();
      if (typeof value !== 'number' || !Number.isFinite(value)) return null;
      try {
        return new Date(value).toISOString();
      } catch (_error) {
        return null;
      }
    }

    list(eventSlug) {
      if (encodedEventSlug(eventSlug) === null) return [];
      const result = this._read();
      if (!result.available) return [];
      return result.entries.filter((entry) => entry.eventSlug === eventSlug);
    }

    save({ eventSlug, resultPath } = {}) {
      if (!isCanonicalSelfieSearchResultPath(eventSlug, resultPath)) {
        return { saved: false, reason: 'invalid_entry' };
      }
      const openedAt = this._openedAt();
      if (openedAt === null) return { saved: false, reason: 'invalid_timestamp' };

      const result = this._read();
      if (!result.available) return { saved: false, reason: 'storage_unavailable' };
      const entries = result.entries.filter(
        (entry) => !(entry.eventSlug === eventSlug && entry.resultPath === resultPath),
      );
      entries.push({ eventSlug, resultPath, openedAt });
      if (!this._write(entries)) return { saved: false, reason: 'storage_unavailable' };
      return { saved: true };
    }

    remove({ eventSlug, resultPath } = {}) {
      if (!isCanonicalSelfieSearchResultPath(eventSlug, resultPath)) {
        return { removed: false, reason: 'invalid_entry' };
      }

      const result = this._read();
      if (!result.available) return { removed: false, reason: 'storage_unavailable' };
      const entries = result.entries.filter(
        (entry) => !(entry.eventSlug === eventSlug && entry.resultPath === resultPath),
      );
      if (!this._write(entries)) return { removed: false, reason: 'storage_unavailable' };
      return { removed: true };
    }
  }

  function formatOpenedAt(openedAt) {
    try {
      return new Date(openedAt).toLocaleString();
    } catch (_error) {
      return '';
    }
  }

  function createElement(document, tagName, { className = '', text = '', type = '' } = {}) {
    const element = document.createElement(tagName);
    if (className) element.className = className;
    if (text !== '') element.textContent = text;
    if (type) element.type = type;
    return element;
  }

  function createTrashIcon(document) {
    const createSvgElement = (tagName) =>
      document.createElementNS?.('http://www.w3.org/2000/svg', tagName) || document.createElement(tagName);
    const icon = createSvgElement('svg');
    icon.setAttribute?.('class', 'icon');
    icon.setAttribute?.('viewBox', '0 0 24 24');
    icon.setAttribute?.('aria-hidden', 'true');
    const use = createSvgElement('use');
    let spriteHref = '/static/ui/icons.svg';
    try {
      const existingHref = document
        .querySelector?.('use[href*="icons"][href*="#"]')
        ?.getAttribute?.('href');
      if (typeof existingHref === 'string' && existingHref.includes('#')) {
        spriteHref = existingHref.slice(0, existingHref.indexOf('#'));
      }
    } catch (_error) {
      // The shared sprite is optional enhancement state for the history control.
    }
    use.setAttribute?.('href', `${spriteHref}#trash`);
    icon.append(use);
    return icon;
  }

  function startEventHistory(document, window, root, store, format) {
    const eventSlug = root?.dataset?.eventSlug;
    const list = root?.querySelector?.('[data-selfie-search-history-list]');
    if (!list || typeof eventSlug !== 'string') return;

    let openButtons = [];
    const render = () => {
      const entries = store.list(eventSlug);
      const previousDetails = list.querySelector?.('details');
      const wasOpen = Boolean(previousDetails?.open);
      list.replaceChildren?.();
      openButtons = [];
      if (entries.length === 0) {
        root.hidden = true;
        return;
      }

      root.hidden = false;
      const details = createElement(document, 'details', {
        className: 'selfie-search-history-disclosure-control',
      });
      details.open = wasOpen;
      const summary = createElement(document, 'summary', {
        className: 'selfie-search-history-summary',
        text: `Мои результаты поиска · ${entries.length}`,
      });
      const body = createElement(document, 'div', {
        className: 'selfie-search-history-body',
      });
      body.append(
        createElement(document, 'p', {
          className: 'selfie-search-history-disclosure',
          text: 'Ссылки сохранены только в этом браузере. Любой, у кого есть ссылка, сможет открыть результат.',
        }),
      );
      details.append(summary, body);
      list.append(details);

      entries.forEach((entry, index) => {
        const row = createElement(document, 'div', { className: 'selfie-search-history-row' });
        const label = format(entry.openedAt);
        const open = createElement(document, 'button', {
          className: 'selfie-search-history-open',
          type: 'button',
          text: label,
        });
        open.setAttribute?.('aria-label', `Открыть результат от ${label}`);
        const remove = createElement(document, 'button', {
          className: 'selfie-search-history-remove',
          type: 'button',
        });
        remove.setAttribute?.('aria-label', 'Удалить результат с устройства');
        remove.setAttribute?.('title', 'Удалить результат с устройства');
        remove.append(createTrashIcon(document));

        open.addEventListener('click', () => {
          if (!isCanonicalSelfieSearchResultPath(eventSlug, entry.resultPath)) return;
          try {
            window?.location?.assign?.(entry.resultPath);
          } catch (_error) {
            // A locally saved link is optional enhancement state.
          }
        });
        remove.addEventListener('click', () => {
          const outcome = store.remove({ eventSlug, resultPath: entry.resultPath });
          if (!outcome.removed) return;
          render();
          const nextControl = openButtons[Math.min(index, openButtons.length - 1)];
          if (nextControl) {
            nextControl.focus();
            return;
          }
          document.querySelector?.('[data-selfie-search-form] button[type="submit"]')?.focus?.();
        });

        row.append(open, remove);
        body.append(row);
        openButtons.push(open);
      });
    };
    render();
  }

  function startSelfieSearchHistory(document, window, options = {}) {
    const storage = options.store || new SelfieSearchHistoryStore({
      localStorage: safeObjectValue(window, 'localStorage'),
      now: options.now,
    });
    const result = document?.querySelector?.('[data-selfie-search-result]');
    if (result) {
      const eventSlug = result.dataset?.eventSlug;
      const resultPath = safeObjectValue(safeObjectValue(window, 'location'), 'pathname');
      try {
        storage.save({ eventSlug, resultPath });
      } catch (_error) {
        // Result viewing must stay usable when browser storage is unavailable.
      }
      return;
    }

    const root = document?.querySelector?.('[data-selfie-search-history]');
    if (!root) return;
    try {
      startEventHistory(document, window, root, storage, options.formatOpenedAt || formatOpenedAt);
    } catch (_error) {
      root.hidden = true;
    }
  }

  return {
    HISTORY_STORAGE_KEY,
    SelfieSearchHistoryStore,
    isCanonicalSelfieSearchResultPath,
    startSelfieSearchHistory,
  };
});

if (typeof document !== 'undefined') {
  const start = () => globalThis.FindMeSelfieSearchHistory.startSelfieSearchHistory(document, globalThis);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}
