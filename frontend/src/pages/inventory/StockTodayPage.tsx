// ============================================================================
// IMS 2.0 - Stock today  (/inventory/today)        S7, owner approved 2026-10-23
// ============================================================================
// The Stock & supply landing page: what needs the shop's manager today, on
// one read-only page. Every figure comes from GET /inventory/today, which
// reads the rules main already has (transfers, stock counts, quarantine,
// open purchase orders, the stock value rule, the per-shop reorder level);
// this screen decides nothing of its own. The stock value card is drawn only
// when the server sent it - the server sends it to the accounts roles only.
//
// Each card links to the screen that does the job, and only for a role that
// screen's own route admits (the same lists, imported).
// TODO(S3): "Scan in" / "Pack and scan out" open the piece-by-piece transfer
// box once S3 lands; today they open the Transfers list.
// TODO(S6): "See them" opens Find a product once S6 lands; today the ledger.

import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { inventoryApi } from '../../services/api';
import type { StockToday } from '../../services/api/inventory';
import { formatDateIST, istDayString } from '../../utils/datetime';
import { getErrorMessage } from '../../utils/errorHandler';
import { RECEIVING_MANAGER_ROLES } from '../purchase/purchaseTypes';
import { INVENTORY_MANAGE_ROLES, INVENTORY_MODULE_ROLES } from './inventoryRoles';

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const money = (n: number) => `₹${Math.round(n).toLocaleString('en-IN')}`;

function JobCard({ chip, chipTone, title, children, to, action }: {
  chip: string; chipTone?: 'ok' | 'warn' | 'err' | 'info'; title: string; children: ReactNode;
  to?: string | null; action: string;
}) {
  return (
    <div className="card p-4 flex flex-col gap-2">
      <span className={`chip ${chipTone ?? ''}`} style={{ alignSelf: 'flex-start' }}>{chip}</span>
      <span className="font-semibold" style={{ fontSize: 15 }}>{title}</span>
      <span className="text-sm" style={{ color: 'var(--ink-3)' }}>{children}</span>
      {to && <Link to={to} className="font-medium text-sm" style={{ color: 'var(--bv)' }}>{action}</Link>}
    </div>
  );
}

