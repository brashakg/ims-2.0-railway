// ============================================================================
// IMS 2.0 - POS Layout (Wizard Orchestrator)
// ============================================================================
// 6-Step Wizard: Customer -> Prescription -> Products -> Review -> Payment -> Receipt
// Uses posStore (Zustand) for all state with localStorage persistence
// Sub-components extracted to: POSCart, POSPayment, POSReceipt, POSInvoice

import { useState, useEffect, useMemo, useCallback, useRef, startTransition } from 'react';
import {
  ShoppingCart, User, Eye, Package, CreditCard, CheckCircle,
  ChevronRight, ChevronLeft, Plus, X,
  Pause, Play, RotateCcw, AlertTriangle,
  Glasses, Watch, FileText, Zap, Sparkles,
  UserPlus, DoorOpen,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { usePOSStore } from '../../stores/posStore';
import type { SaleType, POSStep, CartLineItem } from '../../stores/posStore';
import { useProducts } from '../../hooks/usePOSQueries';
import { customerApi, orderApi, prescriptionApi, workshopApi, adminStoreApi, inventoryApi, loyaltyApi } from '../../services/api';
import type { Prescription } from '../../types';

// POS Rx auto-attach (clinic initiative C5-A): owner-gated convenience. When the
// flag is "true", the Prescription step auto-selects the customer's Rx IFF there
// is exactly one valid (non-expired) one and none is attached yet. Default OFF so
// staff keep choosing explicitly until the owner opts in — zero behaviour change
// unless VITE_POS_AUTO_ATTACH_SINGLE_RX=true is set at build time.
const POS_AUTO_ATTACH_SINGLE_RX =
  import.meta.env.VITE_POS_AUTO_ATTACH_SINGLE_RX === 'true';

// Backwards-compatible type exports (used by BillingEngine, CartPanel)
export interface CartItem {
  product_id: string;
  name: string;
  sku: string;
  brand?: string;
  unit_price: number;
  quantity: number;
  image_url?: string;
  category: string;
  stock?: number;
  is_optical?: boolean;
  discount_percent?: number;
}

export interface Customer {
  id: string;
  name: string;
  phone: string;
  email?: string;
  address?: string;
  loyalty_points?: number;
}

// Import orphaned components
import { PrescriptionForm } from './PrescriptionForm';
import { PrescriptionPanel } from './PrescriptionPanel';
import { PrescriptionSelectModal } from './PrescriptionSelectModal';
import { LensDetailsModal } from './LensDetailsModal';
import { LensFittingFormModal } from './LensFittingFormModal';
import type { LensFittingFormValue } from './LensFittingFormModal';
import { LensSuggestionPanel } from './LensSuggestionPanel';
import { DiscountModal } from './DiscountModal';
import { DayEndReport } from './DayEndReport';
import { BarcodeScanner } from './BarcodeScanner';
import { AutoSearch } from '../common/AutoSearch';
import { buildCustomerSearchHits, type CustomerSearchHit } from '../../utils/customerSearchHits';
import { choosePrimaryPatient, toPosPatient, sortMembersPrimaryFirst } from '../../utils/patientFromCustomer';
import { AddCustomerModal } from '../customers/AddCustomerModal';
import { buildCustomerCreatePayload, type CustomerFormData } from '../../utils/customerPayload';
import { CustomerCardWithLoyalty } from './CustomerCardWithLoyalty';
import { resolveGstRate, isInclusivePricing } from '../../constants/gstRuntime';
import type { PrescriptionInput } from '../../utils/lensAutoSuggest';

import { useToast } from '../../context/ToastContext';
import { walkoutsApi } from '../../services/api/walkouts';
import { WalkoutIntakeModal } from '../../pages/walkouts/WalkoutIntakeModal';

// Extracted sub-components
import { CartSidebar } from './POSCart';
import { StepPayment } from './POSPayment';
import { POSReceipt } from './POSReceipt';
import { StepComplete } from './POSInvoice';

// ============================================================================
// Constants
// ============================================================================

// ----------------------------------------------------------------------------
// Checkout-flow grouping (condensed — the ONLY flow) — PRESENTATION ONLY.
// ----------------------------------------------------------------------------
// The canonical step machine in posStore (customer/prescription/products/
// review/payment/complete) is UNCHANGED. A "flow group" is one rail entry that
// may render one or more of those canonical steps merged onto a single
// scrollable surface. `anchor` is the canonical step the store sits on while
// the group is active (so persistence, restoreHeldSale -> 'review', held-bill
// recall, and getter math all keep working). `members` lists every canonical
// step rendered inside the group (used for completion/active highlighting).
//
// The condensed flow is the sole checkout flow (the old classic/condensed
// toggle was removed): Customer -> [Prescription+Products] -> [Review+Payment].
//
// QUICK SALE PARITY: the original quick-sale flow (origin/main QUICK_STEPS) was
// customer -> products -> payment — the REVIEW step was excluded for quick
// sales. The condensed grouping honours that: a quick_sale never renders the
// review/cart-discount/delivery/notes panel (it stays available in prescription
// orders). prescription_order keeps customer -> [Rx] -> products -> review ->
// payment.
//
// `complete` is never a navigable group — it is the done state, reached only by
// finishing the order (handleCreateOrder) and exited via New Sale.
interface FlowGroup {
  key: string;
  label: string;
  sub: string;
  icon: typeof User;
  anchor: POSStep;     // canonical step the store rests on for this group
  members: POSStep[];  // canonical steps rendered within this group
}

// Condensed: 3 input groups for an Rx order; 3 for a quick sale but the merged
// final group is PAYMENT ONLY (no review panel), matching the old QUICK_STEPS.
function buildCondensedGroups(saleType: SaleType): FlowGroup[] {
  const isRx = saleType === 'prescription_order';
  return [
    { key: 'customer', label: 'Customer', sub: 'Pick customer', icon: User, anchor: 'customer', members: ['customer'] },
    isRx
      ? { key: 'products', label: 'Products & Rx', sub: 'Rx + cart', icon: Package, anchor: 'products', members: ['prescription', 'products'] }
      : { key: 'products', label: 'Products', sub: 'Cart', icon: Package, anchor: 'products', members: ['products'] },
    isRx
      ? { key: 'payment', label: 'Pay & Review', sub: 'Discount, GST & tender', icon: CreditCard, anchor: 'payment', members: ['review', 'payment'] }
      : { key: 'payment', label: 'Payment', sub: 'Split tender', icon: CreditCard, anchor: 'payment', members: ['payment'] },
  ];
}

/** Safe currency format — never crashes on null/undefined/NaN */
function fc(amount: number | undefined | null): string {
  const val = Math.round((amount || 0) * 100) / 100;
  return `\u20B9${val.toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}

function mapCategory(cat: string): string {
  const map: Record<string, string> = {
    FRAMES: 'FRAME', FRAME: 'FRAME', SUNGLASSES: 'SUNGLASS', SUNGLASS: 'SUNGLASS',
    RX_LENSES: 'LENS', OPTICAL_LENS: 'LENS', CONTACT_LENSES: 'CONTACT_LENS',
    ACCESSORIES: 'ACCESSORY', WRIST_WATCHES: 'WATCH', SMARTWATCHES: 'SMARTWATCH', SERVICES: 'SERVICE',
  };
  return map[cat] || cat;
}

// ============================================================================
// Main POS Layout
// ============================================================================

export function POSLayout() {
  const { user } = useAuth();
  const store = usePOSStore();

  // HIGH #6: Block POS access when no store is selected.
  // The actual "no store" branch is rendered AFTER all hooks have
  // run — early-returning here would violate the rules of hooks
  // and crash the page with "Rendered fewer hooks than expected"
  // whenever the user toggled their active store mid-session.
  const activeStoreId = user?.activeStoreId || user?.storeIds?.[0] || '';
  const noStoreSelected = !activeStoreId || activeStoreId === 'No store';

  const [showPrescriptionModal, setShowPrescriptionModal] = useState(false);
  const [showNewPrescription, setShowNewPrescription] = useState(false);
  const [showLensModal, setShowLensModal] = useState(false);
  const [discountItem, setDiscountItem] = useState<CartLineItem | null>(null);
  const [showReceipt, setShowReceipt] = useState(false);
  const [holdConfirm, setHoldConfirm] = useState(false);
  const [showRecallPanel, setShowRecallPanel] = useState(false);
  const [showDayEnd, setShowDayEnd] = useState(false);
  const [showNewConfirm, setShowNewConfirm] = useState(false);
  const [showWalkoutModal, setShowWalkoutModal] = useState(false);
  const [walkinBusy, setWalkinBusy] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  // Off-canvas cart drawer (tablet/phone <=1024px). Desktop keeps the inline
  // cart column; this only governs the slide-over + scrim on narrow widths.
  const [cartOpen, setCartOpen] = useState(false);
  const toast = useToast();

  // C-5 (DELTA 3): order-create idempotency key for the current submit attempt.
  // Generated when the user initiates "Pay now"; reused on a double-click /
  // retry of the SAME attempt (so the backend returns the same order instead
  // of duplicating), and cleared on success / when a new order starts so a
  // genuinely new order always gets a fresh key.
  const idempotencyKeyRef = useRef<string | null>(null);

  // POS "+1 walk-in" — attributes footfall to the chosen salesperson.
  // Requires a salesperson to be picked first (the conversion engine
  // needs the attribution); otherwise nudges the cashier to pick one.
  const handleWalkin = async () => {
    if (!store.salesperson_id) {
      toast.warning('Pick a salesperson first (step 1) to record a walk-in');
      return;
    }
    setWalkinBusy(true);
    try {
      const r = await walkoutsApi.walkinsPosIncrement(
        { sales_person_id: store.salesperson_id, mobile: store.customer?.phone || undefined },
        store.store_id || undefined,
      );
      toast.success(r?.deduped ? 'Already counted today' : `Walk-in recorded · ${r?.total ?? ''} today`);
    } catch {
      toast.error('Could not record walk-in');
    } finally {
      setWalkinBusy(false);
    }
  };
  // Phase 6.8 — workshop handoff modal. When an Rx order spawns a workshop
  // job, we hold the jobId here and open LensFittingFormModal so sales can
  // attach the physical fitting measurements before the Complete step.
  const [fittingJobId, setFittingJobId] = useState<string | null>(null);
  const [fittingSaving, setFittingSaving] = useState(false);
  const [fittingCoating, setFittingCoating] = useState<string>('');

  // Held bills from localStorage — cached to avoid repeated JSON.parse in render.
  // Since held bills now SURVIVE logout (so an auto-parked cart can be resumed
  // after re-login), the list shown + recallable is scoped to the CURRENT user
  // (held_by === user.id). A different cashier on a shared terminal must never
  // see or recall the previous user's parked cart. Legacy bills with no held_by
  // are treated as NOT the current user's (hidden) to avoid cross-user leaks.
  const currentUserId = user?.id || '';
  const [heldBillsCache, setHeldBillsCache] = useState<Array<{ id: string; customer: string; items: number; total: number; heldAt: string; held_by?: string | null; store_id?: string | null; auto?: boolean; reason?: string; state: any }>>([]);
  const refreshHeldBills = useCallback(() => {
    try {
      const all = JSON.parse(localStorage.getItem('ims-held-bills') || '[]');
      const mine = (Array.isArray(all) ? all : []).filter(
        (b: any) => b && b.held_by && b.held_by === currentUserId,
      );
      setHeldBillsCache(mine);
    } catch { setHeldBillsCache([]); }
  }, [currentUserId]);
  useEffect(() => { refreshHeldBills(); }, [refreshHeldBills]);
  // Only the current user's bills are ever exposed to the UI.
  const getHeldBills = useCallback(() => heldBillsCache, [heldBillsCache]);

  const holdCurrentBill = () => {
    // Single code path with the idle auto-park: the store builds + tags the
    // snapshot (held_by = current user, store_id, auto=false) and pushes it to
    // ims-held-bills. Manual hold then resets the transaction + dismisses UI.
    store.parkCurrentSale({ heldBy: currentUserId });
    refreshHeldBills();
    store.resetTransaction();
    setHoldConfirm(false);
  };

  const recallBill = (billId: string) => {
    // Recall only from the current user's bills (getHeldBills is pre-filtered),
    // so a cashier can never recall another user's parked cart.
    const bills = getHeldBills();
    const bill = bills.find(b => b.id === billId);
    if (!bill) return;
    // Atomic REPLACE (not a per-item merge into the current cart). Restores the
    // cart verbatim plus the cart-level discount + delivery fields, and lands on
    // the review step when there are items — all handled inside the store.
    store.restoreHeldSale(bill.state);
    // Remove ONLY the recalled bill from the full persisted list; leave other
    // users' parked carts untouched.
    try {
      const all = JSON.parse(localStorage.getItem('ims-held-bills') || '[]');
      const next = (Array.isArray(all) ? all : []).filter((b: any) => b?.id !== billId);
      localStorage.setItem('ims-held-bills', JSON.stringify(next));
    } catch { /* ignore */ }
    refreshHeldBills();
    setShowRecallPanel(false);
  };

  const deleteHeldBill = (billId: string) => {
    // Delete only the current user's own bill. Confirm ownership against the
    // filtered cache before touching the persisted list so we never prune
    // another cashier's parked cart.
    if (!getHeldBills().some(b => b.id === billId)) return;
    try {
      const all = JSON.parse(localStorage.getItem('ims-held-bills') || '[]');
      const next = (Array.isArray(all) ? all : []).filter((b: any) => b?.id !== billId);
      localStorage.setItem('ims-held-bills', JSON.stringify(next));
    } catch { /* ignore */ }
    refreshHeldBills();
  };

  // Safe reset: clear zustand + all local state
  const handleFullReset = () => {
    setShowPrescriptionModal(false);
    setShowNewPrescription(false);
    setShowLensModal(false);
    setDiscountItem(null);
    setShowReceipt(false);
    setHoldConfirm(false);
    setShowRecallPanel(false);
    setShowDayEnd(false);
    setErrorMsg(null);
    // C-5: a brand-new order must not reuse the previous attempt's key.
    idempotencyKeyRef.current = null;
    store.resetTransaction();
  };

  useEffect(() => {
    if (user) {
      store.setStoreId(user.activeStoreId || user.storeIds?.[0] || '');
      // Salesperson is NOT auto-defaulted to the logged-in user — the
      // incentive engine needs conscious attribution, so the cashier
      // must pick it explicitly on step 1 (see StepCustomer). It carries
      // across sales within a session once chosen.
    }
  }, [user]);

  // Condensed is the sole checkout flow (the classic alternative + toggle were
  // removed). The input groups (each maps to one rail entry; a condensed group
  // may render several canonical steps) come straight from the condensed
  // grouping. The 'complete' done-state is handled separately and is never part
  // of this navigable sequence.
  const flowGroups = useMemo(
    () => buildCondensedGroups(store.sale_type),
    [store.sale_type],
  );

  // Which group is the store's current canonical step inside? Direct member
  // match first. If the step isn't a member of any group in the active flow
  // (e.g. a quick-sale held bill restored onto 'review', which quick sales fold
  // into payment), project it onto the canonical order and pick the first group
  // whose anchor is at/after it — so 'review' lands on the payment group rather
  // than snapping back to Customer.
  const isComplete = store.current_step === 'complete';
  const currentGroupIndex = useMemo(() => {
    const direct = flowGroups.findIndex((g) => g.members.includes(store.current_step));
    if (direct >= 0) return direct;
    const CANON: POSStep[] = ['customer', 'prescription', 'products', 'review', 'payment', 'complete'];
    const stepRank = CANON.indexOf(store.current_step);
    const fwd = flowGroups.findIndex((g) => CANON.indexOf(g.anchor) >= stepRank);
    return fwd >= 0 ? fwd : Math.max(0, flowGroups.length - 1);
  }, [flowGroups, store.current_step]);
  const currentGroup = flowGroups[currentGroupIndex];

  // Rx accessory override (Issue: 'No Rx · accessory' source). When the operator
  // explicitly picks the accessory/no-Rx source on the prescription step, that
  // step is allowed to proceed even on a prescription order (mirrors the
  // quick-sale exemption) — WITHOUT weakening the server-side spectacle-Rx
  // requirement (order-create still validates every line; a spectacle lens with
  // no Rx is still rejected by the backend). Resets when the customer or sale
  // type changes (a fresh context must re-decide).
  const [rxAccessory, setRxAccessory] = useState(false);
  useEffect(() => {
    setRxAccessory(false);
  }, [store.customer?.id, store.sale_type]);

  // Per-canonical-step completion check (reused for group validation + the
  // condensed merged-step guard). Unchanged business rules — just factored out.
  const stepReady = useCallback((s: POSStep): boolean => {
    switch (s) {
      case 'customer': {
        // BILL-TO-MEMBER P1: a registered customer must have a billed MEMBER
        // selected before advancing (mandatory member step). Walk-ins are exempt
        // -- the backend synthesizes a Primary for the synthetic account, so the
        // member step is skipped (council walk-in rule). The Primary auto-selects
        // on account pick, so single-member accounts stay one-click.
        const isWalkinCust = !!store.customer && (
          store.customer.id?.toString().startsWith('walkin-') ||
          store.customer.name === 'Walk-in Customer'
        );
        if (!store.customer || !store.salesperson_id) return false;
        return isWalkinCust || !!store.patient;
      }
      case 'prescription':
        // Rx is mandatory for prescription orders UNLESS the operator picked the
        // accessory/no-Rx source (rxAccessory) — quick sales always pass. The
        // server still enforces the per-line spectacle-Rx requirement at
        // order-create regardless, so this only ungates step navigation.
        return store.sale_type === 'quick_sale' || !!store.prescription || rxAccessory;
      case 'products': return (store.cart || []).length > 0;
      case 'review': return (store.cart || []).length > 0;
      case 'payment':
        if (store.is_advance_payment) return store.getTotalPaid() > 0;
        return store.getBalance() <= 0.01;
      default: return true;
    }
  }, [store.customer, store.patient, store.salesperson_id, store.prescription, store.sale_type, store.cart, store.payments, store.is_advance_payment, rxAccessory]);

  // A group is satisfied only when EVERY canonical step it renders is ready —
  // so the merged condensed "Products & Rx" still enforces the Rx-attached gate
  // for prescription orders AND cart-not-empty, and "Pay & Review" still
  // enforces the payment-balance guard. Nothing relaxes.
  const canProceed = useMemo(
    () => (currentGroup ? currentGroup.members.every(stepReady) : true),
    [currentGroup, stepReady],
  );

  // Whether the current group is the final INPUT group (its primary action is
  // "Complete order", i.e. it renders the payment step).
  const isFinalInputGroup = !!currentGroup && currentGroup.members.includes('payment');

  // Flow-aware navigation. We drive the canonical store step directly to each
  // group's anchor rather than store.nextStep/prevStep (whose linear 6-step
  // walk doesn't know about condensed grouping).
  const goToGroup = useCallback((idx: number) => {
    const g = flowGroups[idx];
    if (g) startTransition(() => store.setStep(g.anchor));
  }, [flowGroups, store]);

  const goNext = useCallback(() => {
    if (currentGroupIndex < flowGroups.length - 1) goToGroup(currentGroupIndex + 1);
  }, [currentGroupIndex, flowGroups.length, goToGroup]);

  const goBack = useCallback(() => {
    if (currentGroupIndex > 0) goToGroup(currentGroupIndex - 1);
  }, [currentGroupIndex, goToGroup]);

  useEffect(() => {
    const handle = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      // F2 jumps to the group that renders the products catalog; F9 to the
      // group that renders the payment surface.
      if (e.key === 'F2') {
        e.preventDefault();
        const i = flowGroups.findIndex((g) => g.members.includes('products'));
        if (i >= 0) goToGroup(i);
      }
      if (e.key === 'F9' && (store.cart || []).length > 0) {
        e.preventDefault();
        const i = flowGroups.findIndex((g) => g.members.includes('payment'));
        if (i >= 0) goToGroup(i);
      }
      if (e.key === 'Escape' && !isComplete && currentGroupIndex > 0) { e.preventDefault(); goBack(); }
      if (e.key === 'F4' && (store.cart || []).length > 0) { e.preventDefault(); setHoldConfirm(true); }
      // Ctrl+Enter submits when the final input group (payment) is showing.
      if (e.key === 'Enter' && e.ctrlKey && isFinalInputGroup) { e.preventDefault(); handleCreateOrder(); }
      // Plain Enter advances on non-final, non-complete groups.
      if (e.key === 'Enter' && !e.ctrlKey && !isComplete && !isFinalInputGroup) {
        e.preventDefault();
        if (canProceed) goNext();
      }
    };
    window.addEventListener('keydown', handle);
    return () => window.removeEventListener('keydown', handle);
  }, [(store.cart || []).length, isComplete, isFinalInputGroup, currentGroupIndex, flowGroups, goToGroup, goNext, goBack, canProceed]);

  async function handleCreateOrder() {
    if (store.is_processing) return;

    if (store.sale_type === 'prescription_order') {
      const hasLens = (store.cart || []).some(i =>
        i.category === 'RX_LENSES' || i.lens_details || i.is_optical
      );
      if (!hasLens) {
        setErrorMsg('Prescription order requires at least one lens item. Add lenses or switch to Quick Sale.');
        return;
      }
    }

    if (store.getBalance() > 0.01 && !store.is_advance_payment) {
      setErrorMsg('Payment incomplete. Add payments or enable "Advance payment only".');
      return;
    }

    setErrorMsg(null);
    store.setProcessing(true);
    // C-5: mint a key for this attempt if one isn't already in flight. A
    // double-click / retry of the SAME attempt reuses it (idempotent); a new
    // order resets it (on success below / on full reset).
    if (!idempotencyKeyRef.current) {
      idempotencyKeyRef.current =
        typeof crypto !== 'undefined' && crypto.randomUUID
          ? crypto.randomUUID()
          : `idem-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    }
    try {
      const result = await orderApi.createOrder({
        customer_id: store.customer?.id,
        // BILL-TO-MEMBER P1: send the selected member so the order bills to a
        // member, not the bare account. Omitted for walk-ins (the backend
        // synthesizes a Primary for the synthetic account).
        patient_id: store.patient?.id || undefined,
        store_id: store.store_id,
        order_type: store.sale_type,
        salesperson_id: store.salesperson_id,
        salesperson_name: store.salesperson_name,
        visufit_id: store.visufit_id || undefined,
        items: (store.cart || []).map(item => ({
          item_type: mapCategory(item.category),
          product_id: item.product_id,
          product_name: item.name,
          sku: item.sku,
          brand: item.brand,
          subbrand: item.subbrand,
          category: item.category,
          quantity: item.quantity,
          unit_price: item.unit_price,
          discount_percent: item.discount_percent,
          prescription_id: item.linked_prescription_id,
          lens_details: item.lens_details,
          item_note: (item as any).item_note || undefined,
        })),
        notes: store.cart_note || undefined,
        // Phase 6.7 — pass delivery + cart-discount fields through to backend
        delivery_date: store.delivery_date || undefined,
        delivery_time_slot: store.delivery_time_slot || undefined,
        delivery_priority: store.delivery_priority || 'NORMAL',
        cart_discount_percent: store.cart_discount_percent || 0,
        cart_discount_amount: store.cart_discount_amount || 0,
        cart_discount_reason: store.cart_discount_reason || undefined,
        cart_discount_approved_by: store.cart_discount_approved_by || undefined,
      } as any, idempotencyKeyRef.current || undefined);
      if (result?.order_id) {
        // C-5: success -> drop the key so the next order gets a fresh one.
        idempotencyKeyRef.current = null;

        // POS-3: loyalty points are only atomically debited AFTER the order
        // is confirmed. Call /loyalty/redeem now with the real order_id so
        // the ledger is linked. Fail-soft: if the redeem call fails the order
        // is still finalized (staff can adjust manually); the pending intent
        // is cleared regardless.
        const pendingLoyalty = store.pendingLoyaltyRedeem;
        if (pendingLoyalty && store.customer?.id) {
          try {
            await loyaltyApi.redeem({
              customer_id: String(store.customer.id),
              order_id: result.order_id,
              points: pendingLoyalty.points,
              order_value: pendingLoyalty.orderValue,
            });
          } catch {
            // Non-fatal: order is created. Log for ops visibility.
            // eslint-disable-next-line no-console
            console.warn('[POS] Deferred loyalty redeem failed — points NOT debited; order still saved.');
          }
          store.clearPendingLoyaltyRedeem();
        }

        for (const p of (store.payments || [])) {
          // Skip the LOYALTY tender — it is a UI-only line that tracks the
          // rupee value of the deferred redeem; the actual ledger entry was
          // created by /loyalty/redeem above (or skipped on failure).
          if (p.method === 'LOYALTY') continue;
          try {
            const body: Record<string, unknown> = {
              method: p.method,
              amount: p.amount,
              reference: p.reference,
              voucher_code: p.voucherCode,
            };
            // EMI requires emi_months on the backend (else 400). Forward the
            // tenure/provider the POS already captured — without this every EMI
            // payment silently failed and the order stayed unpaid.
            // POS-2: also pass emi_principal (financed balance) so the backend
            // builds the schedule on the loan amount, not the down-payment.
            if (p.method === 'EMI') {
              body.emi_months = p.emiTenure;
              body.emi_provider = p.emiProvider;
              if (p.emiBalance && p.emiBalance > 0) {
                body.emi_principal = p.emiBalance;
              }
            }
            await orderApi.addPayment(result.order_id, body as any);
          } catch {
            // Don't block order — payment can be recorded later
          }
        }
        store.setOrderResult(result.order_id, result.order_number);

        // Phase 6.8 — auto-create workshop job + prompt sales to fill
        // fitting details. Only fires for Rx orders that actually ship a
        // lens. Earlier code matched category==='RX_LENSES' which never
        // matched the real catalog (categories are OPTICAL_LENS /
        // SPECTACLE_LENS). We also no longer silently swallow errors.
        const LENS_CATS = ['OPTICAL_LENS', 'OPTICAL_LENSES', 'SPECTACLE_LENS', 'SPECTACLE_LENSES', 'RX_LENSES', 'LENS', 'LENSES'];
        const FRAME_CATS = ['FRAMES', 'FRAME', 'SUNGLASSES', 'SUNGLASS', 'SPECTACLE_FRAME'];
        const cartItems = store.cart || [];
        const frameItem = cartItems.find(i => FRAME_CATS.includes((i.category || '').toUpperCase()));
        const lensItem = cartItems.find(
          i => LENS_CATS.includes((i.category || '').toUpperCase()) || !!i.lens_details,
        );
        if (store.sale_type === 'prescription_order' && store.prescription && (frameItem || lensItem)) {
          try {
            const expectedDate = new Date();
            expectedDate.setDate(expectedDate.getDate() + 5);
            const jobResp = await workshopApi.createJob({
              order_id: result.order_id,
              frame_details: frameItem ? {
                product_id: frameItem.product_id,
                name: frameItem.name,
                sku: frameItem.sku,
                brand: frameItem.brand,
              } : {},
              lens_details: lensItem?.lens_details || {
                product_id: lensItem?.product_id,
                name: lensItem?.name,
              },
              prescription_id: store.prescription.id || '',
              fitting_instructions: cartItems
                .filter(i => i.notes)
                .map(i => `${i.name}: ${i.notes}`)
                .join('; ') || undefined,
              special_notes: store.cart_note || undefined,
              expected_date: expectedDate.toISOString().split('T')[0],
            });
            // Open fitting-details modal with the new jobId — Complete step
            // is advanced from the modal's onSave / onBack handlers.
            if (jobResp?.job_id) {
              setFittingJobId(jobResp.job_id);
              setFittingCoating(
                (lensItem?.lens_details?.coatings || []).join(', ') || '',
              );
              // Keep the POS in its current step; the modal overlays above
              // and advances to 'complete' when resolved. Processing flag
              // already turned off in `finally` below.
              return;
            }
          } catch (e) {
            // Non-fatal — the order IS created. Surface a warning so staff
            // can manually create the workshop job / call IT if necessary.
            // eslint-disable-next-line no-console
            console.warn('[POS] Workshop job auto-create failed:', e);
            setErrorMsg(
              'Order saved, but workshop job auto-create failed — please add it manually from the Workshop page.',
            );
          }
        }

        store.setStep('complete');
      } else {
        setErrorMsg('Order created but no ID returned. Check order list.');
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setErrorMsg('Failed to create order: ' + (msg || 'Network error'));
    } finally {
      store.setProcessing(false);
    }
  }

  // Editorial title + subtitle for each canonical step.
  const STEP_HEADERS: Record<POSStep, { title: string; sub: string }> = {
    customer: { title: "Who's buying?", sub: 'Search an existing customer or create a walk-in. Phone lookup picks up family members automatically.' },
    prescription: { title: 'Capture Rx', sub: "Pull the customer's active prescription or enter a new one. Optometrist role required for tested-at-store Rx." },
    products: { title: 'Add to cart', sub: 'Barcode scan, SKU lookup, or browse. Lens orders auto-attach to the selected Rx.' },
    review: { title: 'Review & discount', sub: 'Check line items, apply category / brand / role-capped discount. GST auto-calculated.' },
    payment: { title: 'Tender', sub: 'Single or split tender across Cash / UPI / Card / EMI / Advance. Round-off at 50p if enabled.' },
    complete: { title: 'Done.', sub: 'Order placed. Receipt printed. Workshop job card auto-created for Rx orders.' },
  };
  // Editorial header for the active GROUP. For the single-member Customer group
  // reuse the per-step header; for the merged condensed groups give a combined
  // title.
  const GROUP_HEADERS: Record<string, { title: string; sub: string }> = {
    'condensed:products': { title: 'Products & prescription', sub: 'Capture or attach the Rx, then add frames, lenses, contacts and accessories.' },
    'condensed:payment': { title: 'Pay & review', sub: 'Final check — discount, GST and delivery on one side, tender on the other.' },
  };
  const header = isComplete
    ? STEP_HEADERS.complete
    : GROUP_HEADERS[`condensed:${currentGroup?.key}`]
      || STEP_HEADERS[currentGroup?.anchor ?? 'customer'];

  // Cart column shows on any input group that renders products/review/
  // prescription — that's the merged Products & Rx and Pay & Review groups
  // (the Customer group renders none of them).
  const cartRelevantSteps: POSStep[] = ['products', 'review', 'prescription'];
  const showCartCol =
    !isComplete &&
    !!currentGroup &&
    currentGroup.members.some((m) => cartRelevantSteps.includes(m)) &&
    (store.cart || []).length > 0;

  // Hooks-rule-safe early return: every useState / useEffect / useMemo /
  // useCallback above this point is unconditional, so React's hook
  // ordering stays stable when activeStoreId flips.
  if (noStoreSelected) {
    return (
      <div className="min-h-screen min-h-[100dvh] bg-white flex items-center justify-center p-4">
        <div className="bg-white border border-amber-300 rounded-2xl p-8 max-w-md text-center">
          <AlertTriangle className="w-12 h-12 text-amber-500 mx-auto mb-4" />
          <h2 className="text-xl font-bold text-gray-900 mb-2">No Store Selected</h2>
          <p className="text-sm text-gray-500 mb-4">
            POS requires an active store to process transactions. Please select a store from the header dropdown before accessing Point of Sale.
          </p>
          <p className="text-xs text-gray-500">
            Orders created without a store context cannot be tracked, invoiced, or reported accurately.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="pos-body">
      {/* ── Left rail: vertical stepper + actions + held bills ── */}
      <aside className="steps-rail">
        <span className="eyebrow">Checkout · {flowGroups.length} steps</span>

        {flowGroups.map((group, idx) => {
          const isActive = !isComplete && idx === currentGroupIndex;
          const isDone = isComplete || idx < currentGroupIndex;
          const Icon = group.icon;
          // Sub-label reflects the merged group's live state.
          let sub = group.sub;
          if (group.key === 'customer') sub = store.customer?.name ?? 'Pick customer';
          else if (group.members.includes('products')) {
            const n = (store.cart || []).length;
            const rx = store.sale_type === 'prescription_order'
              ? (store.prescription ? 'Rx ✓' : 'Rx needed') + ' · '
              : '';
            sub = `${rx}${n} ${n === 1 ? 'item' : 'items'}`;
          } else if (group.members.includes('payment')) {
            sub = store.getBalance() <= 0.01 ? 'Paid in full' : 'Discount, GST & tender';
          }
          return (
            <button
              key={group.key}
              type="button"
              className={'step' + (isActive ? ' active' : '') + (isDone ? ' done' : '')}
              onClick={() => {
                // Allow jumping back to a completed group (same guard as before:
                // only completed/active groups are clickable).
                if (isDone && !isComplete) goToGroup(idx);
              }}
              disabled={(!isDone && !isActive) || isComplete}
              title={group.label}
            >
              <div className="step-num">
                {isDone && !isActive ? '' : <Icon className="w-3 h-3" />}
              </div>
              <div className="min-w-0 flex-1">
                <div className="step-title">{group.label}</div>
                <div className="step-sub">{sub}</div>
              </div>
            </button>
          );
        })}

        {/* Rail actions — Hold / Recall / New + kbd hints */}
        <div className="rail-actions">
          <button
            type="button"
            onClick={() => setHoldConfirm(true)}
            disabled={(store.cart || []).length === 0}
            className="btn sm"
            title="Hold current bill (F4)"
          >
            <Pause className="w-4 h-4" /> Hold bill
          </button>
          <button
            type="button"
            onClick={() => setShowRecallPanel(true)}
            className="btn sm relative"
            title="Recall held bill"
          >
            <Play className="w-4 h-4" /> Recall
            {getHeldBills().length > 0 && (
              <span
                className="grid place-items-center"
                style={{
                  position: 'absolute',
                  top: -6,
                  right: -6,
                  minWidth: 18,
                  height: 18,
                  padding: '0 5px',
                  borderRadius: 9,
                  background: 'var(--bv)',
                  color: '#fff',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 10,
                  fontWeight: 600,
                }}
              >
                {getHeldBills().length}
              </span>
            )}
          </button>
          <button
            type="button"
            onClick={() => {
              if ((store.cart || []).length > 0) {
                setHoldConfirm(false);
                setShowNewConfirm(true);
              } else {
                handleFullReset();
              }
            }}
            className="btn sm"
            title="Start a new transaction"
          >
            <RotateCcw className="w-4 h-4" /> New sale
          </button>
          {/* Footfall + walkout capture — feed the incentive conversion math */}
          <button
            type="button"
            onClick={handleWalkin}
            disabled={walkinBusy}
            className="btn sm"
            title="Record a walk-in (footfall) for the current salesperson"
          >
            <UserPlus className="w-4 h-4" /> {walkinBusy ? 'Recording…' : '+1 walk-in'}
          </button>
          <button
            type="button"
            onClick={() => setShowWalkoutModal(true)}
            className="btn sm"
            title="Log a customer who left without buying"
          >
            <DoorOpen className="w-4 h-4" /> Walkout
          </button>
          <div className="kbd-row">
            <span><kbd className="kbd">F2</kbd>Search</span>
            <span><kbd className="kbd">F4</kbd>Hold</span>
            <span><kbd className="kbd">F9</kbd>Pay</span>
            <span><kbd className="kbd">Esc</kbd>Back</span>
            <span><kbd className="kbd">{'\u23CE'}</kbd>Next</span>
          </div>
        </div>

        {/* Held bills preview */}
        {getHeldBills().length > 0 && (
          <div className="held-list">
            <div className="held-list-title">
              <span>Held · {getHeldBills().length}</span>
              <button
                type="button"
                className="mute"
                style={{ background: 'transparent', border: 0, fontSize: 11, cursor: 'pointer', fontFamily: 'var(--font-mono)' }}
                onClick={() => setShowRecallPanel(true)}
              >
                see all
              </button>
            </div>
            {getHeldBills().slice(0, 3).map((bill) => (
              <div
                key={bill.id}
                className="held-item"
                onClick={() => recallBill(bill.id)}
                title="Click to recall"
              >
                <div className="row1">
                  <span>{bill.customer}</span>
                  <span className="code">{bill.items} · ₹{Math.round(bill.total).toLocaleString('en-IN')}</span>
                </div>
                <div className="row2">
                  <span>held</span>
                  <span>{new Date(bill.heldAt).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </aside>

      {/* ── Right cell: work surface (+ optional cart col) + sticky footer ── */}
      <div className="pos-main">
        <div className="pos-work">
          {/* Editorial header */}
          <div className="work-head">
            <div className="eyebrow mb-1.5">
              {isComplete
                ? 'Complete'
                : `Step ${Math.max(1, currentGroupIndex + 1)} / ${flowGroups.length} · ${currentGroup?.label ?? ''}`}
            </div>
            <h2>{header.title}</h2>
            <p className="sub">{header.sub}</p>
          </div>

          {/* Error banner */}
          {errorMsg && (
            <div
              className="s-section flex items-center"
              style={{
                padding: 12,
                borderColor: 'var(--err-50)',
                background: 'var(--err-50)',
                gap: 8,
                marginBottom: 14,
              }}
            >
              <AlertTriangle className="w-5 h-5" style={{ color: 'var(--err)' }} />
              <span className="flex-1" style={{ color: 'var(--err)' }}>{errorMsg}</span>
              <button onClick={() => setErrorMsg(null)} className="btn sm ghost" aria-label="Dismiss error" title="Dismiss error">
                <X className="w-4 h-4" />
              </button>
            </div>
          )}

          {/* Step content — renders every canonical step in the active group.
              The work area is a flex column with THIS region scrolling and the
              footer pinned (flex:0 0 auto) below; pb gives the last row scroll
              clearance. The condensed flow simply stacks the same step
              components on one scrollable surface for merged groups (Rx above
              Products; Review above Payment). */}
          <div className="pos-scroll">
            {/* Complete — the done state (not a navigable group). Rendered
                exclusively; no input steps show alongside the receipt. */}
            {isComplete ? (
              <StepComplete onPrint={() => setShowReceipt(true)} onReset={handleFullReset} />
            ) : (<>
              {/* Customer (its own group) */}
              {currentGroup?.members.includes('customer') && <StepCustomer />}

              {/* Merged "Products & Rx" group renders the Prescription surface
                  ABOVE the Products catalog in one scroll. The Rx surface only
                  shows for prescription orders (StepPrescription is gated). */}
              {currentGroup?.members.includes('prescription') && store.sale_type === 'prescription_order' && (
                <section className={currentGroup.members.includes('products') ? 'pos-merge-sec' : undefined}>
                  {currentGroup.members.includes('products') && (
                    <div className="pos-merge-cap">Prescription</div>
                  )}
                  <StepPrescription
                    onShowModal={() => setShowPrescriptionModal(true)}
                    onShowNew={() => setShowNewPrescription(true)}
                    onAccessoryOnlyChange={setRxAccessory}
                  />
                </section>
              )}
              {currentGroup?.members.includes('products') && (
                <section className={currentGroup.members.includes('prescription') && store.sale_type === 'prescription_order' ? 'pos-merge-sec' : undefined}>
                  {currentGroup.members.includes('prescription') && store.sale_type === 'prescription_order' && (
                    <div className="pos-merge-cap">Products</div>
                  )}
                  <StepProducts onOpenLensModal={() => setShowLensModal(true)} />
                </section>
              )}

              {/* Merged "Pay & Review" group: Review + Payment side by side on
                  desktop (stacked on narrow). Quick sales render Payment only. */}
              {currentGroup && currentGroup.members.includes('review') && currentGroup.members.includes('payment') ? (
                <div className="pos-payreview">
                  <section className="pos-merge-sec">
                    <div className="pos-merge-cap">Review</div>
                    <StepReview onOpenDiscount={(item) => setDiscountItem(item)} />
                  </section>
                  <section className="pos-merge-sec">
                    <div className="pos-merge-cap">Payment</div>
                    <StepPayment />
                  </section>
                </div>
              ) : (
                <>
                  {currentGroup?.members.includes('review') && <StepReview onOpenDiscount={(item) => setDiscountItem(item)} />}
                  {currentGroup?.members.includes('payment') && <StepPayment />}
                </>
              )}
            </>)}
          </div>

          {/* Bottom action bar — flex:0 0 auto, pinned to the bottom of the
              work area (the content region above scrolls independently). */}
          {!isComplete && (
            <div className="pos-footer">
              <div className="left">
                {currentGroupIndex > 0 && (
                  <button
                    type="button"
                    onClick={goBack}
                    className="btn sm"
                  >
                    <ChevronLeft className="w-4 h-4" /> Back
                  </button>
                )}
                {(store.cart || []).length > 0 && (
                  <span className="cart-hint">
                    <strong>{(store.cart || []).length}</strong>{' '}
                    {(store.cart || []).length === 1 ? 'item' : 'items'} · <strong>₹{Math.round(store.getGrandTotal()).toLocaleString('en-IN')}</strong>
                  </span>
                )}
              </div>
              <button
                type="button"
                onClick={() => {
                  setErrorMsg(null);
                  if (isFinalInputGroup) {
                    handleCreateOrder();
                  } else {
                    goNext();
                  }
                }}
                disabled={!canProceed || store.is_processing}
                className={'btn sm ' + (isFinalInputGroup ? 'accent' : 'primary')}
              >
                {store.is_processing ? (
                  <>
                    <span
                      className="w-4 h-4 animate-spin rounded-full"
                      style={{
                        border: '2px solid rgba(255,255,255,.3)',
                        borderTopColor: '#fff',
                      }}
                    />
                    Processing…
                  </>
                ) : (
                  <>
                    {isFinalInputGroup ? 'Complete order' : 'Continue'}
                    <ChevronRight className="w-4 h-4" />
                  </>
                )}
              </button>
            </div>
          )}
        </div>

        {/* Right cart column — inline on desktop; an off-canvas drawer on
            tablet/phone (<=1024px) toggled by the FAB / topbar control with a
            scrim. `open` only affects the narrow-width slide-over; desktop
            ignores it. Rendered only during cart-relevant groups. */}
        {showCartCol && (
          <>
            <aside className={'pos-cart-col' + (cartOpen ? ' open' : '')}>
              <button
                type="button"
                className="pos-cart-close btn sm ghost"
                onClick={() => setCartOpen(false)}
                aria-label="Close cart"
                title="Close cart"
              >
                <X className="w-4 h-4" />
              </button>
              <CartSidebar />
            </aside>
            <div
              className={'pos-cart-scrim' + (cartOpen ? ' on' : '')}
              onClick={() => setCartOpen(false)}
              aria-hidden="true"
            />
          </>
        )}
      </div>

      {/* Walkout capture — reuses the Walkouts-module intake modal */}
      <WalkoutIntakeModal
        isOpen={showWalkoutModal}
        onClose={() => setShowWalkoutModal(false)}
        onSaved={() => {
          toast.success('Walkout logged');
          setShowWalkoutModal(false);
        }}
      />

      {/* Floating cart FAB (tablet/phone). Opens the off-canvas cart drawer
          rather than jumping the wizard to the review step — so it never
          disrupts the current step. */}
      {showCartCol && (
        <div className="pos-cart-fab fixed bottom-20 right-4 z-30">
          <button
            onClick={() => setCartOpen(true)}
            className="w-14 h-14 rounded-full shadow-lg flex items-center justify-center relative touch-manipulation"
            style={{ background: 'var(--bv)', color: '#fff' }}
            aria-label={`Open cart (${(store.cart || []).length} items)`}
          >
            <ShoppingCart className="w-6 h-6" />
            <span
              className="absolute -top-1 -right-1 w-6 h-6 text-white text-xs font-bold rounded-full flex items-center justify-center"
              style={{ background: 'var(--ink)' }}
            >
              {(store.cart || []).length}
            </span>
          </button>
        </div>
      )}

      {/* MODALS */}
      {showPrescriptionModal && store.customer && (
        <PrescriptionSelectModal patient={store.patient} customerId={store.customer.id} currentPrescriptionId={store.prescription?.id}
          onSelect={(rx) => { store.setPrescription(rx); setShowPrescriptionModal(false); }}
          onCreateNew={() => { setShowPrescriptionModal(false); setShowNewPrescription(true); }}
          onClose={() => setShowPrescriptionModal(false)} />
      )}
      {showNewPrescription && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
            <div className="p-4 border-b border-gray-200 flex items-center justify-between">
              <h3 className="font-semibold text-gray-900">New Prescription</h3>
              <button onClick={() => { setShowNewPrescription(false); setErrorMsg(null); }} className="p-1 hover:bg-gray-100 rounded" aria-label="Close" title="Close"><X className="w-5 h-5" /></button>
            </div>
            {errorMsg && (
              <div className="mx-4 mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700 flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                <div><p className="font-medium">Failed to save prescription</p><p className="text-xs mt-0.5">{errorMsg}</p></div>
                <button onClick={() => setErrorMsg(null)} className="ml-auto text-red-400 hover:text-red-600" aria-label="Dismiss error" title="Dismiss error"><X className="w-4 h-4" /></button>
              </div>
            )}
            <div className="p-4">
              <PrescriptionForm
                allowContactLens={false}
                onSubmit={async (rxData) => {
                  setErrorMsg(null);
                  try {
                    const isOptometrist = user?.roles?.includes('OPTOMETRIST');
                    const source = isOptometrist ? 'TESTED_AT_STORE' : 'FROM_DOCTOR';

                    const result = await prescriptionApi.createPrescription({
                      patient_id: store.patient?.id || store.customer?.id,
                      customer_id: store.customer?.id,
                      source,
                      optometrist_id: isOptometrist ? user?.id : (user?.id || 'admin-override'),
                      validity_months: 12,
                      right_eye: { sph: String(rxData.sph_od || 0), cyl: String(rxData.cyl_od || 0), axis: rxData.axis_od || 180, add: String(rxData.add_od || 0), pd: String(rxData.pd_od || ''), prism: rxData.prism_od || undefined, base: rxData.base_od || undefined, acuity: rxData.va_od || undefined },
                      left_eye: { sph: String(rxData.sph_os || 0), cyl: String(rxData.cyl_os || 0), axis: rxData.axis_os || 180, add: String(rxData.add_os || 0), pd: String(rxData.pd_os || ''), prism: rxData.prism_os || undefined, base: rxData.base_os || undefined, acuity: rxData.va_os || undefined },
                      ipd: rxData.ipd || undefined,
                      lens_recommendation: rxData.lens_type || undefined,
                      next_checkup: rxData.next_checkup || undefined,
                      remarks: rxData.doctor_name ? `Dr. ${rxData.doctor_name}` : undefined,
                    } as any);

                    if (result?.prescription_id) {
                      store.setPrescription({
                        id: result.prescription_id,
                        patientId: store.patient?.id || '',
                        customerId: store.customer?.id || '',
                        storeId: store.store_id,
                        testDate: new Date().toISOString(),
                        rightEye: { sphere: rxData.sph_od || 0, cylinder: rxData.cyl_od || null, axis: rxData.axis_od || null, add: rxData.add_od || null, pd: rxData.pd_od || 0 },
                        leftEye: { sphere: rxData.sph_os || 0, cylinder: rxData.cyl_os || null, axis: rxData.axis_os || null, add: rxData.add_os || null, pd: rxData.pd_os || 0 },
                        status: 'COMPLETED',
                        createdAt: new Date().toISOString(),
                        updatedAt: new Date().toISOString(),
                      } as Prescription);
                      setErrorMsg(null);
                      setShowNewPrescription(false);
                    } else {
                      setErrorMsg('Prescription saved but no ID returned. Try selecting from existing prescriptions.');
                    }
                  } catch (err) {
                    const msg = err instanceof Error ? err.message : String(err);
                    setErrorMsg(msg || 'Network error -- check your connection and try again');
                  }
                }}
                onCancel={() => { setShowNewPrescription(false); setErrorMsg(null); }}
              />
            </div>
          </div>
        </div>
      )}
      {showLensModal && (
        <LensDetailsModal onClose={() => setShowLensModal(false)}
          onSave={(details) => {
            store.addToCart({ product_id: `lens-${Date.now()}`, name: `${details.lensCategory} - ${details.indexLabel}`, sku: `RX-CUSTOM-${Date.now()}`,
              category: 'RX_LENSES', unit_price: details.totalPrice || 0, mrp: details.totalPrice || 0, quantity: 2, is_optical: true,
              linked_prescription_id: store.prescription?.id, lens_details: { type: details.lensCategory, material: details.indexLabel, index: details.indexId, coatings: [details.coatingLabel].filter(Boolean) } });
            setShowLensModal(false);
          }} />
      )}
      {discountItem && (
        <DiscountModal item={{ product_id: discountItem.product_id, name: discountItem.name, sku: discountItem.sku, unitPrice: discountItem.unit_price, quantity: discountItem.quantity, category: discountItem.category, brand: discountItem.brand, discountPercent: discountItem.discount_percent } as any}
          maxDiscountPercent={user?.discountCap || 10}
          onApply={(pct) => { store.applyDiscount(discountItem.id, pct); setDiscountItem(null); }}
          onClose={() => setDiscountItem(null)} />
      )}
      {showReceipt && <POSReceipt onClose={() => setShowReceipt(false)} />}

      {/* Phase 6.8 — Sales→Workshop fitting handoff. Opens right after an
          Rx order spawns a workshop job; advances POS to Complete on save
          or back. Either way the order is already created. */}
      {fittingJobId && (
        <LensFittingFormModal
          prefilledCoating={fittingCoating}
          isSaving={fittingSaving}
          onSave={async (v: LensFittingFormValue) => {
            setFittingSaving(true);
            try {
              await workshopApi.updateFittingDetails(fittingJobId, v);
              setFittingJobId(null);
              setFittingCoating('');
              store.setStep('complete');
            } catch (e) {
              // Keep the modal open so sales can retry; surface the error.
              // Rest of the order is already persisted — no revenue at risk.
              // eslint-disable-next-line no-console
              console.error('[POS] Save fitting details failed:', e);
              setErrorMsg('Could not save fitting details — please try again or skip for now.');
            } finally {
              setFittingSaving(false);
            }
          }}
          onBack={() => {
            // "Back" dismisses the modal but still advances to Complete —
            // the order + workshop job are already created; fitting details
            // can be filled later from the Workshop page.
            setFittingJobId(null);
            setFittingCoating('');
            store.setStep('complete');
          }}
        />
      )}
      {holdConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 max-w-sm">
            <h3 className="font-semibold text-gray-900 mb-2">Hold this bill?</h3>
            <p className="text-sm text-gray-500 mb-1">{store.customer?.name || 'Walk-in'} {'\u00B7'} {(store.cart || []).length} items {'\u00B7'} {'\u20B9'}{Math.round(store.getGrandTotal()).toLocaleString('en-IN')}</p>
            <p className="text-xs text-gray-500 mb-4">Cart will be saved and can be recalled later.</p>
            <div className="flex gap-2">
              <button onClick={() => setHoldConfirm(false)} className="flex-1 px-4 py-2 border border-gray-300 rounded-lg text-sm">Cancel</button>
              <button onClick={holdCurrentBill} className="flex-1 px-4 py-2 bg-amber-500 text-white rounded-lg text-sm font-semibold">Hold Bill</button>
            </div>
          </div>
        </div>
      )}
      {showNewConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl p-6 max-w-sm border border-gray-200">
            <h3 className="font-semibold text-gray-900 mb-2">Start new transaction?</h3>
            <p className="text-sm text-gray-500 mb-4">Current cart ({(store.cart || []).length} items, {'\u20B9'}{Math.round(store.getGrandTotal()).toLocaleString('en-IN')}) will be cleared. Consider holding the bill first.</p>
            <div className="flex gap-2">
              <button onClick={() => setShowNewConfirm(false)} className="flex-1 px-4 py-2 border border-gray-300 text-gray-700 rounded-lg text-sm hover:bg-gray-100">Cancel</button>
              <button onClick={() => { holdCurrentBill(); setShowNewConfirm(false); }} className="flex-1 px-4 py-2 bg-amber-600 text-white rounded-lg text-sm font-semibold hover:bg-amber-700">Hold & New</button>
              <button onClick={() => { handleFullReset(); setShowNewConfirm(false); }} className="flex-1 px-4 py-2 bg-red-600 text-white rounded-lg text-sm font-semibold hover:bg-red-700">Discard & New</button>
            </div>
          </div>
        </div>
      )}
      {showRecallPanel && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-md max-h-[70vh] overflow-y-auto">
            <div className="p-4 border-b border-gray-200 flex items-center justify-between">
              <h3 className="font-semibold text-gray-900">Held Bills ({getHeldBills().length})</h3>
              <button onClick={() => setShowRecallPanel(false)} className="p-1 hover:bg-gray-100 rounded" aria-label="Close" title="Close"><X className="w-5 h-5" /></button>
            </div>
            <div className="p-4 space-y-2">
              {getHeldBills().length === 0 ? (
                <p className="text-sm text-gray-500 text-center py-8">No held bills</p>
              ) : getHeldBills().map(bill => (
                <div key={bill.id} className="bg-amber-50 border border-amber-300 rounded-lg p-3 flex items-center justify-between">
                  <div>
                    <p className="font-medium text-sm text-gray-900">{bill.customer}</p>
                    <p className="text-xs text-gray-500">{bill.items} items {'\u00B7'} {'\u20B9'}{Math.round(bill.total).toLocaleString('en-IN')}</p>
                    <p className="text-[10px] text-gray-500">{new Date(bill.heldAt).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}</p>
                  </div>
                  <div className="flex gap-2">
                    <button onClick={() => { deleteHeldBill(bill.id); setShowRecallPanel(false); setTimeout(() => setShowRecallPanel(true), 50); }}
                      className="text-xs text-red-500 hover:text-red-700 px-2 py-1">Delete</button>
                    <button onClick={() => recallBill(bill.id)}
                      className="text-xs bg-bv-red-600 text-white px-3 py-1 rounded font-semibold hover:bg-bv-red-700">Recall</button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
      {showDayEnd && (
        <DayEndReport storeId={store.store_id} onClose={() => setShowDayEnd(false)} />
      )}
    </div>
  );
}

// ============================================================================
// Customer Purchase History (compact, in StepCustomer)
// ============================================================================
function RxAvailableBadge({ customerId }: { customerId: string; customerName?: string }) {
  const store = usePOSStore();
  const [rxCount, setRxCount] = useState(0);
  const [latestRx, setLatestRx] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await prescriptionApi.getPrescriptions(customerId);
        const prescriptions = result?.prescriptions || result || [];
        if (!cancelled && Array.isArray(prescriptions)) {
          const now = new Date();
          const valid = prescriptions.filter((rx: any) => {
            const testDate = new Date(rx.testDate || rx.test_date || rx.created_at);
            const months = rx.validityMonths || rx.validity_months || 12;
            const expiry = new Date(testDate);
            expiry.setMonth(expiry.getMonth() + months);
            return now < expiry;
          });
          setRxCount(valid.length);
          if (valid.length > 0) {
            valid.sort((a: any, b: any) => {
              const da = new Date(a.testDate || a.test_date || a.created_at).getTime();
              const db = new Date(b.testDate || b.test_date || b.created_at).getTime();
              return db - da;
            });
            setLatestRx(valid[0]);
          }
        }
      } catch { /* no prescriptions */ }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [customerId]);

  if (loading || rxCount === 0) return null;

  const handleSwitchToRx = () => {
    store.setSaleType('prescription_order');
    if (latestRx) {
      const rx: Prescription = {
        prescriptionId: latestRx.prescriptionId || latestRx.prescription_id || latestRx._id,
        prescriptionNumber: latestRx.prescriptionNumber || latestRx.prescription_number || '',
        patientId: latestRx.patientId || latestRx.patient_id || customerId,
        source: latestRx.source || 'TESTED_AT_STORE',
        optometristName: latestRx.optometristName || latestRx.optometrist_name || '',
        rightEye: {
          sphere: parseFloat(latestRx.rightEye?.sph || latestRx.right_eye?.sph || latestRx.rightEye?.sphere || '0'),
          cylinder: parseFloat(latestRx.rightEye?.cyl || latestRx.right_eye?.cyl || latestRx.rightEye?.cylinder || '0'),
          axis: Number(latestRx.rightEye?.axis || latestRx.right_eye?.axis || 180),
          add: parseFloat(latestRx.rightEye?.add || latestRx.right_eye?.add || '0'),
          pd: latestRx.rightEye?.pd || latestRx.right_eye?.pd || undefined,
        },
        leftEye: {
          sphere: parseFloat(latestRx.leftEye?.sph || latestRx.left_eye?.sph || latestRx.leftEye?.sphere || '0'),
          cylinder: parseFloat(latestRx.leftEye?.cyl || latestRx.left_eye?.cyl || latestRx.leftEye?.cylinder || '0'),
          axis: Number(latestRx.leftEye?.axis || latestRx.left_eye?.axis || 180),
          add: parseFloat(latestRx.leftEye?.add || latestRx.left_eye?.add || '0'),
          pd: latestRx.leftEye?.pd || latestRx.left_eye?.pd || undefined,
        },
        lensRecommendation: latestRx.lensRecommendation || latestRx.lens_recommendation || '',
        coatingRecommendation: latestRx.coatingRecommendation || latestRx.coating_recommendation || '',
        remarks: latestRx.remarks || '',
        testDate: latestRx.testDate || latestRx.test_date || '',
        validityMonths: latestRx.validityMonths || latestRx.validity_months || 12,
        status: 'ACTIVE',
      } as any;
      store.setPrescription(rx);
    }
  };

  return (
    <div className="mt-2 bg-blue-50 border border-blue-200 rounded-lg p-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-full bg-blue-500 text-white flex items-center justify-center">
            <Eye className="w-4 h-4" />
          </div>
          <div>
            <p className="text-sm font-semibold text-blue-800">
              {rxCount} Valid Prescription{rxCount > 1 ? 's' : ''} Available
            </p>
            <p className="text-xs text-blue-600">
              {latestRx?.optometristName || latestRx?.optometrist_name
                ? `By ${latestRx.optometristName || latestRx.optometrist_name}`
                : 'From eye test'}
              {latestRx?.testDate || latestRx?.test_date
                ? ` \u00B7 ${new Date(latestRx.testDate || latestRx.test_date).toLocaleDateString('en-IN')}`
                : ''}
            </p>
          </div>
        </div>
        {store.sale_type !== 'prescription_order' && (
          <button
            onClick={handleSwitchToRx}
            className="text-xs font-semibold text-gray-900 bg-blue-600 hover:bg-blue-700 px-3 py-1.5 rounded-lg transition-colors"
          >
            Use Rx {'\u2192'} Prescription Order
          </button>
        )}
        {store.sale_type === 'prescription_order' && !store.prescription && (
          <button
            onClick={handleSwitchToRx}
            className="text-xs font-semibold text-blue-700 bg-blue-100 hover:bg-blue-200 px-3 py-1.5 rounded-lg transition-colors"
          >
            Attach Latest Rx
          </button>
        )}
      </div>
    </div>
  );
}

function CustomerHistory({ customerId }: { customerId: string }) {
  const [orders, setOrders] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await orderApi.getOrders({ customer_id: customerId, page_size: 5 } as any);
        if (!cancelled) setOrders((result?.orders || result || []).slice(0, 5));
      } catch { /* no history available */ }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [customerId]);

  if (loading) return null;
  if (orders.length === 0) return <p className="text-xs text-gray-500 mt-2 italic">No previous orders found</p>;

  return (
    <div className="mt-2 bg-white border border-gray-200 rounded-lg p-3">
      <p className="text-[10px] font-medium text-gray-500 uppercase tracking-wide mb-1.5">Recent Purchases</p>
      <div className="space-y-1">
        {orders.map((o: any, i: number) => {
          const date = o.createdAt || o.created_at || o.order_date;
          const ago = date ? getTimeAgo(new Date(date)) : '';
          const items = o.items?.map((item: any) => item.productName || item.product_name || item.name).filter(Boolean).join(', ') || '';
          return (
            <div key={o.order_id || o._id || i} className="flex items-center gap-2 text-xs">
              <span className="text-gray-500 w-16 flex-shrink-0">{ago}</span>
              <span className="text-gray-700 truncate flex-1">{items || o.orderNumber || 'Order'}</span>
              <span className="text-gray-500 flex-shrink-0 font-medium">{'\u20B9'}{Math.round(o.grandTotal || o.grand_total || 0).toLocaleString('en-IN')}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function getTimeAgo(date: Date): string {
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const days = Math.floor(diffMs / (1000 * 60 * 60 * 24));
  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}yr ago`;
}

// ============================================================================
// Salesperson picker — required attribution for the incentive engine.
// Lists active users for the current store; starts empty (no default to
// the logged-in user) so attribution is a conscious choice.
function SalespersonPicker() {
  const store = usePOSStore();
  const { user } = useAuth();
  const [people, setPeople] = useState<Array<{ id: string; name: string }>>([]);
  const [loading, setLoading] = useState(false);

  // Only manager-tier (Store Manager and up) may attribute a sale to ANOTHER
  // salesperson. Everyone below Store Manager (sales staff / cashier /
  // optometrist / workshop) is auto-attributed to themselves -- no picker.
  const MANAGER_TIER = ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER', 'STORE_MANAGER'];
  const canPick = (user?.roles || []).some((r: string) => MANAGER_TIER.includes(r));
  const selfId = user?.id || (user as any)?.user_id || '';
  const selfName =
    (user as any)?.name || (user as any)?.full_name || (user as any)?.username || 'You';

  // Below-manager: lock the salesperson to the logged-in user (no choice).
  useEffect(() => {
    if (!canPick && selfId && store.salesperson_id !== selfId) {
      store.setSalesperson(selfId, selfName);
    }
  }, [canPick, selfId, selfName, store.salesperson_id]);

  // Manager-tier only: load the store's sales-floor users for the dropdown.
  useEffect(() => {
    if (!canPick) return;
    const sid = store.store_id || user?.activeStoreId;
    if (!sid) return;
    let cancelled = false;
    setLoading(true);
    // Sales-attributable roles only. SUPERADMIN/ADMIN/AREA_MANAGER are
    // cross-store and never on the shop floor; ACCOUNTANT is back-office;
    // OPTOMETRIST runs the exam chamber not the till. Anyone else is a
    // future role we'll add by request.
    adminStoreApi
      .getStoreUsers(sid, {
        roles: ['STORE_MANAGER', 'SALES_STAFF', 'OPTICIAN', 'CASHIER'],
        activeOnly: true,
      })
      .then((r: any) => {
        if (cancelled) return;
        const list = (r?.users || r || []) as any[];
        const mapped = list
          .map((u) => ({
            id: u.user_id || u.id || u._id || u.username,
            name: u.name || u.full_name || u.username || u.user_id,
          }))
          .filter((u) => u.id);
        setPeople(mapped);
      })
      .catch(() => setPeople([]))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [canPick, store.store_id, user?.activeStoreId]);

  // Below Store Manager: no picker -- the sale is auto-attributed to the
  // logged-in user (set by the effect above). Read-only display.
  if (!canPick) {
    return (
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">Salesperson</label>
        <div className="w-full px-3 py-2.5 border-2 border-gray-200 rounded-xl text-sm bg-gray-50 text-gray-700">
          {selfName} <span className="text-gray-400">(you)</span>
        </div>
      </div>
    );
  }

  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-2">
        Salesperson <span className="text-red-500">*</span>
      </label>
      <select
        aria-label="Select salesperson"
        value={store.salesperson_id}
        onChange={(e) => {
          const p = people.find((x) => x.id === e.target.value);
          store.setSalesperson(e.target.value, p?.name || '');
        }}
        className="w-full px-3 py-2.5 border-2 border-gray-300 rounded-xl text-sm bg-white"
      >
        <option value="">{loading ? 'Loading staff…' : '— Select salesperson —'}</option>
        {people.map((p) => (
          <option key={p.id} value={p.id}>{p.name}</option>
        ))}
      </select>
      {!store.salesperson_id && (
        <p className="text-xs text-gray-500 mt-1">Required — pick who is handling this sale.</p>
      )}
    </div>
  );
}

// STEP 1: Customer
// ============================================================================
// ============================================================================
// BILL-TO-MEMBER P1 -- mandatory member picker for a registered account.
// ============================================================================
// Every order bills a MEMBER, never the bare account. When an account is
// selected the Primary member auto-selects (one click for single-member
// accounts). For a multi-member family the operator must pick WHO is being
// billed before continuing (the Continue gate enforces store.patient is set).
// The Primary / account holder is badged + listed first.
function MemberSelect() {
  const store = usePOSStore();
  const customerId = store.customer?.id ? String(store.customer.id) : '';
  const [members, setMembers] = useState<any[]>(
    Array.isArray((store.customer as any)?.patients) ? (store.customer as any).patients : [],
  );

  // If the selected-customer object didn't carry patients[] (e.g. created
  // inline, recalled from a held bill, or a thin search row), fetch the full
  // account so the member list is complete + a Primary can auto-select.
  useEffect(() => {
    let cancelled = false;
    const onCust = Array.isArray((store.customer as any)?.patients)
      ? (store.customer as any).patients
      : null;
    if (onCust && onCust.length > 0) {
      setMembers(onCust);
      return;
    }
    if (!customerId || customerId.startsWith('walkin-')) return;
    (async () => {
      try {
        const full: any = await customerApi.getCustomer(customerId);
        const pts = full?.patients || full?.customer?.patients || [];
        if (!cancelled && Array.isArray(pts)) setMembers(pts);
      } catch {
        /* fail-soft: leave whatever we have; gate still requires a member */
      }
    })();
    return () => { cancelled = true; };
  }, [customerId]);

  // Auto-select the Primary the moment we have members and none is chosen yet.
  useEffect(() => {
    if (store.patient) return;
    const primary = choosePrimaryPatient(members);
    if (primary) store.setPatient(toPosPatient(primary, customerId) as any);
  }, [members, store.patient, customerId]);

  const ordered = useMemo(() => sortMembersPrimaryFirst(members), [members]);

  // Single member (or none yet) -> no picker; the auto-select handles it.
  if (ordered.length <= 1) return null;

  const selectedId = store.patient?.id ? String(store.patient.id) : '';

  return (
    <div className="mt-3">
      <label className="block text-xs font-medium text-gray-700 mb-1.5">
        Billing to (family member) <span className="text-bv-red-600">*</span>
      </label>
      <div className="grid grid-cols-2 gap-2">
        {ordered.map((p: any) => {
          const pid = String(p?.patient_id || p?.id || p?.name || '');
          const isSel = pid === selectedId;
          const isPrimary = !!p?.is_primary || (p?.relation || '').toLowerCase() === 'self';
          return (
            <button
              key={pid}
              type="button"
              onClick={() => store.setPatient(toPosPatient(p, customerId) as any)}
              className={`text-left p-2.5 rounded-lg border-2 transition-all ${
                isSel ? 'border-bv-red-600 bg-bv-red-50' : 'border-gray-200 hover:border-gray-300 bg-white'
              }`}
            >
              <p className="text-sm font-medium text-gray-900 truncate">
                {p?.name || 'Member'}
                {isPrimary && <span className="ml-2 align-middle text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded bg-amber-50 text-amber-700">Primary</span>}
              </p>
              <p className="text-xs text-gray-500 truncate">{p?.relation || 'Family'}{p?.dob ? ` · ${p.dob}` : ''}</p>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function StepCustomer() {
  const store = usePOSStore();
  const [showAddCustomerModal, setShowAddCustomerModal] = useState(false);

  const handleSaveCustomer = async (customerData: CustomerFormData) => {
    // ONE shared builder maps the form onto the canonical CustomerCreate payload
    // (same as the Customers page + Clinical intake) so every door produces the
    // identical record — incl. dob / anniversary / consents that were dropped here
    // before.
    const payload = buildCustomerCreatePayload(customerData);
    const r = await customerApi.createCustomer(payload as any);
    const custId = r?.customer_id || r?.id || `new-${Date.now()}`;
    // Carry the server-returned patients[] (incl. the auto-seeded Primary, with
    // real patient_ids + is_primary) onto the selected customer so MemberSelect
    // can render the family + the Primary auto-selects.
    const returnedPatients = Array.isArray(r?.patients) ? r.patients : [];
    store.setCustomer({
      id: custId,
      name: customerData.fullName,
      phone: customerData.mobileNumber,
      email: customerData.email,
      customerType: customerData.customerType,
      patients: returnedPatients,
    } as any);
    // BILL-TO-MEMBER P1: default-select the Primary (account holder) member so a
    // single-member new account is one click; MemberSelect lets multi-member
    // families switch.
    const primary = choosePrimaryPatient(returnedPatients);
    if (primary) {
      store.setPatient(toPosPatient(primary, custId) as any);
    }
    setShowAddCustomerModal(false);
  };

  const isWalkin = store.customer?.id?.toString().startsWith('walkin-') || store.customer?.name === 'Walk-in Customer';

  useEffect(() => {
    if (isWalkin && store.sale_type === 'prescription_order') {
      store.setSaleType('quick_sale');
    }
  }, [isWalkin]);

  return (
    <div className="w-full max-w-5xl mx-auto space-y-6">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">Sale Type</label>
        <div className="grid grid-cols-2 gap-3">
          {([
            { id: 'quick_sale' as SaleType, label: 'Quick Sale', desc: 'Frames, sunglasses, accessories -- immediate delivery', icon: Zap, blocked: false },
            { id: 'prescription_order' as SaleType, label: 'Prescription Order', desc: isWalkin ? 'Register customer first for Rx orders' : 'Frame + lens with Rx -- workshop job created', icon: Eye, blocked: isWalkin },
          ]).map(opt => (
            <button key={opt.id} onClick={() => { if (!opt.blocked) store.setSaleType(opt.id); }}
              title={opt.blocked ? 'Select a registered customer for prescription orders' : ''}
              className={`flex items-start gap-3 p-4 rounded-xl border-2 text-left transition-all ${
                opt.blocked ? 'border-gray-200 bg-white opacity-50 cursor-not-allowed' :
                store.sale_type === opt.id ? 'border-bv-red-600 bg-bv-red-50' : 'border-gray-200 hover:border-gray-300'}`}>
              <div className={`w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0 ${
                opt.blocked ? 'bg-gray-100 text-gray-500' :
                store.sale_type === opt.id ? 'bg-bv-red-600 text-white' : 'bg-gray-100 text-gray-500'}`}>
                <opt.icon className="w-5 h-5" />
              </div>
              <div>
                <p className={`font-semibold ${opt.blocked ? 'text-gray-500' : 'text-gray-900'}`}>{opt.label}</p>
                <p className={`text-xs mt-0.5 ${opt.blocked ? 'text-red-400' : 'text-gray-500'}`}>{opt.desc}</p>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Salesperson — required, conscious attribution for incentives.
          No auto-default; must be chosen before advancing past step 1. */}
      <SalespersonPicker />

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">Customer</label>
        {store.customer ? (
          <>
          <div className={`${isWalkin ? 'bg-white border-gray-200' : 'bg-bv-red-50 border-bv-red-600'} border rounded-xl p-4 flex items-center justify-between`}>
            <div className="flex items-center gap-3">
              <div className={`w-10 h-10 rounded-full ${isWalkin ? 'bg-gray-500 text-white' : 'bg-bv-red-700 text-white'} flex items-center justify-center font-semibold`}>{store.customer.name?.charAt(0)?.toUpperCase() || 'W'}</div>
              <div>
                <p className="font-semibold text-gray-900">{store.customer.name}</p>
                {/* gray-600 (not -500): on the bv-red-50 selected-customer card
                    gray-500 fell below WCAG AA (~4.0:1). gray-600 is ~6.2:1 on
                    that pink bg and still fine on the white walk-in card. */}
                <p className="text-sm text-gray-600">{store.customer.phone || 'No phone'}</p>
                {isWalkin && <p className="text-xs text-amber-600 mt-0.5">Walk-in -- Quick Sale only</p>}
                {store.patient && <p className="text-xs text-bv-red-700 mt-0.5 font-medium">Patient: {store.patient.name}<span className="text-gray-500 font-normal"> · billed to {store.customer.name}</span></p>}
              </div>
            </div>
            <button onClick={() => startTransition(() => store.setCustomer(null))} className="text-sm text-gray-600 hover:text-gray-800 px-3 py-1 border border-gray-200 rounded-lg">Change</button>
          </div>
          {!isWalkin && <MemberSelect />}
          {!isWalkin && <CustomerCardWithLoyalty />}
          {!isWalkin && <RxAvailableBadge customerId={store.customer.id} customerName={store.customer.name} />}
          {!isWalkin && <CustomerHistory customerId={store.customer.id} />}
          </>
        ) : (
          <>
            <AutoSearch<CustomerSearchHit>
              fetchResults={async (q, sid) => {
                try {
                  const res = await customerApi.getCustomers({ search: q, storeId: sid, limit: 8 });
                  const customers = (res as any)?.customers || (res as any) || [];
                  return buildCustomerSearchHits(customers, q);
                } catch { return []; }
              }}
              maxResults={10}
              renderItem={(hit) => {
                // Hierarchy: a hit.kind === 'account' is the master record (the
                // ACCOUNT); a hit.kind === 'patient' is a family-member record
                // nested UNDER it. Per owner terminology, outside the clinical
                // module those members are "Customer" (not "Patient") \u2014 the
                // internal 'patient' discriminator stays (shared with clinical),
                // only the POS label + indent change. buildCustomerSearchHits
                // already emits the account immediately before its members, so
                // indenting members renders the account -> customer tree.
                const isMember = hit.kind === 'patient';
                const c: any = hit.customer || {};
                const initial = (hit.displayName || '?').charAt(0).toUpperCase();
                // Cleaner card chips (design pos.jsx CustomerPicker) \u2014 rendered
                // ONLY from data actually present on the search doc; no mock /
                // placeholder values, no extra API calls.
                const createdRaw = c.created_at || c.createdAt || c.registered_at;
                let since: string | null = null;
                if (createdRaw) {
                  const d = new Date(createdRaw);
                  if (!isNaN(d.getTime())) since = d.toLocaleDateString('en-IN', { year: 'numeric', month: 'short' });
                }
                const tier = c.loyalty_tier || c.tier;
                const pts = c.loyalty_points ?? c.loyaltyPoints;
                const wallet = c.store_credit_balance ?? c.store_credit ?? c.wallet_balance ?? c.credit_balance;
                const isB2B = (c.customer_type || c.customerType) === 'B2B';
                const hasPts = typeof pts === 'number' && pts > 0;
                const hasWallet = typeof wallet === 'number' && wallet > 0;
                const hasChips = !!(tier || since || hasWallet || isB2B || hasPts);
                return (
                  <div className={`flex items-start gap-3 ${isMember ? 'pl-3.5 ml-1.5 border-l-2 border-gray-200' : ''}`}>
                    <div className={`rounded-full flex items-center justify-center font-bold text-white flex-shrink-0 ${isMember ? 'w-7 h-7 text-xs bg-blue-600' : 'w-9 h-9 text-sm bg-bv-red-700'}`}>{initial}</div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-900 truncate">
                        {hit.displayName}
                        <span className={`ml-2 align-middle text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded ${isMember ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-600'}`}>{isMember ? 'Customer' : 'Account'}</span>
                      </p>
                      <p className="text-xs text-gray-500 truncate font-mono">
                        {hit.phone || 'No phone'}
                        {isMember ? ` \u00B7 under ${hit.accountName}` : (c.city ? ` \u00B7 ${c.city}` : '')}
                      </p>
                      {hasChips && (
                        <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
                          {isB2B && <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-600">B2B</span>}
                          {tier && <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-700">{String(tier)}</span>}
                          {!tier && hasPts && <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-700">{pts.toLocaleString('en-IN')} pts</span>}
                          {since && <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-500">Since {since}</span>}
                          {hasWallet && <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-blue-50 text-blue-700">Wallet {'\u20B9'}{Math.round(wallet).toLocaleString('en-IN')}</span>}
                        </div>
                      )}
                    </div>
                  </div>
                );
              }}
              onSelect={(hit) => {
                const c = hit.customer;
                startTransition(() => {
                  store.setCustomer({
                    ...c,
                    id: c.customer_id || c._id || c.id,
                    name: c.name || c.customer_name || c.full_name || 'Customer',
                    phone: c.phone || c.mobile || '',
                  } as any);
                  // BILL-TO-MEMBER P1: setCustomer resets patient to null. Set
                  // the billed member AFTER.
                  //  - a 'patient' hit IS a member -> bill to that member.
                  //  - an 'account' hit -> default-select the account's Primary
                  //    member so single-member accounts (the ~90% case) are one
                  //    click; multi-member accounts still land on the Primary and
                  //    the operator can switch via the member picker below.
                  const cid = c.customer_id || c._id || c.id || '';
                  if (hit.kind === 'patient' && hit.patient) {
                    const p = hit.patient;
                    store.setPatient(toPosPatient(p, cid) as any);
                  } else {
                    const primary = choosePrimaryPatient((c as any).patients);
                    if (primary) store.setPatient(toPosPatient(primary, cid) as any);
                  }
                });
              }}
              getKey={(hit) => hit.key}
              placeholder="Search by phone number or name..."
              autoFocus
              clearOnSelect
              emptyMessage="No customers found"
            />
            <div className="mt-3 flex gap-4">
              <button onClick={() => setShowAddCustomerModal(true)} className="flex items-center gap-2 text-sm text-bv-red-600 hover:text-bv-red-700 font-medium"><Plus className="w-4 h-4" /> Create new customer</button>
              <button onClick={async () => {
                try {
                  const r = await customerApi.createCustomer({ name: 'Walk-in Customer', mobile: '0000000000', customer_type: 'B2C' } as any);
                  store.setCustomer({ id: r?.customer_id || r?.id || `walkin-${Date.now()}`, name: 'Walk-in Customer', phone: '0000000000', email: '', customerType: 'B2C' } as any);
                } catch {
                  store.setCustomer({ id: `walkin-${Date.now()}`, name: 'Walk-in Customer', phone: '', email: '', customerType: 'B2C' } as any);
                }
                store.setSaleType('quick_sale');
              }}
                className="flex items-center gap-2 text-sm text-gray-500 hover:text-gray-700"><User className="w-4 h-4" /> Walk-in (Quick Sale only)</button>
            </div>
          </>
        )}
      </div>

      <AddCustomerModal
        isOpen={showAddCustomerModal}
        onClose={() => setShowAddCustomerModal(false)}
        onSave={handleSaveCustomer}
      />
    </div>
  );
}

// ============================================================================
// STEP 2: Prescription
// ============================================================================
type RxSource = 'last' | 'fresh' | 'external' | 'none';

function StepPrescription({ onShowModal, onShowNew, onAccessoryOnlyChange }: { onShowModal: () => void; onShowNew: () => void; onAccessoryOnlyChange?: (v: boolean) => void }) {
  const store = usePOSStore();
  const [recentRx, setRecentRx] = useState<any[]>([]);
  const [rxLoading, setRxLoading] = useState(false);
  // Rx source-gating (additive): the existing Rx UI (recent list / browse-all /
  // new-Rx / validation / expiry gate / CL exemption) stays exactly as-is, but
  // it is now revealed only AFTER the operator picks a source — so the matrix /
  // flags don't appear cold. This is purely an initial empty-state in front of
  // the existing surface; it removes nothing.
  const [rxSource, setRxSource] = useState<RxSource | null>(null);

  const lookupId = store.patient?.id || store.customer?.id;
  // Reset the picked source back to the empty-state when the customer/patient
  // changes, so the accessory note can't linger for a different customer while
  // the parent's nav guard has already reset.
  useEffect(() => {
    setRxSource(null);
    onAccessoryOnlyChange?.(false);
    // onAccessoryOnlyChange is a stable setState dispatcher from the parent.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lookupId]);
  useEffect(() => {
    if (!lookupId || store.prescription) return;
    let cancelled = false;
    setRxLoading(true);
    (async () => {
      try {
        const result = await prescriptionApi.getPrescriptions(lookupId);
        const list = result?.prescriptions || result || [];
        if (!cancelled && Array.isArray(list)) {
          const now = new Date();
          const valid = list.filter((rx: any) => {
            const testDate = new Date(rx.testDate || rx.test_date || rx.created_at);
            const months = rx.validityMonths || rx.validity_months || 12;
            const expiry = new Date(testDate);
            expiry.setMonth(expiry.getMonth() + months);
            return now < expiry;
          }).sort((a: any, b: any) => {
            const da = new Date(a.testDate || a.test_date || a.created_at).getTime();
            const db = new Date(b.testDate || b.test_date || b.created_at).getTime();
            return db - da;
          });
          setRecentRx(valid.slice(0, 3));
        }
      } catch { /* ignore */ }
      if (!cancelled) setRxLoading(false);
    })();
    return () => { cancelled = true; };
  }, [lookupId, store.prescription]);

  const attachRx = (rx: any) => {
    const mapped: Prescription = {
      prescriptionId: rx.prescriptionId || rx.prescription_id || rx._id,
      prescriptionNumber: rx.prescriptionNumber || rx.prescription_number || '',
      patientId: rx.patientId || rx.patient_id || lookupId || '',
      source: rx.source || 'TESTED_AT_STORE',
      optometristName: rx.optometristName || rx.optometrist_name || '',
      rightEye: {
        sphere: parseFloat(rx.rightEye?.sph || rx.right_eye?.sph || rx.rightEye?.sphere || '0'),
        cylinder: parseFloat(rx.rightEye?.cyl || rx.right_eye?.cyl || rx.rightEye?.cylinder || '0'),
        axis: Number(rx.rightEye?.axis || rx.right_eye?.axis || 180),
        add: parseFloat(rx.rightEye?.add || rx.right_eye?.add || '0'),
        pd: rx.rightEye?.pd || rx.right_eye?.pd || undefined,
      },
      leftEye: {
        sphere: parseFloat(rx.leftEye?.sph || rx.left_eye?.sph || rx.leftEye?.sphere || '0'),
        cylinder: parseFloat(rx.leftEye?.cyl || rx.left_eye?.cyl || rx.leftEye?.cylinder || '0'),
        axis: Number(rx.leftEye?.axis || rx.left_eye?.axis || 180),
        add: parseFloat(rx.leftEye?.add || rx.left_eye?.add || '0'),
        pd: rx.leftEye?.pd || rx.left_eye?.pd || undefined,
      },
      lensRecommendation: rx.lensRecommendation || rx.lens_recommendation || '',
      coatingRecommendation: rx.coatingRecommendation || rx.coating_recommendation || '',
      remarks: rx.remarks || '',
      testDate: rx.testDate || rx.test_date || '',
      validityMonths: rx.validityMonths || rx.validity_months || 12,
      status: 'ACTIVE',
    } as any;
    store.setPrescription(mapped);
  };

  // C5-A: owner-gated auto-attach. Fires only when the flag is on, nothing is
  // attached yet, the fetch has settled, and EXACTLY ONE valid Rx exists (so an
  // ambiguous multi-Rx customer still falls to manual choice). Separate effect —
  // declared after attachRx so there is no forward reference; re-runs once
  // recentRx settles, then the store.prescription guard makes it idempotent.
  useEffect(() => {
    if (!POS_AUTO_ATTACH_SINGLE_RX) return;
    if (rxLoading || store.prescription) return;
    if (recentRx.length === 1) attachRx(recentRx[0]);
    // attachRx is a stable closure over lookupId/store; deps below cover the
    // trigger inputs without re-binding it each render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recentRx, rxLoading, store.prescription]);

  const fmtPower = (v: any) => {
    const n = parseFloat(v);
    if (!n || isNaN(n)) return '0.00';
    return n >= 0 ? `+${n.toFixed(2)}` : n.toFixed(2);
  };

  // An already-attached Rx always shows the selected panel (source-gating is
  // only an entry empty-state; it never hides an attached Rx).
  if (store.prescription) {
    return (
      <div className="w-full max-w-5xl mx-auto space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-gray-900">Selected Prescription</h3>
          <button onClick={onShowModal} className="text-sm text-bv-red-600 hover:text-bv-red-700 font-medium">Change</button>
        </div>
        <PrescriptionPanel prescription={store.prescription} patientName={store.patient?.name || store.customer?.name} readOnly />
        <div className="bg-green-50 border border-green-200 rounded-lg p-3 flex items-center gap-2 text-sm text-green-700">
          <CheckCircle className="w-4 h-4" /> Prescription attached -- you can now select lenses
        </div>
      </div>
    );
  }

  // Pick a source. fresh/external route straight into the existing new-Rx form
  // (external = an Rx from an outside doctor, transcribed/uploaded via the same
  // form; the form's source defaults to FROM_DOCTOR for non-optometrists).
  const pickSource = (s: RxSource) => {
    setRxSource(s);
    // 'none' = accessory/no-Rx path → let the step proceed (parent ungates the
    // nav guard). Any real Rx source clears the override so the Rx-attached gate
    // applies again. Server-side per-line Rx validation is unaffected.
    onAccessoryOnlyChange?.(s === 'none');
    if (s === 'fresh' || s === 'external') onShowNew();
  };
  const srcBtn = (id: RxSource, label: string) => (
    <button
      type="button"
      onClick={() => pickSource(id)}
      aria-pressed={rxSource === id}
      className={'btn sm' + (rxSource === id ? ' primary' : '')}
    >
      {label}
    </button>
  );

  return (
    <div className="w-full max-w-5xl mx-auto space-y-6">
      <div><h3 className="font-semibold text-gray-900 mb-1">Prescription</h3><p className="text-sm text-gray-500">Pick a source to begin — the Rx surface fills in below.</p></div>

      {/* Source picker — always visible. */}
      <div className="flex flex-wrap gap-2">
        {srcBtn('last', 'Use last exam')}
        {srcBtn('fresh', '+ Fresh Rx')}
        {srcBtn('external', 'External (upload)')}
        {srcBtn('none', 'No Rx · accessory')}
      </div>

      {/* Empty state — before a source is chosen, nothing else shows. */}
      {rxSource === null && (
        <div className="border-2 border-dashed border-gray-200 rounded-xl p-8 text-center">
          <div className="w-12 h-12 rounded-full bg-gray-100 text-gray-400 flex items-center justify-center mx-auto mb-3 font-mono text-sm">Rx</div>
          <p className="font-semibold text-gray-700">No prescription selected yet</p>
          <p className="text-xs text-gray-500 mt-1 max-w-md mx-auto">
            Pick a source above — last exam, a fresh Rx, an uploaded external Rx, or skip for accessories — and the prescription surface fills in here.
          </p>
        </div>
      )}

      {/* Accessory-only — no Rx required. The operator can still pick another
          source to attach one; the products step does not require a lens. */}
      {rxSource === 'none' && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-sm text-gray-700">
          <strong className="text-gray-900">Accessory-only sale.</strong> No prescription required for this order — add frames or accessories in the next step. Pick another source above if the customer does need lenses.
        </div>
      )}

      {/* External-Rx context note. The new-Rx form (opened on selection) is the
          capture surface; this just frames it. */}
      {rxSource === 'external' && (
        <div className="bg-amber-50 border border-amber-300 rounded-lg p-3 text-xs text-amber-700">
          External prescription from an outside doctor — transcribe the values in the New Prescription form so the order can be reconciled against stock.
        </div>
      )}

      {/* Existing Rx surface — revealed once a non-accessory source is picked.
          Everything below is the original UI (recent valid Rx list, Browse All,
          New Prescription, no-Rx-found notice). Untouched behaviour. */}
      {(rxSource === 'last' || rxSource === 'fresh' || rxSource === 'external') && (<>
      {rxLoading && (
        <div className="text-sm text-gray-500 animate-pulse">Checking for prescriptions...</div>
      )}
      {!rxLoading && recentRx.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">Recent Prescriptions (from eye test)</p>
          {recentRx.map((rx, i) => {
            const re = rx.rightEye || rx.right_eye || {};
            const le = rx.leftEye || rx.left_eye || {};
            const testDate = rx.testDate || rx.test_date;
            return (
              <div key={rx.prescriptionId || rx.prescription_id || rx._id || i}
                className="flex items-center gap-4 p-3 bg-blue-50 border border-blue-200 rounded-lg hover:bg-blue-100 transition-colors">
                <div className="w-8 h-8 rounded-full bg-blue-500 text-white flex items-center justify-center flex-shrink-0">
                  <Eye className="w-4 h-4" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-900">
                    R: {fmtPower(re.sph || re.sphere)}/{fmtPower(re.cyl || re.cylinder)}{'\u00D7'}{re.axis || 180}
                    {' \u00B7 '}
                    L: {fmtPower(le.sph || le.sphere)}/{fmtPower(le.cyl || le.cylinder)}{'\u00D7'}{le.axis || 180}
                  </p>
                  <p className="text-xs text-gray-500">
                    {rx.optometristName || rx.optometrist_name ? `By ${rx.optometristName || rx.optometrist_name}` : 'Eye test'}
                    {testDate ? ` \u00B7 ${new Date(testDate).toLocaleDateString('en-IN')}` : ''}
                    {rx.source === 'TESTED_AT_STORE' && ' \u00B7 Tested at store'}
                  </p>
                </div>
                <button onClick={() => attachRx(rx)}
                  className="text-xs font-semibold text-gray-900 bg-blue-600 hover:bg-blue-700 px-3 py-1.5 rounded-lg transition-colors flex-shrink-0">
                  Attach
                </button>
              </div>
            );
          })}
        </div>
      )}

      <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
        <button onClick={onShowModal} className="flex items-start gap-3 p-4 rounded-xl border-2 border-gray-200 hover:border-bv-red-300 text-left">
          <div className="w-10 h-10 rounded-lg bg-blue-50 text-blue-600 flex items-center justify-center"><FileText className="w-5 h-5" /></div>
          <div><p className="font-semibold text-gray-900">Browse All Prescriptions</p><p className="text-xs text-gray-500 mt-0.5">View full prescription history</p></div>
        </button>
        <button onClick={onShowNew} className="flex items-start gap-3 p-4 rounded-xl border-2 border-gray-200 hover:border-bv-red-300 text-left">
          <div className="w-10 h-10 rounded-lg bg-green-50 text-green-600 flex items-center justify-center"><Plus className="w-5 h-5" /></div>
          <div><p className="font-semibold text-gray-900">New Prescription</p><p className="text-xs text-gray-500 mt-0.5">Enter a new Rx manually</p></div>
        </button>
      </div>
      {recentRx.length === 0 && !rxLoading && (
        <div className="bg-amber-50 border border-amber-300 rounded-lg p-3 flex items-center gap-2 text-sm text-amber-700">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" /> No prescriptions found. Enter manually or send customer for an eye test first.
        </div>
      )}
      </>)}
    </div>
  );
}

// ============================================================================
// STEP 3: Products
// ============================================================================
function StepProducts({ onOpenLensModal }: { onOpenLensModal: () => void }) {
  const store = usePOSStore();
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [showSuggestions, setShowSuggestions] = useState(true);
  const [blockMsg, setBlockMsg] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');
  const { data: products = [], isLoading } = useProducts({ search: debouncedSearch || undefined, category: categoryFilter || undefined, store_id: store.store_id || undefined });
  const categories = ['FRAMES', 'SUNGLASSES', 'RX_LENSES', 'CONTACT_LENSES', 'WRIST_WATCHES', 'SMARTWATCHES', 'ACCESSORIES'];

  const handleBarcodeScan = async (barcode: string) => {
    const code = (barcode || '').trim();
    if (!code) return;
    // Resolve the EXACT physical unit by its intake barcode (GET
    // /inventory/barcode/{code}), scoped to the active store. This is the real
    // scan path -- the old code searched the product TEXT index (which never
    // indexed the barcode field), so a genuine intake barcode matched nothing
    // and was silently dumped into the search box (a Fail-Loudly violation).
    try {
      const hit = await inventoryApi.searchByBarcode(code, store.store_id || '');
      // A foreign-store unit must NOT be quietly sold at this terminal.
      if (hit?.cross_store) {
        setBlockMsg(`Barcode ${code} belongs to another store's stock -- it cannot be sold here.`);
        setTimeout(() => setBlockMsg(null), 6000);
        return;
      }
      // Build a cart-ready product from the joined product master (the scan
      // endpoint joins `products` onto the `stock_units` row); fall back to the
      // unit's own fields if the join is absent.
      const p = hit?.product || {};
      const product = {
        product_id: p.product_id || hit?.product_id,
        name: p.name || p.model || hit?.product_name,
        sku: p.sku || hit?.sku,
        barcode: hit?.barcode || code,
        brand: p.brand,
        subbrand: p.subbrand || p.sub_brand,
        category: p.category || hit?.category,
        mrp: p.mrp,
        offer_price: p.offer_price ?? p.offerPrice,
        image_url: p.image_url,
      };
      if (product.product_id) {
        handleAddProduct(product);
        return;
      }
      // Hit with no resolvable product -- loud-fail rather than swallow it.
      setBlockMsg(`Barcode ${code} found but its product record is missing. Tell the manager.`);
      setTimeout(() => setBlockMsg(null), 6000);
    } catch {
      // 404 (or any error) = this barcode is not in stock. Fail loudly; add
      // NOTHING (do not dump the scanned value into the search box).
      setBlockMsg(`Barcode ${code} not found in stock. Check the item or search by name.`);
      setTimeout(() => setBlockMsg(null), 6000);
    }
  };

  const handleManualSearch = (q: string) => {
    startTransition(() => setDebouncedSearch(q));
  };

  const rxInput: PrescriptionInput | null = store.prescription ? {
    rightSphere: store.prescription.rightEye?.sphere ?? null,
    rightCylinder: store.prescription.rightEye?.cylinder ?? null,
    rightAxis: store.prescription.rightEye?.axis ?? null,
    rightAdd: store.prescription.rightEye?.add ?? null,
    leftSphere: store.prescription.leftEye?.sphere ?? null,
    leftCylinder: store.prescription.leftEye?.cylinder ?? null,
    leftAxis: store.prescription.leftEye?.axis ?? null,
    leftAdd: store.prescription.leftEye?.add ?? null,
  } : null;

  const handleAddProduct = (product: any) => {
    const mrp = product.mrp || 0;
    const offerPrice = product.offer_price || product.offerPrice || mrp;
    if (offerPrice > mrp && mrp > 0) {
      setBlockMsg(`BLOCKED: ${product.name} -- Offer Price (${fc(offerPrice)}) exceeds MRP (${fc(mrp)}). Contact HQ to fix pricing.`);
      setTimeout(() => setBlockMsg(null), 6000);
      return;
    }
    const finalPrice = offerPrice || mrp;
    if (!finalPrice || finalPrice <= 0 || isNaN(finalPrice)) {
      setBlockMsg(`BLOCKED: ${product.name} -- Invalid pricing (${fc(finalPrice)}). Contact HQ to fix.`);
      setTimeout(() => setBlockMsg(null), 6000);
      return;
    }
    setBlockMsg(null);
    startTransition(() => {
      store.addToCart({ product_id: product.product_id || product._id || product.id, name: product.name, sku: product.sku, barcode: product.barcode, brand: product.brand, subbrand: product.subbrand || product.sub_brand, category: product.category,
        unit_price: finalPrice, mrp, offer_price: offerPrice !== mrp ? offerPrice : undefined, quantity: 1,
        is_optical: ['FRAMES', 'RX_LENSES', 'CONTACT_LENSES', 'COLOUR_CONTACTS'].includes(product.category), image_url: product.image_url });
    });
  };

  return (
    <div className="space-y-4">
      {blockMsg && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 flex items-center gap-2 text-sm text-red-700">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" />
          <span className="flex-1">{blockMsg}</span>
          <button onClick={() => setBlockMsg(null)} className="text-red-400 hover:text-red-600" aria-label="Dismiss" title="Dismiss"><X className="w-3.5 h-3.5" /></button>
        </div>
      )}
      <div className="flex gap-3">
        <div className="flex-1">
          <BarcodeScanner onScan={handleBarcodeScan} onManualSearch={handleManualSearch} placeholder="Scan barcode or search products..." autoFocus />
        </div>
        {store.sale_type === 'prescription_order' && store.prescription && (
          <button onClick={onOpenLensModal} className="flex items-center gap-2 px-4 py-2 bg-purple-50 text-purple-700 border border-purple-200 rounded-lg hover:bg-purple-100 whitespace-nowrap text-sm font-medium">
            <Eye className="w-4 h-4" /> Add Lens (Manual)
          </button>
        )}
      </div>

      {store.sale_type === 'prescription_order' && rxInput && showSuggestions && (
        <div className="relative">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2 text-sm font-medium text-purple-700">
              <Sparkles className="w-4 h-4" /> Recommended Lenses (based on Rx)
              <span className="text-xs text-gray-500 font-normal">-- suggestions only, staff can override</span>
            </div>
            <button onClick={() => setShowSuggestions(false)} className="text-xs text-gray-500 hover:text-gray-700 px-2 py-1">Dismiss</button>
          </div>
          <LensSuggestionPanel
            prescriptionInput={rxInput}
            onSelect={(suggestion) => {
              store.addToCart({
                product_id: `lens-sug-${Date.now()}`,
                name: `${suggestion.lensType} -- ${suggestion.material}`,
                sku: `RX-${suggestion.material.replace(/[^a-zA-Z0-9]/g, '').toUpperCase()}-${Date.now().toString(36)}`,
                category: 'RX_LENSES',
                unit_price: suggestion.priceRange.min,
                mrp: suggestion.priceRange.max,
                quantity: 2,
                is_optical: true,
                linked_prescription_id: store.prescription?.id,
                lens_details: {
                  type: suggestion.lensType,
                  material: suggestion.material,
                  coatings: suggestion.coatings,
                },
              });
            }}
          />
        </div>
      )}

      <div className="flex gap-2 overflow-x-auto pb-1 items-center">
        <button onClick={() => setCategoryFilter('')} className={`px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap ${!categoryFilter ? 'bg-bv-red-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}>All</button>
        {categories.map(cat => (
          <button key={cat} onClick={() => setCategoryFilter(cat === categoryFilter ? '' : cat)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap ${categoryFilter === cat ? 'bg-bv-red-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}>
            {cat.replace(/_/g, ' ')}
          </button>
        ))}
        <div className="ml-auto flex gap-0.5 bg-gray-100 rounded-lg p-0.5">
          <button onClick={() => setViewMode('grid')} className={`p-1.5 rounded ${viewMode === 'grid' ? 'bg-white shadow-sm' : 'text-gray-500 hover:text-gray-700'}`} title="Grid view">
            <Package className="w-3.5 h-3.5" />
          </button>
          <button onClick={() => setViewMode('list')} className={`p-1.5 rounded ${viewMode === 'list' ? 'bg-white shadow-sm' : 'text-gray-500 hover:text-gray-700'}`} title="List view">
            <FileText className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {store.sale_type === 'prescription_order' && store.prescription && (
        <div className="bg-purple-50 border border-purple-200 rounded-lg p-3 flex items-center gap-3 text-sm">
          <Eye className="w-4 h-4 text-purple-600 flex-shrink-0" />
          <span className="text-purple-700 font-medium">Rx:</span>
          <span className="text-purple-500">OD {store.prescription.rightEye?.sphere}/{store.prescription.rightEye?.cylinder} {'\u00B7'} OS {store.prescription.leftEye?.sphere}/{store.prescription.leftEye?.cylinder}</span>
        </div>
      )}

      {isLoading ? (
        <div className="grid grid-cols-2 tablet:grid-cols-3 laptop:grid-cols-4 gap-3">
          {[...Array(8)].map((_, i) => <div key={i} className="bg-white rounded-xl border border-gray-200 p-3 animate-pulse"><div className="h-20 bg-gray-100 rounded-lg mb-2" /><div className="h-4 bg-gray-100 rounded w-3/4 mb-1" /><div className="h-3 bg-gray-100 rounded w-1/2" /></div>)}
        </div>
      ) : viewMode === 'list' ? (
        /* COMPACT LIST VIEW */
        <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
          <div className="divide-y divide-gray-100 max-h-[60vh] overflow-y-auto">
            {(products as any[]).map((product: any) => {
              const mrp = product.mrp || 0; const offer = product.offer_price || mrp; const hasDiscount = offer < mrp;
              const inCart = (store.cart || []).some(i => i.product_id === (product.product_id || product._id));
              const stock = product.stock ?? product.quantity ?? product.stock_available ?? null;
              const isOutOfStock = stock !== null && stock <= 0;
              const isLowStock = stock !== null && stock > 0 && stock <= 3;
              return (
                <button key={product.product_id || product._id} onClick={() => handleAddProduct(product)} disabled={inCart || isOutOfStock}
                  className={`w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-gray-100 transition-colors ${
                    isOutOfStock ? 'opacity-50 cursor-not-allowed bg-red-50' : inCart ? 'bg-green-50' : ''}`}>
                  <div className="w-10 h-10 bg-white rounded flex items-center justify-center flex-shrink-0">
                    {product.image_url ? <img src={product.image_url} alt="" className="h-8 w-auto object-contain" /> :
                    <Package className="w-4 h-4 text-gray-700" />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-900 truncate">{product.name}</p>
                    <p className="text-[10px] text-gray-500">{product.brand} {'\u00B7'} {product.sku}</p>
                  </div>
                  {stock !== null && (
                    <span className={`text-[10px] px-1.5 py-0.5 rounded flex-shrink-0 ${
                      isOutOfStock ? 'bg-red-100 text-red-600' : isLowStock ? 'bg-amber-100 text-amber-700' : 'text-gray-500'
                    }`}>{isOutOfStock ? 'Out' : isLowStock ? `${stock} left` : `\u00D7${stock}`}</span>
                  )}
                  <div className="text-right flex-shrink-0">
                    <span className="text-sm font-bold text-gray-900">{fc(offer)}</span>
                    {hasDiscount && <span className="text-[9px] text-gray-500 line-through ml-1">{fc(mrp)}</span>}
                  </div>
                  {inCart && <span className="text-[9px] px-1 py-0.5 bg-green-100 text-green-700 rounded flex-shrink-0">{'\u2713'}</span>}
                </button>
              );
            })}
          </div>
        </div>
      ) : (
        /* GRID VIEW */
        <div className="grid grid-cols-2 tablet:grid-cols-3 laptop:grid-cols-4 gap-3">
          {(products as any[]).map((product: any) => {
            const mrp = product.mrp || 0; const offer = product.offer_price || mrp; const hasDiscount = offer < mrp;
            const inCart = (store.cart || []).some(i => i.product_id === (product.product_id || product._id));
            const stock = product.stock ?? product.quantity ?? product.stock_available ?? null;
            const isLowStock = stock !== null && stock > 0 && stock <= 3;
            const isOutOfStock = stock !== null && stock <= 0;
            return (
              <button key={product.product_id || product._id} onClick={() => handleAddProduct(product)} disabled={inCart || isOutOfStock}
                className={`bg-white rounded-xl border text-left p-3 transition-all hover:shadow-md ${
                  isOutOfStock ? 'border-red-200 bg-red-50 opacity-60 cursor-not-allowed' :
                  inCart ? 'border-green-300 bg-green-50 opacity-70' : 'border-gray-200 hover:border-bv-red-300'}`}>
                <div className="h-16 bg-white rounded-lg mb-2 flex items-center justify-center relative">
                  {product.image_url ? <img src={product.image_url} alt="" className="h-14 w-auto object-contain" /> :
                  product.category === 'FRAMES' || product.category === 'SUNGLASSES' ? <Glasses className="w-8 h-8 text-gray-700" />
                  : product.category?.includes('WATCH') ? <Watch className="w-8 h-8 text-gray-700" /> : <Package className="w-8 h-8 text-gray-700" />}
                  {stock !== null && (
                    <span className={`absolute top-1 right-1 text-[9px] px-1 py-0.5 rounded font-medium ${
                      isOutOfStock ? 'bg-red-100 text-red-600' : isLowStock ? 'bg-amber-100 text-amber-700' : 'bg-green-50 text-green-600'
                    }`}>{isOutOfStock ? 'Out' : isLowStock ? `${stock} left` : `${stock}`}</span>
                  )}
                </div>
                <p className="text-xs font-semibold text-gray-900 truncate">{product.name}</p>
                <p className="text-[10px] text-gray-500 truncate">{product.brand} {'\u00B7'} {product.sku}</p>
                <div className="mt-1.5 flex items-baseline gap-1.5">
                  <span className="text-sm font-bold text-gray-900">{fc(offer)}</span>
                  {hasDiscount && <span className="text-[10px] text-gray-500 line-through">{fc(mrp)}</span>}
                </div>
                {inCart && <span className="inline-block mt-1 text-[10px] px-1.5 py-0.5 bg-green-100 text-green-700 rounded font-medium">In cart</span>}
                {isOutOfStock && <span className="inline-block mt-1 text-[10px] px-1.5 py-0.5 bg-red-100 text-red-600 rounded font-medium">Out of stock</span>}
              </button>
            );
          })}
        </div>
      )}
      {!isLoading && (products as any[]).length === 0 && (
        <div className="text-center py-12 text-gray-500">
          <Package className="w-12 h-12 mx-auto mb-2 opacity-50" />
          <p className="text-sm">No products found</p>
          {categoryFilter && debouncedSearch && <p className="text-xs mt-1">Search is filtered to <span className="font-medium">{categoryFilter.replace(/_/g, ' ')}</span>. <button onClick={() => setCategoryFilter('')} className="text-bv-red-600 hover:underline">Search all categories</button></p>}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// STEP 4: Review
// ============================================================================
function StepReview({ onOpenDiscount }: { onOpenDiscount: (item: CartLineItem) => void }) {
  const store = usePOSStore();
  const { user } = useAuth();
  const subtotal = store.getSubtotal(); const discount = store.getTotalDiscount();

  const taxBreakdown = useMemo(() => {
    // GST_PRICING_MODE (runtime, /health): mirrors posStore.getTax / getGrandTotal.
    //   INCLUSIVE (default): line_total is all-in; EXTRACT GST from within
    //     (taxable = gross/(1+rate); tax = gross-taxable).
    //   EXCLUSIVE (legacy): line_total is the pre-tax base; GST added on top
    //     (taxable = gross; tax = gross*rate).
    // Uses the DB-aware resolver (editable HSN/GST overrides). `rates` maps a
    // rate -> its taxable base so the HSN breakdown reconciles to the grand
    // total (which getGrandTotal also computes per-mode).
    const inclusive = isInclusivePricing();
    const cartFactor = 1 - (store.cart_discount_percent || 0) / 100;
    let totalTax = 0;
    const rates: Record<number, number> = {};
    for (const item of (store.cart || [])) {
      const rate = resolveGstRate(item.category, (item as any).hsn_code || (item as any).hsnCode);
      const itemGross = Math.round((item.line_total || 0) * cartFactor * 100) / 100;
      const itemTaxable = inclusive ? itemGross / (1 + rate / 100) : itemGross;
      totalTax += inclusive ? itemGross - itemTaxable : itemGross * (rate / 100);
      rates[rate] = (rates[rate] || 0) + itemTaxable;
    }
    Object.keys(rates).forEach((k) => { rates[+k] = Math.round(rates[+k] * 100) / 100; });
    return { totalTax: Math.round(totalTax * 100) / 100, rates };
  }, [store.cart, store.cart_discount_percent]);

  const total = store.getGrandTotal();

  return (
    <div className="w-full max-w-5xl mx-auto space-y-4">
      <h3 className="font-semibold text-gray-900">Order Review</h3>
      {store.customer && (
        <div className="bg-white rounded-lg p-3 flex items-center gap-3 text-sm">
          <User className="w-4 h-4 text-gray-500" /><span className="font-medium">{store.customer.name}</span><span className="text-gray-500">{store.customer.phone}</span>
          {store.sale_type === 'prescription_order' && <span className="ml-auto px-2 py-0.5 bg-purple-50 text-purple-600 rounded text-xs font-medium">Prescription Order</span>}
        </div>
      )}

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-white text-xs text-gray-500 uppercase">
            <tr><th className="text-left px-4 py-2">Item</th><th className="text-center px-2 py-2">Qty</th><th className="text-right px-2 py-2">MRP</th><th className="text-right px-2 py-2">Price</th><th className="text-right px-2 py-2">Disc</th><th className="text-center px-2 py-2">GST</th><th className="text-right px-4 py-2">Total</th><th className="w-8"></th></tr>
          </thead>
          <tbody>
            {(store.cart || []).map(item => {
              const gstRate = resolveGstRate(item.category, (item as any).hsn_code || (item as any).hsnCode);
              return (
              <tr key={item.id} className="border-t border-gray-200">
                <td className="px-4 py-3">
                  <p className="font-medium text-gray-900">{item.name}</p>
                  <p className="text-xs text-gray-500">{item.brand} {'\u00B7'} {item.sku}</p>
                  {item.lens_details && <p className="text-xs text-purple-500 mt-0.5">{item.lens_details.type} {'\u00B7'} {item.lens_details.coatings.join(', ')}</p>}
                  <input
                    placeholder="Item notes (PD, fitting, tint, coating...)"
                    defaultValue={(item as any).item_note || ''}
                    onBlur={(e) => store.setItemNote?.(item.id, e.target.value)}
                    className="mt-1 w-full text-[11px] px-2 py-1 bg-white border border-gray-200 rounded text-gray-700 placeholder:text-gray-400 focus:border-bv-red-300 focus:bg-white"
                  />
                </td>
                <td className="text-center px-2">
                  <div className="flex items-center justify-center gap-1">
                    <button onClick={() => store.updateQuantity(item.id, item.quantity - 1)} className="w-6 h-6 rounded bg-gray-100 text-xs hover:bg-gray-200">-</button>
                    <span className="w-6 text-center font-medium">{item.quantity}</span>
                    <button onClick={() => store.updateQuantity(item.id, item.quantity + 1)} className="w-6 h-6 rounded bg-gray-100 text-xs hover:bg-gray-200">+</button>
                  </div>
                </td>
                <td className="text-right px-2 text-gray-500">{fc(item.mrp)}</td>
                <td className="text-right px-2">{fc(item.unit_price)}</td>
                <td className="text-right px-2">
                  {item.offer_price && item.offer_price < item.mrp ? (
                    <span className="px-2 py-0.5 rounded text-xs bg-gray-100 text-gray-500 cursor-not-allowed" title="MRP > Offer Price: No further discount allowed">N/A</span>
                  ) : (
                    <button onClick={() => onOpenDiscount(item)}
                      className={`px-2 py-0.5 rounded text-xs ${item.discount_percent > 0 ? 'bg-green-50 text-green-700 font-medium' : 'bg-white text-gray-500 hover:bg-gray-100'}`}>
                      {item.discount_percent > 0 ? `${item.discount_percent}%` : 'Add'}
                    </button>
                  )}
                </td>
                <td className="text-center px-2 text-xs text-gray-500">{gstRate}%</td>
                <td className="text-right px-4 font-semibold">{fc(item.line_total)}</td>
                <td><button onClick={() => store.removeFromCart(item.id)} className="p-1 text-gray-500 hover:text-red-500" aria-label="Remove item" title="Remove item"><X className="w-4 h-4" /></button></td>
              </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <textarea value={store.cart_note} onChange={(e) => store.setCartNote(e.target.value)} placeholder="Order notes, fitting instructions..." className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm h-16 resize-none" />

      {/* Visufit measurement ID — shown for optical carts only. Optional;
          drives the per-staff Visufit-coverage gate in the incentive
          engine. Empty = no Visufit demo done for this order. */}
      {(store.cart || []).some((i) =>
        i.is_optical || ['FRAMES', 'RX_LENSES', 'CONTACT_LENSES', 'OPTICAL_LENS', 'COLOUR_CONTACTS'].includes(i.category)
      ) && (
        <div className="bg-white border border-gray-200 rounded-xl p-4 text-sm">
          <label className="font-medium text-gray-900 block mb-1">
            Visufit measurement ID <span className="text-xs font-normal text-gray-400">(optional)</span>
          </label>
          <p className="text-xs text-gray-500 mb-2">Enter the Visufit / Avataar measurement reference if a demo was done.</p>
          <input
            type="text"
            value={store.visufit_id}
            onChange={(e) => store.setVisufitId(e.target.value)}
            placeholder="e.g. VF-2026-04829"
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
          />
        </div>
      )}

      {/* Phase 6.7 — Order-level discount. Stacks on top of per-item
          discounts. Capped at the user's role discount cap. */}
      <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-2 text-sm">
        <div className="flex items-center justify-between">
          <div>
            <label className="font-medium text-gray-900">Overall Discount</label>
            <p className="text-xs text-gray-500">Applied to subtotal (after per-item discounts)</p>
          </div>
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={0}
              max={user?.discountCap ?? 10}
              step={0.5}
              value={store.cart_discount_percent || 0}
              onChange={(e) => {
                const pct = Math.max(0, Math.min(user?.discountCap ?? 10, parseFloat(e.target.value) || 0));
                store.setCartDiscount(pct);
              }}
              onFocus={(e) => e.target.select()}
              className="w-20 px-2 py-1 border border-gray-300 rounded text-sm text-right text-gray-900"
              placeholder="0"
            />
            <span className="text-sm text-gray-500">%</span>
          </div>
        </div>
        {store.cart_discount_percent > 0 && (
          <div className="pt-2 space-y-2 border-t border-gray-200">
            <input
              type="text"
              value={store.cart_discount_reason || ''}
              onChange={(e) => store.setCartDiscount(store.cart_discount_percent, e.target.value, store.cart_discount_approved_by || undefined)}
              placeholder="Reason (loyal customer, damaged box, festival offer...)"
              className="w-full px-2 py-1 border border-gray-300 rounded text-xs text-gray-900"
            />
            <div className="flex items-center justify-between text-xs">
              <span className="text-gray-500">Max allowed: {user?.discountCap ?? 10}% (role cap)</span>
              <span className="text-green-600 font-medium">-{fc(store.cart_discount_amount || 0)}</span>
            </div>
          </div>
        )}
      </div>

      {/* Phase 6.7 — Delivery scheduling (all sale types, not just Rx orders).
          Stores collect pickup/delivery prefs at order time so Workshop + the
          Orders list can sort by priority. */}
      <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-3 text-sm">
        <div>
          <label className="font-medium text-gray-900">Delivery / Collection</label>
          <p className="text-xs text-gray-500">Date, time window, and priority. Required for prescription orders.</p>
        </div>
        <div className="grid grid-cols-1 tablet:grid-cols-3 gap-3">
          <div>
            <label className="text-xs text-gray-500 block mb-1">Date</label>
            <input
              type="date"
              title="Delivery / collection date"
              value={store.delivery_date || ''}
              onChange={(e) => store.setDeliveryDate(e.target.value || null)}
              className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm text-gray-900"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">Time slot</label>
            <select
              title="Delivery time slot"
              value={store.delivery_time_slot || ''}
              onChange={(e) => store.setDeliveryTimeSlot(e.target.value || null)}
              className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm text-gray-900"
            >
              <option value="">Any time</option>
              <option value="10:00-12:00">10:00 – 12:00</option>
              <option value="12:00-14:00">12:00 – 14:00</option>
              <option value="14:00-16:00">14:00 – 16:00</option>
              <option value="16:00-18:00">16:00 – 18:00</option>
              <option value="18:00-20:00">18:00 – 20:00</option>
            </select>
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">Priority</label>
            <select
              title="Delivery priority"
              value={store.delivery_priority || 'NORMAL'}
              onChange={(e) => store.setDeliveryPriority(e.target.value as 'NORMAL' | 'EXPRESS' | 'URGENT')}
              className={`w-full px-2 py-1.5 border rounded text-sm font-medium ${
                store.delivery_priority === 'URGENT' ? 'border-red-300 text-red-700 bg-red-50' :
                store.delivery_priority === 'EXPRESS' ? 'border-orange-300 text-orange-700 bg-orange-50' :
                'border-gray-300 text-gray-700'
              }`}
            >
              <option value="NORMAL">Normal</option>
              <option value="EXPRESS">Express (+surcharge)</option>
              <option value="URGENT">Urgent (same day)</option>
            </select>
          </div>
        </div>
      </div>

      <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-2 text-sm">
        <div className="flex justify-between"><span className="text-gray-500">Subtotal</span><span>{fc(subtotal)}</span></div>
        {discount > 0 && <div className="flex justify-between text-green-600"><span>Discount</span><span>-{fc(discount)}</span></div>}
        {Object.entries(taxBreakdown.rates).map(([rate, taxable]) => {
          const r = Number(rate);
          const halfRate = r / 2;
          const tax = Math.round((taxable as number) * (r / 100) * 100) / 100;
          const cgst = Math.floor(tax * 100 / 2) / 100;
          const sgst = Math.round((tax - cgst) * 100) / 100;
          return (
            <div key={rate} className="space-y-1">
              <div className="flex justify-between text-gray-500"><span>CGST ({halfRate}%)</span><span>{fc(cgst)}</span></div>
              <div className="flex justify-between text-gray-500"><span>SGST ({halfRate}%)</span><span>{fc(sgst)}</span></div>
            </div>
          );
        })}
        <div className="border-t border-gray-200 pt-2 flex justify-between font-bold text-lg"><span>Grand Total</span><span className="text-bv-red-600">{fc(total)}</span></div>
      </div>

      {store.sale_type === 'prescription_order' && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={store.is_advance_payment} onChange={(e) => store.setAdvancePayment(e.target.checked)} className="rounded border-gray-300" />
            <span className="font-medium text-blue-700">Advance payment only</span><span className="text-blue-500 text-xs">(Balance on delivery)</span>
          </label>
        </div>
      )}
    </div>
  );
}

export default POSLayout;
