import { Banknote, Coins } from 'lucide-react';
import type { DenomRow } from '../../utils/denominations';

/**
 * THE count sheet. One component, used everywhere cash is counted note by note
 * -- the cash register open/close, the blind EOD tally, the POS day-end close.
 *
 * Two near-identical copies of this grid used to live inside two pages, with
 * two different row shapes and two different piece-setter signatures. They are
 * gone; this is the survivor.
 */
export default function DenominationGrid({
  rows,
  onChange,
  disabled,
  showHeader = false,
}: {
  rows: DenomRow[];
  /** index of the row, and its new piece count (already clamped >= 0). */
  onChange: (index: number, pieces: number) => void;
  disabled?: boolean;
  showHeader?: boolean;
}) {
  const inr = (n: number) => `₹${Math.round(n).toLocaleString('en-IN')}`;
  return (
    <div className="space-y-1.5">
      {showHeader && (
        <div className="flex items-center justify-between px-3 text-xs text-gray-400 uppercase tracking-wide">
          <span>Denomination</span>
          <span className="flex items-center gap-3">
            <span className="w-20 text-center">Pieces</span>
            <span className="w-24 text-right">Amount</span>
          </span>
        </div>
      )}
      <div className="divide-y divide-gray-100 border border-gray-200 rounded-lg overflow-hidden">
        {rows.map((r, i) => (
          <div
            key={`${r.kind}-${r.face}`}
            className="flex items-center justify-between px-3 py-1.5 text-sm"
          >
            <span className="flex items-center gap-2 text-gray-700">
              {r.kind === 'note' ? (
                <Banknote className="w-4 h-4 text-gray-400" />
              ) : (
                <Coins className="w-4 h-4 text-gray-400" />
              )}
              <span className="tabular-nums">{`₹${r.face}`}</span>
              <span className="text-gray-400 text-xs uppercase">{r.kind}</span>
            </span>
            <span className="flex items-center gap-3">
              <input
                type="number"
                min={0}
                value={r.pieces || ''}
                disabled={disabled}
                placeholder="0"
                aria-label={`${r.kind} of ${r.face} rupees, pieces`}
                onChange={(e) => onChange(i, Math.max(0, parseInt(e.target.value, 10) || 0))}
                className="w-20 px-2 py-1 border border-gray-300 rounded text-right tabular-nums focus:outline-none focus:ring-1 focus:ring-bv disabled:bg-gray-50"
              />
              <span className="w-24 text-right text-gray-500 tabular-nums">
                {inr(r.face * (r.pieces || 0))}
              </span>
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
