import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';

// The catch-all header rule's `source`. It used to be `/(.*)` but that ONLY
// matched the literal source files. The real SPA routes (/, /dashboard, ...)
// are served via the rewrite to /index.html and were getting Vercel's default
// `public, max-age=0, must-revalidate`, which the edge CACHES -> users kept the
// OLD HTML (pointing at old hashed JS) after a deploy. The fix: a
// negative-lookahead catch-all that covers every path EXCEPT /assets/* and
// sends no-cache, so HTML/routes are always fresh while hashed bundles under
// /assets stay immutable. Vercel's `source` supports path-to-regexp lookaheads.
const ROUTE_SOURCE = '/((?!assets/).*)';

describe('vercel.json security headers', () => {
  let vercelConfig: any;

  it('loads vercel.json without errors', () => {
    const vercelPath = path.join(__dirname, '../..', 'vercel.json');
    expect(() => {
      const content = fs.readFileSync(vercelPath, 'utf-8');
      vercelConfig = JSON.parse(content);
    }).not.toThrow();
    expect(vercelConfig).toBeDefined();
  });

  it('defines a headers array', () => {
    expect(Array.isArray(vercelConfig.headers)).toBe(true);
    expect(vercelConfig.headers.length).toBeGreaterThan(0);
  });

  it('includes security headers on the catch-all route', () => {
    const rootRoute = vercelConfig.headers.find((h: any) => h.source === ROUTE_SOURCE);
    expect(rootRoute).toBeDefined();
    expect(Array.isArray(rootRoute.headers)).toBe(true);
    expect(rootRoute.headers.length).toBeGreaterThan(0);
  });

  it('has X-Content-Type-Options set to nosniff', () => {
    const rootRoute = vercelConfig.headers.find((h: any) => h.source === ROUTE_SOURCE);
    const header = rootRoute.headers.find((h: any) => h.key === 'X-Content-Type-Options');
    expect(header).toBeDefined();
    expect(header.value).toBe('nosniff');
  });

  it('has X-Frame-Options set to DENY', () => {
    const rootRoute = vercelConfig.headers.find((h: any) => h.source === ROUTE_SOURCE);
    const header = rootRoute.headers.find((h: any) => h.key === 'X-Frame-Options');
    expect(header).toBeDefined();
    expect(header.value).toBe('DENY');
  });

  it('has Referrer-Policy set to strict-origin-when-cross-origin', () => {
    const rootRoute = vercelConfig.headers.find((h: any) => h.source === ROUTE_SOURCE);
    const header = rootRoute.headers.find((h: any) => h.key === 'Referrer-Policy');
    expect(header).toBeDefined();
    expect(header.value).toBe('strict-origin-when-cross-origin');
  });

  it('has Strict-Transport-Security with max-age and includeSubDomains', () => {
    const rootRoute = vercelConfig.headers.find((h: any) => h.source === ROUTE_SOURCE);
    const header = rootRoute.headers.find((h: any) => h.key === 'Strict-Transport-Security');
    expect(header).toBeDefined();
    expect(header.value).toContain('max-age=31536000');
    expect(header.value).toContain('includeSubDomains');
  });

  it('has Content-Security-Policy with frame-ancestors none', () => {
    const rootRoute = vercelConfig.headers.find((h: any) => h.source === ROUTE_SOURCE);
    const header = rootRoute.headers.find((h: any) => h.key === 'Content-Security-Policy');
    expect(header).toBeDefined();
    expect(header.value).toContain('frame-ancestors \'none\'');
  });

  it('has Content-Security-Policy with default-src self', () => {
    const rootRoute = vercelConfig.headers.find((h: any) => h.source === ROUTE_SOURCE);
    const header = rootRoute.headers.find((h: any) => h.key === 'Content-Security-Policy');
    expect(header).toBeDefined();
    expect(header.value).toContain("default-src 'self'");
  });

  it('has Content-Security-Policy with wasm-unsafe-eval for Vite runtime', () => {
    const rootRoute = vercelConfig.headers.find((h: any) => h.source === ROUTE_SOURCE);
    const header = rootRoute.headers.find((h: any) => h.key === 'Content-Security-Policy');
    expect(header).toBeDefined();
    expect(header.value).toContain("'wasm-unsafe-eval'");
  });

  // Regression guard for the #785 prod font outage: index.html loaded Google
  // Fonts but the CSP allowed neither host, so Instrument Serif + Inter silently
  // fell back to system fonts in production. The CSP MUST permit whatever fonts
  // index.html actually loads.
  it('CSP permits the web fonts that index.html actually loads', () => {
    const rootRoute = vercelConfig.headers.find((h: any) => h.source === ROUTE_SOURCE);
    const csp = rootRoute.headers.find((h: any) => h.key === 'Content-Security-Policy').value as string;
    const indexHtml = fs.readFileSync(path.join(__dirname, '../..', 'index.html'), 'utf-8');
    const styleSrc = (csp.match(/style-src ([^;]+)/) || [])[1] || '';
    const fontSrc = (csp.match(/font-src ([^;]+)/) || [])[1] || '';
    if (indexHtml.includes('fonts.googleapis.com')) {
      // the stylesheet host
      expect(styleSrc).toContain('https://fonts.googleapis.com');
      // Google Fonts CSS @font-face fetches the actual font files from gstatic
      expect(fontSrc).toContain('https://fonts.gstatic.com');
    }
  });

  // index.html must not carry an inline <script> body (it would be CSP-blocked
  // since script-src has no 'unsafe-inline'); boot flags are externalized.
  it('has no CSP-blocked inline script in index.html', () => {
    const rootRoute = vercelConfig.headers.find((h: any) => h.source === ROUTE_SOURCE);
    const csp = rootRoute.headers.find((h: any) => h.key === 'Content-Security-Policy').value as string;
    const scriptSrc = (csp.match(/script-src ([^;]+)/) || [])[1] || '';
    const indexHtml = fs.readFileSync(path.join(__dirname, '../..', 'index.html'), 'utf-8');
    expect(/<script>[\s\S]*?<\/script>/.test(indexHtml)).toBe(false);
    expect(scriptSrc).not.toContain("'unsafe-inline'");
  });

  it('preserves existing rewrites for SPA routing', () => {
    expect(Array.isArray(vercelConfig.rewrites)).toBe(true);
    const spaRewrite = vercelConfig.rewrites.find((r: any) => r.source === '/(.*)');
    expect(spaRewrite).toBeDefined();
    expect(spaRewrite.destination).toBe('/index.html');
  });

  it('preserves asset caching (31536000 seconds = 1 year, immutable)', () => {
    const assetRoute = vercelConfig.headers.find((h: any) => h.source === '/assets/(.*)');
    expect(assetRoute).toBeDefined();
    const cacheHeader = assetRoute.headers.find((h: any) => h.key === 'Cache-Control');
    expect(cacheHeader).toBeDefined();
    expect(cacheHeader.value).toContain('max-age=31536000');
    expect(cacheHeader.value).toContain('immutable');
  });

  it('sets X-Content-Type-Options on hashed assets too', () => {
    const assetRoute = vercelConfig.headers.find((h: any) => h.source === '/assets/(.*)');
    const header = assetRoute.headers.find((h: any) => h.key === 'X-Content-Type-Options');
    expect(header).toBeDefined();
    expect(header.value).toBe('nosniff');
  });

  // The core stale-after-deploy fix: the catch-all (which serves /, /dashboard,
  // index.html, /version.json, manifest, boot-flags — every non-asset path) MUST
  // send no-cache so the edge never serves stale HTML after a deploy.
  it('serves no-cache on the catch-all route (HTML + all SPA routes, not just /index.html)', () => {
    const rootRoute = vercelConfig.headers.find((h: any) => h.source === ROUTE_SOURCE);
    expect(rootRoute).toBeDefined();
    const cacheHeader = rootRoute.headers.find((h: any) => h.key === 'Cache-Control');
    expect(cacheHeader).toBeDefined();
    expect(cacheHeader.value).toContain('no-cache');
    expect(cacheHeader.value).toContain('no-store');
    expect(cacheHeader.value).toContain('must-revalidate');
  });

  // The catch-all source must EXCLUDE /assets/ so hashed bundles keep their
  // immutable caching (the negative-lookahead). Guards against a regression to
  // a plain `/(.*)` that would no-cache the bundles too.
  it('catch-all excludes /assets via negative-lookahead', () => {
    const rootRoute = vercelConfig.headers.find((h: any) => h.source === ROUTE_SOURCE);
    expect(rootRoute).toBeDefined();
    expect(ROUTE_SOURCE).toContain('(?!assets/)');
  });

  // No two header rules may set a conflicting Cache-Control on the same path.
  // The asset path (e.g. /assets/index-abc.js) is excluded from the catch-all,
  // so only the immutable rule applies to it — never the no-cache rule.
  it('does not apply no-cache to /assets paths', () => {
    const sampleAsset = '/assets/index-abc123.js';
    const ruleMatches = (source: string, p: string): boolean => {
      // Translate Vercel/path-to-regexp source to a JS RegExp for this check.
      // Anchor full-path. `(.*)` and `((?!assets/).*)` both translate directly.
      try {
        return new RegExp('^' + source + '$').test(p);
      } catch {
        return false;
      }
    };
    const matched = vercelConfig.headers.filter((h: any) => ruleMatches(h.source, sampleAsset));
    // The asset must match the immutable rule and NOT the no-cache catch-all.
    const sources = matched.map((m: any) => m.source);
    expect(sources).toContain('/assets/(.*)');
    expect(sources).not.toContain(ROUTE_SOURCE);
  });

  // There must be no longer be a separate /index.html rule that could conflict
  // with the catch-all's Cache-Control (the catch-all now covers it).
  it('has no standalone /index.html rule conflicting with the catch-all', () => {
    const htmlRoute = vercelConfig.headers.find((h: any) => h.source === '/index.html');
    expect(htmlRoute).toBeUndefined();
  });
});
