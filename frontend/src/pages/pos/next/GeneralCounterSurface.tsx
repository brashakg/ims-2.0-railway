// ============================================================================
// IMS 2.0 - POS general counter (Wave 4, owner spec 1-ii)
// ============================================================================
// The THIRD surface: NON-optical selling -- watches, wall clocks, perfumes,
// smartwatches, accessories, non-Rx sunglasses. It is the billing surface with
// every optical part removed: no prescription selector, no pair chips. What
// takes their place is browse (category chips + a bigger product grid) and a
// take-away / home-delivery choice instead of the delivery date + slot row.
//
// SAME BRAINS as BillingSurface -- posStore, submitOrder.ts (the one submit
// path), productIntake.ts (scan resolution + the MONEY guards),
// CustomerSearchBar (bill-to-member), SalespersonPicker -- so no money, scan
// or customer rule is written twice. Nothing here computes a total, a tax or
// a balance; those are read off the store/server.
//
// Mount (another step wires the route; this file edits nothing else):
//   const GeneralCounterSurface = lazy(
//     () => import('../pages/pos/next/GeneralCounterSurface'));
//   <Route
//     path="pos/counter"
//     element={
//       <ProtectedRoute allowedRoles={[/* same gate as pos/new */]}>
//         <GeneralCounterSurface />
//       </ProtectedRoute>
//     }
//   />
// Zero props. It fills its parent cell (h-full min-h-0) and scrolls only
// INSIDE the product grid and the cart -- the page itself never scrolls
// (spec 11b).

import { useState, useRef } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, X, Glasses, ShoppingBag, Home } from 'lucide-react';
import { useAuth } from '../../../context/AuthContext';
import { usePOSStore, type CartLineItem } from '../../../stores/posStore';
import { useIsOnlineStore } from '../../../hooks/useIsOnlineStore';
import { useProducts } from '../../../hooks/usePOSQueries';
import WalkoutComplianceBanner from '../../../components/pos/WalkoutComplianceBanner';
import { WalkinWalkoutControls } from '../../../components/pos/WalkinWalkoutControls';
import { HeldBillsControls } from '../../../components/pos/HeldBillsControls';
import { CustomerCardWithLoyalty } from '../../../components/pos/CustomerCardWithLoyalty';
import { CartSidebar } from '../../../components/pos/POSCart';
import { DiscountModal, toDiscountItem } from '../../../components/pos/DiscountModal';
import { BillDiscountCard } from '../../../components/pos/BillDiscountCard';
import { StepPayment } from '../../../components/pos/POSPayment';
import { StepComplete } from '../../../components/pos/POSInvoice';
import { BarcodeScanner } from '../../../components/pos/BarcodeScanner';
import { SalespersonPicker } from '../../../components/pos/SalespersonPicker';
import { AddCustomerModal } from '../../../components/customers/AddCustomerModal';
import {
  CustomerSearchBar,
  createAndSelectCustomer,
  selectCustomerHit,
} from '../../../components/pos/CustomerSearchBar';
import { PosWidgets } from './PosWidgets';
import { CounterCompleteScreen } from './SaleCompleteScreen';
import { ProductCard, productIdOf, MAX_PRODUCT_RESULTS } from './ProductResultsStrip';
import { submitPosOrder } from '../../../components/pos/submitOrder';
import {
  resolveBarcode,
  posPriceGuard,
  cartItemFromProduct,
} from '../../../components/pos/productIntake';
import { CATEGORY_BROWSE_OPTIONS } from '../../../utils/categoryNormalize';
import { istDayString } from '../../../utils/datetime';

// What this counter sells (owner spec 1-ii). Values are the canonical spine
// categories and the labels come from the ONE shared browse vocabulary, so a
// chip here can never filter on a spelling the catalogue does not store.
// Perfumes and the rest of the giftable stock live under ACCESSORIES.
// Sunglasses and smart glasses belong here because neither carries an Rx --
// exactly the rule productIntake uses when it stamps `is_optical`.
const COUNTER_CATEGORIES = [
  'WATCH',
  'SMARTWATCH',
  'WALL_CLOCK',
  'SUNGLASS',
  'SMARTGLASSES',
  'ACCESSORIES',
];
const COUNTER_CHIPS = CATEGORY_BROWSE_OPTIONS.filter((o) =>
  COUNTER_CATEGORIES.includes(o.value),
);

