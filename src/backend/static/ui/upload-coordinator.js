(function uploadCoordinatorModule(globalScope, factory) {
  const api = factory(globalScope);
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  if (globalScope) {
    globalScope.FindMeUpload = api;
  }
})(typeof globalThis === 'undefined' ? this : globalThis, function buildUploadCoordinator(globalScope) {
  'use strict';

  class SelectionError extends Error {}

  function prepareSelection(files, { maxFiles, maxFileBytes, crypto }) {
    const selected = Array.from(files || []);
    if (selected.length < 1 || selected.length > maxFiles) {
      throw new SelectionError(`Выберите от 1 до ${maxFiles} файлов.`);
    }
    for (const file of selected) {
      if (file.type !== 'image/jpeg') {
        throw new SelectionError('Можно загружать только JPEG-файлы.');
      }
      if (file.size < 1 || file.size > maxFileBytes) {
        throw new SelectionError(`Размер каждого файла должен быть от 1 до ${maxFileBytes} байт.`);
      }
    }
    return {
      items: selected.map((file) => ({ clientItemId: crypto.randomUUID(), file })),
    };
  }

  function chunkItems(items, size) {
    const chunks = [];
    for (let index = 0; index < items.length; index += size) {
      chunks.push(items.slice(index, index + size));
    }
    return chunks;
  }

  class ControlError extends Error {
    constructor(status, payload) {
      super(payload?.error?.message || 'Сервер временно недоступен.');
      this.status = status;
      this.payload = payload;
    }
  }

  function interpolate(template, values) {
    return Object.entries(values).reduce(
      (url, [key, value]) => url.replaceAll(`{${key}}`, encodeURIComponent(value)),
      template,
    );
  }

  function retryableTransfer(status) {
    return status === 0 || status === 408 || status === 429 || status >= 500;
  }

  function visibleItems(items, windowSize) {
    return items.slice(Math.max(0, items.length - windowSize));
  }

  function summarize(items) {
    const totalBytes = items.reduce((sum, item) => sum + item.file.size, 0);
    const completedBytes = items.reduce(
      (sum, item) => sum + item.file.size * ((item.progress || 0) / 100),
      0,
    );
    return {
      total: items.length,
      uploaded: items.filter((item) => item.status === 'uploaded').length,
      failed: items.filter((item) => item.status === 'failed').length,
      totalBytes,
      progress: totalBytes ? Math.round((completedBytes / totalBytes) * 100) : 0,
    };
  }

  function formatBytes(bytes) {
    if (bytes < 1024) return `${bytes} Б`;
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} КБ`;
    return `${(bytes / (1024 * 1024)).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} МБ`;
  }

  function statusCopy(item) {
    if (item.status === 'uploaded') return 'Загружено';
    if (item.status === 'failed') return 'Ошибка';
    if (item.status === 'uploading') return `Передача · ${item.progress}%`;
    return 'Ожидает';
  }

  function renderPage(root, coordinator, globalError = '') {
    const summary = summarize(coordinator.items);
    const terminal = summary.uploaded + summary.failed === summary.total && summary.total > 0;
    const title = root.querySelector('#upload-summary-title');
    const message = root.querySelector('[data-summary-message]');
    const percent = root.querySelector('[data-summary-percent]');
    const progress = root.querySelector('[data-upload-progress]');
    root.dataset.state = globalError ? 'partial' : coordinator.active ? 'active' : terminal && summary.failed ? 'partial' : terminal ? 'complete' : 'empty';
    if (title) {
      title.textContent = globalError
        ? 'Загрузка остановлена'
        : coordinator.active
          ? 'Идёт загрузка'
          : terminal && summary.failed
            ? 'Загружено частично'
            : terminal
              ? 'Загрузка завершена'
              : 'Файлы не выбраны';
    }
    if (message) {
      message.textContent = globalError || (summary.total
        ? `${summary.uploaded} из ${summary.total} файлов загружено${summary.failed ? `, ошибок: ${summary.failed}` : '.'}`
        : 'Здесь появится общий прогресс.');
    }
    if (percent) percent.textContent = `${summary.progress}%`;
    if (progress) {
      progress.value = summary.progress;
      progress.textContent = `${summary.progress}%`;
    }
    const values = {
      '[data-total-count]': summary.total,
      '[data-uploaded-count]': summary.uploaded,
      '[data-failed-count]': summary.failed,
      '[data-total-bytes]': formatBytes(summary.totalBytes),
    };
    for (const [selector, value] of Object.entries(values)) {
      const node = root.querySelector(selector);
      if (node) node.textContent = String(value);
    }

    const queue = root.querySelector('[data-upload-queue]');
    const template = root.querySelector('#upload-queue-row-template');
    if (!queue || !template || !coordinator.items.length) return;
    queue.replaceChildren();
    for (const item of visibleItems(coordinator.items, coordinator.config.queueWindow)) {
      const row = template.content.firstElementChild.cloneNode(true);
      row.dataset.clientItemId = item.clientItemId;
      row.classList.add(`queue-item-${item.status === 'uploading' ? 'active' : item.status}`);
      row.querySelector('[data-file-name]').textContent = item.file.name;
      row.querySelector('[data-file-meta]').textContent = formatBytes(item.file.size);
      row.querySelector('[data-file-status]').textContent = statusCopy(item);
      const itemProgress = row.querySelector('progress');
      itemProgress.value = item.progress;
      itemProgress.textContent = `${item.progress}%`;
      const error = row.querySelector('[data-file-error]');
      error.textContent = item.error;
      error.hidden = !item.error;
      const retry = row.querySelector('[data-retry-item]');
      retry.hidden = item.status !== 'failed';
      retry.dataset.clientItemId = item.clientItemId;
      const cancel = row.querySelector('[data-cancel-item]');
      cancel.hidden = item.status !== 'uploading';
      cancel.dataset.clientItemId = item.clientItemId;
      queue.append(row);
    }
  }

  function bindUploadPage(root, dependencies = {}) {
    if (!root) return null;
    let globalError = '';
    const config = {
      createBatchUrl: root.dataset.createBatchUrl,
      registerUrl: root.dataset.registerUrlTemplate,
      authorizeUrl: root.dataset.authorizeUrlTemplate,
      retryUrl: root.dataset.retryUrlTemplate,
      confirmUrl: root.dataset.confirmUrlTemplate,
      failedUrl: root.dataset.failedUrlTemplate,
      finalizeUrl: root.dataset.finalizeUrlTemplate,
      csrfToken: root.dataset.csrfToken,
      maxFiles: Number(root.dataset.maxFiles),
      maxFileBytes: Number(root.dataset.maxFileBytes),
      registrationChunk: Number(root.dataset.registrationChunk),
      concurrency: Number(root.dataset.concurrency),
      queueWindow: Number(root.dataset.queueWindowSize),
    };
    const coordinator = new UploadCoordinator({
      config,
      fetch: dependencies.fetch || globalScope.fetch.bind(globalScope),
      XMLHttpRequest: dependencies.XMLHttpRequest || globalScope.XMLHttpRequest,
      FormData: dependencies.FormData || globalScope.FormData,
      crypto: dependencies.crypto || globalScope.crypto,
      setTimeout: dependencies.setTimeout || globalScope.setTimeout.bind(globalScope),
      onChange: () => renderPage(root, coordinator, globalError),
    });
    const input = root.querySelector('#upload-files');
    const eventSelect = root.querySelector('#upload-event');
    const begin = async (files) => {
      globalError = '';
      if (!eventSelect.value) {
        globalError = 'Сначала выберите событие.';
        renderPage(root, coordinator, globalError);
        eventSelect.focus();
        return;
      }
      try {
        await coordinator.start(files, eventSelect.value);
      } catch (error) {
        globalError = error instanceof SelectionError ? error.message : 'Не удалось продолжить загрузку. Повторите попытку.';
        coordinator.active = false;
        renderPage(root, coordinator, globalError);
      }
    };
    input?.addEventListener('change', () => begin(input.files));
    const dropTarget = root.querySelector('[data-upload-drop-target]');
    dropTarget?.addEventListener('dragover', (event) => event.preventDefault());
    dropTarget?.addEventListener('drop', (event) => {
      event.preventDefault();
      begin(event.dataTransfer.files);
    });
    root.querySelector('[data-upload-queue]')?.addEventListener('click', async (event) => {
      const retry = event.target.closest('[data-retry-item]');
      const cancel = event.target.closest('[data-cancel-item]');
      if (retry) {
        await coordinator.manualRetry(retry.dataset.clientItemId);
      } else if (cancel) {
        coordinator.cancel(cancel.dataset.clientItemId);
      }
    });
    globalScope.addEventListener?.('beforeunload', (event) => {
      if (coordinator.shouldWarnBeforeUnload()) {
        event.preventDefault();
        event.returnValue = '';
      }
    });
    root.uploadCoordinator = coordinator;
    return coordinator;
  }

  class UploadCoordinator {
    constructor(options) {
      this.config = options.config;
      this.fetch = options.fetch;
      this.XMLHttpRequest = options.XMLHttpRequest;
      this.FormData = options.FormData;
      this.crypto = options.crypto;
      this.setTimeout = options.setTimeout;
      this.onChange = options.onChange || (() => {});
      this.items = [];
      this.batchId = null;
      this.active = false;
      this.registeredAll = false;
      this.finalizing = false;
      this.finalized = false;
      this.nextIndex = 0;
    }

    async start(files, eventId) {
      const selection = prepareSelection(files, {
        maxFiles: this.config.maxFiles,
        maxFileBytes: this.config.maxFileBytes,
        crypto: this.crypto,
      });
      this.items = selection.items.map((entry) => ({
        ...entry,
        id: null,
        status: 'pending',
        progress: 0,
        error: '',
        xhr: null,
        cycle: 0,
      }));
      this.active = true;
      this.onChange(this);

      const created = await this.control(this.config.createBatchUrl, {
        event_id: Number(eventId),
        expected_item_count: this.items.length,
      });
      this.batchId = created.batch.id;

      for (const group of chunkItems(this.items, Math.min(this.config.registrationChunk, 100))) {
        const registered = await this.control(
          interpolate(this.config.registerUrl, { batch: this.batchId }),
          {
            items: group.map((item) => ({
              client_item_id: item.clientItemId,
              filename: item.file.name,
              content_type: item.file.type,
              size: item.file.size,
            })),
          },
        );
        const byClientId = new Map(
          registered.items.map((item) => [item.client_item_id, item]),
        );
        for (const item of group) {
          item.id = byClientId.get(item.clientItemId).id;
          item.status = 'registered';
        }
        this.onChange(this);
      }
      this.registeredAll = true;
      this.nextIndex = 0;
      const workers = Array.from(
        { length: Math.min(this.config.concurrency, 4, this.items.length) },
        () => this.worker(),
      );
      await Promise.all(workers);
      await this.finalizeIfReady();
      return this;
    }

    async worker() {
      while (this.nextIndex < this.items.length) {
        const item = this.items[this.nextIndex++];
        await this.processItem(item);
      }
    }

    async processItem(item, initialGrant = null) {
      let grant = initialGrant;
      let dataAttempt = 0;
      let refreshed = false;
      while (dataAttempt < 4) {
        if (!grant) {
          const authorization = await this.control(
            interpolate(this.config.authorizeUrl, { batch: this.batchId, item: item.id }),
            { reason: 'data_attempt' },
          );
          grant = authorization.grant;
        }
        item.status = 'uploading';
        item.error = '';
        this.onChange(this);
        const outcome = await this.transfer(item, grant);
        grant = null;
        if (outcome.type === 'cancelled') {
          await this.failItem(item, 'transfer_cancelled', 'Передача отменена.');
          return;
        }
        if (outcome.status >= 200 && outcome.status < 300) {
          await this.control(
            interpolate(this.config.confirmUrl, { batch: this.batchId, item: item.id }),
            {},
          );
          item.status = 'uploaded';
          item.progress = 100;
          item.error = '';
          this.onChange(this);
          return;
        }
        if (outcome.status === 403 && !refreshed) {
          refreshed = true;
          const authorization = await this.control(
            interpolate(this.config.authorizeUrl, { batch: this.batchId, item: item.id }),
            { reason: 'grant_refresh' },
          );
          grant = authorization.grant;
          continue;
        }
        if (outcome.status === 403 || !retryableTransfer(outcome.status)) {
          await this.failItem(item, 'transfer_retries_exhausted', 'Не удалось передать файл.');
          return;
        }
        dataAttempt += 1;
        if (dataAttempt >= 4) {
          await this.failItem(
            item,
            'transfer_retries_exhausted',
            'Не удалось передать файл после четырёх попыток.',
          );
          return;
        }
        await new Promise((resolve) => this.setTimeout(resolve, [1000, 3000, 7000][dataAttempt - 1]));
      }
    }

    transfer(item, grant) {
      return new Promise((resolve) => {
        const xhr = new this.XMLHttpRequest();
        item.xhr = xhr;
        xhr.open('POST', grant.url);
        xhr.timeout = 120000;
        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable) {
            item.progress = Math.round((event.loaded / event.total) * 100);
            this.onChange(this);
          }
        };
        xhr.onload = () => {
          item.xhr = null;
          resolve({ type: 'load', status: xhr.status });
        };
        xhr.onerror = () => {
          item.xhr = null;
          resolve({ type: 'error', status: 0 });
        };
        xhr.ontimeout = () => {
          item.xhr = null;
          resolve({ type: 'timeout', status: 0 });
        };
        xhr.onabort = () => {
          item.xhr = null;
          resolve({ type: 'cancelled', status: 0 });
        };
        const form = new this.FormData();
        for (const [key, value] of Object.entries(grant.fields)) {
          form.append(key, value);
        }
        form.append('file', item.file);
        xhr.send(form);
      });
    }

    async failItem(item, code, message) {
      await this.control(
        interpolate(this.config.failedUrl, { batch: this.batchId, item: item.id }),
        { code },
      );
      item.status = 'failed';
      item.error = message;
      this.onChange(this);
    }

    cancel(clientItemId) {
      const item = this.items.find((candidate) => candidate.clientItemId === clientItemId);
      if (item?.xhr) {
        item.xhr.abort();
        return true;
      }
      return false;
    }

    async manualRetry(clientItemId) {
      const item = this.items.find((candidate) => candidate.clientItemId === clientItemId);
      if (!item || item.status !== 'failed') {
        return false;
      }
      this.active = true;
      this.finalized = false;
      item.progress = 0;
      item.error = '';
      const authorization = await this.control(
        interpolate(this.config.retryUrl, { batch: this.batchId, item: item.id }),
        {},
      );
      await this.processItem(item, authorization.grant);
      await this.finalizeIfReady();
      return true;
    }

    shouldWarnBeforeUnload() {
      return this.active;
    }

    async control(url, body) {
      const result = await this.fetch(url, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': this.config.csrfToken,
        },
        body: JSON.stringify(body),
      });
      const payload = await result.json();
      if (!result.ok) {
        throw new ControlError(result.status, payload);
      }
      return payload;
    }

    async finalizeIfReady() {
      const terminal = this.items.every((item) => ['uploaded', 'failed'].includes(item.status));
      if (!this.registeredAll || !terminal || this.finalizing || this.finalized) {
        return;
      }
      this.finalizing = true;
      try {
        await this.control(interpolate(this.config.finalizeUrl, { batch: this.batchId }), {});
        this.finalized = true;
        this.active = false;
        this.onChange(this);
      } finally {
        this.finalizing = false;
      }
    }
  }

  return {
    chunkItems,
    ControlError,
    prepareSelection,
    retryableTransfer,
    SelectionError,
    UploadCoordinator,
    bindUploadPage,
    renderPage,
    summarize,
    visibleItems,
  };
});

if (typeof document !== 'undefined') {
  const start = () => globalThis.FindMeUpload.bindUploadPage(document.querySelector('[data-upload-root]'));
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}
