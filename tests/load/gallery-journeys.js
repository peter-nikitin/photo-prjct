import { check, sleep } from 'k6';
import { scenario } from 'k6/execution';
import http from 'k6/http';
import { Counter, Rate, Trend } from 'k6/metrics';
import crypto from 'k6/crypto';

const base = (__ENV.BASE_URL || '').replace(/\/$/, '');
const event = __ENV.EVENT_SLUG || '';
const profile = __ENV.PROFILE || 'smoke';
const rates = {
  pagination: 90,
  folder: 45,
  time: 30,
  folder_and_time: 30,
  gallery_face: 45,
  selfie: 15,
  find_by_result: 15,
  add_selfie: 15,
  negative_feedback: 15,
};
const selected = __ENV.JOURNEY ? [__ENV.JOURNEY] : Object.keys(rates);
const smoke = profile === 'smoke';
const searchFlows = ['gallery_face', 'selfie', 'find_by_result', 'add_selfie', 'negative_feedback'];
const uploadFlows = ['selfie', 'add_selfie'];
const previewCount = Number(__ENV.PREVIEW_COUNT || '6');
if (!Number.isSafeInteger(previewCount) || previewCount < 0 || previewCount > 20) throw new Error('PREVIEW_COUNT must be 0..20.');
const deadlineMs = 120_000;

if (!/^https?:\/\/[^/?#@]+$/.test(base) || !/^[\w-]+$/.test(event)) {
  throw new Error('Set BASE_URL and EVENT_SLUG.');
}
if (!['smoke', '60', '300'].includes(profile) || selected.some((name) => !Object.hasOwn(rates, name))) {
  throw new Error('Use PROFILE=smoke, 60 or 300 and a valid JOURNEY.');
}
if (!__ENV.JOURNEY_CASES_FILE) throw new Error('Set JOURNEY_CASES_FILE.');
const fixtures = JSON.parse(open(__ENV.JOURNEY_CASES_FILE));
if (fixtures._meta && fixtures._meta.eventSlug !== event) throw new Error('Fixture belongs to a different event.');
const validId = (value) => typeof value === 'string' && /^[\w-]+$/.test(value);
const validDate = (value) => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value);
const validPage = (page) => Number.isSafeInteger(page) && page >= 2;
const validators = {
  pagination: (entry) => validPage(entry.page),
  folder: (entry) => Array.isArray(entry.folders) && entry.folders.length > 0 &&
    entry.folders.every(validId) && new Set(entry.folders).size === entry.folders.length,
  time: (entry) => validDate(entry.from) && validDate(entry.to) && entry.from <= entry.to,
  folder_and_time: (entry) => validators.folder(entry) && validators.time(entry),
  gallery_face: (entry) => (entry.page === undefined || validPage(entry.page)) &&
    typeof entry.detectionId === 'string' &&
    /^[\da-f]{8}-(?:[\da-f]{4}-){3}[\da-f]{12}$/i.test(entry.detectionId),
  selfie: (entry) => typeof entry.file === 'string' && /^(?:\/|\.\/).+\.(?:png|jpe?g)$/i.test(entry.file),
};

for (const name of ['find_by_result', 'negative_feedback']) validators[name] = validators.gallery_face;
validators.add_selfie = validators.selfie;

for (const name of selected) {
  const cases = fixtures[name];
  if (!Array.isArray(cases) || !cases.length ||
      cases.some((entry) => !entry || !validId(entry.expectedPhotoId) || !validators[name](entry))) {
    throw new Error(`Missing or invalid fixture for ${name}.`);
  }
}

const selfies = {};
for (const name of selected.filter((flow) => uploadFlows.includes(flow))) {
  selfies[name] = fixtures[name].map((entry) => {
    const image = open(entry.file, 'b');
    if (fixtures._meta && crypto.sha256(image, 'hex') !== fixtures._meta.selfieSha256) throw new Error('Selfie fixture hash mismatch.');
    return image;
  });
}
const galleryUrl = `${base}/events/${event}/`;
const journeyFailed = new Rate('journey_failed');
const journeyDuration = new Trend('journey_duration_ms', true);
const searchReady = new Trend('search_ready_ms', true);
const testSearches = new Counter('test_searches');

