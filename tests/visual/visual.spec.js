const { expect, test } = require('@playwright/test');

const DESKTOP_VIEWPORT = { width: 1440, height: 1000 };
const MOBILE_VIEWPORT = { width: 390, height: 844 };
const SELFIE_SEARCH_HISTORY_STORAGE_KEY = 'findme_selfie_search_history:v1';
const SAVED_SELFIE_SEARCH_HISTORY = [
  {
    eventSlug: 'london-10k',
    resultPath: '/events/london-10k/selfie-search/saved-result-newest/',
    openedAt: '2026-08-04T12:34:56.000Z',
  },
  {
    eventSlug: 'london-10k',
    resultPath: '/events/london-10k/selfie-search/saved-result-earlier/',
    openedAt: '2026-08-03T12:34:56.000Z',
  },
  {
    eventSlug: 'brighton-ride',
    resultPath: '/events/brighton-ride/selfie-search/other-event-result/',
    openedAt: '2026-08-04T13:34:56.000Z',
  },
];

const desktopPages = [
  ['catalog-populated', '/__visual__/catalog/populated/'],
  ['catalog-empty', '/__visual__/catalog/empty/'],
  ['event-covered', '/__visual__/event/covered/'],
  ['event-uncovered', '/__visual__/event/uncovered/'],
  ['event-gallery-populated', '/__visual__/event/gallery-populated/'],
  ['event-gallery-empty', '/__visual__/event/gallery-empty/'],
  ['event-selfie-search', '/__visual__/event/selfie-search/'],
  ['event-selfie-search-rejected', '/__visual__/event/selfie-search/rejected/'],
  ['selfie-search-processing', '/__visual__/event/selfie-search/processing/'],
  ['selfie-search-empty', '/__visual__/event/selfie-search/empty/'],
  ['selfie-search-error', '/__visual__/event/selfie-search/error/'],
  ['selfie-search-ready', '/__visual__/event/selfie-search/ready/'],
  ['selfie-search-feedback-problem', '/__visual__/event/selfie-search/feedback-problem/'],
  ['legal', '/__visual__/legal/'],
  ['reference-search', '/__visual__/reference/search/'],
  ['reference-dashboard', '/__visual__/reference/dashboard/'],
  ['reference-events', '/__visual__/reference/events/'],
  ['upload-empty', '/__visual__/upload/empty/'],
  ['upload-active', '/__visual__/upload/active/'],
  ['upload-partial', '/__visual__/upload/partial/'],
  ['upload-complete', '/__visual__/upload/complete/'],
  ['reference-orders', '/__visual__/reference/orders/'],
  ['reference-promotions', '/__visual__/reference/promotions/'],
  ['reference-purchased', '/__visual__/reference/purchased/'],
];

const mobilePages = [
  ['catalog-populated', '/__visual__/catalog/populated/'],
  ['catalog-empty', '/__visual__/catalog/empty/'],
  ['event-covered', '/__visual__/event/covered/'],
  ['event-uncovered', '/__visual__/event/uncovered/'],
  ['event-gallery-populated', '/__visual__/event/gallery-populated/'],
  ['event-gallery-empty', '/__visual__/event/gallery-empty/'],
  ['event-selfie-search', '/__visual__/event/selfie-search/'],
  ['event-selfie-search-rejected', '/__visual__/event/selfie-search/rejected/'],
  ['selfie-search-processing', '/__visual__/event/selfie-search/processing/'],
  ['selfie-search-empty', '/__visual__/event/selfie-search/empty/'],
  ['selfie-search-error', '/__visual__/event/selfie-search/error/'],
  ['selfie-search-ready', '/__visual__/event/selfie-search/ready/'],
  ['selfie-search-feedback-problem', '/__visual__/event/selfie-search/feedback-problem/'],
  ['selfie-search-feedback-marking', '/__visual__/event/selfie-search/feedback-marking/'],
  ['legal', '/__visual__/legal/'],
  ['reference-search', '/__visual__/reference/search/'],
  ['upload-empty', '/__visual__/upload/empty/'],
  ['upload-active', '/__visual__/upload/active/'],
  ['upload-partial', '/__visual__/upload/partial/'],
  ['upload-complete', '/__visual__/upload/complete/'],
];

function collectBrowserFailures(page) {
  const failures = [];
  const resources = [];

  page.on('console', (message) => {
    if (message.type() === 'error') {
      failures.push(`console: ${message.text()}`);
    }
  });
  page.on('requestfailed', (request) => {
    failures.push(
      `requestfailed: ${request.method()} ${request.url()} (${request.failure()?.errorText ?? 'unknown'})`,
    );
  });
  page.on('response', (response) => {
    const resourceType = response.request().resourceType();
    if (resourceType !== 'document') {
      resources.push({
        resourceType,
        status: response.status(),
        url: response.url(),
      });
    }
  });

  return { failures, resources };
}

async function settlePage(page) {
  await page.waitForLoadState('networkidle');
  await page.waitForFunction(() =>
    Array.from(document.images).every((image) => image.complete && image.naturalWidth > 0),
  );
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
}

async function preloadCookieAcknowledgement(page) {
  await page.addInitScript(() => {
    window.localStorage.setItem('findme_cookie_notice', '2026-08-02');
  });
}

async function preloadSavedSelfieSearchHistory(page) {
  await page.addInitScript(
    ({ storageKey, entries }) => {
      window.localStorage.setItem(storageKey, JSON.stringify(entries));
    },
    { storageKey: SELFIE_SEARCH_HISTORY_STORAGE_KEY, entries: SAVED_SELFIE_SEARCH_HISTORY },
  );
}

async function savedSelfieSearchHistory(page) {
  await preloadCookieAcknowledgement(page);
  await preloadSavedSelfieSearchHistory(page);
  await page.goto('/__visual__/event/selfie-search/');
  return page.locator('[data-selfie-search-history]');
}

