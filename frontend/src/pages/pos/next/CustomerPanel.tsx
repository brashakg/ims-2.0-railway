// ============================================================================
// IMS 2.0 - POS customer panel (owner spec, 2026-09-05)
// ============================================================================
// The four widget tiles used to NAVIGATE: tap Family Rx mid-sale and the
// counter lands on a page that asks them to search the customer again, and a
// cashier is bounced by the page's route gate. This is the fix: one slide-over
// on the till itself. The bill, cart and total stay on screen behind a scrim;
// four sections with an in-panel tab strip; Escape or tap-outside returns to
// the bill; "Open full page" is still there, carrying the customer in the link.
//
// PERMISSIONS: no route, so no route gate. Every section reads endpoints the
// server already lets POS staff call (family Rx, credit summary, orders,
// loyalty account, promotions, the self-only my-day figures). A server 403
// is shown as the server's own message -- never bounced, never widened.
//
// OWNER DECISIONS (binding):
//  1. "Bill for <member>" KEEPS THE CART and changes the person: the SAME
//     posStore setters the customer search uses (setPatient via toPosPatient,
//     setPrescription via mapRx). No confirm, no clearing.
//  2. Mounted once per surface (through PosWidgets, which all three share).
//  3. Money is READ-ONLY here: dues and offers are shown; discounts and
//     payments stay in the cart / payment flows (a hatch to them is fine).
//  4. "Send reminder on WhatsApp" QUEUES through the existing recall door
//     (POST /marketing/rx-reminder -> flow key PRESCRIPTION_EXPIRY). Nothing
//     leaves while DISPATCH_MODE is off, so the toast says "queued".
//  5. "Book eye test" adds the member to TODAY's clinical queue through the
//     existing queue-add door; the queue is read first so a member already
//     waiting is reported instead of queued twice (the door itself does not
//     dedupe).
//  6. "My day" = GET /incentive/points/my-day: the signed-in user's OWN day.
//
// PHONE (<=767px): the same panel as a bottom sheet -- grab handle, rounded
// top, scrolls inside itself, tab strip scrolls sideways, member actions drop
// under the text, full-width "Back to bill".

import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ExternalLink, X } from 'lucide-react';
import { usePOSStore } from '../../../stores/posStore';
import { useAuth } from '../../../context/AuthContext';
import { useToast } from '../../../context/ToastContext';
import { useFamilyRx, rxExpiryStatus } from '../../../hooks/useFamilyRx';
import { useCustomerDues } from '../../../hooks/useCustomerDues';
import { useLoyaltyLedger } from '../../../hooks/useLoyaltyLedger';
import { toPosPatient, type RawPatient } from '../../../utils/patientFromCustomer';
import { formatPowerOrDash } from '../../../utils/rxPowerValue';
import { mapRx, type FamilyRxMember } from '../../../services/api/sales';
import { clinicalApi } from '../../../services/api/clinical';
import { marketingApi } from '../../../services/api/marketing';
import { promotionsApi, type PromoRule } from '../../../services/api/promotions';
import { incentiveApi } from '../../../services/api/incentive';

export type PanelSection = 'family' | 'dues' | 'offers' | 'myday';

export const PANEL_SECTIONS: ReadonlyArray<{ key: PanelSection; label: string }> = [
  { key: 'family', label: 'Family Rx' },
  { key: 'dues', label: 'Dues' },
  { key: 'offers', label: 'Offers & loyalty' },
  { key: 'myday', label: 'My day' },
];

export interface PanelCustomer {
  id: string;
  name: string;
  phone?: string;
}

interface CustomerPanelProps {
  /** Open section, or null when the panel is closed. */
  section: PanelSection | null;
  onSection: (s: PanelSection) => void;
  onClose: () => void;
  /** Who the panel is about: the bill's customer, or on the delivery counter
   *  the scanned order's customer. */
  customer: PanelCustomer | null;
  /** True when a bill is being built on this surface (store.customer is set),
   *  so "Bill for <member>" has a bill to switch. */
  canBill: boolean;
}

