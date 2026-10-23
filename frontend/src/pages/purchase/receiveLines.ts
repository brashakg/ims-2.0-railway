// ============================================================================
// IMS 2.0 - The one receiving screen: its rules, without React
// ============================================================================
// Every kind of receipt (against an order, bought without PO, a delivery
// challan) is the same list of lines with the same per-line rules: count it,
// tick "I counted this", say what is damaged and what the supplier owes for
// it, put it on a shelf. These pure functions are those rules, once; the
// screen (ReceiveForm.tsx) only draws them.

export type ReceiveKind = 'STANDARD' | 'DELIVERY_CHALLAN' | 'NO_PO';
export type DamageChoice = '' | 'replacement' | 'credit_note';

/** One order a receipt can be logged against, as GET
 *  /vendors/purchase-orders?receipt_candidates=true sends it. */
export interface CandidatePO {
  po_id: string;
  po_number: string;
  vendor_id: string;
  vendor_name: string;
  status: string;
  delivery_store_id: string;
  expected_date: string | null;
  closed_by_cancel: boolean;
  extra_receipt_allowed: boolean;
  items: Array<Record<string, unknown>>;
  received_qty_by_product: Record<string, number>;
}

export interface ReceiveLine {
  key: string;
  po_item_id?: string;
  product_id: string;
  product_name: string;
  sku?: string;
  /** Still due on the order; null for a line typed in with no order. */
  due: number | null;
  arrived: number;
  damaged: number;
  damageReason: string;
  damageChoice: DamageChoice;
  counted: boolean;
  shelf: string;
  batch: string;
  expiry: string;
  /** Bought without PO, cost roles only. */
  unitPrice?: number;
  /** A cancelled product on the order: every piece that arrives is rejected. */
  rejectOnly: boolean;
  // From the product master (fail-soft: absent until it loads).
  contactLens?: boolean;
  heldForCatalogue?: boolean;
  codes?: string[];
}

const num = (v: unknown) => {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
};

export function toCandidate(p: Record<string, unknown>): CandidatePO {
  return {
    po_id: String(p.po_id ?? p.id ?? p._id ?? ''),
    po_number: String(p.po_number ?? p.po_id ?? 'PO'),
    vendor_id: String(p.vendor_id ?? ''),
    vendor_name: String(p.vendor_name ?? '').trim() || String(p.vendor_id ?? ''),
    status: String(p.status ?? ''),
    delivery_store_id: String(p.delivery_store_id ?? ''),
    expected_date: p.expected_date ? String(p.expected_date) : null,
    closed_by_cancel: !!p.closed_by_cancel,
    extra_receipt_allowed: !!p.extra_receipt_allowed,
    items: Array.isArray(p.items) ? (p.items as Array<Record<string, unknown>>) : [],
    received_qty_by_product: (p.received_qty_by_product ?? {}) as Record<string, number>,
  };
}

/** An order a cancel closed: what arrives is logged, all of it rejected. */
export const isRejectOnlyOrder = (po: CandidatePO) => po.closed_by_cancel && !po.extra_receipt_allowed;
/** A fully received order: only extra goods are counted. */
export const isExtraReceipt = (po: CandidatePO) => po.status === 'RECEIVED' && !isRejectOnlyOrder(po);

function dueOf(po: CandidatePO, it: Record<string, unknown>): number {
  const ordered = num(it.ordered_qty ?? it.quantity ?? it.qty);
  const received = num(it.received_qty ?? po.received_qty_by_product[String(it.product_id ?? '')]);
  return Math.max(0, ordered - received);
}

export function pendingUnits(po: CandidatePO): number {
  return po.items.reduce((s, it) => s + dueOf(po, it), 0);
}

export function newLine(p: { product_id: string; product_name: string; sku?: string }, due: number | null): ReceiveLine {
  return {
    key: `${p.product_id}-${Math.random().toString(36).slice(2, 8)}`,
    product_id: p.product_id,
    product_name: p.product_name,
    sku: p.sku,
    due,
    arrived: due ?? 1,
    damaged: 0,
    damageReason: '',
    damageChoice: '',
    counted: false,
    shelf: '',
    batch: '',
    expiry: '',
    rejectOnly: false,
  };
}

/** The lines to count for an order. An open order lists what is still due
 *  (arrived prefilled, NOT ticked: a person counts). A fully received or
 *  cancel-closed order lists every line at 0 -- only what is extra counts. */
