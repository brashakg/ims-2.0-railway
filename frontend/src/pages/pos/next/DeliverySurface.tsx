// ============================================================================
// IMS 2.0 - POS delivery / pickup surface (Wave 4, owner spec 1-iii)
// ============================================================================
// The second POS surface: the customer collects. Find the order (scan the job
// card barcode or type the order number), run the handover checks, collect any
// balance, hand over — in ONE action.
//
// Ponytail: this screen owns NO money logic. It calls the merged
// /orders/{id}/deliver-with-payment door, which delegates to the very same
// add_payment + deliver_order handlers the Orders screen uses, so every guard
// (over-tender, credit limit, QC gate, Rx hold, atomic claim, and the
// credit-delivery manager gate) runs verbatim. The balance shown here is the
// server's balance_due, never a local recomputation.
//
// Owner rulings honoured: handover checks are ADVISORY (each tick is
// name-stamped, none of them block — audit MCQ round); delivering with money
// still owed needs a manager, or a manager's approval token pasted here;
// viewport-locked like the billing surface (spec 11b).

import { useRef, useState } from 'react';
import { AlertTriangle, X, PackageCheck } from 'lucide-react';
import { useAuth } from '../../../context/AuthContext';
import { orderApi } from '../../../services/api/sales';
import { BarcodeScanner } from '../../../components/pos/BarcodeScanner';
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
  const [busy, setBusy] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);
  // The handed-over order, held so the completion screen can print the
  // final invoice and send the thank-you against it.
  const [handedOver, setHandedOver] = useState<LoadedOrder | null>(null);
  const idempotencyKeyRef = useRef<string | null>(null);

  // Handover checks — advisory only, name-stamped on the order.
  const [fitCheck, setFitCheck] = useState(false);
  const [cleaned, setCleaned] = useState(false);
  const [pickedUpBy, setPickedUpBy] = useState('');
  const [handoverNote, setHandoverNote] = useState('');

  // Money: collect all, part, or nothing (owner: credit delivery is allowed
  // with a manager). `collect` is what the till takes right now.
  const [collect, setCollect] = useState<string>('');
  const [method, setMethod] = useState<'CASH' | 'UPI' | 'CARD'>('CASH');
  const [approvalToken, setApprovalToken] = useState('');

  const balance = Number(order?.balanceDue || 0);
  const collectNum = Number(collect || 0);
  const shortfall = Math.max(0, Math.round((balance - collectNum) * 100) / 100);

  const findOrder = async (ref: string) => {
    const q = (ref || '').trim();
    if (!q) return;
    setErrorMsg(null);
    setOkMsg(null);
    try {
      // The scan/typed value is an order id or order number. Try the direct
      // read first; fall back to a store-scoped list lookup by number.
      let doc: any = null;
      try {
        doc = await orderApi.getOrder(q);
      } catch {
        const list: any = await orderApi.getOrders({
          storeId: user?.activeStoreId,
          limit: 20,
        });
        const rows = list?.orders || list || [];
        doc = rows.find(
          (o: any) => o.orderNumber === q || o.id === q,
        );
      }
      if (!doc?.id) {
        setErrorMsg(`No order found for "${q}". Check the job card or order number.`);
        setOrder(null);
        return;
      }
      setOrder(doc);
      setCollect(String(Math.max(0, Number(doc.balanceDue || 0))));
    } catch {
      setErrorMsg('Could not load that order. Try again.');
    }
  };

  const handOver = async () => {
    if (!order || busy) return;
    setBusy(true);
    setErrorMsg(null);
    if (!idempotencyKeyRef.current) {
      idempotencyKeyRef.current =
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

      await orderApi.deliverWithPayment(
        order.id,
        {
          payment: collectNum > 0 ? { method, amount: collectNum } : undefined,
          handover: Object.keys(handover).length ? (handover as any) : undefined,
          approval_token: approvalToken.trim() || undefined,
        },
        idempotencyKeyRef.current || undefined,
      );
      idempotencyKeyRef.current = null;
      setOkMsg(
        `Delivered — ${order.orderNumber || order.id}` +
          (collectNum > 0 ? ` · collected ${money(collectNum)}` : '') +
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
      setApprovalToken('');
    } catch (err: any) {
      // The server owns the refusal (credit gate, QC gate, Rx hold, status).
      setErrorMsg(err?.response?.data?.detail || 'Could not complete the handover.');
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

      {handedOver ? (
        <DeliveryCompleteScreen
          orderId={handedOver.id}
          orderNumber={handedOver.orderNumber}
          salespersonId={handedOver.salespersonId}
          salespersonName={handedOver.salespersonName}
          onDone={() => {
            setHandedOver(null);
            setOkMsg(null);
          }}
        />
      ) : (
      <div className="flex-1 lg:min-h-0 flex flex-col lg:flex-row gap-3.5 p-3.5">
        {/* LEFT: find the order + handover checks */}
        <div className="flex-1 min-w-0 flex flex-col gap-3 lg:min-h-0">
          <div className="shrink-0">
            <BarcodeScanner
              onScan={findOrder}
              onManualSearch={findOrder}
              placeholder="Scan job card or type the order number…"
              autoFocus
            />
          </div>

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
              <label className="flex items-center gap-2 min-h-[36px] text-sm">
                <input type="checkbox" checked={fitCheck} onChange={(e) => setFitCheck(e.target.checked)} />
                Power verified &amp; fitting adjusted
              </label>
              <label className="flex items-center gap-2 min-h-[36px] text-sm">
                <input type="checkbox" checked={cleaned} onChange={(e) => setCleaned(e.target.checked)} />
                Cleaned, case &amp; cloth given
              </label>
              <input
                value={pickedUpBy}
                onChange={(e) => setPickedUpBy(e.target.value)}
                placeholder="Collected by (if not the customer)"
                className="w-full h-10 px-3 rounded-lg border border-gray-200 text-sm"
              />
              <input
                value={handoverNote}
                onChange={(e) => setHandoverNote(e.target.value)}
                placeholder="Handover note (optional)"
                className="w-full h-10 px-3 rounded-lg border border-gray-200 text-sm"
              />
            </div>
          )}
        </div>

        {/* RIGHT: money + hand over */}
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

          {order && balance > 0 && (
            <div className="rounded-xl border border-gray-200 bg-white p-3 shrink-0 space-y-2">
              <div className="text-[10px] font-medium uppercase tracking-widest text-gray-500">
                Collect now
              </div>
              <div className="flex gap-2">
                {(['CASH', 'UPI', 'CARD'] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => setMethod(m)}
                    className={
                      'flex-1 min-h-[44px] rounded-lg border text-sm font-medium ' +
                      (method === m
                        ? 'bg-gray-900 text-white border-gray-900'
                        : 'bg-white text-gray-700 border-gray-200')
                    }
                  >
                    {m}
                  </button>
                ))}
              </div>
              <input
                type="number"
                value={collect}
                onChange={(e) => setCollect(e.target.value)}
                className="w-full h-11 px-3 rounded-lg border border-gray-200 text-lg font-semibold"
              />
              {shortfall > 0 && (
                <div className="rounded-lg bg-amber-50 border border-amber-200 p-2 space-y-1.5">
                  <p className="text-xs text-amber-800">
                    <strong>{money(shortfall)}</strong> will stay outstanding on this customer's
                    account. Managers can deliver on credit directly; other roles need a
                    manager-approved token.
                  </p>
                  <input
                    value={approvalToken}
                    onChange={(e) => setApprovalToken(e.target.value)}
                    placeholder="Manager approval token (if you are not a manager)"
                    className="w-full h-10 px-3 rounded-lg border border-amber-300 text-sm bg-white"
                  />
                </div>
              )}
            </div>
          )}

          <div className="hidden lg:block flex-1 min-h-0" />

          <button
            type="button"
            onClick={handOver}
            disabled={!order || busy}
            className="h-12 shrink-0 rounded-xl bg-gray-900 text-white font-semibold text-base disabled:opacity-40"
          >
            {busy
              ? 'Working…'
              : balance > 0 && collectNum > 0
                ? `Collect ${money(collectNum)} & mark delivered`
                : 'Mark delivered'}
          </button>
        </div>
      </div>
      )}
    </div>
  );
}

export default DeliverySurface;