const money = (v: number) => `₹${Math.round(v || 0).toLocaleString('en-IN')}`;
const dateShort = (s: string | null | undefined) => {
  if (!s) return '—';
  const d = new Date(s);
  return Number.isNaN(d.getTime())
    ? '—'
    : d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
};
const initialsOf = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() || '')
    .join('') || '?';
const ageOf = (dob: string | null | undefined): number | null => {
  if (!dob) return null;
  const d = new Date(dob);
  if (Number.isNaN(d.getTime())) return null;
  const years = Math.floor((Date.now() - d.getTime()) / (365.25 * 24 * 60 * 60 * 1000));
  return years >= 0 && years < 130 ? years : null;
};
const firstName = (name: string | null | undefined) => (name || 'member').split(/\s+/)[0];
const serverDetail = (e: any, fallback: string) =>
  e?.response?.data?.detail || e?.message || fallback;

/** <=767px is the phone sheet; the app's `tablet` breakpoint starts at 768.
 *  FOLLOWS THE VIEWPORT (resize-subscribed), never a mount-time read: the e2e
 *  layout probe resizes an open page rather than reloading it, and the
 *  reactiveWidthGates guard fails any width gate that would not follow. */
const PHONE_MAX_WIDTH = 767;
const readIsPhone = () => typeof window !== 'undefined' && window.innerWidth <= PHONE_MAX_WIDTH;
function useIsPhone(): boolean {
  const [phone, setPhone] = useState<boolean>(readIsPhone);
  useEffect(() => {
    const on = () => setPhone(readIsPhone());
    window.addEventListener('resize', on);
    return () => window.removeEventListener('resize', on);
  }, []);
  return phone;
}

// ---- Shared bits ------------------------------------------------------------
type ChipTone = 'plain' | 'ok' | 'info' | 'warn' | 'err';
const CHIP: Record<ChipTone, string> = {
  plain: 'bg-gray-100 text-gray-800 border border-gray-200',
  ok: 'bg-green-50 text-green-700',
  info: 'bg-blue-50 text-blue-700',
  warn: 'bg-amber-50 text-amber-700',
  err: 'bg-red-50 text-red-700',
};
const Chip = ({ tone = 'plain', children }: { tone?: ChipTone; children: React.ReactNode }) => (
  <span className={'inline-flex items-center h-[22px] px-2 rounded-md text-[11px] font-medium whitespace-nowrap ' + CHIP[tone]}>
    {children}
  </span>
);
const Eyebrow = ({ children }: { children: React.ReactNode }) => (
  <span className="text-[10px] font-medium uppercase tracking-widest text-gray-500">{children}</span>
);
const Muted = ({ children }: { children: React.ReactNode }) => (
  <p className="text-sm text-gray-500 py-6 text-center">{children}</p>
);
const BTN = 'inline-flex items-center justify-center gap-1.5 min-h-[44px] px-3.5 rounded-lg border text-[12px] font-medium';
const btn = (dark = false) =>
  BTN + (dark ? ' bg-gray-900 text-white border-gray-900' : ' bg-white text-gray-900 border-gray-300 hover:bg-gray-50');

// ---- Section: Family Rx ------------------------------------------------------
function powerLine(eye: Record<string, unknown> | null | undefined): string {
  if (!eye) return '—';
  const pick = (...keys: string[]) => keys.map((k) => eye[k]).find((v) => v !== undefined && v !== null && v !== '');
  const axis = pick('axis');
  return `${formatPowerOrDash(pick('sphere', 'sph'))} / ${formatPowerOrDash(pick('cylinder', 'cyl'))}` +
    (axis !== undefined ? ` × ${String(axis)}` : '');
}

