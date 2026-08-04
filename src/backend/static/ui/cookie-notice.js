(() => {
  const notice = document.querySelector('[data-cookie-notice]');
  const accept = document.querySelector('[data-cookie-notice-accept]');
  const storageKey = 'findme_cookie_notice';
  const version = '2026-08-02';

  if (!notice || !accept) {
    return;
  }

  try {
    if (window.localStorage.getItem(storageKey) === version) {
      notice.hidden = true;
    }
  } catch {
    // Storage is optional: leave the notice available when a browser blocks it.
  }

  accept.addEventListener('click', () => {
    try {
      window.localStorage.setItem(storageKey, version);
    } catch {
      return;
    }

    notice.hidden = true;
  });
})();
