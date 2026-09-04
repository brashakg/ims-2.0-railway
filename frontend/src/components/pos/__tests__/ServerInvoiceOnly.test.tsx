// ============================================================================
// The tax invoice is the SERVER's document -- and ONLY the server's
// ============================================================================
// Under GST the invoice serial is a consecutive series per financial year,
// minted once and recorded. The retired GSTInvoice modal INVENTED a serial in
// the browser (a BV/FY/store/order-slice pattern that existed nowhere in the
// books) and re-computed GST client-side. Owner decision 2026-09-03: retire
// it -- one invoice document, one number, printed from
// GET /orders/{id}/invoice.pdf. The guards that keep it dead:
//
// 1. BEHAVIOUR: the completion step's "Tax Invoice" button fetches the server
//    PDF and renders NO invoice document of its own.
// 2. SOURCE: no POS source (components/pos + pages/pos, tests excluded) may
//    contain a client-side invoice-number generator or import a GSTInvoice
//    again. A renamed generator can evade the name check -- the behaviour
//    guard is the primary tripwire; this one makes the known implementation
//    un-revertable.
// 3. THERMAL: the 80mm ReceiptPreview (and its POSReceipt wrapper) followed
//    on 2026-09-04 -- no live screen rendered it after the legacy till went,
//    and the owner ruled A4 + WhatsApp, not thermal. No POS source may lay
//    out a thermal receipt in the browser under any name.
// 4. GST MATH: constants/gst exports no calculator, and no POS source extracts
//    GST from a price. The split is the server's (gst_rates.split_gst); a dead
//    client copy of a money rule is the copy that gets resurrected.
//
// Source needles match CODE LINES ONLY -- a note recording why something was
// deleted has to name it, and a guard that matches its own explanation is a
// hollow test this repo has already paid for once.

import { render, screen, act, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
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
import { usePOSStore } from '../../../stores/posStore';
import * as gstModule from '../../../constants/gst';

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

// ---------------------------------------------------------------------------
// Source walk shared by the guards below
// ---------------------------------------------------------------------------
const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = join(HERE, '..', '..', '..'); // frontend/src
const POS_DIRS = [
  join(SRC, 'components', 'pos'),
  join(SRC, 'pages', 'pos'),
];

const sources = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    if (e.name === '__tests__' || e.name.startsWith('.')) return [];
    const p = join(dir, e.name);
    if (e.isDirectory()) return sources(p);
    return /\.(ts|tsx)$/.test(e.name) && !/\.(test|spec)\./.test(e.name) ? [p] : [];
  });

const allSources = POS_DIRS.flatMap(sources);

// Drop comment lines (line comments, block-comment lines, JSX comment lines)
// so prose about a deleted thing can never trip a needle meant for code.
const codeOnly = (src: string): string =>
  src
    .split('\n')
    .filter((l) => !/^(\/\/|\/\*|\*|\{\/\*)/.test(l.trim()))
    .join('\n');

const expectNoNeedle = (files: string[], needles: RegExp[], what: string) => {
  for (const p of files) {
    const code = codeOnly(readFileSync(p, 'utf8'));
    for (const rx of needles) {
      expect(code, `${p} ${what}: matches ${rx}`).not.toMatch(rx);
    }
  }
};

describe('no client-side invoice-number generator exists in the POS source', () => {
  it('found the POS sources it is guarding', () => {
    // If the walk comes back (near) empty the other assertions pass by
    // vacuity -- a moved directory must fail here, not silently disarm.
    expect(allSources.length).toBeGreaterThan(10);
  });

  it('GSTInvoice is gone and stays gone', () => {
    expect(allSources.map((p) => basename(p))).not.toContain('GSTInvoice.tsx');
    expectNoNeedle(allSources, [/from\s+['"][^'"]*GSTInvoice['"]/], 'imports a GSTInvoice component');
  });

  it('no POS source mints an invoice number in the browser', () => {
    // The known generator by name, and its FY-serial template by shape.
    expectNoNeedle(
      allSources,
      [new RegExp('generate' + 'InvoiceNumber'), /\$\{brand\}\/\$\{fy\}/],
      'mints an invoice number',
    );
  });
});

describe('no thermal receipt renderer exists in the POS source', () => {
  it('POSReceipt / ReceiptPreview are gone and stay gone', () => {
    const names = allSources.map((p) => basename(p));
    expect(names).not.toContain('POSReceipt.tsx');
    expect(names).not.toContain('ReceiptPreview.tsx');
    // The receipt's line-description helper was a client copy of the server
    // rule (portal._describe_for_customer); it went with its only consumer.
    expect(existsSync(join(SRC, 'utils', 'receiptFormat.ts'))).toBe(false);
    expectNoNeedle(
      allSources,
      [/from\s+['"][^'"]*(?:ReceiptPreview|POSReceipt|receiptFormat)['"]/],
      'imports the retired receipt',
    );
  });

  it('no POS source lays out an 80mm receipt in the browser, under any name', () => {
    // Shape, not name: a thermal page rule, the 80mm width, the print-area
    // classes the old preview isolated with, or the thermal_receipt legal
    // text kind -- any one of these is a receipt being rendered client-side.
    expectNoNeedle(
      allSources,
      [/@page\s+thermal\b/i, /\b80\s*mm\b/i, /receipt-(?:thermal|print-area)/, /thermal_receipt/],
      'renders a thermal receipt',
    );
  });
});

describe('no client-side GST calculator exists', () => {
  const GST_TS = join(SRC, 'constants', 'gst.ts');

  it('constants/gst still exports what the live screens use (negative control)', () => {
    // Without this, a moved module would make the "exports no calculator"
    // check pass on an empty namespace.
    expect(typeof gstModule.validateGSTNumber).toBe('function');
    expect(typeof gstModule.gstinStateCode).toBe('function');
    expect(readFileSync(GST_TS, 'utf8').length).toBeGreaterThan(1000);
  });

  it('constants/gst exports no GST calculator, named or default', () => {
    const named = Object.keys(gstModule).filter((k) => /calculate/i.test(k));
    expect(named).toEqual([]);
    const viaDefault = Object.keys(gstModule.default).filter((k) => /calculate/i.test(k));
    expect(viaDefault).toEqual([]);
  });

  it('neither constants/gst nor any POS source extracts GST from a price', () => {
    // The inclusive-extraction shape (`x / (100 + rate)`, `x / (1 + rate/100)`)
    // and the calculator by name. The cart's on-screen totals live in
    // stores/posStore and are deliberately outside this walk: the server
    // still totals the document, this only stops a second one appearing in
    // components/pos or pages/pos.
    expectNoNeedle(
      [GST_TS, ...allSources],
      [/\/\s*\(\s*100\s*\+/, /\/\s*\(\s*1\s*\+\s*[^)]*\b(?:rate|gst)/i, /\bcalculate\w*gst\w*\b/i],
      'computes GST client-side',
    );
  });
});
