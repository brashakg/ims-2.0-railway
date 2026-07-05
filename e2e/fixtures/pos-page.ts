/**
 * Page object for the POS condensed checkout (Quick Sale path).
 *
 * The condensed 3-step grouping is the SOLE checkout flow since PR #783/#790
 * (the classic 4-step wizard was removed). For a QUICK SALE the groups are
 *   Customer -> Products -> Payment
 * — there is NO Review step (QUICK_STEPS parity: the review panel only exists
 * inside the merged "Pay & Review" group of a prescription order).
 *
 * Totals the customer sees before Payment therefore live in the CART SIDEBAR
 * (right column, `aside.pos-cart-col`): Subtotal / GST / "Total (incl. GST)".
 *
 * Selectors are role/label-based and match POSLayout.tsx / POSCart.tsx /
 * POSPayment.tsx / POSInvoice.tsx as they exist on origin/main.
 */
import { type Page, type Locator, expect } from '@playwright/test';

export class PosPage {
  readonly page: Page;
  readonly salespersonSelect: Locator;
  readonly continueButton: Locator;
  readonly completeOrderButton: Locator;
  /** Right cart column (inline on desktop) — scopes the totals rows. */
  readonly cart: Locator;

  constructor(page: Page) {
    this.page = page;
    this.salespersonSelect = page.getByLabel('Select salesperson');
    // Bottom action bar CTA — "Continue" on every input group except the
    // final one, where it reads "Complete order" (POSLayout pos-footer).
    this.continueButton = page.getByRole('button', { name: 'Continue' });
    this.completeOrderButton = page.getByRole('button', { name: 'Complete order' });
    this.cart = page.locator('aside.pos-cart-col');
  }

  async goto() {
    await this.page.goto('/pos', { waitUntil: 'domcontentloaded' });
    // Step 1 renders the salesperson picker once the store context is ready.
    await expect(this.salespersonSelect).toBeVisible();
  }

  /** Step 1: pick the first real salesperson and create a walk-in customer. */
  async selectFirstSalespersonAndWalkin() {
    // Wait for staff to load (the picker shows "Loading staff…" until then),
    // then pick the first non-placeholder option by value.
    await expect
      .poll(async () => this.salespersonSelect.locator('option').count())
      .toBeGreaterThan(1);
    const firstRealValue = await this.salespersonSelect
      .locator('option')
      .nth(1)
      .getAttribute('value');
    await this.salespersonSelect.selectOption(firstRealValue!);

    // The CUSTOMER button (not the sidebar "+1 walk-in" footfall button).
    // Picking it also forces sale_type = quick_sale.
    await this.page
      .getByRole('button', { name: 'Walk-in (Quick Sale only)' })
      .click();
    // Confirm the selected-customer CARD rendered. "Walk-in Customer" also
    // appears in the step-indicator subtitle (a <div>), so scope to the card's
    // <p> (paragraph role) to avoid a strict-mode 2-element match.
    await expect(
      this.page.getByRole('paragraph').filter({ hasText: /^Walk-in Customer$/ })
    ).toBeVisible();

    await this.continueButton.click();
  }

  /** Step 2 (Products): add a product tile by visible name. */
  async addProductByName(name: string) {
    const tile = this.page.getByRole('button', { name: new RegExp(escapeRe(name)) });
    await expect(tile.first()).toBeVisible();
    await tile.first().click();
  }

  /**
   * Leave the Products step. For a quick sale this lands DIRECTLY on the
   * Payment step (no Review group exists — QUICK_STEPS parity).
   */
  async continueFromProducts() {
    await this.continueButton.click();
  }

  /**
   * Locate a cart-sidebar totals row value by its exact label.
   * Each row is `<div><span>label</span><span class="figure">₹value</span></div>`
   * (POSCart.tsx totals footer). The value span carries the ₹-prefixed,
   * whole-rupee en-IN formatted amount (e.g. "₹2,179").
   */
  cartRowValue(label: 'Subtotal' | 'GST' | 'Total (incl. GST)'): Locator {
    return this.cart
      .locator('div', { has: this.page.getByText(label, { exact: true }) })
      .last()
      .locator('span')
      .last();
  }

  /** Step 4 (Payment): the "Total Due (incl. GST)" headline amount. */
  get totalDueHeadline(): Locator {
    return this.page.locator('p.text-4xl');
  }

  /** Pay the whole balance in cash, then finalise the order. */
  async payFullCashAndComplete() {
    await this.page.getByRole('button', { name: 'Full Cash', exact: true }).click();
    await expect(
      this.page.getByText(/Payment complete/i)
    ).toBeVisible();
    await expect(this.completeOrderButton).toBeEnabled();
    await this.completeOrderButton.click();
  }

  /** Step 5 (Complete): wait for success and return the ORD-... number. */
  async waitForOrderCreated(): Promise<string> {
    await expect(
      this.page.getByRole('heading', { name: 'Order Created!' })
    ).toBeVisible({ timeout: 30_000 });
    const orderLine = this.page.getByText(/Order #ORD-/);
    await expect(orderLine).toBeVisible();
    const text = (await orderLine.textContent()) ?? '';
    const match = text.match(/ORD-[A-Z0-9-]+/);
    if (!match) {
      throw new Error(`Could not parse order number from "${text}"`);
    }
    return match[0];
  }

  /** Open the GST Tax Invoice modal from the Complete step. */
  async openTaxInvoice() {
    await this.page.getByRole('button', { name: 'Tax Invoice' }).click();
    await expect(
      this.page.getByRole('heading', { name: 'GST Tax Invoice' })
    ).toBeVisible();
  }
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
