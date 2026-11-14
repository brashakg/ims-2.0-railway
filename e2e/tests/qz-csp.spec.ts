/**
 * The deployed browser policy lets the page reach QZ Tray - and only QZ Tray.
 *
 * Audit L2-INTEGRATION-F03: the Content-Security-Policy that Vercel sends
 * (frontend/vercel.json) allowed connections to the app and the API only, so
 * Chrome refused the QZ Tray websocket (wss://localhost:8181) BEFORE it
 * started and every label fell to the print window. A local Vite run sends no
 * such header, which is how it was missed.
 *
 * This is the audit's probe as a gate: a real Chromium loads a page carrying
 * the EXACT checked-in header and opens a real WebSocket to every endpoint the
 * app connects to (frontend/src/services/qzEndpoint.ts - imported, never
 * copied). The browser must raise no policy violation for those, and must
 * still raise one for a neighbouring local socket the policy does not list
 * (which also proves the probe can see a block).
 *
 * No QZ Tray runs here, so the allowed sockets simply fail to connect: this
 * checks the browser POLICY only. A real print on a shop PC (QZ installed, its
 * certificate trusted, a printer attached) is a separate, physical check.
 * WebSockets are NOT routed through Playwright here - that would bypass the
 * browser's own enforcement, which is the thing under test.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { test, expect } from '@playwright/test';
import { QZ_ORIGINS } from '../../frontend/src/services/qzEndpoint';

const vercel = JSON.parse(
  readFileSync(fileURLToPath(new URL('../../frontend/vercel.json', import.meta.url)), 'utf8'),
);
const CSP: string = vercel.headers
  .flatMap((rule: { headers: { key: string; value: string }[] }) => rule.headers)
  .find((h: { key: string }) => h.key === 'Content-Security-Policy').value;

// The deployed page is https; a made-up host, answered by the route below.
const PAGE = 'https://ims-csp-probe.test/';

// Not QZ: its insecure port, a port QZ never uses, and QZ's second default
// host (a public DNS name that points at 127.0.0.1) - none is in the policy.
const NOT_ALLOWED = ['ws://localhost:8182', 'wss://localhost:9100', 'wss://localhost.qz.io:8181'];

test('the deployed CSP allows the QZ Tray sockets and blocks other local sockets', async ({
  page,
}) => {
  await page.route(PAGE, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'text/html',
      headers: { 'Content-Security-Policy': CSP },
      body: '<!doctype html><title>CSP probe</title>',
    }),
  );
  await page.goto(PAGE);

  const blocked: string[] = await page.evaluate(async (urls) => {
    const seen: string[] = [];
    document.addEventListener('securitypolicyviolation', (e) => {
      if (e.effectiveDirective === 'connect-src') seen.push(e.blockedURI);
    });
    for (const url of urls) {
      try {
        const socket = new WebSocket(url);
        socket.onerror = () => {};
        setTimeout(() => socket.close(), 1000);
      } catch {
        seen.push(url); // some browsers throw instead of raising the event
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 1500));
    return seen;
  }, [...QZ_ORIGINS, ...NOT_ALLOWED]);

  const wasBlocked = (url: string) => blocked.some((b) => b.replace(/\/$/, '') === url);
  expect(QZ_ORIGINS.length).toBeGreaterThan(0);
  expect(QZ_ORIGINS.filter(wasBlocked), 'QZ Tray sockets the policy refused').toEqual([]);
  expect(NOT_ALLOWED.filter((url) => !wasBlocked(url)), 'other sockets the policy let through').toEqual([]);
});
