// ============================================================================
// IMS 2.0 - POS delivery / pickup surface (Wave 4, owner spec 1-iii)
// ============================================================================
// The second POS surface: the customer collects. Find the order (scan the job
// card barcode or type the order number), run the handover checks, collect any
// balance, hand over — in ONE action.
//
// Ponytail: this screen owns NO money logic and NO payment UI of its own. The
// tender block IS the till's — `StepPayment` from components/pos/POSPayment,
// the same component /pos/new and /pos/counter render — driven by a
// `PaymentTarget` that says "the amount due is this ORDER's balance, not a
// cart total". That is why the counter now has split tender, per-leg
// references, EMI and the cash-denomination capture, and why it looks like the
// billing screen: it IS the billing screen's payment surface.
//
// It calls the merged /orders/{id}/deliver-with-payment door, which delegates
// to the very same add_payment + deliver_order handlers the Orders screen
// uses, so every guard (over-tender, credit limit, QC gate, Rx hold, atomic
// claim, and the credit-delivery manager gate) runs verbatim. The balance
// shown here is the server's balance_due, never a local recomputation.
//
// Owner rulings honoured: handover checks are ADVISORY (each tick is
// name-stamped, none of them block — audit MCQ round); delivering with money
// still owed needs a manager, or a manager's approval token pasted here;
// viewport-locked like the billing surface (spec 11b).

import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, X, PackageCheck } from 'lucide-react';
import { useAuth } from '../../../context/AuthContext';
import { orderApi } from '../../../services/api/sales';
import { BarcodeScanner } from '../../../components/pos/BarcodeScanner';
import { StepPayment, type PaymentTarget } from '../../../components/pos/POSPayment';
import { SalespersonPicker } from '../../../components/pos/SalespersonPicker';
import { buildPaymentBody } from '../../../components/pos/paymentBody';
import { usePOSStore, type CashTenderCapture, type PaymentEntry } from '../../../stores/posStore';
import type { Order } from '../../../types';
import DeliveryCompleteScreen from './DeliveryCompleteScreen';

const money = (v: number) => `₹${Math.round(v || 0).toLocaleString('en-IN')}`;

// GET /orders/{id} runs order_to_frontend (backend orders.py:284), which RENAMES
// the snake keys -- order_id -> id, balance_due -> balanceDue, status ->
// orderStatus -- and the axios interceptor only ever adds camel aliases TO snake
// keys, never the reverse (client.ts:337). So the wire shape here is camelCase.
// Reuse the canonical Order type rather than restating its field names: a second
// hand-written shape is exactly how the two halves drifted apart in the first
// place. Partial<> because this screen only needs a handful of the fields.
type LoadedOrder = Partial<Order> & { id: string };