async function capturePage(page, { path, snapshot, viewport, cookieAcknowledged = true }) {
  const { failures, resources } = collectBrowserFailures(page);
  await page.setViewportSize(viewport);
  if (cookieAcknowledged) {
    await preloadCookieAcknowledgement(page);
  } else {
    await page.addInitScript(() => {
      window.localStorage.removeItem('findme_cookie_notice');
    });
  }

  const response = await page.goto(path);
  expect(response, `Expected a document response for ${path}`).not.toBeNull();
  expect(response.status(), `Expected ${path} to load successfully`).toBeLessThan(400);
  await settlePage(page);

  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scrollWidth, `${path} must not overflow horizontally`).toBeLessThanOrEqual(
    dimensions.clientWidth,
  );
  expect(failures, `Browser failures on ${path}`).toEqual([]);
  expect(resources.some(({ resourceType }) => resourceType === 'stylesheet')).toBe(true);
  expect(resources.some(({ url }) => url.endsWith('.woff2'))).toBe(true);
  if (!path.includes('/reference/')) {
    expect(resources.some(({ url }) => url.endsWith('/ui/icons.svg'))).toBe(true);
  }
  expect(
    resources.filter(({ status }) => status >= 400),
    `CSS, sprite, and images on ${path} must load successfully`,
  ).toEqual([]);
  await expect(page).toHaveScreenshot(snapshot, {
    animations: 'disabled',
    fullPage: true,
    timeout: 15_000,
  });
}

async function installUploadStubs(
  page,
  {
    authorizeDelay = 0,
    confirmFailureStatus = null,
    retryFailureStatus = null,
    storageStatuses = [204],
    storageDelay = 0,
  } = {},
) {
  let itemSequence = 0;
  let activeTransfers = 0;
  let maxActiveTransfers = 0;
  const controlCalls = [];
  const storageCalls = [];
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await page.route('**/photographer/uploads/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === 'GET' && url.pathname.endsWith('/batch-resume-1/resume/')) {
      return route.fulfill({
        json: {
          batch: { id: 'batch-resume-1', event: { id: 'london-10k', name: 'London 10K' } },
          items: [
            {
              id: 'confirmed', filename: 'confirmed.jpg', size: 9, last_modified_ms: null,
              ambiguous_sha256: null, status: 'uploaded', confirmed: true,
            },
            {
              id: 'pending', filename: 'pending.jpg', size: 7, last_modified_ms: null,
              ambiguous_sha256: null, status: 'pending', confirmed: false,
            },
          ],
        },
      });
    }
    if (request.method() !== 'POST') return route.continue();
    const body = request.postDataJSON();
    controlCalls.push({ path: url.pathname, body });
    if (url.pathname.endsWith('/batches/')) {
      return route.fulfill({ json: { batch: { id: 'batch-1' } } });
    }
    if (url.pathname === '/photographer/uploads/batch-1/items/') {
      return route.fulfill({
        json: {
          items: body.items.map((item) => ({
            id: `item-${++itemSequence}`,
            client_item_id: item.client_item_id,
            status: 'pending',
          })),
        },
      });
    }
    const item = url.pathname.match(/items\/([^/]+)\//)?.[1];
    if (url.pathname.endsWith('/retry/') && retryFailureStatus) {
      return route.fulfill({
        status: retryFailureStatus,
        json: { error: { code: 'storage_unavailable', message: 'Private detail.' } },
      });
    }
    if (url.pathname.endsWith('/authorize/') && authorizeDelay) {
      await new Promise((resolve) => setTimeout(resolve, authorizeDelay));
    }
    if (url.pathname.endsWith('/authorize/') || url.pathname.endsWith('/retry/')) {
      return route.fulfill({
        json: {
          item: { id: item, status: 'authorized', attempt: 1 },
          grant: { url: `http://storage.test/upload/${item}`, fields: { policy: 'secret' } },
        },
      });
    }
    if (url.pathname.endsWith('/confirm/')) {
      if (confirmFailureStatus) {
        return route.fulfill({
          status: confirmFailureStatus,
          json: { error: { code: 'storage_unavailable', message: 'Private detail.' } },
        });
      }
      return route.fulfill({ json: { item: { id: item, status: 'uploaded' } } });
    }
    if (url.pathname.endsWith('/failed/')) {
      return route.fulfill({ json: { item: { id: item, status: 'failed' } } });
    }
    if (url.pathname.endsWith('/finalize/')) {
      return route.fulfill({ json: { batch: { id: 'batch-1', status: 'complete' } } });
    }
    return route.abort();
  });
  await page.route('http://storage.test/**', async (route) => {
    storageCalls.push(new URL(route.request().url()).pathname);
    activeTransfers += 1;
    maxActiveTransfers = Math.max(maxActiveTransfers, activeTransfers);
    if (storageDelay) {
      await new Promise((resolve) => setTimeout(resolve, storageDelay));
    }
    const status = storageStatuses.shift() ?? 204;
    activeTransfers -= 1;
    await route.fulfill({
      status,
      body: '',
      headers: { 'access-control-allow-origin': '*' },
    });
  });
  return { controlCalls, storageCalls, pageErrors, getMaxActiveTransfers: () => maxActiveTransfers };
}

test.describe('desktop visual regression', () => {
  for (const [name, path] of desktopPages) {
    test(name, async ({ page }) => {
      await capturePage(page, {
        path,
        snapshot: `desktop-${name}.png`,
        viewport: DESKTOP_VIEWPORT,
      });
    });
  }
});

test.describe('mobile visual regression', () => {
  for (const [name, path] of mobilePages) {
    test(name, async ({ page }) => {
      await capturePage(page, {
        path,
        snapshot: `mobile-${name}.png`,
        viewport: MOBILE_VIEWPORT,
      });
    });
  }
});

