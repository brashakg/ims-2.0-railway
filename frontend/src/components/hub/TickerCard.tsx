// ============================================================================
// IMS 2.0 - F34 Global Target Ticker card (Hub)
// ----------------------------------------------------------------------------
// Monthly-revenue-vs-target ticker, shown to EVERY role on the Hub. The view is
// privacy-stratified by the SERVER (raw_visible decided from the JWT, never the
// client): management sees rupees + pace + per-store rows; floor staff see a
// single unlabelled progress bar + "X% reached" with NO rupee amounts and NO
// store breakdown. A store with no REVENUE budget for the month renders a greyed
// "No target set" state -- never a fabricated number.
//
// Restrained/executive light UI: a single var(--color-accent) progress fill, no
// confetti, no multi-colour. Polls every ticker_refresh_seconds; AbortController
// cancels the in-flight poll on unmount. A milestone toast fires ONCE per new
// crossing (tracked via a useRef snapshot so it never re-fires on a later poll).
// Fail-soft: a backend hiccup renders nothing (the rest of the Hub is unaffected).
// ============================================================================

import { useEffect, useRef, useState } from 'react';
import { Target } from 'lucide-react';
import { financeApi, type TickerResponse, type TickerStore } from '../../services/api/finance';
import { useToast } from '../../context/ToastContext';

const INR = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });

function inr(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—';
  const v = Math.round(n);
  if (Math.abs(v) >= 100000) return `₹${(v / 100000).toFixed(2)}L`;
  return `₹${INR.format(v)}`;
}

function clampPct(p: number | null | undefined): number {
  const v = typeof p === 'number' ? p : 0;
  if (v < 0) return 0;
  if (v > 100) return 100;
  return v;
}

function ProgressBar({ pct, muted }: { pct: number; muted?: boolean }) {
  return (
    <div
      className="h-2 w-full overflow-hidden rounded-full bg-gray-100"
      role="progressbar"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className="h-full rounded-full transition-[width] duration-500"
        style={{
          width: `${clampPct(pct)}%`,
          background: muted ? 'var(--color-line-strong, #d8d8d5)' : 'var(--color-accent, #CD201A)',
        }}
      />
    </div>
  );
}

function ManagementRow({ store }: { store: TickerStore }) {
  if (store.no_target) {
    return (
      <div className="py-2">
        <div className="flex items-center justify-between text-sm">
          <span className="text-gray-700">{store.store_name || '—'}</span>
          <span className="text-xs text-gray-400">No target set</span>
        </div>
        <div className="mt-1.5 opacity-60">
          <ProgressBar pct={0} muted />
        </div>
        <p className="mt-1 text-[11px] text-gray-400">Configure a REVENUE budget in Budgets.</p>
      </div>
    );
  }
  const ahead = (store.pace_delta ?? 0) >= 0;
  return (
    <div className="py-2">
      <div className="flex items-center justify-between text-sm">
        <span className="text-gray-700">{store.store_name || '—'}</span>
        <span className="font-semibold tabular-nums text-gray-900">{clampPct(store.pct_complete).toFixed(0)}%</span>
      </div>
      <div className="mt-1.5">
        <ProgressBar pct={store.pct_complete} />
      </div>
      <div className="mt-1 flex items-center justify-between text-[11px]">
        <span className="text-gray-500 tabular-nums">
          {inr(store.mtd_revenue)} of {inr(store.monthly_target)}
        </span>
        <span className="text-gray-500 tabular-nums">
          {inr(Math.abs(store.pace_delta ?? 0))} {ahead ? 'ahead of pace' : 'behind pace'}
        </span>
      </div>
    </div>
  );
}

function StaffView({ store }: { store: TickerStore }) {
  if (store.no_target) {
    return (
      <div>
        <div className="opacity-60">
          <ProgressBar pct={0} muted />
        </div>
        <p className="mt-1.5 text-xs text-gray-400">No target set</p>
      </div>
    );
  }
  return (
    <div>
      <ProgressBar pct={store.pct_complete} />
      <p className="mt-1.5 text-sm text-gray-600">
        <span className="font-semibold text-gray-900 tabular-nums">{clampPct(store.pct_complete).toFixed(0)}%</span> reached
      </p>
    </div>
  );
}

export default function TickerCard() {
  const toast = useToast();
  const [data, setData] = useState<TickerResponse | null>(null);
  const [failed, setFailed] = useState(false);
  // Per-store snapshot of milestones already toasted this session, so a toast
  // fires exactly ONCE per new crossing and never re-fires on a later poll.
  const firedRef = useRef<Record<string, Set<number>>>({});
  const refreshRef = useRef<number>(60);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | null = null;

    const poll = async () => {
      controller = new AbortController();
      try {
        const res = await financeApi.getTargetTicker();
        if (!alive) return;
        setData(res);
        setFailed(false);
        if (typeof res.ticker_refresh_seconds === 'number' && res.ticker_refresh_seconds > 0) {
          refreshRef.current = res.ticker_refresh_seconds;
        }
        // Milestone toast: any threshold in milestones_fired not yet seen this
        // session triggers a single celebratory toast, then is recorded so the
        // next poll (same list) does not re-fire it.
        for (const store of res.stores || []) {
          const key = store.store_id || '_';
          const seen = firedRef.current[key] ?? new Set<number>();
          const fresh = (store.milestones_fired || []).filter((m) => !seen.has(m));
          if (fresh.length > 0) {
            // Only toast after the FIRST snapshot is established (avoid a burst
            // of "milestone reached" toasts on the very first load).
            if (firedRef.current[key] !== undefined) {
              const top = Math.max(...fresh);
              toast.success(`Target milestone reached — ${top}%`);
            }
            fresh.forEach((m) => seen.add(m));
          }
          firedRef.current[key] = seen;
        }
      } catch {
        if (alive && !data) setFailed(true);
      } finally {
        if (alive) {
          timer = setTimeout(poll, Math.max(refreshRef.current, 30) * 1000);
        }
      }
    };

    poll();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
      if (controller) controller.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fail-soft: an error before any data loads renders nothing.
  if (failed && !data) return null;

  const stores = data?.stores ?? [];
  const rawVisible = data?.raw_visible ?? false;

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4">
      <div className="mb-2 flex items-center gap-1.5 text-[10.5px] font-mono uppercase tracking-wider text-gray-400">
        <Target className="h-3 w-3" />
        {rawVisible ? 'Monthly target' : 'Company goal'}
      </div>

      {!data ? (
        <div className="py-4 text-sm text-gray-400">Loading…</div>
      ) : rawVisible ? (
        <div className="divide-y divide-gray-100">
          {stores.length === 0 ? (
            <p className="py-2 text-sm text-gray-400">No stores to show.</p>
          ) : (
            stores.map((s, i) => <ManagementRow key={s.store_id || i} store={s} />)
          )}
        </div>
      ) : (
        // Floor staff: a single company-goal bar (first store entry).
        <div className="pt-1">
          <StaffView store={stores[0] ?? ({ pct_complete: 0, no_target: true, milestones_fired: [], days_elapsed: 0, days_in_month: 0, store_id: '' } as TickerStore)} />
        </div>
      )}
    </div>
  );
}