export function DeliverySurface() {
  const { user } = useAuth();
  const [order, setOrder] = useState<LoadedOrder | null>(null);
  // A name search can hit several waiting jobs (a family shares a surname, and
  // one customer can have two pairs on the shelf). Picking the wrong one hands
  // the wrong goods over, so several matches must be CHOSEN from, never guessed.
  const [matches, setMatches] = useState<LoadedOrder[]>([]);
  const [busy, setBusy] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);
  // The handed-over order, held so the completion screen can print the
  // final invoice and send the thank-you against it.
  const [handedOver, setHandedOver] = useState<LoadedOrder | null>(null);
  const handoverSessionRef = useRef<string | null>(null);
  // WHICH legs of the current attempt already REACHED the server, keyed by the
  // leg itself. A retry after a mid-split failure skips exactly those: money
  // collected once must never be posted twice because a later leg's call
  // failed. Keyed by leg, not by index, because the operator may well edit the
  // tender before retrying -- an index would then skip the WRONG leg and
  // silently lose a payment that was actually taken.
  const postedLegsRef = useRef<Set<string>>(new Set());
  const legKey = (p: PaymentEntry) => `${p.timestamp}|${p.method}|${p.amount}`;

  // Handover checks — advisory only, name-stamped on the order.
  const [fitCheck, setFitCheck] = useState(false);
  const [cleaned, setCleaned] = useState(false);
  const [pickedUpBy, setPickedUpBy] = useState('');
  const [handoverNote, setHandoverNote] = useState('');

  // Money: the tender legs the counter has taken for THIS handover. Held
  // locally, NOT in posStore — the cart on the till is a different bill and
  // must not be touched. StepPayment reads and writes them via the target.
  const [payments, setPayments] = useState<PaymentEntry[]>([]);
  const [cashTender, setCashTender] = useState<CashTenderCapture | null>(null);
  const [approvalToken, setApprovalToken] = useState('');

  const balance = Number(order?.balanceDue || 0);
  const collected =
    Math.round(payments.reduce((s, p) => s + (Number(p.amount) || 0), 0) * 100) / 100;
  const shortfall = Math.max(0, Math.round((balance - collected) * 100) / 100);

  // ---- Who handed the goods over (owner 2026-09-02) ----------------------
  // The SAME picker the billing surface uses, so the same manager-tier rule
  // applies: below Store Manager it is locked to the signed-in user; a manager
  // may name someone else. It reads/writes posStore.salesperson_id, which is
  // the BILL's attribution and feeds incentives — so this screen BORROWS that
  // field and puts back exactly what it found on unmount. Naming who handed a
  // pair over must never re-credit a bill still open at the till.
  const deliveredById = usePOSStore((s) => s.salesperson_id);
  const deliveredByName = usePOSStore((s) => s.salesperson_name);
  useEffect(() => {
    const { salesperson_id, salesperson_name, setSalesperson } = usePOSStore.getState();
    const selfName =
      (user as any)?.name || (user as any)?.full_name || (user as any)?.username || 'You';
    if (user?.id) setSalesperson(user.id, selfName);
    return () => usePOSStore.getState().setSalesperson(salesperson_id, salesperson_name);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id]);

  // The seam into the shared till (components/pos/POSPayment). Everything the
  // billing screen can do with a tender, this counter can now do against the
  // order's balance — split legs, per-leg references, EMI, note-by-note cash.
  const paymentTarget: PaymentTarget = {
    due: balance,
    payments,
    addPayment: (p) =>
      setPayments((list) => [...list, { ...p, timestamp: new Date().toISOString() }]),
    removePayment: (i) => setPayments((list) => list.filter((_, idx) => idx !== i)),
    setCashTender,
    storeId: order?.storeId || user?.activeStoreId,
    // Owner 2026-09-04 ("yes, enable it"): the ORDER's customer, so the
    // store-credit tender renders against this handover's balance. camelCase
    // is the wire shape order_to_frontend emits (deliverySurfaceWireShape).
    // The server redeems against the order's customer whatever id is sent;
    // this only scopes the balance the till displays.
    customerId: order?.customerId,
  };

  const resetTender = () => {
    setPayments([]);
    setCashTender(null);
    setApprovalToken('');
    postedLegsRef.current = new Set();
    handoverSessionRef.current = null;
  };

  const findOrder = async (ref: string) => {
    const q = (ref || '').trim();
    if (!q) return;
    setErrorMsg(null);
    setOkMsg(null);
    setMatches([]);
    try {
      // A SCAN is an order id or number, so try the direct read first — it is
      // the common case and a single-record lookup carries no browse window.
      let doc: any = null;
      try {
        doc = await orderApi.getOrder(q);
      } catch {
        // Not an id. Search the delivery QUEUE by order number, customer name
        // or phone. This replaces a fallback that fetched the newest 20 orders
        // of any status and matched the number exactly client-side: it could
        // not find a name, could not find a phone, and silently missed the
        // 21st job. The queue endpoint applies the 30-day horizon server-side
        // and lifts it when the query names one customer.
        const res: any = await orderApi.getPendingDelivery({
          storeId: user?.activeStoreId,
          q,
        });
        const rows: any[] = res?.orders || [];
        if (rows.length > 1) {
          setMatches(rows);
          setOrder(null);
          return;
        }
        doc = rows[0] || null;
      }
      if (!doc?.id) {
        setErrorMsg(
          `Nothing awaiting collection for "${q}". Try the job card number, ` +
            `the customer's name, or their phone number.`,
        );
        setOrder(null);
        return;
      }
      selectOrder(doc);
    } catch {
      setErrorMsg('Could not load that order. Try again.');
    }
  };

  /** Commit to one order. A different order means a different balance and a
   *  different idempotency key — nothing from the last attempt may carry over. */
  const selectOrder = (doc: any) => {
    resetTender();
    setMatches([]);
    setOrder(doc);
  };

  const handOver = async () => {
    if (!order || busy) return;
    setBusy(true);
    setErrorMsg(null);
    // A HANDOVER SESSION id, not the delivery key. The key itself is derived
    // per attempt from the leg that rides the deliver door (see below): a
    // single key reused across attempts whose LAST LEG had changed matched the
    // previous attempt's payment on the server and swallowed the new money
    // entirely - cash in the drawer with no payment row against it.
    if (!handoverSessionRef.current) {
      handoverSessionRef.current =
        typeof crypto !== 'undefined' && crypto.randomUUID
          ? crypto.randomUUID()
          : `idem-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    }
    try {
      const handover: Record<string, unknown> = {};
      if (pickedUpBy.trim()) handover.picked_up_by_name = pickedUpBy.trim();
      if (fitCheck) handover.fit_check_done = true;
      if (cleaned) handover.cleaned_and_cased = true;
      if (handoverNote.trim()) handover.notes = handoverNote.trim();
      // The STAFF side of the handover — picked_up_by_name above is the
      // CUSTOMER side. Keys agreed with the backend half of this change:
      // handover.delivered_by_id / handover.delivered_by_name.
      if (deliveredById) {
        handover.delivered_by_id = deliveredById;
        if (deliveredByName) handover.delivered_by_name = deliveredByName;
      }

      // ONE body builder for every leg — the same one submitOrder uses, so an
      // EMI leg and the optional note-by-note cash record are shaped
      // identically here and at the till. The capture rides the FIRST cash leg
      // only (the customer handed one wad over once).
      let capture: CashTenderCapture | null = cashTender;
      const legs = payments.map((p) => {
        const body = buildPaymentBody(p, p.method === 'CASH' ? capture : null);
        if (p.method === 'CASH') capture = null;
        return { key: legKey(p), body };
      });

      // Every leg but the last goes through the payments door; the last rides
      // the deliver door. That is the SAME server sequence either way —
      // deliver_with_payment literally calls add_payment first — and it keeps
      // the Idempotency-Key on the single-leg case, which is what the counter
      // does on nearly every handover.
      for (const leg of legs.slice(0, -1)) {
        if (postedLegsRef.current.has(leg.key)) continue;
        // Keyed on the LEG, so a re-send of the same tender is a server-side
        // replay rather than a second payment row. postedLegsRef alone was not
        // enough: the leg that rode the deliver door was never recorded in it,
        // so a retry posted that money a second time through this loop.
        await orderApi.addPayment(
          order.id,
          leg.body as any,
          `${handoverSessionRef.current}:${leg.key}`,
        );
        postedLegsRef.current.add(leg.key);
      }
      const last = legs.length ? legs[legs.length - 1] : undefined;
      const lastLeg = last?.body;
      // The delivery key CHANGES when the last leg changes. Reusing one key
      // across attempts made the server match the earlier attempt's payment
      // and record nothing for the new tender.
      const deliverKey = `${handoverSessionRef.current}:${last?.key ?? 'no-tender'}`;

      await orderApi.deliverWithPayment(
        order.id,
        {
          // The wire type here predates the EMI / cash-record fields on a leg;
          // the server's PaymentCreate accepts the fuller body, so it is built
          // once by the shared builder and passed straight through.
          payment: lastLeg as unknown as { method: string; amount: number; reference?: string },
          handover: Object.keys(handover).length ? (handover as any) : undefined,
          approval_token: approvalToken.trim() || undefined,
        },
        deliverKey,
      );
      setOkMsg(
        `Delivered — ${order.orderNumber || order.id}` +
          (collected > 0 ? ` · collected ${money(collected)}` : '') +
          (shortfall > 0 ? ` · ${money(shortfall)} booked as outstanding` : ''),
      );
      // Hand over to the completion screen: final invoice, care card, and the
      // delivered-thank-you the server has already queued.
      setHandedOver(order);
      setOrder(null);
      setPickedUpBy('');
      setHandoverNote('');
      setFitCheck(false);
      setCleaned(false);
      resetTender();
    } catch (err: any) {
      // The server owns the refusal (credit gate, QC gate, Rx hold, status).
      // A 422 detail is an ARRAY of objects — rendering that straight into JSX
      // white-screens the counter, so anything that is not a plain string
      // falls back to the generic line.
      const detail = err?.response?.data?.detail;
      const reason = typeof detail === 'string' ? detail : 'Could not complete the handover.';

      // RESYNC, because the deliver door is not atomic: it records the payment
      // and THEN runs the credit gate, so a refusal can leave money already
      // banked against this order. The screen's balance is then stale and too
      // HIGH, and the honest-looking next step -- take the rest and press again
      // -- is what double-charged the customer. The server is the authority on
      // what has been paid, so re-read it and clear the pending tender: any leg
      // that landed is now inside amountPaid, and the operator enters only what
      // is genuinely still owed.
      try {
        const fresh: any = await orderApi.getOrder(order.id);
        if (fresh?.id) {
          const wasDue = balance;
          const nowDue = Number(fresh.balanceDue || 0);
          setOrder(fresh);
          resetTender();
          setErrorMsg(
            nowDue < wasDue
              ? `${reason} ${money(wasDue - nowDue)} was recorded before it stopped; ` +
                `${money(nowDue)} is still due. Re-enter only the outstanding amount.`
              : reason,
          );
          return;
        }
      } catch {
        /* the re-read failed too; fall through to the plain refusal below */
      }
      setErrorMsg(reason);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-full lg:h-full lg:min-h-0 flex flex-col overflow-y-auto lg:overflow-hidden bg-gray-50">
      {(errorMsg || okMsg) && (
        <div
          className={
            'mx-3.5 mt-2 rounded-lg p-2.5 flex items-center gap-2 text-sm ' +
            (errorMsg
              ? 'bg-red-50 border border-red-200 text-red-700'
              : 'bg-green-50 border border-green-200 text-green-800')
          }
        >
          {errorMsg ? <AlertTriangle className="w-4 h-4 shrink-0" /> : <PackageCheck className="w-4 h-4 shrink-0" />}
          <span className="flex-1">{errorMsg || okMsg}</span>
          <button onClick={() => { setErrorMsg(null); setOkMsg(null); }} aria-label="Dismiss" title="Dismiss">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* Handover strip — the billing surface's bill strip, same idiom: a slim
          context row carrying the staff CHIP, not a labelled form block
          (owner: the labelled field "takes up too much space"). */}
      {!handedOver && (
        <div className="px-3.5 pt-2 pb-1 flex items-center gap-2 shrink-0">
          <span className="text-[10px] font-medium uppercase tracking-widest text-gray-500">
            Handing over
          </span>
          <SalespersonPicker compact />
          <div className="flex-1" />
          <span className="text-[11px] text-gray-500">
            {order ? order.orderNumber || order.id : 'Scan a job card'}
          </span>
        </div>
      )}

      {handedOver ? (
        <DeliveryCompleteScreen
          orderId={handedOver.id}
          orderNumber={handedOver.orderNumber}
          // A DELIVERY belongs to whoever handed it over — that is whose day
          // the scorecard counts it on, not the person who sold it months ago.
          salespersonId={deliveredById || handedOver.salespersonId}
          salespersonName={deliveredByName || handedOver.salespersonName}
          onDone={() => {
            setHandedOver(null);
            setOkMsg(null);
          }}
        />
      ) : (
      <div className="flex-1 lg:min-h-0 flex flex-col lg:flex-row gap-3.5 px-3.5 pb-3.5">
        {/* LEFT: find the order + handover checks */}
        <div className="flex-1 min-w-0 flex flex-col gap-3 lg:min-h-0">
          <div className="shrink-0">
            <BarcodeScanner
              onScan={findOrder}
              onManualSearch={findOrder}
              placeholder="Scan job card, or type order no. / customer name / phone…"
              autoFocus
            />
          </div>

          {matches.length > 1 && !order ? (
            <div className="rounded-xl border border-gray-200 bg-white p-3 shrink-0">
              <div className="text-xs font-semibold uppercase tracking-widest text-gray-500">
                {matches.length} jobs waiting — pick one
              </div>
              <ul className="mt-2 divide-y divide-gray-100">
                {matches.map((m) => (
                  <li key={m.id}>
                    <button
                      type="button"
                      onClick={() => selectOrder(m)}
                      className="w-full min-h-[44px] py-2 text-left hover:bg-gray-50 rounded-lg px-2"
                    >
                      <div className="text-sm font-medium text-gray-900 truncate">
                        {m.customerName || 'Customer'}
                      </div>
                      <div className="text-xs text-gray-500 truncate">
                        {m.orderNumber || m.id} · {m.customerPhone || 'no phone'}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {order ? (
            <div className="rounded-xl border border-gray-200 bg-white p-3 shrink-0">
              <div className="flex items-baseline justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-gray-900 truncate">
                    {order.customerName || 'Customer'}
                  </div>
                  <div className="text-xs text-gray-500 truncate">
                    {order.orderNumber || order.id} · {order.customerPhone || 'no phone'}
                  </div>
                </div>
                <span className="text-[11px] uppercase tracking-widest text-gray-500">{order.orderStatus}</span>
              </div>
              <ul className="mt-2 space-y-0.5">
                {(order.items || []).slice(0, 4).map((it, i) => (
                  <li key={i} className="text-xs text-gray-600 truncate">
                    {it.quantity ?? 1} × {it.productName || 'Item'}
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-gray-300 bg-white p-6 text-center text-sm text-gray-500 shrink-0">
              Scan the job card to bring up the order.
            </div>
          )}

          {/* Handover checks — ADVISORY (owner ruling): they never block. */}
          {order && (
            <div className="rounded-xl border border-gray-200 bg-white p-3 shrink-0 space-y-2">
              <div className="text-[10px] font-medium uppercase tracking-widest text-gray-500">
                Handover checks · advisory
              </div>
              <label className="flex items-center gap-2 min-h-[44px] text-sm">
                <input type="checkbox" className="w-5 h-5" checked={fitCheck} onChange={(e) => setFitCheck(e.target.checked)} />
                Power verified &amp; fitting adjusted
              </label>
              <label className="flex items-center gap-2 min-h-[44px] text-sm">
                <input type="checkbox" className="w-5 h-5" checked={cleaned} onChange={(e) => setCleaned(e.target.checked)} />
                Cleaned, case &amp; cloth given
              </label>
              <input
                value={pickedUpBy}
                onChange={(e) => setPickedUpBy(e.target.value)}
                placeholder="Collected by (if not the customer)"
                className="w-full h-11 px-3 rounded-lg border border-gray-200 text-sm"
              />
              <input
                value={handoverNote}
                onChange={(e) => setHandoverNote(e.target.value)}
                placeholder="Handover note (optional)"
                className="w-full h-11 px-3 rounded-lg border border-gray-200 text-sm"
              />
            </div>
          )}
        </div>

        {/* RIGHT: money + hand over (430px — the billing till's own column) */}
        <div className="w-full lg:w-[430px] shrink-0 lg:min-h-0 flex flex-col gap-3">
          <div className="rounded-xl border border-gray-200 bg-white p-4 shrink-0">
            <div className="text-[10px] font-medium uppercase tracking-widest text-gray-500">
              Balance due
            </div>
            <div className={'text-3xl font-semibold ' + (balance > 0 ? 'text-red-600' : 'text-gray-900')}>
              {money(balance)}
            </div>
            {order && (
              <div className="mt-1 text-xs text-gray-500">
                {money(Number(order.amountPaid || 0))} of {money(Number(order.grandTotal || 0))} paid
              </div>
            )}
          </div>

          {/* THE TILL'S OWN PAYMENT SURFACE, pointed at this order's balance.
              Keyed on the order so scanning the next job card starts a clean
              tender instead of inheriting the last one's. */}
          <div className="lg:flex-1 lg:min-h-0 lg:overflow-y-auto flex flex-col gap-3">
            {order && balance > 0 && (
              <>
                <StepPayment key={order.id} target={paymentTarget} />
                {shortfall > 0 && (
                  <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 space-y-2">
                    <div className="text-[10px] font-medium uppercase tracking-widest text-amber-700">
                      Deliver on credit (khata) · {money(shortfall)} short
                    </div>
                    <p className="text-xs text-amber-800">
                      {money(shortfall)} of this bill will stay outstanding on the customer's
                      account. Managers can deliver on credit directly; other roles need a
                      manager-approved token.
                    </p>
                    <input
                      value={approvalToken}
                      onChange={(e) => setApprovalToken(e.target.value)}
                      placeholder="Manager approval token (if you are not a manager)"
                      className="w-full h-11 px-3 rounded-lg border border-amber-300 text-sm bg-white"
                    />
                  </div>
                )}
              </>
            )}
          </div>

          <button
            type="button"
            onClick={handOver}
            disabled={!order || busy}
            className="h-12 shrink-0 rounded-xl bg-gray-900 text-white font-semibold text-base disabled:opacity-40"
          >
            {busy
              ? 'Working…'
              : collected > 0
                ? `Collect ${money(collected)} & mark delivered`
                : 'Mark delivered'}
          </button>
        </div>
      </div>
      )}
    </div>
  );
}

export default DeliverySurface;
