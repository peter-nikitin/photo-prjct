(function eventPhotoManagementModule(globalScope, factory) {
  const api = factory(globalScope);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (globalScope) globalScope.FindMeEventPhotoManagement = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, (globalScope) => {
  class SelectionModel {
    constructor() {
      this.clear();
    }

    clear() {
      this.mode = 'none';
      this.photoIds = new Set();
      this.count = 0;
      this.filterQuery = '';
    }

    clearForFilterEdit() {
      this.clear();
    }

    toggle(photoId, checked) {
      if (this.mode === 'all_filtered') throw new Error('Per-photo changes are disabled in all-filtered mode.');
      this.mode = 'explicit';
      if (checked) this.photoIds.add(photoId);
      else this.photoIds.delete(photoId);
      this.count = this.photoIds.size;
      if (!this.count) this.mode = 'none';
    }

    selectPage(photoIds) {
      if (this.mode === 'all_filtered') this.clear();
      this.mode = 'explicit';
      for (const photoId of photoIds) this.photoIds.add(photoId);
      this.count = this.photoIds.size;
      if (!this.count) this.mode = 'none';
    }

    selectAllFiltered(count, filterQuery) {
      this.clear();
      if (!count) return;
      this.mode = 'all_filtered';
      this.count = count;
      this.filterQuery = filterQuery;
    }

    updateFilteredCount(count) {
      if (this.mode === 'all_filtered') this.count = count;
    }

    get perPhotoDisabled() {
      return this.mode === 'all_filtered';
    }

    payload() {
      if (this.mode === 'explicit') {
        return { selection_mode: 'explicit', photo_id: [...this.photoIds] };
      }
      if (this.mode === 'all_filtered') {
        return { selection_mode: 'all_filtered', filter_query: this.filterQuery };
      }
      return { selection_mode: 'none' };
    }
  }

  function replaceManagementFragment(managementRoot, replacement) {
    const current = managementRoot.querySelector('[data-event-photo-fragment]');
    if (!current) throw new Error('Administrative photo fragment is missing.');
    current.replaceWith(replacement);
  }

  function folderTarget(document, folder) {
    const label = document.createElement('label');
    label.className = 'folder-target';
    label.dataset.folderTarget = '';
    label.dataset.folderId = folder ? String(folder.id) : '';
    label.dataset.folderName = folder ? folder.name : 'Без папки';
    label.dataset.defaultCopy = 'Перетащите JPEG сюда';
    const copy = document.createElement('span');
    copy.className = 'folder-target-copy';
    const strong = document.createElement('strong');
    strong.dataset.folderTargetCopy = '';
    strong.textContent = 'Перетащите JPEG сюда';
    const destination = document.createElement('span');
    destination.textContent = folder ? `Загрузить в «${folder.name}»` : 'Загрузить без папки';
    copy.append(strong, destination);
    const picker = document.createElement('span');
    picker.className = 'button file-picker';
    picker.textContent = 'Выбрать фотографии';
    const input = document.createElement('input');
    input.className = 'visually-hidden';
    input.dataset.folderTargetInput = '';
    input.type = 'file';
    input.accept = 'image/jpeg,.jpg,.jpeg';
    input.multiple = true;
    label.append(copy, picker, input);
    return label;
  }

  function refreshFolderDestinations(document, folders) {
    for (const container of document.querySelectorAll('[data-folder-targets]')) {
      const original = container.querySelector?.('[data-folder-target][data-folder-id=""]');
      const render = (folder) => {
        if (!original?.cloneNode) return folderTarget(document, folder);
        const target = original.cloneNode(true);
        target.dataset.folderId = folder ? String(folder.id) : '';
        target.dataset.folderName = folder ? folder.name : 'Без папки';
        const destination = target.querySelector('.folder-target-copy > span');
        if (destination) destination.textContent = folder ? `Загрузить в «${folder.name}»` : 'Загрузить без папки';
        const input = target.querySelector('[data-folder-target-input]');
        if (input) input.value = '';
        return target;
      };
      container.replaceChildren(render(null), ...folders.map((folder) => render(folder)));
    }
    for (const select of document.querySelectorAll('[data-import-folder]')) {
      const selected = select.value;
      const eventId = select.closest?.('[data-import-root]')?.dataset.eventId || select.dataset?.eventId || '';
      const rows = [{ id: '', name: 'Без папки' }, ...folders];
      const options = rows.map((folder) => {
        const option = document.createElement('option');
        option.value = String(folder.id);
        option.textContent = folder.name;
        option.dataset.importFolderOption = '';
        option.dataset.eventId = eventId;
        return option;
      });
      select.replaceChildren(...options);
      select.value = rows.some((folder) => String(folder.id) === selected) ? selected : '';
    }
    const EventType = document.defaultView?.Event || globalScope.Event;
    for (const root of document.querySelectorAll('[data-import-root]')) {
      root.dispatchEvent(new EventType('findme:event-photo-folders-refreshed'));
    }
  }

  function errorText(payload) {
    if (!payload?.errors) return 'Не удалось выполнить действие.';
    return Object.values(payload.errors).flat().join(' ');
  }

  class EventPhotoManagementController {
    constructor(root, environment = globalScope) {
      this.root = root;
      this.environment = environment;
      this.document = root.ownerDocument || environment.document;
      this.selection = new SelectionModel();
      this.filtersDirty = false;
      this.onClick = this.onClick.bind(this);
      this.onSubmit = this.onSubmit.bind(this);
      this.onFilterEdit = this.onFilterEdit.bind(this);
      this.onFilterCount = this.onFilterCount.bind(this);
      this.onPopState = this.onPopState.bind(this);
    }

    start() {
      this.root.addEventListener('click', this.onClick);
      this.root.addEventListener('submit', this.onSubmit);
      this.root.addEventListener('input', this.onFilterEdit);
      this.root.addEventListener('change', this.onFilterEdit);
      this.environment.addEventListener?.('popstate', this.onPopState);
      this.document.addEventListener?.('findme:event-photo-filter-count', this.onFilterCount);
      this.renderSelection();
      return this;
    }

    fragment() {
      return this.root.querySelector('[data-event-photo-fragment]');
    }

    renderSelection() {
      const fragment = this.fragment();
      if (!fragment) return;
      const selectionBlocked = this.filtersDirty || fragment.dataset.filterValid !== 'true';
      for (const checkbox of fragment.querySelectorAll('[data-photo-select]')) {
        checkbox.disabled = selectionBlocked || this.selection.perPhotoDisabled;
        checkbox.checked = this.selection.mode === 'explicit' && this.selection.photoIds.has(checkbox.value);
      }
      for (const button of fragment.querySelectorAll('[data-select-page], [data-select-all-filtered]')) {
        button.disabled = selectionBlocked;
      }
      const summary = fragment.querySelector('[data-selection-summary]');
      if (summary) {
        summary.textContent = this.selection.mode === 'all_filtered'
          ? `Выбрано ${this.selection.count} фотографий во всех результатах на момент действия`
          : this.selection.count ? `Выбрано: ${this.selection.count}` : 'Ничего не выбрано';
      }
      for (const button of fragment.querySelectorAll('[data-photo-action-form] button[type="submit"]')) {
        button.disabled = this.selection.count === 0 || selectionBlocked;
      }
    }

    clearSelection() {
      this.selection.clear();
      this.renderSelection();
    }

    onFilterEdit(event) {
      if (!event.target.closest?.('[data-event-photo-filter-form]')) return;
      this.filtersDirty = true;
      this.selection.clearForFilterEdit();
      this.renderSelection();
    }

    onFilterCount(event) {
      const count = Number(event?.detail?.count);
      const fragment = this.fragment();
      if (!fragment || fragment.dataset.filterValid !== 'true'
        || !Number.isSafeInteger(count) || count < 0) return;
      fragment.dataset.filteredCount = String(count);
      const output = fragment.querySelector('[data-event-photo-filtered-count]');
      if (output) output.textContent = String(count);
      this.selection.updateFilteredCount(count);
      this.renderSelection();
    }

    async onClick(event) {
      const target = event.target.closest?.('a, button, input');
      if (!target) return;
      if (target.matches('[data-photo-select]')) {
        if (this.filtersDirty || this.fragment().dataset.filterValid !== 'true') {
          this.renderSelection();
          return;
        }
        this.selection.toggle(target.value, target.checked);
        this.renderSelection();
        return;
      }
      if (target.matches('[data-select-page]')) {
        if (this.filtersDirty || this.fragment().dataset.filterValid !== 'true') return;
        this.selection.selectPage([...this.fragment().querySelectorAll('[data-photo-select]')].map((item) => item.value));
        this.renderSelection();
        return;
      }
      if (target.matches('[data-select-all-filtered]')) {
        const fragment = this.fragment();
        if (this.filtersDirty || fragment.dataset.filterValid !== 'true') return;
        this.selection.selectAllFiltered(Number(fragment.dataset.filteredCount), fragment.dataset.canonicalQuery || '');
        this.renderSelection();
        return;
      }
      if (target.matches('[data-clear-selection]')) {
        this.clearSelection();
        return;
      }
      if (target.matches('[data-event-photo-page]')) {
        event.preventDefault();
        await this.refreshFromManagementUrl(target.href, 'push', true);
        return;
      }
      if (target.matches('[data-event-photo-filter-reset], .event-photo-summary a')) {
        event.preventDefault();
        this.clearSelection();
        await this.refreshFromManagementUrl(target.href, 'push');
      }
    }

    async onSubmit(event) {
      const form = event.target;
      if (form.matches('[data-event-photo-filter-form]')) {
        event.preventDefault();
        this.filtersDirty = true;
        this.clearSelection();
        const query = new this.environment.URLSearchParams(new this.environment.FormData(form));
        query.delete('page');
        const current = new this.environment.URL(this.environment.location.href);
        const batchPage = current.searchParams.get('batch_page');
        if (batchPage) query.set('batch_page', batchPage);
        const url = `${current.pathname}${query.toString() ? `?${query}` : ''}`;
        await this.refreshFromManagementUrl(url, 'push');
        return;
      }
      if (form.matches('[data-folder-create-form], [data-folder-rename-form], [data-folder-delete-form]')) {
        event.preventDefault();
        this.clearSelection();
        await this.submitFolderForm(form);
        return;
      }
      if (form.matches('[data-photo-action-form]')) {
        event.preventDefault();
        await this.submitAction(form, event.submitter?.value);
      }
    }

    async request(url, options = {}) {
      const response = await this.environment.fetch(url, {
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'XMLHttpRequest', ...(options.headers || {}) },
        ...options,
      });
      if (response.status === 401 || response.status === 403) {
        this.showMessage('Доступ изменился. Обновите страницу.');
      }
      return response;
    }

    async refreshFromManagementUrl(url, historyMode, preserveSameFilter = false) {
      const managementUrl = new this.environment.URL(url, this.environment.location.href);
      const fragmentUrl = new this.environment.URL(this.root.dataset.resultsUrl, this.environment.location.href);
      fragmentUrl.search = managementUrl.search;
      const response = await this.request(fragmentUrl);
      if (![200, 422].includes(response.status)) return;
      const previousFilterQuery = this.fragment()?.dataset.canonicalQuery || '';
      const html = await response.text();
      const template = this.document.createElement('template');
      template.innerHTML = html.trim();
      const replacement = template.content.firstElementChild;
      replaceManagementFragment(this.root, replacement);
      if (response.status === 200) {
        this.filtersDirty = false;
        if (!preserveSameFilter || replacement.dataset.canonicalQuery !== previousFilterQuery) {
          this.selection.clear();
        }
        const canonical = response.headers.get('X-Event-Photo-Canonical-Url') || managementUrl.pathname + managementUrl.search;
        const currentUrl = new this.environment.URL(
          this.environment.location.href,
          this.environment.location.href,
        );
        const batchPage = managementUrl.searchParams.get('batch_page')
          || currentUrl.searchParams.get('batch_page');
        const finalUrl = new this.environment.URL(canonical, this.environment.location.href);
        if (batchPage) finalUrl.searchParams.set('batch_page', batchPage);
        const historyMethod = historyMode === 'push' ? 'pushState' : 'replaceState';
        this.environment.history[historyMethod]({}, '', finalUrl.pathname + finalUrl.search);
        this.document.dispatchEvent(new this.environment.Event('findme:event-photo-status-refresh'));
      } else {
        this.filtersDirty = true;
      }
      this.renderSelection();
    }

    async submitFolderForm(form) {
      const response = await this.request(form.action, {
        method: 'POST',
        body: new this.environment.FormData(form),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        this.showFormError(form, errorText(payload));
        return;
      }
      refreshFolderDestinations(this.document, payload.folders || []);
      this.showMessage('Папки обновлены.');
      const current = new this.environment.URL(this.environment.location.href);
      if (payload.deleted_folder_id) {
        const retained = current.searchParams.getAll('folder').filter(
          (value) => value !== String(payload.deleted_folder_id),
        );
        if (retained.length !== current.searchParams.getAll('folder').length) {
          current.searchParams.delete('folder');
          retained.forEach((value) => current.searchParams.append('folder', value));
          current.searchParams.delete('page');
        }
      }
      await this.refreshFromManagementUrl(current, 'replace');
    }

    async submitAction(form, action) {
      if (this.filtersDirty || this.fragment().dataset.filterValid !== 'true') {
        this.showFormError(form, 'Примените корректные фильтры перед массовым действием.');
        return;
      }
      if (!action || !this.selection.count) return;
      const data = new this.environment.FormData(form);
      data.set('action', action);
      const payload = this.selection.payload();
      data.set('selection_mode', payload.selection_mode);
      if (action !== 'move') data.delete('target_folder');
      if (payload.selection_mode === 'explicit') {
        data.delete('photo_id');
        payload.photo_id.forEach((photoId) => data.append('photo_id', photoId));
      } else {
        for (const [name, value] of new this.environment.URLSearchParams(payload.filter_query)) {
          data.append(name, value);
        }
      }
      const response = await this.request(form.action, { method: 'POST', body: data });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        this.showFormError(form, errorText(result));
        return;
      }
      this.clearSelection();
      this.showMessage(`Изменено фотографий: ${result.changed_count}.`);
      await this.refreshFromManagementUrl(this.environment.location.href, 'replace');
    }

    showFormError(form, message) {
      const output = form.querySelector('[data-form-errors]');
      if (output) output.textContent = message;
      else this.showMessage(message);
    }

    showMessage(message) {
      const output = this.document.querySelector('[data-event-photo-status-message]');
      if (output) output.textContent = message;
    }

    onPopState() {
      return this.refreshFromManagementUrl(this.environment.location.href, 'replace', true);
    }
  }

  function bindEventPhotoManagement(root, environment = globalScope) {
    if (!root) return null;
    if (root.eventPhotoManagementController) return root.eventPhotoManagementController;
    root.eventPhotoManagementController = new EventPhotoManagementController(root, environment).start();
    return root.eventPhotoManagementController;
  }

  return {
    EventPhotoManagementController,
    SelectionModel,
    bindEventPhotoManagement,
    refreshFolderDestinations,
    replaceManagementFragment,
  };
}));

if (typeof document !== 'undefined') {
  const start = () => globalThis.FindMeEventPhotoManagement.bindEventPhotoManagement(
    document.querySelector('[data-event-photo-management-root]'),
  );
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}
