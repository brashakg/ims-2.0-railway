// ============================================================================
// IMS 2.0 - "the expected-cash figure leaves something out"
// ============================================================================
// NOT a redaction notice. Read the difference before editing this sentence.
//
// RestrictedTotalsNotice says "your role is not shown some costs". THIS says
// something else entirely: salaries, staff advances and PF/ESI are never paid
// out of a shop till (owner ruling 2026-08-14), so finance.py deliberately
// leaves them OUT of `cash_expenses` and out of `expected`. That exclusion is
// identical for every role including ADMIN -- the drawer figure is now CORRECT,
// not shortened, and this notice must never imply otherwise.
//
// It exists because a number a human counts money against must never be
// adjusted behind their back. If a manager books a pay head as an expense and
// then sees the drawer not move, the screen has to say why, or they will
// "correct" a count that was right.
//
// NO AMOUNT, NO HEAD NAME. The backend's OFF_TILL_EXPENSE_MESSAGE holds the
// same line for the live preview; this constant is the FRONTEND's single copy,
// used for the reconciliation history rows (whose API carries only the boolean)
// and as the fallback if a preview ever arrives flagged but without its text --
// an empty amber box would be worse than no box at all.

export const OFF_TILL_EXPENSE_NOTICE =
  'One or more expenses booked here in this period are not paid out of the ' +
  'shop till, so they are left out of the expected-cash figure. If your ' +
  'count does not tally, check with an administrator before adjusting ' +
  'anything.';
