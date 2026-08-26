// ============================================================================
// The per-face tally must never present a partial count as a complete answer
// ============================================================================
// This is the most seductive way this feature could go wrong: a plausible,
// authoritative-looking table computed over a day where half the movements were
// never counted note by note. It would read as "the drawer is two Rs 500 short"
// when the truth is "we have no idea where Rs 4,000 of it went". The verdict is
// withheld unless every part of the day carries a breakdown.

import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import FaceTallyTable, { isComplete, type FaceLedger } from '../FaceTallyTable';

const FULL_COVERAGE = {
  cash_sale_legs: 2,
  cash_sale_legs_counted: 2,
  refund_legs: 1,
  refund_legs_counted: 1,
  payouts: 1,
  payouts_counted: 1,
  flagged: 0,
};

function ledger(over: Partial<FaceLedger> = {}): FaceLedger {
  return {
    rows: [],
    coverage: { ...FULL_COVERAGE },
    read_ok: true,
    opening_captured: true,
    closing_captured: true,
    difference_paisa: 0,
    ...over,
  };
}

const SHORT_500 = {
  face: 500,
  kind: 'note' as const,
  expected_pieces: 14,
  counted_pieces: 12,
  difference_pieces: -2,
  difference_paisa: -100000,
};
const OVER_100 = {
  face: 100,
  kind: 'note' as const,
  expected_pieces: 6,
  counted_pieces: 16,
  difference_pieces: 10,
  difference_paisa: 100000,
};
const BALANCED_50 = {
  face: 50,
  kind: 'note' as const,
  expected_pieces: 4,
  counted_pieces: 4,
  difference_pieces: 0,
  difference_paisa: 0,
};

describe('a fully counted day', () => {
  it('names the face and the number of pieces, not just a rupee figure', () => {
    render(<FaceTallyTable ledger={ledger({ rows: [SHORT_500, BALANCED_50], difference_paisa: -100000 })} />);

    const row = screen.getByText('₹500').closest('tr')!;
    expect(row).toHaveTextContent('14');
    expect(row).toHaveTextContent('12');
    expect(row).toHaveTextContent('-2');
    expect(row).toHaveTextContent('-₹1,000');
    expect(screen.queryByText(/not counted note by note/i)).not.toBeInTheDocument();
  });

  it('lists ONLY the faces that are out of true', () => {
    render(<FaceTallyTable ledger={ledger({ rows: [SHORT_500, BALANCED_50, OVER_100] })} />);
    // ASSERT THE SET AND THE COUNT. The Rs 50 row tallied, so it is not noise
    // in a table a manager reads to find a problem.
    expect(screen.getAllByRole('row')).toHaveLength(1 + 2 + 1); // header + 2 + footer
    expect(screen.getByText('₹500')).toBeInTheDocument();
    expect(screen.getByText('₹100')).toBeInTheDocument();
    expect(screen.queryByText('₹50')).not.toBeInTheDocument();
  });

  it('shows the two mistakes that cancelled out on a drawer that balances', () => {
    // THE SINGLE BIGGEST GAIN. Rupee variance is zero; two real errors happened.
    render(<FaceTallyTable ledger={ledger({ rows: [SHORT_500, OVER_100], difference_paisa: 0 })} />);
    expect(screen.getByText('-2')).toBeInTheDocument();
    expect(screen.getByText('+10')).toBeInTheDocument();
    expect(screen.getByText(/balances to the rupee, but the notes do not/i)).toBeInTheDocument();
  });

  it('says so plainly when nothing is out of true', () => {
    render(<FaceTallyTable ledger={ledger()} />);
    expect(screen.getByText(/Every denomination tallies/i)).toBeInTheDocument();
  });
});

describe('a day that was only partly counted', () => {
  const PARTIAL: Array<[string, Partial<FaceLedger>]> = [
    ['a cash sale with no breakdown', { coverage: { ...FULL_COVERAGE, cash_sale_legs_counted: 1 } }],
    ['a refund with no breakdown', { coverage: { ...FULL_COVERAGE, refund_legs_counted: 0 } }],
    ['a payout with no breakdown', { coverage: { ...FULL_COVERAGE, payouts_counted: 0 } }],
    ['no opening float counted', { opening_captured: false }],
    ['the drawer not counted at close', { closing_captured: false }],
    ['a database read that failed', { read_ok: false }],
  ];

  it.each(PARTIAL)('withholds the verdict when there is %s', (_label, over) => {
    const l = ledger({ rows: [SHORT_500], ...over });
    expect(isComplete(l)).toBe(false);
    render(<FaceTallyTable ledger={l} />);
    // The table is still SHOWN -- it is real information about the money that
    // was counted -- but it never claims to explain the whole drawer.
    expect(screen.getByText(/not counted note by note/i)).toBeInTheDocument();
    expect(screen.getByText('₹500')).toBeInTheDocument();
  });

  it('does not claim everything tallies when nothing was counted', () => {
    render(<FaceTallyTable ledger={ledger({ closing_captured: false })} />);
    expect(screen.queryByText(/Every denomination tallies/i)).not.toBeInTheDocument();
    expect(screen.getByText(/No difference at any counted denomination/i)).toBeInTheDocument();
  });
});

describe('a breakdown that did not add up', () => {
  it('surfaces the flag and says the amount stands', () => {
    render(
      <FaceTallyTable
        ledger={ledger({ rows: [SHORT_500], coverage: { ...FULL_COVERAGE, flagged: 2 } })}
      />,
    );
    expect(screen.getByText(/2 counts did not add up/i)).toBeInTheDocument();
    expect(screen.getByText(/The amounts stand/i)).toBeInTheDocument();
  });
});
