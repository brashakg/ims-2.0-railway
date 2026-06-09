// ============================================================================
// IMS 2.0 - F41 Lapsed-patient reactivation (#41)
// ============================================================================
// An in-app, per-store REACTIVATION WORK-LIST of clinically lapsed patients (no
// confirmed order AND no prescription exam in the lapse window, default 24
// months), VIP-prioritised. This is a WORK-LIST, NOT a message channel: marking
// an entry Reached/Skip records an in-app reactivation follow-up outcome -- it
// NEVER sends a WhatsApp/SMS and NEVER mints a voucher (the channel is disabled;
// #41 reactivation-send is deferred, so F41 is dark, mirroring #39).
//
// Restrained/executive UI: white cards, neutral chips, a single amber left-edge
// accent on VIP entries, colour used only for semantic meaning. The analytics
// strip is plain monochrome KPI chips -- no charts.

import { useCallback, useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { crmApi } from '../../services/api/crm';
import type {
  ReactivationEntry,
  ReactivationOutcome,
  ReactivationAnalytics,
} from '../../services/api/crm';

const OUTCOMES: { value: ReactivationOutcome; label: string; reached: boolean }[] = [
  { value: 'reached', label: 'Reached -- interested', reached: true },
  { value: 'scheduled_visit', label: 'Scheduled a visit', reached: true },
  { value: 'no_answer', label: 'No answer', reached: false },
  { value: 'not_interested', label: 'Not interested', reached: false },
  { value: 'wrong_number', label: 'Wrong number', reached: false },
];

function rupees(paisa: number): string {
  return `₹${Math.round((paisa ?? 0) / 100).toLocaleString('en-IN')}`;
}

function lapseLabel(months: number | null): string {
  if (months === null) return 'No visit on record';
  if (months >= 24) return `Lapsed ~${Math.floor(months / 12)}y`;
  return `Lapsed ${months}mo`;
}

export function LapsedReactivationPage() {
  const { user } = useAuth();
  const isHq = (user?.roles || []).some((r) => ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER'].includes(r));

  const [storeId, setStoreId] = useState<string>(user?.activeStoreId || '');
  const [entries, setEntries] = useState<ReactivationEntry[]>([]);
  const [listDate, setListDate] = useState<string>('');
  const [analytics, setAnalytics] = useState<ReactivationAnalytics | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Outcome modal state.
  const [actFor, setActFor] = useState<ReactivationEntry | null>(null);
  const [outcome, setOutcome] = useState<ReactivationOutcome>('reached');
  const [notes, setNotes] = useState('');
  const [nextDate, setNextDate] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!storeId) {
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    try {
      const [list, an] = await Promise.all([
        crmApi.getReactivationWorklist(storeId),
        crmApi.getReactivationAnalytics(storeId).catch(() => null),
      ]);
      setEntries(Array.isArray(list?.entries) ? list.entries : []);
      setListDate(list?.date || '');
      setAnalytics(an);
    } catch {
      setEntries([]);
    } finally {
      setIsLoading(false);
    }
  }, [storeId]);

  useEffect(() => {
    load();
  }, [load]);

  const closeModal = () => {
    setActFor(null);
    setOutcome('reached');
    setNotes('');
    setNextDate('');
  };

  const submit = async () => {
    if (!actFor) return;
    setBusy(true);
    try {
      await crmApi.logReactivationOutcome(storeId, actFor.customer_id, outcome, {
        notes: notes.trim(),
        followUpScheduledDate: nextDate || undefined,
      });
      closeModal();
      await load();
    } catch {
      /* fail-soft: leave the entry so the associate can retry */
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="inv-body">
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>CRM &middot; Reactivation</div>
          <h1>Lapsed patients{listDate ? ` — ${listDate}` : ''}.</h1>
          <div className="hint">
            {entries.length} patients with no order or eye test in the lapse window. VIPs are listed first.
            Call or visit them, then record the outcome. This stays in-app; no message is sent.
          </div>
        </div>
        <button
          type="button"
          onClick={load}
          className="border border-gray-300 text-gray-700 rounded px-3 py-1.5 text-sm hover:bg-gray-50"
        >
          Refresh
        </button>
      </div>

      {isHq && (
        <div className="mb-3">
          <label htmlFor="react-store" className="block text-xs text-gray-500 mb-1">Store</label>
          <input
            id="react-store"
            type="text"
            value={storeId}
            onChange={(e) => setStoreId(e.target.value)}
            placeholder="Store ID"
            className="border border-gray-200 rounded px-2 py-1 text-sm"
          />
        </div>
      )}

      {/* Analytics: plain monochrome KPI chips (restrained, no charts). */}
      {analytics && (
        <div className="flex flex-wrap gap-2 mb-4">
          {[
            { label: 'Currently lapsed', value: analytics.currently_lapsed },
            { label: 'Logged (90d)', value: analytics.logged },
            { label: 'Reached', value: analytics.reached },
            { label: 'Visits scheduled', value: analytics.scheduled_visit },
          ].map((k) => (
            <div key={k.label} className="border border-gray-200 rounded-lg px-4 py-2 bg-white">
              <div className="text-xl font-semibold text-gray-900">{k.value}</div>
              <div className="text-xs text-gray-500">{k.label}</div>
            </div>
          ))}
        </div>
      )}

      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-6 h-6 text-gray-400 animate-spin" />
        </div>
      ) : !storeId ? (
        <p className="text-gray-500 py-8">Select a store to see its reactivation list.</p>
      ) : entries.length === 0 ? (
        <p className="text-gray-500 py-8">No lapsed patients to reactivate today.</p>
      ) : (
        <div className="flex flex-col gap-2">
          {entries.map((e) => (
            <div
              key={e.customer_id}
              className={
                'bg-white border border-gray-200 rounded-lg p-4 flex items-start gap-4' +
                (e.is_vip ? ' border-l-4 border-l-amber-400' : '')
              }
            >
              <span className="text-gray-400 text-sm font-mono w-8 shrink-0 pt-0.5">#{e.rank}</span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-gray-900 font-medium">{e.customer_name}</span>
                  {e.customer_mobile && (
                    <a href={`tel:${e.customer_mobile}`} className="text-gray-500 text-sm hover:underline">
                      {e.customer_mobile}
                    </a>
                  )}
                  {e.is_vip && (
                    <span className="text-xs text-amber-700 bg-amber-50 rounded px-1.5 py-0.5">VIP</span>
                  )}
                  <span className="text-xs border border-gray-300 text-gray-600 rounded px-1.5 py-0.5">
                    {lapseLabel(e.months_lapsed)}
                  </span>
                </div>
                <p className="text-gray-800 text-sm mt-1">{e.headline}</p>
                <div className="flex items-center gap-1.5 mt-2 flex-wrap">
                  {e.lifetime_value > 0 && (
                    <span className="text-xs text-gray-500">LTV {rupees(e.lifetime_value)}</span>
                  )}
                  {e.last_touch_date && (
                    <span className="text-xs text-gray-500">Last seen {e.last_touch_date}</span>
                  )}
                  {e.tags.map((t) => (
                    <span key={t} className="border border-gray-300 text-gray-600 text-xs rounded px-1.5 py-0.5">
                      {t}
                    </span>
                  ))}
                </div>
              </div>
              <div className="shrink-0">
                <button
                  type="button"
                  onClick={() => setActFor(e)}
                  className="bg-bv-red text-white rounded px-3 py-1.5 text-sm"
                >
                  Log outcome
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Outcome modal */}
      {actFor && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg p-5 w-full max-w-md">
            <h2 className="text-gray-900 font-medium mb-1">Reactivation outcome &mdash; {actFor.customer_name}</h2>
            <p className="text-gray-500 text-xs mb-3">
              Record what happened. This stays as an in-app note; no message is sent.
            </p>
            <div className="flex flex-col gap-2 mb-3">
              {OUTCOMES.map((o) => (
                <label key={o.value} className="flex items-center gap-2 text-sm text-gray-700">
                  <input
                    type="radio"
                    name="react-outcome"
                    value={o.value}
                    checked={outcome === o.value}
                    onChange={() => setOutcome(o.value)}
                  />
                  {o.label}
                </label>
              ))}
            </div>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Notes (optional)"
              rows={2}
              className="w-full border border-gray-200 rounded px-2 py-1.5 text-sm"
            />
            <label htmlFor="react-next" className="block text-xs text-gray-500 mt-3 mb-1">
              Schedule next touch (optional)
            </label>
            <input
              id="react-next"
              type="date"
              value={nextDate}
              onChange={(e) => setNextDate(e.target.value)}
              className="border border-gray-200 rounded px-2 py-1 text-sm"
            />
            <div className="flex justify-end gap-2 mt-4">
              <button type="button" onClick={closeModal} className="text-gray-600 text-sm px-3 py-1.5">Cancel</button>
              <button
                type="button"
                onClick={submit}
                disabled={busy}
                className="bg-bv-red text-white rounded px-3 py-1.5 text-sm disabled:opacity-40"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default LapsedReactivationPage;
