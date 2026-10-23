// ============================================================================
// IMS 2.0 - Receive goods (/purchase/receive): THE receiving screen
// ============================================================================
// One door for every delivery (owner ruling: the classic /purchase/grn form
// is retired and forwards here). Landing: the receipts still waiting, the
// deliveries inbox (every receivable order in the Purchase shop scope,
// soonest first), the doors for goods bought without a PO or arriving on a
// delivery challan, and any other order (extra goods on a fully received one,
// a rejected delivery on one a cancel closed). Picking one opens ReceiveForm.
// ?po_id= (the Purchase Orders "Receive" button, the order timeline) opens
// that order directly. Receipt history (with the GRN print) is one tap away.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { AlertCircle, FileText, Inbox, Loader2, Package, Truck } from 'lucide-react';
import { vendorsApi } from '../../services/api/inventory';
import { useToast } from '../../context/ToastContext';
import { useIsOnlineStore } from '../../hooks/useIsOnlineStore';
import { useStores } from '../../hooks/usePOSQueries';
import { UnitLabelsModal } from '../../components/labels/UnitLabelsModal';
import { PurchaseStatusChip } from '../../components/purchase/PurchaseStatusChip';
import { RECEIVABLE_PO_STATUSES } from './purchaseTypes';
import { PurchaseShopName, PurchaseShopPicker, shopNameOf, usePurchaseShop } from './purchaseShop';
import { PendingReceipts } from './PendingReceipts';
import { ReceiptHistory } from './ReceiptHistory';
import { ReceiveForm } from './ReceiveForm';
import { isRejectOnlyOrder, pendingUnits, toCandidate, type CandidatePO, type ReceiveKind } from './receiveLines';

const fmtDate = (iso: string | null) => {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString('en-IN');
};

/** Soonest expected first; undated boxes sink to the bottom. */
const soonestFirst = (a: CandidatePO, b: CandidatePO) => {
  if (a.expected_date && b.expected_date) return a.expected_date.localeCompare(b.expected_date);
  if (a.expected_date) return -1;
  if (b.expected_date) return 1;
  return a.po_number.localeCompare(b.po_number);
};

