// ============================================================================
// IMS 2.0 - POS Layout (Wizard Orchestrator)
// ============================================================================
// 6-Step Wizard: Customer -> Prescription -> Products -> Review -> Payment -> Receipt
// Uses posStore (Zustand) for all state with localStorage persistence
// Sub-components extracted to: POSCart, POSPayment, POSReceipt, POSInvoice

import { useState, useEffect, useMemo, useCallback, startTransition } from 'react';
import {
  ShoppingCart, User, Eye, Package, CreditCard, CheckCircle,
  ChevronRight, ChevronLeft, Plus, X,
  Pause, Play, RotateCcw, AlertTriangle,
  Glasses, Watch, FileText, Zap, Sparkles,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { usePOSStore } from '../../stores/posStore';
import type { SaleType, POSStep, CartLineItem } from '../../stores/posStore';
import { useProducts } from '../../hooks/usePOSQueries';
import { customerApi, orderApi, prescriptionApi, productApi, workshopApi } from '../../services/api';
import type { Prescription } from '../../types';

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
import { AddCustomerModal, type CustomerFormData } from '../customers/AddCustomerModal';
import { CustomerCardWithLoyalty } from './CustomerCardWithLoyalty';
import { getGSTRateByCategory } from '../../constants/gst';
import type { PrescriptionInput } from '../../utils/lensAutoSuggest';

// Extracted sub-components
import { CartSidebar } from './POSCart';
import { StepPayment } from './POSPayment';
import { POSReceipt } from './POSReceipt';
import { StepComplete } from './POSInvoice';

// ============================================================================
// Constants
// ============================================================================

const STEPS: { id: POSStep; label: string; icon: typeof User }[] = [
  { id: 'customer', label: 'Customer', icon: User },
  { id: 'prescription', label: 'Prescription', icon: Eye },
  { id: 'products', label: 'Products', icon: Package },
  { id: 'review', label: 'Review', icon: ShoppingCart },
  { id: 'payment', label: 'Payment', icon: CreditCard },
  { id: 'complete', label: 'Complete', icon: CheckCircle },
];

const QUICK_STEPS: POSStep[] = ['customer', 'products', 'payment', 'complete'];

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

  // HIGH #6: Block POS access when no store is selected
  const activeStoreId = user?.activeStoreId || user?.storeIds?.[0] || '';
  if (!activeStoreId || activeStoreId === 'No store') {
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

  const [showPrescriptionModal, setShowPrescriptionModal] = useState(false);
  const [showNewPrescription, setShowNewPrescription] = useState(false);
  const [showLensModal, setShowLensModal] = useState(false);
  const [discountItem, setDiscountItem] = useState<CartLineItem | null>(null);
  const [showReceipt, setShowReceipt] = useState(false);
  const [holdConfirm, setHoldConfirm] = useState(false);
  const [showRecallPanel, setShowRecallPanel] = useState(false);
  const [showDayEnd, setShowDayEnd] = useState(false);
  const [showNewConfirm, setShowNewConfirm] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  // Phase 6.8 — workshop handoff modal. When an Rx order spawns a workshop
  // job, we hold the jobId here and open LensFittingFormModal so sales can
  // attach the physical fitting measurements before the Complete step.
  const [fittingJobId, setFittingJobId] = useState<string | null>(null);
  const [fittingSaving, setFittingSaving] = useState(false);
  const [fittingCoating, setFittingCoating] = useState<string>('');

  // Held bills from localStorage — cached to avoid repeated JSON.parse in render
  const [heldBillsCache, setHeldBillsCache] = useState<Array<{ id: string; customer: string; items: number; total: number; heldAt: string; state: any }>>([]);
  const refreshHeldBills = useCallback(() => {
    try { setHeldBillsCache(JSON.parse(localStorage.getItem('ims-held-bills') || '[]')); } catch { setHeldBillsCache([]); }
  }, []);
  useEffect(() => { refreshHeldBills(); }, [refreshHeldBills]);
  const getHeldBills = useCallback(() => heldBillsCache, [heldBillsCache]);

  const holdCurrentBill = () => {
    const bills = getHeldBills();
    bills.push({
      id: `hold-${Date.now()}`,
      customer: store.customer?.name || 'Walk-in',
      items: (store.cart || []).length,
      total: store.getGrandTotal(),
      heldAt: new Date().toISOString(),
      state: {
        sale_type: store.sale_type,
        customer: store.customer,
        patient: store.patient,
        prescription: store.prescription,
        cart: store.cart,
        cart_note: store.cart_note,
        payments: store.payments,
        is_advance_payment: store.is_advance_payment,
      },
    });
    localStorage.setItem('ims-held-bills', JSON.stringify(bills));
    refreshHeldBills();
    store.resetTransaction();
    setHoldConfirm(false);
  };

  const recallBill = (billId: string) => {
    const bills = getHeldBills();
    const bill = bills.find(b => b.id === billId);
    if (!bill) return;
    const s = bill.state;
    if (s.customer) store.setCustomer(s.customer);
    if (s.patient) store.setPatient(s.patient);
    if (s.prescription) store.setPrescription(s.prescription);
    if (s.sale_type) store.setSaleType(s.sale_type);
    if (s.cart_note) store.setCartNote(s.cart_note);
    if (s.is_advance_payment) store.setAdvancePayment(s.is_advance_payment);
    for (const item of (s.cart || [])) {
      store.addToCart(item);
    }
    for (const p of (s.payments || [])) {
      store.addPayment(p);
    }
    localStorage.setItem('ims-held-bills', JSON.stringify(bills.filter(b => b.id !== billId)));
    refreshHeldBills();
    setShowRecallPanel(false);
    if ((s.cart || []).length > 0) store.setStep('review');
  };

  const deleteHeldBill = (billId: string) => {
    const bills = getHeldBills().filter(b => b.id !== billId);
    localStorage.setItem('ims-held-bills', JSON.stringify(bills));
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
    store.resetTransaction();
  };

  useEffect(() => {
    if (user && !store.salesperson_id) {
      store.setStoreId(user.activeStoreId || user.storeIds?.[0] || '');
      store.setSalesperson(user.id, user.name);
    }
  }, [user]);

  const activeSteps = store.sale_type === 'quick_sale' ? QUICK_STEPS : STEPS.map(s => s.id);
  const currentStepIndex = activeSteps.indexOf(store.current_step);
  const visibleSteps = STEPS.filter(s => activeSteps.includes(s.id));

  const canProceed = useMemo(() => {
    switch (store.current_step) {
      case 'customer': return !!store.customer;
      case 'prescription': return !!store.prescription;
      case 'products': return (store.cart || []).length > 0;
      case 'review': return (store.cart || []).length > 0;
      case 'payment': {
        if (store.is_advance_payment) return store.getTotalPaid() > 0;
        return store.getBalance() <= 0.01;
      }
      default: return true;
    }
  }, [store.current_step, store.customer, store.prescription, store.cart, store.payments, store.is_advance_payment]);

  useEffect(() => {
    const handle = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (e.key === 'F2') { e.preventDefault(); startTransition(() => store.setStep('products')); }
      if (e.key === 'F9' && (store.cart || []).length > 0) { e.preventDefault(); startTransition(() => store.setStep('payment')); }
      if (e.key === 'Escape' && store.current_step !== 'customer') { e.preventDefault(); startTransition(() => store.prevStep()); }
      if (e.key === 'F4' && (store.cart || []).length > 0) { e.preventDefault(); setHoldConfirm(true); }
      if (e.key === 'Enter' && e.ctrlKey && store.current_step === 'payment') { e.preventDefault(); handleCreateOrder(); }
      if (e.key === 'Enter' && !e.ctrlKey && store.current_step !== 'complete' && store.current_step !== 'payment') {
        e.preventDefault();
        if (canProceed) startTransition(() => store.nextStep());
      }
    };
    window.addEventListener('keydown', handle);
    return () => window.removeEventListener('keydown', handle);
  }, [(store.cart || []).length, store.current_step, canProceed]);

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
    try {
      const result = await orderApi.createOrder({
        customer_id: store.customer?.id,
        store_id: store.store_id,
        order_type: store.sale_type,
        salesperson_id: store.salesperson_id,
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
      } as any);
      if (result?.order_id) {
        for (const p of (store.payments || [])) {
          try {
            await orderApi.addPayment(result.order_id, { method: p.method, amount: p.amount, reference: p.reference } as any);
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

  // Editorial title + subtitle for each wizard step — rendered in the work
  // surface header so step components themselves don't need to know about it.
  const STEP_HEADERS: Record<POSStep, { title: string; sub: string }> = {
    customer: { title: "Who's buying?", sub: 'Search an existing customer or create a walk-in. Phone lookup picks up family members automatically.' },
    prescription: { title: 'Capture Rx', sub: "Pull the customer's active prescription or enter a new one. Optometrist role required for tested-at-store Rx." },
    products: { title: 'Add to cart', sub: 'Barcode scan, SKU lookup, or browse. Lens orders auto-attach to the selected Rx.' },
    review: { title: 'Review & discount', sub: 'Check line items, apply category / brand / role-capped discount. GST auto-calculated.' },
    payment: { title: 'Tender', sub: 'Single or split tender across Cash / UPI / Card / EMI / Advance. Round-off at 50p if enabled.' },
    complete: { title: 'Done.', sub: 'Order placed. Receipt printed. Workshop job card auto-created for Rx orders.' },
  };
  const header = STEP_HEADERS[store.current_step];
  const showCartCol =
    (['products', 'review', 'prescription'] as POSStep[]).includes(store.current_step) &&
    (store.cart || []).length > 0;

  return (
    <div className="pos-body">
      {/* ── Left rail: vertical stepper + actions + held bills ── */}
      <aside className="steps-rail">
        <span className="eyebrow">Checkout</span>

        {visibleSteps.map((step) => {
          const stepIdx = activeSteps.indexOf(step.id);
          const isActive = step.id === store.current_step;
          const isComplete = stepIdx < currentStepIndex;
          const Icon = step.icon;
          return (
            <button
              key={step.id}
              type="button"
              className={'step' + (isActive ? ' active' : '') + (isComplete ? ' done' : '')}
              onClick={() => {
                if (isComplete) startTransition(() => store.setStep(step.id));
              }}
              disabled={!isComplete && !isActive}
              title={step.label}
            >
              <div className="step-num">
                {isComplete ? '' : <Icon className="w-3 h-3" />}
              </div>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div className="step-title">{step.label}</div>
                <div className="step-sub">
                  {step.id === 'customer' && (store.customer?.name ?? 'Pick customer')}
                  {step.id === 'prescription' && (store.prescription ? 'Rx attached' : 'Optional')}
                  {step.id === 'products' && `${(store.cart || []).length} items`}
                  {step.id === 'review' && 'Discount & GST'}
                  {step.id === 'payment' && 'Split tender OK'}
                  {step.id === 'complete' && 'Print receipt'}
                </div>
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
            className="btn sm"
            style={{ position: 'relative' }}
            title="Recall held bill"
          >
            <Play className="w-4 h-4" /> Recall
            {getHeldBills().length > 0 && (
              <span
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
                  display: 'grid',
                  placeItems: 'center',
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
            <div className="eyebrow" style={{ marginBottom: 6 }}>
              Step {currentStepIndex + 1} / {visibleSteps.length} · {visibleSteps.find((s) => s.id === store.current_step)?.label}
            </div>
            <h2>{header.title}</h2>
            <p className="sub">{header.sub}</p>
          </div>

          {/* Error banner */}
          {errorMsg && (
            <div
              className="s-section"
              style={{
                padding: 12,
                borderColor: 'var(--err-50)',
                background: 'var(--err-50)',
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                marginBottom: 14,
              }}
            >
              <AlertTriangle className="w-5 h-5" style={{ color: 'var(--err)' }} />
              <span style={{ color: 'var(--err)', flex: 1 }}>{errorMsg}</span>
              <button onClick={() => setErrorMsg(null)} className="btn sm ghost">
                <X className="w-4 h-4" />
              </button>
            </div>
          )}

          {/* Step content (unchanged components).
              Audit 2026-04-21 flagged sticky-footer overlapping row-3
              products on Step 2 at 900px viewport. paddingBottom below
              gives the last row scroll clearance past the footer. */}
          <div style={{ flex: 1, minHeight: 0, paddingBottom: '80px', overflowY: 'auto' }}>
            {store.current_step === 'customer' && <StepCustomer />}
            {store.current_step === 'prescription' && (
              <StepPrescription
                onShowModal={() => setShowPrescriptionModal(true)}
                onShowNew={() => setShowNewPrescription(true)}
              />
            )}
            {store.current_step === 'products' && <StepProducts onOpenLensModal={() => setShowLensModal(true)} />}
            {store.current_step === 'review' && <StepReview onOpenDiscount={(item) => setDiscountItem(item)} />}
            {store.current_step === 'payment' && <StepPayment />}
            {store.current_step === 'complete' && (
              <StepComplete onPrint={() => setShowReceipt(true)} onReset={handleFullReset} />
            )}
          </div>

          {/* Sticky footer: Back + cart hint + Continue/Complete */}
          <div className="pos-footer">
            <div className="left">
              {currentStepIndex > 0 && store.current_step !== 'complete' && (
                <button
                  type="button"
                  onClick={() => startTransition(() => store.prevStep())}
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
            {store.current_step !== 'complete' && (
              <button
                type="button"
                onClick={() => {
                  setErrorMsg(null);
                  store.current_step === 'payment'
                    ? handleCreateOrder()
                    : startTransition(() => store.nextStep());
                }}
                disabled={!canProceed || store.is_processing}
                className={'btn sm ' + (store.current_step === 'payment' ? 'accent' : 'primary')}
              >
                {store.is_processing ? (
                  <>
                    <span
                      className="w-4 h-4 animate-spin"
                      style={{
                        border: '2px solid rgba(255,255,255,.3)',
                        borderTopColor: '#fff',
                        borderRadius: '50%',
                      }}
                    />
                    Processing…
                  </>
                ) : (
                  <>
                    {store.current_step === 'payment' ? 'Complete order' : 'Continue'}
                    <ChevronRight className="w-4 h-4" />
                  </>
                )}
              </button>
            )}
          </div>
        </div>

        {/* Right cart column — only during cart-relevant steps */}
        {showCartCol && (
          <aside className="pos-cart-col">
            <CartSidebar />
          </aside>
        )}
      </div>

      {/* Mobile floating cart FAB — unchanged behavior */}
      {showCartCol && (
        <div className="tablet:hidden fixed bottom-20 right-4 z-30">
          <button
            onClick={() => store.setStep('review')}
            className="w-14 h-14 rounded-full shadow-lg flex items-center justify-center relative touch-manipulation"
            style={{ background: 'var(--bv)', color: '#fff' }}
            aria-label={`Review cart (${(store.cart || []).length} items)`}
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
              <button onClick={() => { setShowNewPrescription(false); setErrorMsg(null); }} className="p-1 hover:bg-gray-100 rounded"><X className="w-5 h-5" /></button>
            </div>
            {errorMsg && (
              <div className="mx-4 mt-4 p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700 flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                <div><p className="font-medium">Failed to save prescription</p><p className="text-xs mt-0.5">{errorMsg}</p></div>
                <button onClick={() => setErrorMsg(null)} className="ml-auto text-red-400 hover:text-red-600"><X className="w-4 h-4" /></button>
              </div>
            )}
            <div className="p-4">
              <PrescriptionForm
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
                      right_eye: { sph: String(rxData.sph_od || 0), cyl: String(rxData.cyl_od || 0), axis: rxData.axis_od || 180, add: String(rxData.add_od || 0), pd: String(rxData.pd_od || '') },
                      left_eye: { sph: String(rxData.sph_os || 0), cyl: String(rxData.cyl_os || 0), axis: rxData.axis_os || 180, add: String(rxData.add_os || 0), pd: String(rxData.pd_os || '') },
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
              <button onClick={() => setShowRecallPanel(false)} className="p-1 hover:bg-gray-100 rounded"><X className="w-5 h-5" /></button>
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
// STEP 1: Customer
// ============================================================================
function StepCustomer() {
  const store = usePOSStore();
  const [showAddCustomerModal, setShowAddCustomerModal] = useState(false);

  const handleSaveCustomer = async (customerData: CustomerFormData) => {
    const payload = {
      name: customerData.fullName,
      mobile: customerData.mobileNumber,
      email: customerData.email || undefined,
      customer_type: customerData.customerType,
      gstin: customerData.customerType === 'B2B' ? customerData.gstNumber : undefined,
      billing_address: (customerData.address || customerData.city || customerData.pincode) ? {
        address: customerData.address,
        city: customerData.city,
        state: customerData.state,
        pincode: customerData.pincode,
      } : undefined,
      patients: (customerData.patients || []).map(p => ({
        name: p.name,
        mobile: p.mobile || undefined,
        dob: p.dateOfBirth || undefined,
        relation: p.relation || 'Self',
      })),
    };
    const r = await customerApi.createCustomer(payload as any);
    const custId = r?.customer_id || r?.id || `new-${Date.now()}`;
    store.setCustomer({
      id: custId,
      name: customerData.fullName,
      phone: customerData.mobileNumber,
      email: customerData.email,
      customerType: customerData.customerType,
    } as any);
    if (customerData.patients?.length > 0) {
      store.setPatient({ name: customerData.patients[0].name, id: customerData.patients[0].id } as any);
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

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-2">Customer</label>
        {store.customer ? (
          <>
          <div className={`${isWalkin ? 'bg-white border-gray-200' : 'bg-bv-red-50 border-bv-red-600'} border rounded-xl p-4 flex items-center justify-between`}>
            <div className="flex items-center gap-3">
              <div className={`w-10 h-10 rounded-full ${isWalkin ? 'bg-gray-500' : 'bg-bv-red-700'} text-gray-900 flex items-center justify-center font-semibold`}>{store.customer.name?.charAt(0)?.toUpperCase() || 'W'}</div>
              <div>
                <p className="font-semibold text-gray-900">{store.customer.name}</p>
                <p className="text-sm text-gray-500">{store.customer.phone || 'No phone'}</p>
                {isWalkin && <p className="text-xs text-amber-600 mt-0.5">Walk-in -- Quick Sale only</p>}
                {store.patient && <p className="text-xs text-bv-red-600 mt-0.5">Patient: {store.patient.name}</p>}
              </div>
            </div>
            <button onClick={() => startTransition(() => store.setCustomer(null))} className="text-sm text-gray-500 hover:text-gray-700 px-3 py-1 border border-gray-200 rounded-lg">Change</button>
          </div>
          {!isWalkin && <CustomerCardWithLoyalty />}
          {!isWalkin && <RxAvailableBadge customerId={store.customer.id} customerName={store.customer.name} />}
          {!isWalkin && <CustomerHistory customerId={store.customer.id} />}
          </>
        ) : (
          <>
            <AutoSearch<any>
              fetchResults={async (q, sid) => {
                try {
                  const res = await customerApi.getCustomers({ search: q, storeId: sid, limit: 8 });
                  return res?.customers || res || [];
                } catch { return []; }
              }}
              renderItem={(cust) => {
                const custName = cust.name || cust.customer_name || cust.full_name || 'Unknown';
                const custPhone = cust.phone || cust.mobile || '';
                return (
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-full bg-bv-red-700 flex items-center justify-center text-sm font-bold text-gray-900">{custName.charAt(0).toUpperCase()}</div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-900 truncate">{custName}</p>
                      <p className="text-xs text-gray-500">{custPhone} {cust.city && `\u00B7 ${cust.city}`}</p>
                    </div>
                  </div>
                );
              }}
              onSelect={(cust) => {
                startTransition(() => {
                  store.setCustomer({
                    ...cust,
                    id: cust.customer_id || cust._id || cust.id,
                    name: cust.name || cust.customer_name || cust.full_name || 'Customer',
                    phone: cust.phone || cust.mobile || '',
                  } as any);
                });
              }}
              getKey={(cust) => cust.customer_id || cust._id || cust.id || cust.phone || cust.name || 'unknown'}
              placeholder="Search by phone number or name..."
              autoFocus
              clearOnSelect
              emptyMessage="No customers found"
            />
            <div className="mt-3 flex gap-4">
              <button onClick={() => setShowAddCustomerModal(true)} className="flex items-center gap-2 text-sm text-bv-red-600 hover:text-bv-gold-700 font-medium"><Plus className="w-4 h-4" /> Create new customer</button>
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
function StepPrescription({ onShowModal, onShowNew }: { onShowModal: () => void; onShowNew: () => void }) {
  const store = usePOSStore();
  const [recentRx, setRecentRx] = useState<any[]>([]);
  const [rxLoading, setRxLoading] = useState(false);

  const lookupId = store.patient?.id || store.customer?.id;
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

  const fmtPower = (v: any) => {
    const n = parseFloat(v);
    if (!n || isNaN(n)) return '0.00';
    return n >= 0 ? `+${n.toFixed(2)}` : n.toFixed(2);
  };

  if (store.prescription) {
    return (
      <div className="w-full max-w-5xl mx-auto space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-gray-900">Selected Prescription</h3>
          <button onClick={onShowModal} className="text-sm text-bv-red-600 hover:text-bv-gold-700 font-medium">Change</button>
        </div>
        <PrescriptionPanel prescription={store.prescription} patientName={store.patient?.name || store.customer?.name} readOnly />
        <div className="bg-green-50 border border-green-200 rounded-lg p-3 flex items-center gap-2 text-sm text-green-700">
          <CheckCircle className="w-4 h-4" /> Prescription attached -- you can now select lenses
        </div>
      </div>
    );
  }
  return (
    <div className="w-full max-w-5xl mx-auto space-y-6">
      <div><h3 className="font-semibold text-gray-900 mb-1">Prescription Required</h3><p className="text-sm text-gray-500">Select existing or enter a new prescription.</p></div>

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
    try {
      const result = await productApi.getProducts({ search: barcode });
      const products = result?.products || result || [];
      if (Array.isArray(products) && products.length === 1) {
        handleAddProduct(products[0]);
        return;
      }
    } catch { /* fall back to search */ }
    startTransition(() => setDebouncedSearch(barcode));
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
          <button onClick={() => setBlockMsg(null)} className="text-red-400 hover:text-red-600"><X className="w-3.5 h-3.5" /></button>
        </div>
      )}
      <div className="flex gap-3">
        <div className="flex-1">
          <BarcodeScanner onScan={handleBarcodeScan} onManualSearch={handleManualSearch} placeholder="Scan barcode or search products..." autoFocus />
        </div>
        {store.sale_type === 'prescription_order' && store.prescription && (
          <button onClick={onOpenLensModal} className="flex items-center gap-2 px-4 py-2 bg-purple-900/30 text-purple-700 border border-purple-200 rounded-lg hover:bg-purple-100 whitespace-nowrap text-sm font-medium">
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
        <div className="bg-purple-900/30 border border-purple-200 rounded-lg p-3 flex items-center gap-3 text-sm">
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
    let totalTax = 0;
    const rates: Record<number, number> = {};
    for (const item of (store.cart || [])) {
      const rate = getGSTRateByCategory(item.category);
      const itemTaxable = item.line_total;
      const itemTax = Math.round(itemTaxable * (rate / 100) * 100) / 100;
      totalTax += itemTax;
      rates[rate] = (rates[rate] || 0) + itemTaxable;
    }
    return { totalTax: Math.round(totalTax * 100) / 100, rates };
  }, [store.cart]);

  const total = store.getGrandTotal();

  return (
    <div className="w-full max-w-5xl mx-auto space-y-4">
      <h3 className="font-semibold text-gray-900">Order Review</h3>
      {store.customer && (
        <div className="bg-white rounded-lg p-3 flex items-center gap-3 text-sm">
          <User className="w-4 h-4 text-gray-500" /><span className="font-medium">{store.customer.name}</span><span className="text-gray-500">{store.customer.phone}</span>
          {store.sale_type === 'prescription_order' && <span className="ml-auto px-2 py-0.5 bg-purple-900/30 text-purple-600 rounded text-xs font-medium">Prescription Order</span>}
        </div>
      )}

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-white text-xs text-gray-500 uppercase">
            <tr><th className="text-left px-4 py-2">Item</th><th className="text-center px-2 py-2">Qty</th><th className="text-right px-2 py-2">MRP</th><th className="text-right px-2 py-2">Price</th><th className="text-right px-2 py-2">Disc</th><th className="text-center px-2 py-2">GST</th><th className="text-right px-4 py-2">Total</th><th className="w-8"></th></tr>
          </thead>
          <tbody>
            {(store.cart || []).map(item => {
              const gstRate = getGSTRateByCategory(item.category);
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
                <td><button onClick={() => store.removeFromCart(item.id)} className="p-1 text-gray-500 hover:text-red-500"><X className="w-4 h-4" /></button></td>
              </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <textarea value={store.cart_note} onChange={(e) => store.setCartNote(e.target.value)} placeholder="Order notes, fitting instructions..." className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm h-16 resize-none" />

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
              value={store.delivery_date || ''}
              onChange={(e) => store.setDeliveryDate(e.target.value || null)}
              className="w-full px-2 py-1.5 border border-gray-300 rounded text-sm text-gray-900"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 block mb-1">Time slot</label>
            <select
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
