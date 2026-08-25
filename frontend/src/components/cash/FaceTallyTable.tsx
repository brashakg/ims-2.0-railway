import { AlertTriangle } from 'lucide-react';

/**
 * THE MANAGER'S ANSWER TO "WHERE DID THE DIFFERENCE COME FROM".
 *
 * A drawer that balances to the rupee can still hide two mistakes that
 * cancelled out -- two Rs 500 short and ten Rs 100 over is Rs 0 of variance and
 * two real errors. The single closing figure can never show that; the count
 * face by face is the only thing that can.
 *
 * THE VERDICT IS WITHHELD when the day is not fully counted. A per-face table
 * computed over a partial set looks exactly like a complete one, and presenting
 * it as an answer would be worse than the single number it replaces.
 */

export interface FaceLedgerRow {
  face: number;
  kind: 'note' | 'coin';
  expected_pieces: number;
  counted_pieces: number;
  difference_pieces: number;
  difference_paisa: number;
}

export interface FaceLedgerCoverage {
  cash_sale_legs: number;
  cash_sale_legs_counted: number;
  refund_legs: number;
  refund_legs_counted: number;
  payouts: number;
  payouts_counted: number;
  flagged: number;
}

export interface FaceLedger {
  rows: FaceLedgerRow[];
  coverage: FaceLedgerCoverage;
  read_ok: boolean;
  opening_captured: boolean;
  closing_captured: boolean;
  difference_paisa: number;
}

const inr = (paisa: number) => {
  const sign = paisa < 0 ? '-' : paisa > 0 ? '+' : '';
  return `${sign}₹${Math.abs(Math.round(paisa / 100)).toLocaleString('en-IN')}`;
};

/** Every movement in the day carries a breakdown, so the per-face figures
 *  explain the WHOLE drawer rather than a slice of it. */
export function isComplete(l: FaceLedger): boolean {
  const c = l.coverage;
  return (
    l.read_ok &&
    l.opening_captured &&
    l.closing_captured &&
    c.cash_sale_legs === c.cash_sale_legs_counted &&
    c.refund_legs === c.refund_legs_counted &&
    c.payouts === c.payouts_counted
  );
}

function uncounted(l: FaceLedger): number {
  const c = l.coverage;
  return (
    c.cash_sale_legs - c.cash_sale_legs_counted +
    (c.refund_legs - c.refund_legs_counted) +
    (c.payouts - c.payouts_counted)
  );
}

export default function FaceTallyTable({ ledger }: { ledger: FaceLedger }) {
  const complete = isComplete(ledger);
  const off = ledger.rows.filter((r) => r.difference_pieces !== 0);

  return (
    <div className="space-y-2">
      {!complete && (
        <div className="rounded-lg px-3 py-2 bg-amber-50 border border-amber-200 text-amber-800 text-xs flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
          <span>
            Part of this day was not counted note by note
            {uncounted(ledger) > 0 && <> ({uncounted(ledger)} cash movements)</>}
            {!ledger.opening_captured && <>, and no opening float was counted</>}
            {!ledger.closing_captured && <>, and the drawer was not counted at close</>}
            . The rows below explain only the money that was counted &mdash; the rest
            cannot be traced to a denomination, so no note-by-note verdict is given.
          </span>
        </div>
      )}
      {ledger.coverage.flagged > 0 && (
        <div className="rounded-lg px-3 py-2 bg-amber-50 border border-amber-200 text-amber-800 text-xs flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
          <span>
            {ledger.coverage.flagged} count{ledger.coverage.flagged === 1 ? '' : 's'} did
            not add up to the amount recorded against {ledger.coverage.flagged === 1 ? 'it' : 'them'}.
            The amounts stand; the breakdowns are worth a look.
          </span>
        </div>
      )}

      {off.length === 0 ? (
        <p className="text-sm text-gray-600">
          {complete
            ? 'Every denomination tallies. No face is over or short.'
            : 'No difference at any counted denomination.'}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-gray-400 uppercase tracking-wide text-left">
                <th className="py-1 pr-3 font-medium">Denomination</th>
                <th className="py-1 px-3 font-medium text-right">Expected</th>
                <th className="py-1 px-3 font-medium text-right">Counted</th>
                <th className="py-1 px-3 font-medium text-right">Difference</th>
                <th className="py-1 pl-3 font-medium text-right">Value</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {off.map((r) => (
                <tr key={`${r.kind}-${r.face}`}>
                  <td className="py-1.5 pr-3 text-gray-700">
                    ₹{r.face} <span className="text-gray-400 text-xs uppercase">{r.kind}</span>
                  </td>
                  <td className="py-1.5 px-3 text-right tabular-nums text-gray-600">
                    {r.expected_pieces}
                  </td>
                  <td className="py-1.5 px-3 text-right tabular-nums text-gray-600">
                    {r.counted_pieces}
                  </td>
                  <td
                    className={
                      'py-1.5 px-3 text-right tabular-nums font-semibold ' +
                      (r.difference_pieces < 0 ? 'text-red-700' : 'text-amber-700')
                    }
                  >
                    {r.difference_pieces > 0 ? '+' : ''}
                    {r.difference_pieces}
                  </td>
                  <td className="py-1.5 pl-3 text-right tabular-nums text-gray-600">
                    {inr(r.difference_paisa)}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-gray-200">
                <td className="py-1.5 pr-3 text-gray-500 text-xs uppercase tracking-wide" colSpan={4}>
                  Net across the faces that moved
                </td>
                <td className="py-1.5 pl-3 text-right tabular-nums font-semibold text-gray-900">
                  {inr(ledger.difference_paisa)}
                </td>
              </tr>
            </tfoot>
          </table>
          {ledger.difference_paisa === 0 && (
            <p className="text-xs text-gray-500 mt-2">
              The drawer balances to the rupee, but the notes do not. Two errors
              cancelled each other out &mdash; the rows above are what actually happened.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
