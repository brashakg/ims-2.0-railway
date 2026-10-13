/**
 * The photo badges on the add / edit page, measured in a real browser.
 *
 * Each photo tile carries a dark place-number pill and photo 1 a brand-red
 * 'Main photo' badge, both with white text. Inside <main> the light-theme
 * rules in frontend/src/index.css repaint `span.text-white` near-black and
 * bleach `.bg-gray-900` near-white: 'Main photo' went black on red (3.22:1,
 * WCAG AA needs 4.5:1). jsdom has no stylesheet, so only a browser sees it.
 *
 * Read-only: the product is a route mock (nothing is saved, no seeded photo
 * needed); the photo files need not load, the badges draw either way.
 */
import { test, expect } from '../fixtures/test';

const ID = 'E2E-PHOTO-BADGES';
const PRODUCT = {
  product_id: ID,
  sku: 'SG-E2E-0001',
  category: 'SG',
  brand: 'Ray-Ban',
  model: 'RB4165',
  attributes: { brand_name: 'Ray-Ban', model_no: 'RB4165', colour_code: '001' },
  mrp: 7890,
  offer_price: 7890,
  hsn_code: '900410',
  gst_rate: 18,
  images: ['/api/v1/products/image/e2e-front', '/api/v1/products/image/e2e-side'],
};

test('photo badges keep white text on their dark and brand-red fills, at AA contrast', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 }); // the shop tablet, landscape
  await page.route(`**/api/v1/products/${ID}`, (r) => r.fulfill({ json: PRODUCT }));
  await page.goto(`/catalog/add?edit=${ID}`);

  const photo1 = page.getByAltText('Photo 1').locator('xpath=..');
  for (const badge of [photo1.getByText('1', { exact: true }), photo1.getByText('Main photo', { exact: true })]) {
    await expect(badge).toBeVisible();
    const m = await badge.evaluate((el) => {
      // Any CSS colour (Tailwind v4 writes oklch) -> sRGB bytes, via a canvas.
      const ctx = document.createElement('canvas').getContext('2d')!;
      const rgb = (c: string) => {
        ctx.clearRect(0, 0, 1, 1);
        ctx.fillStyle = c;
        ctx.fillRect(0, 0, 1, 1);
        return Array.from(ctx.getImageData(0, 0, 1, 1).data.slice(0, 3));
      };
      const cs = getComputedStyle(el);
      return { inMain: !!el.closest('main'), fg: rgb(cs.color), bg: rgb(cs.backgroundColor) };
    });
    const lum = (c: number[]) => {
      const [r, g, b] = c.map((v) => v / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const [hi, lo] = [lum(m.fg), lum(m.bg)].sort((a, b) => b - a);
    const label = await badge.textContent();
    expect(m.inMain, `${label}: the probe must sit where the page draws it, inside <main>`).toBe(true);
    expect(m.fg, `${label}: text colour`).toEqual([255, 255, 255]);
    expect((hi + 0.05) / (lo + 0.05), `${label}: contrast`).toBeGreaterThanOrEqual(4.5);
  }
});
