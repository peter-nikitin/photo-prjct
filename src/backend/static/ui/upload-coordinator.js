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

  function prepareSelection(files, { maxFiles, maxFileBytes, crypto, folder = null }) {
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
        folder,
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

  function uploadErrorMessage(error) {
    if (error instanceof SelectionError) return error.message;
    if (error instanceof ControlError && error.payload?.error?.code === 'folder_not_found') {
      return 'Выбранная папка больше недоступна. Обновите страницу и добавьте эти файлы заново через актуальную папку события.';
    }
    return 'Не удалось продолжить загрузку. Повторите попытку.';
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

  const QUEUE_PAGE_SIZE = 20;
  const QUEUE_GROUPS = [
    {
      key: 'needs_attention',
      label: 'Требуют внимания',
      expanded: true,
      includes: (item) => ['failed', 'needs_attention'].includes(item.status),
    },
    {
      key: 'uploading',
      label: 'Загружаются',
      expanded: true,
      includes: (item) => item.status === 'uploading',
    },
    {
      key: 'waiting',
      label: 'Ожидают',
      expanded: false,
      includes: (item) => !['failed', 'needs_attention', 'uploading', 'uploaded'].includes(item.status),
    },
    {
      key: 'uploaded',
      label: 'Загружены',
      expanded: false,
      includes: (item) => item.status === 'uploaded',
    },
  ];

  function groupItems(items) {
    return QUEUE_GROUPS.map(({ key, label, expanded, includes }) => {
      const groupedItems = items.filter(includes);
      return { key, label, expanded, items: groupedItems, count: groupedItems.length };
    });
  }

  function visibleGroupItems(group, offset = 0, pageSize = QUEUE_PAGE_SIZE) {
    const start = Math.max(0, offset);
    return group.items.slice(start, start + pageSize);
  }

  function queuePresentation(coordinator) {
    if (!coordinator.queuePresentation) {
      coordinator.queuePresentation = {
        expanded: Object.fromEntries(QUEUE_GROUPS.map(({ key, expanded }) => [key, expanded])),
        offsets: Object.fromEntries(QUEUE_GROUPS.map(({ key }) => [key, 0])),
      };
    }
    return coordinator.queuePresentation;
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

  function renderQueueRow(template, item, groupKey, index) {
    const row = template.content.firstElementChild.cloneNode(true);
    const errorId = `queue-error-${groupKey}-${index}`;
    row.dataset.clientItemId = item.clientItemId;
    row.dataset.renderedQueueItem = '';
    row.classList.add(`queue-item-${item.status === 'uploading' ? 'active' : item.status}`);
    row.querySelector('[data-file-name]').textContent = item.file.name;
    row.querySelector('[data-file-meta]').textContent = formatBytes(item.file.size);
    row.querySelector('[data-file-folder]').textContent = `Папка: ${item.folder?.name || 'Без папки'}`;
    row.querySelector('[data-file-status]').textContent = statusCopy(item);
    const itemProgress = row.querySelector('progress');
    itemProgress.value = item.progress;
    itemProgress.textContent = `${item.progress}%`;
    const error = row.querySelector('[data-file-error]');
    error.id = errorId;
    error.textContent = item.error;
    error.hidden = !item.error;
    const retry = row.querySelector('[data-retry-item]');
    retry.hidden = item.status !== 'failed';
    retry.dataset.clientItemId = item.clientItemId;
    retry.setAttribute('aria-describedby', errorId);
    const cancel = row.querySelector('[data-cancel-item]');
    cancel.hidden = !['registered', 'retry_pending', 'uploading'].includes(item.status);
    cancel.dataset.clientItemId = item.clientItemId;
    return row;
  }

  function renderQueue(root, coordinator) {
    const queue = root.querySelector('[data-upload-queue]');
    const groupTemplate = root.querySelector('#upload-queue-group-template');
    const rowTemplate = root.querySelector('#upload-queue-row-template');
    if (!queue || !groupTemplate || !rowTemplate) return;
    if (!coordinator.items.length) return;

    const presentation = queuePresentation(coordinator);
    queue.replaceChildren();
    for (const group of groupItems(coordinator.items)) {
      const section = groupTemplate.content.firstElementChild.cloneNode(true);
      const content = section.querySelector('[data-queue-group-content]');
      const toggle = section.querySelector('[data-queue-group-toggle]');
      const items = section.querySelector('[data-queue-group-items]');
      const pagination = section.querySelector('[data-queue-pagination]');
      const maxOffset = Math.max(0, Math.floor((group.count - 1) / QUEUE_PAGE_SIZE) * QUEUE_PAGE_SIZE);
      const offset = Math.min(presentation.offsets[group.key] || 0, maxOffset);
      const expanded = presentation.expanded[group.key];
      presentation.offsets[group.key] = offset;

      section.dataset.queueGroup = group.key;
      content.id = `queue-group-${group.key}`;
      content.hidden = !expanded;
      toggle.dataset.queueGroupToggle = group.key;
      toggle.setAttribute('aria-controls', content.id);
      toggle.setAttribute('aria-expanded', String(expanded));
      toggle.querySelector('[data-queue-group-label]').textContent = group.label;
      toggle.querySelector('[data-queue-group-count]').textContent = String(group.count);

      if (expanded) {
        for (const [index, item] of visibleGroupItems(group, offset, QUEUE_PAGE_SIZE).entries()) {
          items.append(renderQueueRow(rowTemplate, item, group.key, offset + index + 1));
        }
      }

      if (group.count > QUEUE_PAGE_SIZE) {
        const start = offset + 1;
        const end = Math.min(offset + QUEUE_PAGE_SIZE, group.count);
        pagination.hidden = false;
        pagination.querySelector('[data-queue-page-status]').textContent = `Показаны ${start}–${end} из ${group.count}`;
        const previous = pagination.querySelector('[data-queue-previous-page]');
        previous.dataset.queuePageGroup = group.key;
        previous.disabled = offset === 0;
        const next = pagination.querySelector('[data-queue-next-page]');
        next.dataset.queuePageGroup = group.key;
        next.disabled = offset >= maxOffset;
      }
      queue.append(section);
    }
  }

  function renderPage(root, coordinator, globalError = '') {
    const summary = summarize(coordinator.items);
    const terminal = summary.uploaded + summary.failed === summary.total && summary.total > 0;
    const waiting = coordinator.items.some((item) => item.status === 'waiting');
    const needsAttention = coordinator.items.some((item) => item.status === 'needs_attention');
    const needsAction = waiting || needsAttention;
    const title = root.querySelector('#upload-summary-title');
    const message = root.querySelector('[data-summary-message]');
    const percent = root.querySelector('[data-summary-percent]');
    const progress = root.querySelector('[data-upload-progress]');
    root.dataset.state = globalError
      ? 'partial'
      : coordinator.active
        ? 'active'
        : terminal && summary.failed
          ? 'partial'
          : terminal
            ? 'complete'
            : summary.total
              ? 'partial'
              : 'empty';
    if (title) {
      title.textContent = globalError
        ? 'Загрузка остановлена'
        : coordinator.active
          ? 'Идёт загрузка'
          : terminal && summary.failed
            ? 'Загружено частично'
            : terminal
              ? 'Загрузка завершена'
              : needsAction
                ? 'Требуется действие'
                : summary.total
                  ? 'Загрузка не завершена'
                  : 'Файлы не выбраны';
    }
    if (message) {
      const incompleteMessage = needsAttention && waiting
        ? 'Загрузка не завершена: выберите недостающие файлы и проверьте файлы, требующие внимания.'
        : needsAttention
          ? 'Загрузка не завершена: проверьте файлы, требующие внимания.'
          : waiting
            ? 'Загрузка не завершена: выберите недостающие файлы.'
            : '';
      message.textContent = globalError || (needsAction
        ? incompleteMessage
        : summary.total
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

    renderQueue(root, coordinator);
  }

  function folderFromTarget(target) {
    const folderId = target.dataset.folderId;
    return folderId ? { id: Number(folderId), name: target.dataset.folderName } : null;
  }

  function bindFolderTargets(root, onSelection) {
    const targets = Array.from(root.querySelectorAll?.('[data-folder-target]') || []);
    const clear = () => {
      delete root.dataset.dragActive;
      for (const target of targets) {
        delete target.dataset.dragActive;
        const copy = target.querySelector('[data-folder-target-copy]');
        if (copy) copy.textContent = target.dataset.defaultCopy;
      }
    };
    const activate = (target) => {
      clear();
      root.dataset.dragActive = 'true';
      target.dataset.dragActive = 'true';
      const copy = target.querySelector('[data-folder-target-copy]');
      if (copy) {
        copy.textContent = target.dataset.folderId
          ? `Загрузить в «${target.dataset.folderName}»`
          : 'Загрузить без папки';
      }
    };
    for (const target of targets) {
      target.addEventListener('dragenter', (event) => {
        event.preventDefault();
        activate(target);
      });
      target.addEventListener('dragover', (event) => {
        event.preventDefault();
        activate(target);
      });
      target.addEventListener('dragleave', (event) => {
        if (!target.contains(event.relatedTarget)) clear();
      });
      target.addEventListener('drop', (event) => {
        event.preventDefault();
        clear();
        onSelection(event.dataTransfer.files, folderFromTarget(target));
      });
      target.querySelector('[data-folder-target-input]')?.addEventListener('change', (event) => {
        onSelection(event.currentTarget.files, folderFromTarget(target));
        event.currentTarget.value = '';
      });
    }
    root.addEventListener?.('dragend', clear);
    return clear;
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
    const resumeInput = root.querySelector('#resume-upload-files');
    const eventSelect = root.querySelector('#upload-event');
    const startUpload = root.querySelector('[data-start-upload]');
    let resumeManifest = null;
    const stage = (files, folder) => {
      globalError = '';
      if (!eventSelect.value) {
        globalError = 'Сначала выберите событие.';
        renderPage(root, coordinator, globalError);
        eventSelect.focus();
        return;
      }
      try {
        coordinator.stage(files, folder);
        eventSelect.disabled = true;
        if (startUpload) startUpload.disabled = !coordinator.items.length;
      } catch (error) {
        globalError = error instanceof SelectionError ? error.message : 'Не удалось продолжить загрузку. Повторите попытку.';
        renderPage(root, coordinator, globalError);
      }
    };
    bindFolderTargets(root, stage);
    startUpload?.addEventListener('click', async () => {
      if (startUpload.disabled) return;
      startUpload.disabled = true;
      globalError = '';
      try {
        await coordinator.start(eventSelect.value);
      } catch (error) {
        globalError = uploadErrorMessage(error);
        coordinator.active = false;
        if (!coordinator.batchId) startUpload.disabled = false;
        renderPage(root, coordinator, globalError);
      }
    });
    const syncFolderTargets = () => {
      for (const collection of root.querySelectorAll?.('[data-folder-targets]') || []) {
        collection.hidden = collection.dataset.eventId !== eventSelect.value;
      }
    };
    eventSelect?.addEventListener?.('change', syncFolderTargets);
    if (eventSelect && root.dataset.initialEventId) eventSelect.value = root.dataset.initialEventId;
    if (eventSelect) syncFolderTargets();
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
    root.querySelector('[data-upload-queue]')?.addEventListener('click', async (event) => {
      const toggle = event.target.closest('[data-queue-group-toggle]');
      const previousPage = event.target.closest('[data-queue-previous-page]');
      const nextPage = event.target.closest('[data-queue-next-page]');
      const retry = event.target.closest('[data-retry-item]');
      const cancel = event.target.closest('[data-cancel-item]');
      if (toggle) {
        const presentation = queuePresentation(coordinator);
        const key = toggle.dataset.queueGroupToggle;
        presentation.expanded[key] = !presentation.expanded[key];
        renderPage(root, coordinator, globalError);
      } else if (previousPage || nextPage) {
        const control = previousPage || nextPage;
        const presentation = queuePresentation(coordinator);
        const delta = nextPage ? QUEUE_PAGE_SIZE : -QUEUE_PAGE_SIZE;
        const key = control.dataset.queuePageGroup;
        presentation.offsets[key] = Math.max(0, (presentation.offsets[key] || 0) + delta);
        renderPage(root, coordinator, globalError);
      } else if (retry) {
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
      this.startPromise = null;
      this.active = false;
      this.registeredAll = false;
      this.finalizing = false;
      this.finalized = false;
      this.transferQueue = [];
      this.runningTransfers = 0;
      this.manualRetryCycles = new Map();
    }

    stage(files, folder = null) {
      if (this.batchId || this.startPromise) {
        throw new SelectionError('Загрузка уже начата. Добавить файлы в неё нельзя.');
      }
      const selection = prepareSelection(files, {
        maxFiles: this.config.maxFiles - this.items.length,
        maxFileBytes: this.config.maxFileBytes,
        crypto: this.crypto,
        folder,
      });
      this.items.push(...selection.items.map((entry) => ({
        ...entry,
        id: null,
        status: 'pending',
        progress: 0,
        error: '',
        xhr: null,
        cycleToken: this.createCycleToken(),
      })));
      this.onChange(this);
      return this;
    }

    start(eventId) {
      if (this.startPromise) return this.startPromise;
      if (!this.items.length) {
        return Promise.reject(new SelectionError('Сначала добавьте JPEG-файлы в очередь.'));
      }
      const attempt = this.startBatch(eventId);
      this.startPromise = attempt;
      attempt.catch(() => {
        if (!this.batchId && this.startPromise === attempt) this.startPromise = null;
      });
      return attempt;
    }

    async startBatch(eventId) {
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
              folder_id: item.folder?.id || null,
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
          folder: manifestItem.folder || null,
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
          folder: null,
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

    markUploading(item, token) {
      if (token.cancelled || item.status === 'uploading') return;
      item.status = 'uploading';
      item.error = '';
      this.onChange(this);
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
        this.markUploading(item, token);
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
        this.markUploading(item, token);
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
      let completed = true;
      let retryAuthorized = false;
      try {
        this.markUploading(item, token);
        const authorization = await this.control(
          interpolate(this.config.retryUrl, { batch: this.batchId, item: item.id }),
          {},
          token,
        );
        retryAuthorized = true;
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
          if (retryAuthorized) {
            await this.failItem(
              item,
              'transfer_retries_exhausted',
              'Не удалось повторить загрузку. Повторите попытку.',
            );
          }
          this.settleManualRetryFailure(item);
          completed = false;
        }
      }
      await this.finalizeIfReady();
      return completed;
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
    groupItems,
    matchingKey,
    matchResumeSelection,
    prepareAmbiguousFingerprints,
    prepareSelection,
    retryableTransfer,
    SelectionError,
    UploadCoordinator,
    bindUploadPage,
    bindFolderTargets,
    renderPage,
    summarize,
    uploadErrorMessage,
    visibleGroupItems,
  };
});

if (typeof document !== 'undefined') {
  const start = () => globalThis.FindMeUpload.bindUploadPage(document.querySelector('[data-upload-root]'));
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}
