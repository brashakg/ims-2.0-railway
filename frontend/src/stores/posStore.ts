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
}

export interface PaymentEntry {
  method: 'CASH' | 'UPI' | 'CARD' | 'BANK_TRANSFER' | 'EMI' | 'CREDIT';
  amount: number;
  reference?: string;            // UPI ref, card last 4, etc.
  timestamp: string;
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

  // Order result
  order_id: string | null;
  order_number: string | null;

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
  linkLensToFrame: (lensLineId: string, frameLineId: string) => void;

  // Payment
  addPayment: (payment: Omit<PaymentEntry, 'timestamp'>) => void;
  removePayment: (index: number) => void;
  setAdvancePayment: (isAdvance: boolean) => void;
  setDeliveryDate: (date: string | null) => void;

  // Order
  setOrderResult: (orderId: string, orderNumber: string) => void;
  setProcessing: (val: boolean) => void;

  // Reset
  resetTransaction: () => void;

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
  order_id: null,
  order_number: null,
  is_processing: false,
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
        set((state) => ({ cart: [...state.cart, newItem] }));
      },

      removeFromCart: (lineId) => {
        set((state) => ({
          cart: state.cart.filter((item) => item.id !== lineId),
        }));
      },

      updateQuantity: (lineId, qty) => {
        if (qty <= 0) {
          get().removeFromCart(lineId);
          return;
        }
        set((state) => ({
          cart: state.cart.map((item) =>
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

      applyDiscount: (lineId, percent, reason, approvedBy) => {
        set((state) => ({
          cart: state.cart.map((item) => {
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

      updateItemNote: (lineId, note) => {
        set((state) => ({
          cart: state.cart.map((item) =>
            item.id === lineId ? { ...item, notes: note } : item
          ),
        }));
      },

      setCartNote: (note) => set({ cart_note: note }),

      linkLensToFrame: (lensLineId, frameLineId) => {
        set((state) => ({
          cart: state.cart.map((item) =>
            item.id === lensLineId
              ? { ...item, linked_frame_id: frameLineId }
              : item
          ),
        }));
      },

      // --- Payment ---
      addPayment: (payment) => {
        set((state) => ({
          payments: [
            ...state.payments,
            { ...payment, timestamp: new Date().toISOString() },
          ],
        }));
      },

      removePayment: (index) => {
        set((state) => ({
          payments: state.payments.filter((_, i) => i !== index),
        }));
      },

      setAdvancePayment: (isAdvance) => set({ is_advance_payment: isAdvance }),
      setDeliveryDate: (date) => set({ delivery_date: date }),

      // --- Order ---
      setOrderResult: (orderId, orderNumber) => set({ order_id: orderId, order_number: orderNumber }),
      setProcessing: (val) => set({ is_processing: val }),

      // --- Reset ---
      resetTransaction: () => {
        const { store_id, salesperson_id, salesperson_name } = get();
        set({ ...initialState, store_id, salesperson_id, salesperson_name });
      },

      // --- Computed (with null guards) ---
      getSubtotal: () => {
        return (get().cart || []).reduce((sum, item) => sum + item.unit_price * item.quantity, 0);
      },

      getTotalDiscount: () => {
        return (get().cart || []).reduce((sum, item) => sum + (item.discount_amount || 0), 0);
      },

      getGrandTotal: () => {
        // Sum line totals (after discount) + GST per item using correct HSN-based rates
        const cart = get().cart || [];
        let subtotal = 0;
        let totalTax = 0;
        for (const item of cart) {
          const lineTotal = item.line_total || 0;
          subtotal += lineTotal;
          const gstRate = getGSTRateByCategory(item.category);
          totalTax += lineTotal * (gstRate / 100);
        }
        return Math.round((subtotal + totalTax) * 100) / 100;
      },

      getTotalPaid: () => {
        return (get().payments || []).reduce((sum, p) => sum + p.amount, 0);
      },

      getBalance: () => {
        return get().getGrandTotal() - get().getTotalPaid();
      },
    }),
    {
      name: 'ims-pos-draft',
      storage: createJSONStorage(() => debouncedLocalStorage),
      // Only persist cart-critical fields, not UI state
      partialize: (state) => ({
        ...state,
        // Reset transient state
        is_processing: false,
        order_id: null,
        order_number: null,
      }),
      // Safe merge: ensure arrays are never undefined after hydration
      onRehydrateStorage: () => (state) => {
        if (state) {
          if (!Array.isArray(state.cart)) state.cart = [];
          if (!Array.isArray(state.payments)) state.payments = [];
          state.current_step = 'customer'; // Always start fresh on page load
          state.is_processing = false;
          state.order_id = null;
          state.order_number = null;
        }
      },
    }
  )
);