const scenarios = {};
const thresholds = { dropped_iterations: ['count==0'] };
for (const name of selected) {
  const rate = rates[name] * Number(profile) / 300;
  const search = searchFlows.includes(name);
  scenarios[name] = smoke
    ? { executor: 'shared-iterations', vus: 1, iterations: fixtures[name].length, maxDuration: '10m' }
    : {
        executor: 'constant-arrival-rate',
        rate,
        timeUnit: '1m',
        duration: '1m',
        preAllocatedVUs: search ? rate : Math.min(rate, 20),
        maxVUs: rate,
        gracefulStop: '3m',
      };
  thresholds[`iterations{scenario:${name}}`] = [`count>=${smoke ? fixtures[name].length : rate}`];
  thresholds[`journey_failed{scenario:${name}}`] = [smoke ? 'rate==0' : 'rate<0.01'];
  if (!smoke) {
    thresholds[`http_req_duration{scenario:${name},name:gallery_open}`] = ['p(95)<2000'];
    if (search) thresholds[`search_ready_ms{scenario:${name}}`] = ['p(95)<60000'];
    else thresholds[`http_req_duration{scenario:${name},name:gallery_action}`] = ['p(95)<2000'];
  }
}

export const options = {
  systemTags: ['name', 'method', 'status', 'scenario', 'check', 'expected_response', 'error_code'],
  discardResponseBodies: true,
  scenarios,
  thresholds,
};

function requireCheck(condition, label) {
  if (!check(Boolean(condition), { [label]: (passed) => passed })) throw new Error(label);
}

function params(name, headers = {}, timeout = '10s') {
  return { tags: { name }, redirects: 0, responseType: 'text', headers, timeout };
}

