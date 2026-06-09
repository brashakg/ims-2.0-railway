// ============================================================================
// IMS 2.0 — POS Walkout Compliance Banner (F45 D5/D8)
// ============================================================================
// A SOFT-BLOCK ONLY, dismissable banner shown at the top of POS when the
// current salesperson has open walkouts without a follow-up. It NEVER blocks a
// sale (DECISIONS sec 2 item 10) — it nudges the associate to keep their
// conversion score honest.
//
// FEATURE-FLAG GUARD: gated on VITE_ENABLE_POS_WALKOUT_COMPLIANCE_BANNER.
// When the flag is OFF (the default), this component renders null AND makes NO
// network call — so the revenue-critical POS is byte-for-byte unchanged. The
// orchestrator flips the flag per store after validation.

import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, X } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { walkoutsApi } from '../../services/api';

// Off by default. Only 'true' enables the banner (matches the POS_AUTO_ATTACH
// flag convention in POSLayout.tsx).
const FLAG_ON =
  import.meta.env.VITE_ENABLE_POS_WALKOUT_COMPLIANCE_BANNER === 'true';

export function WalkoutComplianceBanner() {
  const { user } = useAuth();
  const [openCount, setOpenCount] = useState(0);
  const [dismissed, setDismissed] = useState(false);

  const storeId = user?.activeStoreId || '';
  const salesPersonId = user?.id || '';

  useEffect(() => {
    // Flag-off short-circuit: no fetch, no side effect on POS.
    if (!FLAG_ON || !storeId || !salesPersonId) return;
    let cancelled = false;
    walkoutsApi
      .posComplianceCheck(storeId, salesPersonId)
      .then((res) => {
        if (!cancelled) setOpenCount(res.open_count || 0);
      })
      .catch(() => {
        // Read-only nudge — a failed check must never disrupt POS.
        if (!cancelled) setOpenCount(0);
      });
    return () => {
      cancelled = true;
    };
  }, [storeId, salesPersonId]);

  if (!FLAG_ON || dismissed || openCount <= 0) return null;

  return (
    <div className="flex items-center gap-3 bg-amber-50 border-b border-amber-200 text-amber-800 px-4 py-2 text-sm">
      <AlertTriangle className="w-4 h-4 shrink-0" />
      <span className="flex-1">
        You have <span className="font-semibold">{openCount}</span> walkout
        {openCount === 1 ? '' : 's'} without a follow-up. Log them to maintain
        your conversion score.
      </span>
      <Link
        to="/walkouts"
        className="underline font-medium hover:text-amber-900 shrink-0"
      >
        View walkouts
      </Link>
      <button
        type="button"
        onClick={() => setDismissed(true)}
        className="p-1 rounded hover:bg-amber-100 shrink-0"
        aria-label="Dismiss"
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}

export default WalkoutComplianceBanner;
