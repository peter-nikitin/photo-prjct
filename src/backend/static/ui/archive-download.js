(() => {
  const readyDownloads = new WeakMap();
  const pollIntervalMs = 2000;

  let downloadFrame;

  function startDownload(url) {
    downloadFrame?.remove();
    downloadFrame = document.createElement('iframe');
    downloadFrame.hidden = true;
    downloadFrame.referrerPolicy = 'no-referrer';
    downloadFrame.sandbox = 'allow-downloads';
    downloadFrame.src = url;
    document.body.append(downloadFrame);
  }

  async function getJson(url, options = {}) {
    const response = await fetch(url, { mode: 'same-origin', ...options });
    if (!response.ok) throw new Error('Archive request failed');
    return response.json();
  }

  async function waitForArchive(statusUrl) {
    for (;;) {
      const { status } = await getJson(statusUrl);
      if (status === 'ready') return;
      if (status !== 'queued' && status !== 'running') throw new Error('Archive failed');
      await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
    }
  }

  document.addEventListener('submit', async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.matches('[data-archive-request]')) return;

    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    if (button.disabled) return;
    const readyDownload = readyDownloads.get(form);
    if (readyDownload) {
      startDownload(readyDownload);
      return;
    }

    const label = form.querySelector('[data-archive-label]');
    const spinner = form.querySelector('[data-archive-spinner]');
    const pending = form.querySelector('[data-archive-pending]');
    const error = form.querySelector('[role="alert"]');
    button.disabled = true;
    label.hidden = true;
    spinner.hidden = pending.hidden = false;
    error.hidden = true;

    try {
      // Django's HTTPS CSRF check needs the origin. The document URL is private.
      const { status_url: statusUrl } = await getJson(form.action, {
        method: 'POST',
        referrerPolicy: 'origin',
        body: new FormData(form),
      });
      await waitForArchive(statusUrl);
      const downloadUrl = new URL(statusUrl, window.location.origin);
      downloadUrl.searchParams.set('download', '1');
      readyDownloads.set(form, downloadUrl.href);
      startDownload(downloadUrl.href);
    } catch {
      error.hidden = false;
    } finally {
      button.disabled = false;
      label.hidden = false;
      spinner.hidden = pending.hidden = true;
    }
  });
})();
