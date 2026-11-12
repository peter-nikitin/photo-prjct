const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('sends one bounded packet on hidden and does not repeat the same metric IDs', () => {
  const calls = [];
  const callbacks = {};
  let hidden;
  const document = {
    currentScript: { dataset: { webVitalsPage: 'catalog' } },
    visibilityState: 'visible',
    addEventListener: (event, callback) => { if (event === 'visibilitychange') hidden = callback; },
  };
  const context = {
    document,
    window: { innerWidth: 767 },
    webVitals: {
      onLCP: callback => { callbacks.LCP = callback; },
      onINP: callback => { callbacks.INP = callback; },
      onCLS: callback => { callbacks.CLS = callback; },
      onTTFB: callback => { callbacks.TTFB = callback; },
    },
    fetch: (...args) => { calls.push(args); return Promise.resolve(); },
  };
  vm.runInNewContext(fs.readFileSync('src/backend/static/ui/web-vitals-telemetry.js', 'utf8'), context);
  callbacks.LCP({ name: 'LCP', id: 'a', value: 2500 });
  callbacks.LCP({ name: 'LCP', id: 'a', value: 2600 });
  callbacks.CLS({ name: 'CLS', id: 'b', value: 0.1 });
  callbacks.INP({ name: 'INP', id: 'c', value: 180 });
  callbacks.TTFB({ name: 'TTFB', id: 'd', value: 800, navigationType: 'navigate' });
  document.visibilityState = 'hidden';
  hidden();
  hidden();
  assert.equal(calls.length, 1);
  assert.deepEqual(JSON.parse(calls[0][1].body), {
    page: 'catalog', viewport: 'narrow', metrics: { LCP: 2600, CLS: 0.1, INP: 180, TTFB: 800 },
  });
  assert.equal(calls[0][0], '/telemetry/web-vitals/');
  assert.equal(calls[0][1].credentials, 'omit');
  assert.equal(calls[0][1].referrerPolicy, 'no-referrer');
  assert.equal(calls[0][1].redirect, 'error');
  assert.equal(calls[0][1].mode, 'same-origin');
  assert.equal(calls[0][1].keepalive, true);
  callbacks.LCP({ name: 'LCP', id: 'a', value: 2700 });
  hidden();
  assert.equal(calls.length, 1);
  callbacks.TTFB({ name: 'TTFB', id: 'restored-navigation', value: 0, navigationType: 'back-forward-cache' });
  callbacks.LCP({ name: 'LCP', id: 'new-navigation', value: 1100 });
  hidden();
  assert.equal(calls.length, 2);
  assert.deepEqual(JSON.parse(calls[1][1].body).metrics, { LCP: 1100 });
});
