// ============================================================================
// IMS 2.0 - Period Management Tab
// ============================================================================

import { Calendar, Lock, Unlock } from 'lucide-react';

interface PeriodManagementProps {
  periodLocked: boolean;
  onLockPeriod: () => void;
  onUnlockPeriod: () => void;
  dateFrom: string;
  dateTo: string;
}

// Derive a human period label + financial-year tag from the real selected
// date range (was hardcoded "April - June 2025"). Indian FY runs Apr 1 -> Mar 31.
function fyLabel(dateStr: string): string {
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return '';
  const y = d.getFullYear();
  const fyStart = d.getMonth() >= 3 ? y : y - 1; // months are 0-indexed; Apr = 3
  return `${fyStart}-${String((fyStart + 1) % 100).padStart(2, '0')}`;
}

function periodLabel(from: string, to: string): string {
  const f = new Date(from);
  const t = new Date(to);
  if (isNaN(f.getTime()) || isNaN(t.getTime())) return 'Selected period';
  const fmt = (d: Date) =>
    d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
  return `${fmt(f)} - ${fmt(t)}`;
}

export default function PeriodManagement({
  periodLocked,
  onLockPeriod,
  onUnlockPeriod,
  dateFrom,
  dateTo,
}: PeriodManagementProps) {
  return (
    <div className="space-y-6">
      {/* Period Status */}
      <div className="bg-white border border-gray-200 rounded-lg overflow-hidden">
        <div className="bg-slate-50 px-6 py-4 border-b border-gray-200">
          <h3 className="text-gray-900 font-semibold flex items-center gap-2">
            <Calendar className="w-5 h-5 text-cyan-600" />
            Financial Period Management
          </h3>
        </div>
        <div className="p-6 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="bg-slate-50 p-4 rounded border border-gray-200">
              <p className="text-slate-600 text-sm">Current Period</p>
              <p className="text-gray-900 text-lg font-semibold mt-2">{periodLabel(dateFrom, dateTo)}</p>
              <p className="text-xs text-slate-500 mt-1">Financial Year: {fyLabel(dateTo)}</p>
            </div>
            <div className="bg-slate-50 p-4 rounded border border-gray-200">
              <p className="text-slate-600 text-sm">Period Status</p>
              <div className="flex items-center gap-2 mt-2">
                {periodLocked ? (
                  <Lock className="w-5 h-5 text-red-600" />
                ) : (
                  <Unlock className="w-5 h-5 text-green-600" />
                )}
                <span className="text-gray-900 text-lg font-semibold">
                  {periodLocked ? 'Locked' : 'Open'}
                </span>
              </div>
              <p className="text-xs text-slate-500 mt-1">
                {periodLocked ? 'No transactions allowed' : 'Editable'}
              </p>
            </div>
          </div>

          {/* Lock/Unlock Actions */}
          <div className="flex gap-3 pt-4 border-t border-gray-200">
            {!periodLocked ? (
              <button
                onClick={onLockPeriod}
                className="flex items-center gap-2 px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded border border-red-700 transition-colors font-medium text-sm"
              >
                <Lock className="w-4 h-4" />
                Lock Period
              </button>
            ) : (
              <button
                onClick={onUnlockPeriod}
                className="flex items-center gap-2 px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded border border-green-700 transition-colors font-medium text-sm"
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
