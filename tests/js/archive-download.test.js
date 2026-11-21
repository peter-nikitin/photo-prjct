const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

async function submit(states) {
  let handler;
  const delays = [];
  const requests = [];
  const navigations = [];
  const nodes = Object.fromEntries(['button[type="submit"]', '[data-archive-label]', '[data-archive-spinner]', '[data-archive-pending]', '[role="alert"]'].map(key => [key, {}]));
  class Form {
    matches() { return true; }
    hasAttribute() { return false; }
    querySelector(key) { return nodes[key]; }
  }
  const form = new Form();
  form.action = '/prepare/';
  const context = {
    HTMLFormElement: Form, FormData: class {}, URL, WeakMap,
    document: { addEventListener(name, callback) { handler = callback; } },
    window: { location: { origin: 'https://example.test', assign(url) { navigations.push(url); } } },
    setTimeout(callback, delay) { delays.push(delay); callback(); },
    fetch: async (url) => {
      requests.push(url);
      return { ok: true, json: async () => url === '/prepare/' ? { status_url: '/archives/job/' } : { status: states.shift() } };
    },
  };
  vm.runInNewContext(fs.readFileSync('src/backend/static/ui/archive-download.js', 'utf8'), context);
  await handler({ target: form, preventDefault() {} });
  return { delays, requests, navigations, form, handler, nodes };
}

test('queued polls use only three early fast intervals, running stays responsive', async () => {
  const result = await submit(['queued', 'queued', 'queued', 'queued', 'queued', 'running', 'running', 'ready']);
  assert.deepEqual(result.delays, [500, 500, 500, 2000, 2000, 500, 500]);
  assert.equal(result.navigations.length, 1);
  assert.equal(result.navigations[0], 'https://example.test/archives/job/?download=1');
  assert.equal(result.nodes['button[type="submit"]'].disabled, false);
});

test('ready completes immediately and cached click skips preparation', async () => {
  const result = await submit(['ready']);
  assert.deepEqual(result.delays, []);
  const count = result.requests.length;
  await result.handler({ target: result.form, preventDefault() {} });
  assert.equal(result.requests.length, count);
  assert.equal(result.navigations.length, 2);
});

test('failed archive stops polling and displays error', async () => {
  const result = await submit(['queued', 'failed']);
  assert.deepEqual(result.delays, [500]);
  assert.equal(result.navigations.length, 0);
  assert.equal(result.nodes['[role="alert"]'].hidden, false);
});
