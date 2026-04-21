// ============================================================================
// IMS 2.0 - POS Store (Zustand with Persistence)
// ============================================================================
// Manages the entire POS transaction lifecycle:
//   Customer → Prescription → Products → Discount → Payment → Order
// Persists draft cart to localStorage so sales aren't lost on browser crash.

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type { Customer, Patient, Prescription } from '../types';
import { getGSTRateByCategory } from '../constants/gst';

// ============================================================================
// Debounced storage — prevents localStorage writes from blocking UI (fixes INP)
// Writes are batched and delayed 500ms. Reads are instant.
// ============================================================================

const debouncedLocalStorage = {
  getItem: (name: string): string | null => {
    return localStorage.getItem(name);
  },
  setItem: (() => {
    let timeout: ReturnType<typeof setTimeout> | null = null;
    return (name: string, value: string) => {
      if (timeout) clearTimeout(timeout);
      timeout = setTimeout(() => {
        try { localStorage.setItem(name, value); } catch { /* quota exceeded */ }
      }, 500);
    };
  })(),
  removeItem: (name: string) => {
    localStorage.removeItem(name);
  },
};

// ============================================================================
// Types
// ============================================================================

export type SaleType = 'quick_sale' | 'prescription_order';

export type POSStep =
  | 'customer'      // Step 1: Select/create customer
  | 'prescription'  // Step 2: Select/enter prescription (optical only)
  | 'products'      // Step 3: Select frame, lens, accessories
  | 'review'        // Step 4: Review cart, apply discounts
  | 'payment'       // Step 5: Collect payment (advance or full)
  | 'complete';     // Step 6: Order created, show receipt

export interface CartLineItem {
  id: string;                    // UUID for cart line (not product_id)
  product_id: string;
  name: string;
  sku: string;
  barcode?: string;
  brand?: string;
  subbrand?: string;
  category: string;
  unit_price: number;            // Selling price (Offer Price or MRP)
  mrp: number;                   // Maximum Retail Price
  offer_price?: number;          // Offer Price (if different from MRP)
  quantity: number;
  stock_available?: number;
  image_url?: string;

  // Optical linkage
  is_optical: boolean;
  linked_prescription_id?: string;
  linked_frame_id?: string;      // For lenses linked to a frame
  lens_details?: {
    type: string;                // Single Vision / Bifocal / Progressive
    material: string;            // CR-39 / Polycarbonate / Hi-Index
    index?: string;              // 1.5 / 1.56 / 1.6 / 1.67 / 1.74
    coatings: string[];          // AR / Blue-cut / Photochromic
  };

  // Discount (validated server-side)
  discount_percent: number;
  discount_amount: number;
  discount_approved_by?: string;
  discount_reason?: string;

  // Calculated
  line_total: number;            // (unit_price * quantity) - discount_amount

  // Item-level notes (PD, fitting, tint, etc.)
  notes?: string;

  // Second note field used by POSLayout's optical-detail panel (distinct
  // from `notes`, which is written by POSCart). Kept as a separate field
  // because the two UIs show/write them independently.
  item_note?: string;
}

export interface PaymentEntry {
  method: 'CASH' | 'UPI' | 'CARD' | 'BANK_TRANSFER' | 'EMI' | 'CREDIT' | 'VOUCHER';
  amount: number;
  reference?: string;            // UPI ref, card last 4, voucher code, etc.
  timestamp: string;
  voucherCode?: string;
  voucherAmount?: number;
  
  // EMI-specific fields
  emiProvider?: string;          // HDFC, ICICI, Axis, ADITYA BIRLA, etc.
  emiTenure?: number;            // Months: 3, 6, 9, 12, 18, 24
  downPayment?: number;          // Down payment amount in rupees
  monthlyEMI?: number;           // Calculated monthly EMI amount
  processingFee?: number;        // 2% of loan amount
}

export interface POSState {
  // Current transaction
  sale_type: SaleType;
  current_step: POSStep;
  store_id: string;
  salesperson_id: string;
  salesperson_name: string;