function FamilySection({
  customer,
  canBill,
  phone,
  onClose,
}: {
  customer: PanelCustomer | null;
  canBill: boolean;
  phone: boolean;
  onClose: () => void;
}) {
  const store = usePOSStore();
  const { user } = useAuth();
  const toast = useToast();
  const family = useFamilyRx(customer?.id);
  const [busy, setBusy] = useState<string | null>(null);

  const members = family.data?.members || [];
  const statuses = useMemo(() => members.map((m) => rxExpiryStatus(m.latest)), [members]);
  const expired = statuses.filter((s) => s.kind === 'expired').length;
  const due = statuses.filter((s) => s.kind === 'due').length;

  if (!customer) return <Muted>Pick a customer on the bill to see the household&rsquo;s prescriptions.</Muted>;
  if (family.isLoading) return <Muted>Loading prescriptions…</Muted>;
  if (family.error) return <Muted>{serverDetail(family.error, 'Could not load prescriptions')}</Muted>;

  // Decision 1: the SAME setters the customer search uses. setPatient changes
  // the billed member (the cart is untouched); setPrescription loads the
  // member's latest usable Rx, or clears it when they have none in date.
  const billFor = (m: FamilyRxMember, status: ReturnType<typeof rxExpiryStatus>) => {
    const onRecord: RawPatient | undefined = ((store.customer as any)?.patients || []).find(
      (p: RawPatient) => (p.patient_id || p.id) === m.patient_id,
    );
    const raw: RawPatient = onRecord || {
      patient_id: m.patient_id || undefined,
      name: m.name || undefined,
      relation: m.relation || undefined,
      dob: m.dob || undefined,
    };
    store.setPatient(toPosPatient(raw, customer.id) as any);
    const usable = m.latest && status.kind !== 'expired' && status.kind !== 'none';
    store.setPrescription(usable ? (mapRx(m.latest) as any) : null);
    toast.success(
      usable
        ? `Billing for ${m.name || 'member'} — prescription loaded`
        : `Billing for ${m.name || 'member'} — no prescription in date, pick or capture one`,
    );
    onClose();
  };

  // Decision 5: the existing queue-add door. It does not dedupe, so read
  // today's queue first and say so instead of queuing twice.
  const bookTest = async (m: FamilyRxMember) => {
    const storeId = user?.activeStoreId || store.store_id;
    const key = m.patient_id || m.name || '';
    setBusy(key);
    try {
      const q: any = await clinicalApi.getQueue(storeId);
      const items: any[] = Array.isArray(q?.queue) ? q.queue : [];
      const waiting = items.find((i) => {
        const st = String(i?.status || '').toUpperCase();
        if (st !== 'WAITING' && st !== 'IN_PROGRESS') return false;
        return m.patient_id
          ? i?.patientId === m.patient_id
          : i?.customerId === customer.id && i?.patientName === m.name;
      });
      if (waiting) {
        toast.info(`${m.name || 'This member'} is already in today's queue (${waiting.tokenNumber || 'waiting'})`);
        return;
      }
      const age = ageOf(m.dob);
      await clinicalApi.addToQueue({
        storeId,
        patientName: m.name || customer.name,
        customerPhone: customer.phone || '',
        customerId: customer.id,
        patientId: m.patient_id || undefined,
        ...(age !== null ? { age } : {}),
        reason: 'Rx expired or due — booked from the till',
      });
      toast.success(`${m.name || 'Member'} added to today's eye-test queue`);
    } catch (e: any) {
      toast.error(serverDetail(e, 'Could not book the eye test'));
    } finally {
      setBusy(null);
    }
  };

  // Decision 4: the existing recall door, one message per household (it goes
  // to the account's number). 429 = the door's own 24h dedupe.
  const remind = async () => {
    setBusy('remind');
    try {
      await marketingApi.sendRxReminder(customer.id);
      toast.success(`Reminder queued for ${customer.name} on WhatsApp`);
    } catch (e: any) {
      if (e?.response?.status === 429) toast.info('A reminder already went to this customer in the last 24 hours');
      else toast.error(serverDetail(e, 'Could not queue the reminder'));
    } finally {
      setBusy(null);
    }
  };

  const onBillMemberId = store.patient?.id;

  return (
    <div>
      <div className="flex items-baseline gap-2">
        <Eyebrow>Household prescriptions</Eyebrow>
        <div className="flex-1" />
        <span className="text-[11px] text-gray-500 font-mono">{members.length} member{members.length === 1 ? '' : 's'}</span>
      </div>

      {members.length === 0 ? (
        <Muted>No family members on this account yet.</Muted>
      ) : (
        <ul>
          {members.map((m, i) => {
            const status = statuses[i];
            const age = ageOf(m.dob);
            const relation = m.relation ? m.relation + (age !== null ? ` · ${age}y` : '') : 'Unlinked';
            const onBill = !!m.patient_id && m.patient_id === onBillMemberId;
            const needsTest = status.kind === 'expired' || status.kind === 'due' || status.kind === 'none';
            const statusChip =
              status.kind === 'expired' ? (
                <Chip tone="err">{status.months !== null && status.months < 0 ? `Expired ${Math.abs(status.months)} mo` : 'Expired'}</Chip>
              ) : status.kind === 'due' ? (
                <Chip tone="warn">{status.months !== null && status.months > 0 ? `Expires in ${status.months} mo` : 'Expires this month'}</Chip>
              ) : status.kind === 'valid' ? (
                <Chip tone="ok">Valid</Chip>
              ) : status.kind === 'none' ? (
                <Chip>No Rx</Chip>
              ) : (
                <Chip>Unknown expiry</Chip>
              );
            return (
              <li
                key={m.patient_id || `m-${i}`}
                className={'flex gap-3 py-3 ' + (i === 0 ? '' : 'border-t border-gray-100 ') + (phone ? 'items-start' : 'items-center')}
              >
                <div className="w-[34px] h-[34px] rounded-full bg-gray-100 flex items-center justify-center text-[12px] font-semibold text-gray-600 shrink-0">
                  {initialsOf(m.name || '?')}
                </div>
                <div className="flex-1 min-w-0 flex flex-col gap-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-semibold text-sm text-gray-900">{m.name || 'Unnamed member'}</span>
                    <Chip>{relation}</Chip>
                    {statusChip}
                  </div>
                  <span className="text-[11px] text-gray-500 font-mono truncate">
                    {m.latest
                      ? `R ${powerLine(m.latest.right_eye)} · L ${powerLine(m.latest.left_eye)} · ` +
                        (m.latest.expiry_date ? `expires ${dateShort(m.latest.expiry_date)}` : `tested ${dateShort(m.latest.test_date ?? m.latest.created_at)}`)
                      : 'No prescription on file'}
                  </span>
                  {phone && (
                    <MemberActions
                      onBill={onBill}
                      canBill={canBill}
                      needsTest={needsTest}
                      busy={busy === (m.patient_id || m.name || '')}
                      name={firstName(m.name)}
                      onBillFor={() => billFor(m, status)}
                      onBook={() => bookTest(m)}
                      stacked
                    />
                  )}
                </div>
                {!phone && (
                  <MemberActions
                    onBill={onBill}
                    canBill={canBill}
                    needsTest={needsTest}
                    busy={busy === (m.patient_id || m.name || '')}
                    name={firstName(m.name)}
                    onBillFor={() => billFor(m, status)}
                    onBook={() => bookTest(m)}
                  />
                )}
              </li>
            );
          })}
        </ul>
      )}

      {members.length > 0 && (
        <div className={'mt-3 flex gap-2.5 ' + (phone ? 'flex-col items-stretch' : 'items-center')}>
          {expired + due > 0 ? (
            <Chip tone="warn">
              {[expired ? `${expired} expired` : '', due ? `${due} due within 60 days` : ''].filter(Boolean).join(' · ')}
            </Chip>
          ) : (
            <Chip tone="ok">All prescriptions current</Chip>
          )}
          {!phone && <div className="flex-1" />}
          <button type="button" onClick={remind} disabled={busy === 'remind'} className={btn()}>
            Send reminder on WhatsApp
          </button>
        </div>
      )}
    </div>
  );
}