function readPage(url, name) {
  const response = http.get(url, params(name, { 'Accept-Encoding': 'gzip' }));
  requireCheck(response.status === 200, `${name}: HTTP 200`);
  const page = response.html();
  if (previewCount === 0) return page;
  const images = page.find('.gallery-card img');
  const requests = [];
  for (let i = 0; i < Math.min(previewCount, images.size()); i++) {
    const source = images.eq(i).attr('src');
    const url = source && source.startsWith('/') ? appUrl(source) : source;
    requireCheck(typeof url === 'string' && /^https?:\/\//.test(url), 'preview has HTTP URL');
    requests.push(['GET', url, null, { tags: { name: 'preview' }, redirects: 3, timeout: '10s', responseType: 'none' }]);
  }
  for (const image of http.batch(requests)) requireCheck(image.status === 200, 'preview: HTTP 200');
  return page;
}

function appUrl(path) {
  if (typeof path !== 'string' || !/^\/(?![/\\])/.test(path)) return null;
  return base + path.split('#', 1)[0];
}

function expectPhoto(page, section, photoId) {
  requireCheck(page.find(`${section} .gallery-card[data-photo-id="${photoId}"]`).size() > 0,
    'expected photo is visible');
}

function filterOrPaginate(name, entry, gallery) {
  const query = [];
  if (name === 'pagination') {
    const maxPage = Number(gallery.find('input[name=page]').first().attr('max'));
    requireCheck(maxPage >= entry.page, 'requested gallery page exists');
    query.push(['page', entry.page]);
  }
  for (const folder of entry.folders || []) {
    requireCheck(gallery.find(`input[name=folder][value="${folder}"]`).size() > 0,
      'configured folder is visible');
    query.push(['folder', folder]);
  }
  if (name === 'time' || name === 'folder_and_time') {
    query.push(['from', entry.from], ['to', entry.to]);
  }
  const encoded = query.map(([key, value]) => `${key}=${encodeURIComponent(value)}`).join('&');
  const page = readPage(`${galleryUrl}?${encoded}`, 'gallery_action');
  expectPhoto(page, '[data-event-gallery]', entry.expectedPhotoId);
  if (name !== 'pagination') {
    requireCheck(page.find('.manual-time-filter-reset').size() > 0, 'gallery filters are active');
  }
  for (const folder of entry.folders || []) {
    requireCheck(page.find(`input[name=folder][value="${folder}"][checked]`).size() > 0,
      'requested folder is selected');
  }
  if (name === 'pagination') {
    const current = Number(page.find('input[name=page]').first().attr('value'));
    requireCheck(current === entry.page, 'requested gallery page is displayed');
  }
}

function waitUntilReady(statusUrl, startedAt) {
  const pending = ['queued', 'processing', 'cleanup_pending'];
  while (Date.now() - startedAt < deadlineMs) {
    const remaining = deadlineMs - (Date.now() - startedAt);
    const response = http.get(statusUrl,
      params('search_status', { Accept: 'application/json' }, `${Math.min(10_000, Math.max(1, remaining))}ms`));
    requireCheck(response.status === 200, 'search status: HTTP 200');
    const state = response.json('status');
    if (state === 'ready' && Date.now() - startedAt <= deadlineMs) return;
    requireCheck(pending.includes(state), 'search remains active');
    const waitMs = deadlineMs - (Date.now() - startedAt);
    if (waitMs > 0) sleep(Math.min(2_000, waitMs) / 1000);
  }
  requireCheck(false, 'search ready within 120 seconds');
}

function findByFace(name, entry, gallery, index, sourceUrl) {
  const upload = uploadFlows.includes(name);
  const selector = upload
    ? 'form[data-selfie-search-form]'
    : `form.gallery-face-search[action$="/${entry.detectionId}/"]`;
  const form = gallery.find(selector).first();
  const action = appUrl(form.attr('action'));
  const token = form.find('input[name=csrfmiddlewaretoken]').attr('value');
  requireCheck(action && token, 'search form has action and CSRF token');
  const fields = { csrfmiddlewaretoken: token };
  if (upload) {
    const png = entry.file.toLowerCase().endsWith('.png');
    fields.selfie = http.file(selfies[name][index], png ? 'selfie.png' : 'selfie.jpg',
      png ? 'image/png' : 'image/jpeg');
  }

  const startedAt = Date.now();
  const submission = http.post(action, fields, params('search_submit', { Referer: sourceUrl }, '30s'));
  const resultUrl = appUrl(submission.headers.Location);
  requireCheck(submission.status === 302 && resultUrl, 'search accepted');
  let result = readPage(resultUrl, 'search_result');
  const digest = result.find('[data-selfie-search-result]').attr('data-result-digest');
  requireCheck(typeof digest === 'string' && /^[a-f0-9]{64}$/.test(digest), 'search has non-secret tracking digest');
  testSearches.add(1, { digest });
  const statusPath = result.find('[data-selfie-search-result]').attr('data-status-url');
  if (statusPath) {
    const statusUrl = appUrl(statusPath);
    requireCheck(statusUrl, 'search status URL belongs to test origin');
    waitUntilReady(statusUrl, startedAt);
  }
  const readyMs = Date.now() - startedAt;
  requireCheck(readyMs <= deadlineMs, 'search ready within 120 seconds');
  if (statusPath) result = readPage(resultUrl, 'search_result_ready');
  expectPhoto(result, '[data-selfie-search-result]', entry.expectedPhotoId);
  searchReady.add(readyMs, { action: 'initial' });
  return { page: result, url: resultUrl };
}


function formFields(form) {
  const fields = {};
  const inputs = form.find('input[type=hidden]');
  for (let i = 0; i < inputs.size(); i++) {
    const input = inputs.eq(i);
    fields[input.attr('name')] = input.attr('value') || '';
  }
  requireCheck(fields.csrfmiddlewaretoken, 'button form has CSRF token');
  return fields;
}

function followSearchButton(name, entry, previous, index) {
  const form = previous.page.find(name === 'add_selfie' ? 'form[data-selfie-add-photo]' : 'form#search-continuation').first();
  const action = appUrl(form.attr('action'));
  requireCheck(action, `${name}: button form is available`);
  const fields = formFields(form);
  if (name === 'find_by_result') {
    // JS enables data-continuation-control buttons except manually rejected cards.
    const button = previous.page.find('.gallery-card:not([data-negative-feedback="manual"]) button[name=photo_id][data-continuation-control]').first();
    fields.photo_id = button.attr('value');
    requireCheck(validId(fields.photo_id), 'result photo is available as a new anchor');
  }
  if (name === 'add_selfie') {
    const png = entry.file.toLowerCase().endsWith('.png');
    fields.selfie = http.file(selfies[name][index], png ? 'selfie.png' : 'selfie.jpg', png ? 'image/png' : 'image/jpeg');
    requireCheck(fields.upload_count, 'additional selfie has current upload count');
  }
  sleep(1); // Reading results before clicking, identical in both comparison runs.
  const startedAt = Date.now();
  const response = http.post(action, fields,
    params(name, { Accept: 'application/json', Referer: base + '/', Origin: base }, '30s'));
  requireCheck(response.status === (name === 'add_selfie' ? 202 : 200), `${name}: button accepted`);
  const path = response.json('result_url');
  const url = appUrl(path);
  requireCheck(url && !path.includes('more=unavailable'), `${name}: new pass was accepted`);
  let result = readPage(url, 'continuation_result');
  // Parent can remain ready even when an additional pass fails. Always verify the child.
  const statusUrl = previous.url.split('?', 1)[0] + 'status/?continuation=1';
  waitUntilReady(statusUrl, startedAt);
  result = readPage(url, 'continuation_ready');
  requireCheck(!result.find('p[role=status]').text().includes('Подходящих новых опор не найдено'),
    'continuation has new usable anchors');
  expectPhoto(result, '[data-selfie-search-result]', entry.expectedPhotoId);
  searchReady.add(Date.now() - startedAt, { action: name });
}

function freshUuid() {
  // Idempotency identifier, unrelated to bearer credentials.
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
    const value = Math.floor(Math.random() * 16);
    return (char === 'x' ? value : (value & 3) | 8).toString(16);
  });
}

