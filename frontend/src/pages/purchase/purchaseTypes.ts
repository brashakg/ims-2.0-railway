// ============================================================================
// IMS 2.0 - Purchase Management Types
// ============================================================================

import type { UserRole } from '../../types';

export type TabType = 'purchase-orders' | 'purchase-invoices' | 'variance' | 'suppliers' | 'vendor-returns' | 'analytics';
// The statuses the server actually writes. There is NO approval step (owner
// ruling 2026-09-28): a DRAFT is sent straight to the vendor, so the old
// PENDING / APPROVED / ORDERED words are gone -- they only ever existed on
// screen. PARTIAL is the legacy spelling of PARTIALLY_RECEIVED.
export type POStatus =
  | 'DRAFT'
  | 'SENT'
  | 'ACKNOWLEDGED'
  | 'PARTIAL'
  | 'PARTIALLY_RECEIVED'
  | 'RECEIVED'
  | 'CANCELLED';

export interface Supplier {
  id: string;
  name: string;
  code: string;
  contactPerson: string;
  phone: string;
  email: string;
  address: string;
  city: string;
  state: string;
  /** 2-digit GST state code. Derived from the GSTIN by the backend. */
  stateCode?: string;
  gstNumber: string;
  paymentTerms: number; // days
  creditLimit: number;
  currentOutstanding: number;
  rating: number; // 1-5
  totalPurchases: number;
  lastPurchaseDate: string;
  performance: {
    onTimeDelivery: number; // percentage
    qualityScore: number; // percentage
    priceCompetitiveness: number; // percentage
  };
}

export interface PurchaseOrder {
  id: string;
  poNumber: string;
  supplierId: string;
  supplierName: string;
  date: string;
  expectedDelivery: string;
  status: POStatus;
  items: POItem[];
  subtotal: number;
  taxAmount: number;
  total: number;
  /** How the tax on this order actually splits, as the SERVER stored it
   *  (purchase_orders.gst_summary). A purchase inside the state is CGST + SGST,
   *  half each; across states it is one IGST charge. Same money either way --
   *  what changes is the return it is filed in, so a saved order has to show
   *  which one it is, not a single "Tax" line. Absent on orders raised before
   *  the split was stored. */
  gstSummary?: { cgst: number; sgst: number; igst: number; tax: number };
  /** true = IGST, false = CGST + SGST, undefined = the order predates the
   *  split (or the server could not tell). */
  interstate?: boolean;
  receivedDate?: string;
  notes?: string;
  /** Why the order was cancelled (the timeline shows who and when). */
  cancellationReason?: string;
  /** Set on drafts generated automatically (lens top-up / forecast): their
   *  lines carry data the edit form cannot hold, so they are not editable. */
  source?: string;
}

export interface POItem {
  productId: string;
  productName: string;
  sku: string;
  quantity: number;
  unitCost: number;
  taxRate: number;
  total: number;
  /** Units received so far (per-line received_qty, falling back to the PO
   *  header received_qty_by_product for pre-S1 POs). Drives the "N of M
   *  lines received" progress chip on the PO list. */
  receivedQty?: number;
  /** Units withdrawn from this line by a cancel (never received stock). */
  cancelledQty?: number;
  /** Server line status: OPEN / PARTIAL / RECEIVED / CANCELLED. */
  lineStatus?: string;
}

// An audit stamp names a PERSON. The backend resolves the raw user id it
// stores ("user-superadmin") into a display name and returns it beside the id
// as <field>_name; that name is ABSENT when the id no longer matches a user.
// So: show the name, fall back to the id (still traceable), and when nobody was
// stamped at all say nothing rather than inventing anyone.
export function byPerson(name?: string | null, id?: string | null): string {
  const who = name || id;
  return who ? ` by ${who}` : '';
}

/** The managers who send orders to vendors and receive goods into stock
 *  (owner ruling 2026-09-28: receiving stays with them; workshop staff hand the
 *  box to one of them, the catalogue manager only raises drafts). ONE list for
 *  the purchase section pages, the receive routes, every Receive button, the
 *  blocked page that names them and the Buy Desk's "who sends it" hint;
 *  mirrors the backend _VENDOR_ROLES gate (+ SUPERADMIN), which guards ordering
 *  and receiving alike. Narrowing only who RECEIVES needs its own list on both
 *  sides -- shrinking this one would also lock those roles out of orders and
 *  invoices. */
export const PURCHASE_MANAGER_ROLES: readonly UserRole[] = [
  'SUPERADMIN',
  'ADMIN',
  'AREA_MANAGER',
  'STORE_MANAGER',
  'ACCOUNTANT',
];

/** PO statuses the Goods-Receipt cockpit can receive against (mirrors the
 *  backend _RECEIVABLE_PO_STATUSES tuple in vendors.py). */
export const RECEIVABLE_PO_STATUSES: readonly POStatus[] = [
  'SENT',
  'ACKNOWLEDGED',
  'PARTIAL',
  'PARTIALLY_RECEIVED',
];