test('saved selfie-search history is private, event-scoped, navigable, and removable', async ({ page }) => {
  const history = await savedSelfieSearchHistory(page);

  await expect(history).toBeVisible();
  await expect(history.getByRole('heading', { name: 'Мои результаты поиска' })).toBeVisible();
  await expect(history.locator('.selfie-search-history-label')).toHaveText([
    'Поиск от 04.08.2026, 15:34:56',
    'Поиск от 03.08.2026, 15:34:56',
  ]);
  await expect(history.getByRole('button', { name: 'Открыть результат' })).toHaveCount(2);
  await expect(history.getByRole('button', { name: 'Удалить с устройства' })).toHaveCount(2);
  const bodyText = await page.locator('body').textContent();
  const attributeValues = await page
    .locator('*')
    .evaluateAll((elements) => elements.flatMap((element) => Array.from(element.attributes, ({ value }) => value)));
  const hrefs = await page.locator('[href]').evaluateAll((elements) =>
    elements.map((element) => element.getAttribute('href') ?? ''),
  );
  for (const { resultPath } of SAVED_SELFIE_SEARCH_HISTORY) {
    const token = resultPath.split('/').at(-2);
    for (const secret of [resultPath, token]) {
      expect(bodyText).not.toContain(secret);
      expect(attributeValues.some((value) => value.includes(secret))).toBe(false);
      expect(hrefs.some((href) => href.includes(secret))).toBe(false);
    }
  }

  const savedResultPath = SAVED_SELFIE_SEARCH_HISTORY[0].resultPath;
  await page.route(`**${savedResultPath}`, (route) =>
    route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>Saved</title>' }),
  );
  const navigationRequest = page.waitForRequest(
    (request) => request.isNavigationRequest() && new URL(request.url()).pathname === savedResultPath,
  );
  const openNewestResult = history.getByRole('button', { name: 'Открыть результат' }).first();
  await openNewestResult.focus();
  await page.keyboard.press('Enter');
  expect(new URL((await navigationRequest).url()).pathname).toBe(savedResultPath);
  await expect(page).toHaveURL(savedResultPath);

  await page.goBack();
  const restoredHistory = page.locator('[data-selfie-search-history]');
  const removeNewestResult = restoredHistory
    .getByRole('button', { name: 'Удалить с устройства' })
    .first();
  await removeNewestResult.focus();
  await page.keyboard.press('Space');
  await expect(restoredHistory.locator('.selfie-search-history-label')).toHaveText([
    'Поиск от 03.08.2026, 15:34:56',
  ]);
  await expect(restoredHistory.getByRole('button', { name: 'Открыть результат' })).toBeFocused();
  expect(
    await page.evaluate((storageKey) => JSON.parse(window.localStorage.getItem(storageKey)), SELFIE_SEARCH_HISTORY_STORAGE_KEY),
  ).toEqual([SAVED_SELFIE_SEARCH_HISTORY[2], SAVED_SELFIE_SEARCH_HISTORY[1]]);

  await restoredHistory.getByRole('button', { name: 'Удалить с устройства' }).click();
  await expect(restoredHistory).toBeHidden();
  await expect(page.getByRole('button', { name: 'Найти мои фото' })).toBeFocused();
  expect(
    await page.evaluate((storageKey) => JSON.parse(window.localStorage.getItem(storageKey)), SELFIE_SEARCH_HISTORY_STORAGE_KEY),
  ).toEqual([SAVED_SELFIE_SEARCH_HISTORY[2]]);
});

