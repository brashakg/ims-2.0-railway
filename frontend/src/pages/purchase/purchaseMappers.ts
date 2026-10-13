// ============================================================================
// IMS 2.0 - Purchase: backend doc -> frontend shape mappers
// ============================================================================
// Moved verbatim from PurchaseManagementPage.tsx when the tab container was
// split into per-URL section pages (Wave 1). Shared by the Orders, Invoices,
// Suppliers and Analytics sections.

import { gstinStateCode } from '../../constants/gst';
import type { POStatus, Supplier, PurchaseOrder } from './purchaseTypes';

// ============================================================================
// Field mapping: backend vendor doc -> frontend Supplier shape
// ============================================================================
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function mapVendorToSupplier(v: any): Supplier {
  return {
    id: v.vendor_id ?? v._id ?? '',
    name: v.trade_name ?? v.legal_name ?? '',
    code: v.vendor_code ?? v.vendor_id?.slice(0, 8).toUpperCase() ?? '',
    contactPerson: v.contact_person ?? '',
    phone: v.mobile ?? v.phone ?? '',
    email: v.email ?? '',
    address: v.address ?? '',
    city: v.city ?? '',
    state: v.state ?? '',
    // Vendors created before the state was derived from the GSTIN have no
    // state_code stored; read it off the GSTIN so their cards still classify.
    stateCode: v.state_code || gstinStateCode(v.gstin) || undefined,
    gstNumber: v.gstin ?? '',
    paymentTerms: v.credit_days ?? 30,
    creditLimit: v.credit_limit ?? 0,
    currentOutstanding: v.current_outstanding ?? 0,
    rating: v.rating ?? 0,
    totalPurchases: v.total_purchases ?? 0,
    lastPurchaseDate: v.last_purchase_date ?? '',
    performance: {
      onTimeDelivery: v.on_time_delivery ?? 0,
      qualityScore: v.quality_score ?? 0,
      priceCompetitiveness: v.price_competitiveness ?? 0,
    },
  };
}

// ============================================================================
// Field mapping: backend purchase_order doc -> frontend PurchaseOrder shape
// ============================================================================
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function mapPOtoPurchaseOrder(po: any): PurchaseOrder {
  // Per-product header fallback for POs created before the per-line
  // received_qty field (S1) existed.
  const headerReceived: Record<string, number> = po.received_qty_by_product ?? {};
  // A line with no stored rate: where does its displayed rate come from?
  //
  // NOT a flat 18 (over-taxes every 5% frame, lens and contact lens on the
  // page) and NOT a flat 0 either -- 0 prints a line total that does not add up
  // to the header Tax and Total on the same screen. Rate-less lines were only
  // ever written by the two automatic PO doors, and those wrote EVERY line of
  // an order rate-less under a header tax of exactly subtotal x one rate. When
  // that is the shape in front of us, the rate is read off the order's own
  // arithmetic -- not guessed. Any other shape (a modern order, or a mixed one
  // no door writes) leaves the rate-less line at 0 rather than handing it a
  // blended number that is nobody's actual rate.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const rawItems: any[] = po.items ?? [];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const noneRated = rawItems.length > 0 && rawItems.every((i: any) => i.tax_rate == null);
  const impliedRate =
    noneRated && (po.subtotal ?? 0) > 0
      ? Math.round(((po.tax_amount ?? 0) / po.subtotal) * 1000) / 10
      : 0;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const items = rawItems.map((item: any) => {
    // A line nobody priced (None from the server, 0.0 from a CL / forecast
    // draft -- the server's is_unpriced_line) stays null: shown as needing a
    // price, never as a real 0 (owner ruling 2026-10-12).
    const unitCost: number | null = item.unit_price || item.unit_cost || null;
    return {
      productId: item.product_id ?? '',
      productName: item.product_name ?? '',
      sku: item.sku ?? '',
      quantity: item.ordered_qty ?? item.quantity ?? 0,
      unitCost,
      taxRate: item.tax_rate ?? impliedRate,
      total:
        unitCost == null
          ? null
          : item.total ?? (item.quantity ?? 0) * unitCost * (1 + (item.tax_rate ?? impliedRate) / 100),
      receivedQty: item.received_qty ?? headerReceived[item.product_id ?? ''] ?? 0,
    };
  });

  return {
    id: po.po_id ?? po._id ?? '',
    poNumber: po.po_number ?? '',
    supplierId: po.vendor_id ?? '',
    supplierName: po.vendor_name ?? '',
    date: po.created_at ? po.created_at.split('T')[0] : '',
    expectedDelivery: po.expected_date ?? '',
    status: (po.status ?? 'DRAFT') as POStatus,
    items,
    subtotal: po.subtotal ?? 0,
    taxAmount: po.tax_amount ?? 0,
    total: po.total_amount ?? po.total ?? 0,
    // Read the split back off the stored order instead of re-deriving it: the
    // server decided CGST+SGST vs IGST from the two GST numbers, and its answer
    // is the one that gets filed.
    gstSummary: po.gst_summary ?? undefined,
    interstate: typeof po.interstate === 'boolean' ? po.interstate : undefined,
    approvedBy: po.approved_by,
    receivedDate: po.received_date ?? po.received_at?.split('T')[0],
    notes: po.notes,
  };
}

/** What a PO line's price reads as: the rupees, or that accounts still have
 *  to agree it (a line nobody priced -- never shown as a real 0). */
export const PRICE_NEEDED = 'Price needed';
export function poMoney(value: number | null): string {
  return value == null ? PRICE_NEEDED : `₹${value.toLocaleString()}`;
}
