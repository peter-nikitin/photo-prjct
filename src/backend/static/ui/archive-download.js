document.addEventListener('submit', async (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !form.matches('[data-archive-request]')) return;

  event.preventDefault();
  const button = form.querySelector('button[type="submit"]');
  const error = form.querySelector('[role="alert"]');
  if (button.disabled) return;
  button.disabled = true;
  error.hidden = true;

  try {
    // Send the origin for Django's CSRF check without sending the private page URL.
    const response = await fetch(form.action, {
      method: 'POST',
      mode: 'same-origin',
      referrerPolicy: 'origin',
      body: new FormData(form),
    });
    if (!response.ok || !response.redirected) {
      error.hidden = false;
      return;
    }
    window.location.assign(response.url);
  } catch {
    error.hidden = false;
  } finally {
    button.disabled = false;
  }
});