test('saved selfie-search history has approved desktop and mobile presentation', async ({ page }) => {
  for (const [viewport, snapshot] of [
    [DESKTOP_VIEWPORT, 'desktop-event-selfie-search-history.png'],
    [MOBILE_VIEWPORT, 'mobile-event-selfie-search-history.png'],
  ]) {
    const { failures, resources } = collectBrowserFailures(page);
    await page.setViewportSize(viewport);
    const history = await savedSelfieSearchHistory(page);
    await settlePage(page);
    await expect(history.locator('.selfie-search-history-label')).toHaveText([
      'Поиск от 04.08.2026, 15:34:56',
      'Поиск от 03.08.2026, 15:34:56',
    ]);
    const openResult = history.getByRole('button', { name: 'Открыть результат' }).first();
    await openResult.focus();
    await expect(openResult).toBeFocused();
    await expect(openResult).toHaveCSS('outline-style', 'solid');
    await expect(openResult).toHaveCSS('outline-width', '3px');
    const dimensions = await page.evaluate(() => ({
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
    expect(failures).toEqual([]);
    expect(resources.some(({ resourceType }) => resourceType === 'stylesheet')).toBe(true);
    expect(resources.some(({ url }) => url.endsWith('.woff2'))).toBe(true);
    expect(resources.some(({ url }) => url.endsWith('/ui/icons.svg'))).toBe(true);
    expect(resources.filter(({ status }) => status >= 400)).toEqual([]);
    await expect(page).toHaveScreenshot(snapshot, {
      animations: 'disabled',
      fullPage: true,
      timeout: 15_000,
    });
  }
});

test('desktop feedback marking keeps gallery full width and terminal actions equal', async ({ page }) => {
  await page.setViewportSize(DESKTOP_VIEWPORT);
  await preloadCookieAcknowledgement(page);
  const response = await page.goto('/__visual__/event/selfie-search/feedback-marking/');
  expect(response).not.toBeNull();
  expect(response.status()).toBeLessThan(400);
  await settlePage(page);

  const layout = await page.evaluate(() => ({
    scrollY: window.scrollY,
    activeTag: document.activeElement?.tagName,
    terminal: (() => {
      const actions = document.querySelector('.selfie-search-terminal-actions');
      const newSearch = actions?.querySelector(':scope > .button');
      const feedback = actions?.querySelector(':scope > .selfie-search-terminal-feedback');
      const actionsRect = actions?.getBoundingClientRect();
      const newSearchRect = newSearch?.getBoundingClientRect();
      const feedbackRect = feedback?.getBoundingClientRect();
      const galleryRect = document.querySelector('.selfie-search-results')?.getBoundingClientRect();
      return {
        display: actions ? getComputedStyle(actions).display : '',
        columns: actions ? getComputedStyle(actions).gridTemplateColumns : '',
        newSearchBeforeFeedback: Boolean(newSearch && feedback && newSearchRect && feedbackRect && newSearchRect.left < feedbackRect.left),
        equalColumns: Boolean(actionsRect && newSearchRect && feedbackRect && Math.abs(newSearchRect.width - feedbackRect.width) < 1),
        galleryWidth: galleryRect?.width ?? 0,
        actionsWidth: actionsRect?.width ?? 0,
      };
    })(),
    form: (() => {
      const form = document.querySelector('[data-feedback-form]');
      return {
        visible: Boolean(form && !form.hidden),
        hasDisclosureAttributes: Boolean(form?.hasAttribute('data-feedback-open') || form?.hasAttribute('data-feedback-close') || form?.hasAttribute('data-feedback-opt-out')),
      };
    })(),
    cards: Array.from(document.querySelectorAll('.gallery-card-actions')).map((actions) => {
      const controls = actions.querySelector('[data-feedback-card-controls]');
      const download = actions.querySelector('.gallery-download');
      const controlRect = controls?.getBoundingClientRect();
      const downloadRect = download?.getBoundingClientRect();
      return {
        controlsVisible: Boolean(controls && !controls.hidden),
        controlsBeforeDownload: Boolean(controlRect && downloadRect && controlRect.right <= downloadRect.left),
      };
    }),
  }));

  expect(layout.scrollY).toBe(0);
  expect(layout.activeTag).toBe('BODY');
  expect(layout.terminal.display).toBe('grid');
  expect(layout.terminal.columns).toMatch(/^\d+(?:\.\d+)?px \d+(?:\.\d+)?px$/);
  expect(layout.terminal.newSearchBeforeFeedback).toBe(true);
  expect(layout.terminal.equalColumns).toBe(true);
  expect(layout.terminal.actionsWidth).toBe(layout.terminal.galleryWidth);
  expect(layout.form.visible).toBe(true);
  expect(layout.form.hasDisclosureAttributes).toBe(false);
  expect(layout.cards).toHaveLength(3);
  expect(layout.cards.every(({ controlsVisible }) => controlsVisible)).toBe(true);
  expect(layout.cards.every(({ controlsBeforeDownload }) => controlsBeforeDownload)).toBe(true);
  await page.addStyleTag({
    content: '.topbar { position: static !important; } .skip-link { display: none !important; }',
  });
  await expect(page).toHaveScreenshot('desktop-selfie-search-feedback-marking.png', {
    animations: 'disabled',
    fullPage: true,
    timeout: 15_000,
  });
});

test('mobile feedback marking stacks the new-search action before the form', async ({ page }) => {
  await page.setViewportSize(MOBILE_VIEWPORT);
  await preloadCookieAcknowledgement(page);
  const response = await page.goto('/__visual__/event/selfie-search/feedback-marking/');
  expect(response).not.toBeNull();
  expect(response.status()).toBeLessThan(400);
  await settlePage(page);

  const layout = await page.evaluate(() => {
    const actions = document.querySelector('.selfie-search-terminal-actions');
    const newSearch = actions?.querySelector(':scope > .button');
    const feedback = actions?.querySelector(':scope > .selfie-search-terminal-feedback');
    const actionsRect = actions?.getBoundingClientRect();
    const newSearchRect = newSearch?.getBoundingClientRect();
    const feedbackRect = feedback?.getBoundingClientRect();
    return {
      columns: actions ? getComputedStyle(actions).gridTemplateColumns : '',
      stacked: Boolean(newSearchRect && feedbackRect && newSearchRect.top < feedbackRect.top),
      fullWidth: Boolean(actionsRect && newSearchRect && feedbackRect && Math.abs(newSearchRect.width - actionsRect.width) < 1 && Math.abs(feedbackRect.width - actionsRect.width) < 1),
    };
  });

  expect(layout.columns).toMatch(/^\d+(?:\.\d+)?px$/);
  expect(layout.stacked).toBe(true);
  expect(layout.fullWidth).toBe(true);
});

test('marking mode keeps the original cards operable and updates optional progress', async ({ page }) => {
  await page.goto('/__visual__/event/selfie-search/feedback-marking/');

  const firstPresent = page.getByRole('button', { name: 'На фотографии 1: я есть' });
  await expect(firstPresent).toBeVisible();
  await expect(page.locator('.gallery-download')).toHaveCount(3);
  await firstPresent.click();
  await expect(firstPresent).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('[data-feedback-progress]')).toHaveText('Размечено 1 из 3 фотографий');
  await firstPresent.click();
  await expect(firstPresent).toHaveAttribute('aria-pressed', 'false');
  await expect(page.locator('[data-feedback-progress]')).toHaveText('Размечено 0 из 3 фотографий');
  await expect(page.locator('.glightbox-container')).toHaveCount(0);
});

test('cookie notice is visible and usable on a fresh desktop profile', async ({ page }) => {
  await capturePage(page, {
    path: '/__visual__/legal/',
    snapshot: 'desktop-cookie-notice.png',
    viewport: DESKTOP_VIEWPORT,
    cookieAcknowledged: false,
  });

  const notice = page.locator('[data-cookie-notice]');
  await expect(notice).toBeVisible();
  await expect(notice).toContainText(
    'Мы используем файлы cookie, чтобы обеспечить работу нашего сайта и проанализировать его',
  );
  await expect(notice.getByRole('link')).toHaveAttribute('href', /personal-data-policy\.pdf$/);
  await expect(notice.getByRole('button', { name: 'OK' })).toBeVisible();
});

test('cookie notice is readable without overflow on a fresh mobile profile', async ({ page }) => {
  await capturePage(page, {
    path: '/__visual__/legal/',
    snapshot: 'mobile-cookie-notice.png',
    viewport: MOBILE_VIEWPORT,
    cookieAcknowledged: false,
  });

  await expect(page.locator('[data-cookie-notice]')).toBeVisible();
});

test('cookie notice stores acknowledgement before hiding and persists across reloads', async ({ page }) => {
  await page.goto('/__visual__/legal/');
  const notice = page.locator('[data-cookie-notice]');
  const accept = notice.getByRole('button', { name: 'OK' });

  await expect(notice).toBeVisible();
  await accept.click();
  await expect(notice).toBeHidden();
  await expect
    .poll(() => page.evaluate(() => window.localStorage.getItem('findme_cookie_notice')))
    .toBe('2026-08-02');
  await page.reload();
  await expect(notice).toBeHidden();
});

test('cookie notice reappears for a stale acknowledgement', async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('findme_cookie_notice', 'stale-version');
  });
  await page.goto('/__visual__/legal/');

  await expect(page.locator('[data-cookie-notice]')).toBeVisible();
});

test('cookie notice remains operable when localStorage read or write throws', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await page.addInitScript(() => {
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: {
        getItem() {
          throw new Error('read blocked');
        },
        setItem() {
          throw new Error('write blocked');
        },
      },
    });
  });
  await page.goto('/__visual__/legal/');

  const notice = page.locator('[data-cookie-notice]');
  await expect(notice).toBeVisible();
  await notice.getByRole('button', { name: 'OK' }).click();
  await expect(notice).toBeVisible();
  expect(pageErrors).toEqual([]);
});

test('cookie notice accept button works from the keyboard', async ({ page }) => {
  await page.goto('/__visual__/legal/');
  const notice = page.locator('[data-cookie-notice]');
  const accept = notice.getByRole('button', { name: 'OK' });

  await accept.focus();
  await page.keyboard.press('Enter');
  await expect(notice).toBeHidden();
});

