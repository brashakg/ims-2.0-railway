// ============================================================================
// IMS 2.0 — POS Loyalty Redeem Control
// ============================================================================
// Sits inside the Payment step. Reads the customer's current account
// from /loyalty/account, lets the cashier slide a points-amount, and
// records a DEFERRED redemption intent in posStore so the LOYALTY line
// is reflected in balance_due / change calculations.
//
// POS-3 FIX: The actual /loyalty/redeem call (which atomically debits
// points) is deferred to POSLayout.handleCreateOrder() AFTER the order
// is confirmed. This prevents points being burned on a failed order.
//
// Fail-soft: if the account fetch fails or validation fails, the control
// just shows the error and keeps the rest of the payment step usable.
// No state held outside the component.

import { useEffect, useMemo, useState } from 'react';
import { Award } from 'lucide-react';

import { usePOSStore } from '../../stores/posStore';
import {
  type LoyaltyAccount,
  type LoyaltySettings,
  loyaltyApi,
} from '../../services/api/loyalty';

export function LoyaltyRedeemControl() {
  const store = usePOSStore();
  const [account, setAccount] = useState<LoyaltyAccount | null>(null);
  const [settings, setSettings] = useState<LoyaltySettings | null>(null);
  const [loading, setLoading] = useState(false);
  const [pointsToRedeem, setPointsToRedeem] = useState<number>(0);
  const [error, setError] = useState<string | null>(null);
  // OTP-on-redemption (owner ruling: loyalty redemption ONLY). The server
  // says whether this store requires it; when it does, Apply first sends a
  // code to the CUSTOMER's mobile and staff enter it here. When it does not
  // (dark deploy / policy off), this state stays inert and the control
  // behaves exactly as before.
  const [otpRequired, setOtpRequired] = useState(false);
  const [otpSentTo, setOtpSentTo] = useState<string | null>(null);
  const [otpCode, setOtpCode] = useState('');
  const [otpBusy, setOtpBusy] = useState(false);

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
        setOtpRequired(envelope.redeem_otp_required === true);
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

  // POS-3: compute expected rupee value client-side using the same formula
  // as the backend (settings.redeem_rupee_per_point). The actual atomic debit
  // happens in POSLayout after the order is created, so points are only spent
  // on a successfully created order.
  const computeRupeeValue = (points: number): number => {
    return Math.round(points * rate * 100) / 100;
  };

  // The original apply body: records the deferred intent + LOYALTY tender
  // line. Runs directly when no OTP is required; after a verified code when
  // it is.
  const commitApply = () => {
    const rupeeValue = computeRupeeValue(pointsToRedeem);
    // Record the deferred intent — the actual /loyalty/redeem call (which
    // atomically debits points) runs in POSLayout after createOrder succeeds.
    store.setPendingLoyaltyRedeem({
      points: pointsToRedeem,
      rupeeValue,
      orderValue: orderTotal,
    });
    // Add the LOYALTY tender line to posStore so balance_due reflects it
    // before the order is finalized.
    store.addPayment({
      method: 'LOYALTY',
      amount: rupeeValue,
      reference: `PENDING:${pointsToRedeem}pts`,
    });
    // Optimistically reflect the deduction on the widget so it hides itself
    setAccount({
      ...account,
      balance_points: balance - pointsToRedeem,
    });
    store.setCustomerLoyaltyPoints(balance - pointsToRedeem);
  };

  const validateBounds = (): boolean => {
    setError(null);
    if (pointsToRedeem < minRedeem) {
      setError(`Minimum redeem is ${minRedeem} points.`);
      return false;
    }
    if (pointsToRedeem > maxAllowed) {
      setError(`Cannot exceed ${maxAllowed} points (balance or order cap).`);
      return false;
    }
    return true;
  };

  const apply = async () => {
    if (!validateBounds()) return;
    if (!otpRequired) {
      commitApply();
      return;
    }
    // OTP path: send the code to the customer's stored mobile and open the
    // code input. Nothing is applied until the code verifies.
    setOtpBusy(true);
    try {
      const res = await loyaltyApi.redeemOtpSend(String(customerId));
      if (!res.otp_required) {
        // Server says the gate is off (e.g. policy changed) - proceed as today.
        commitApply();
        return;
      }
      setOtpSentTo(res.sent_to_last4 ?? '');
      setOtpCode('');
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: unknown } } })
        ?.response?.data?.detail;
      setError(
        typeof detail === 'string'
          ? detail
          : 'Could not send the verification code. Try again.',
      );
    } finally {
      setOtpBusy(false);
    }
  };

  const verifyAndApply = async () => {
    if (!validateBounds()) return;
    setOtpBusy(true);
    try {
      await loyaltyApi.redeemOtpVerify(String(customerId), otpCode.trim());
      setOtpSentTo(null);
      setOtpCode('');
      commitApply();
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: unknown } } })
        ?.response?.data?.detail;
      setError(
        typeof detail === 'string'
          ? detail
          : 'That code is not correct - check with the customer and retry.',
      );
    } finally {
      setOtpBusy(false);
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

      {otpSentTo !== null ? (
        <div className="space-y-2">
          <p className="text-xs text-gray-600">
            Code sent to the customer&apos;s mobile
            {otpSentTo ? ` ending ${otpSentTo}` : ''}. Ask the customer for it.
          </p>
          <input
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={6}
            value={otpCode}
            onChange={(e) => setOtpCode(e.target.value.replace(/\D/g, ''))}
            placeholder="6-digit code"
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm tracking-widest"
            aria-label="Customer verification code"
          />
          <button
            onClick={verifyAndApply}
            disabled={otpBusy || otpCode.trim().length < 4}
            className="w-full px-4 py-2 rounded-lg text-sm font-semibold bg-bv-red-600 text-white disabled:bg-gray-200 disabled:text-gray-500 hover:bg-bv-red-700"
          >
            {otpBusy ? 'Checking…' : `Verify & apply ₹${rupeeForCurrent.toLocaleString('en-IN')} discount`}
          </button>
          <button
            onClick={apply}
            disabled={otpBusy}
            className="w-full px-4 py-1.5 rounded-lg text-xs font-medium text-gray-600 hover:bg-gray-100 disabled:text-gray-400"
          >
            Resend code
          </button>
        </div>
      ) : (
        <>
          {otpRequired && (
            <p className="text-[11px] text-gray-500">
              This store verifies redemptions: a one-time code goes to the
              customer&apos;s mobile first.
            </p>
          )}
          <button
            onClick={apply}
            disabled={otpBusy || pointsToRedeem < minRedeem || pointsToRedeem > maxAllowed}
            className="w-full px-4 py-2 rounded-lg text-sm font-semibold bg-bv-red-600 text-white disabled:bg-gray-200 disabled:text-gray-500 hover:bg-bv-red-700"
          >
            {otpBusy
              ? 'Sending code…'
              : `Apply ₹${rupeeForCurrent.toLocaleString('en-IN')} discount`}
          </button>
        </>
      )}
    </div>
  );
}
