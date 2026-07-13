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
  ['reference-upload', '/__visual__/reference/upload/'],
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
  ['reference-upload', '/__visual__/reference/upload/'],
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
    const pathname = new URL(response.url()).pathname;
    if (
      resourceType === 'stylesheet' ||
      pathname.endsWith('.svg') ||
      pathname.startsWith('/static/images/')
    ) {
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

test('public global navigation targets resolve', async ({ page, request }) => {
  await page.goto('/__visual__/legal/');
  const navigation = page.getByRole('navigation', { name: 'Основная навигация' });
  const links = [
    navigation.getByRole('link', { name: 'События' }),
    navigation.getByRole('link', { name: 'Документы' }),
    navigation.getByRole('link', { name: 'Администрирование' }),
  ];

  for (const link of links) {
    const href = await link.getAttribute('href');
    expect(href).toBeTruthy();
    const response = await request.get(href);
    expect(response.status(), `${href} must resolve successfully`).toBeLessThan(400);
  }
});