test('visual pages do not request Yandex Metrika', async ({ page }) => {
  const metrikaRequests = [];
  page.on('request', (request) => {
    if (new URL(request.url()).hostname === 'mc.yandex.ru') {
      metrikaRequests.push(request.url());
    }
  });

  await page.goto('/__visual__/legal/');
  await settlePage(page);

  expect(metrikaRequests).toEqual([]);
});

test('all links on live production pages resolve', async ({ page, request }) => {
  for (const path of ['/', '/legal/']) {
    await page.goto(path);
    const hrefs = await page.locator('a[href]').evaluateAll((links) =>
      links.map((link) => link.href).filter((href) => new URL(href).origin === window.location.origin),
    );

    for (const href of new Set(hrefs)) {
      const response = await request.get(href);
      expect(response.status(), `${href} linked from ${path} must resolve successfully`).toBeLessThan(
        400,
      );
    }
  }
});

test('legal contacts and documents are keyboard reachable', async ({ page, request }) => {
  await page.goto('/legal/');

  const links = page.locator('.legal-content a');
  await expect(links).toHaveCount(4);
  await links.nth(0).focus();
  await expect(links.nth(0)).toBeFocused();

  for (const index of [1, 2, 3]) {
    const href = await links.nth(index).getAttribute('href');
    const response = await request.get(href);
    expect(response.status(), `${href} must resolve successfully`).toBeLessThan(400);
  }
});

test('gallery supports keyboard navigation and focus restoration', async ({ page }) => {
  await page.goto('/__visual__/event/gallery-populated/');
  const firstCard = page.locator('.gallery-card-link').first();
  const currentImage = page.locator('.gslide.current .gslide-image img');

  await firstCard.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.glightbox-container')).toBeVisible();
  await expect(currentImage).toHaveAttribute('src', /run-city-1842\.png$/);

  await page.keyboard.press('ArrowRight');
  await expect(currentImage).toHaveAttribute('src', /run-track-1190\.png$/);
  await page.keyboard.press('ArrowLeft');
  await expect(currentImage).toHaveAttribute('src', /run-city-1842\.png$/);

  await page.keyboard.press('Escape');
  await expect(page.locator('.glightbox-container')).toBeHidden();
  await expect(firstCard).toBeFocused();
});

test('gallery supports pointer open and visible close control', async ({ page }) => {
  await page.goto('/__visual__/event/gallery-populated/');
  const firstCard = page.locator('.gallery-card-link').first();

  await firstCard.click();
  await expect(page.locator('.glightbox-container')).toBeVisible();
  const closeButton = page.locator('.glightbox-container .gclose');
  await expect(closeButton).toBeVisible();
  await closeButton.click();

  await expect(page.locator('.glightbox-container')).toBeHidden();
  await expect(firstCard).toBeFocused();
});

test('gallery supports mobile swipe', async ({ browser }) => {
  const context = await browser.newContext({
    hasTouch: true,
    isMobile: true,
    viewport: MOBILE_VIEWPORT,
  });
  const page = await context.newPage();
  try {
    await page.goto('/__visual__/event/gallery-populated/');
    await page.locator('.gallery-card-link').first().tap();
    const currentImage = page.locator('.gslide.current .gslide-image img');
    await expect(currentImage).toHaveAttribute('src', /run-city-1842\.png$/);

    const slideBox = await page.locator('.gslide.current').boundingBox();
    expect(slideBox).not.toBeNull();
    const startX = slideBox.x + slideBox.width * 0.85;
    const y = slideBox.y + slideBox.height * 0.5;
    const cdp = await context.newCDPSession(page);
    const touchPoint = (x) => ({ x, y, id: 1, radiusX: 1, radiusY: 1, force: 1 });

    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchStart',
      touchPoints: [touchPoint(startX)],
    });
    for (const x of [0.7, 0.55, 0.4, 0.25, 0.15].map(
      (fraction) => slideBox.x + slideBox.width * fraction,
    )) {
      await cdp.send('Input.dispatchTouchEvent', {
        type: 'touchMove',
        touchPoints: [touchPoint(x)],
      });
    }
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });

    await expect(currentImage).toHaveAttribute('src', /run-track-1190\.png$/);
  } finally {
    await context.close();
  }
});

test('gallery fallback link works without JavaScript', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  try {
    await page.goto('/__visual__/event/gallery-populated/');
    const firstCard = page.locator('.gallery-card-link').first();
    await expect(firstCard).toHaveAttribute('href', /\/static\/images\/run-city-1842\.png$/);

    await firstCard.click();
    await expect(page).toHaveURL(/\/static\/images\/run-city-1842\.png$/);
  } finally {
    await context.close();
  }
});

test('selfie search form keeps its native multipart fallback without JavaScript', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  try {
    await page.goto('/__visual__/event/selfie-search/');
    const form = page.locator('[data-selfie-search-form]');
    await expect(form).toHaveAttribute('method', 'post');
    await expect(form).toHaveAttribute('enctype', 'multipart/form-data');
    await expect(form.locator('input[type="file"]')).toHaveAttribute(
      'accept',
      'image/jpeg,image/png,image/heic,image/heif,.heic,.heif',
    );
    await expect(form.getByRole('button', { name: 'Найти мои фото' })).toBeEnabled();
  } finally {
    await context.close();
  }
});

test('selfie search rejection keeps correction controls visible without JavaScript', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  try {
    const response = await page.goto('/__visual__/event/selfie-search/rejected/');
    expect(response?.status()).toBeLessThan(400);
    await expect(page.locator('[data-selfie-search-error]')).toHaveText(
      'Не удалось прочитать фотографию. Выберите JPEG, PNG, HEIC или HEIF.',
    );
    await expect(page.locator('input[type="file"]')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Найти мои фото' })).toBeEnabled();
  } finally {
    await context.close();
  }
});

test('ready selfie result reuses keyboard-accessible gallery lightbox', async ({ page }) => {
  await page.goto('/__visual__/event/selfie-search/ready/');
  const firstCard = page.locator('.selfie-search-results .gallery-card-link').first();
  await firstCard.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.glightbox-container')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(firstCard).toBeFocused();
});

