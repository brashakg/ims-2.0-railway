// ============================================================================
// The Orders screen must not print an invoice it built itself
// ============================================================================
// `printOrder` used to hand-build an HTML page titled "Tax Invoice" and print
// the ORDER number under an "Invoice:" label, with client-assembled totals. So
// a customer could be handed paper carrying a number that exists nowhere in the
// books. Under Indian GST the invoice serial is a consecutive series per
// financial year, minted once by the server and recorded -- a number assembled
// in a browser cannot be that, however plausible it looks.
//
// It was the THIRD renderer of this defect class in the app. The POS GSTInvoice
// modal invented a BV/FY/store serial outright and the receipt preview's A4 tab
// hard-coded 9/9/18 tax labels; both were retired on 2026-09-03. This file is
// the tripwire for the Orders screen, so the pattern cannot come back here.
//
// It is a SOURCE guard, and the honest limit of that is worth stating: it
// catches the shapes we have actually seen (a document title, an "Invoice:"
// label, a window.open of hand-written markup). A renderer that avoided all
// three spellings would slip past. The primary defence is that the screen calls
// the server door -- asserted first, below -- and this is the backstop.

import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect } from 'vitest';

const HERE = dirname(fileURLToPath(import.meta.url));
const ORDERS_PAGE = join(HERE, '..', 'OrdersPage.tsx');
const src = readFileSync(ORDERS_PAGE, 'utf8');

/** The body of printOrder only -- the rest of the page legitimately renders. */
function printOrderBody(): string {
  const start = src.indexOf('const printOrder = async (order: Order) => {');
  expect(start, 'printOrder no longer exists under that name').toBeGreaterThan(-1);
  const end = src.indexOf('\n  };', start);
  expect(end).toBeGreaterThan(start);
  // CODE ONLY. The note recording why the hand-built invoice was deleted has to
  // name what it deleted, and a comment is never handed to a customer -- only
  // markup is. Matching prose is how an earlier guard in this repo failed on
  // its own explanation.
  return src
    .slice(start, end)
    .split('\n')
    .filter((l) => !l.trim().startsWith('//'))
    .join('\n');
}

describe('the Orders screen prints the server invoice, never its own', () => {
  it('reads the file it means to guard', () => {
    // Without this, a bad path would make every assertion below vacuously pass
    // on an empty string -- the classic hollow source guard.
    expect(src.length).toBeGreaterThan(1000);
    expect(src).toContain('printOrder');
  });

  it('calls the server invoice door', () => {
    expect(printOrderBody()).toContain('openOrderInvoice');
  });

  it('does not assemble an invoice document of its own', () => {
    const body = printOrderBody();
    // The defect is ASSEMBLING A DOCUMENT, not naming one: the phrase "tax
    // invoice" legitimately appears in the error message this function shows,
    // so matching the phrase would fail on correct code. Match the markup.
    expect(body).not.toMatch(/<\s*(div|table|html|body|strong|h[1-6])\b/i);
    expect(body).not.toContain('document.write');
    expect(body).not.toMatch(/window\.open\(\s*['"`]\s*['"`]/);
    expect(body).not.toContain('<style');
  });

  it('never labels the ORDER number as the invoice number', () => {
    // The specific defect: `<strong>Invoice:</strong> ${order.orderNumber}`.
    // The order number is not the statutory serial and never was.
    const body = printOrderBody();
    expect(body).not.toMatch(/Invoice[^\n]{0,40}orderNumber/);
  });

  it('leaves the delivery challan alone, which was already a server document', () => {
    // The negative control. printChallan has always opened a server-rendered
    // document; if this guard were somehow matching the whole file rather than
    // printOrder, this sibling would be the thing it broke.
    expect(src).toContain('openOrderChallan');
  });
});
