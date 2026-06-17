// ============================================================================
// IMS 2.0 - POS Store (Zustand with Persistence)
// ============================================================================
// Manages the entire POS transaction lifecycle:
//   Customer → Prescription → Products → Discount → Payment → Order
// Persists draft cart to localStorage so sales aren't lost on browser crash.

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type { Customer, Patient, Prescription } from '../types';
import { resolveGstRate, isInclusivePricing } from '../constants/gstRuntime';

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
  method: 'CASH' | 'UPI' | 'CARD' | 'BANK_TRANSFER' | 'EMI' | 'CREDIT' | 'VOUCHER' | 'GIFT_VOUCHER' | 'LOYALTY';
  amount: number;
  reference?: string;            // UPI ref, card last 4, voucher code, etc.
  timestamp: string;
  voucherCode?: string;
  voucherAmount?: number;
  
  // EMI-specific fields
  emiProvider?: string;          // HDFC, ICICI, Axis, ADITYA BIRLA, etc.
  emiTenure?: number;            // Months: 3, 6, 9, 12, 18, 24
  downPayment?: number;          // Down payment amount in rupees
  emiBalance?: number;           // POS-2: financed balance (order_total - downPayment)
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
  // Incentive — Visufit measurement ID captured at Review for optical carts
  visufit_id: string;

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
  // POS-3: deferred loyalty redemption intent. Points are NOT debited until
  // the order is confirmed. The intent is stored here so the payment panel
  // can show the LOYALTY line; actual /loyalty/redeem call happens in
  // POSLayout after createOrder succeeds.
  pendingLoyaltyRedeem: { points: number; rupeeValue: number; orderValue: number } | null;

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
  setVisufitId: (id: string) => void;

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
  // POS-3: store a deferred redemption intent without debiting points yet.
  setPendingLoyaltyRedeem: (intent: { points: number; rupeeValue: number; orderValue: number } | null) => void;
  clearPendingLoyaltyRedeem: () => void;
  // Reset
  resetTransaction: () => void;
  clearAllOnLogout: () => void;
  // Park/Hold: atomically REPLACE the current transaction with a previously
  // held one (no merge into the existing cart, per-item discounts preserved,
  // cart-level discount + delivery fields restored).
  restoreHeldSale: (snapshot: any) => void;

  // Computed getters
  getSubtotal: () => number;
  getTotalDiscount: () => number;
  getGrandTotal: () => number;
  getTax: () => number;
  getTaxableValue: () => number;
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