test('gallery and ready result lightboxes keep original download as a compact icon action', async ({
  page,
}) => {
  for (const [path, cardSelector, viewport, snapshot, expectedColor] of [
    [
      '/__visual__/event/gallery-populated/',
      '.gallery-card-link',
      DESKTOP_VIEWPORT,
      'desktop-gallery-lightbox-download.png',
      null,
    ],
    [
      '/__visual__/event/selfie-search/ready/',
      '.selfie-search-results .gallery-card-link',
      MOBILE_VIEWPORT,
      'mobile-selfie-search-result-lightbox-download.png',
      'rgb(104, 111, 119)',
    ],
  ]) {
    await page.setViewportSize(viewport);
    await page.goto(path);
    await page.locator(cardSelector).first().click();

    const description = page.locator('.glightbox-container .gslide.current .gslide-description');
    const download = description.getByRole('link', { name: 'Скачать оригинал' });
    await expect(download).toBeVisible();
    await expect(download.locator('svg use')).toHaveAttribute('href', /#download$/);
    await expect(download).toHaveText('');
    await expect(download).toHaveCSS('width', '44px');
    await expect(download).toHaveCSS('height', '44px');
    await expect(description).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
    await expect(description).toHaveCSS('height', '44px');
    if (expectedColor) {
      await expect(download).toHaveCSS('color', expectedColor);
    }
    await expect(page).toHaveScreenshot(snapshot, { animations: 'disabled' });

    await page.keyboard.press('Escape');
  }
});

test('browser coordinator completes a successful upload and announces progress', async ({ page }) => {
  const stubs = await installUploadStubs(page);
  await preloadCookieAcknowledgement(page);
  await page.goto('/__visual__/upload/empty/');
  await page.locator('#upload-event').selectOption({ index: 1 });
  await page.locator('#upload-files').setInputFiles([
    { name: 'one.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('one') },
    { name: 'two.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('two') },
  ]);

  await expect(page.locator('#upload-summary-title')).toHaveText('Загрузка завершена');
  await expect(page.locator('[data-summary-message]')).toContainText('2 из 2');
  await expect(page.getByRole('status')).toContainText('2 из 2');
  const uploadedToggle = page.locator('[data-queue-group-toggle="uploaded"]');
  await expect(uploadedToggle).toHaveAttribute('aria-expanded', 'false');
  await uploadedToggle.click();
  await expect(page.locator('[data-upload-queue] .queue-item')).toHaveCount(2);
  expect(stubs.controlCalls.filter(({ path }) => path.endsWith('/confirm/'))).toHaveLength(2);
  expect(stubs.pageErrors).toEqual([]);
});

test('upload queue prioritizes groups, discloses them from the keyboard, and pages ten thousand files', async ({ page }) => {
  await page.goto('/__visual__/upload/empty/');
  await page.evaluate(() => {
    const root = document.querySelector('[data-upload-root]');
    const coordinator = root.uploadCoordinator;
    coordinator.items = [
      {
        clientItemId: 'failed', file: { name: 'failed.jpg', size: 10 }, status: 'failed', progress: 50,
        error: 'Файл требует повторной отправки.',
      },
      {
        clientItemId: 'active', file: { name: 'active.jpg', size: 10 }, status: 'uploading', progress: 68,
        error: '',
      },
      ...Array.from({ length: 2 }, (_, index) => ({
        clientItemId: `waiting-${index}`, file: { name: `waiting-${index}.jpg`, size: 10 },
        status: 'waiting', progress: 0, error: '',
      })),
      ...Array.from({ length: 9_996 }, (_, index) => ({
        clientItemId: `uploaded-${index}`, file: { name: `uploaded-${index}.jpg`, size: 10 },
        status: 'uploaded', progress: 100, error: '',
      })),
    ];
    window.FindMeUpload.renderPage(root, coordinator);
  });

  const groups = page.locator('[data-upload-queue] > [data-queue-group]');
  await expect(groups).toHaveCount(4);
  expect(await groups.evaluateAll((nodes) => nodes.map((node) => node.dataset.queueGroup))).toEqual([
    'needs_attention',
    'uploading',
    'waiting',
    'uploaded',
  ]);
  await expect(page.locator('[data-queue-group="needs_attention"]')).toContainText('failed.jpg');
  await expect(page.locator('[data-queue-group="uploading"]')).toContainText('active.jpg');
  await expect(page.locator('[data-queue-group-toggle="needs_attention"]')).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('[data-queue-group-toggle="uploading"]')).toHaveAttribute('aria-expanded', 'true');
  const waitingToggle = page.locator('[data-queue-group-toggle="waiting"]');
  await expect(waitingToggle).toHaveAttribute('aria-expanded', 'false');
  await waitingToggle.focus();
  await page.keyboard.press('Enter');
  await expect(waitingToggle).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('[data-queue-group="waiting"] .queue-item')).toHaveCount(2);

  const uploadedToggle = page.locator('[data-queue-group-toggle="uploaded"]');
  await uploadedToggle.focus();
  await page.keyboard.press('Space');
  await expect(uploadedToggle).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('[data-queue-group="uploaded"] .queue-item')).toHaveCount(20);
  await uploadedToggle.evaluate((button) => button.closest('[data-queue-group]').querySelector('[data-queue-next-page]').click());
  await expect(page.locator('[data-queue-group="uploaded"]')).toContainText('uploaded-20.jpg');
  await expect(page.locator('[data-queue-group="uploaded"] .queue-item')).toHaveCount(20);
});

