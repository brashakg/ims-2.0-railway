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
// CUSTOMER PANEL (owner, 2026-09-05): a tap no longer NAVIGATES. It opens the
// slide-over in CustomerPanel.tsx on the till itself, landing on that tile's
// section, with the bill/cart/total still on screen. The tapped tile stays
// lit while its section is open. Mounted here, so every surface that shows
// the tiles (billing, general counter, delivery) gets the panel once, with no
// per-surface wiring. The full pages are still reachable from the panel's
// "Open full page" hatch, customer carried in the link.

import { useState } from 'react';
import { usePOSStore } from '../../../stores/posStore';
import { customerApi } from '../../../services/api/customers';
import { useFamilyRx } from '../../../hooks/useFamilyRx';
import { useQuery } from '@tanstack/react-query';
import { CustomerPanel, type PanelCustomer, type PanelSection } from './CustomerPanel';

const money = (v: number) => `₹${Math.round(v || 0).toLocaleString('en-IN')}`;

function Tile({
  title,
  onClick,
  children,
  hint,
  active,
}: {
  title: string;
  onClick: () => void;
  children: React.ReactNode;
  hint?: string;
  active?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={hint}
      aria-pressed={!!active}
      // 44px+ touch target, bordered (owner spec 11: no ghost buttons on iPad).
      // Lit while its panel section is open so the cashier keeps their place.
      className={
        'min-h-[68px] text-left rounded-xl border bg-white px-3 py-2.5 hover:bg-gray-50 active:bg-gray-100 flex flex-col gap-1 ' +
        (active ? 'border-gray-900 ring-2 ring-gray-900/10' : 'border-gray-200')
      }
    >
      <span className={'text-[10px] font-medium uppercase tracking-widest ' + (active ? 'text-gray-900' : 'text-gray-500')}>
        {title}
      </span>
      <div className="flex-1 min-h-0 text-sm text-gray-900">{children}</div>
    </button>
  );
}

const Muted = ({ children }: { children: React.ReactNode }) => (
  <span className="text-xs text-gray-500">{children}</span>
);

export function PosWidgets({
  customer: customerOverride,
}: {
  /** The delivery counter has no bill customer in posStore; it hands the
   *  scanned order's customer here so the tiles and the panel read the person
   *  in front of the counter. Billing surfaces pass nothing (store.customer). */
  customer?: PanelCustomer | null;
} = {}) {
  const store = usePOSStore();
  const [section, setSection] = useState<PanelSection | null>(null);

  const billCustomer: PanelCustomer | null = store.customer?.id
    ? { id: String(store.customer.id), name: store.customer.name || 'Customer', phone: store.customer.phone || '' }
    : null;
  const customer = customerOverride === undefined ? billCustomer : customerOverride;
  const customerId = customer?.id || '';

  // Outstanding & dues — existing credit-summary door. Fail-soft: a widget must
  // never break billing, so an error simply leaves the tile empty.
  const credit = useQuery({
    queryKey: ['customers', customerId, 'credit-summary'] as const,
    enabled: !!customerId,
    queryFn: () => customerApi.getCreditSummary(customerId).catch(() => null),
  }).data ?? null;

  // Family Rx timelines — THE family Rx read (shared with the page + panel).
  const family = useFamilyRx(customerId);
  const rxDue = (family.data?.members || []).slice(0, 3).map((m) => ({
    name: m?.name || 'Member',
    expiry: m?.latest?.expiry_date ?? null,
    valid: m?.latest?.is_valid ?? null,
  }));

  const points = store.customerLoyaltyPoints || 0;
  const open = (s: PanelSection) => setSection(s);

  return (
    <>
      <div className="grid grid-cols-2 gap-2">
        {/* 1 — My day (salesperson scorecard) */}
        <Tile title="My day" hint="Your own figures for today" onClick={() => open('myday')} active={section === 'myday'}>
          {store.salesperson_name ? (
            <>
              <div className="font-medium truncate">{store.salesperson_name}</div>
              <Muted>Sales, bills and conversion today</Muted>
            </>
          ) : (
            <Muted>Your sales, bills and conversion today</Muted>
          )}
        </Tile>

        {/* 2 — Offers & loyalty progress */}
        <Tile title="Offers &amp; loyalty" hint="Offers that apply and loyalty points" onClick={() => open('offers')} active={section === 'offers'}>
          {customerId ? (
            <>
              <div className="font-medium">{points.toLocaleString('en-IN')} pts</div>
              <Muted>Earns only under 5% discount and with no offer applied</Muted>
            </>
          ) : (
            <Muted>Pick a customer to see their tier and points</Muted>
          )}
        </Tile>

        {/* 3 — Customer outstanding & dues */}
        <Tile title="Outstanding &amp; dues" hint="This customer's dues, per order" onClick={() => open('dues')} active={section === 'dues'}>
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
        <Tile title="Family Rx" hint="The household's prescriptions" onClick={() => open('family')} active={section === 'family'}>
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

      <CustomerPanel
        section={section}
        onSection={setSection}
        onClose={() => setSection(null)}
        customer={customer}
        canBill={!!store.customer?.id}
      />
    </>
  );
}

export default PosWidgets;