  // Customer & patient
  customer: Customer | null;
  patient: Patient | null;
  prescription: Prescription | null;

  // Cart
  cart: CartLineItem[];
  cart_note: string;

  // Payments
  payments: PaymentEntry[];
  is_advance_payment: boolean;
  delivery_date: string | null;
  // Phase 6.7 — time + priority for delivery/collection.
  // time_slot uses 2-hour windows (e.g. "10:00-12:00") to match the
  // way store staff schedule pickups; priority drives queue ordering
  // in workshop + sorting on the Orders list.
  delivery_time_slot: string | null;
  delivery_priority: 'NORMAL' | 'EXPRESS' | 'URGENT';

  // Order-level discount (applied AFTER per-item discounts, on the
  // taxable subtotal). Capped at the user's role discount cap when the
  // Review step validates; caller must pass approved_by for any value
  // above a junior staff's cap. 0% by default.
  cart_discount_percent: number;
  cart_discount_amount: number;
  cart_discount_reason: string | null;
  cart_discount_approved_by: string | null;

  // Order result
  order_id: string | null;
  order_number: string | null;

  // Customer loyalty & history
  customerLoyaltyPoints: number;
  customerLastOrder?: { productName: string; monthsAgo: number };
  customerLastRx?: any[];

  // Voucher & rewards
  appliedVoucher?: { code: string; discountAmount: number };
  loyaltyPointsRedeemed: number;

  // UI state
  is_processing: boolean;

  // ========== ACTIONS ==========

  // Navigation
  setStep: (step: POSStep) => void;
  nextStep: () => void;
  prevStep: () => void;

  // Sale type
  setSaleType: (type: SaleType) => void;

  // Context
  setStoreId: (id: string) => void;
  setSalesperson: (id: string, name: string) => void;

  // Customer
  setCustomer: (customer: Customer | null) => void;
  setPatient: (patient: Patient | null) => void;
  setPrescription: (rx: Prescription | null) => void;

  // Cart
  addToCart: (item: Omit<CartLineItem, 'id' | 'line_total' | 'discount_percent' | 'discount_amount'>) => void;
  removeFromCart: (lineId: string) => void;
  updateQuantity: (lineId: string, qty: number) => void;
  applyDiscount: (lineId: string, percent: number, reason?: string, approvedBy?: string) => void;
  updateItemNote: (lineId: string, note: string) => void;
  setCartNote: (note: string) => void;
  setItemNote: (lineId: string, note: string) => void;
  linkLensToFrame: (lensLineId: string, frameLineId: string) => void;

  // Payment
  addPayment: (payment: Omit<PaymentEntry, 'timestamp'>) => void;
  removePayment: (index: number) => void;
  setAdvancePayment: (isAdvance: boolean) => void;
  setDeliveryDate: (date: string | null) => void;
  setDeliveryTimeSlot: (slot: string | null) => void;
  setDeliveryPriority: (priority: 'NORMAL' | 'EXPRESS' | 'URGENT') => void;

  // Cart-level discount — applied to subtotal after per-item discounts.
  // Passing percent=0 clears the discount.
  setCartDiscount: (percent: number, reason?: string, approvedBy?: string) => void;

  // Order
  setOrderResult: (orderId: string, orderNumber: string) => void;
  setProcessing: (val: boolean) => void;

  // --- Loyalty & Vouchers ---
  setCustomerLoyaltyPoints: (points: number) => void;
  setCustomerHistory: (lastOrder?: any, lastRx?: any) => void;
  applyVoucher: (code: string, discountAmount: number) => void;
  removeVoucher: () => void;
  redeemLoyaltyPoints: (pointsToRedeem: number) => void;
  // Reset
  resetTransaction: () => void;
  clearAllOnLogout: () => void;

  // Computed getters
  getSubtotal: () => number;
  getTotalDiscount: () => number;
  getGrandTotal: () => number;
  getTotalPaid: () => number;
  getBalance: () => number;
}

// ============================================================================
// Step Order (for next/prev)
// ============================================================================

const STEP_ORDER: POSStep[] = ['customer', 'prescription', 'products', 'review', 'payment', 'complete'];

