const { expect, test } = require('@playwright/test');

const DESKTOP_VIEWPORT = { width: 1440, height: 1000 };
const MOBILE_VIEWPORT = { width: 390, height: 844 };

const desktopPages = [
  ['catalog-populated', '/__visual__/catalog/populated/'],
  ['catalog-empty', '/__visual__/catalog/empty/'],
  ['event-covered', '/__visual__/event/covered/'],
  ['event-uncovered', '/__visual__/event/uncovered/'],
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
