// The run history as the clinic reads it: every run with the source that
// started it — Schedule, Manual, Event.
// Playwright is not a dependency of this repository: point PLAYWRIGHT at an
// installation of it (`.../node_modules/playwright/index.mjs`).
const { chromium } = await import(process.env.PLAYWRIGHT);
import { readFileSync } from 'node:fs';

const BASE = process.env.APP_BASE;
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
await page.goto(BASE, { waitUntil: 'domcontentloaded' });
await page.waitForSelector('input[name="username"]', { timeout: 60000 });
await page.fill('input[name="username"]', process.env.APP_USER);
await page.fill('input[name="password"]', readFileSync(process.env.PASS_FILE, 'utf8').trim());
await page.click('input[type="submit"], button[type="submit"]');
await page.waitForLoadState('networkidle', { timeout: 90000 }).catch(() => {});
const tenant = new URL(page.url()).pathname.split('/')[1];
await page.goto(`${BASE}/${tenant}/autopilot/${process.env.AUTOPILOT_ID}`, { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(12000);
// The run picker names the run and its source; opening it is how the history reads.
await page.click('button:has-text("Manual"), button:has-text("Event"), button:has-text("Schedule")').catch((e) => console.log('no picker:', e.message.slice(0, 60)));
await page.waitForTimeout(2500);
await page.screenshot({ path: process.env.OUT });
console.log('wrote', process.env.OUT);
await browser.close();
