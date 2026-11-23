// The stand as a person sees it: sign in through the application's own front
// door and capture the pages the scenarios live on.
// Playwright is not a dependency of this repository: point PLAYWRIGHT at an
// installation of it (`.../node_modules/playwright/index.mjs`).
const { chromium } = await import(process.env.PLAYWRIGHT);
import { readFileSync } from 'node:fs';

const BASE = process.env.APP_BASE;
const PASSWORD = readFileSync(process.env.PASS_FILE, 'utf8').trim();

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1400 } });
await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('input[name="username"]', { timeout: 60000 });
await page.fill('input[name="username"]', process.env.APP_USER);
await page.fill('input[name="password"]', PASSWORD);
await page.click('input[type="submit"], button[type="submit"]');
await page.waitForLoadState('networkidle', { timeout: 90000 }).catch(() => {});
const tenant = new URL(page.url()).pathname.split('/')[1];
console.log('tenant', tenant, 'at', page.url());

for (const [name, path, wait] of JSON.parse(process.env.PAGES)) {
  await page.goto(`${BASE}/${tenant}${path}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(wait ?? 9000);
  await page.screenshot({ path: `${process.env.OUTDIR}/${name}.png`, fullPage: true });
  console.log('wrote', name, '←', page.url());
}
await browser.close();
