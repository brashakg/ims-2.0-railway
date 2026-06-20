import { describe, it, expect } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';

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

  it('includes security headers on the root (.*) route', () => {
    const rootRoute = vercelConfig.headers.find((h: any) => h.source === '/(.*)');
    expect(rootRoute).toBeDefined();
    expect(Array.isArray(rootRoute.headers)).toBe(true);
    expect(rootRoute.headers.length).toBeGreaterThan(0);
  });

  it('has X-Content-Type-Options set to nosniff', () => {
    const rootRoute = vercelConfig.headers.find((h: any) => h.source === '/(.*)');
    const header = rootRoute.headers.find((h: any) => h.key === 'X-Content-Type-Options');
    expect(header).toBeDefined();
    expect(header.value).toBe('nosniff');
  });

  it('has X-Frame-Options set to DENY', () => {
    const rootRoute = vercelConfig.headers.find((h: any) => h.source === '/(.*)');
    const header = rootRoute.headers.find((h: any) => h.key === 'X-Frame-Options');
    expect(header).toBeDefined();
    expect(header.value).toBe('DENY');
  });

  it('has Referrer-Policy set to strict-origin-when-cross-origin', () => {
    const rootRoute = vercelConfig.headers.find((h: any) => h.source === '/(.*)');
    const header = rootRoute.headers.find((h: any) => h.key === 'Referrer-Policy');
    expect(header).toBeDefined();
    expect(header.value).toBe('strict-origin-when-cross-origin');
  });

  it('has Strict-Transport-Security with max-age and includeSubDomains', () => {
    const rootRoute = vercelConfig.headers.find((h: any) => h.source === '/(.*)');
    const header = rootRoute.headers.find((h: any) => h.key === 'Strict-Transport-Security');
    expect(header).toBeDefined();
    expect(header.value).toContain('max-age=31536000');
    expect(header.value).toContain('includeSubDomains');
  });

  it('has Content-Security-Policy with frame-ancestors none', () => {
    const rootRoute = vercelConfig.headers.find((h: any) => h.source === '/(.*)');
    const header = rootRoute.headers.find((h: any) => h.key === 'Content-Security-Policy');
    expect(header).toBeDefined();
    expect(header.value).toContain('frame-ancestors \'none\'');
  });

  it('has Content-Security-Policy with default-src self', () => {
    const rootRoute = vercelConfig.headers.find((h: any) => h.source === '/(.*)');
    const header = rootRoute.headers.find((h: any) => h.key === 'Content-Security-Policy');
    expect(header).toBeDefined();
    expect(header.value).toContain("default-src 'self'");
  });

  it('has Content-Security-Policy with wasm-unsafe-eval for Vite runtime', () => {
    const rootRoute = vercelConfig.headers.find((h: any) => h.source === '/(.*)');
    const header = rootRoute.headers.find((h: any) => h.key === 'Content-Security-Policy');
    expect(header).toBeDefined();
    expect(header.value).toContain("'wasm-unsafe-eval'");
  });

  // Regression guard for the #785 prod font outage: index.html loaded Google
  // Fonts but the CSP allowed neither host, so Instrument Serif + Inter silently
  // fell back to system fonts in production. The CSP MUST permit whatever fonts
  // index.html actually loads.
  it('CSP permits the web fonts that index.html actually loads', () => {
    const rootRoute = vercelConfig.headers.find((h: any) => h.source === '/(.*)');
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
    const rootRoute = vercelConfig.headers.find((h: any) => h.source === '/(.*)');
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

  it('preserves asset caching (31536000 seconds = 1 year)', () => {
    const assetRoute = vercelConfig.headers.find((h: any) => h.source === '/assets/(.*)');
    expect(assetRoute).toBeDefined();
    const cacheHeader = assetRoute.headers.find((h: any) => h.key === 'Cache-Control');
    expect(cacheHeader).toBeDefined();
    expect(cacheHeader.value).toContain('max-age=31536000');
    expect(cacheHeader.value).toContain('immutable');
  });

  it('preserves index.html no-cache policy', () => {
    const htmlRoute = vercelConfig.headers.find((h: any) => h.source === '/index.html');
    expect(htmlRoute).toBeDefined();
    const cacheHeader = htmlRoute.headers.find((h: any) => h.key === 'Cache-Control');
    expect(cacheHeader).toBeDefined();
    expect(cacheHeader.value).toContain('no-cache');
    expect(cacheHeader.value).toContain('no-store');
    expect(cacheHeader.value).toContain('must-revalidate');
  });
});