const QUICK_SALE_STEPS: POSStep[] = ['customer', 'products', 'review', 'payment', 'complete'];

// ============================================================================
// Helpers
// ============================================================================

function calcLineTotal(item: { unit_price: number; quantity: number; discount_percent: number }): number {
  const gross = item.unit_price * item.quantity;
  const discountAmt = gross * (item.discount_percent / 100);
  return Math.round((gross - discountAmt) * 100) / 100;
}

function generateLineId(): string {
  return `line-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

// ============================================================================
// Initial State
// ============================================================================

const initialState = {
  sale_type: 'quick_sale' as SaleType,
  current_step: 'customer' as POSStep,
  store_id: '',
  salesperson_id: '',
  salesperson_name: '',
  customer: null,
  patient: null,
  prescription: null,
  cart: [] as CartLineItem[],
  cart_note: '',
  payments: [] as PaymentEntry[],
  is_advance_payment: false,
  delivery_date: null,
  delivery_time_slot: null,
  delivery_priority: 'NORMAL' as 'NORMAL' | 'EXPRESS' | 'URGENT',
  cart_discount_percent: 0,
  cart_discount_amount: 0,
  cart_discount_reason: null,
  cart_discount_approved_by: null,
  order_id: null,
  order_number: null,
  is_processing: false,
  customerLoyaltyPoints: 0,
  customerLastOrder: undefined,
  customerLastRx: undefined,
  appliedVoucher: undefined,
  loyaltyPointsRedeemed: 0,
};

// ============================================================================
// Store
// ============================================================================

export const usePOSStore = create<POSState>()(
  persist(
    (set, get) => ({
      ...initialState,

      // --- Navigation ---
      setStep: (step: POSStep) => set({ current_step: step }),

      nextStep: () => {
        const steps = get().sale_type === 'quick_sale' ? QUICK_SALE_STEPS : STEP_ORDER;
        const idx = steps.indexOf(get().current_step);
        if (idx < steps.length - 1) {
          set({ current_step: steps[idx + 1] });
        }
      },

      prevStep: () => {
        const steps = get().sale_type === 'quick_sale' ? QUICK_SALE_STEPS : STEP_ORDER;
        const idx = steps.indexOf(get().current_step);
        if (idx > 0) {
          set({ current_step: steps[idx - 1] });
        }
      },

      // --- Sale Type ---
      setSaleType: (type) => set({ sale_type: type }),

      // --- Context ---
      setStoreId: (id) => set({ store_id: id }),
      setSalesperson: (id, name) => set({ salesperson_id: id, salesperson_name: name }),

      // --- Customer ---
      setCustomer: (customer) => set({ customer, patient: null, prescription: null }),
      setPatient: (patient) => set({ patient }),
      setPrescription: (rx) => set({ prescription: rx }),

      // --- Cart ---
      addToCart: (item) => {
        const lineId = generateLineId();
        const newItem: CartLineItem = {
          ...item,
          id: lineId,
          discount_percent: 0,
          discount_amount: 0,
          line_total: item.unit_price * item.quantity,
        };
        set((state) => ({ cart: [...(state.cart || []), newItem] }));
      },

      removeFromCart: (lineId: string) => {
        set((state: POSState) => ({
          cart: (state.cart || []).filter((item: CartLineItem) => item.id !== lineId),
        }));
      },

      updateQuantity: (lineId: string, qty: number) => {
        if (qty <= 0) {
          get().removeFromCart(lineId);
          return;
        }
        set((state: POSState) => ({
          cart: (state.cart || []).map((item: CartLineItem) =>
            item.id === lineId
              ? {
                  ...item,
                  quantity: qty,
                  discount_amount: item.unit_price * qty * (item.discount_percent / 100),
                  line_total: calcLineTotal({ ...item, quantity: qty }),
                }
              : item
          ),
        }));
      },

      applyDiscount: (lineId: string, percent: number, reason?: string, approvedBy?: string) => {
        set((state: POSState) => ({
          cart: (state.cart || []).map((item: CartLineItem) => {
            if (item.id !== lineId) return item;
            const discountAmt = item.unit_price * item.quantity * (percent / 100);
            return {
              ...item,
              discount_percent: percent,
              discount_amount: Math.round(discountAmt * 100) / 100,
              discount_reason: reason,
              discount_approved_by: approvedBy,
              line_total: calcLineTotal({ ...item, discount_percent: percent }),
            };
          }),
        }));
      },

      updateItemNote: (lineId: string, note: string) => {
        set((state: POSState) => ({
          cart: (state.cart || []).map((item: CartLineItem) =>
            item.id === lineId ? { ...item, notes: note } : item
          ),
        }));
      },

      setCartNote: (note: string) => set({ cart_note: note }),
      setItemNote: (lineId: string, note: string) => set((state: POSState) => ({
        cart: (state.cart || []).map((item: CartLineItem) =>
          item.id === lineId ? { ...item, item_note: note } : item
        ),
      })),

      linkLensToFrame: (lensLineId: string, frameLineId: string) => {
        set((state: POSState) => ({
          cart: (state.cart || []).map((item: CartLineItem) =>
            item.id === lensLineId
              ? { ...item, linked_frame_id: frameLineId }
              : item
          ),
        }));
      },

      // --- Payment ---
      addPayment: (payment: Omit<PaymentEntry, 'timestamp'>) => {
        set((state: POSState) => ({
          payments: [
            ...(state.payments || []),
            { ...payment, timestamp: new Date().toISOString() },
          ],
        }));
      },

      removePayment: (index: number) => {
        set((state: POSState) => ({
          payments: (state.payments || []).filter((_: PaymentEntry, i: number) => i !== index),
        }));
      },

      setAdvancePayment: (isAdvance: boolean) => set({ is_advance_payment: isAdvance }),
      setDeliveryDate: (date: string | null) => set({ delivery_date: date }),
      setDeliveryTimeSlot: (slot: string | null) => set({ delivery_time_slot: slot }),
      setDeliveryPriority: (priority: 'NORMAL' | 'EXPRESS' | 'URGENT') => set({ delivery_priority: priority }),

      setCartDiscount: (percent: number, reason?: string, approvedBy?: string) => {
        // Recompute cart_discount_amount from the current taxable subtotal
        // (post item-discount) so it re-evaluates when items change.
        set((state: POSState) => {
          const clamped = Math.max(0, Math.min(100, percent));
          const subtotalAfterItemDiscounts = (state.cart || []).reduce(
            (sum: number, item: CartLineItem) => sum + (item.line_total || 0),
            0
          );
          const amt = Math.round(subtotalAfterItemDiscounts * (clamped / 100) * 100) / 100;
          return {
            cart_discount_percent: clamped,
            cart_discount_amount: amt,
            cart_discount_reason: reason ?? null,
            cart_discount_approved_by: approvedBy ?? null,
          };
        });
      },

      // --- Order ---
      setOrderResult: (orderId: string, orderNumber: string) => set({ order_id: orderId, order_number: orderNumber }),
      setProcessing: (val: boolean) => set({ is_processing: val }),

      // --- Loyalty & Vouchers ---
      setCustomerLoyaltyPoints: (points: number) => set({ customerLoyaltyPoints: points }),

      setCustomerHistory: (lastOrder?: any, lastRx?: any) => set({
        customerLastOrder: lastOrder,
        customerLastRx: lastRx
      }),

      applyVoucher: (code: string, discountAmount: number) => set({
        appliedVoucher: { code, discountAmount }
      }),

      removeVoucher: () => set({ appliedVoucher: undefined }),

      redeemLoyaltyPoints: (pointsToRedeem: number) => set({
        loyaltyPointsRedeemed: pointsToRedeem
      }),

      // --- Reset ---
      resetTransaction: () => {
        const { store_id, salesperson_id, salesperson_name } = get();
        set({ ...initialState, store_id, salesperson_id, salesperson_name });
      },

      // --- Full clear (for logout) ---
      clearAllOnLogout: () => {
        set(initialState);
        try {
          localStorage.removeItem('ims-pos-draft');
          localStorage.removeItem('ims-held-bills');
        } catch { /* ignore */ }
      },

      // --- Computed (with null guards) ---
      getSubtotal: () => {
        return (get().cart || []).reduce(
          (sum: number, item: CartLineItem) => sum + item.unit_price * item.quantity,
          0
        );
      },

      getTotalDiscount: () => {
        // Sum of per-item discounts + cart-level discount. Drives the
        // "Total Discount" line on the Review step + GST invoice.
        const state = get();
        const itemDiscount = (state.cart || []).reduce(
          (sum: number, item: CartLineItem) => sum + (item.discount_amount || 0),
          0
        );
        return Math.round((itemDiscount + (state.cart_discount_amount || 0)) * 100) / 100;
      },

      getGrandTotal: () => {
        // Flow: raw_line_total → per-item discount → per-item taxable
        //   → apply cart-level discount proportionally → GST per category
        //   → sum.
        // GST is calculated on the POST-cart-discount taxable value so
        // the invoice math stays legally consistent (GST on the actual
        // amount charged).
        const state = get();
        const cart = state.cart || [];
        const subtotalAfterItem = cart.reduce(
          (sum: number, item: CartLineItem) => sum + (item.line_total || 0),
          0
        );
        const cartDiscountPct = state.cart_discount_percent || 0;
        const cartDiscountFactor = 1 - cartDiscountPct / 100;  // 1.0 when 0

        let taxable = 0;
        let totalTax = 0;
        for (const item of cart) {
          const lineTotal = item.line_total || 0;
          const itemTaxable = Math.round(lineTotal * cartDiscountFactor * 100) / 100;
          taxable += itemTaxable;
          const gstRate = getGSTRateByCategory(item.category);
          totalTax += itemTaxable * (gstRate / 100);
        }
        // Side-effect: keep cart_discount_amount in sync with the current
        // subtotal so "Total Discount" on the Review card reflects reality
        // even after the user edits cart quantities.
        const syncedCartDiscountAmt = Math.round(subtotalAfterItem * (cartDiscountPct / 100) * 100) / 100;
        if (Math.abs(syncedCartDiscountAmt - (state.cart_discount_amount || 0)) > 0.01) {
          set({ cart_discount_amount: syncedCartDiscountAmt });
        }
        return Math.round((taxable + totalTax) * 100) / 100;
      },

      getTotalPaid: () => {
        return (get().payments || []).reduce(
          (sum: number, p: PaymentEntry) => sum + p.amount,
          0
        );
      },

      getBalance: () => {
        return get().getGrandTotal() - get().getTotalPaid();
      },
    }),
    {
      name: 'ims-pos-draft',
      storage: createJSONStorage(() => debouncedLocalStorage),
      // Only persist cart-critical fields, not UI state
      partialize: (state: POSState) => ({
        ...state,
        // Reset transient state
        is_processing: false,
        order_id: null,
        order_number: null,
      }),
      // Safe merge: ensure arrays are never undefined after hydration and
      // backfill Phase 6.7 fields if the persisted draft predates them.
      onRehydrateStorage: () => (state: POSState | undefined) => {
        if (state) {
          if (!Array.isArray(state.cart)) state.cart = [];
          if (!Array.isArray(state.payments)) state.payments = [];
          state.current_step = 'customer'; // Always start fresh on page load
          state.is_processing = false;
          state.order_id = null;
          state.order_number = null;
          // Phase 6.7 backfill — persisted drafts from earlier builds won't
          // have these keys; default them so the Review UI doesn't crash.
          if (state.delivery_time_slot === undefined) state.delivery_time_slot = null;
          if (state.delivery_priority === undefined) state.delivery_priority = 'NORMAL';
          if (state.cart_discount_percent === undefined) state.cart_discount_percent = 0;
          if (state.cart_discount_amount === undefined) state.cart_discount_amount = 0;
          if (state.cart_discount_reason === undefined) state.cart_discount_reason = null;
          if (state.cart_discount_approved_by === undefined) state.cart_discount_approved_by = null;
        }
      },
    }
  )
);
