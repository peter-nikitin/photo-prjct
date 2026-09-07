(function exposeEventPhotoStatus(globalScope, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (globalScope) globalScope.FindMeEventPhotoStatus = api;
})(typeof globalThis === 'undefined' ? this : globalThis, function buildEventPhotoStatus() {
  'use strict';

  const NORMAL_INTERVAL_MS = 5_000;
  const MAX_BACKOFF_MS = 30_000;
  const UPLOAD_ACTIVITY_EVENT = 'findme:event-photo-upload-activity';
  const IMPORT_PROGRESS_EVENT = 'findme:event-photo-import-progress';
  const MANUAL_REFRESH_EVENT = 'findme:event-photo-status-refresh';
  const FILTER_COUNT_EVENT = 'findme:event-photo-filter-count';
  const CATEGORY_LABELS = {
    succeeded: 'Обработано',
    processing: 'Обрабатывается',
    queued: 'Ожидает обработки',
    failed: 'Ошибки',
    cancelled: 'Остановлена',
    not_started: 'Не запущена',
    not_required: 'Не требуется',
  };

  function setText(root, selector, value) {
    const node = root.querySelector?.(selector);
    if (node) node.textContent = String(value);
  }

  function renderProcessingSummary(node, summary) {
    const parts = [];
    for (const [category, label] of Object.entries(CATEGORY_LABELS)) {
      const value = summary?.categories?.[category] || 0;
      if (['succeeded', 'processing', 'queued', 'failed'].includes(category) || value) {
        parts.push(`${label}: ${value}`);
      }
    }
    node.textContent = parts.join(' · ');
  }

  function batchState(row) {
    if (row.can_close) return 'Все фотографии загружены. Можно закрыть страницу';
    if (row.failed_count) return `Ошибок передачи: ${row.failed_count}. Продолжите загрузку.`;
    return 'Загрузка не завершена.';
  }

  class EventPhotoStatusController {
    constructor(root, environment = {}) {
      const windowObject = environment.window || globalThis;
      const documentObject = environment.document || root?.ownerDocument || globalThis.document;
      if (!root?.dataset?.statusUrl) throw new Error('Event photo status URL is required.');
      if (typeof windowObject.fetch !== 'function') throw new Error('Status fetch is required.');
      this.root = root;
      this.window = windowObject;
      this.document = documentObject;
      this.fetch = windowObject.fetch.bind(windowObject);
      this.timer = null;
      this.inFlight = null;
      this.historyInFlight = null;
      this.pendingHistoryBatchId = null;
      this.refreshPending = false;
      this.serverActive = false;
      this.localUploadActive = false;
      this.accessLost = false;
      this.queryBlocked = false;
      this.canInspect = null;
      this.canUpload = null;
      this.failureDelay = NORMAL_INTERVAL_MS;
      this.onVisibilityChange = this.onVisibilityChange.bind(this);
      this.onFocus = this.onFocus.bind(this);
      this.onUploadActivity = this.onUploadActivity.bind(this);
      this.onImportProgress = this.onImportProgress.bind(this);
      this.onManualRefresh = this.onManualRefresh.bind(this);
      this.onClick = this.onClick.bind(this);
    }

    start() {
      this.document?.addEventListener?.('visibilitychange', this.onVisibilityChange);
      this.document?.addEventListener?.(UPLOAD_ACTIVITY_EVENT, this.onUploadActivity);
      this.document?.addEventListener?.(IMPORT_PROGRESS_EVENT, this.onImportProgress);
      this.document?.addEventListener?.(MANUAL_REFRESH_EVENT, this.onManualRefresh);
      this.root.addEventListener?.('click', this.onClick);
      this.window?.addEventListener?.('focus', this.onFocus);
      this.refresh();
      return this;
    }

    isVisible() {
      return !this.document || this.document.visibilityState !== 'hidden';
    }

    clearTimer() {
      if (this.timer !== null) this.window.clearTimeout(this.timer);
      this.timer = null;
    }

    schedule(delay) {
      this.clearTimer();
      if (this.accessLost || !this.isVisible()) return;
      this.timer = this.window.setTimeout(() => {
        this.timer = null;
        this.refresh();
      }, delay);
    }

    requestUrl() {
      const url = new URL(this.root.dataset.statusUrl, this.window.location.href);
      const fragment = this.root.querySelector?.('[data-event-photo-fragment]');
      const validResults = fragment?.dataset?.filterValid === 'true';
      if (validResults) {
        const filters = new URLSearchParams(fragment.dataset.canonicalQuery || '');
        for (const [name, value] of filters) url.searchParams.append(name, value);
        const canonical = new URL(fragment.dataset.canonicalUrl || '', this.window.location.href);
        const page = canonical.searchParams.get('page');
        if (page) url.searchParams.set('page', page);
      } else {
        url.searchParams.set('include_results', '0');
      }
      if (validResults) {
        for (const node of this.root.querySelectorAll?.('[data-photo-status-id]') || []) {
          if (node.dataset.photoStatusId) url.searchParams.append('photo_id', node.dataset.photoStatusId);
        }
      }
      for (const node of this.root.querySelectorAll?.('[data-batch-status-id]') || []) {
        if (node.dataset.batchStatusId) url.searchParams.append('batch_id', node.dataset.batchStatusId);
      }
      return url.toString();
    }

    resultScope() {
      const fragment = this.root.querySelector?.('[data-event-photo-fragment]');
      if (fragment?.dataset?.filterValid !== 'true') return null;
      const photoIds = [...(this.root.querySelectorAll?.('[data-photo-status-id]') || [])]
        .map((node) => node.dataset.photoStatusId)
        .filter(Boolean);
      return JSON.stringify([
        fragment.dataset.canonicalQuery || '',
        fragment.dataset.canonicalUrl || '',
        photoIds,
      ]);
    }

    refresh() {
      if (this.accessLost || this.queryBlocked) return this.inFlight;
      this.clearTimer();
      if (this.inFlight) {
        this.refreshPending = true;
        return this.inFlight;
      }
      if (!this.isVisible()) {
        this.refreshPending = true;
        return null;
      }
      if (this.pendingHistoryBatchId && !this.historyInFlight) this.refreshHistory();
      this.refreshPending = false;
      const requestedResultScope = this.resultScope();
      this.inFlight = this.fetch(this.requestUrl(), {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
        cache: 'no-store',
      })
        .then(async (response) => {
          if ([401, 403, 404, 410].includes(response.status)) {
            this.loseAccess();
            return null;
          }
          if (response.status === 400) {
            this.blockRejectedQuery();
            return null;
          }
          if (!response.ok) throw new Error(`Status request failed: ${response.status}`);
          const body = await response.json();
          this.render(body, requestedResultScope);
          this.failureDelay = NORMAL_INTERVAL_MS;
          this.serverActive = Boolean(body.has_active_work);
          if (this.serverActive || this.localUploadActive || this.pendingHistoryBatchId) {
            this.schedule(NORMAL_INTERVAL_MS);
          }
          return body;
        })
        .catch(() => {
          if (this.accessLost) return null;
          this.markStale();
          this.failureDelay = Math.min(this.failureDelay * 2, MAX_BACKOFF_MS);
          this.schedule(this.failureDelay);
          return null;
        })
        .finally(() => {
          this.inFlight = null;
          if (this.refreshPending && !this.accessLost && this.isVisible()) {
            this.refresh();
          }
        });
      return this.inFlight;
    }

    render(body, requestedResultScope) {
      this.root.dataset.statusStale = 'false';
      setText(this.root, '[data-event-photo-status-message]', '');
      const updated = this.root.querySelector?.('[data-event-photo-status-updated-at]');
      if (updated) {
        updated.textContent = body.server_timestamp || '';
        updated.dateTime = body.server_timestamp || '';
        updated.hidden = true;
      }
      this.applyCapabilities(body.capabilities);
      if (this.canInspect !== false && body.admin) {
        this.renderAdmin(body.admin, requestedResultScope === this.resultScope());
      }
      if (this.canUpload !== false && Array.isArray(body.batches)) this.renderBatches(body.batches);
    }

    applyCapabilities(capabilities) {
      if (!capabilities || typeof capabilities !== 'object') return;
      this.canInspect = capabilities.can_inspect === true;
      this.canUpload = capabilities.can_upload === true;
      const admin = this.root.querySelector?.('[data-event-photo-admin]');
      const upload = this.root.querySelector?.('[data-event-photo-upload]');
      if (admin) admin.hidden = !this.canInspect;
      if (upload) upload.hidden = !this.canUpload;
      if (!this.canUpload) {
        this.localUploadActive = false;
        this.pendingHistoryBatchId = null;
      }
    }

    renderAdmin(admin, renderResults) {
      setText(this.root, '[data-event-photo-summary-total]', admin.summary?.total || 0);
      for (const node of this.root.querySelectorAll?.('[data-event-photo-summary-category]') || []) {
        const category = node.dataset.eventPhotoSummaryCategory;
        node.textContent = String(admin.summary?.categories?.[category] || 0);
      }
      if (!renderResults) return;
      if (Number.isSafeInteger(admin.filtered_result_count)) {
        setText(this.root, '[data-event-photo-filtered-count]', admin.filtered_result_count);
        if (this.root.querySelector?.('[data-event-photo-fragment]')?.dataset?.filterValid === 'true') {
          this.document?.dispatchEvent?.(new this.window.CustomEvent(
            FILTER_COUNT_EVENT,
            { detail: { count: admin.filtered_result_count } },
          ));
        }
      }
      const changed = this.root.querySelector?.('[data-event-photo-result-list-changed]');
      if (changed && typeof admin.result_list_changed === 'boolean') {
        changed.hidden = !admin.result_list_changed;
        const copy = changed.querySelector?.('[data-event-photo-result-list-message]');
        if (copy) copy.textContent = admin.result_list_changed ? 'Список фотографий изменился.' : '';
        else changed.textContent = admin.result_list_changed ? 'Список фотографий изменился. Обновите его.' : '';
      }
      const rows = new Map((admin.photos || []).map((row) => [row.photo_id, row]));
      for (const node of this.root.querySelectorAll?.('[data-photo-status-id]') || []) {
        const row = rows.get(node.dataset.photoStatusId);
        if (!row) continue;
        setText(node, '[data-photo-status-category]', row.category_label || '');
        setText(
          node,
          '[data-photo-status-stages]',
          (row.stages || []).map((stage) => `${stage.label}: ${stage.status_label}`).join(' · '),
        );
      }
    }

    renderBatches(batches) {
      const rows = new Map(batches.map((row) => [row.id, row]));
      for (const node of this.root.querySelectorAll?.('[data-batch-status-id]') || []) {
        const row = rows.get(node.dataset.batchStatusId);
        if (!row) continue;
        setText(node, '[data-batch-status-state]', batchState(row));
        setText(
          node,
          '[data-batch-status-progress]',
          `${row.confirmed_count} из ${row.expected_count} загружено · осталось ${row.unresolved_count}`,
        );
        const processing = node.querySelector?.('[data-batch-status-processing]');
        if (processing) renderProcessingSummary(processing, row.processing);
        if (row.can_close) {
          node.removeAttribute?.('data-unfinished-upload');
          node.querySelector?.('[data-resume-batch]')?.remove?.();
        }
      }
    }

    markStale() {
      this.root.dataset.statusStale = 'true';
      setText(this.root, '[data-event-photo-status-message]', 'Не удалось обновить статус');
      const updated = this.root.querySelector?.('[data-event-photo-status-updated-at]');
      if (updated?.textContent) updated.hidden = false;
    }

    blockRejectedQuery() {
      this.queryBlocked = true;
      this.refreshPending = false;
      this.clearTimer();
      this.markStale();
    }

    loseAccess() {
      this.accessLost = true;
      this.refreshPending = false;
      this.clearTimer();
      this.root.dataset.statusAccessLost = 'true';
      for (const node of this.root.querySelectorAll?.('[data-event-photo-private-status]') || []) {
        node.hidden = true;
      }
      setText(this.root, '[data-event-photo-status-message]', 'Доступ к статусам потерян');
    }

    onVisibilityChange() {
      if (!this.isVisible()) {
        this.clearTimer();
        return;
      }
      if (this.pendingHistoryBatchId) this.refreshHistory();
      this.refresh();
    }

    onFocus() {
      if (this.queryBlocked) return;
      this.refresh();
    }

    onUploadActivity(event) {
      if (typeof event?.detail?.active !== 'boolean') return;
      this.localUploadActive = event.detail.active;
      const batchId = event.detail.batchId;
      if (batchId && this.canUpload !== false) this.queueHistoryRefresh(String(batchId));
      this.refresh();
    }

    onManualRefresh() {
      this.queryBlocked = false;
      this.refresh();
    }

    onImportProgress(event) {
      if (String(event?.detail?.eventId) !== String(this.root.dataset.eventId)) return;
      this.refresh();
    }

    hasBatch(batchId) {
      return [...(this.root.querySelectorAll?.('[data-batch-status-id]') || [])]
        .some((node) => node.dataset.batchStatusId === batchId);
    }

    queueHistoryRefresh(batchId) {
      if (!this.root.dataset.batchHistoryUrl || this.hasBatch(batchId)) return;
      this.pendingHistoryBatchId = batchId;
      if (this.isVisible()) this.refreshHistory();
    }

    refreshHistory() {
      if (this.accessLost || this.canUpload === false || this.historyInFlight
        || !this.pendingHistoryBatchId || !this.isVisible()) return this.historyInFlight;
      const batchId = this.pendingHistoryBatchId;
      const url = new URL(this.root.dataset.batchHistoryUrl, this.window.location.href);
      url.searchParams.set('batch_id', batchId);
      this.historyInFlight = this.fetch(url.toString(), {
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        cache: 'no-store',
      })
        .then(async (response) => {
          if (response.status === 401) {
            this.loseAccess();
            return;
          }
          if (response.status === 403) {
            this.refresh();
            return;
          }
          if (!response.ok) throw new Error(`History request failed: ${response.status}`);
          const template = this.document.createElement('template');
          template.innerHTML = (await response.text()).trim();
          const replacement = template.content.firstElementChild;
          const current = this.root.querySelector?.('[data-batch-history-fragment]');
          if (!replacement || !current) return;
          current.replaceWith(replacement);
          if (this.pendingHistoryBatchId === batchId && this.hasBatch(batchId)) {
            this.pendingHistoryBatchId = null;
          }
          const pageUrl = new URL(this.window.location.href);
          pageUrl.searchParams.delete('batch_page');
          this.window.history?.replaceState?.({}, '', pageUrl.pathname + pageUrl.search);
          this.refresh();
        })
        .catch(() => {
          if (!this.accessLost) this.markStale();
        })
        .finally(() => {
          this.historyInFlight = null;
          if (this.pendingHistoryBatchId && this.pendingHistoryBatchId !== batchId) {
            this.refreshHistory();
          }
        });
      return this.historyInFlight;
    }

    captureFragmentFocus(fragment) {
      const active = this.document?.activeElement;
      if (!active || !fragment?.contains?.(active)) return null;
      return {
        id: active.id || '',
        name: active.getAttribute?.('name') || '',
        tagName: active.tagName || '',
        type: active.getAttribute?.('type') || '',
        selectionEnd: active.selectionEnd,
        selectionStart: active.selectionStart,
      };
    }

    restoreFragmentFocus(management, focus) {
      if (!focus) return;
      let target = focus.id ? this.document?.getElementById?.(focus.id) : null;
      if (!target && focus.name) {
        target = [...(management.querySelectorAll?.('[name]') || [])].find(
          (node) => node.getAttribute('name') === focus.name
            && node.tagName === focus.tagName
            && (node.getAttribute('type') || '') === focus.type,
        );
      }
      target?.focus?.({ preventScroll: true });
      if (target?.setSelectionRange && Number.isInteger(focus.selectionStart)) {
        target.setSelectionRange(focus.selectionStart, focus.selectionEnd);
      }
    }

    async onClick(event) {
      const historyPage = event.target.closest?.('[data-batch-history-page]');
      if (historyPage?.dataset?.batchHistoryPage) {
        const fragment = this.root.querySelector?.('[data-event-photo-fragment]');
        const destination = new URL(
          fragment?.dataset?.canonicalUrl || this.window.location.href,
          this.window.location.href,
        );
        destination.searchParams.set('batch_page', historyPage.dataset.batchHistoryPage);
        destination.searchParams.delete('batch_id');
        historyPage.href = destination.pathname + destination.search + destination.hash;
        return;
      }
      const trigger = event.target.closest?.('[data-event-photo-result-list-refresh]');
      if (!trigger) return;
      event.preventDefault?.();
      const management = this.root.querySelector?.('[data-event-photo-management-root]');
      const controller = management?.eventPhotoManagementController;
      const fragment = this.root.querySelector?.('[data-event-photo-fragment]');
      if (!controller || fragment?.dataset?.filterValid !== 'true') return;
      if (controller.filtersDirty) {
        setText(
          this.root,
          '[data-event-photo-status-message]',
          'Примените или сбросьте изменения фильтров перед обновлением списка.',
        );
        return;
      }
      const scrollX = this.window.scrollX || 0;
      const scrollY = this.window.scrollY || 0;
      const focus = this.captureFragmentFocus(fragment);
      await controller.refreshFromManagementUrl(fragment.dataset.canonicalUrl, 'replace', true);
      this.restoreFragmentFocus(management, focus);
      this.window.scrollTo?.(scrollX, scrollY);
    }
  }

  function bindEventPhotoStatus(root, environment = {}) {
    if (!root) return null;
    if (root.eventPhotoStatusController) return root.eventPhotoStatusController;
    root.eventPhotoStatusController = new EventPhotoStatusController(root, environment).start();
    return root.eventPhotoStatusController;
  }

  return {
    EventPhotoStatusController,
    FILTER_COUNT_EVENT,
    IMPORT_PROGRESS_EVENT,
    MANUAL_REFRESH_EVENT,
    MAX_BACKOFF_MS,
    NORMAL_INTERVAL_MS,
    UPLOAD_ACTIVITY_EVENT,
    bindEventPhotoStatus,
  };
});

if (typeof document !== 'undefined') {
  const start = () => {
    for (const root of document.querySelectorAll('[data-event-photo-status-root]')) {
      globalThis.FindMeEventPhotoStatus.bindEventPhotoStatus(root);
    }
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}