// Re-derives `cart_discount_amount` from the post-item-discount subtotal
// and the current cart-level discount percent. Used by every cart
// mutator so the field stays in sync without doing a `set()` inside the
// `getGrandTotal` selector (which is read during render and would crash
// React 18+/zustand 5 with "Cannot update a component while rendering").
function recalcCartDiscountAmount(cart: CartLineItem[], percent: number): number {
  if (!percent) return 0;
  const subtotalAfterItem = (cart || []).reduce(
    (s, i) => s + (i.line_total || 0),
    0,
  );
  return Math.round(subtotalAfterItem * (percent / 100) * 100) / 100;
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
  visufit_id: '',
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
  pendingLoyaltyRedeem: null,
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
      setVisufitId: (id) => set({ visufit_id: id }),

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
        set((state) => {
          const newCart = [...(state.cart || []), newItem];
          return {
            cart: newCart,
            cart_discount_amount: recalcCartDiscountAmount(newCart, state.cart_discount_percent || 0),
          };
        });
      },

      removeFromCart: (lineId: string) => {
        set((state: POSState) => {
          const newCart = (state.cart || []).filter((item: CartLineItem) => item.id !== lineId);
          return {
            cart: newCart,
            cart_discount_amount: recalcCartDiscountAmount(newCart, state.cart_discount_percent || 0),
          };
        });
      },

      updateQuantity: (lineId: string, qty: number) => {
        if (qty <= 0) {
          get().removeFromCart(lineId);
          return;
        }
        set((state: POSState) => {
          const newCart = (state.cart || []).map((item: CartLineItem) =>
            item.id === lineId
              ? {
                  ...item,
                  quantity: qty,
                  discount_amount: item.unit_price * qty * (item.discount_percent / 100),
                  line_total: calcLineTotal({ ...item, quantity: qty }),
                }
              : item
          );
          return {
            cart: newCart,
            cart_discount_amount: recalcCartDiscountAmount(newCart, state.cart_discount_percent || 0),
          };
        });
      },

      applyDiscount: (lineId: string, percent: number, reason?: string, approvedBy?: string) => {
        set((state: POSState) => {
          const newCart = (state.cart || []).map((item: CartLineItem) => {
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
          });
          return {
            cart: newCart,
            cart_discount_amount: recalcCartDiscountAmount(newCart, state.cart_discount_percent || 0),
          };
        });
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
        set((state: POSState) => {
          const removed = (state.payments || [])[index];
          // POS-3: if cashier removes the LOYALTY tender line, also discard
          // the pending intent so no redeem call fires on order create.
          const clearLoyalty = removed?.method === 'LOYALTY';
          return {
            payments: (state.payments || []).filter((_: PaymentEntry, i: number) => i !== index),
            ...(clearLoyalty ? { pendingLoyaltyRedeem: null } : {}),
          };
        });
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

      removeVoucher: () => set(state => ({
        appliedVoucher: undefined,
        payments: (state.payments || []).filter(p => p.method !== 'GIFT_VOUCHER'),
      })),

      redeemLoyaltyPoints: (pointsToRedeem: number) => set({
        loyaltyPointsRedeemed: pointsToRedeem
      }),

      // POS-3: deferred loyalty redemption (points debited AFTER order create)
      setPendingLoyaltyRedeem: (intent) => set({ pendingLoyaltyRedeem: intent }),
      clearPendingLoyaltyRedeem: () => set({ pendingLoyaltyRedeem: null }),

      // --- Reset ---
      resetTransaction: () => {
        const { store_id, salesperson_id, salesperson_name } = get();
        set({ ...initialState, store_id, salesperson_id, salesperson_name });
      },

      // --- Park/Hold: recall a held sale, REPLACING the current one ---
      // The previous recall path re-added each line via addToCart (which merges
      // into whatever was already in the cart -> two customers' sales could
      // fuse) and dropped the cart-level discount + delivery fields entirely.
      // This sets everything in ONE atomic write: cart verbatim (per-item
      // discount_amount/approved_by/reason intact), cart-discount recomputed
      // from the restored cart, delivery + advance flags restored.
      restoreHeldSale: (snapshot: any) => {
        const { store_id, salesperson_id, salesperson_name } = get();
        const snap = snapshot || {};
        const cart: CartLineItem[] = Array.isArray(snap.cart) ? snap.cart : [];
        const percent = snap.cart_discount_percent || 0;
        set({
          ...initialState,
          store_id,
          salesperson_id,
          salesperson_name,
          sale_type: snap.sale_type || initialState.sale_type,
          customer: snap.customer ?? null,
          patient: snap.patient ?? null,
          prescription: snap.prescription ?? null,
          cart,
          cart_note: snap.cart_note || '',
          payments: Array.isArray(snap.payments) ? snap.payments : [],
          is_advance_payment: !!snap.is_advance_payment,
          delivery_date: snap.delivery_date ?? null,
          delivery_time_slot: snap.delivery_time_slot ?? null,
          delivery_priority: snap.delivery_priority || 'NORMAL',
          cart_discount_percent: percent,
          cart_discount_amount: recalcCartDiscountAmount(cart, percent),
          cart_discount_reason: snap.cart_discount_reason ?? null,
          cart_discount_approved_by: snap.cart_discount_approved_by ?? null,
          current_step: cart.length > 0 ? 'review' : 'customer',
        });
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
        // GST_PRICING_MODE (read at runtime from /health, see gstRuntime):
        //   INCLUSIVE (default, owner decision / QA F3): the counter price IS
        //     the all-in price; total = sum of inclusive line totals (GST is
        //     WITHIN, see getTax). A Rs 999 frame rings up Rs 999.
        //   EXCLUSIVE (legacy): GST is added on top -> total = line + GST.
        // The backend recomputes authoritatively on order-create; this mirrors
        // the same mode for the live preview so FE+BE agree during a flag flip.
        const state = get();
        const cart = state.cart || [];
        const cartDiscountFactor = 1 - (state.cart_discount_percent || 0) / 100;  // 1.0 when 0
        const inclusive = isInclusivePricing();

        let total = 0;
        for (const item of cart) {
          const lineGross = Math.round((item.line_total || 0) * cartDiscountFactor * 100) / 100;
          if (inclusive) {
            total += lineGross;
          } else {
            const rate = resolveGstRate(item.category, (item as any).hsn_code || (item as any).hsnCode);
            total += lineGross + lineGross * (rate / 100);
          }
        }
        // NOTE: never `set()` inside a getter (illegal during React render).
        return Math.round(total * 100) / 100;
      },

      getTax: () => {
        // GST component, mode-aware (summed per line, rounded once):
        //   INCLUSIVE -> extracted from WITHIN: gross - gross/(1 + rate/100).
        //   EXCLUSIVE -> added on top: gross * rate.
        const state = get();
        const cart = state.cart || [];
        const cartDiscountFactor = 1 - (state.cart_discount_percent || 0) / 100;
        const inclusive = isInclusivePricing();

        let tax = 0;
        for (const item of cart) {
          const lineGross = Math.round((item.line_total || 0) * cartDiscountFactor * 100) / 100;
          const gstRate = resolveGstRate(item.category, (item as any).hsn_code || (item as any).hsnCode);
          tax += inclusive
            ? lineGross - lineGross / (1 + gstRate / 100)
            : lineGross * (gstRate / 100);
        }
        return Math.round(tax * 100) / 100;
      },

      getTaxableValue: () => {
        // Pre-tax taxable base = grand - tax. Correct in BOTH modes:
        // inclusive -> gross/(1+rate); exclusive -> the line total (gross).
        return Math.round((get().getGrandTotal() - get().getTax()) * 100) / 100;
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
      // SEC-4: Only persist cart-critical fields. Strip customer PII and
      // prescription data so a shared POS terminal never leaves a prior
      // customer's personal data (name, DOB, mobile, Rx values) readable
      // via DevTools -> Application -> Local Storage. The cart (products +
      // discounts), payments, and delivery details are safe to keep because
      // they contain no identity data and the cashier needs them on refresh.
      partialize: (state: POSState) => ({
        ...state,
        // Strip PII / sensitive clinical data
        customer: null,
        patient: null,
        prescription: null,
        customerLoyaltyPoints: 0,
        customerLastOrder: undefined,
        customerLastRx: undefined,
        appliedVoucher: undefined,
        loyaltyPointsRedeemed: 0,
        pendingLoyaltyRedeem: null,
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
