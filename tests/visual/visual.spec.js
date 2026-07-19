const { expect, test } = require('@playwright/test');

const DESKTOP_VIEWPORT = { width: 1440, height: 1000 };
const MOBILE_VIEWPORT = { width: 390, height: 844 };

const desktopPages = [
  ['catalog-populated', '/__visual__/catalog/populated/'],
  ['catalog-empty', '/__visual__/catalog/empty/'],
  ['event-covered', '/__visual__/event/covered/'],
  ['event-uncovered', '/__visual__/event/uncovered/'],
  ['event-gallery-populated', '/__visual__/event/gallery-populated/'],
  ['event-gallery-empty', '/__visual__/event/gallery-empty/'],
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

async function capturePage(page, { path, snapshot, viewport }) {
  const { failures, resources } = collectBrowserFailures(page);
  await page.setViewportSize(viewport);

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
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await page.route('**/photographer/uploads/**', async (route) => {
    const request = route.request();
    if (request.method() !== 'POST') {
      return route.continue();
    }
    const url = new URL(request.url());
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
    const item = url.pathname.match(/items\/(item-\d+)\//)?.[1];
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
  return { controlCalls, pageErrors, getMaxActiveTransfers: () => maxActiveTransfers };
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

test('browser coordinator completes a successful upload and announces progress', async ({ page }) => {
  const stubs = await installUploadStubs(page);
  await page.goto('/__visual__/upload/empty/');
  await page.locator('#upload-event').selectOption({ index: 1 });
  await page.locator('#upload-files').setInputFiles([
    { name: 'one.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('one') },
    { name: 'two.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('two') },
  ]);

  await expect(page.locator('#upload-summary-title')).toHaveText('Загрузка завершена');
  await expect(page.locator('[data-summary-message]')).toContainText('2 из 2');
  await expect(page.getByRole('status')).toContainText('2 из 2');
  await expect(page.locator('[data-upload-queue] .queue-item')).toHaveCount(2);
  expect(stubs.controlCalls.filter(({ path }) => path.endsWith('/confirm/'))).toHaveLength(2);
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
  await expect(page.locator('[data-upload-queue] .queue-item')).toHaveCount(1);
  expect(stubs.controlCalls.filter(({ path }) => path.endsWith('/retry/'))).toHaveLength(1);
  expect(stubs.pageErrors).toEqual([]);
});

test('manual retry 503 remains retryable without leaking an unhandled page error', async ({ page }) => {
  const stubs = await installUploadStubs(page, {
    retryFailureStatus: 503,
    storageStatuses: [400],
  });
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