/** The order note carries the fulfilment choice.
 *  WHY a note: OrderCreate has delivery_date / slot / priority but NO
 *  delivery-mode field, so tagging the note is the smallest TRUTHFUL record
 *  the packing desk can actually read today. Upgrade path: a real
 *  `delivery_mode` on OrderCreate, at which point this tag goes away.
 *  Idempotent by construction -- re-tagging, or toggling back to take-away,
 *  neither doubles the tag nor eats the staff's own note. */
export const HOME_DELIVERY_TAG = '[HOME DELIVERY]';

export function withFulfilmentTag(
  note: string | null | undefined,
  homeDelivery: boolean,
): string {
  const base = (note || '').split(HOME_DELIVERY_TAG).join('').trim();
  const tagged = homeDelivery ? `${HOME_DELIVERY_TAG}${base ? ` ${base}` : ''}` : base;
  // POS-9: the server rejects a note over 500 chars, and losing the whole
  // order to a long note is worse than losing the note's tail.
  return tagged.slice(0, 500);
}

export function GeneralCounterSurface() {
  const { user } = useAuth();
  const store = usePOSStore();
  const activeStoreId = user?.activeStoreId || store.store_id;
  const onlineStoreActive = useIsOnlineStore(activeStoreId);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  // The cart line whose discount is being edited. The cart IS the review
  // step on this surface, so the trigger lives on the line itself.
  const [discountLine, setDiscountLine] = useState<CartLineItem | null>(null);
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('');
  const [homeDelivery, setHomeDelivery] = useState(false);
  const [addCustomerOpen, setAddCustomerOpen] = useState(false);
  // The finished sale, held so the completion screen can print and send
  // against it (same wiring as BillingSurface). Cleared by Done, which also
  // resets the till for the next customer.
  const [completed, setCompleted] = useState<{
    orderId: string;
    orderNumber?: string;
    jobId?: string;
  } | null>(null);
  const idempotencyKeyRef = useRef<string | null>(null);

  const { data: products = [], isLoading } = useProducts({
    search: search || undefined,
    category: category || undefined,
    store_id: store.store_id || activeStoreId || undefined,
  });

  // ---- Guards (identical to the billing surface; backend enforces both) ---
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

  const cart = store.cart || [];
  // An optical line can only arrive here by scan, or on a cart carried over
  // from the optical POS. `is_optical` is stamped by the shared intake brain,
  // so this asks the ONE rule instead of re-listing categories.
  const opticalInCart = cart.some((i) => i.is_optical);

  const addProduct = (product: any) => {
    // Shared MONEY guard: offer>MRP and zero/NaN pricing are hard blocks.
    const guard = posPriceGuard(product);
    if (!guard.ok) {
      setErrorMsg(guard.message || 'Blocked');
      return;
    }
    setErrorMsg(null);
    store.addToCart(cartItemFromProduct(product, guard));
  };

  const handleScan = async (code: string) => {
    const res = await resolveBarcode(store.store_id || activeStoreId, code);
    if (res.ok && res.product) {
      addProduct(res.product);
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
      // This counter only ever rings a QUICK SALE. A 'prescription_order' left
      // in the store by the optical POS would be refused by the shared submit
      // brain ("requires at least one lens item") with no way to switch it
      // back from here.
      store.setSaleType('quick_sale');
      // The goods leave the counter today either way — general-counter stock is
      // on the shelf, nothing is being made. Left blank, the backend books
      // expected delivery 7 days out, which is wrong for a take-away AND for a
      // same-day drop. istDayString pins this to the IST business day (a UTC
      // date before 05:30 IST is YESTERDAY, which the server rejects as past).
      store.setDeliveryDate(istDayString(new Date()));
      store.setDeliveryTimeSlot(null);
      store.setCartNote(withFulfilmentTag(store.cart_note, homeDelivery));
      // Read the LIVE store, not this render's snapshot — the writes above
      // landed in the store, while `store` is the value from render time.
      const res = await submitPosOrder(usePOSStore.getState(), idempotencyKeyRef.current);
      if (!res.ok) {
        setErrorMsg(res.error || 'Failed to create order');
        return;
      }
      idempotencyKeyRef.current = null;
      if (res.warning) setErrorMsg(res.warning);
      // Land on the good completion screen (server-read totals, PDF, sends,
      // scorecard) -- the same one BillingSurface shows, in its COUNTER stage:
      // the tax invoice is the primary print (owner 2026-09-04: the counter
      // always issues the tax invoice). The legacy StepComplete recomputed the
      // bill in the browser and offered none of that; it remains only as the
      // reload fallback below.
      if (res.orderId) {
        setCompleted({
          orderId: res.orderId,
          orderNumber: res.orderNumber,
          jobId: res.fittingJobId,
        });
      }
    } catch (e: any) {
      // WITHOUT THIS the handler was `try { } finally { }` with no catch, so
      // anything that THREW escaped as an unhandled rejection: the spinner
      // cleared, no message appeared, and the operator saw a button that did
      // nothing at all. Owner, on the live screen: "complete sale button on
      // general counter not doing anything". submitPosOrder returns its
      // refusals rather than throwing, so what lands here is the unexpected
      // kind -- which is exactly the kind that must never be silent at a till.
      const detail = e?.response?.data?.detail;
      setErrorMsg(
        typeof detail === 'string'
          ? detail
          : `Could not complete the sale: ${e?.message || 'unexpected error'}. Nothing was charged.`,
      );
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

      {/* Owner rule: an optical item belongs on the optical POS (Rx, pair
          linking, workshop job). We PROMPT — never auto-navigate — because a
          jump would take the operator off the counter mid-sale. Cart and
          customer live in the same store, so the link carries them across. */}
      {!isComplete && opticalInCart && (
        <div className="mx-3 mt-2 bg-amber-50 border border-amber-200 rounded-lg p-2.5 flex items-center gap-2 text-sm text-amber-900">
          <Glasses className="w-4 h-4 flex-shrink-0" />
          <span className="flex-1">
            There is an optical item in this bill. Prescription, pair linking and the
            workshop job live on the optical POS — switch counters to finish it there.
            Your cart and customer come with you.
          </span>
          <Link
            to="/pos/new"
            className="min-h-[44px] px-3 inline-flex items-center rounded-lg border border-amber-400 bg-white text-amber-900 font-medium whitespace-nowrap"
          >
            Open optical POS
          </Link>
        </div>
      )}

      {/* Bill strip — the same slim context row as the billing surface: the
          salesperson is a CHIP, not a labelled form block. */}
      {!isComplete && (
        <div className="px-3.5 pt-2 pb-1 flex items-center gap-2 shrink-0">
          <span className="text-[10px] font-medium uppercase tracking-widest text-gray-500">
            General counter
          </span>
          <SalespersonPicker compact />
          <div className="flex-1" />
          {/* Hold / recall, shared with the billing surface. The store parks a
              cart automatically when the screen idles on EVERY surface, so
              without this a cart parked here was unrecoverable from here. */}
          <HeldBillsControls />
          <WalkinWalkoutControls />
          <span className="text-[11px] text-gray-500">
            {cart.length} item{cart.length === 1 ? '' : 's'}
          </span>
        </div>
      )}

      {completed ? (
        <CounterCompleteScreen
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
        <div className="flex-1 lg:min-h-0 flex flex-col gap-3 px-3.5 pb-3.5">
          {/* FULL-WIDTH customer strip: no Rx block sits beside it here, so the
              customer takes the whole width and the browse grid inherits the
              height the Rx block used to hold. */}
          <div className="rounded-xl border border-gray-200 bg-white p-3 shrink-0">
            {store.customer ? (
              /* Change = clear the pick; a wrong pick could otherwise only be
                 undone by reloading the page. */
              <CustomerCardWithLoyalty onChange={() => store.setCustomer(null)} />
            ) : (
              <div>
                <div className="text-[10px] font-medium uppercase tracking-widest text-gray-500 mb-1.5">
                  Customer <span className="text-red-500">*</span>
                </div>
                <CustomerSearchBar store={store} />
                <div className="mt-1.5 flex items-center gap-2">
                  <span className="text-[11px] text-gray-500 flex-1">
                    Every bill needs a customer — no anonymous sale on any counter.
                  </span>
                  {/* Same modal and same shared creator (createAndSelectCustomer)
                      the billing surface uses -- a first-time customer could not
                      be billed on this counter at all before this. */}
                  <button
                    type="button"
                    onClick={() => setAddCustomerOpen(true)}
                    className="shrink-0 min-h-[44px] px-2.5 rounded-lg border border-gray-200 bg-white text-[11px] font-medium text-gray-700 hover:bg-gray-50"
                  >
                    + New customer
                  </button>
                </div>
              </div>
            )}
          </div>

          <div className="flex-1 lg:min-h-0 flex flex-col lg:flex-row gap-3.5">
            {/* ── LEFT: scan, browse, grid ── */}
            <div className="flex-1 min-w-0 flex flex-col gap-3">
              <div className="shrink-0">
                <BarcodeScanner
                  onScan={handleScan}
                  onManualSearch={setSearch}
                  placeholder="Scan barcode or search watches, perfumes, accessories…"
                  autoFocus
                />
              </div>

              {/* Category chips — browse is how this counter sells; its stock is
                  picked off a shelf, not read off a job card. */}
              <div className="shrink-0 flex gap-2 overflow-x-auto pb-0.5">
                <button
                  type="button"
                  onClick={() => setCategory('')}
                  className={
                    'min-h-[44px] px-4 rounded-lg border text-sm font-medium whitespace-nowrap ' +
                    (category
                      ? 'bg-white text-gray-700 border-gray-200'
                      : 'bg-gray-900 text-white border-gray-900')
                  }
                >
                  All
                </button>
                {COUNTER_CHIPS.map((c) => (
                  <button
                    key={c.value}
                    type="button"
                    onClick={() => setCategory(category === c.value ? '' : c.value)}
                    className={
                      'min-h-[44px] px-4 rounded-lg border text-sm font-medium whitespace-nowrap ' +
                      (category === c.value
                        ? 'bg-gray-900 text-white border-gray-900'
                        : 'bg-white text-gray-700 border-gray-200')
                    }
                  >
                    {c.label}
                  </button>
                ))}
              </div>

              {/* The grid is the big surface here. It scrolls INSIDE its own box
                  — the page itself never scrolls (spec 11b). */}
              <div className="flex-1 min-h-0 overflow-y-auto rounded-xl border border-gray-200 bg-white p-2">
                {isLoading ? (
                  <div className="grid grid-cols-2 tablet:grid-cols-3 laptop:grid-cols-4 gap-2">
                    {[...Array(8)].map((_, i) => (
                      <div key={i} className="rounded-xl border border-gray-200 p-2 animate-pulse">
                        <div className="h-20 bg-gray-100 rounded-lg mb-2" />
                        <div className="h-3.5 bg-gray-100 rounded w-3/4 mb-1" />
                        <div className="h-3 bg-gray-100 rounded w-1/2" />
                      </div>
                    ))}
                  </div>
                ) : (products as any[]).length === 0 ? (
                  <div className="h-full flex items-center justify-center text-center text-sm text-gray-500 p-6">
                    {search || category
                      ? 'Nothing in stock matches that. Try another category or search.'
                      : 'Pick a category, or scan the item to start the bill.'}
                  </div>
                ) : (
                  <>
                    {/* The card itself (id/offer/stock reads, badges, disabled
                        states) is ProductResultsStrip's <ProductCard> -- ONE
                        implementation for both tills. Only the surrounding
                        grid layout belongs to this surface. */}
                    <div className="grid grid-cols-2 tablet:grid-cols-3 laptop:grid-cols-4 gap-2">
                      {(products as any[])
                        .slice(0, MAX_PRODUCT_RESULTS)
                        .map((product: any) => (
                          <ProductCard
                            key={productIdOf(product) || product.sku}
                            product={product}
                            layout="grid"
                            onPick={() => addProduct(product)}
                          />
                        ))}
                    </div>
                    {(products as any[]).length > MAX_PRODUCT_RESULTS && (
                      <p className="mt-2 text-center text-[11px] text-gray-500">
                        Showing the first {MAX_PRODUCT_RESULTS} — narrow the
                        search or pick a category to see the rest.
                      </p>
                    )}
                  </>
                )}
              </div>

              <div className="shrink-0">
                <PosWidgets />
              </div>
            </div>

            {/* ── RIGHT: cart + payment, always visible (430px per mockup) ── */}
            <div className="w-full lg:w-[430px] shrink-0 lg:min-h-0 flex flex-col lg:grid gap-3 lg:grid-rows-[minmax(0,1fr)_minmax(0,auto)_auto_auto]">
              {/* Plain cart: pair chips never appear because nothing on this
                  counter is optical — CartSidebar renders them for optical
                  lines only, so the same component IS the plain cart. */}
              <div className="min-h-0 overflow-y-auto rounded-xl border border-gray-200 bg-white">
                <CartSidebar onOpenDiscount={setDiscountLine} />
              </div>

              <div className="min-h-0 overflow-y-auto flex flex-col gap-3">
                <BillDiscountCard />
                <StepPayment />
              </div>

              {/* Take away now / Home delivery — this counter's answer to "when
                  does it leave", replacing the optical delivery date + slot. */}
              <div className="shrink-0 rounded-xl border border-gray-200 bg-white p-2">
                <div className="text-[10px] font-medium uppercase tracking-widest text-gray-500 mb-1.5 px-1">
                  Handover
                </div>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => setHomeDelivery(false)}
                    className={
                      'flex-1 min-h-[44px] rounded-lg border text-sm font-medium inline-flex items-center justify-center gap-2 ' +
                      (homeDelivery
                        ? 'bg-white text-gray-700 border-gray-200'
                        : 'bg-gray-900 text-white border-gray-900')
                    }
                  >
                    <ShoppingBag className="w-4 h-4" />
                    Take away now
                  </button>
                  <button
                    type="button"
                    onClick={() => setHomeDelivery(true)}
                    className={
                      'flex-1 min-h-[44px] rounded-lg border text-sm font-medium inline-flex items-center justify-center gap-2 ' +
                      (homeDelivery
                        ? 'bg-gray-900 text-white border-gray-900'
                        : 'bg-white text-gray-700 border-gray-200')
                    }
                  >
                    <Home className="w-4 h-4" />
                    Home delivery
                  </button>
                </div>
                {homeDelivery && (
                  <p className="mt-1.5 px-1 text-[11px] text-gray-500">
                    Goes out today to the address on this customer's account — the bill is
                    tagged for the packing desk.
                  </p>
                )}
                {/* cart_note is the SAME bill-level note field the billing
                    surface writes (DeliveryOptionsRow) and this counter's own
                    submit already tags for a home delivery
                    (withFulfilmentTag) -- staff just had no box to type one. */}
                <input
                  type="text"
                  aria-label="Note for this bill"
                  title="Note for this bill"
                  value={store.cart_note || ''}
                  onChange={(e) => store.setCartNote(e.target.value)}
                  placeholder="Quick note (gift wrap, call before delivery…)"
                  className="mt-2 w-full min-h-[44px] px-2 rounded-lg border border-gray-200 bg-white text-sm text-gray-900"
                />
              </div>

              <button
                type="button"
                onClick={handleCompleteSale}
                disabled={store.is_processing || cart.length === 0}
                className="h-12 shrink-0 rounded-xl bg-gray-900 text-white font-semibold text-base disabled:opacity-40"
              >
                {store.is_processing ? 'Saving…' : 'Complete sale'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Mounted unconditionally: the whole point is to reach it when there
          is NO customer on the bill yet. It renders nothing while closed. */}
      <AddCustomerModal
        isOpen={addCustomerOpen}
        onClose={() => setAddCustomerOpen(false)}
        onSave={async (data) => {
          await createAndSelectCustomer(store, data);
          setAddCustomerOpen(false);
        }}
        /* The number belongs to a family member on another account: the modal
           offers promote-to-own-account / open-existing and hands back the
           resulting customer here, so the SALE CONTINUES on this till instead
           of navigating away to /customers. Same selector the search bar uses. */
        onSelectExisting={(c: any) => {
          selectCustomerHit(store, {
            kind: 'account',
            customer: c,
            accountName: c?.name || '',
            displayName: c?.name || '',
            phone: c?.phone || c?.mobile || '',
            key: String(c?.id || c?.customer_id || ''),
          });
          setAddCustomerOpen(false);
        }}
      />

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

export default GeneralCounterSurface;
