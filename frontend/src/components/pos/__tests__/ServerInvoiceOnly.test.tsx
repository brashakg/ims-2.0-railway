// ============================================================================
// The tax invoice is the SERVER's document -- and ONLY the server's
// ============================================================================
// Under GST the invoice serial is a consecutive series per financial year,
// minted once and recorded. The retired GSTInvoice modal INVENTED a serial in
// the browser (a BV/FY/store/order-slice pattern that existed nowhere in the
// books) and re-computed GST client-side. Owner decision 2026-09-03: retire
// it -- one invoice document, one number, printed from
// GET /orders/{id}/invoice.pdf. Three guards keep it dead:
//
// 1. BEHAVIOUR: the completion step's "Tax Invoice" button fetches the server
//    PDF and renders NO invoice document of its own.
// 2. RECEIPT: the thermal receipt never titles itself "TAX INVOICE" -- a
//    second document claiming that title with a different serial is the same
//    defect wearing 80mm paper. (The old A4 "Tax Invoice" tab is gone too.)
// 3. SOURCE: no POS source (components/pos + pages/pos, tests excluded) may
//    contain a client-side invoice-number generator or import a GSTInvoice
//    again. A renamed generator can evade the name check -- the behaviour
//    guard is the primary tripwire; this one makes the known implementation
//    un-revertable.

import { render, screen, act, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { readdirSync, readFileSync } from 'node:fs';
import { basename, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

// Map-backed localStorage for the posStore persist middleware.
(() => {
  const m = new Map<string, string>();
  Object.defineProperty(globalThis, 'localStorage', {
    value: {
      getItem: (k: string) => (m.has(k) ? m.get(k)! : null),
      setItem: (k: string, v: string) => { m.set(k, String(v)); },
      removeItem: (k: string) => { m.delete(k); },
      clear: () => { m.clear(); },
      key: (i: number) => Array.from(m.keys())[i] ?? null,
      get length() { return m.size; },
    },
    configurable: true,
    writable: true,
  });
})();

const { apiGet } = vi.hoisted(() => ({ apiGet: vi.fn() }));
vi.mock('../../../services/api/client', () => ({ default: { get: apiGet } }));

import StepComplete from '../POSInvoice';
import { ReceiptPreview } from '../ReceiptPreview';
import { usePOSStore } from '../../../stores/posStore';

describe('POS completion step prints the SERVER invoice', () => {
  beforeEach(() => {
    apiGet.mockReset();
    // jsdom has no blob-URL plumbing or real window.open.
    Object.defineProperty(URL, 'createObjectURL', {
      value: vi.fn(() => 'blob:test-invoice'), configurable: true, writable: true,
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      value: vi.fn(), configurable: true, writable: true,
    });
    vi.spyOn(window, 'open').mockReturnValue({} as Window);
    act(() => {
      const s = usePOSStore.getState();
      s.resetTransaction();
      s.setOrderResult('o1', 'BV-BOK01-000001');
    });
  });

  it('Tax Invoice fetches GET /orders/{id}/invoice.pdf and renders no local invoice', async () => {
    apiGet.mockResolvedValue({ data: new Blob(['%PDF-1.7'], { type: 'application/pdf' }) });
    render(<StepComplete onPrint={() => {}} onReset={() => {}} />);

    const btn = screen.getByRole('button', { name: /Tax Invoice/i });
    expect(btn).toBeEnabled();
    fireEvent.click(btn);

    await waitFor(() =>
      expect(apiGet).toHaveBeenCalledWith('/orders/o1/invoice.pdf', { responseType: 'blob' }),
    );
    await waitFor(() => expect(window.open).toHaveBeenCalledWith('blob:test-invoice', '_blank'));

    // Nothing invoice-shaped may exist in the DOM: no statutory render, no
    // fabricated serial. (The retired modal announced itself with both.)
    expect(screen.queryByText(/RULE 46/i)).toBeNull();
    expect(document.querySelector('.tax-invoice-print')).toBeNull();
    expect(document.body.textContent).not.toMatch(/BV\/FY/);
  });

  it('a failed PDF fetch surfaces an error instead of falling back to a local render', async () => {
    apiGet.mockRejectedValue(new Error('503'));
    render(<StepComplete onPrint={() => {}} onReset={() => {}} />);

    fireEvent.click(screen.getByRole('button', { name: /Tax Invoice/i }));

    expect(await screen.findByText(/Could not build the tax invoice/i)).toBeTruthy();
    expect(document.querySelector('.tax-invoice-print')).toBeNull();
    expect(window.open).not.toHaveBeenCalled();
  });
});

describe('the thermal receipt is a receipt, not a second tax invoice', () => {
  it('never titles itself TAX INVOICE, even with a GSTIN present', () => {
    render(
      <ReceiptPreview
        billData={{
          bill_number: 'BV-BOK01-000001', total_amount: 1050, subtotal: 1050,
          item_discount: 0, order_discount_amount: 0, total_gst: 50,
          igst_amount: 0, roundoff_amount: 0,
        }}
        selectedCustomer={{ name: 'Asha', phone: '9000000001' }}
        cartItems={[]}
        storeData={{ name: 'Better Vision', gst: '20AABCU9603R1ZM' }}
        onClose={() => {}}
      />,
    );
    // What matters is the PRINTED document (the .receipt-print-area is what
    // @media print isolates) -- the modal chrome may explain where the real
    // tax invoice lives, the paper itself must never claim to be one.
    const printArea = document.querySelector('.receipt-print-area') as HTMLElement;
    expect(printArea).toBeTruthy();
    expect(printArea.textContent).not.toMatch(/tax invoice/i);
    expect(printArea.textContent).toContain('SALES RECEIPT');
    // The old A4 client-side invoice tab must not come back either.
    expect(screen.queryByRole('button', { name: /A4 Tax Invoice/i })).toBeNull();
  });
});

describe('no client-side invoice-number generator exists in the POS source', () => {
  const HERE = dirname(fileURLToPath(import.meta.url));
  const POS_DIRS = [
    join(HERE, '..'), // src/components/pos
    join(HERE, '..', '..', '..', 'pages', 'pos'), // src/pages/pos
  ];

  const sources = (dir: string): string[] =>
    readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
      if (e.name === '__tests__' || e.name.startsWith('.')) return [];
      const p = join(dir, e.name);
      if (e.isDirectory()) return sources(p);
      return /\.(ts|tsx)$/.test(e.name) && !/\.(test|spec)\./.test(e.name) ? [p] : [];
    });

  const allSources = POS_DIRS.flatMap(sources);

  it('found the POS sources it is guarding', () => {
    // If the walk comes back (near) empty the other assertions pass by
    // vacuity -- a moved directory must fail here, not silently disarm.
    expect(allSources.length).toBeGreaterThan(10);
  });

  it('GSTInvoice is gone and stays gone', () => {
    expect(allSources.map((p) => basename(p))).not.toContain('GSTInvoice.tsx');
    for (const p of allSources) {
      const src = readFileSync(p, 'utf8');
      expect(src, `${p} imports a GSTInvoice component`).not.toMatch(
        /from\s+['"][^'"]*GSTInvoice['"]/,
      );
    }
  });

  it('no POS source mints an invoice number in the browser', () => {
    // The known generator by name, and its FY-serial template by shape.
    const needles = [
      new RegExp('generate' + 'InvoiceNumber'),
      /\$\{brand\}\/\$\{fy\}/,
    ];
    for (const p of allSources) {
      const src = readFileSync(p, 'utf8');
      for (const rx of needles) {
        expect(src, `${p} matches ${rx}`).not.toMatch(rx);
      }
    }
  });
});
