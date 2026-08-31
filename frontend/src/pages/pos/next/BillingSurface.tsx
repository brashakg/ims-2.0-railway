// ============================================================================
// IMS 2.0 - POS one-surface billing (Wave 4, owner-locked mockup)
// ============================================================================
// The new register: EVERYTHING on one viewport-locked screen — no step
// wizard. Customer + Rx left (big, per owner), scan + products center
// (small), cart + tenders right, widgets strip below. Reuses the classic
// surface's store (posStore), its zero-prop panels, and the two shared
// brains (submitOrder.ts, productIntake.ts) so there is exactly ONE submit
// path and ONE money guard across both surfaces. The classic POS stays
// untouched at its route until the owner calls the switch (real-store
// testing, old POS = fallback — owner ruling 2026-08-31).
//
// PHASE A (walking skeleton): functional end-to-end billing. Mockup-fidelity
// styling, widgets and the delivery surface land in the next phases.

import { useRef, useState } from 'react';
import { AlertTriangle, X } from 'lucide-react';
import { useAuth } from '../../../context/AuthContext';
import { usePOSStore } from '../../../stores/posStore';
import { useIsOnlineStore } from '../../../hooks/useIsOnlineStore';
import WalkoutComplianceBanner from '../../../components/pos/WalkoutComplianceBanner';
import { CustomerCardWithLoyalty } from '../../../components/pos/CustomerCardWithLoyalty';
import { CartSidebar } from '../../../components/pos/POSCart';
import { StepPayment } from '../../../components/pos/POSPayment';
import { StepComplete } from '../../../components/pos/POSInvoice';
import { BarcodeScanner } from '../../../components/pos/BarcodeScanner';
import { PrescriptionSelectModal } from '../../../components/pos/PrescriptionSelectModal';
import { submitPosOrder } from '../../../components/pos/submitOrder';
import {
  resolveBarcode,
  posPriceGuard,
  cartItemFromProduct,
} from '../../../components/pos/productIntake';