function MemberActions({
  onBill,
  canBill,
  needsTest,
  busy,
  name,
  onBillFor,
  onBook,
  stacked = false,
}: {
  onBill: boolean;
  canBill: boolean;
  needsTest: boolean;
  busy: boolean;
  name: string;
  onBillFor: () => void;
  onBook: () => void;
  stacked?: boolean;
}) {
  return (
    <div className={'flex gap-2 shrink-0 ' + (stacked ? 'flex-wrap mt-1' : 'flex-col items-end')}>
      {onBill ? (
        <Chip tone="info">On this bill</Chip>
      ) : canBill ? (
        <button type="button" onClick={onBillFor} className={btn(true)}>
          Bill for {name}
        </button>
      ) : null}
      {needsTest && (
        <button type="button" onClick={onBook} disabled={busy} className={btn()}>
          {busy ? 'Booking…' : 'Book eye test'}
        </button>
      )}
    </div>
  );
}

// ---- Section: Dues -------------------------------------------------------------
function DuesSection({ customer }: { customer: PanelCustomer | null }) {
  const navigate = useNavigate();
  const dues = useCustomerDues(customer?.id);
  if (!customer) return <Muted>Pick a customer on the bill to see their dues.</Muted>;
  if (dues.isLoading) return <Muted>Loading dues…</Muted>;
  if (dues.error) return <Muted>{serverDetail(dues.error, 'Could not load dues')}</Muted>;
  const credit = dues.data?.credit;
  const rows = dues.data?.dueOrders || [];
  const outstanding = credit?.ar_outstanding ?? rows.reduce((s, r) => s + r.balanceDue, 0);
  return (
    <div>
      <div className="flex items-center gap-2">
        <div>
          <Eyebrow>Outstanding</Eyebrow>
          <div className={'text-2xl font-semibold tabular-nums ' + (outstanding > 0 ? 'text-red-600' : 'text-gray-900')}>
            {money(outstanding)}
          </div>
        </div>
        <div className="flex-1" />
        {credit?.limit_exceeded && <Chip tone="err">Credit limit exceeded</Chip>}
        {credit && credit.credit_limit > 0 && !credit.limit_exceeded && (
          <Chip>Limit {money(credit.credit_limit)}</Chip>
        )}
      </div>
      {rows.length === 0 ? (
        <Muted>No order carries a balance.</Muted>
      ) : (
        <ul className="mt-3">
          {rows.map((r, i) => (
            <li key={r.id || i} className={'flex items-center gap-3 py-2.5 ' + (i ? 'border-t border-gray-100' : '')}>
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-gray-900 truncate">{r.orderNumber}</div>
                <div className="text-[11px] text-gray-500 font-mono">{dateShort(r.createdAt)} · bill {money(r.grandTotal)}</div>
              </div>
              <span className="text-sm font-semibold tabular-nums text-red-600">{money(r.balanceDue)} due</span>
            </li>
          ))}
        </ul>
      )}
      {rows.length > 0 && (
        // Decision 3: read-only here. Collecting stays on the existing counters.
        <button type="button" onClick={() => navigate('/pos/delivery')} className={btn() + ' mt-2'}>
          Collect at the delivery counter <ExternalLink className="w-3.5 h-3.5" />
        </button>
      )}
    </div>
  );
}