export function linesFromPO(po: CandidatePO): ReceiveLine[] {
  const closed = isRejectOnlyOrder(po) || po.status === 'RECEIVED';
  const cancelled = new Set(
    po.items
      .filter((it) => it.line_status === 'CANCELLED' || num(it.cancelled_qty) > 0)
      .map((it) => String(it.product_id ?? '')),
  );
  return po.items
    .filter((it) => it.product_id && (closed || dueOf(po, it) > 0))
    .map((it, i) => {
      const pid = String(it.product_id);
      const line = newLine(
        {
          product_id: pid,
          product_name: String(it.product_name ?? it.name ?? pid),
          sku: it.sku ? String(it.sku) : undefined,
        },
        closed ? 0 : dueOf(po, it),
      );
      const rejectOnly = isRejectOnlyOrder(po) || (po.status === 'RECEIVED' && cancelled.has(pid));
      return {
        ...line,
        key: `${pid}-${i}`,
        po_item_id: String(it.po_item_id ?? it.id ?? pid),
        rejectOnly,
        damageReason: rejectOnly ? 'Cancelled on the order' : '',
      };
    });
}

/** Pieces of a line that go to quarantine (never on the shelf). */
export const rejectedOf = (l: ReceiveLine) => (l.rejectOnly ? l.arrived : Math.min(l.damaged, l.arrived));
/** Pieces of a line that go into stock (or wait, if the line is held). */
export const goodOf = (l: ReceiveLine) => Math.max(0, l.arrived - rejectedOf(l));

/** "Everything arrived as ordered": every line as due, nothing damaged, ticked. */
export function tallyAll(lines: ReceiveLine[]): ReceiveLine[] {
  return lines.map((l) => ({
    ...l,
    arrived: l.due ?? l.arrived,
    damaged: l.rejectOnly ? l.damaged : 0,
    counted: true,
  }));
}

export interface LineNote {
  text: string;
  tone: 'ok' | 'warn' | 'err' | 'info';
}

/** What happens to a line, in shop words -- shown on its row. */
export function lineNotes(l: ReceiveLine): LineNote[] {
  if (l.rejectOnly) {
    return l.arrived > 0
      ? [{ text: `${l.arrived} to quarantine: cancelled on the order`, tone: 'err' }]
      : [{ text: 'Cancelled on the order', tone: 'info' }];
  }
  const out: LineNote[] = [];
  const rej = rejectedOf(l);
  if (rej > 0) out.push({ text: `${rej} damaged: to quarantine`, tone: 'err' });
  if (l.due !== null && l.arrived < l.due) {
    out.push({ text: `${l.due - l.arrived} short, stays on the order`, tone: 'warn' });
  }
  if (l.due !== null && l.arrived > l.due) {
    out.push({ text: `${l.arrived - l.due} more than ordered: held for the manager`, tone: 'warn' });
  }
  if (l.heldForCatalogue && goodOf(l) > 0) {
    out.push({ text: 'Held until catalogued', tone: 'warn' });
  }
  if (out.length === 0) out.push({ text: l.arrived > 0 ? 'All good' : 'Nothing arrived', tone: l.arrived > 0 ? 'ok' : 'info' });
  return out;
}

/** Contact-lens expiry: expired or within six months is a WARNING only. */
export function expiryWarning(expiry: string, today: string): 'expired' | 'short' | null {
  if (!expiry || !today) return null;
  if (expiry < today) return 'expired';
  const d = new Date(`${today}T00:00:00Z`);
  d.setUTCMonth(d.getUTCMonth() + 6);
  return expiry < d.toISOString().slice(0, 10) ? 'short' : null;
}

/** The line a scanned barcode / GTIN / SKU belongs to, or -1. */
export function matchScan(lines: ReceiveLine[], code: string): number {
  const c = code.trim().toLowerCase();
  if (!c) return -1;
  return lines.findIndex(
    (l) => l.product_id.toLowerCase() === c || [l.sku, ...(l.codes ?? [])].some((x) => x && x.toLowerCase() === c),
  );
}

/** What the product master tells the screen about a line. */
export function productFacts(p: Record<string, unknown> | null | undefined): Pick<ReceiveLine, 'contactLens' | 'heldForCatalogue' | 'codes'> {
  if (!p) return {};
  return {
    contactLens: /CONTACT/i.test(String(p.category ?? '')),
    heldForCatalogue: p.ordered_draft === true,
    codes: ['sku', 'barcode', 'gtin', 'storeBarcode', 'store_barcode']
      .map((k) => p[k])
      .filter((v): v is string => typeof v === 'string' && v.trim() !== ''),
  };
}