test('desktop upload summary keeps controls, geometry, and every metric visible as counters gain digits', async ({ page }) => {
  await page.goto('/__visual__/upload/empty/');
  const measurements = await page.evaluate(() => {
    const controls = document.querySelector('.upload-controls');
    const summary = document.querySelector('.upload-summary');
    const countNodes = document.querySelectorAll(
      '[data-total-count], [data-uploaded-count], [data-failed-count], [data-total-bytes]',
    );
    const measureMetrics = () => {
      const metrics = document.querySelector('.summary-metrics').getBoundingClientRect().toJSON();
      const values = Array.from(document.querySelectorAll('.summary-metrics dt, .summary-metrics dd'))
        .map((node) => ({ text: node.textContent, box: node.getBoundingClientRect().toJSON() }));
      return { metrics, values };
    };
    return [9, 10, 999, 1_000].map((count) => {
      countNodes.forEach((node, index) => {
        node.textContent = index === 3 ? '2,1 из 5,8 ГБ' : count.toLocaleString('ru-RU');
      });
      return {
        controls: controls.getBoundingClientRect().toJSON(),
        summary: summary.getBoundingClientRect().toJSON(),
        metricVisibility: measureMetrics(),
        fontVariantNumeric: getComputedStyle(summary.querySelector('[data-total-count]')).fontVariantNumeric,
        widths: [document.documentElement.clientWidth, document.documentElement.scrollWidth],
      };
    });
  });

  for (const measurement of measurements.slice(1)) {
    expect(measurement.controls).toEqual(measurements[0].controls);
    expect(measurement.summary).toEqual(measurements[0].summary);
    expect(measurement.widths[1]).toBeLessThanOrEqual(measurement.widths[0]);
  }
  for (const measurement of measurements) {
    expect(measurement.metricVisibility.values).toHaveLength(8);
    for (const { text, box } of measurement.metricVisibility.values) {
      expect(text).not.toBe('');
      expect(box.top).toBeGreaterThanOrEqual(measurement.metricVisibility.metrics.top);
      expect(box.bottom).toBeLessThanOrEqual(measurement.metricVisibility.metrics.bottom);
    }
  }
  expect(measurements[0].fontVariantNumeric).toContain('tabular-nums');
});

test('mobile upload summary reserves separate metric and message rows', async ({ page }) => {
  await page.setViewportSize(MOBILE_VIEWPORT);
  await page.goto('/__visual__/upload/active/');
  const boxes = await page.evaluate(() => {
    const metrics = document.querySelector('.summary-metrics').getBoundingClientRect().toJSON();
    const message = document.querySelector('[data-summary-message]').getBoundingClientRect().toJSON();
    return { metrics, message };
  });

  expect(boxes.message.y).toBeGreaterThanOrEqual(boxes.metrics.y + boxes.metrics.height);
});

test('returning photographer resumes only the unfinished item from an owned batch', async ({ page }) => {
  const stubs = await installUploadStubs(page);
  await page.goto('/__visual__/upload/empty/?resume=1');

  await page.getByRole('button', { name: 'Продолжить загрузку' }).click();
  await expect(page.locator('#upload-event')).toHaveValue('london-10k');
  await expect(page.locator('#upload-event')).toBeDisabled();
  await page.locator('#resume-upload-files').setInputFiles([
    { name: 'confirmed.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('confirmed') },
    { name: 'pending.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('pending') },
  ]);

  await expect(page.locator('#upload-summary-title')).toHaveText('Загрузка завершена');
  expect(stubs.controlCalls.filter(({ path }) => path.includes('/items/confirmed/'))).toHaveLength(0);
  expect(stubs.controlCalls.filter(({ path }) => path.endsWith('/items/pending/authorize/'))).toHaveLength(1);
  expect(stubs.storageCalls).toEqual(['/upload/pending']);
  expect(stubs.pageErrors).toEqual([]);
});

test('incomplete resumed selections tell the photographer what action is needed', async ({ page }) => {
  await page.goto('/__visual__/upload/empty/');
  await page.evaluate(() => {
    const root = document.querySelector('[data-upload-root]');
    const coordinator = root.uploadCoordinator;
    coordinator.active = false;
    coordinator.items = [
      { clientItemId: 'waiting', file: { name: 'missing.jpg', size: 4 }, status: 'waiting', progress: 0 },
      { clientItemId: 'attention', file: { name: 'extra.jpg', size: 4 }, status: 'needs_attention', progress: 0 },
    ];
    window.FindMeUpload.renderPage(root, coordinator);
  });

  await expect(page.locator('#upload-summary-title')).toHaveText('Требуется действие');
  await expect(page.locator('[data-summary-message]')).toHaveText(
    'Загрузка не завершена: выберите недостающие файлы и проверьте файлы, требующие внимания.',
  );
});

test('browser coordinator accepts a dropped JPEG when the browser omits its MIME type', async ({
  page,
}) => {
  const stubs = await installUploadStubs(page);
  await page.goto('/__visual__/upload/empty/');
  await page.locator('#upload-event').selectOption({ index: 1 });
  await page.locator('[data-upload-drop-target]').evaluate((dropTarget) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(['jpeg'], 'dropped.jpeg'));
    dropTarget.dispatchEvent(
      new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: transfer }),
    );
    dropTarget.dispatchEvent(
      new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: transfer }),
    );
  });

  await expect(page.locator('#upload-summary-title')).toHaveText('Загрузка завершена');
  const registration = stubs.controlCalls.find(({ path }) => path.endsWith('/items/'));
  expect(registration.body.items[0].content_type).toBe('image/jpeg');
  expect(stubs.controlCalls.filter(({ path }) => path.endsWith('/confirm/'))).toHaveLength(1);
  expect(stubs.pageErrors).toEqual([]);
});