export function BillingSurface() {
  const { user } = useAuth();
  const store = usePOSStore();
  const activeStoreId = user?.activeStoreId || store.store_id;
  const onlineStoreActive = useIsOnlineStore(activeStoreId);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [rxPickerOpen, setRxPickerOpen] = useState(false);
  const idempotencyKeyRef = useRef<string | null>(null);

  // ---- Guards (same rules as the classic surface; backend enforces both) --
  if (!activeStoreId) {
    return (
      <div className="min-h-[100dvh] bg-white flex items-center justify-center p-4">
        <div className="border border-amber-300 rounded-2xl p-8 max-w-md text-center">
          <AlertTriangle className="w-12 h-12 text-amber-500 mx-auto mb-4" />
          <h2 className="text-xl font-bold text-gray-900 mb-2">No Store Selected</h2>
          <p className="text-sm text-gray-500">
            POS requires an active store. Pick one from the header before billing.
          </p>
        </div>
      </div>
    );
  }
  if (onlineStoreActive) {
    return (
      <div className="min-h-[100dvh] bg-white flex items-center justify-center p-4">
        <div className="border border-blue-200 rounded-2xl p-8 max-w-md text-center">
          <h2 className="text-xl font-bold text-gray-900 mb-2">This is an online store</h2>
          <p className="text-sm text-gray-500">
            Website orders arrive from Shopify and bill automatically — the till
            is disabled here.
          </p>
        </div>
      </div>
    );
  }

  const handleScan = async (code: string) => {
    const res = await resolveBarcode(store.store_id || activeStoreId, code);
    if (res.ok && res.product) {
      const guard = posPriceGuard(res.product);
      if (!guard.ok) {
        setErrorMsg(guard.message || 'Blocked');
        return;
      }
      setErrorMsg(null);
      store.addToCart(cartItemFromProduct(res.product, guard));
      return;
    }
    if (res.message) setErrorMsg(res.message);
  };

  const handleCompleteSale = async () => {
    if (store.is_processing) return;
    setErrorMsg(null);
    store.setProcessing(true);
    if (!idempotencyKeyRef.current) {
      idempotencyKeyRef.current =
        typeof crypto !== 'undefined' && crypto.randomUUID
          ? crypto.randomUUID()
          : `idem-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    }
    try {
      const res = await submitPosOrder(store, idempotencyKeyRef.current);
      if (!res.ok) {
        setErrorMsg(res.error || 'Failed to create order');
        return;
      }
      idempotencyKeyRef.current = null;
      if (res.warning) setErrorMsg(res.warning);
      // Fitting-modal flow arrives with the Rx panel in Phase B; until then a
      // workshop job created by the shared brain simply completes the sale.
      if (res.fittingJobId) store.setStep('complete');
    } finally {
      store.setProcessing(false);
    }
  };

  const isComplete = store.current_step === 'complete';

  return (
    <div className="h-[calc(100dvh-52px)] flex flex-col overflow-hidden bg-gray-50">
      <WalkoutComplianceBanner />

      {errorMsg && (
        <div className="mx-3 mt-2 bg-red-50 border border-red-200 rounded-lg p-2.5 flex items-center gap-2 text-sm text-red-700">
          <AlertTriangle className="w-4 h-4 flex-shrink-0" />
          <span className="flex-1">{errorMsg}</span>
          <button onClick={() => setErrorMsg(null)} aria-label="Dismiss" title="Dismiss">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {isComplete ? (
        <div className="flex-1 overflow-y-auto p-4">
          <StepComplete onPrint={() => window.print()} onReset={() => store.resetTransaction()} />
        </div>
      ) : (
        <div className="flex-1 min-h-0 grid gap-3 p-3 grid-cols-1 lg:grid-cols-[minmax(320px,30fr)_minmax(300px,34fr)_minmax(320px,36fr)]">
          {/* ── Left: who's buying + Rx (owner: this column is the BIG one) ── */}
          <div className="min-h-0 overflow-y-auto space-y-3">
            <CustomerCardWithLoyalty />

            {/* Rx SELECTOR BAR (owner spec 11c: "half baked, too complicated
                — remake"). ONE Rx is shown — the one billing uses — behind a
                single 44px bar. Tapping opens the EXISTING family/history
                picker rather than a second Rx UI. */}
            {store.customer && (
              <button
                type="button"
                onClick={() => setRxPickerOpen(true)}
                disabled={!store.customer?.id}
                className="w-full min-h-[44px] px-3 py-2 rounded-xl border border-gray-200 bg-white text-left flex items-center gap-3 disabled:opacity-50"
              >
                <span className="text-[10px] font-medium uppercase tracking-widest text-gray-500 shrink-0">
                  Rx
                </span>
                {store.prescription ? (
                  <span className="flex-1 min-w-0 text-sm">
                    <span className="font-medium">
                      {store.patient?.name || store.customer?.name || 'On file'}
                    </span>
                    <span className="text-gray-500">
                      {' · '}R {store.prescription.rightEye?.sphere ?? '—'}
                      {' / '}L {store.prescription.leftEye?.sphere ?? '—'}
                    </span>
                  </span>
                ) : (
                  <span className="flex-1 text-sm text-gray-500">
                    No prescription selected — tap to choose or add
                  </span>
                )}
                <span className="text-xs text-blue-700 font-medium shrink-0">Change</span>
              </button>
            )}
          </div>

          {/* ── Center: always-armed scan + cart lines ── */}
          <div className="min-h-0 flex flex-col gap-3">
            <BarcodeScanner
              onScan={handleScan}
              placeholder="Scan barcode…"
              autoFocus
            />
            <div className="flex-1 min-h-0 overflow-y-auto">
              <CartSidebar />
            </div>
          </div>

          {/* ── Right: tenders + complete ── */}
          <div className="min-h-0 flex flex-col gap-3">
            <div className="flex-1 min-h-0 overflow-y-auto">
              <StepPayment />
            </div>
            <button
              type="button"
              onClick={handleCompleteSale}
              disabled={store.is_processing || (store.cart || []).length === 0}
              className="h-12 rounded-xl bg-gray-900 text-white font-semibold text-base disabled:opacity-40"
            >
              {store.is_processing ? 'Saving…' : 'Complete sale'}
            </button>
          </div>
        </div>
      )}

      {rxPickerOpen && store.customer?.id && (
        <PrescriptionSelectModal
          customerId={String(store.customer.id)}
          patient={store.patient || null}
          currentPrescriptionId={store.prescription?.id}
          onClose={() => setRxPickerOpen(false)}
          onSelect={(rx) => {
            store.setPrescription(rx);
            setRxPickerOpen(false);
          }}
          onCreateNew={() => {
            // Capture-new lands with the Rx scenarios panel; until then the
            // clinic/classic Rx form remains the create door.
            setRxPickerOpen(false);
            setErrorMsg('Add a new prescription from the Clinical screen, then pick it here.');
          }}
        />
      )}
    </div>
  );
}

export default BillingSurface;
