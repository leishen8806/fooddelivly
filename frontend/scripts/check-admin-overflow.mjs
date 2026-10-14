import { chromium } from 'playwright';

const baseUrl = process.env.TEACAFE_ADMIN_URL || 'http://127.0.0.1:33000';
const apiUrl = process.env.TEACAFE_API_URL || 'http://127.0.0.1:18000';
const token = process.env.TEACAFE_ADMIN_TOKEN;
const routes = [
  '/admin/overview',
  '/admin/orders',
  '/admin/orders/OVERFLOW-CHECK',
  '/admin/products',
  '/admin/products/new',
  '/admin/categories',
  '/admin/categories/new',
  '/admin/coupons',
  '/admin/coupons/new',
  '/admin/finance/daily',
  '/admin/finance/stats',
  '/admin/refunds',
  '/admin/audit',
  '/admin/staff',
  '/admin/settings',
  '/admin/settings/business',
  '/admin/settings/payment',
  '/admin/settings/notice',
  '/admin/settings/region',
];

if (!token) {
  throw new Error('TEACAFE_ADMIN_TOKEN is required; provide a real manager session JWT for the browser check.');
}

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
await context.addCookies([{ name: 'admin_session_token', value: token, url: apiUrl, httpOnly: true, sameSite: 'Lax' }]);
const page = await context.newPage();
const results = [];

for (const route of routes) {
  await page.goto(`${baseUrl}${route}`, { waitUntil: 'domcontentloaded', timeout: 15000 });
  await page.waitForTimeout(250);
  const result = await page.evaluate(() => ({
    route: window.location.pathname,
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
  }));
  results.push({ requested: route, ...result });
}

console.table(results);
await browser.close();
if (results.some((result) => result.overflow)) {
  process.exitCode = 1;
}
