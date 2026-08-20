(function commerceCartModule(globalScope, factory) {
  const api = factory(globalScope);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (globalScope) globalScope.FindMeCommerceCart = api;
})(typeof globalThis === 'undefined' ? this : globalThis, function buildCommerceCart(globalScope) {
  'use strict';

  const RETRY_MESSAGE = 'Не удалось обновить корзину. Попробуйте ещё раз.';

  function controlState(form) {
    return form?.querySelector?.('[name="selected"]')?.value === '0';
  }

  function updateControl(form, selected) {
    const button = form?.querySelector?.('[data-cart-button]');
    const selectedInput = form?.querySelector?.('[name="selected"]');
    const icon = form?.querySelector?.('[data-cart-icon] use');
    if (!button || !selectedInput) return;
    selectedInput.value = selected ? '0' : '1';
    button.setAttribute('aria-label', selected ? 'Удалить из корзины' : 'Добавить в корзину');
    button.setAttribute('title', selected ? 'Удалить из корзины' : 'Добавить в корзину');
    button.setAttribute('aria-pressed', selected ? 'true' : 'false');
    button.classList?.toggle?.('is-selected', selected);
    if (icon) {
      const sprite = icon.getAttribute?.('href')?.split('#')[0] || '/static/ui/icons.svg';
      icon.setAttribute('href', `${sprite}#${selected ? 'cart-check' : 'cart-plus'}`);
    }
  }

  function setText(elements, text) {
    elements.forEach((element) => {
      element.textContent = `${element.dataset?.cartCountLabel || ''}${text}`;
      const cartLink = element.closest?.('.event-cart-link');
      if (cartLink) cartLink.setAttribute('aria-label', `Корзина: ${text}`);
    });
  }

  function updatePrice(root, snapshot) {
    if (typeof snapshot.unit_price_display !== 'string') return;
    root.querySelectorAll('[data-cart-price][data-photo-id]').forEach((surface) => {
      surface.textContent = snapshot.unit_price_display;
    });
  }

  function updateLightboxDescription(root, snapshot) {
    if (!snapshot.photo_id && typeof snapshot.unit_price_display !== 'string') return;
    const documentObject = root.ownerDocument || globalScope.document;
    if (!documentObject?.createElement) return;
    root.querySelectorAll('.gallery-card[data-photo-id]').forEach((card) => {
      const link = card.querySelector?.('.gallery-card-link[data-description]');
      const description = link?.getAttribute?.('data-description');
      if (!description) return;
      const holder = documentObject.createElement('div');
      holder.innerHTML = description;
      if (card.dataset.photoId === snapshot.photo_id) {
        holder.querySelectorAll('[data-cart-form]').forEach((form) => updateControl(form, snapshot.selected));
      }
      if (typeof snapshot.unit_price_display === 'string') {
        holder.querySelectorAll('[data-cart-price]').forEach((surface) => {
          surface.textContent = snapshot.unit_price_display;
        });
      }
      link.setAttribute('data-description', holder.innerHTML);
    });
  }

  function showError(root) {
    const error = root?.querySelector?.('[data-cart-error]');
    if (!error) return;
    error.textContent = RETRY_MESSAGE;
    error.hidden = false;
  }

  function clearError(root) {
    const error = root?.querySelector?.('[data-cart-error]');
    if (error) error.hidden = true;
  }

  function applySnapshot(root, snapshot) {
    if (snapshot.photo_id) {
      root.querySelectorAll('[data-cart-form]').forEach((form) => {
        if (form.dataset.photoId === snapshot.photo_id) updateControl(form, snapshot.selected);
      });
    }
    updatePrice(root, snapshot);
    updateLightboxDescription(root, snapshot);
    setText(root.querySelectorAll('[data-cart-count]'), snapshot.item_count);
    const total = root.querySelector('[data-cart-total]');
    if (total) total.textContent = `${total.dataset?.cartTotalLabel || ''}${snapshot.total_display}`;
    if (snapshot.photo_id && snapshot.selected === false && root.querySelectorAll) {
      root.querySelectorAll('[data-cart-item][data-photo-id]').forEach((item) => {
        if (item.dataset.photoId === snapshot.photo_id) item.remove?.();
      });
    }
    if (snapshot.item_count === 0) {
      root.querySelectorAll('[data-cart-item][data-photo-id]').forEach((item) => item.remove?.());
      const populated = root.querySelector('[data-cart-populated]');
      if (populated) populated.hidden = true;
      const empty = root.querySelector('[data-cart-empty]');
      if (empty) empty.hidden = false;
    }
    if (typeof globalScope.CustomEvent === 'function' && root.dispatchEvent) {
      root.dispatchEvent(new globalScope.CustomEvent('cart:snapshot-applied', { detail: snapshot }));
    }
  }

  function sameOriginAction(form, locationObject) {
    if (!locationObject?.origin) return true;
    try {
      return new URL(form.action, locationObject.href).origin === locationObject.origin;
    } catch {
      return false;
    }
  }

  function initializeCommerceCart(root, dependencies = {}) {
    const fetchFunction = dependencies.fetch || globalScope.fetch;
    const FormDataConstructor = dependencies.FormData || globalScope.FormData;
    const locationObject = dependencies.location || globalScope.location;
    if (!root?.addEventListener || typeof fetchFunction !== 'function' || !FormDataConstructor) return;

    root.addEventListener('submit', (event) => {
      const form = event.target?.closest?.('[data-cart-form]') || event.target;
      if (!form?.dataset || !Object.hasOwn(form.dataset, 'cartForm')) return;
      if (!sameOriginAction(form, locationObject)) return;
      const button = form.querySelector?.('[data-cart-button]');
      if (!button || button.disabled) return;
      event.preventDefault();
      button.disabled = true;
      clearError(root);
      const csrf = form.querySelector?.('[name="csrfmiddlewaretoken"]')?.value || '';
      const data = new FormDataConstructor(form);
      void fetchFunction(form.action, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          Accept: 'application/json',
          ...(csrf ? { 'X-CSRFToken': csrf } : {}),
        },
        body: data,
      })
        .then(async (response) => {
          if (!response.ok) throw new Error('Cart mutation failed');
          const snapshot = await response.json();
          if (!snapshot || typeof snapshot.item_count !== 'number' || !snapshot.total_display) {
            throw new Error('Invalid cart mutation response');
          }
          applySnapshot(root, snapshot);
        })
        .catch(() => showError(root))
        .finally(() => { button.disabled = false; });
    });
  }

  return { RETRY_MESSAGE, applySnapshot, controlState, initializeCommerceCart };
});

if (typeof document !== 'undefined') {
  const start = () => globalThis.FindMeCommerceCart.initializeCommerceCart(document);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}
