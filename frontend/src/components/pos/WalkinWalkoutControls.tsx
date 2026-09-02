// ============================================================================
// Walk-in (+1 footfall) and Walkout capture — the POS pair, in ONE place
// ============================================================================
// Both counters feed the same conversion maths the incentive engine runs:
// footfall in, walkouts out, sales in between. The classic POS carried these
// two buttons in its toolbar and the redesigned surfaces shipped only the
// compliance BANNER that nags about them — so the till told staff to log
// walkouts on a screen with no way to log one.
//
// One component, mounted by every counter surface, rather than a copy per
// surface: two implementations of a rule is how this repo's worst bugs start,
// and "which button did they press" is exactly the kind of thing that drifts.

import { useState } from 'react';
import { UserPlus, DoorOpen } from 'lucide-react';
import { usePOSStore } from '../../stores/posStore';
import { useToast } from '../../context/ToastContext';
import { walkoutsApi } from '../../services/api/walkouts';
import { WalkoutIntakeModal } from '../../pages/walkouts/WalkoutIntakeModal';

export function WalkinWalkoutControls({ className = '' }: { className?: string }) {
  const store = usePOSStore();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [walkoutOpen, setWalkoutOpen] = useState(false);

  // Footfall is attributed to the salesperson who greeted the customer, so a
  // walk-in with nobody selected has nothing to attribute and would quietly
  // skew every conversion rate. Nudge instead of guessing.
  const recordWalkin = async () => {
    if (!store.salesperson_id) {
      toast.warning('Pick a salesperson first to record a walk-in');
      return;
    }
    setBusy(true);
    try {
      const r = await walkoutsApi.walkinsPosIncrement(
        {
          sales_person_id: store.salesperson_id,
          mobile: store.customer?.phone || undefined,
        },
        store.store_id || undefined,
      );
      toast.success(
        r?.deduped ? 'Already counted today' : `Walk-in recorded - ${r?.total ?? ''} today`,
      );
    } catch {
      toast.error('Could not record walk-in');
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className={`flex items-center gap-1.5 ${className}`}>
        <button
          type="button"
          onClick={recordWalkin}
          disabled={busy}
          title="Record a walk-in (footfall) for the current salesperson"
          className="inline-flex items-center gap-1.5 px-2.5 min-h-[36px] rounded-lg border border-gray-200 bg-white text-[11px] font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
        >
          <UserPlus className="w-3.5 h-3.5" />
          {busy ? 'Recording…' : '+1 walk-in'}
        </button>
        <button
          type="button"
          onClick={() => setWalkoutOpen(true)}
          title="Log a customer who left without buying"
          className="inline-flex items-center gap-1.5 px-2.5 min-h-[36px] rounded-lg border border-gray-200 bg-white text-[11px] font-medium text-gray-700 hover:bg-gray-50"
        >
          <DoorOpen className="w-3.5 h-3.5" />
          Walkout
        </button>
      </div>

      <WalkoutIntakeModal
        isOpen={walkoutOpen}
        onClose={() => setWalkoutOpen(false)}
        onSaved={() => {
          toast.success('Walkout logged');
          setWalkoutOpen(false);
        }}
      />
    </>
  );
}

export default WalkinWalkoutControls;