function toggleFeedback(entry, previous) {
  let page = previous.page;
  const card = page.find('.gallery-card').first();
  const photoId = card.attr('data-photo-id');
  requireCheck(validId(photoId), 'feedback target is visible');
  for (const operation of ['mark', 'unmark']) {
    const form = page.find(`.gallery-card[data-photo-id="${photoId}"] form[data-reversible-feedback]`).first();
    const action = appUrl(form.attr('action'));
    requireCheck(action, 'reversible feedback button is available');
    const fields = formFields(form);
    fields.action = operation;
    fields.submission_id = freshUuid();
    const response = http.post(action, fields,
      params(`feedback_${operation}`, { Referer: base + '/', Origin: base }, '30s'));
    requireCheck(response.status === (operation === 'mark' ? 201 : 200) && response.json('status') === 'saved', `feedback ${operation}: saved`);
    const manual = response.json('manual_photo_ids');
    requireCheck(Array.isArray(manual) && manual.includes(photoId) === (operation === 'mark'), `feedback ${operation}: correct state`);
    const excludedBefore = Number(page.find('[data-feedback-scope]').attr('data-excluded-photo-count'));
    if (response.json('excluded_photo_count') !== excludedBefore) page = readPage(previous.url, 'feedback_result');
  }
  expectPhoto(page, '[data-selfie-search-result]', entry.expectedPhotoId);
}

export default function () {
  const name = scenario.name;
  const cases = fixtures[name];
  const index = scenario.iterationInTest % cases.length;
  const startedAt = Date.now();
  let failed = true;
  const entry = cases[index];
  const sourceUrl = !uploadFlows.includes(name) && entry.page ? `${galleryUrl}?page=${entry.page}` : galleryUrl;
  try {
    const gallery = readPage(sourceUrl, 'gallery_open');
    if (searchFlows.includes(name)) {
      const result = findByFace(name, entry, gallery, index, sourceUrl);
      if (['find_by_result', 'add_selfie'].includes(name)) followSearchButton(name, entry, result, index);
      if (name === 'negative_feedback') toggleFeedback(entry, result);
    }
    else filterOrPaginate(name, entry, gallery);
    failed = false;
  } finally {
    journeyFailed.add(failed);
    journeyDuration.add(Date.now() - startedAt);
  }
}