export interface ReceiveHeader {
  kind: ReceiveKind;
  hasOrder: boolean;
  vendorId: string;
  billNo: string;
  dcNumber: string;
  billFileId: string | null;
}

/** The lines a receipt sends: only what arrived (a 0 line is counted --
 *  ticked -- but carries nothing). */
export const linesToSend = (lines: ReceiveLine[]) => lines.filter((l) => l.arrived > 0);

/** The first reason this receipt cannot be sent yet, in shop words; null = ready. */
export function receiveProblem(h: ReceiveHeader, lines: ReceiveLine[], rejectOnlyOrder: boolean): string | null {
  const typed = !h.hasOrder;
  if (typed && !h.vendorId) return 'Pick the supplier the goods came from, or add the dealer';
  if (lines.length === 0) return typed ? 'Add the items that arrived' : 'This order has nothing left to receive';
  if (typed && lines.some((l) => l.arrived <= 0)) return 'Every line needs a quantity of at least 1';
  if (linesToSend(lines).length === 0) {
    return rejectOnlyOrder ? 'Enter the pieces that arrived - each one is logged as rejected' : 'Enter what arrived';
  }
  const unticked = lines.filter((l) => !l.counted).length;
  if (unticked > 0) return `Tick "I counted this" on every line (${unticked} still unticked)`;
  const sent = linesToSend(lines);
  if (sent.some((l) => l.damaged > l.arrived)) return 'A line cannot have more damaged pieces than arrived';
  if (sent.some((l) => rejectedOf(l) > 0 && !l.damageReason.trim())) {
    return "Say what is wrong with each damaged piece - the supplier's debit note needs it";
  }
  if (sent.some((l) => rejectedOf(l) > 0 && !l.damageChoice)) {
    return 'Choose "replacement coming" or "credit" for each damaged piece';
  }
  if (h.kind === 'DELIVERY_CHALLAN' && !h.dcNumber.trim()) return 'Enter the challan number';
  if (h.kind === 'STANDARD' && !h.billNo.trim()) return 'Enter the vendor bill number';
  if (h.kind !== 'DELIVERY_CHALLAN' && !h.billFileId) return 'Attach the bill photo or PDF - no paper, no stock';
  return null;
}

export interface GrnItemPayload {
  po_item_id?: string;
  product_id: string;
  received_qty: number;
  accepted_qty: number;
  rejected_qty: number;
  rejection_reason?: string;
  rejection_disposition?: 'credit_note' | 'replacement';
  tallied: boolean;
  location_code?: string;
  batch_code?: string;
  expiry_date?: string;
  unit_price?: number;
}

export function grnItems(kind: ReceiveKind, lines: ReceiveLine[], canEnterPrice: boolean): GrnItemPayload[] {
  return linesToSend(lines).map((l) => {
    const rejected = rejectedOf(l);
    return {
      po_item_id: l.po_item_id || undefined,
      product_id: l.product_id,
      received_qty: l.arrived,
      accepted_qty: l.arrived - rejected,
      rejected_qty: rejected,
      rejection_reason: rejected > 0 ? l.damageReason.trim() : undefined,
      rejection_disposition: rejected > 0 && l.damageChoice ? l.damageChoice : undefined,
      tallied: l.counted,
      location_code: l.shelf.trim().toUpperCase() || undefined,
      batch_code: l.batch.trim() || undefined,
      expiry_date: l.expiry || undefined,
      unit_price: kind === 'NO_PO' && canEnterPrice ? l.unitPrice : undefined,
    };
  });
}

/** A clean box against an open order goes through POST /grn/express, which
 *  also drafts the bill and runs the 3-way match for accounts. Anything else
 *  -- a damaged piece, a piece beyond the order, a line held for the
 *  catalogue, an extra or rejected delivery, a challan, no order -- is
 *  created and accepted in two calls (express would refuse or half-finish it). */
export function goesExpress(kind: ReceiveKind, po: CandidatePO | null, lines: ReceiveLine[]): boolean {
  if (kind !== 'STANDARD' || !po || po.status === 'RECEIVED' || isRejectOnlyOrder(po)) return false;
  return linesToSend(lines).every(
    (l) => !l.rejectOnly && rejectedOf(l) === 0 && l.due !== null && l.arrived <= l.due && !l.heldForCatalogue,
  );
}
