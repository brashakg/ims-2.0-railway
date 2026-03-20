// ============================================================================
// IMS 2.0 - Period Management Tab
// ============================================================================

import { Calendar, Lock, Unlock } from 'lucide-react';

interface PeriodManagementProps {
  periodLocked: boolean;
  onLockPeriod: () => void;
  onUnlockPeriod: () => void;
}

export default function PeriodManagement({
  periodLocked,
  onLockPeriod,
  onUnlockPeriod,
}: PeriodManagementProps) {
  return (
    <div className="space-y-6">
      {/* Period Status */}
      <div className="bg-slate-800 border border-slate-700 rounded-lg overflow-hidden">
        <div className="bg-slate-900 px-6 py-4 border-b border-slate-700">
          <h3 className="text-white font-semibold flex items-center gap-2">
            <Calendar className="w-5 h-5 text-cyan-400" />
            Financial Period Management
          </h3>
        </div>
        <div className="p-6 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="bg-slate-900 p-4 rounded border border-slate-700">
              <p className="text-slate-400 text-sm">Current Period</p>
              <p className="text-white text-lg font-semibold mt-2">April - June 2025</p>
              <p className="text-xs text-slate-500 mt-1">Financial Year: 2025-26</p>
            </div>
            <div className="bg-slate-900 p-4 rounded border border-slate-700">
              <p className="text-slate-400 text-sm">Period Status</p>
              <div className="flex items-center gap-2 mt-2">
                {periodLocked ? (
                  <Lock className="w-5 h-5 text-red-400" />
                ) : (
                  <Unlock className="w-5 h-5 text-green-400" />
                )}
                <span className="text-white text-lg font-semibold">
                  {periodLocked ? 'Locked' : 'Open'}
                </span>
              </div>
              <p className="text-xs text-slate-500 mt-1">
                {periodLocked ? 'No transactions allowed' : 'Editable'}
              </p>
            </div>
            <div className="bg-slate-900 p-4 rounded border border-slate-700">
              <p className="text-slate-400 text-sm">Last Reconciliation</p>
              <p className="text-white text-lg font-semibold mt-2">2025-06-27</p>
              <p className="text-xs text-green-400 mt-1">Current</p>
            </div>
          </div>

          {/* Lock/Unlock Actions */}
          <div className="flex gap-3 pt-4 border-t border-slate-700">
            {!periodLocked ? (
              <button
                onClick={onLockPeriod}
                className="flex items-center gap-2 px-4 py-2 bg-red-900 hover:bg-red-800 text-red-100 rounded border border-red-700 transition-colors font-medium text-sm"
              >
                <Lock className="w-4 h-4" />
                Lock Period
              </button>
            ) : (
              <button
                onClick={onUnlockPeriod}
                className="flex items-center gap-2 px-4 py-2 bg-green-900 hover:bg-green-800 text-green-100 rounded border border-green-700 transition-colors font-medium text-sm"
              >
                <Unlock className="w-4 h-4" />
                Unlock Period
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