export function ReceiveGoods() {
  const toast = useToast();
  const { storeId: scope, ownStoreId, canPick } = usePurchaseShop();
  const storeId = scope || '';
  const onlineStore = useIsOnlineStore(storeId);
  const { data: stores } = useStores();

  const [orders, setOrders] = useState<CandidatePO[] | null>(null);
  const [form, setForm] = useState<{ po: CandidatePO | null; kind: ReceiveKind; n: number } | null>(null);
  const [view, setView] = useState<'deliveries' | 'history'>('deliveries');
  const [refreshKey, setRefreshKey] = useState(0);
  const [highlight, setHighlight] = useState<string | null>(null);
  const [labels, setLabels] = useState<{ grnId: string; grnNumber: string } | null>(null);

  // Every order a receipt can be logged against, filtered by the server
  // before it pages (an old cancel-closed order never hides behind 50 drafts).
  const loadOrders = useCallback(async () => {
    try {
      const res = await vendorsApi.getPurchaseOrders({ ...(storeId ? { store_id: storeId } : {}), receipt_candidates: true });
      const raw = Array.isArray(res?.purchase_orders) ? (res.purchase_orders as Array<Record<string, unknown>>) : [];
      const seen = new Set<string>();
      setOrders(
        raw.map(toCandidate).filter((p) => p.po_id && !seen.has(p.po_id) && !!seen.add(p.po_id)),
      );
    } catch {
      setOrders([]);
    }
  }, [storeId]);

  useEffect(() => {
    void loadOrders();
  }, [loadOrders]);

  const inbox = useMemo(
    () =>
      (orders ?? [])
        .filter((p) => (RECEIVABLE_PO_STATUSES as readonly string[]).includes(p.status) && !p.closed_by_cancel)
        .sort(soonestFirst),
    [orders],
  );
  const extraOrders = (orders ?? []).filter((p) => p.status === 'RECEIVED' && !isRejectOnlyOrder(p));
  const rejectedOrders = (orders ?? []).filter(isRejectOnlyOrder);

  // An admin narrowing the shop: an order the screen no longer shows is
  // dropped, never received against.
  const shownScope = useRef(storeId);
  useEffect(() => {
    if (shownScope.current === storeId) return;
    shownScope.current = storeId;
    setForm((f) => (f?.po && storeId && f.po.delivery_store_id && f.po.delivery_store_id !== storeId ? null : f));
  }, [storeId]);

  const openOrder = (po: CandidatePO) => {
    setHighlight(null);
    setForm({ po, kind: 'STANDARD', n: Date.now() });
  };

  // Deep link: /purchase/receive?po_id= (vendor_id is accepted and unused).
  const [params] = useSearchParams();
  const deepLinkDone = useRef(false);
  useEffect(() => {
    if (deepLinkDone.current || orders === null) return;
    deepLinkDone.current = true;
    const poId = params.get('po_id');
    if (!poId) return;
    const po = orders.find((p) => p.po_id === poId);
    if (po) openOrder(po);
    else toast.warning('That order is not open for receiving at this shop.');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orders]);

  const openWithout = (kind: ReceiveKind) => {
    setHighlight(null);
    setForm({ po: null, kind, n: Date.now() });
  };
  const saved = () => {
    setRefreshKey((k) => k + 1);
    void loadOrders();
  };

  const nameShop = (id: string) => (id ? ` · delivers to ${shopNameOf(id, stores)}` : '');
  const emptyWhere = !storeId ? 'in any store' : canPick ? 'at the shop picked above' : 'at your shop';

  return (
    <>
      {labels && (
        <UnitLabelsModal
          grnId={labels.grnId}
          title="Print stock labels"
          subtitle={`Receipt ${labels.grnNumber}: one label per piece put on the shelf.`}
          onClose={() => setLabels(null)}
        />
      )}
      <div className="inv-body">
        <div className="inv-head">
          <div>
            <div className="eyebrow" style={{ marginBottom: 6 }}>Purchase &middot; receive</div>
            <h1>Receive goods.</h1>
            <p className="text-sm mt-1" style={{ color: 'var(--ink-4)' }}>
              Every box on its way to you. Tap one when it lands: bill first, count, then it goes on the shelf.
            </p>
          </div>
          <div className="flex items-center gap-3 flex-wrap">
            <PurchaseShopPicker />
            {form ? (
              <button type="button" className="btn sm" onClick={() => setForm(null)}>
                <Inbox className="w-3.5 h-3.5" /> All deliveries
              </button>
            ) : (
              <button
                type="button"
                className="btn sm"
                aria-pressed={view === 'history'}
                onClick={() => setView((v) => (v === 'history' ? 'deliveries' : 'history'))}
              >
                {view === 'history' ? <Inbox className="w-3.5 h-3.5" /> : <FileText className="w-3.5 h-3.5" />}
                {view === 'history' ? 'Deliveries' : 'Receipt history'}
              </button>
            )}
          </div>
        </div>

        {/* W1.4 / OS-006: an online store holds no stock (the server refuses too). */}
        {onlineStore && (
          <div className="p-4 mb-4 bg-blue-50 border border-blue-200 rounded-lg flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-blue-600 flex-shrink-0 mt-0.5" />
            <div className="text-sm text-blue-900">
              <p className="font-medium">You're on an online store — it holds no stock.</p>
              <p className="text-xs text-blue-800 mt-1">
                Goods must be received into a physical shop. Switch stores from the header dropdown; receiving here
                will be rejected.
              </p>
            </div>
          </div>
        )}

        {form ? (
          <ReceiveForm
            key={form.n}
            po={form.po}
            initialKind={form.kind}
            storeId={storeId}
            ownStoreId={ownStoreId || ''}
            onClose={() => setForm(null)}
            onSaved={saved}
            onOpenWaiting={(grnNumber) => {
              setForm(null);
              setView('deliveries');
              setHighlight(grnNumber || null);
              saved();
            }}
          />
        ) : view === 'history' ? (
          <ReceiptHistory storeId={storeId} refreshKey={refreshKey} />
        ) : (
          <>
            <PendingReceipts
              storeId={storeId}
              refreshKey={refreshKey}
              highlight={highlight}
              onAccepted={(grnId, grnNumber) => {
                if (highlight === grnNumber) setHighlight(null);
                setLabels({ grnId, grnNumber });
              }}
              onChanged={() => void loadOrders()}
            />

            <div className="card mb-4" style={{ padding: 16 }}>
              <div className="flex items-center gap-3 flex-wrap">
                <span className="text-sm" style={{ color: 'var(--ink-3)' }}>No purchase order?</span>
                <button type="button" className="btn" onClick={() => openWithout('NO_PO')}>
                  <Package className="w-4 h-4" /> Bought without PO
                </button>
                <button type="button" className="btn" onClick={() => openWithout('DELIVERY_CHALLAN')}>
                  <Truck className="w-4 h-4" /> Delivery challan, invoice comes later
                </button>
              </div>
            </div>

            {orders === null ? (
              <div className="card text-center py-12" style={{ color: 'var(--ink-4)' }}>
                <Loader2 className="w-6 h-6 mx-auto mb-3 animate-spin" />
                <p className="text-sm">Checking for deliveries…</p>
              </div>
            ) : inbox.length === 0 ? (
              <div className="card text-center py-12" style={{ color: 'var(--ink-4)' }}>
                <Package className="w-10 h-10 mx-auto mb-3" style={{ color: 'var(--ink-5)' }} />
                <p className="font-medium">No deliveries waiting</p>
                <p className="text-sm mt-1" style={{ color: 'var(--ink-5)' }}>
                  No open purchase orders to receive against {emptyWhere}. New boxes appear here the moment an order
                  is sent.
                </p>
              </div>
            ) : (
              <div className="grid grid-cols-1 tablet:grid-cols-2 gap-3">
                {inbox.map((row) => {
                  const due = pendingUnits(row);
                  return (
                    <div key={row.po_id} className="card" style={{ padding: 16 }}>
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <p className="font-semibold text-sm truncate" style={{ color: 'var(--ink)' }}>{row.vendor_name}</p>
                          <p className="mono text-xs mt-0.5" style={{ color: 'var(--ink-4)' }}>{row.po_number}</p>
                          {!storeId && row.delivery_store_id && (
                            <p className="text-xs mt-0.5" style={{ color: 'var(--ink-4)' }}>
                              For <PurchaseShopName storeId={row.delivery_store_id} />
                            </p>
                          )}
                          <p className="text-xs mt-1" style={{ color: 'var(--ink-4)' }}>
                            {row.items.length} item{row.items.length === 1 ? '' : 's'}
                            {due > 0 && ` · ${due.toLocaleString('en-IN')} units due`}
                            {fmtDate(row.expected_date) && ` · expected ${fmtDate(row.expected_date)}`}
                          </p>
                        </div>
                        <PurchaseStatusChip status={row.status} />
                      </div>
                      <button type="button" className="btn accent w-full mt-3 justify-center" onClick={() => openOrder(row)}>
                        <Package className="w-4 h-4" /> Receive this box
                      </button>
                    </div>
                  );
                })}
              </div>
            )}

            {(extraOrders.length > 0 || rejectedOrders.length > 0) && (
              <div className="card mt-4" style={{ padding: 16 }}>
                <label className="block text-xs font-medium mb-1" style={{ color: 'var(--ink-4)' }} htmlFor="receive-other-order">
                  Another order: extra goods on a fully received order, or a delivery on an order a cancel closed
                </label>
                <select
                  id="receive-other-order"
                  className="input w-full"
                  value=""
                  onChange={(e) => {
                    const po = (orders ?? []).find((p) => p.po_id === e.target.value);
                    if (po) openOrder(po);
                  }}
                >
                  <option value="">Pick an order…</option>
                  {extraOrders.length > 0 && (
                    <optgroup label="Count extra goods">
                      {extraOrders.map((p) => (
                        <option key={p.po_id} value={p.po_id}>
                          {p.po_number} · {p.vendor_name} · received — count extra goods{!storeId ? nameShop(p.delivery_store_id) : ''}
                        </option>
                      ))}
                    </optgroup>
                  )}
                  {rejectedOrders.length > 0 && (
                    <optgroup label="Log a rejected delivery">
                      {rejectedOrders.map((p) => (
                        <option key={p.po_id} value={p.po_id}>
                          {p.po_number} · {p.vendor_name} · closed by a cancel{!storeId ? nameShop(p.delivery_store_id) : ''}
                        </option>
                      ))}
                    </optgroup>
                  )}
                </select>
              </div>
            )}
          </>
        )}
      </div>
    </>
  );
}

export default ReceiveGoods;
