/**
 * E2E tests for the shop React Admin UI.
 *
 * Key invariants of this setup:
 * - Hash routing: all RA routes are under /#/resource
 * - React Admin v5 uses optimistic deletes (no confirm dialog, shows "Undo" snack)
 * - Inputs carry name="<source>" attributes from react-hook-form
 */
import { test, expect, Page } from '@playwright/test';

const API = 'http://localhost:8001';
const BASE = 'http://localhost:5173';

// ---------------------------------------------------------------------------
// API helpers (bypass UI for setup/teardown)
// ---------------------------------------------------------------------------

async function apiPost(path: string, body: object) {
  const res = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json();
}

async function apiDelete(path: string) {
  await fetch(`${API}${path}`, { method: 'DELETE' });
}

// ---------------------------------------------------------------------------
// Navigation helpers
// ---------------------------------------------------------------------------

async function goto(page: Page, hash: string) {
  await page.goto(`${BASE}/#${hash}`, { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(500);
}

async function waitForRows(page: Page) {
  await page.waitForSelector('.RaDatagrid-row', { timeout: 10000 });
}

// ---------------------------------------------------------------------------
// Navigation / smoke tests
// ---------------------------------------------------------------------------

test.describe('Navigation', () => {
  test('loads without JS errors', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', err => errors.push(err.message));

    await goto(page, '/customers');
    await page.waitForTimeout(2000);

    expect(errors).toHaveLength(0);
  });

  test('sidebar shows all three resources', async ({ page }) => {
    await goto(page, '/customers');

    await expect(page.locator('a[href="#/customers"]')).toBeVisible();
    await expect(page.locator('a[href="#/products"]')).toBeVisible();
    await expect(page.locator('a[href="#/orders"]')).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Customers (integer primary key)
// ---------------------------------------------------------------------------

test.describe('Customers', () => {
  test('list loads and shows rows', async ({ page }) => {
    await goto(page, '/customers');
    await waitForRows(page);
    await expect(page.locator('.RaDatagrid-row').first()).toBeVisible();
  });

  test('list pagination shows total count', async ({ page }) => {
    await goto(page, '/customers');
    await expect(page.locator('.MuiTablePagination-displayedRows')).toBeVisible();
  });

  test('can create a customer', async ({ page }) => {
    await goto(page, '/customers/create');

    await page.fill('input[name="email"]', 'e2e-new@test.com');
    await page.click('button[aria-label="Save"]');

    // After save, redirected to the new record
    await page.waitForURL(/\/#\/customers\/\d+$/, { timeout: 10000 });
    await expect(page.locator('.RaNotification-error')).not.toBeVisible();

    // Clean up: extract id from URL and delete
    const id = page.url().match(/\/customers\/(\d+)$/)?.[1];
    if (id) await apiDelete(`/customers/${id}`);
  });

  test('can edit a customer', async ({ page }) => {
    const customer = await apiPost('/customers/', { email: 'e2e-edit@test.com' });

    await goto(page, `/customers/${customer.id}`);

    await page.fill('input[name="email"]', 'e2e-edited@test.com');
    await page.click('button[aria-label="Save"]');

    await expect(page.locator('.RaNotification-error')).not.toBeVisible();
    await apiDelete(`/customers/${customer.id}`);
  });

  test('can delete a customer', async ({ page }) => {
    const customer = await apiPost('/customers/', { email: 'e2e-del@test.com' });

    await goto(page, `/customers/${customer.id}`);
    await page.click('button[aria-label="Delete"]');

    // Optimistic delete: navigates immediately to list with Undo snack
    await page.waitForURL(/\/#\/customers$/, { timeout: 10000 });
    await expect(page.locator('button:has-text("Undo")')).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Products (UUID primary key)
// ---------------------------------------------------------------------------

test.describe('Products', () => {
  test('list loads and shows rows', async ({ page }) => {
    await goto(page, '/products');
    await waitForRows(page);
    await expect(page.locator('.RaDatagrid-row').first()).toBeVisible();
  });

  test('can create a product', async ({ page }) => {
    await goto(page, '/products/create');

    await page.fill('input[name="name"]', 'E2E Widget');
    await page.fill('input[name="price"]', '42');
    await page.click('button[aria-label="Save"]');

    // UUID in URL
    await page.waitForURL(/\/#\/products\/[a-f0-9-]{36}$/, { timeout: 10000 });
    await expect(page.locator('.RaNotification-error')).not.toBeVisible();

    const uuid = page.url().match(/\/products\/([a-f0-9-]{36})$/)?.[1];
    if (uuid) await apiDelete(`/products/${uuid}`);
  });

  test('can edit a product', async ({ page }) => {
    const product = await apiPost('/products/', { name: 'E2E Edit', price: 1.0 });

    await goto(page, `/products/${product.id}`);

    await page.fill('input[name="price"]', '99');
    await page.click('button[aria-label="Save"]');

    await expect(page.locator('.RaNotification-error')).not.toBeVisible();
    await apiDelete(`/products/${product.id}`);
  });

  test('can delete a product', async ({ page }) => {
    const product = await apiPost('/products/', { name: 'E2E Del', price: 1.0 });

    await goto(page, `/products/${product.id}`);
    await page.click('button[aria-label="Delete"]');

    await page.waitForURL(/\/#\/products$/, { timeout: 10000 });
    await expect(page.locator('button:has-text("Undo")')).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// Orders (relationships + timestamps)
// ---------------------------------------------------------------------------

test.describe('Orders', () => {
  let customerId: number;
  let productId: string;
  let orderId: number;

  test.beforeAll(async () => {
    const c = await apiPost('/customers/', { email: 'e2e-orders@test.com' });
    customerId = c.id;
    const p = await apiPost('/products/', { name: 'E2E Order Product', price: 5.0 });
    productId = p.id;
    const o = await apiPost('/orders/', {
      customer_id: customerId,
      products: [{ id: productId }],
    });
    orderId = o.id;
  });

  test.afterAll(async () => {
    await apiDelete(`/orders/${orderId}`);
    await apiDelete(`/products/${productId}`);
    await apiDelete(`/customers/${customerId}`);
  });

  test('list loads and shows rows', async ({ page }) => {
    await goto(page, '/orders');
    await waitForRows(page);
    await expect(page.locator('.RaDatagrid-row').first()).toBeVisible();
  });

  test('list shows created_at column', async ({ page }) => {
    await goto(page, '/orders');
    await waitForRows(page);

    // The ListGuesser renders column headers from field names
    const headers = await page.$$eval('th', els => els.map(el => el.textContent?.trim().toLowerCase()));
    expect(headers.some(h => h?.includes('created'))).toBeTruthy();
  });

  test('can delete an order', async ({ page }) => {
    const extra = await apiPost('/orders/', {
      customer_id: customerId,
      products: [],
    });

    await goto(page, `/orders/${extra.id}`);
    await page.click('button[aria-label="Delete"]');

    await page.waitForURL(/\/#\/orders$/, { timeout: 10000 });
    await expect(page.locator('button:has-text("Undo")')).toBeVisible();
  });
});
