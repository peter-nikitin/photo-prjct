import { check, sleep } from 'k6';
import { scenario } from 'k6/execution';
import http from 'k6/http';
import { Rate, Trend } from 'k6/metrics';

const base = (__ENV.BASE_URL || '').replace(/\/$/, '');
const event = __ENV.EVENT_SLUG || '';
const profile = __ENV.PROFILE || 'smoke';
const previewCount = Number(__ENV.PREVIEW_COUNT || 8);
const searchDeadlineMs = 120_000;
const activeSearchStates = ['queued', 'processing', 'cleanup_pending'];
const cases = JSON.parse(open(__ENV.FACE_CASES_FILE || './face-cases.json'));

if (!/^https?:\/\/[^/?#@]+$/.test(base) || !/^[\w-]+$/.test(event)) {
  throw new Error('Set BASE_URL to the test origin and EVENT_SLUG to the test event slug.');
}
if (!['smoke', '300', '600'].includes(profile)) {
  throw new Error('PROFILE must be smoke, 300 or 600.');
}
if (!Number.isInteger(previewCount) || previewCount < 1) {
  throw new Error('PREVIEW_COUNT must be a positive integer.');
}
if (!Array.isArray(cases) || !cases.length || cases.some((entry) =>
  !entry || typeof entry.detectionId !== 'string' || typeof entry.expectedPhotoId !== 'string' ||
  !/^[\da-f]{8}-(?:[\da-f]{4}-){3}[\da-f]{12}$/.test(entry.detectionId) ||
  !/^[\w-]+$/.test(entry.expectedPhotoId)
)) {
  throw new Error('FACE_CASES_FILE must contain a nonempty array of detectionId and expectedPhotoId pairs.');
}

const galleryUrl = `${base}/events/${event}/`;
const visitFailed = new Rate('visit_failed');
const searchReadyTime = new Trend('search_ready_ms', true);

function arrivalRate(exec, rate, preAllocatedVUs, maxVUs) {
  return {
    exec,
    executor: 'constant-arrival-rate',
    timeUnit: '1m',
    duration: '1m',
    rate,
    preAllocatedVUs,
    maxVUs,
    gracefulStop: '4m',
  };
}

const smoke = profile === 'smoke';
const searchVisits = Number(profile) / 5;
export const options = {
  // URLs may contain search capabilities or signed media credentials.
  systemTags: ['name', 'method', 'status', 'scenario', 'check', 'expected_response', 'error_code'],
  discardResponseBodies: true,
  scenarios: smoke
    ? { smoke: { executor: 'shared-iterations', exec: 'searchAndDownload', vus: 1, iterations: 1, maxDuration: '4m' } }
    : {
        browse: arrivalRate('browse', searchVisits * 4, 20, searchVisits * 4),
        search: arrivalRate('searchAndDownload', searchVisits, searchVisits, searchVisits),
      },
  thresholds: {
    dropped_iterations: ['count==0'],
    iterations: [smoke ? 'count==1' : `count>=${profile}`],
    visit_failed: [smoke ? 'rate==0' : 'rate<0.01'],
    ...(smoke ? {} : {
      'http_req_duration{name:gallery}': ['p(95)<2000'],
      search_ready_ms: ['p(95)<60000'],
    }),
  },
};

function appUrl(path) {
  return path && path.startsWith('/') && !path.startsWith('//') ? base + path : null;
}

function mediaUrl(path) {
  if (path && /^https?:\/\//.test(path)) return path;
  return appUrl(path);
}

function requireCheck(value, label) {
  if (!check(value, { [label]: Boolean })) throw new Error(label);
}

function requestParams(name, overrides = {}) {
  return { tags: { name }, timeout: '10s', ...overrides };
}

function imageResponse(response) {
  return response.status === 200 && /^image\//i.test(response.headers['Content-Type'] || '');
}

function loadPreviews(page, selector, limit, name) {
  const urls = page.find(selector).toArray().slice(0, limit).map((image) => mediaUrl(image.attr('src')));
  requireCheck(urls.length > 0 && urls.every(Boolean), `${name}: image links present`);
  const responses = http.batch(urls.map((url) => ['GET', url, null, requestParams(name)]));
  requireCheck(responses.every(imageResponse), `${name}: images downloaded`);
}

function openGallery() {
  const response = http.get(galleryUrl, requestParams('gallery', {
    responseType: 'text',
    redirects: 0,
    headers: { 'Accept-Encoding': 'gzip' },
  }));
  requireCheck(response.status === 200, 'gallery: HTTP 200');
  const page = response.html();
  loadPreviews(page, '[data-event-gallery] .gallery-card-link img', previewCount, 'gallery_preview');
  return page;
}

function waitForResult(statusUrl, startedAt) {
  while (Date.now() - startedAt < searchDeadlineMs) {
    const response = http.get(statusUrl, requestParams('search_status', {
      responseType: 'text',
      redirects: 0,
      headers: { Accept: 'application/json' },
    }));
    requireCheck(response.status === 200, 'search status: HTTP 200');
    const state = response.json('status');
    if (state === 'ready' && Date.now() - startedAt <= searchDeadlineMs) return;
    requireCheck(activeSearchStates.includes(state), 'search status: active or ready');
    sleep(2);
  }
  requireCheck(false, 'search ready within 120 seconds');
}

function downloadExpectedPhoto(gallery) {
  const face = cases[scenario.iterationInTest % cases.length];
  const form = gallery.find(`form.gallery-face-search[action$="/${face.detectionId}/"]`).first();
  const action = appUrl(form.attr('action'));
  const csrf = form.find('input[name=csrfmiddlewaretoken]').attr('value');
  requireCheck(action && csrf, 'search form: action and CSRF token present');

  const startedAt = Date.now();
  const submission = http.post(action, { csrfmiddlewaretoken: csrf }, requestParams('search_submit', {
    redirects: 0,
    headers: { Referer: galleryUrl },
  }));
  const resultUrl = appUrl(submission.headers.Location);
  requireCheck(submission.status === 302 && resultUrl, 'search submit: redirected to result');

  const pending = http.get(resultUrl, requestParams('search_result', { responseType: 'text', redirects: 0 }));
  requireCheck(pending.status === 200, 'search result: HTTP 200');
  let resultPage = pending.html();
  const statusPath = resultPage.find('[data-selfie-search-result]').attr('data-status-url');
  const downloadSelector = `[data-photo-id="${face.expectedPhotoId}"] a.gallery-download`;
  if (statusPath) {
    const statusUrl = appUrl(statusPath);
    requireCheck(statusUrl, 'search result: status URL present');
    waitForResult(statusUrl, startedAt);
  } else {
    requireCheck(resultPage.find(downloadSelector).size() > 0, 'immediate result: expected match present');
  }
  searchReadyTime.add(Date.now() - startedAt);

  if (statusPath) {
    const ready = http.get(resultUrl, requestParams('search_result', { responseType: 'text', redirects: 0 }));
    requireCheck(ready.status === 200, 'ready result: HTTP 200');
    resultPage = ready.html();
  }
  const downloadUrl = appUrl(resultPage.find(downloadSelector).first().attr('href'));
  requireCheck(downloadUrl, 'ready result: expected photo has download link');
  loadPreviews(resultPage, '[data-selfie-search-result] .gallery-card-link img', previewCount, 'result_preview');

  const redirect = http.get(downloadUrl, requestParams('download_authorize', { redirects: 0 }));
  const original = mediaUrl(redirect.headers.Location);
  requireCheck(redirect.status === 302 && original, 'download authorization: redirected to original');
  const image = http.get(original, requestParams('download_original', { timeout: '30s' }));
  requireCheck(imageResponse(image), 'original: image downloaded');
}

function visit(flow) {
  let failed = true;
  try {
    flow(openGallery());
    failed = false;
  } finally {
    visitFailed.add(failed);
  }
}

export function browse() {
  visit(() => {});
}

export function searchAndDownload() {
  visit(downloadExpectedPhoto);
}
