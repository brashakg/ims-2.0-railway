// ============================================================================
// IMS 2.0 — POS Loyalty Redeem Control
// ============================================================================
// Sits inside the Payment step. Reads the customer's current account
// from /loyalty/account, lets the cashier slide a points-amount, calls
// /loyalty/redeem, and records the rupee discount as a LOYALTY payment
// line so it gets reflected in balance_due / change calculations like
// any other tender.
//
// Fail-soft: if the account fetch fails or the engine returns a 4xx,
// the control just shows the error and keeps the rest of the payment
// step usable. No state held outside the component.

import { useEffect, useMemo, useState } from 'react';
import { Award } from 'lucide-react';

import { usePOSStore } from '../../stores/posStore';
import {
  loyaltyApi,
  type LoyaltyAccount,
  type LoyaltySettings,
} from '../../services/api/loyalty';

export function LoyaltyRedeemControl() {
  const store = usePOSStore();
  const [account, setAccount] = useState<LoyaltyAccount | null>(null);
  const [settings, setSettings] = useState<LoyaltySettings | null>(null);
  const [loading, setLoading] = useState(false);
  const [pointsToRedeem, setPointsToRedeem] = useState<number>(0);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const customerId = store.customer?.id;
  const orderTotal = store.getGrandTotal();

  // Has the cashier already locked in a loyalty payment line in this
  // transaction? If yes, hide the control (otherwise they could
  // double-redeem).
  const alreadyRedeemed = (store.payments || []).some(
    (p) => p.method === 'LOYALTY',
  );

  useEffect(() => {
    let alive = true;
    if (!customerId || customerId.toString().startsWith('walkin-')) return;
    setLoading(true);
    loyaltyApi
      .getAccount(String(customerId))
      .then((envelope) => {
        if (!alive) return;
        setAccount(envelope.account);
        setSettings(envelope.settings);
        // Default redeem amount: balance, but never more than the cap
        // would allow. The redeem endpoint applies the real cap; this is
        // just the UI default.
        const maxByBalance = envelope.account?.balance_points ?? 0;
        const cap = (envelope.settings?.max_redeem_pct_of_order ?? 50) / 100;
        const rate = envelope.settings?.redeem_rupee_per_point ?? 1;
        const maxByCap = Math.floor((orderTotal * cap) / Math.max(rate, 0.001));
        setPointsToRedeem(Math.min(maxByBalance, maxByCap));
      })
      .catch(() => {
        // fail-soft
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customerId]);

  const minRedeem = settings?.min_redeem_points ?? 100;
  const balance = account?.balance_points ?? 0;
  const rate = settings?.redeem_rupee_per_point ?? 1;
  const maxPct = settings?.max_redeem_pct_of_order ?? 50;
  const maxByCap = useMemo(
    () => Math.floor((orderTotal * (maxPct / 100)) / Math.max(rate, 0.001)),
    [orderTotal, maxPct, rate],
  );
  const maxAllowed = Math.min(balance, maxByCap);
  const rupeeForCurrent = Math.round(pointsToRedeem * rate * 100) / 100;

  // Eligibility: customer + balance >= min + a positive order total.
  const eligible =
    !!customerId &&
    !customerId.toString().startsWith('walkin-') &&
    balance >= minRedeem &&
    orderTotal > 0 &&
    !alreadyRedeemed;

  if (alreadyRedeemed) {
    return (
      <div className="bg-green-50 border border-green-200 rounded-xl p-3 text-sm text-green-700">
        <Award className="inline w-4 h-4 mr-1" /> Loyalty points applied below.
      </div>
    );
  }

  if (loading) {
    return (
      <div className="bg-gray-50 border border-gray-200 rounded-xl p-3 text-sm text-gray-500">
        Loading loyalty account…
      </div>
    );
  }

  if (!account) return null;
  if (!eligible) return null;

  const apply = async () => {
    setError(null);
    if (pointsToRedeem < minRedeem) {
      setError(`Minimum redeem is ${minRedeem} points.`);
      return;
    }
    setSubmitting(true);
    try {
      const r = await loyaltyApi.redeem({
        customer_id: String(customerId),
        order_id: store.order_id ?? undefined,
        points: pointsToRedeem,
        order_value: orderTotal,
      });
      // Persist to POS as a LOYALTY tender line
      store.addPayment({
        method: 'LOYALTY',
        amount: r.rupee_value,
        reference: r.txn_id,
      });
      // Reflect on the local widget state
      setAccount({
        ...account,
        balance_points: balance - r.redeemed_points,
      });
      store.setCustomerLoyaltyPoints(balance - r.redeemed_points);
    } catch (err: any) {
      const detail = err?.response?.data?.detail;
      setError(
        typeof detail === 'string'
          ? detail
          : detail?.reason || 'Redeem failed. Try a smaller amount.',
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Award className="w-5 h-5 text-bv-red-600" />
          <span className="font-medium text-gray-900">Redeem loyalty points</span>
        </div>
        <span className="text-xs text-gray-500">
          Balance: <span className="font-semibold text-gray-900">{balance.toLocaleString('en-IN')}</span>
        </span>
      </div>

      <div>
        <div className="flex items-center justify-between text-xs text-gray-600 mb-1">
          <span>Apply</span>
          <span>
            {pointsToRedeem.toLocaleString('en-IN')} pts → ₹{rupeeForCurrent.toLocaleString('en-IN')}
          </span>
        </div>
        <input
          type="range"
          min={minRedeem}
          max={maxAllowed}
          step={1}
          value={pointsToRedeem}
          onChange={(e) => setPointsToRedeem(Number(e.target.value))}
          className="w-full"
          aria-label="Points to redeem"
        />
        <div className="flex justify-between text-[11px] text-gray-400 mt-1">
          <span>{minRedeem.toLocaleString('en-IN')} min</span>
          <span>
            {maxAllowed.toLocaleString('en-IN')} max
            {maxAllowed === maxByCap && balance > maxByCap
              ? ` (cap ${maxPct}%)`
              : ''}
          </span>
        </div>
      </div>

      {error && (
        <p className="text-xs text-red-600">{error}</p>
      )}

      <button
        onClick={apply}
        disabled={submitting || pointsToRedeem < minRedeem || pointsToRedeem > maxAllowed}
        className="w-full px-4 py-2 rounded-lg text-sm font-semibold bg-bv-red-600 text-white disabled:bg-gray-200 disabled:text-gray-500 hover:bg-bv-red-700"
      >
        {submitting ? 'Applying…' : `Apply ₹${rupeeForCurrent.toLocaleString('en-IN')} discount`}
      </button>
    </div>
  );
}
