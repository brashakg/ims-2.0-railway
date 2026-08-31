/**
 * Popup DISCOVERY aid — a manual tool, never run by CI.
 *
 * How tests/popup-layout.spec.ts's POPUPS list was built, and how to extend it
 * when new screens land: point this at some routes, and for each one it reads
 * every enabled button inside #main-content, drops anything whose accessible
 * name looks destructive, clicks the rest one at a time from a fresh page load,
 * and reports which ones made a popup appear.
 *
 * Its output is a CANDIDATE list, not coverage. Hand-check each hit (open it
 * yourself, confirm the heading and that the opener really is read-only) before
 * copying it into POPUPS. Nothing is auto-discovered at test time on purpose: a
 * blocking gate that re-derives its own scope on every run is a flaky gate.
 *
 * Usage (against a running local stack, after the e2e suite has logged in once
 * so fixtures/.auth/user.json exists):
 *
 *   cd e2e
 *   node scripts/discover-popups.mjs /pos /hr/salary-setup /inventory
 *   BASE=http://127.0.0.1:4273 node scripts/discover-popups.mjs /catalog
 *
 * On Git Bash prefix with MSYS_NO_PATHCONV=1, or the leading "/" in each route
 * is rewritten into a Windows path before node ever sees it.
 */
import { chromium } from '@playwright/test';
import fs from 'node:fs';

const BASE = process.env.BASE ?? process.env.E2E_BASE_URL ?? 'http://localhost:4173';
const ROUTES = process.argv.slice(2);

/**
 * Never clicked. This is the safety rule of the whole exercise: a layout probe
 * must not be able to void a bill or approve a refund in order to measure a box.
 * Keep it broad — a missed candidate costs nothing, a wrong click costs money.
 */
const DESTRUCTIVE =
  /delete|remove|void|refund|approve|reject|\bpay\b|payout|cancel|submit|confirm|save|send|post|issue|lock|logout|sign out|archive|deactivate|disable|reset|\brun\b|generate|export|import|sync|push|publish|finali[sz]e|complete|\bstart\b|\bend\b|clear|discard|print|download|upload|merge|split|transfer|adjust|write.?off|escalate|assign|mark |apply|activate|enable|restore|revert|undo|retry|refresh|reload/i;

/** Same union the spec uses; see the POPUP comment in popup-layout.spec.ts. */
const POPUP = '[role="dialog"], div.fixed.inset-0';

const lines = [];
const say = (s) => {
  console.log(s);
  lines.push(s);
  fs.writeFileSync('discover-popups.log', lines.join('\n'));
};

const openPopupCount = (page) =>
  page.evaluate((sel) => {
    return Array.from(document.querySelectorAll(sel)).filter((e) => {
      const r = e.getBoundingClientRect();
      return r.width > 40 && r.height > 40;
    }).length;
  }, POPUP);

if (!ROUTES.length) {
  console.error('usage: node scripts/discover-popups.mjs /route [/route ...]');
  process.exit(2);
}

const browser = await chromium.launch();
const ctx = await browser.newContext({
  storageState: 'fixtures/.auth/user.json',
  viewport: { width: 1440, height: 900 },
});
const page = await ctx.newPage();
const results = [];

for (const route of ROUTES) {
  let names = [];
  try {
    await page.goto(BASE + route, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2500);
    names = await page.evaluate(() => {
      const seen = new Set();
      return Array.from(document.querySelectorAll('#main-content button, #main-content [role="button"]'))
        .filter((b) => b.getBoundingClientRect().width > 2 && !b.disabled)
        .map((b) => (b.getAttribute('aria-label') || b.textContent || '').trim().replace(/\s+/g, ' '))
        .filter((n) => n.length > 1 && n.length < 45)
        .filter((n) => {
          if (seen.has(n)) return false;
          seen.add(n);
          return true;
        });
    });
  } catch (e) {
    say(`### ${route} ERROR ${String(e).slice(0, 120)}`);
    continue;
  }

  const candidates = names.filter((n) => !DESTRUCTIVE.test(n));
  say(`\n### ${route} - ${names.length} buttons, ${candidates.length} safe candidates`);

  for (const name of candidates) {
    try {
      // Fresh load per candidate: closing a popup is per-component and would
      // itself need hand-checking, and a stale one poisons the next reading.
      await page.goto(BASE + route, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(1800);
      const before = await openPopupCount(page);
      const byLabel = page.locator(`#main-content [aria-label="${name.replace(/"/g, '\\"')}"]`).first();
      const target = (await byLabel.count())
        ? byLabel
        : page.locator('#main-content button, #main-content [role="button"]').filter({ hasText: name }).first();
      await target.click({ timeout: 4000 });
      await page.waitForTimeout(1400);
      if ((await openPopupCount(page)) > before) {
        const info = await page.evaluate((sel) => {
          const els = Array.from(document.querySelectorAll(sel)).filter((e) => {
            const r = e.getBoundingClientRect();
            return r.width > 40 && r.height > 40;
          });
          const e = els[els.length - 1];
          return {
            role: e.getAttribute('role'),
            cls: String(e.className).slice(0, 70),
            controls: e.querySelectorAll('button,input,select,textarea,a[href]').length,
            heading: (e.querySelector('h1,h2,h3')?.textContent || '').trim().slice(0, 45),
          };
        }, POPUP);
        say(`  OPENS "${name}" -> role=${info.role} controls=${info.controls} heading="${info.heading}" cls=${info.cls}`);
        results.push({ route, name, ...info });
      }
    } catch {
      // Not clickable at this width, or it navigated instead of opening a
      // popup. Either way it is not a candidate.
    }
  }
}

fs.writeFileSync('discover-popups.json', JSON.stringify(results, null, 2));
say(`\n${results.length} candidate popups written to discover-popups.json — HAND-CHECK before adding to POPUPS.`);
await browser.close();
