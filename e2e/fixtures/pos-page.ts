/**
 * Page object for the POS wizard (Quick Sale path).
 *
 * Encapsulates the 4-step quick-sale flow so the specs read as intent:
 *   Customer (salesperson + walk-in) -> Products -> Review -> Payment -> Complete.
 *
 * Selectors are role/label-based and match POSLayout.tsx / POSPayment.tsx /
 * POSInvoice.tsx as they exist on origin/main (PR #331 era).
 */
import { type Page, type Locator, expect } from '@playwright/test';

export class PosPage {
  readonly page: Page;
  readonly salespersonSelect: Locator;
  readonly continueButton: Locator;
  readonly completeOrderButton: Locator;

  constructor(page: Page) {
    this.page = page;
    this.salespersonSelect = page.getByLabel('Select salesperson');
    // Footer CTA — "Continue" on every step except payment ("Complete order").
    this.continueButton = page.getByRole('button', { name: 'Continue' });
    this.completeOrderButton = page.getByRole('button', { name: 'Complete order' });
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
    await this.page
      .getByRole('button', { name: 'Walk-in (Quick Sale only)' })
      .click();
    await expect(this.page.getByText('Walk-in Customer')).toBeVisible();

    await this.continueButton.click();
  }

  /** Step 2 (Products): add a product tile by visible name, then continue. */
  async addProductByName(name: string) {
    const tile = this.page.getByRole('button', { name: new RegExp(escapeRe(name)) });
    await expect(tile.first()).toBeVisible();
    await tile.first().click();
  }

  async continueFromProducts() {
    await this.continueButton.click();
  }

  /** Locate a Review-step total row (Subtotal / Grand Total) value cell. */
  reviewRowValue(label: 'Subtotal' | 'Grand Total'): Locator {
    // Each total row is a flex div: <span>label</span><span>value</span>.
    return this.page
      .locator('div', { has: this.page.getByText(label, { exact: true }) })
      .filter({ hasText: '₹' })
      .last()
      .locator('span')
      .last();
  }

  async continueFromReview() {
    await this.continueButton.click();
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
