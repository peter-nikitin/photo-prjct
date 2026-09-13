(function importCoordinatorModule(globalScope, factory) {
  const api = factory(globalScope);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (globalScope) globalScope.FindMeImport = api;
})(typeof globalThis === 'undefined' ? this : globalThis, function buildImportCoordinator(globalScope) {
  'use strict';

  const PAGE_SIZE = 20;
  const POLL_DELAY = 4000;
  const TERMINAL = new Set(['completed', 'partial', 'failed']);
  const STATUS_LABELS = {
    queued: 'В очереди',
    enumerating: 'Проверяем папку',
    transferring: 'Загружаем',
    paused: 'Приостановлено',
    completed: 'Завершено',
    partial: 'Завершено с ошибками',
    failed: 'Не удалось загрузить',
  };
  const ITEM_LABELS = {
    pending: 'Ожидает', claimed: 'Готовится', uploading: 'Загружается',
    imported: 'Загружено', duplicate: 'Уже было', error: 'Ошибка',
  };
  const ITEM_ERRORS = {
    file_too_large: 'Файл больше 50 МБ.',
    invalid_jpeg: 'Файл не является корректным JPEG.',
    source_changed: 'Файл изменился во время загрузки.',
    source_missing: 'Файл больше недоступен по ссылке.',
    download_failed: 'Не удалось скачать файл.',
    storage_unavailable: 'Хранилище временно недоступно.',
  };
  const BATCH_ERRORS = {
    source_unavailable: 'Публичная папка недоступна. Проверьте ссылку и доступ.',
    source_not_folder: 'Ссылка должна вести на публичную папку.',
    manifest_too_large: 'В папке слишком много файлов. Разделите исходную папку.',
    manifest_changed: 'Содержимое папки изменилось во время проверки. Повторите импорт.',
    permission_denied: 'Право загрузки отозвано. Обратитесь к администратору.',
    feature_paused: 'Импорт временно приостановлен.',
  };

  function interpolate(template, values) {
    return Object.entries(values).reduce((result, [key, value]) => result.replace(`{${key}}`, value), template);
  }

  function importPresentation(record) {
    const counts = record.counts || {};
    let message = '';
    if (record.status === 'completed' && Number(counts.jpeg || 0) === 0) {
      message = 'В указанной папке нет JPEG-файлов для загрузки';
    } else if (record.status === 'completed' && Number(counts.imported || 0) === 0 && Number(counts.duplicate || 0) > 0) {
      message = 'Новых фотографий нет';
    } else if (record.status === 'completed' && Number(counts.imported || 0) > 0 && record.processing_active === true) {
      message = 'Загрузка завершена. Обработка фотографий продолжается';
    } else if (record.status === 'completed' && Number(counts.imported || 0) > 0) {
      message = 'Фотографии переданы в стандартную обработку.';
    }
    return {
      label: STATUS_LABELS[record.status] || 'Состояние неизвестно',
      message,
      warning: Number(counts.directory || 0) > 0
        ? 'В папке по ссылке есть вложенные папки. Фотографии из них загружены не будут.'
        : '',
      error: record.error_code ? (BATCH_ERRORS[record.error_code] || 'Не удалось выполнить импорт.') : '',
      terminal: TERMINAL.has(record.status),
    };
  }

  function itemPresentation(item) {
    return {
      label: ITEM_LABELS[item.status] || 'Состояние неизвестно',
      error: item.status === 'error' ? (ITEM_ERRORS[item.error_code] || 'Не удалось загрузить файл.') : '',
    };
  }

  async function json(response) {
    const payload = await response.json();
    if (!response.ok || payload.contract_version !== 1) {
      const code = payload && payload.error && payload.error.code;
      const publicMessage = BATCH_ERRORS[code] || 'Не удалось выполнить запрос. Попробуйте ещё раз.';
      const error = new Error(publicMessage);
      error.code = code || 'request_failed';
      error.publicMessage = publicMessage;
      error.definitiveRejection = response.status >= 400 && response.status < 500;
      throw error;
    }
    return payload;
  }

  function submissionBody(selection, submissionKey) {
    return {
      contract_version: 1,
      event_id: Number(selection.eventId),
      folder_id: selection.folderId === '' ? null : Number(selection.folderId),
      source_url: selection.sourceUrl,
      submission_key: submissionKey,
    };
  }

  function matchesSelection(body, selection) {
    return body.event_id === Number(selection.eventId)
      && body.folder_id === (selection.folderId === '' ? null : Number(selection.folderId))
      && body.source_url === selection.sourceUrl;
  }

  class ImportCoordinator {
    constructor({ fetch, urls, csrfToken, randomUUID, render, schedule, cancel, eventId = null }) {
      this.fetch = fetch;
      this.eventId = eventId;
      this.urls = urls;
      this.csrfToken = csrfToken;
      this.randomUUID = randomUUID;
      this.render = render;
      this.schedule = schedule || ((callback, delay) => globalScope.setTimeout(callback, delay));
      this.cancel = cancel || ((timer) => globalScope.clearTimeout(timer));
      this.pendingSubmission = null;
      this.activeIds = new Set();
      this.page = 1;
      this.timer = null;
      this.stopped = false;
    }

    async request(url, options = {}) {
      return json(await this.fetch(url, options));
    }

    async submit(selection) {
      if (!this.pendingSubmission || !matchesSelection(this.pendingSubmission, selection)) {
        this.pendingSubmission = submissionBody(selection, this.randomUUID());
      }
      const body = this.pendingSubmission;
      let payload;
      try {
        payload = await this.request(this.urls.collection, {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken },
          body: JSON.stringify(body),
        });
      } catch (error) {
        if (error.definitiveRejection === true) this.pendingSubmission = null;
        this.render({ type: 'submit-error', error });
        throw error;
      }
      this.pendingSubmission = null;
      const record = payload.batch || payload;
      this.render({ type: 'created', record });
      try {
        await this.loadPage(1);
      } catch (error) {
        this.render({ type: 'history-error', error });
      }
      return record;
    }

    async loadPage(page = 1) {
      const query = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
      if (this.eventId !== null) query.set('event_id', String(this.eventId));
      const payload = await this.request(`${this.urls.collection}?${query}`, { credentials: 'same-origin' });
      this.page = page;
      this.activeIds = new Set(payload.imports.filter((record) => !TERMINAL.has(record.status) || record.processing_active === true).map((record) => record.id));
      this.render({ type: 'list', records: payload.imports, pagination: payload.pagination });
      this.queuePoll();
      return payload;
    }

    async loadItems(batchId, page = 1) {
      const url = `${interpolate(this.urls.items, { batch: batchId })}?page=${page}&page_size=${PAGE_SIZE}`;
      const payload = await this.request(url, { credentials: 'same-origin' });
      this.render({ type: 'items', batchId, items: payload.items, pagination: payload.pagination });
      return payload;
    }

    async retry(batchId) {
      const payload = await this.request(interpolate(this.urls.retry, { batch: batchId }), {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken },
        body: JSON.stringify({ contract_version: 1 }),
      });
      this.render({ type: 'retry-success', batchId });
      try {
        await this.loadPage(this.page);
      } catch (error) {
        this.render({ type: 'history-error', error });
      }
      return payload.batch;
    }

    queuePoll() {
      if (this.stopped || this.timer !== null || this.activeIds.size === 0) return;
      this.timer = this.schedule(() => this.poll().catch(() => this.queuePoll()), POLL_DELAY);
    }

    async poll() {
      if (this.stopped) return;
      this.timer = null;
      const ids = Array.from(this.activeIds);
      for (const batchId of ids) {
        try {
          const payload = await this.request(interpolate(this.urls.detail, { batch: batchId }), { credentials: 'same-origin' });
          const record = payload.batch || payload;
          this.render({ type: 'updated', record });
          if (TERMINAL.has(record.status) && record.processing_active !== true) this.activeIds.delete(batchId);
        } catch (error) {
          this.render({ type: 'poll-error', batchId, error });
        }
      }
      this.queuePoll();
    }

    stop() {
      this.stopped = true;
      if (this.timer !== null) this.cancel(this.timer);
      this.timer = null;
    }
  }

  function setText(root, selector, value) {
    const node = root.querySelector(selector);
    if (node) node.textContent = String(value);
  }

  function setStatus(root, selector, value) {
    const node = root.querySelector(selector);
    if (!node) return;
    node.textContent = value;
    node.hidden = !value;
  }

  function safeErrorMessage(error, fallback) {
    return error && typeof error.publicMessage === 'string' ? error.publicMessage : fallback;
  }

  function renderCard(root, record) {
    const template = root.querySelector('[data-import-card-template]');
    const card = template.content.firstElementChild.cloneNode(true);
    return updateCard(card, record);
  }

  function updateCard(card, record) {
    card.dataset.importId = record.id;
    const presentation = importPresentation(record);
    const counts = record.counts || {};
    setText(card, '[data-import-event]', record.event.name);
    setText(card, '[data-import-folder-name]', record.folder ? record.folder.name : 'Без папки');
    setText(card, '[data-import-created]', new Date(record.created_at).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }));
    setText(card, '[data-import-status]', presentation.label);
    for (const key of ['jpeg', 'imported', 'duplicate', 'error', 'pending', 'unsupported']) setText(card, `[data-import-${key}]`, counts[key] || 0);
    const accepted = card.querySelector('[data-import-accepted]');
    accepted.hidden = record.status !== 'queued' && record.status !== 'enumerating';
    const message = card.querySelector('[data-import-message]');
    message.hidden = !presentation.message;
    message.textContent = presentation.message;
    const warning = card.querySelector('[data-import-subfolder-warning]');
    warning.hidden = !presentation.warning;
    const error = card.querySelector('[data-import-error-message]');
    error.hidden = !presentation.error;
    error.textContent = presentation.error;
    card.querySelector('[data-import-retry]').hidden = Number(counts.error || 0) === 0 && record.status !== 'failed';
    setStatus(card, '[data-import-action-status]', '');
    return card;
  }

  function pagination(nav, pagination, statusSelector, previousSelector, nextSelector) {
    const pages = Number(pagination.pages || 0);
    nav.hidden = pages <= 1;
    setText(nav, statusSelector, pages ? `Страница ${pagination.page} из ${pages}` : '');
    nav.querySelector(previousSelector).disabled = pagination.page <= 1;
    nav.querySelector(nextSelector).disabled = pagination.page >= pages;
    nav.dataset.page = String(pagination.page);
  }

  function bindImportPage(root, environment = globalScope) {
    if (!root || root.dataset.importHistoryEnabled !== 'true') return null;
    if (root.importCoordinator) return root.importCoordinator;
    const eventId = Number(root.dataset.eventId);
    if (!Number.isSafeInteger(eventId) || eventId <= 0) return null;
    const section = root.querySelector('[data-import-panel]');
    const list = root.querySelector('[data-import-list]');
    const coordinator = new ImportCoordinator({
      eventId,
      fetch: environment.fetch.bind(environment),
      urls: {
        collection: root.dataset.importCollectionUrl,
        detail: root.dataset.importDetailUrlTemplate,
        items: root.dataset.importItemsUrlTemplate,
        retry: root.dataset.importRetryUrlTemplate,
      },
      csrfToken: root.dataset.csrfToken,
      randomUUID: () => environment.crypto.randomUUID(),
      schedule: (callback, delay) => environment.setTimeout(callback, delay),
      cancel: (timer) => environment.clearTimeout(timer),
      render(change) {
        if (['created', 'list', 'updated'].includes(change.type)) {
          const document = root.ownerDocument || environment.document;
          if (document && environment.CustomEvent) document.dispatchEvent(new environment.CustomEvent(
            'findme:event-photo-import-progress', { detail: { eventId } },
          ));
        }
        if (change.type === 'list') {
          list.replaceChildren(...change.records.map((record) => renderCard(root, record)));
          section.hidden = change.records.length === 0 && root.dataset.importEnabled !== 'true';
          setText(root, '[data-import-list-status]', change.records.length ? '' : 'Сохранённых импортов пока нет.');
          pagination(root.querySelector('[data-import-list-pagination]'), change.pagination, '[data-import-list-page-status]', '[data-import-list-previous]', '[data-import-list-next]');
        } else if (change.type === 'updated') {
          const current = list.querySelector(`[data-import-id="${change.record.id}"]`);
          if (current) updateCard(current, change.record);
        } else if (change.type === 'created') {
          section.hidden = false;
          setText(root, '[data-import-form-message]', 'Задача принята. Можно закрыть страницу — загрузка продолжится на сервере');
        } else if (change.type === 'submit-error') {
          setText(root, '[data-import-form-message]', safeErrorMessage(change.error, 'Не удалось отправить задачу. Попробуйте ещё раз.'));
        } else if (change.type === 'history-error') {
          setText(root, '[data-import-list-status]', 'Не удалось обновить список импортов. Попробуйте ещё раз.');
        } else if (change.type === 'poll-error') {
          const card = list.querySelector(`[data-import-id="${change.batchId}"]`);
          if (card) setStatus(card, '[data-import-action-status]', 'Не удалось обновить прогресс. Попробуйте ещё раз.');
        } else if (change.type === 'retry-success') {
          const card = list.querySelector(`[data-import-id="${change.batchId}"]`);
          if (card) setStatus(card, '[data-import-action-status]', '');
        } else if (change.type === 'items') {
          const card = list.querySelector(`[data-import-id="${change.batchId}"]`);
          if (!card) return;
          const itemList = card.querySelector('[data-import-item-list]');
          const itemTemplate = root.querySelector('[data-import-item-template]');
          itemList.replaceChildren(...change.items.map((item) => {
            const row = itemTemplate.content.firstElementChild.cloneNode(true);
            const view = itemPresentation(item);
            setText(row, '[data-import-item-name]', item.filename);
            setText(row, '[data-import-item-state]', view.label);
            const error = row.querySelector('[data-import-item-error]');
            error.hidden = !view.error;
            error.textContent = view.error;
            return row;
          }));
          const items = card.querySelector('[data-import-items]');
          items.hidden = false;
          pagination(card.querySelector('[data-import-items-pagination]'), change.pagination, '[data-import-items-page-status]', '[data-import-items-previous]', '[data-import-items-next]');
          setStatus(card, '[data-import-action-status]', '');
        }
      },
    });
    root.importCoordinator = coordinator;
    section.hidden = root.dataset.importEnabled !== 'true';

    const form = root.querySelector('[data-import-form]');
    const folderSelect = root.querySelector('[data-import-folder]');
    const source = root.querySelector('[data-import-source-url]');
    const submit = root.querySelector('[data-import-submit]');
    function syncForm(resetFolder = false) {
      if (!form) return;
      let first = null;
      let currentMatches = false;
      for (const option of folderSelect.querySelectorAll('[data-import-folder-option]')) {
        const matches = Number(option.dataset.eventId) === eventId;
        option.hidden = !matches;
        option.disabled = !matches;
        if (matches && first === null) first = option;
        if (matches && option.value === folderSelect.value) currentMatches = true;
      }
      folderSelect.disabled = !eventId;
      if (first && (resetFolder || !currentMatches)) first.selected = true;
      submit.disabled = !eventId || !source.value.trim();
    }
    if (form) {
      root.addEventListener('findme:event-photo-folders-refreshed', () => syncForm());
      source.addEventListener('input', () => syncForm());
      form.addEventListener('submit', async (event) => {
        event.preventDefault();
        submit.disabled = true;
        try {
          await coordinator.submit({ eventId, folderId: folderSelect.value, sourceUrl: source.value.trim() });
          source.value = '';
        } catch (_error) {
          // The same submission key is retained for an explicit retry.
        }
        syncForm();
      });
      syncForm();
    }

    root.addEventListener('click', (event) => {
      const card = event.target.closest('[data-import-card]');
      if (!card) return;
      if (event.target.closest('[data-import-show-items]')) {
        coordinator.loadItems(card.dataset.importId, 1).catch((error) => {
          setStatus(card, '[data-import-action-status]', safeErrorMessage(error, 'Не удалось загрузить список файлов. Попробуйте ещё раз.'));
        });
      }
      if (event.target.closest('[data-import-retry]')) {
        coordinator.retry(card.dataset.importId).catch((error) => {
          setStatus(card, '[data-import-action-status]', safeErrorMessage(error, 'Не удалось повторить импорт. Попробуйте ещё раз.'));
        });
      }
      const page = Number(card.querySelector('[data-import-items-pagination]')?.dataset.page || 1);
      if (event.target.closest('[data-import-items-previous]')) {
        coordinator.loadItems(card.dataset.importId, page - 1).catch((error) => {
          setStatus(card, '[data-import-action-status]', safeErrorMessage(error, 'Не удалось загрузить список файлов. Попробуйте ещё раз.'));
        });
      }
      if (event.target.closest('[data-import-items-next]')) {
        coordinator.loadItems(card.dataset.importId, page + 1).catch((error) => {
          setStatus(card, '[data-import-action-status]', safeErrorMessage(error, 'Не удалось загрузить список файлов. Попробуйте ещё раз.'));
        });
      }
    });
    root.querySelector('[data-import-list-previous]').addEventListener('click', () => coordinator.loadPage(coordinator.page - 1).catch(() => {
      setText(root, '[data-import-list-status]', 'Не удалось загрузить страницу импортов. Попробуйте ещё раз.');
    }));
    root.querySelector('[data-import-list-next]').addEventListener('click', () => coordinator.loadPage(coordinator.page + 1).catch(() => {
      setText(root, '[data-import-list-status]', 'Не удалось загрузить страницу импортов. Попробуйте ещё раз.');
    }));
    environment.addEventListener('pagehide', () => coordinator.stop(), { once: true });
    coordinator.loadPage(1).catch(() => {
      setText(root, '[data-import-list-status]', 'Не удалось загрузить сохранённый прогресс. Обновите страницу.');
      section.hidden = root.dataset.importEnabled !== 'true';
    });
    return coordinator;
  }

  return { ImportCoordinator, bindImportPage, importPresentation, itemPresentation, updateCard };
});

if (typeof document !== 'undefined') {
  const start = () => globalThis.FindMeImport.bindImportPage(document.querySelector('[data-import-root]'));
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}
