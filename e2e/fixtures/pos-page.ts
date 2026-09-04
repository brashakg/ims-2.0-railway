/**
 * Page object for the one-surface till at /pos/new (BillingSurface.tsx).
 *
 * Everything is on ONE viewport-locked screen -- no wizard, no Continue:
 * salesperson chip + Hold / Held / Walkout strip on top, customer + Rx and
 * the scan/search row on the left, cart + tenders + "Complete sale" on the
 * right. A finished sale swaps the surface for SaleCompleteScreen, which
 * prints the SERVER's tax invoice (GET /orders/{id}/invoice.pdf). There is no
 * client-side invoice modal any more, so invoice assertions read the same
 * document through ApiClient.getInvoice, not the DOM.
 *
 * Owner rule: EVERY bill needs a customer -- there is no walk-in button. The
 * seed creates no customers, so a spec creates one through "+ New customer"
 * (the same AddCustomerModal the till uses for a first-time buyer).
 *
 * Selectors are role/label-based and match BillingSurface / SalespersonPicker
 * (compact) / AddCustomerModal / ProductResultsStrip / POSCart / POSPayment /
 * SaleCompleteScreen as they exist on origin/main.
 */
import { type Page, type Locator, expect } from '@playwright/test';

/** A fresh 10-digit Indian mobile (leading 9) per call, so a re-run never
 *  trips the one-person-one-record 409 on the customer create door. */
export function uniqueMobile(): string {
  return '9' + Date.now().toString().slice(-9);
}

export class PosPage {
  readonly page: Page;
  /** Compact salesperson chip in the bill strip. Manager-tier only, and the
   *  e2e user is SUPERADMIN; a sales-floor login gets no picker at all. */
  readonly salespersonSelect: Locator;
  /** The scan / search row (BarcodeScanner). Also the route-ready signal. */
  readonly scanInput: Locator;
  readonly completeSaleButton: Locator;

  constructor(page: Page) {
    this.page = page;
    this.salespersonSelect = page.getByLabel('Salesperson', { exact: true });
    this.scanInput = page.getByPlaceholder(/Scan barcode/);
    this.completeSaleButton = page.getByRole('button', { name: 'Complete sale', exact: true });
  }

  async goto() {
    await this.page.goto('/pos/new', { waitUntil: 'domcontentloaded' });
    // Same "the register is up" selector fixtures/routes.ts uses for /pos/new.
    await expect(this.scanInput).toBeVisible();
  }

  /** Pick the first real salesperson once the store's staff list has loaded
   *  (the chip shows a single placeholder option until then). */
  async pickFirstSalesperson() {
    await expect
      .poll(async () => this.salespersonSelect.locator('option').count())
      .toBeGreaterThan(1);
    const value = await this.salespersonSelect.locator('option').nth(1).getAttribute('value');
    await this.salespersonSelect.selectOption(value!);
  }

  /**
   * "+ New customer" -> AddCustomerModal -> Create Customer. Resolves once the
   * customer card is on the bill (its Change button is the signal: it only
   * renders for a selected customer).
   */
  async createCustomer(name = 'E2E Buyer', mobile = uniqueMobile()) {
    await this.page.getByRole('button', { name: '+ New customer' }).click();
    const modal = this.page.locator('div.fixed.inset-0', {
      has: this.page.getByRole('heading', { name: 'Add New Customer' }),
    });
    await expect(modal).toBeVisible();
    await modal.getByPlaceholder('Enter name').fill(name);
    // The customer's own Mobile field comes before any family-member row's.
    await modal.getByPlaceholder('9876543210').first().fill(mobile);
    await modal.getByRole('button', { name: 'Create Customer' }).click();
    await expect(modal).toHaveCount(0);
    await expect(this.page.getByRole('button', { name: 'Change', exact: true })).toBeVisible();
  }

  /**
   * Type the SKU into the scan row and tap the product card in the results
   * strip. Enter on a hyphenated SKU is a MANUAL search (BarcodeScanner only
   * treats 8+ plain alphanumerics as a scan), and product search matches
   * brand / model / sku / variant / barcode -- never the display name -- so
   * the SKU is the handle and the name is what the card and cart line show.
   * Resolves once the cart line exists.
   */
  async addProduct(product: { sku: string; name: string }) {
    await this.scanInput.fill(product.sku);
    await this.scanInput.press('Enter');
    // The strip card carries the SKU in its second line; the cart's own
    // "Remove <name>" button also contains the name, so filter on the SKU.
    await this.page
      .getByRole('button', { name: product.name })
      .filter({ has: this.page.getByText(product.sku) })
      .first()
      .click();
    await expect(
      this.page.getByRole('button', { name: `Remove ${product.name}`, exact: true })
    ).toBeVisible();
  }

  /**
   * A cart-totals row value by its exact label. Each row is
   * `<div><span>label</span><span class="figure">₹value</span></div>`
   * (POSCart totals footer), whole-rupee en-IN, e.g. "₹2,179".
   */
  cartRowValue(label: 'Subtotal' | 'GST' | 'Total (incl. GST)'): Locator {
    return this.page.getByText(label, { exact: true }).locator('..').locator('span').last();
  }

  /** The payment card's "Total Due (incl. GST)" headline amount. */
  get totalDueHeadline(): Locator {
    return this.page.locator('p.text-4xl');
  }

  /** Pay the whole balance in cash: pick Cash (prefills the balance), Add. */
  async payFullCash() {
    await this.page.getByRole('button', { name: 'Cash', exact: true }).click();
    await this.page.getByRole('button', { name: 'Add', exact: true }).click();
    await expect(this.page.getByText(/Payment complete/)).toBeVisible();
  }

  /** "Complete sale" -> SaleCompleteScreen. Returns the ORD-... number it
   *  shows in its "Sale complete" banner. */
  async completeSale(): Promise<string> {
    await expect(this.completeSaleButton).toBeEnabled();
    await this.completeSaleButton.click();
    await expect(this.page.getByText('Sale complete', { exact: true })).toBeVisible({
      timeout: 30_000,
    });
    const line = this.page.getByText(/ORD-/).first();
    await expect(line).toBeVisible();
    const text = (await line.textContent()) ?? '';
    const match = text.match(/ORD-[A-Z0-9-]+/);
    if (!match) {
      throw new Error(`Could not parse order number from "${text}"`);
    }
    return match[0];
  }
}
