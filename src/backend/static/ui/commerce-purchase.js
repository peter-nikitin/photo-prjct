(function commercePurchaseModule(globalScope, factory) {
  const api = factory(globalScope);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (globalScope) globalScope.FindMeCommercePurchase = api;
})(typeof globalThis === 'undefined' ? this : globalThis, function buildCommercePurchase(globalScope) {
  'use strict';

  const POLL_INTERVAL_MS = 4_000;
  const MAX_STATUS_REQUESTS = 6;
  const PENDING_STATUSES = new Set(['pending', 'superseded']);
  const TERMINAL_STATUSES = new Set(['paid', 'canceled']);
  const STATUS_COPY = {
    paid: 'Заказ оплачен. Обновляем страницу.',
    canceled: 'Оплата не завершена.',
  };
  const POLL_FAILURE_MESSAGE = 'Не удалось проверить оплату. Обновите страницу позднее.';
  const RESEND_SUCCESS_MESSAGE = 'Письмо со ссылкой поставлено в очередь.';
  const RESEND_RATE_LIMIT_MESSAGE = 'Подождите минуту перед повторной отправкой письма.';
  const RESEND_FAILURE_MESSAGE = 'Не удалось отправить письмо. Попробуйте ещё раз.';

  function sameOriginUrl(value, locationObject) {
    if (!value || !locationObject?.origin) return null;
    try {
      const url = new URL(value, locationObject.href);
      return url.origin === locationObject.origin ? url : null;
    } catch {
      return null;
    }
  }

  function setMessage(element, message) {
    if (!element) return;
    element.textContent = message;
    element.hidden = !message;
  }

  function updateStatus(statusElement, status) {
    if (!statusElement || !TERMINAL_STATUSES.has(status)) return;
    statusElement.dataset.orderStatus = status;
    statusElement.textContent = STATUS_COPY[status];
    statusElement.focus?.({ preventScroll: true });
  }

  function initializeStatusPolling(root, dependencies) {
    const fetchFunction = dependencies.fetch || globalScope.fetch;
    const setTimeoutFunction = dependencies.setTimeout || globalScope.setTimeout;
    const locationObject = dependencies.location || globalScope.location;
    const statusElement = root?.querySelector?.('[data-order-status]');
    const pollMessage = root?.querySelector?.('[data-order-poll-message]');
    const statusUrl = sameOriginUrl(root?.dataset?.orderStatusUrl, locationObject);
    if (
      !root
      || typeof fetchFunction !== 'function'
      || typeof setTimeoutFunction !== 'function'
      || !PENDING_STATUSES.has(root.dataset?.orderStatus)
      || !statusUrl
    ) return;

    let requestCount = 0;
    let stopped = false;
    const poll = () => {
      if (stopped || requestCount >= MAX_STATUS_REQUESTS) return;
      requestCount += 1;
      void fetchFunction(statusUrl.pathname + statusUrl.search, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      })
        .then(async (response) => {
          if (!response.ok) throw new Error('Order status request failed');
          const payload = await response.json();
          const status = payload?.status;
          if (!PENDING_STATUSES.has(status) && !TERMINAL_STATUSES.has(status)) {
            throw new Error('Invalid Order status response');
          }
          if (TERMINAL_STATUSES.has(status)) {
            stopped = true;
            updateStatus(statusElement, status);
            if (status === 'paid' && root.dataset.orderUrl && typeof locationObject.assign === 'function') {
              locationObject.assign(root.dataset.orderUrl);
            }
            return;
          }
          setMessage(pollMessage, '');
        })
        .catch(() => setMessage(pollMessage, POLL_FAILURE_MESSAGE))
        .finally(() => {
          if (!stopped && requestCount < MAX_STATUS_REQUESTS) {
            setTimeoutFunction(poll, POLL_INTERVAL_MS);
          }
        });
    };
    poll();
  }

  function initializeResend(root, dependencies) {
    const fetchFunction = dependencies.fetch || globalScope.fetch;
    const FormDataConstructor = dependencies.FormData || globalScope.FormData;
    const locationObject = dependencies.location || globalScope.location;
    if (!root?.addEventListener || typeof fetchFunction !== 'function' || !FormDataConstructor) return;

    root.addEventListener('submit', (event) => {
      if (event.defaultPrevented) return;
      const form = event.target?.closest?.('[data-order-resend]') || event.target;
      if (!form?.dataset || !Object.hasOwn(form.dataset, 'orderResend')) return;
      if (!sameOriginUrl(form.action, locationObject)) return;
      const button = form.querySelector?.('[data-order-resend-button]');
      if (!button || button.disabled) return;
      event.preventDefault();
      button.disabled = true;
      const message = root.querySelector?.('[data-order-resend-message]');
      setMessage(message, '');
      const csrf = form.querySelector?.('[name="csrfmiddlewaretoken"]')?.value || '';
      void fetchFunction(form.action, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          Accept: 'application/json',
          ...(csrf ? { 'X-CSRFToken': csrf } : {}),
        },
        body: new FormDataConstructor(form),
      })
        .then((response) => {
          if (response.ok) {
            setMessage(message, RESEND_SUCCESS_MESSAGE);
          } else if (response.status === 429) {
            setMessage(message, RESEND_RATE_LIMIT_MESSAGE);
          } else {
            setMessage(message, RESEND_FAILURE_MESSAGE);
          }
        })
        .catch(() => setMessage(message, RESEND_FAILURE_MESSAGE))
        .finally(() => { button.disabled = false; });
    });
  }

  function initializeCommercePurchase(root, dependencies = {}) {
    initializeStatusPolling(root, dependencies);
    initializeResend(root, dependencies);
  }

  return {
    MAX_STATUS_REQUESTS,
    POLL_FAILURE_MESSAGE,
    POLL_INTERVAL_MS,
    RESEND_FAILURE_MESSAGE,
    RESEND_RATE_LIMIT_MESSAGE,
    RESEND_SUCCESS_MESSAGE,
    initializeCommercePurchase,
    sameOriginUrl,
  };
});

if (typeof document !== 'undefined') {
  const start = () => globalThis.FindMeCommercePurchase.initializeCommercePurchase(
    document.querySelector('[data-order-root]'),
  );
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}