test('browser coordinator preserves success when another upload fails', async ({ page }) => {
  const stubs = await installUploadStubs(page, { storageStatuses: [204, 400] });
  await page.goto('/__visual__/upload/empty/');
  await page.locator('#upload-event').selectOption({ index: 1 });
  await page.locator('#upload-files').setInputFiles([
    { name: 'good.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('good') },
    { name: 'bad.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('bad') },
  ]);

  await expect(page.locator('#upload-summary-title')).toHaveText('Загружено частично');
  await expect(page.locator('[data-uploaded-count]')).toHaveText('1');
  await expect(page.locator('[data-failed-count]')).toHaveText('1');
  await expect(page.getByRole('button', { name: 'Повторить' })).toHaveCount(1);
  expect(stubs.pageErrors).toEqual([]);
});

test('slow upload has an active close warning and visible cancel control', async ({ page }) => {
  const stubs = await installUploadStubs(page, { storageDelay: 400 });
  await page.goto('/__visual__/upload/empty/');
  await page.locator('#upload-event').selectOption({ index: 1 });
  await page.locator('#upload-files').setInputFiles({
    name: 'slow.jpg',
    mimeType: 'image/jpeg',
    buffer: Buffer.from('slow'),
  });

  await expect(page.locator('#upload-summary-title')).toHaveText('Идёт загрузка');
  await expect(page.getByRole('button', { name: 'Отменить' })).toBeVisible();
  const warned = await page.evaluate(() => {
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  });
  expect(warned).toBe(true);
  await expect(page.locator('#upload-summary-title')).toHaveText('Загрузка завершена');
  expect(
    await page.evaluate(() => {
      const event = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    }),
  ).toBe(false);
  expect(stubs.pageErrors).toEqual([]);
});

test('cancel is visible during authorization and aborts the pending control request', async ({ page }) => {
  const stubs = await installUploadStubs(page, { authorizeDelay: 1000 });
  await preloadCookieAcknowledgement(page);
  await page.goto('/__visual__/upload/empty/');
  await page.locator('#upload-event').selectOption({ index: 1 });
  await page.locator('#upload-files').setInputFiles({
    name: 'cancel-authorization.jpg',
    mimeType: 'image/jpeg',
    buffer: Buffer.from('cancel-authorization'),
  });

  const cancel = page.getByRole('button', { name: 'Отменить' });
  await expect(cancel).toBeVisible({ timeout: 250 });
  await cancel.click();

  await expect(page.locator('#upload-summary-title')).toHaveText('Загружено частично');
  await expect(page.locator('[data-file-error]')).toHaveText('Передача отменена.');
  expect(stubs.controlCalls.filter(({ path }) => path.endsWith('/failed/'))).toHaveLength(1);
  expect(stubs.pageErrors).toEqual([]);
});

test('expired grant is refreshed once without starting another data attempt', async ({ page }) => {
  const stubs = await installUploadStubs(page, { storageStatuses: [403, 204] });
  await page.goto('/__visual__/upload/empty/');
  await page.locator('#upload-event').selectOption({ index: 1 });
  await page.locator('#upload-files').setInputFiles({
    name: 'expired.jpg',
    mimeType: 'image/jpeg',
    buffer: Buffer.from('expired'),
  });

  await expect(page.locator('#upload-summary-title')).toHaveText('Загрузка завершена');
  expect(
    stubs.controlCalls
      .filter(({ path }) => path.endsWith('/authorize/'))
      .map(({ body }) => body.reason),
  ).toEqual(['data_attempt', 'grant_refresh']);
  expect(stubs.pageErrors).toEqual([]);
});

test('browser queue never exceeds four simultaneous transfers', async ({ page }) => {
  const stubs = await installUploadStubs(page, { storageDelay: 100 });
  await page.goto('/__visual__/upload/empty/');
  await page.locator('#upload-event').selectOption({ index: 1 });
  await page.locator('#upload-files').setInputFiles(
    Array.from({ length: 8 }, (_, index) => ({
      name: `${index}.jpg`,
      mimeType: 'image/jpeg',
      buffer: Buffer.from(String(index)),
    })),
  );

  await expect(page.locator('#upload-summary-title')).toHaveText('Загрузка завершена');
  expect(stubs.getMaxActiveTransfers()).toBeGreaterThan(1);
  expect(stubs.getMaxActiveTransfers()).toBeLessThanOrEqual(4);
  expect(stubs.pageErrors).toEqual([]);
});

test('failed file can be retried from the keyboard without losing its row', async ({ page }) => {
  const stubs = await installUploadStubs(page, { storageStatuses: [400, 204] });
  await page.goto('/__visual__/upload/empty/');
  await page.locator('#upload-event').selectOption({ index: 1 });
  await page.locator('#upload-files').setInputFiles({
    name: 'keyboard.jpg',
    mimeType: 'image/jpeg',
    buffer: Buffer.from('keyboard'),
  });
  await expect(page.locator('#upload-summary-title')).toHaveText('Загружено частично');
  const retry = page.getByRole('button', { name: 'Повторить' });
  await retry.focus();
  await page.keyboard.press('Enter');

  await expect(page.locator('#upload-summary-title')).toHaveText('Загрузка завершена');
  const uploadedToggle = page.locator('[data-queue-group-toggle="uploaded"]');
  await expect(uploadedToggle).toHaveAttribute('aria-expanded', 'false');
  await uploadedToggle.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('[data-upload-queue] .queue-item')).toHaveCount(1);
  expect(stubs.controlCalls.filter(({ path }) => path.endsWith('/retry/'))).toHaveLength(1);
  expect(stubs.pageErrors).toEqual([]);
});

test('manual retry 503 remains retryable without leaking an unhandled page error', async ({ page }) => {
  const stubs = await installUploadStubs(page, {
    retryFailureStatus: 503,
    storageStatuses: [400],
  });
  await preloadCookieAcknowledgement(page);
  await page.goto('/__visual__/upload/empty/');
  await page.locator('#upload-event').selectOption({ index: 1 });
  await page.locator('#upload-files').setInputFiles({
    name: 'retry-503.jpg',
    mimeType: 'image/jpeg',
    buffer: Buffer.from('retry-503'),
  });
  await expect(page.locator('#upload-summary-title')).toHaveText('Загружено частично');

  await page.getByRole('button', { name: 'Повторить' }).click();

  await expect(page.locator('#upload-summary-title')).toHaveText('Загружено частично');
  await expect(page.locator('[data-file-error]')).toHaveText(
    'Не удалось повторить загрузку. Повторите попытку.',
  );
  await expect(page.getByRole('button', { name: 'Повторить' })).toBeVisible();
  expect(await page.evaluate(() => window.document.querySelector('[data-upload-root]').uploadCoordinator.active)).toBe(false);
  expect(stubs.pageErrors).toEqual([]);
});

test('manual retry confirm failure is contained without an unhandled page error', async ({ page }) => {
  const stubs = await installUploadStubs(page, {
    confirmFailureStatus: 503,
    storageStatuses: [400, 204],
  });
  await preloadCookieAcknowledgement(page);
  await page.goto('/__visual__/upload/empty/');
  await page.locator('#upload-event').selectOption({ index: 1 });
  await page.locator('#upload-files').setInputFiles({
    name: 'retry-confirm-503.jpg',
    mimeType: 'image/jpeg',
    buffer: Buffer.from('retry-confirm-503'),
  });
  await expect(page.locator('#upload-summary-title')).toHaveText('Загружено частично');

  await page.getByRole('button', { name: 'Повторить' }).click();

  await expect(page.locator('#upload-summary-title')).toHaveText('Загружено частично');
  await expect(page.locator('[data-file-error]')).toHaveText(
    'Не удалось повторить загрузку. Повторите попытку.',
  );
  expect(stubs.controlCalls.filter(({ path }) => path.endsWith('/failed/'))).toHaveLength(1);
  expect(stubs.pageErrors).toEqual([]);
});
