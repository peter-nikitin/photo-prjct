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
      const hasJpegExtension = /\.jpe?g$/i.test(file.name);
      if (file.type !== 'image/jpeg' && !(!file.type && hasJpegExtension)) {
        throw new SelectionError('Можно загружать только JPEG-файлы.');
      }
      if (file.size < 1 || file.size > maxFileBytes) {
        throw new SelectionError(`Размер каждого файла должен быть от 1 до ${maxFileBytes} байт.`);
      }
    }
    return {
      items: selected.map((file) => ({
        clientItemId: crypto.randomUUID(),
        contentType: 'image/jpeg',
        file,
      })),
    };
  }

  function normalizedFilename(file) {
    return String(file.name || '').normalize('NFC').toLocaleLowerCase('en-US');
  }

  function lastModifiedMs(file) {
    return Number.isSafeInteger(file.lastModified) && file.lastModified >= 0
      ? file.lastModified
      : null;
  }

  function matchingKey(file) {
    return `${normalizedFilename(file)}\u0000${file.size}\u0000${lastModifiedMs(file)}`;
  }

  function legacyMatchingKey(file) {
    return `${normalizedFilename(file)}\u0000${file.size}`;
  }

  function groupBy(items, key) {
    return items.reduce((groups, item) => {
      const value = key(item);
      const group = groups.get(value) || [];
      group.push(item);
      groups.set(value, group);
      return groups;
    }, new Map());
  }

  function fingerprintHex(bytes) {
    return Array.from(new Uint8Array(bytes), (byte) => byte.toString(16).padStart(2, '0')).join('');
  }

  async function prepareAmbiguousFingerprints(items, subtle, { hashSingletons = false } = {}) {
    const groups = groupBy(items, (item) => matchingKey(item.file));
    await Promise.all(
      Array.from(groups.values()).flatMap((group) => {
        if (group.length < 2 && !hashSingletons) {
          for (const item of group) item.ambiguousSha256 = null;
          return [];
        }
        return group.map(async (item) => {
          item.ambiguousSha256 = fingerprintHex(
            await subtle.digest('SHA-256', await item.file.arrayBuffer()),
          );
        });
      }),
    );
  }

  async function matchResumeSelection(files, manifest, { subtle } = {}) {
    const selected = Array.from(files || []).map((file) => ({ file }));
    const matches = [];
    const matched = new Set();
    const unresolved = new Map();
    const modernGroups = groupBy(
      manifest.items.filter(
        (item) => item.last_modified_ms !== null && item.last_modified_ms !== undefined,
      ),
      (item) => matchingKey({
        name: item.filename,
        size: item.size,
        lastModified: item.last_modified_ms,
      }),
    );

    for (const [key, manifestGroup] of modernGroups) {
      const candidates = selected.filter(
        (candidate) => (
          !matched.has(candidate)
          && !unresolved.has(candidate)
          && matchingKey(candidate.file) === key
        ),
      );
      if (manifestGroup.length === 1 && candidates.length === 1) {
        matches.push({ manifestItem: manifestGroup[0], file: candidates[0].file });
        matched.add(candidates[0]);
        continue;
      }
      if (
        manifestGroup.length > 1
        && candidates.length > 0
        && subtle
        && manifestGroup.every((item) => item.ambiguous_sha256)
      ) {
        await prepareAmbiguousFingerprints(candidates, subtle, { hashSingletons: true });
        const remainingByHash = groupBy(
          [...manifestGroup].sort((left, right) => Number(right.confirmed) - Number(left.confirmed)),
          (item) => item.ambiguous_sha256,
        );
        for (const candidate of candidates) {
          const available = remainingByHash.get(candidate.ambiguousSha256);
          if (available?.length) {
            matches.push({ manifestItem: available.shift(), file: candidate.file });
            matched.add(candidate);
          } else {
            unresolved.set(candidate, 'ambiguous');
          }
        }
      } else if (candidates.length) {
        for (const candidate of candidates) unresolved.set(candidate, 'ambiguous');
      }
    }

    const legacyGroups = groupBy(
      manifest.items.filter((item) => item.last_modified_ms === null || item.last_modified_ms === undefined),
      (item) => legacyMatchingKey({ name: item.filename, size: item.size }),
    );
    for (const [key, manifestGroup] of legacyGroups) {
      const candidates = selected.filter(
        (candidate) => (
          !matched.has(candidate)
          && !unresolved.has(candidate)
          && legacyMatchingKey(candidate.file) === key
        ),
      );
      if (manifestGroup.length === 1 && candidates.length === 1) {
        matches.push({ manifestItem: manifestGroup[0], file: candidates[0].file });
        matched.add(candidates[0]);
      } else if (candidates.length) {
        for (const candidate of candidates) unresolved.set(candidate, 'ambiguous');
      }
    }

    return {
      matches,
      unmatched: selected
        .filter((candidate) => !matched.has(candidate))
        .map((candidate) => ({
          file: candidate.file,
          reason: unresolved.get(candidate) || 'extra',
        })),
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
    if (item.status === 'needs_attention') return 'Требует внимания';
    if (item.status === 'uploading') return `Передача · ${item.progress}%`;
    if (item.status === 'waiting') return 'Ожидает повторного выбора';
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
      cancel.hidden = !['registered', 'retry_pending', 'uploading'].includes(item.status);
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
      resumeManifestUrl: root.dataset.resumeManifestUrlTemplate,
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
      AbortController: dependencies.AbortController || globalScope.AbortController,
      setTimeout: dependencies.setTimeout || globalScope.setTimeout.bind(globalScope),
      clearTimeout: dependencies.clearTimeout || globalScope.clearTimeout.bind(globalScope),
      onChange: () => renderPage(root, coordinator, globalError),
    });
    const input = root.querySelector('#upload-files');
    const resumeInput = root.querySelector('#resume-upload-files');
    const eventSelect = root.querySelector('#upload-event');
    let resumeManifest = null;
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
    resumeInput?.addEventListener('change', async () => {
      if (!resumeManifest || !resumeInput.files.length) return;
      globalError = '';
      try {
        await coordinator.resume(resumeInput.files, resumeManifest);
      } catch (error) {
        globalError = error instanceof SelectionError ? error.message : 'Не удалось продолжить загрузку. Повторите попытку.';
        coordinator.active = false;
        renderPage(root, coordinator, globalError);
      }
    });
    root.querySelector('[data-unfinished-uploads]')?.addEventListener('click', async (event) => {
      const resume = event.target.closest('[data-resume-batch]');
      if (!resume) return;
      globalError = '';
      try {
        resumeManifest = await coordinator.loadResumeManifest(resume.dataset.resumeBatchId);
        eventSelect.value = String(resumeManifest.batch.event.id);
        eventSelect.disabled = true;
        resumeInput.value = '';
        resumeInput.click();
      } catch (error) {
        globalError = 'Не удалось открыть незавершённую загрузку. Повторите попытку.';
        renderPage(root, coordinator, globalError);
      }
    });
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
      this.AbortController = options.AbortController;
      this.setTimeout = options.setTimeout;
      this.clearTimeout = options.clearTimeout;
      this.onChange = options.onChange || (() => {});
      this.items = [];
      this.batchId = null;
      this.active = false;
      this.registeredAll = false;
      this.finalizing = false;
      this.finalized = false;
      this.transferQueue = [];
      this.runningTransfers = 0;
      this.manualRetryCycles = new Map();
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
        cycleToken: this.createCycleToken(),
      }));
      this.active = true;
      this.onChange(this);
      await prepareAmbiguousFingerprints(this.items, this.crypto.subtle);

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
              content_type: item.contentType,
              size: item.file.size,
              last_modified_ms: lastModifiedMs(item.file),
              ambiguous_sha256: item.ambiguousSha256,
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
      await Promise.all(
        this.items.map((item) => this.enqueueTransfer(item, null, item.cycleToken)),
      );
      await this.finalizeIfReady();
      return this;
    }

    async loadResumeManifest(batchId) {
      const result = await this.fetch(
        interpolate(this.config.resumeManifestUrl, { batch: batchId }),
        { method: 'GET', credentials: 'same-origin' },
      );
      const payload = await result.json();
      if (!result.ok) throw new ControlError(result.status, payload);
      return payload;
    }

    async resume(files, manifest) {
      prepareSelection(files, {
        maxFiles: this.config.maxFiles,
        maxFileBytes: this.config.maxFileBytes,
        crypto: this.crypto,
      });
      const selection = await matchResumeSelection(files, manifest, { subtle: this.crypto.subtle });
      const matches = new Map(selection.matches.map((match) => [match.manifestItem.id, match.file]));
      this.batchId = manifest.batch.id;
      this.finalizing = false;
      this.finalized = false;
      this.registeredAll = true;
      this.items = manifest.items.map((manifestItem) => {
        const selected = matches.get(manifestItem.id);
        const confirmed = manifestItem.confirmed === true;
        return {
          clientItemId: `resume-${manifestItem.id}`,
          id: manifestItem.id,
          contentType: 'image/jpeg',
          file: selected || { name: manifestItem.filename, size: manifestItem.size },
          status: confirmed
            ? 'uploaded'
            : selected
              ? manifestItem.status === 'failed' ? 'retry_pending' : 'registered'
              : 'waiting',
          progress: confirmed ? 100 : 0,
          error: '',
          xhr: null,
          durable: true,
          cycleToken: this.createCycleToken(),
        };
      });
      for (const unmatched of selection.unmatched) {
        this.items.push({
          clientItemId: `resume-extra-${this.crypto.randomUUID()}`,
          id: null,
          contentType: 'image/jpeg',
          file: unmatched.file,
          status: 'needs_attention',
          progress: 0,
          error: unmatched.reason === 'extra'
            ? 'Этот файл не входит в выбранную загрузку.'
            : 'Не удалось однозначно сопоставить файл.',
          xhr: null,
          durable: false,
          cycleToken: this.createCycleToken(),
        });
      }
      this.active = true;
      this.onChange(this);
      await Promise.all(this.items.flatMap((item) => {
        if (item.status === 'registered') {
          return [this.enqueueTransfer(item, null, item.cycleToken)];
        }
        if (item.status === 'retry_pending') {
          return [this.enqueueWork(() => this.runResumeRetry(item, item.cycleToken))];
        }
        return [];
      }));
      await this.finalizeIfReady();
      if (!this.finalized) {
        this.active = false;
        this.onChange(this);
      }
      return this;
    }

    createCycleToken() {
      return {
        abortController: new this.AbortController(),
        cancelled: false,
        failureReported: false,
        retryTimer: null,
        wakeRetry: null,
      };
    }

    enqueueTransfer(item, initialGrant = null, token = item.cycleToken) {
      return this.enqueueWork(() => this.processItem(item, initialGrant, token));
    }

    enqueueWork(run) {
      return new Promise((resolve, reject) => {
        this.transferQueue.push({ reject, resolve, run });
        this.drainTransferQueue();
      });
    }

    drainTransferQueue() {
      const limit = Math.min(this.config.concurrency, 4);
      while (this.runningTransfers < limit && this.transferQueue.length) {
        const queued = this.transferQueue.shift();
        this.runningTransfers += 1;
        queued.run()
          .then(queued.resolve, queued.reject)
          .finally(() => {
            this.runningTransfers -= 1;
            this.drainTransferQueue();
          });
      }
    }

    async processItem(item, initialGrant = null, token = item.cycleToken) {
      let grant = initialGrant;
      let dataAttempt = 0;
      let refreshed = false;
      while (dataAttempt < 4) {
        if (token.cancelled) {
          await this.finishCancellation(item, token);
          return;
        }
        if (!grant) {
          let authorization;
          try {
            authorization = await this.control(
              interpolate(this.config.authorizeUrl, { batch: this.batchId, item: item.id }),
              { reason: 'data_attempt' },
              token,
            );
          } catch (error) {
            if (token.cancelled) {
              await this.finishCancellation(item, token);
              return;
            }
            throw error;
          }
          grant = authorization.grant;
        }
        if (token.cancelled) {
          await this.finishCancellation(item, token);
          return;
        }
        item.status = 'uploading';
        item.error = '';
        this.onChange(this);
        const outcome = await this.transfer(item, grant);
        grant = null;
        if (token.cancelled || outcome.type === 'cancelled') {
          token.cancelled = true;
          await this.finishCancellation(item, token);
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
          let authorization;
          try {
            authorization = await this.control(
              interpolate(this.config.authorizeUrl, { batch: this.batchId, item: item.id }),
              { reason: 'grant_refresh' },
              token,
            );
          } catch (error) {
            if (token.cancelled) {
              await this.finishCancellation(item, token);
              return;
            }
            throw error;
          }
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
        await this.waitForRetry(token, [1000, 3000, 7000][dataAttempt - 1]);
      }
    }

    waitForRetry(token, delay) {
      return new Promise((resolve) => {
        if (token.cancelled) {
          resolve();
          return;
        }
        const finish = () => {
          token.retryTimer = null;
          token.wakeRetry = null;
          resolve();
        };
        token.wakeRetry = finish;
        token.retryTimer = this.setTimeout(finish, delay);
      });
    }

    async finishCancellation(item, token) {
      if (token.failureReported) return;
      token.failureReported = true;
      await this.failItem(item, 'transfer_cancelled', 'Передача отменена.');
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
      const token = item?.cycleToken;
      if (!item || !token || ['uploaded', 'failed'].includes(item.status)) return false;
      if (token.cancelled) return true;
      token.cancelled = true;
      token.abortController.abort();
      if (item.xhr) item.xhr.abort();
      if (token.retryTimer !== null) this.clearTimeout(token.retryTimer);
      token.wakeRetry?.();
      return true;
    }

    manualRetry(clientItemId) {
      const existing = this.manualRetryCycles.get(clientItemId);
      if (existing) {
        return existing;
      }
      const item = this.items.find((candidate) => candidate.clientItemId === clientItemId);
      if (!item || item.status !== 'failed') {
        return Promise.resolve(false);
      }
      this.active = true;
      item.status = 'retry_pending';
      item.progress = 0;
      item.error = '';
      item.cycleToken = this.createCycleToken();
      this.onChange(this);
      const cycle = this.enqueueWork(() => this.runManualRetry(item, item.cycleToken));
      this.manualRetryCycles.set(clientItemId, cycle);
      cycle.then(
        () => this.manualRetryCycles.delete(clientItemId),
        () => this.manualRetryCycles.delete(clientItemId),
      );
      return cycle;
    }

    async runManualRetry(item, token) {
      let completed = true;
      try {
        const authorization = await this.control(
          interpolate(this.config.retryUrl, { batch: this.batchId, item: item.id }),
          {},
          token,
        );
        this.finalized = false;
        if (token.cancelled) {
          await this.finishCancellation(item, token);
        } else {
          await this.processItem(item, authorization.grant, token);
        }
      } catch (error) {
        if (token.cancelled) {
          await this.finishCancellation(item, token);
        } else {
          this.settleManualRetryFailure(item);
          completed = false;
        }
      }
      await this.finalizeIfReady();
      return completed;
    }

    async runResumeRetry(item, token) {
      const authorization = await this.control(
        interpolate(this.config.retryUrl, { batch: this.batchId, item: item.id }),
        {},
        token,
      );
      await this.processItem(item, authorization.grant, token);
    }

    settleManualRetryFailure(item) {
      item.status = 'failed';
      item.error = 'Не удалось повторить загрузку. Повторите попытку.';
      this.active = this.items.some(
        (candidate) => !['uploaded', 'failed'].includes(candidate.status),
      );
      this.onChange(this);
    }

    shouldWarnBeforeUnload() {
      return this.active;
    }

    async control(url, body, token = null) {
      const result = await this.fetch(url, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': this.config.csrfToken,
        },
        body: JSON.stringify(body),
        signal: token?.abortController.signal,
      });
      const payload = await result.json();
      if (!result.ok) {
        throw new ControlError(result.status, payload);
      }
      return payload;
    }

    async finalizeIfReady() {
      const durableItems = this.items.filter((item) => item.durable !== false);
      const terminal = durableItems.length > 0 && durableItems.every(
        (item) => ['uploaded', 'failed'].includes(item.status),
      );
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
    matchingKey,
    matchResumeSelection,
    prepareAmbiguousFingerprints,
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