export default function StockTodayPage() {
  const { user, hasRole } = useAuth();
  const storeId = user?.activeStoreId || undefined;
  const q = useQuery({
    queryKey: ['inventory', 'today', storeId ?? 'all'],
    queryFn: () => inventoryApi.getStockToday(storeId),
  });

  if (q.isPending) {
    return <div className="inv-body flex justify-center py-12"><Loader2 className="w-6 h-6 animate-spin" /></div>;
  }
  if (q.isError || !q.data) {
    return (
      <div className="inv-body">
        <p className="text-sm" style={{ color: 'var(--err)' }}>{getErrorMessage(q.error, 'Could not load today\'s stock jobs. Try again.')}</p>
      </div>
    );
  }

  const d: StockToday = q.data;
  const allShops = !d.store_id;
  const shopLabel = d.store_name || (allShops ? 'All shops' : d.store_id);
  const today = istDayString(new Date()) ?? '';
  const can = { module: hasRole(INVENTORY_MODULE_ROLES), count: hasRole(INVENTORY_MANAGE_ROLES), receive: hasRole([...RECEIVING_MANAGER_ROLES]) };
  const failed = new Set(d.unavailable ?? []);
  // A section the server could not read says so; it never reads as "nothing to do".
  const notRead = 'Could not be read just now. Refresh to try again.';

  // ---- the four job cards ---------------------------------------------------
  const inPieces = d.incoming.reduce((n, t) => n + t.pieces, 0);
  const senders = [...new Set(d.incoming.map((t) => t.from_store_name).filter(Boolean))].join(', ');
  const outPieces = d.requests.reduce((n, t) => n + t.pieces, 0);
  const asks = d.requests
    .flatMap((t) => t.lines.map((l) => `${t.to_store_name ?? 'A shop'} wants ${l.quantity} × ${l.product_name}`))
    .slice(0, 3).join(' · ');
  const open = d.count.in_progress[0];
  const scope = (c: typeof open) => c?.zone || c?.category || 'whole shop';
  const qz = d.quarantine;

  const late = d.deliveries.filter((p) => p.expected_date && p.expected_date < today).length;
  const due = (date?: string | null) => {
    if (!date) return <span style={{ color: 'var(--ink-4)' }}>no date given</span>;
    if (date < today) return <span className="chip warn">late · was {formatDateIST(date)}</span>;
    if (date === today) return <span className="chip warn">due today</span>;
    return formatDateIST(date);
  };

  return (
    <div className="inv-body space-y-5">
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Stock · {shopLabel}</div>
          <h1>What needs you today</h1>
          {allShops && (
            <p className="text-sm mt-1" style={{ color: 'var(--ink-4)' }}>Every shop together. Pick a shop at the top to see one.</p>
          )}
        </div>
      </div>

      <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))' }}>
        {failed.has('transfers') ? (
          <>
            <JobCard chip="Not read" chipTone="warn" title="Pieces coming from other shops" action="">{notRead}</JobCard>
            <JobCard chip="Not read" chipTone="warn" title="Other shops asked you for stock" action="">{notRead}</JobCard>
          </>
        ) : d.incoming.length > 0 ? (
          <JobCard chip={`${d.incoming.length} on the way`} chipTone="info" title={`Pieces coming from ${senders || 'another shop'}`}
            to={can.module ? '/inventory/transfers' : null} action="Scan in">
            {plural(inPieces, 'piece')} sent{d.incoming[0].shipped_at ? ` ${formatDateIST(d.incoming[0].shipped_at)}` : ''}. Scan them in when the box arrives.
          </JobCard>
        ) : (
          <JobCard chip="Nothing on the way" title="Pieces coming from other shops" action="">
            No box is on its way to this shop.
          </JobCard>
        )}

        {failed.has('transfers') ? null : d.requests.length > 0 ? (
          <JobCard chip={plural(d.requests.length, 'request')} chipTone="err" title="Other shops asked you for stock"
            to={can.module ? '/inventory/transfers' : null} action="Pack and scan out">
            {asks || `${plural(outPieces, 'piece')} to pack`}
          </JobCard>
        ) : (
          <JobCard chip="No requests" title="Other shops asked you for stock" action="">
            No shop is waiting on you.
          </JobCard>
        )}

        {failed.has('count') ? (
          <JobCard chip="Not read" chipTone="warn" title="Day-end count" action="">{notRead}</JobCard>
        ) : open ? (
          <JobCard chip="In progress" chipTone="warn" title={`Day-end count: ${scope(open)}`}
            to={can.count ? '/inventory/audit' : null} action="Continue counting">
            {d.count.in_progress.length > 1 ? `${d.count.in_progress.length} counts are open. ` : ''}
            Scan every piece, then press Finish.
          </JobCard>
        ) : d.count.counted_today > 0 ? (
          <JobCard chip="Counted today" chipTone="ok" title="Day-end count" to={can.count ? '/inventory/audit' : null} action="See the count">
            Today's count is done.
          </JobCard>
        ) : (
          <JobCard chip="Due tonight" title="Day-end count" to={can.count ? '/inventory/audit' : null} action="Start counting">
            Scan every piece before you close.
          </JobCard>
        )}

        {failed.has('quarantine') ? (
          <JobCard chip="Not read" chipTone="warn" title="Damaged pieces" action="">{notRead}</JobCard>
        ) : qz.unlabeled > 0 ? (
          <JobCard chip={`Quarantine · ${plural(qz.pieces, 'piece')}`} chipTone="err" title={`${plural(qz.unlabeled, 'piece')} waiting, print red labels`}
            to={can.module ? '/inventory/quarantine' : null} action="Print red labels">
            Damaged pieces kept off the shelf. Each gets a red DO NOT SHELVE label, then goes to Vendor returns.
          </JobCard>
        ) : (
          <JobCard chip={qz.pieces ? `Quarantine · ${plural(qz.pieces, 'piece')}` : 'Quarantine empty'} title="Damaged pieces"
            to={qz.pieces && can.module ? '/inventory/quarantine' : null} action="Open quarantine">
            {qz.pieces ? 'Every piece already has its red label.' : 'Nothing is waiting for a red label.'}
          </JobCard>
        )}
      </div>

      {/* ---- deliveries due + stock value ---------------------------------- */}
      <div className="flex gap-3 flex-wrap items-start">
        <div className="card overflow-hidden" style={{ flex: '999 1 560px', minWidth: 0 }}>
          <div className="flex gap-2 items-center flex-wrap px-4 py-3" style={{ borderBottom: '1px solid var(--line)' }}>
            <span className="font-semibold">Deliveries due</span>
            {late > 0 && <span className="chip warn">{late} late</span>}
            <span style={{ flex: 1 }} />
            {can.receive && <Link to="/purchase/receive" className="text-sm" style={{ color: 'var(--bv)' }}>Receive goods</Link>}
          </div>
          {d.deliveries.length === 0 ? (
            <p className="text-sm px-4 py-3" style={{ color: failed.has('deliveries') ? 'var(--warn)' : 'var(--ink-4)' }}>
              {failed.has('deliveries') ? notRead : 'No purchase order is waiting to be delivered here.'}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="tbl" style={{ minWidth: 560 }}>
                <thead>
                  <tr><th>Vendor</th><th>Order</th><th className="right">Pieces due</th><th>Expected</th>{can.receive && <th><span className="sr-only">Action</span></th>}</tr>
                </thead>
                <tbody>
                  {d.deliveries.map((p) => (
                    <tr key={p.po_id}>
                      <td>{p.vendor_name || p.vendor_id}</td>
                      <td className="mono">{p.po_number || p.po_id}</td>
                      <td className="right mono">{p.pieces_due}</td>
                      <td>{due(p.expected_date)}</td>
                      {can.receive && (
                        <td className="right">
                          <Link className="btn sm" to={`/purchase/receive?vendor_id=${encodeURIComponent(p.vendor_id ?? '')}&po_id=${encodeURIComponent(p.po_id)}`}>
                            Receive this box
                          </Link>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {failed.has('stock_value') && (
          <div className="card p-4 text-sm" style={{ flex: '1 1 300px', color: 'var(--warn)' }}>Stock value: {notRead}</div>
        )}
        {d.stock_value && (
          <div className="card p-4 flex flex-col gap-2" style={{ flex: '1 1 300px' }}>
            <div className="flex gap-2 items-center flex-wrap">
              <span className="eyebrow">Stock value · {shopLabel}</span>
              <span className="chip">admin and accountant only</span>
            </div>
            <div className="font-semibold" style={{ fontSize: 24 }}>{money(d.stock_value.total)}</div>
            <div className="text-xs" style={{ color: 'var(--ink-4)' }}>Every piece here, on the shelf or held for an order, at what it cost.</div>
            {d.stock_value.uncosted_pieces > 0 && (
              <div className="flex gap-2 flex-wrap text-sm px-3 py-2 rounded-lg" style={{ background: 'var(--warn-50)', color: 'var(--warn)' }}>
                <span style={{ flex: '1 1 180px' }}>{plural(d.stock_value.uncosted_pieces, 'piece')} {d.stock_value.uncosted_pieces === 1 ? 'has' : 'have'} no cost yet, so this figure is too low.</span>
                {can.module && <Link to="/inventory/stock" style={{ color: 'var(--bv)' }}>See them</Link>}
              </div>
            )}
          </div>
        )}
      </div>

      {/* ---- running low ---------------------------------------------------- */}
      <div className="card overflow-hidden">
        <div className="flex gap-2 items-center flex-wrap px-4 py-3" style={{ borderBottom: '1px solid var(--line)' }}>
          <span className="font-semibold">Running low</span>
          <span className="chip">sold fast, few left</span>
          <span style={{ flex: 1 }} />
          {can.module && d.running_low.length > 0 && <Link to="/inventory/reorders" className="btn">Reorder these</Link>}
        </div>
        {d.running_low.length === 0 ? (
          <p className="text-sm px-4 py-3" style={{ color: failed.has('running_low') ? 'var(--warn)' : 'var(--ink-4)' }}>
            {failed.has('running_low') ? notRead : 'Nothing here is at or under its reorder level.'}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="tbl" style={{ minWidth: 520 }}>
              <thead>
                <tr><th>Product</th><th className="right">Here</th><th className="right">Other shops</th><th className="right">Sold 30 days</th></tr>
              </thead>
              <tbody>
                {d.running_low.map((r) => (
                  <tr key={`${r.product_id}|${r.store_id}`}>
                    <td>
                      {r.name || r.sku}
                      {allShops && <div className="text-xs" style={{ color: 'var(--ink-4)' }}>{r.store_name}</div>}
                    </td>
                    <td className="right mono" style={r.here === 0 ? { color: 'var(--err)' } : undefined}>{r.here}</td>
                    <td className="right mono">{r.other_shops}</td>
                    <td className="right mono">{r.sold_30d}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {d.running_low_total > d.running_low.length && (
          <p className="text-xs px-4 py-2" style={{ color: 'var(--ink-4)' }}>
            Showing the {d.running_low.length} selling fastest of {d.running_low_total}.
          </p>
        )}
      </div>
    </div>
  );
}
