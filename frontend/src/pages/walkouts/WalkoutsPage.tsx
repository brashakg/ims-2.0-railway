// ============================================================================
// IMS 2.0 — Walkouts Page (Pune Incentive Module i, Phase 1)
// ============================================================================
// Phase 1 ships intake only — list view, filters, follow-ups, and the
// won-back conversion feed land in Phases 2-5. See
// docs/PUNE_INCENTIVE_BUILD_PLAN.md for the full roadmap.

import { useState } from 'react';
import { Plus, UserX } from 'lucide-react';
import { useToast } from '../../context/ToastContext';
import { WalkoutIntakeModal } from './WalkoutIntakeModal';

export function WalkoutsPage() {
  const toast = useToast();
  const [isModalOpen, setIsModalOpen] = useState(false);

  const handleSaved = (walkoutId: string) => {
    toast.success(`Walkout logged: ${walkoutId}`);
    setIsModalOpen(false);
  };

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
            <UserX className="w-6 h-6 text-bv-red-500" />
            Walkouts
          </h1>
          <p className="text-gray-500 mt-1 text-sm">
            Log customers who left without buying. Phase 2 will add the list view, follow-ups, and the won-back feed.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setIsModalOpen(true)}
          className="btn-primary inline-flex items-center gap-2"
        >
          <Plus className="w-4 h-4" />
          Log Walkout
        </button>
      </div>

      <div className="card p-12 text-center">
        <UserX className="w-12 h-12 text-gray-300 mx-auto mb-3" />
        <h2 className="text-lg font-semibold text-gray-700 mb-1">Intake-only mode</h2>
        <p className="text-gray-500 text-sm max-w-md mx-auto">
          Click <span className="font-medium">Log Walkout</span> to capture a 30-field intake. The list,
          per-walkout follow-up tracking, and the conversion-feed dashboard ship in upcoming phases.
        </p>
      </div>

      <WalkoutIntakeModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onSaved={handleSaved}
      />
    </div>
  );
}

export default WalkoutsPage;
