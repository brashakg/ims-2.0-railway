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
import { usePOSStore, type CartLineItem } from '../../../stores/posStore';
import { useIsOnlineStore } from '../../../hooks/useIsOnlineStore';
import WalkoutComplianceBanner from '../../../components/pos/WalkoutComplianceBanner';
import { CustomerCardWithLoyalty } from '../../../components/pos/CustomerCardWithLoyalty';
import { CartSidebar } from '../../../components/pos/POSCart';
import { DiscountModal, toDiscountItem } from '../../../components/pos/DiscountModal';
import { BillDiscountCard } from '../../../components/pos/BillDiscountCard';
import { StepPayment } from '../../../components/pos/POSPayment';
import { StepComplete } from '../../../components/pos/POSInvoice';
import { BarcodeScanner } from '../../../components/pos/BarcodeScanner';
import { PrescriptionSelectModal } from '../../../components/pos/PrescriptionSelectModal';
import { SalespersonPicker } from '../../../components/pos/SalespersonPicker';
import { PosWidgets } from './PosWidgets';
import { CustomerSearchBar } from '../../../components/pos/CustomerSearchBar';
import { submitPosOrder } from '../../../components/pos/submitOrder';
import SaleCompleteScreen from './SaleCompleteScreen';
import ProductResultsStrip from './ProductResultsStrip';
import DeliveryOptionsRow from './DeliveryOptionsRow';
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
  // The cart line whose discount is being edited. The cart IS the review
  // step on this surface, so the trigger lives on the line itself.
  const [discountLine, setDiscountLine] = useState<CartLineItem | null>(null);
  const [rxPickerOpen, setRxPickerOpen] = useState(false);
  const [productQuery, setProductQuery] = useState('');
  // The finished sale, held so the completion screen can print and send
  // against it. Cleared by Done, which also resets the till for the next
  // customer.
  const [completed, setCompleted] = useState<{
    orderId: string;
    orderNumber?: string;
    jobId?: string;
  } | null>(null);
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
      // EVERY successful sale lands on the completion screen now. Until this
      // wiring, only a sale that happened to spawn a workshop job showed
      // anything at all - a plain frame sale completed silently, leaving the
      // counter with no invoice, no WhatsApp and no way back to a clean till.
      if (res.orderId) {
        setCompleted({
          orderId: res.orderId,
          orderNumber: res.orderNumber,
          jobId: res.fittingJobId,
        });
      }
    } finally {
      store.setProcessing(false);
    }
  };

  const isComplete = store.current_step === 'complete';

  return (
    <div className="min-h-full lg:h-full lg:min-h-0 flex flex-col overflow-y-auto lg:overflow-hidden bg-gray-50">
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

      {/* Bill strip — slim context row. The salesperson lives here as a CHIP
          (owner: the labelled field "takes up too much space"), not a form
          block; the picker itself still enforces the manager-tier rule. */}
      {!isComplete && (
        <div className="px-3.5 pt-2 pb-1 flex items-center gap-2 shrink-0">
          <span className="text-[10px] font-medium uppercase tracking-widest text-gray-500">
            Selling
          </span>
          <SalespersonPicker compact />
          <div className="flex-1" />
          <span className="text-[11px] text-gray-500">
            {(store.cart || []).length} item{(store.cart || []).length === 1 ? '' : 's'}
          </span>
        </div>
      )}

      {completed ? (
        <SaleCompleteScreen
          orderId={completed.orderId}
          orderNumber={completed.orderNumber}
          jobId={completed.jobId}
          salespersonId={store.salesperson_id}
          salespersonName={store.salesperson_name}
          onDone={() => {
            setCompleted(null);
            store.resetTransaction();
          }}
        />
      ) : isComplete ? (
        <div className="flex-1 overflow-y-auto p-4">
          <StepComplete onPrint={() => window.print()} onReset={() => store.resetTransaction()} />
        </div>
      ) : (
        /* TWO columns, per the locked mockup (Main.dc.html): the left column
           flexes and owns customer + Rx + product entry + the 2x2 widgets;
           the right column is a fixed 430px cart + payment that stays visible
           at all times. Nothing here may scroll the PAGE (spec 11b) — only
           the cart list and the left column's own overflow scroll. */
        <div className="flex-1 lg:min-h-0 flex flex-col lg:flex-row gap-3.5 px-3.5 pb-3.5">
          {/* ── LEFT ── */}
          <div className="flex-1 min-w-0 flex flex-col gap-3 lg:min-h-0">
            {/* Customer + Rx: the primary block, and the one that TAKES THE
                SLACK. The column used to hand its spare height to an empty
                spacer purely to pin the widgets to the bottom, which read as a
                dead band across the middle of the till. The widgets still sit
                at the bottom - this card absorbs the same space - but now the
                room goes to the thing the dispenser actually reads. */}
            {/* Sizes to its CONTENT. It used to be lg:flex-1, which stretched
                it to fill the column -- so once an Rx was picked, the leftover
                height showed as an empty band under the power table. The
                product results below take the free space instead, where more
                rows are actually useful. */}
            <div className="rounded-xl border border-gray-200 bg-white p-3 shrink-0 lg:max-h-[45%] lg:overflow-y-auto">
              {store.customer ? (
                <CustomerCardWithLoyalty />
              ) : (
                <div>
                  <div className="text-[10px] font-medium uppercase tracking-widest text-gray-500 mb-1.5">
                    Customer <span className="text-red-500">*</span>
                  </div>
                  <CustomerSearchBar store={store} />
                  <div className="mt-1 text-[11px] text-gray-500">
                    Every bill needs a customer — no anonymous sale on any counter.
                  </div>
                </div>
              )}

              {/* ONE Rx - the one billing uses - behind a single tap to change
                  (spec 11c). It shows the FULL powers, not just sphere: a
                  sphere-only line is unreadable as a prescription, and the
                  dispenser has to open a modal to check the cylinder or the
                  axis on every single bill. The column had dead space directly
                  below this; the table uses it. */}
              {store.customer && (
                <button
                  type="button"
                  onClick={() => setRxPickerOpen(true)}
                  className="mt-2.5 w-full rounded-lg border border-gray-200 bg-white text-left px-3 py-2.5 hover:bg-gray-50"
                >
                  <div className="flex items-center gap-3 min-h-[24px]">
                    <span className="text-[10px] font-medium uppercase tracking-widest text-gray-500 shrink-0">
                      Rx
                    </span>
                    <span className="flex-1 min-w-0 text-sm truncate font-medium">
                      {store.prescription
                        ? store.patient?.name || store.customer?.name || 'On file'
                        : ''}
                    </span>
                    <span className="text-xs text-blue-700 font-medium shrink-0">
                      {store.prescription ? 'Change' : 'Choose or add'}
                    </span>
                  </div>

                  {store.prescription ? (
                    <table className="mt-2 w-full border-collapse tabular-nums">
                      <thead>
                        <tr>
                          {['', 'SPH', 'CYL', 'AXIS', 'ADD', 'PD'].map((h) => (
                            <th
                              key={h}
                              className="text-[10px] font-semibold uppercase tracking-wider text-gray-500 text-right px-1.5 pb-1"
                            >
                              {h}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {([
                          ['R', store.prescription.rightEye],
                          ['L', store.prescription.leftEye],
                        ] as const).map(([eye, p]) => (
                          <tr key={eye} className="border-t border-gray-100">
                            <td className="text-[11px] font-semibold text-gray-500 text-right px-1.5 py-1">
                              {eye}
                            </td>
                            {/* A BLANK power is not the same as ZERO: plano is
                                0.00 and must read as 0.00, while "not measured"
                                must read as a dash. `?? '—'` keeps that apart -
                                `|| '—'` would print a dash for a real 0. */}
                            {[p?.sphere, p?.cylinder, p?.axis, p?.add, p?.pd].map(
                              (v, i) => (
                                <td
                                  key={i}
                                  className="text-xs text-gray-900 text-right px-1.5 py-1"
                                >
                                  {v ?? '—'}
                                </td>
                              ),
                            )}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <div className="mt-1 text-sm text-gray-500">
                      No prescription selected — tap to choose or add
                    </div>
                  )}
                </button>
              )}
            </div>

            {/* Product entry: compact — one row of controls (owner spec 6) */}
            <div className="shrink-0">
              <BarcodeScanner
                onScan={handleScan}
                onManualSearch={setProductQuery}
                placeholder="Scan barcode or search products…"
                autoFocus
              />
            </div>

            {/* Typed search results. Adds the line itself through the shared
                intake guard, and reports a money-guard refusal upward so this
                surface keeps ONE error banner rather than growing a second. */}
            {/* Takes the column's slack now that the Rx card sizes to its own
                content: spare height here means MORE product rows visible,
                which is the one thing on this column that gets better with
                room. min-h-0 so it can shrink rather than push the widgets off
                the bottom. */}
            <div className="min-w-0 shrink-0 lg:flex-1 lg:min-h-0 lg:overflow-y-auto">
              <ProductResultsStrip
                storeId={activeStoreId}
                query={productQuery}
                onBlocked={setErrorMsg}
                onPicked={() => setProductQuery('')}
              />
            </div>

            {/* Delivery date / slot / priority + bill note (owner spec 7). */}
            <div className="shrink-0">
              <DeliveryOptionsRow />
            </div>

            {/* No spacer any more - the customer + Rx card above grows into the
                slack instead, so the widgets stay pinned to the bottom without
                a band of dead air above them. */}

            {/* Bottom 2x2 widgets (owner spec 8) */}
            <div className="shrink-0">
              <PosWidgets />
            </div>
          </div>

          {/* ── RIGHT: cart + payment, always visible (430px per mockup) ── */}
          <div className="w-full lg:w-[430px] shrink-0 lg:min-h-0 flex flex-col lg:grid gap-3 lg:grid-rows-[minmax(0,1fr)_minmax(0,auto)_auto]">
            <div className="lg:min-h-0 lg:overflow-y-auto rounded-xl border border-gray-200 bg-white">
              <CartSidebar onOpenDiscount={setDiscountLine} />
            </div>
            {/* Bill discount sits in the payment row rather than as a fourth
                grid child - the row template is fixed at three, and a stray
                child would auto-place and break the locked layout. */}
            <div className="lg:min-h-0 lg:overflow-y-auto flex flex-col gap-3">
              <BillDiscountCard />
              <StepPayment />
            </div>
            <button
              type="button"
              onClick={handleCompleteSale}
              disabled={store.is_processing || (store.cart || []).length === 0}
              className="h-12 shrink-0 rounded-xl bg-gray-900 text-white font-semibold text-base disabled:opacity-40"
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

      {/* Owner spec 3: item AND bill discounts together, each with a compulsory
          reason when no offer applies, all under the role cap. The SAME modal
          the classic surface opens - the cap check and the reason rule have one
          implementation, not two. */}
      {discountLine && (
        <DiscountModal
          item={toDiscountItem(discountLine)}
          maxDiscountPercent={user?.discountCap ?? 10}
          initialReason={discountLine.discount_reason || ''}
          onApply={(pct, _amt, reason) => {
            store.applyDiscount(discountLine.id, pct, reason);
            setDiscountLine(null);
          }}
          onClose={() => setDiscountLine(null)}
        />
      )}
    </div>
  );
}

export default BillingSurface;
