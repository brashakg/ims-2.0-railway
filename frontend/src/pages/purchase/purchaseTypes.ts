// ============================================================================
// IMS 2.0 - Purchase Management Types
// ============================================================================

export type TabType = 'purchase-orders' | 'purchase-invoices' | 'variance' | 'suppliers' | 'vendor-returns' | 'analytics';
export type POStatus =
  | 'DRAFT'
  | 'PENDING'
  | 'APPROVED'
  | 'SENT'
  | 'ACKNOWLEDGED'
  | 'ORDERED'
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
  approvedBy?: string;
  receivedDate?: string;
  notes?: string;
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

/** PO statuses the Goods-Receipt cockpit can receive against (mirrors the
 *  backend _RECEIVABLE_PO_STATUSES tuple in vendors.py). */
export const RECEIVABLE_PO_STATUSES: readonly POStatus[] = [
  'SENT',
  'ACKNOWLEDGED',
  'PARTIAL',
  'PARTIALLY_RECEIVED',
];
