// ============================================================================
// IMS 2.0 - F39 NBA (next-best-action) daily call list (#39)
// ============================================================================
// A ranked daily list (max 15, 2 reserved VIP slots) of customers a store
// associate should MANUALLY PHONE today. This is a CALL LIST, NOT a message
// channel: marking a card Done/Skip records an in-app follow-up outcome -- it
// NEVER sends a WhatsApp/SMS (the channel is disabled; #39 is dark).
//
// Restrained/executive UI: white cards, neutral chips, a single amber left-edge
// accent on the reserved VIP slots, colour used only for semantic meaning. The
// internal score is never fetched or shown -- associates work by rank.

import { useCallback, useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { crmApi } from '../../services/api/crm';
import type { NbaCard, NbaDismissReason } from '../../services/api/crm';

const SKIP_REASONS: { value: NbaDismissReason; label: string }[] = [
  { value: 'not_interested', label: 'Not interested' },
  { value: 'already_called', label: 'Already called' },
  { value: 'no_answer', label: 'No answer' },
  { value: 'wrong_number', label: 'Wrong number' },
];

function rupees(paisa: number): string {
  return `₹${Math.round((paisa ?? 0) / 100).toLocaleString('en-IN')}`;
}

function tierChipClass(tier: string | null): string {
  // Neutral by default; GOLD/PLATINUM get a subtle tint (semantic only).
  const t = (tier || '').toUpperCase();
  if (t === 'GOLD') return 'bg-amber-50 text-amber-700';
  if (t === 'PLATINUM') return 'bg-slate-100 text-slate-700';
  return 'bg-gray-100 text-gray-700';
}

function prettyDate(iso: string): string {
  const d = new Date(iso + 'T00:00:00');
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString('en-IN', { day: 'numeric', month: 'long', year: 'numeric' });
}

export function NBADashboardPage() {
  const { user } = useAuth();
  const isHq = (user?.roles || []).some((r) => ['SUPERADMIN', 'ADMIN', 'AREA_MANAGER'].includes(r));

  const [storeId, setStoreId] = useState<string>(user?.activeStoreId || '');
  const [cards, setCards] = useState<NbaCard[]>([]);
  const [listDate, setListDate] = useState<string>('');
  const [isLoading, setIsLoading] = useState(true);

  // Done / Skip modal state, keyed on the selected card.
  const [doneFor, setDoneFor] = useState<NbaCard | null>(null);
  const [skipFor, setSkipFor] = useState<NbaCard | null>(null);
  const [notes, setNotes] = useState('');
  const [nextDate, setNextDate] = useState('');
  const [skipReason, setSkipReason] = useState<NbaDismissReason>('no_answer');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!storeId) {
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    try {
      const res = await crmApi.getNbaCallList(storeId);
      setCards(Array.isArray(res?.cards) ? res.cards : []);
      setListDate(res?.date || '');
    } catch {
      setCards([]);
    } finally {
      setIsLoading(false);
    }
  }, [storeId]);

  useEffect(() => {
    load();
  }, [load]);

  const closeModals = () => {
    setDoneFor(null);
    setSkipFor(null);
    setNotes('');
    setNextDate('');
    setSkipReason('no_answer');
  };

  const submitDone = async () => {
    if (!doneFor || notes.trim().length < 10) return;
    setBusy(true);
    try {
      await crmApi.completeNbaCard(storeId, doneFor.customer_id, notes.trim(), nextDate || undefined);
      closeModals();
      await load();
    } catch {
      /* fail-soft: leave the card so the associate can retry */
    } finally {
      setBusy(false);
    }
  };

  const submitSkip = async () => {
    if (!skipFor) return;
    setBusy(true);
    try {
      await crmApi.dismissNbaCard(storeId, skipFor.customer_id, skipReason);
      closeModals();
      await load();
    } catch {
      /* fail-soft */
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="inv-body">
      <div className="inv-head">
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>CRM &middot; Daily Calls</div>
          <h1>Today&rsquo;s call list{listDate ? ` — ${prettyDate(listDate)}` : ''}.</h1>
          <div className="hint">
            The {cards.length} customers worth a phone call today, ranked. The top slots are reserved for VIPs.
            Call them, then mark Done with what you discussed, or Skip with a reason.
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
          <label htmlFor="nba-store" className="block text-xs text-gray-500 mb-1">Store</label>
          <input
            id="nba-store"
            type="text"
            value={storeId}
            onChange={(e) => setStoreId(e.target.value)}
            placeholder="Store ID"
            className="border border-gray-200 rounded px-2 py-1 text-sm"
          />
        </div>
      )}

      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="w-6 h-6 text-gray-400 animate-spin" />
        </div>
      ) : !storeId ? (
        <p className="text-gray-500 py-8">Select a store to see its call list.</p>
      ) : cards.length === 0 ? (
        <p className="text-gray-500 py-8">No calls scheduled for today.</p>
      ) : (
        <div className="flex flex-col gap-2">
          {cards.map((c) => (
            <div
              key={c.customer_id}
              className={
                'bg-white border border-gray-200 rounded-lg p-4 flex items-start gap-4' +
                (c.is_vip_slot ? ' border-l-4 border-l-amber-400' : '')
              }
            >
              <span className="text-gray-400 text-sm font-mono w-8 shrink-0 pt-0.5">#{c.rank}</span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-gray-900 font-medium">{c.customer_name}</span>
                  {c.customer_mobile && (
                    <a href={`tel:${c.customer_mobile}`} className="text-gray-500 text-sm hover:underline">
                      {c.customer_mobile}
                    </a>
                  )}
                  {c.is_vip_slot && (
                    <span className="text-xs text-amber-700 bg-amber-50 rounded px-1.5 py-0.5">VIP</span>
                  )}
                </div>
                <p className="text-gray-800 text-sm mt-1">{c.headline}</p>
                {c.sub_headlines.map((s, i) => (
                  <p key={i} className="text-gray-500 text-xs">{s}</p>
                ))}
                <div className="flex items-center gap-1.5 mt-2 flex-wrap">
                  {c.loyalty_tier && (
                    <span className={'text-xs rounded px-1.5 py-0.5 ' + tierChipClass(c.loyalty_tier)}>
                      {c.loyalty_tier}
                    </span>
                  )}
                  {c.lifetime_value > 0 && (
                    <span className="text-xs text-gray-500">LTV {rupees(c.lifetime_value)}</span>
                  )}
                  {c.tags.map((t) => (
                    <span key={t} className="border border-gray-300 text-gray-600 text-xs rounded px-1.5 py-0.5">
                      {t}
                    </span>
                  ))}
                </div>
              </div>
              <div className="shrink-0 flex items-start">
                <button
                  type="button"
                  onClick={() => { setDoneFor(c); }}
                  className="bg-bv-red text-white rounded px-3 py-1.5 text-sm"
                >
                  Done
                </button>
                <button
                  type="button"
                  onClick={() => { setSkipFor(c); }}
                  className="border border-gray-300 text-gray-700 rounded px-3 py-1.5 text-sm ml-2"
                >
                  Skip
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Done modal */}
      {doneFor && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg p-5 w-full max-w-md">
            <h2 className="text-gray-900 font-medium mb-1">Call done &mdash; {doneFor.customer_name}</h2>
            <p className="text-gray-500 text-xs mb-3">Record what you discussed. This stays as an in-app note; no message is sent.</p>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="What did you discuss? Min 10 characters"
              rows={3}
              className="w-full border border-gray-200 rounded px-2 py-1.5 text-sm"
            />
            <label htmlFor="nba-next" className="block text-xs text-gray-500 mt-3 mb-1">
              Schedule next follow-up (optional)
            </label>
            <input
              id="nba-next"
              type="date"
              value={nextDate}
              onChange={(e) => setNextDate(e.target.value)}
              className="border border-gray-200 rounded px-2 py-1 text-sm"
            />
            <div className="flex justify-end gap-2 mt-4">
              <button type="button" onClick={closeModals} className="text-gray-600 text-sm px-3 py-1.5">Cancel</button>
              <button
                type="button"
                onClick={submitDone}
                disabled={busy || notes.trim().length < 10}
                className="bg-bv-red text-white rounded px-3 py-1.5 text-sm disabled:opacity-40"
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Skip modal */}
      {skipFor && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg p-5 w-full max-w-md">
            <h2 className="text-gray-900 font-medium mb-3">Skip &mdash; {skipFor.customer_name}</h2>
            <div className="flex flex-col gap-2">
              {SKIP_REASONS.map((r) => (
                <label key={r.value} className="flex items-center gap-2 text-sm text-gray-700">
                  <input
                    type="radio"
                    name="skip-reason"
                    value={r.value}
                    checked={skipReason === r.value}
                    onChange={() => setSkipReason(r.value)}
                  />
                  {r.label}
                </label>
              ))}
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <button type="button" onClick={closeModals} className="text-gray-600 text-sm px-3 py-1.5">Cancel</button>
              <button
                type="button"
                onClick={submitSkip}
                disabled={busy}
                className="border border-gray-300 text-gray-700 rounded px-3 py-1.5 text-sm disabled:opacity-40"
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default NBADashboardPage;
