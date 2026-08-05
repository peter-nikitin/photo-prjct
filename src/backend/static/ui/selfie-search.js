(function selfieSearchModule(globalScope, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (globalScope) globalScope.FindMeSelfieSearch = api;
})(typeof globalThis === 'undefined' ? this : globalThis, function buildSelfieSearch() {
  'use strict';

  const ACTIVE_STATES = new Set(['queued', 'processing', 'cleanup_pending']);
  const POLL_DELAY_MS = 2000;
  const PROCESS_RETRY_DELAY_MS = 2000;
  const MAX_BACKOFF_MS = 30000;
  const FEEDBACK_PENDING_KEY = 'findme_selfie_feedback_pending';
  const FEEDBACK_MARKS_PREFIX = 'findme_selfie_feedback_marks:';
  const FEEDBACK_DB_NAME = 'findme-photo-feedback';
  const FEEDBACK_DB_VERSION = 1;
  const FEEDBACK_STORE_NAME = 'selfie-searches';
  const SELFIE_RETENTION_MS = 7 * 24 * 60 * 60 * 1000;
  const SHA256_HEX = /^[0-9a-f]{64}$/i;
  const FEEDBACK_CORRELATION = /^[A-Za-z0-9_-]{32,64}$/;

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

  function canonicalMediaType(file) {
    const type = typeof file?.type === 'string' ? file.type.trim().toLowerCase() : '';
    if (type === 'image/jpeg' || type === 'image/jpg' || type === 'image/pjpeg') {
      return 'image/jpeg';
    }
    if (type === 'image/png') return 'image/png';
    if (type === 'image/heic') return 'image/heic';
    if (type === 'image/heif') return 'image/heif';
    const name = typeof file?.name === 'string' ? file.name.toLowerCase() : '';
    if (/\.(jpe?g)$/.test(name)) return 'image/jpeg';
    if (/\.png$/.test(name)) return 'image/png';
    if (/\.heic$/.test(name)) return 'image/heic';
    if (/\.heif$/.test(name)) return 'image/heif';
    return '';
  }

  async function fileBytes(file) {
    if (!file) throw new Error('No selfie file.');
    let value;
    if (typeof file.arrayBuffer === 'function') value = await file.arrayBuffer();
    else if (file.bytes !== undefined) value = file.bytes;
    else if (file.data !== undefined) value = file.data;
    else throw new Error('Selfie bytes are unavailable.');
    if (value instanceof ArrayBuffer) return value.slice(0);
    if (ArrayBuffer.isView(value)) {
      return value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength);
    }
    throw new Error('Selfie bytes are unavailable.');
  }

  function bytesToHex(value) {
    const bytes = value instanceof Uint8Array ? value : new Uint8Array(value);
    return [...bytes].map((byte) => byte.toString(16).padStart(2, '0')).join('');
  }

  function normalizedDigest(value) {
    return typeof value === 'string' && SHA256_HEX.test(value) ? value.toLowerCase() : '';
  }

  function normalizedFeedbackCorrelation(value) {
    return typeof value === 'string' && FEEDBACK_CORRELATION.test(value) ? value : '';
  }

  function randomHandle(crypto) {
    if (crypto && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
    if (crypto && typeof crypto.getRandomValues === 'function') {
      const bytes = crypto.getRandomValues(new Uint8Array(24));
      return bytesToHex(bytes);
    }
    throw new Error('A secure random source is unavailable.');
  }

  function idbRequest(request, transaction) {
    if (request && typeof request.then === 'function') return request;
    return new Promise((resolve, reject) => {
      let settled = false;
      const succeed = (value) => {
        if (settled) return;
        settled = true;
        resolve(value);
      };
      const fail = (error) => {
        if (settled) return;
        settled = true;
        reject(error || new Error('IndexedDB operation failed.'));
      };
      try {
        request.onsuccess = () => succeed(request.result);
        request.onerror = () => fail(request.error);
        if (transaction) transaction.onerror = () => fail(transaction.error);
      } catch (error) {
        fail(error);
      }
    });
  }

  function openIndexedDb(indexedDB) {
    return new Promise((resolve, reject) => {
      let request;
      try {
        request = indexedDB.open(FEEDBACK_DB_NAME, FEEDBACK_DB_VERSION);
        request.onupgradeneeded = () => {
          const database = request.result;
          const names = database.objectStoreNames;
          const hasStore =
            names &&
            (typeof names.contains === 'function'
              ? names.contains(FEEDBACK_STORE_NAME)
              : Array.from(names).includes(FEEDBACK_STORE_NAME));
          if (!hasStore) database.createObjectStore(FEEDBACK_STORE_NAME, { keyPath: 'handle' });
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error('IndexedDB is unavailable.'));
        request.onblocked = () => reject(new Error('IndexedDB is blocked.'));
      } catch (error) {
        reject(error);
      }
    });
  }

  class BrowserStorageAdapter {
    constructor({
      db = null,
      indexedDB = safeGlobalValue('indexedDB'),
      sessionStorage = safeGlobalValue('sessionStorage'),
      crypto = safeGlobalValue('crypto'),
      now = browserNow,
      randomUUID = null,
      sha256 = null,
      pendingKey = FEEDBACK_PENDING_KEY,
    } = {}) {
      this.db = db;
      this.indexedDB = indexedDB;
      this.sessionStorage = sessionStorage;
      this.crypto = crypto;
      this.now = now;
      this.randomUUID = randomUUID || (() => randomHandle(this.crypto));
      this.sha256 = sha256;
      this.pendingKey = pendingKey;
      this._databasePromise = null;
    }

    createFeedbackCorrelation() {
      return normalizedFeedbackCorrelation(this.randomUUID());
    }

    getPendingHandle() {
      if (!this.sessionStorage || typeof this.sessionStorage.getItem !== 'function') return null;
      try {
        return this.sessionStorage.getItem(this.pendingKey) || null;
      } catch (_error) {
        return null;
      }
    }

    _setPendingHandle(handle) {
      if (!this.sessionStorage || typeof this.sessionStorage.setItem !== 'function') {
        throw new Error('sessionStorage is unavailable.');
      }
      this.sessionStorage.setItem(this.pendingKey, handle);
    }

    _clearPendingHandle() {
      if (!this.sessionStorage || typeof this.sessionStorage.removeItem !== 'function') return;
      try {
        this.sessionStorage.removeItem(this.pendingKey);
      } catch (_error) {
        // Storage cleanup is best effort. It must never block the normal search.
      }
    }

    async _database() {
      if (this.db) return this.db;
      if (!this.indexedDB || typeof this.indexedDB.open !== 'function') {
        throw new Error('IndexedDB is unavailable.');
      }
      if (!this._databasePromise) this._databasePromise = openIndexedDb(this.indexedDB);
      return this._databasePromise;
    }

    async _get(handle) {
      if (this.db) {
        if (typeof this.db.get !== 'function') throw new Error('Database read is unavailable.');
        return this.db.get(handle);
      }
      const database = await this._database();
      const transaction = database.transaction(FEEDBACK_STORE_NAME, 'readonly');
      return idbRequest(transaction.objectStore(FEEDBACK_STORE_NAME).get(handle), transaction);
    }

    async _getAll() {
      if (this.db) {
        if (typeof this.db.getAll !== 'function') throw new Error('Database read is unavailable.');
        return this.db.getAll();
      }
      const database = await this._database();
      const transaction = database.transaction(FEEDBACK_STORE_NAME, 'readonly');
      const store = transaction.objectStore(FEEDBACK_STORE_NAME);
      if (typeof store.getAll === 'function') return idbRequest(store.getAll(), transaction);
      return new Promise((resolve, reject) => {
        const values = [];
        let request;
        try {
          request = store.openCursor();
          request.onsuccess = () => {
            const cursor = request.result;
            if (!cursor) {
              resolve(values);
              return;
            }
            values.push(cursor.value);
            cursor.continue();
          };
          request.onerror = () => reject(request.error || new Error('IndexedDB read failed.'));
        } catch (error) {
          reject(error);
        }
      });
    }

    async _put(record) {
      if (this.db) {
        if (typeof this.db.put !== 'function') throw new Error('Database write is unavailable.');
        return this.db.put(record);
      }
      const database = await this._database();
      const transaction = database.transaction(FEEDBACK_STORE_NAME, 'readwrite');
      return idbRequest(transaction.objectStore(FEEDBACK_STORE_NAME).put(record), transaction);
    }

    async _delete(handle) {
      if (this.db) {
        if (typeof this.db.delete !== 'function') throw new Error('Database delete is unavailable.');
        return this.db.delete(handle);
      }
      const database = await this._database();
      const transaction = database.transaction(FEEDBACK_STORE_NAME, 'readwrite');
      return idbRequest(transaction.objectStore(FEEDBACK_STORE_NAME).delete(handle), transaction);
    }

    async preserveSelectedSelfie(file, { correlation = '' } = {}) {
      if (!file) return { stored: false, reason: 'no_file' };

      let bytes;
      try {
        bytes = await fileBytes(file);
        const handle = this.randomUUID();
        const createdAt = Number(this.now());
        const record = {
          handle,
          bytes,
          mediaType: canonicalMediaType(file),
          byteCount: bytes.byteLength,
          createdAt,
          expiresAt: createdAt + SELFIE_RETENTION_MS,
          resultTokenDigest: '',
          feedbackCorrelation: normalizedFeedbackCorrelation(correlation),
        };
        await this._put(record);
        try {
          this._setPendingHandle(handle);
        } catch (error) {
          try {
            await this._delete(handle);
          } catch (_deleteError) {
            // The feature remains fail-closed if this compensating delete is unavailable.
          }
          throw error;
        }
        return { stored: true, handle, expiresAt: record.expiresAt };
      } catch (_error) {
        return { stored: false, reason: 'storage_unavailable' };
      }
    }

    async digestPublicToken(token) {
      if (this.sha256) return normalizedDigest(await this.sha256(token));
      const subtle = this.crypto && this.crypto.subtle;
      if (!subtle || typeof subtle.digest !== 'function') return '';
      const encoder = typeof TextEncoder === 'function' ? new TextEncoder() : null;
      if (!encoder) return '';
      const digest = await subtle.digest('SHA-256', encoder.encode(token));
      return bytesToHex(digest);
    }

    async associatePendingSelfie({ resultDigest = '', resultToken = '', correlation = '' } = {}) {
      let digest = normalizedDigest(resultDigest);
      const normalizedCorrelation = normalizedFeedbackCorrelation(correlation);
      try {
        if (!digest && resultToken) digest = await this.digestPublicToken(resultToken);
      } catch (_error) {
        return { associated: false, reason: 'storage_unavailable' };
      }
      if (!digest || !normalizedCorrelation) {
        return { associated: false, reason: 'missing_result_identity' };
      }
      const handle = this.getPendingHandle();
      if (!handle) return { associated: false, reason: 'missing_pending_handle' };
      try {
        const record = await this._get(handle);
        if (!record) {
          this._clearPendingHandle();
          return { associated: false, reason: 'missing_record' };
        }
        if (record.feedbackCorrelation !== normalizedCorrelation) {
          return { associated: false, reason: 'correlation_mismatch' };
        }
        if (!Number.isFinite(record.expiresAt) || record.expiresAt <= Number(this.now())) {
          await this._delete(handle);
          this._clearPendingHandle();
          return { associated: false, reason: 'expired' };
        }
        await this._put({ ...record, resultTokenDigest: digest });
        this._clearPendingHandle();
        return { associated: true, handle, resultDigest: digest };
      } catch (_error) {
        return { associated: false, reason: 'storage_unavailable' };
      }
    }

    async getSelfie(handle) {
      if (!handle) return null;
      const record = await this._get(handle);
      if (!record) return null;
      if (!Number.isFinite(record.expiresAt) || record.expiresAt <= Number(this.now())) {
        await this._delete(handle);
        if (this.getPendingHandle() === handle) this._clearPendingHandle();
        return null;
      }
      return record;
    }

    async getAssociatedSelfie(resultDigest) {
      const digest = normalizedDigest(resultDigest);
      if (!digest) return null;
      try {
        const records = await this._getAll();
        for (const record of records || []) {
          if (record.resultTokenDigest !== digest) continue;
          if (!Number.isFinite(record.expiresAt) || record.expiresAt <= Number(this.now())) {
            await this._delete(record.handle);
            return null;
          }
          return record;
        }
      } catch (_error) {
        return null;
      }
      return null;
    }

    async clearSelfie(handle) {
      if (!handle) return false;
      try {
        await this._delete(handle);
        if (this.getPendingHandle() === handle) this._clearPendingHandle();
        return true;
      } catch (_error) {
        return false;
      }
    }

    async discardPendingSelfie() {
      const handle = this.getPendingHandle();
      if (!handle) return true;
      const cleared = await this.clearSelfie(handle);
      if (!cleared) this._clearPendingHandle();
      return cleared;
    }

    async clearAssociatedSelfie(resultDigest) {
      const digest = normalizedDigest(resultDigest);
      if (!digest) return false;
      try {
        const records = await this._getAll();
        const record = (records || []).find((candidate) => candidate.resultTokenDigest === digest);
        if (!record) return true;
        return this.clearSelfie(record.handle);
      } catch (_error) {
        return false;
      }
    }

    async cleanupExpired() {
      try {
        const records = await this._getAll();
        let removed = 0;
        const currentTime = Number(this.now());
        for (const record of records || []) {
          if (!Number.isFinite(record.expiresAt) || record.expiresAt > currentTime) continue;
          await this._delete(record.handle);
          if (this.getPendingHandle() === record.handle) this._clearPendingHandle();
          removed += 1;
        }
        return removed;
      } catch (_error) {
        return 0;
      }
    }

    async clearAll() {
      let cleared = 0;
      try {
        const records = await this._getAll();
        for (const record of records || []) {
          try {
            await this._delete(record.handle);
            cleared += 1;
          } catch (_error) {
            // Continue clearing every accessible record.
          }
        }
      } catch (_error) {
        // IndexedDB can be unavailable while sessionStorage is still writable.
      }
      this._clearPendingHandle();
      try {
        const markKeys = [];
        for (let index = 0; index < (this.sessionStorage?.length || 0); index += 1) {
          const key = this.sessionStorage.key(index);
          if (typeof key === 'string' && key.startsWith(FEEDBACK_MARKS_PREFIX)) markKeys.push(key);
        }
        markKeys.forEach((key) => this.sessionStorage.removeItem(key));
      } catch (_error) {
        // Cleanup remains best effort when browser key-value storage is unavailable.
      }
      return cleared;
    }

  }

  class FeedbackMarkStore {
    constructor({ sessionStorage, resultDigest }) {
      this.sessionStorage = sessionStorage;
      this.resultDigest = normalizedDigest(resultDigest);
      this.key = `${FEEDBACK_MARKS_PREFIX}${this.resultDigest}`;
    }

    getMarks() {
      if (!this.resultDigest || !this.sessionStorage) return {};
      try {
        const parsed = JSON.parse(this.sessionStorage.getItem(this.key) || '{}');
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
        return Object.fromEntries(
          Object.entries(parsed).filter(([, value]) => value === 'present' || value === 'absent'),
        );
      } catch (_error) {
        return {};
      }
    }

    _save(marks) {
      if (!this.resultDigest || !this.sessionStorage) return false;
      try {
        this.sessionStorage.setItem(this.key, JSON.stringify(marks));
        return true;
      } catch (_error) {
        return false;
      }
    }

    toggle(resultId, value) {
      if (typeof resultId !== 'string' || !['present', 'absent'].includes(value)) return null;
      const marks = this.getMarks();
      if (marks[resultId] === value) delete marks[resultId];
      else marks[resultId] = value;
      this._save(marks);
      return marks[resultId] || null;
    }

    count() {
      return Object.keys(this.getMarks()).length;
    }

    clear() {
      try {
        this.sessionStorage?.removeItem(this.key);
        return true;
      } catch (_error) {
        // The privacy-sensitive storage adapter already handles its own failure paths.
        return false;
      }
    }
  }

  function showFeedbackError(element, message) {
    if (!element) return;
    element.textContent = message;
    element.hidden = !message;
  }

  class FeedbackUiController {
    constructor({ root, storage, resultDigest, window, document }) {
      this.root = root;
      this.storage = storage;
      this.resultDigest = normalizedDigest(resultDigest);
      this.window = window;
      this.document = document || root.ownerDocument;
      this.form = root.querySelector?.('[data-feedback-form]');
      this.progress = root.querySelector?.('[data-feedback-progress]');
      this.contact = root.querySelector?.('[name="contact"]');
      this.consent = root.querySelector?.('[name="personal_data_consent"]');
      this.contactError = root.querySelector?.('[data-feedback-contact-error]');
      this.consentError = root.querySelector?.('[data-feedback-consent-error]');
      this.submitError = root.querySelector?.('[data-feedback-submit-error]');
      this.total = Number(root.dataset.feedbackTotal || 0);
      this.variant = root.dataset.feedbackVariant;
      this.marks = new FeedbackMarkStore({
        sessionStorage: safeObjectValue(this.window, 'sessionStorage') || safeGlobalValue('sessionStorage'),
        resultDigest: this.resultDigest,
      });
    }

    _labelButtons() {
      return this.document?.querySelectorAll?.('[data-feedback-label]') || [];
    }

    bind() {
      this.form?.addEventListener('submit', (event) => this.submit(event));
      this._labelButtons().forEach((button) => {
        button.addEventListener('click', (event) => {
          event.preventDefault();
          event.stopPropagation();
          this.marks.toggle(button.dataset.feedbackResultId, button.dataset.feedbackLabel);
          this.renderMarks();
        });
      });
      this.renderMarks();
    }

    renderMarks() {
      const marks = this.marks.getMarks();
      this._labelButtons().forEach((button) => {
        button.setAttribute(
          'aria-pressed',
          String(marks[button.dataset.feedbackResultId] === button.dataset.feedbackLabel),
        );
      });
      if (this.progress) {
        this.progress.textContent = `Размечено ${Object.keys(marks).length} из ${this.total} фотографий`;
      }
    }

    _clearErrors() {
      showFeedbackError(this.contactError, '');
      showFeedbackError(this.consentError, '');
      showFeedbackError(this.submitError, '');
    }

    async submit(event) {
      event.preventDefault();
      this._clearErrors();
      const contact = this.contact?.value?.trim() || '';
      if (!contact) {
        showFeedbackError(this.contactError, 'Укажите контакт для связи.');
        this.contact?.focus();
        return;
      }
      if (!this.consent?.checked) {
        showFeedbackError(this.consentError, 'Нужно согласие на обработку данных.');
        this.consent?.focus();
        return;
      }
      const selfie = await this.storage.getAssociatedSelfie(this.resultDigest);
      if (!selfie) {
        showFeedbackError(this.submitError, 'Селфи для этого поиска больше недоступно в этом браузере.');
        return;
      }
      const data = new FormData();
      const extension = {
        'image/png': 'png',
        'image/heic': 'heic',
        'image/heif': 'heif',
      }[selfie.mediaType] || 'jpg';
      data.append('selfie', new Blob([selfie.bytes], { type: selfie.mediaType }), `selfie.${extension}`);
      data.append('contact', contact);
      data.append('personal_data_consent', 'true');
      data.append('labels', JSON.stringify(this.variant === 'result_labels' ? this.marks.getMarks() : {}));
      const csrf = this.form?.querySelector?.('[name="csrfmiddlewaretoken"]')?.value;
      if (csrf) data.append('csrfmiddlewaretoken', csrf);
      const submit = this.form?.querySelector?.('[data-feedback-submit]');
      if (submit) submit.disabled = true;
      try {
        const response = await this.window.fetch(this.root.dataset.feedbackUrl, {
          method: 'POST',
          body: data,
          credentials: 'same-origin',
          headers: csrf ? { 'X-CSRFToken': csrf } : {},
        });
        const payload = await response.json().catch(() => ({}));
        if (payload.status === 'result_changed') {
          this.marks.clear();
          this.window.location.reload();
          return;
        }
        if (payload.status === 'submitted' || payload.status === 'already_submitted') {
          const marksCleared = this.marks.clear();
          const selfieCleared = marksCleared
            ? await this.storage.clearAssociatedSelfie(this.resultDigest)
            : false;
          if (!marksCleared || !selfieCleared) {
            showFeedbackError(
              this.submitError,
              'Отзыв сохранён, но локальные данные удалить не удалось. Повторите отправку, чтобы завершить очистку.',
            );
            return;
          }
          this.root.innerHTML = '<p role="status">Спасибо, отзыв отправлен.</p>';
          return;
        }
        showFeedbackError(this.submitError, 'Не удалось отправить отзыв. Проверьте данные и попробуйте ещё раз.');
      } catch (_error) {
        showFeedbackError(this.submitError, 'Не удалось отправить отзыв. Проверьте подключение и попробуйте ещё раз.');
      } finally {
        if (submit) submit.disabled = false;
      }
    }
  }

  async function initializeFeedbackUi({ document, window, storage, result }) {
    const root = document?.querySelector?.('[data-selfie-feedback]');
    if (!root || !result) return null;
    const resultDigest = result.dataset?.resultDigest;
    if (!normalizedDigest(resultDigest)) return null;
    const unavailable = document.querySelector?.('[data-feedback-unavailable]');
    if (root.dataset.feedbackPreview !== 'true') {
      const selfie = await storage.getAssociatedSelfie(resultDigest);
      if (!selfie) {
        if (unavailable) unavailable.hidden = false;
        root.hidden = true;
        return null;
      }
    }
    if (unavailable) unavailable.hidden = true;
    root.hidden = false;
    const controller = new FeedbackUiController({ root, storage, resultDigest, window, document });
    controller.bind();
    return controller;
  }

  class FeedbackCleanupUiController {
    constructor({ root, storage, resultDigest, window }) {
      this.root = root;
      this.storage = storage;
      this.resultDigest = normalizedDigest(resultDigest);
      this.pending = root.querySelector?.('[data-feedback-cleanup-pending]');
      this.error = root.querySelector?.('[data-feedback-cleanup-error]');
      this.success = root.querySelector?.('[data-feedback-cleanup-success]');
      this.marks = new FeedbackMarkStore({
        sessionStorage: safeObjectValue(window, 'sessionStorage') || safeGlobalValue('sessionStorage'),
        resultDigest: this.resultDigest,
      });
    }

    async cleanup() {
      if (this.pending) this.pending.hidden = false;
      showFeedbackError(this.error, '');
      if (this.success) this.success.hidden = true;
      const marksCleared = this.marks.clear();
      let selfieCleared = false;
      if (marksCleared) {
        try {
          selfieCleared = await this.storage.clearAssociatedSelfie(this.resultDigest);
        } catch (_error) {
          selfieCleared = false;
        }
      }
      if (!marksCleared || !selfieCleared) {
        if (this.pending) this.pending.hidden = true;
        showFeedbackError(
          this.error,
          'Отзыв отправлен, но локальные данные удалить не удалось. Повторите очистку.',
        );
        return false;
      }
      if (this.pending) this.pending.hidden = true;
      if (this.success) {
        this.success.textContent = 'Спасибо, отзыв отправлен.';
        this.success.hidden = false;
      }
      return true;
    }

    async bind() {
      await this.cleanup();
    }
  }

  async function initializeFeedbackCleanupUi({ document, window, storage, result }) {
    const root = document?.querySelector?.('[data-feedback-cleanup]');
    const resultDigest = result?.dataset?.resultDigest;
    if (!root || !normalizedDigest(resultDigest)) return null;
    const controller = new FeedbackCleanupUiController({
      root,
      storage,
      resultDigest,
      window,
    });
    await controller.bind();
    return controller;
  }

  function selectedFile(form) {
    if (!form || typeof form.querySelector !== 'function') return null;
    let input = null;
    try {
      input = form.querySelector('input[type="file"]');
    } catch (_error) {
      input = null;
    }
    if (!input) {
      try {
        input = form.querySelector('input[name="selfie"]');
      } catch (_error) {
        input = null;
      }
    }
    return input && input.files && input.files.length ? input.files[0] : null;
  }

  function nativeSubmit(form, event) {
    if (form && typeof form.submit === 'function') {
      form.submit();
      return;
    }
    if (event?.target && typeof event.target.submit === 'function') event.target.submit();
  }

  function bindSelfieSearchForm(form, { storage = null } = {}) {
    if (!form || typeof form.addEventListener !== 'function') return;
    const feedbackEnabled = form.dataset?.selfieFeedbackEnabled === 'true';
    const storageAdapter = feedbackEnabled ? (storage || new BrowserStorageAdapter()) : null;
    let submissionStarted = false;
    form.addEventListener('submit', async (event) => {
      if (event && typeof event.preventDefault === 'function') event.preventDefault();
      const button = typeof form.querySelector === 'function'
        ? form.querySelector('button[type="submit"]')
        : null;
      if (button && button.disabled) return;
      if (submissionStarted) return;
      submissionStarted = true;
      if (button) {
        button.disabled = true;
        button.textContent = 'Ищем фотографии…';
      }
      try {
        const file = selectedFile(form);
        if (file && storageAdapter && typeof storageAdapter.preserveSelectedSelfie === 'function') {
          const correlationInput = form.querySelector?.('[name="feedback_correlation"]');
          let correlation = normalizedFeedbackCorrelation(correlationInput?.value || '');
          if (!correlation && typeof storageAdapter.createFeedbackCorrelation === 'function') {
            correlation = normalizedFeedbackCorrelation(storageAdapter.createFeedbackCorrelation());
            if (correlationInput) correlationInput.value = correlation;
          }
          if (normalizedFeedbackCorrelation(correlation)) {
            await storageAdapter.preserveSelectedSelfie(file, { correlation });
          }
        }
      } catch (_error) {
        // Browser storage is optional. The ordinary selfie search must continue.
      }
      nativeSubmit(form, event);
    });
  }

  function resultIdentity(result, window) {
    const data = result?.dataset || {};
    const digest =
      data.resultDigest || data.resultTokenDigest || data.publicTokenDigest || data.tokenDigest || '';
    const correlation = normalizedFeedbackCorrelation(data.feedbackCorrelation || '');
    if (normalizedDigest(digest)) {
      return { resultDigest: normalizedDigest(digest), correlation };
    }
    const token = data.resultToken || data.publicToken || data.token || '';
    if (token) return { resultToken: token };
    const path = window?.location?.pathname;
    if (typeof path !== 'string') return {};
    const parts = path.split('/').filter(Boolean);
    const marker = parts.lastIndexOf('selfie-search');
    if (marker >= 0 && parts[marker + 1]) return { resultToken: parts[marker + 1] };
    return {};
  }

  async function initializeBrowserStorage({ storage, result, window }) {
    if (typeof storage.cleanupExpired === 'function') await storage.cleanupExpired();
    if (result) {
      const association = await storage.associatePendingSelfie(resultIdentity(result, window));
      if (!association.associated && typeof storage.discardPendingSelfie === 'function') {
        await storage.discardPendingSelfie();
      }
    } else if (typeof storage.discardPendingSelfie === 'function') {
      await storage.discardPendingSelfie();
    }
    return { available: true };
  }

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

  function focusSelfieSearchError(document) {
    const error = document.querySelector('[data-selfie-search-error]');
    if (error && typeof error.focus === 'function') error.focus();
  }

  function submitGallerySearchProcess(form, fetch, setTimeout) {
    if (!form || form.gallerySearchProcessStarted || typeof fetch !== 'function') return;
    form.gallerySearchProcessStarted = true;
    const csrfToken = form.querySelector?.('[name="csrfmiddlewaretoken"]')?.value || '';
    let inFlight = false;
    let completed = false;
    const retry = () => {
      if (completed || inFlight) return;
      inFlight = true;
      Promise.resolve(
        fetch(form.action, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'X-CSRFToken': csrfToken },
        }),
      )
        .then((response) => {
          if (response && response.ok) completed = true;
        })
        .catch(() => {})
        .finally(() => {
          inFlight = false;
          if (!completed && typeof setTimeout === 'function') setTimeout(retry, PROCESS_RETRY_DELAY_MS);
        });
    };
    retry();
  }

  function startBrowserUi(document, window, options = {}) {
    const storage = options.storage || new BrowserStorageAdapter({
      indexedDB: safeObjectValue(window, 'indexedDB'),
      sessionStorage: safeObjectValue(window, 'sessionStorage'),
      crypto: safeObjectValue(window, 'crypto'),
    });
    const form = document.querySelector('[data-selfie-search-form]');
    focusSelfieSearchError(document);
    const result = document.querySelector('[data-selfie-search-result]');
    submitGallerySearchProcess(
      document.querySelector('[data-gallery-search-process]'),
      window.fetch?.bind(window),
      window.setTimeout?.bind(window),
    );
    const feedbackEnabled = (form || result)?.dataset?.selfieFeedbackEnabled === 'true';
    const galleryOrigin = result?.dataset?.galleryOrigin === 'true';
    bindSelfieSearchForm(form, { storage });
    if (!feedbackEnabled && !galleryOrigin) {
      Promise.resolve(storage.clearAll?.()).catch(() => {});
    }
    if (!feedbackEnabled && (!result || !result.dataset.statusUrl)) return null;
    const storageReady = feedbackEnabled
      ? initializeBrowserStorage({ storage, result, window }).catch(() => ({
          available: false,
        }))
      : Promise.resolve({ available: false });
    if (feedbackEnabled) storageReady.then((state) => {
      initializeFeedbackCleanupUi({ document, window, storage, result }).catch(() => {});
      if (state.available) {
        initializeFeedbackUi({ document, window, storage, result }).catch(() => {});
      }
    });
    if (!result || !result.dataset.statusUrl) return null;
    const poller = new SelfieSearchPoller({
      url: result.dataset.statusUrl,
      fetch: window.fetch.bind(window),
      setTimeout: window.setTimeout.bind(window),
      clearTimeout: window.clearTimeout.bind(window),
      reload: () => window.location.reload(),
    }).start();
    poller.storage = storage;
    poller.storageReady = storageReady;
    return poller;
  }

  return {
    BrowserStorageAdapter,
    FEEDBACK_PENDING_KEY,
    FeedbackMarkStore,
    FeedbackCleanupUiController,
    FeedbackUiController,
    SELFIE_RETENTION_MS,
    SelfieSearchPoller,
    bindSelfieSearchForm,
    initializeBrowserStorage,
    initializeFeedbackCleanupUi,
    initializeFeedbackUi,
    focusSelfieSearchError,
    submitGallerySearchProcess,
    startBrowserUi,
  };
});

if (typeof document !== 'undefined') {
  const start = () => globalThis.FindMeSelfieSearch.startBrowserUi(document, globalThis);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}
