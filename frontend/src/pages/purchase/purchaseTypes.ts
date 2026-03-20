// ============================================================================
// IMS 2.0 - Purchase Management Types
// ============================================================================

export type TabType = 'purchase-orders' | 'suppliers' | 'vendor-returns' | 'analytics';
export type POStatus = 'DRAFT' | 'PENDING' | 'APPROVED' | 'ORDERED' | 'RECEIVED' | 'CANCELLED';

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
}
