// ============================================================================
// IMS 2.0 - POS widget strip (owner spec 8, picked after 3 option rounds)
// ============================================================================
// The four widgets the owner chose: My day (salesperson scorecard) · Offers &
// loyalty progress · Customer outstanding & dues · Family Rx expiry timelines.
// (Rejected in those rounds: quick picks, today-at-counter, a held-bills
// widget — held bills stay topbar-only.)
//
// Ponytail: every tile reads an endpoint that ALREADY exists — credit-summary,
// family Rx, the loyalty points the POS store already holds — so there is no
// new backend and no new data layer. Tiles are presentational only.
//
// Spec 9 (tap-through + autosave): tapping a tile navigates to its full
// screen. No draft machinery is needed for that — a route change keeps the
// zustand store in memory, and posStore already persists continuously to
// localStorage for crash recovery. NOTE: that persisted copy deliberately
// strips customer/patient/prescription (SEC-4, shared terminals), so a FULL
// PAGE RELOAD still loses who the bill was for; changing that needs an owner
// ruling, not a code decision.

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { usePOSStore } from '../../../stores/posStore';
import { customerApi } from '../../../services/api/customers';
import { prescriptionApi } from '../../../services/api/sales';

const money = (v: number) => `₹${Math.round(v || 0).toLocaleString('en-IN')}`;

function Tile({
  title,
  onClick,
  children,
  hint,
}: {
  title: string;
  onClick: () => void;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={hint}
      // 44px+ touch target, bordered (owner spec 11: no ghost buttons on iPad).
      className="min-h-[68px] text-left rounded-xl border border-gray-200 bg-white px-3 py-2.5 hover:bg-gray-50 active:bg-gray-100 flex flex-col gap-1"
    >
      <span className="text-[10px] font-medium uppercase tracking-widest text-gray-500">
        {title}
      </span>
      <div className="flex-1 min-h-0 text-sm text-gray-900">{children}</div>
    </button>
  );
}

const Muted = ({ children }: { children: React.ReactNode }) => (
  <span className="text-xs text-gray-500">{children}</span>
);

export function PosWidgets() {
  const store = usePOSStore();
  const navigate = useNavigate();
  const customerId = store.customer?.id ? String(store.customer.id) : '';

  const [credit, setCredit] = useState<{ ar_outstanding: number; limit_exceeded: boolean } | null>(null);
  const [rxDue, setRxDue] = useState<{ name: string; expiry: string | null; valid: boolean | null }[]>([]);

  // Outstanding & dues — existing credit-summary door. Fail-soft: a widget must
  // never break billing, so an error simply leaves the tile empty.
  useEffect(() => {
    if (!customerId) { setCredit(null); return; }
    let dead = false;
    customerApi
      .getCreditSummary(customerId)
      .then((r) => { if (!dead) setCredit({ ar_outstanding: r.ar_outstanding, limit_exceeded: r.limit_exceeded }); })
      .catch(() => { if (!dead) setCredit(null); });
    return () => { dead = true; };
  }, [customerId]);

  // Family Rx timelines — the same door the Rx picker uses.
  useEffect(() => {
    if (!customerId) { setRxDue([]); return; }
    let dead = false;
    prescriptionApi
      .getFamilyRx(customerId)
      .then((r: any) => {
        if (dead) return;
        const members = Array.isArray(r?.members) ? r.members : [];
        setRxDue(
          members.slice(0, 3).map((m: any) => ({
            name: m?.name || 'Member',
            expiry: m?.latest?.expiry_date ?? null,
            valid: m?.latest?.is_valid ?? null,
          })),
        );
      })
      .catch(() => { if (!dead) setRxDue([]); });
    return () => { dead = true; };
  }, [customerId]);

  const points = store.customerLoyaltyPoints || 0;

  return (
    <div className="grid grid-cols-2 gap-2">
      {/* 1 — My day (salesperson scorecard) */}
      <Tile
        title="My day"
        hint="Open the incentive scorecard"
        onClick={() => navigate('/incentive')}
      >
        {store.salesperson_name ? (
          <>
            <div className="font-medium truncate">{store.salesperson_name}</div>
            <Muted>Open scorecard — sales vs target, points, conversion</Muted>
          </>
        ) : (
          <Muted>Pick a salesperson to see today's scorecard</Muted>
        )}
      </Tile>

      {/* 2 — Offers & loyalty progress */}
      <Tile
        title="Offers &amp; loyalty"
        hint="Open the loyalty programme"
        onClick={() => navigate('/customers/loyalty')}
      >
        {customerId ? (
          <>
            <div className="font-medium">{points.toLocaleString('en-IN')} pts</div>
            <Muted>
              Earns only under 5% discount and with no offer applied
            </Muted>
          </>
        ) : (
          <Muted>Pick a customer to see their tier and points</Muted>
        )}
      </Tile>

      {/* 3 — Customer outstanding & dues */}
      <Tile
        title="Outstanding &amp; dues"
        hint="Open this customer's account"
        onClick={() => customerId && navigate(`/customers/360?id=${customerId}`)}
      >
        {!customerId ? (
          <Muted>Pick a customer to see dues</Muted>
        ) : credit ? (
          <>
            <div className={'font-medium ' + (credit.ar_outstanding > 0 ? 'text-red-600' : '')}>
              {money(credit.ar_outstanding)}
            </div>
            <Muted>{credit.limit_exceeded ? 'Credit limit exceeded' : 'Outstanding balance'}</Muted>
          </>
        ) : (
          <Muted>No dues on file</Muted>
        )}
      </Tile>

      {/* 4 — Family Rx expiry timelines */}
      <Tile
        title="Family Rx"
        hint="Open the family prescriptions view"
        onClick={() => customerId && navigate(`/clinical/family-rx?customer=${customerId}`)}
      >
        {!customerId ? (
          <Muted>Pick a customer to see Rx expiry</Muted>
        ) : rxDue.length ? (
          <div className="space-y-0.5">
            {rxDue.map((m, i) => (
              <div key={i} className="flex items-center justify-between gap-2 text-xs">
                <span className="truncate">{m.name}</span>
                <span className={m.valid === false ? 'text-red-600' : 'text-gray-500'}>
                  {m.expiry ? new Date(m.expiry).toLocaleDateString('en-IN', { month: 'short', year: '2-digit' }) : '—'}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <Muted>No prescriptions on file</Muted>
        )}
      </Tile>
    </div>
  );
}

export default PosWidgets;
