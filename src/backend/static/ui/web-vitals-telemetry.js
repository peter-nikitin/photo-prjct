(() => {
  const page = document.currentScript.dataset.webVitalsPage;
  const viewport = window.innerWidth < 768 ? 'narrow' : 'wide';
  const pending = new Map();
  const sent = new Set();

  function collect(metric) {
    const key = `${metric.name}:${metric.id}`;
    if (!sent.has(key)) {
      pending.set(key, metric);
    }
  }

  function flush() {
    if (pending.size === 0) return;
    const metrics = {};
    for (const [key, metric] of pending) {
      metrics[metric.name] = metric.value;
      sent.add(key);
    }
    pending.clear();
    fetch('/telemetry/web-vitals/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page, viewport, metrics }),
      keepalive: true,
      credentials: 'omit',
      referrerPolicy: 'no-referrer',
      mode: 'same-origin',
      redirect: 'error',
    }).catch(() => {});
  }

  webVitals.onLCP(collect, { reportAllChanges: true });
  webVitals.onINP(collect, { reportAllChanges: true });
  webVitals.onCLS(collect, { reportAllChanges: true });
  webVitals.onTTFB((metric) => {
    // Restoring a document from the back/forward cache makes no HTML request.
    if (metric.navigationType !== 'back-forward-cache') collect(metric);
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flush();
  });
})();