// ---- Section: Offers & loyalty ---------------------------------------------------
function offerApplies(rule: PromoRule, storeId: string, tier: string | null, today: Date): boolean {
  if (rule.active === false) return false;
  if (rule.valid_until) {
    const d = new Date(rule.valid_until);
    if (!Number.isNaN(d.getTime()) && d.getTime() < today.getTime()) return false;
  }
  if (rule.valid_from) {
    const d = new Date(rule.valid_from);
    if (!Number.isNaN(d.getTime()) && d.getTime() > today.getTime()) return false;
  }
  if (rule.store_ids && rule.store_ids.length && storeId && !rule.store_ids.includes(storeId)) return false;
  if (rule.customer_tiers && rule.customer_tiers.length && tier && !rule.customer_tiers.includes(tier)) return false;
  return true;
}

function OffersSection({ customer }: { customer: PanelCustomer | null }) {
  const { user } = useAuth();
  const store = usePOSStore();
  const storeId = user?.activeStoreId || store.store_id || '';
  const loyalty = useLoyaltyLedger(customer?.id, { limit: 8 });
  const promos = useQuery({
    queryKey: ['promotions', 'active', storeId] as const,
    queryFn: () => promotionsApi.listRules({ store_id: storeId || undefined, active_only: true }),
  });
  const tier = loyalty.data?.account?.account?.tier ?? null;
  const applicable = useMemo(
    () => (promos.data?.rules || []).filter((r) => offerApplies(r, storeId, tier, new Date())),
    [promos.data, storeId, tier],
  );

  return (
    <div className="space-y-4">
      <div>
        <Eyebrow>Offers that apply today</Eyebrow>
        {promos.isLoading ? (
          <Muted>Loading offers…</Muted>
        ) : promos.error ? (
          <p className="text-sm text-gray-500 py-2">{serverDetail(promos.error, 'Could not load offers')}</p>
        ) : applicable.length === 0 ? (
          <p className="text-sm text-gray-500 py-2">No store offer is running for this customer today.</p>
        ) : (
          <ul className="mt-1">
            {applicable.map((r, i) => (
              <li key={r.promo_id} className={'py-2.5 ' + (i ? 'border-t border-gray-100' : '')}>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-gray-900 flex-1 min-w-0 truncate">{r.name}</span>
                  <Chip tone="info">{r.promo_type.replace('_', ' ')}</Chip>
                </div>
                {r.description && <div className="text-[11px] text-gray-500 mt-0.5">{r.description}</div>}
                {r.valid_until && <div className="text-[11px] text-gray-500 font-mono">until {dateShort(r.valid_until)}</div>}
              </li>
            ))}
          </ul>
        )}
        {/* Decision 3: read-only. The discount itself is applied on the cart line
            or the bill-discount card, under the role cap and the reason rule. */}
        <p className="text-[11px] text-gray-500 mt-1">Apply an offer from the cart line or the bill discount — not from here.</p>
      </div>

      <div>
        <Eyebrow>Loyalty</Eyebrow>
        {!customer ? (
          <p className="text-sm text-gray-500 py-2">Pick a customer on the bill to see their points.</p>
        ) : loyalty.isLoading ? (
          <Muted>Loading points…</Muted>
        ) : loyalty.error ? (
          <p className="text-sm text-gray-500 py-2">{serverDetail(loyalty.error, 'Could not load loyalty')}</p>
        ) : (
          <>
            <div className="flex items-baseline gap-2 mt-1">
              <span className="text-2xl font-semibold tabular-nums text-gray-900">
                {(loyalty.data?.account.account.balance_points ?? store.customerLoyaltyPoints ?? 0).toLocaleString('en-IN')} pts
              </span>
              {tier && <Chip tone="ok">{tier}</Chip>}
              {(loyalty.data?.account.expiring_soon_points ?? 0) > 0 && (
                <Chip tone="warn">{loyalty.data?.account.expiring_soon_points} expiring soon</Chip>
              )}
            </div>
            <p className="text-[11px] text-gray-500 mt-0.5">Earns only under 5% discount and with no offer applied.</p>
            {(loyalty.data?.ledger.items.length ?? 0) > 0 ? (
              <ul className="mt-2">
                {loyalty.data!.ledger.items.map((t, i) => (
                  <li key={t.txn_id} className={'flex items-center gap-3 py-2 ' + (i ? 'border-t border-gray-100' : '')}>
                    <Chip tone={t.type === 'EARN' ? 'ok' : t.type === 'REDEEM' ? 'info' : t.type === 'EXPIRE' ? 'warn' : 'plain'}>{t.type}</Chip>
                    <span className="flex-1 min-w-0 text-[12px] text-gray-700 truncate">{t.reason}</span>
                    <span className="text-[11px] text-gray-500 font-mono">{dateShort(t.created_at)}</span>
                    <span className={'text-sm font-semibold tabular-nums ' + (t.type === 'REDEEM' || t.type === 'EXPIRE' ? 'text-gray-900' : 'text-green-700')}>
                      {t.type === 'REDEEM' || t.type === 'EXPIRE' ? '−' : '+'}
                      {Math.abs(t.points).toLocaleString('en-IN')}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-gray-500 py-2">No loyalty activity yet.</p>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ---- Section: My day -----------------------------------------------------------------
function MyDaySection() {
  const { user } = useAuth();
  const store = usePOSStore();
  const navigate = useNavigate();
  const myDay = useQuery({
    queryKey: ['incentive', 'my-day', user?.id ?? ''] as const,
    queryFn: () => incentiveApi.getMyDay(),
  });
  const sellingAsSomeoneElse = !!store.salesperson_id && !!user?.id && store.salesperson_id !== user.id;
  return (
    <div>
      <div className="flex items-center gap-2">
        <Eyebrow>Your day · {user?.name || 'signed-in user'}</Eyebrow>
        <div className="flex-1" />
        <span className="text-[11px] text-gray-500 font-mono">{myDay.data?.date || ''}</span>
      </div>
      {myDay.isLoading ? (
        <Muted>Loading your figures…</Muted>
      ) : myDay.error ? (
        <Muted>{serverDetail(myDay.error, 'Could not load your day')}</Muted>
      ) : myDay.data ? (
        <div className="grid grid-cols-2 gap-2.5 mt-2">
          <Stat label="Sales today" value={money(myDay.data.sales_today)} />
          <Stat label="Bills" value={String(myDay.data.bills_today)} />
          {myDay.data.conversion_pct !== undefined ? (
            <Stat label="Conversion" value={`${myDay.data.conversion_pct}%`} hint={`${myDay.data.bills_today} of ${myDay.data.walkins_today} walk-ins`} />
          ) : (
            <Stat label="Conversion" value="—" hint="No walk-in logged under you today" />
          )}
        </div>
      ) : null}
      {sellingAsSomeoneElse && (
        <p className="text-[11px] text-gray-500 mt-2">
          These are your own figures. This bill is selling as {store.salesperson_name || 'someone else'}.
        </p>
      )}
      <button type="button" onClick={() => navigate('/incentive')} className={btn() + ' mt-3'}>
        Open scorecard <ExternalLink className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

const Stat = ({ label, value, hint }: { label: string; value: string; hint?: string }) => (
  <div className="rounded-xl border border-gray-200 bg-white px-3 py-2.5">
    <Eyebrow>{label}</Eyebrow>
    <div className="text-xl font-semibold tabular-nums text-gray-900 mt-0.5">{value}</div>
    {hint && <div className="text-[11px] text-gray-500">{hint}</div>}
  </div>
);

// ---- The panel ---------------------------------------------------------------------------
export function CustomerPanel({ section, onSection, onClose, customer, canBill }: CustomerPanelProps) {
  const navigate = useNavigate();
  const phone = useIsPhone();
  const open = section !== null;

  // Escape returns to the bill; the page behind must not scroll under the sheet.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);

  if (!open || typeof document === 'undefined') return null;

  const fullPage: Record<PanelSection, string | null> = {
    family: customer ? `/clinical/family-rx?customer=${encodeURIComponent(customer.id)}` : null,
    dues: customer ? `/customers/${encodeURIComponent(customer.id)}/360` : null,
    offers: customer ? `/customers/${encodeURIComponent(customer.id)}/loyalty` : null,
    myday: '/incentive',
  };
  const hatch = fullPage[section];
  const dues = section === 'dues';

  const body = (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Customer panel"
      data-variant={phone ? 'sheet' : 'slide-over'}
      className={'fixed inset-0 z-50 flex ' + (phone ? 'items-end' : 'justify-end')}
    >
      {/* Scrim: the bill, cart and total stay visible behind it; tapping it returns to the bill. */}
      <div className="absolute inset-0 bg-gray-900/20" onClick={onClose} aria-hidden="true" data-testid="customer-panel-scrim" />

      <div
        className={
          'relative bg-white flex flex-col shadow-2xl ' +
          (phone
            ? 'w-full h-[78dvh] max-h-[92dvh] rounded-t-2xl'
            : 'h-full w-full max-w-[560px] border-l border-gray-200')
        }
      >
        {phone && (
          <div className="shrink-0 pt-2.5 pb-1 flex justify-center" aria-hidden="true">
            <div className="w-10 h-1 rounded-full bg-gray-300" data-testid="customer-panel-grab" />
          </div>
        )}

        {/* Header: who this is about. No search box - the bill already knows. */}
        <div className={'shrink-0 border-b border-gray-100 ' + (phone ? 'px-4 pt-1 pb-2.5' : 'px-5 pt-4 pb-3')}>
          <div className="flex items-center gap-3">
            <div className="w-[38px] h-[38px] rounded-full bg-gray-100 flex items-center justify-center font-semibold text-gray-600 shrink-0">
              {customer ? initialsOf(customer.name) : '—'}
            </div>
            <div className="flex-1 min-w-0">
              <div className="font-semibold text-[15px] text-gray-900 truncate">{customer?.name || 'No customer on the bill'}</div>
              <div className="text-[11px] text-gray-500 font-mono truncate">
                {customer ? `${customer.phone ? customer.phone + ' · ' : ''}${canBill ? 'on this bill' : 'on this order'}` : 'Pick a customer to see their details'}
              </div>
            </div>
            {hatch && (
              <button
                type="button"
                onClick={() => {
                  onClose();
                  navigate(hatch);
                }}
                title="Open the full page with this customer"
                className={btn() + (phone ? ' w-11 px-0' : '')}
              >
                {phone ? <ExternalLink className="w-4 h-4" /> : <>Open full page <ExternalLink className="w-3.5 h-3.5" /></>}
              </button>
            )}
            <button type="button" onClick={onClose} aria-label="Close" title="Back to bill" className={btn() + ' w-11 px-0'}>
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* One panel, four sections. The strip scrolls sideways on a phone; the sheet never does. */}
          <div className="flex gap-1.5 mt-3 overflow-x-auto pb-0.5" role="tablist">
            {PANEL_SECTIONS.map((s) => (
              <button
                key={s.key}
                type="button"
                role="tab"
                aria-selected={section === s.key}
                onClick={() => onSection(s.key)}
                className={
                  'shrink-0 inline-flex items-center gap-1.5 min-h-[44px] px-3.5 rounded-lg text-[13px] font-medium whitespace-nowrap ' +
                  (section === s.key ? 'bg-gray-900 text-white' : 'text-gray-600 hover:bg-gray-100')
                }
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>

        {/* Body scrolls inside itself. */}
        <div className={'flex-1 min-h-0 overflow-y-auto ' + (phone ? 'px-4 py-2.5' : 'px-5 py-3.5')}>
          {section === 'family' && <FamilySection customer={customer} canBill={canBill} phone={phone} onClose={onClose} />}
          {dues && <DuesSection customer={customer} />}
          {section === 'offers' && <OffersSection customer={customer} />}
          {section === 'myday' && <MyDaySection />}
        </div>

        {/* Footer: the way back is always the bill. */}
        <div className={'shrink-0 border-t border-gray-100 flex items-center gap-2.5 ' + (phone ? 'px-4 pt-2.5 pb-4' : 'px-5 py-3')}>
          {!phone && <span className="text-[11px] text-gray-500 font-mono">Esc or tap outside → back to the bill</span>}
          {!phone && <div className="flex-1" />}
          <button type="button" onClick={onClose} className={btn(true) + (phone ? ' w-full' : ' px-5')}>
            Back to bill
          </button>
        </div>
      </div>
    </div>
  );

  return createPortal(body, document.body);
}

export default CustomerPanel;
