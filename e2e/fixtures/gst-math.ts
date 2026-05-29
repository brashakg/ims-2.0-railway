/**
 * GST expectation math — the test's independent oracle.
 *
 * The backend (`orders._compute_per_category_gst`) and the GST invoice
 * (`constants/gst.ts::calculateGST`) both EXTRACT GST from within an inclusive
 * counter price under PR #331:
 *     taxable = price / (1 + rate/100)   (rounded to paise)
 *     tax     = price - taxable
 *     cgst    = floor(tax*100 / 2) / 100   (remainder to sgst)
 *
 * We re-derive the expectations here from first principles so the specs assert
 * a value reasoned from the rule, not copied from the implementation's output.
 *
 * A `mode` switch keeps the suite honest if the backend is ever flipped back to
 * exclusive pricing (price is the pre-tax net, GST added on top).
 */
import type { GstMode } from './constants';

const round2 = (n: number) => Math.round(n * 100) / 100;

export interface LineGst {
  /** The all-in amount the customer pays for this line. */
  grandTotal: number;
  /** Pre-tax base. */
  taxable: number;
  /** Total GST on the line. */
  tax: number;
  cgst: number;
  sgst: number;
}

/** GST for a single line at a given rate, in the given pricing mode. */
export function lineGst(price: number, rate: number, mode: GstMode): LineGst {
  let taxable: number;
  let tax: number;
  let grandTotal: number;
  if (mode === 'inclusive') {
    // Counter price is all-in; extract the components.
    taxable = round2(price / (1 + rate / 100));
    tax = round2(price - taxable);
    grandTotal = round2(taxable + tax); // == price
  } else {
    // Counter price is the net; GST is added on top.
    taxable = round2(price);
    tax = round2((price * rate) / 100);
    grandTotal = round2(taxable + tax);
  }
  // CGST floored, remainder to SGST — matches calculateGST + the backend.
  const cgst = Math.floor((tax * 100) / 2) / 100;
  const sgst = round2(tax - cgst);
  return { grandTotal, taxable, tax, cgst, sgst };
}

/** Aggregate a multi-line cart. */
export function cartGst(
  lines: Array<{ price: number; rate: number }>,
  mode: GstMode
): LineGst {
  let grandTotal = 0;
  let taxable = 0;
  let tax = 0;
  let cgst = 0;
  let sgst = 0;
  for (const l of lines) {
    const g = lineGst(l.price, l.rate, mode);
    grandTotal = round2(grandTotal + g.grandTotal);
    taxable = round2(taxable + g.taxable);
    tax = round2(tax + g.tax);
    cgst = round2(cgst + g.cgst);
    sgst = round2(sgst + g.sgst);
  }
  return { grandTotal, taxable, tax, cgst, sgst };
}
