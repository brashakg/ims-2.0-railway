// ============================================================================
// IMS 2.0 - "this total leaves something out"
// ============================================================================
// The backend already knows when it has withheld an expense head from a reader
// below ADMIN: /finance/pnl sets `expenses_partially_restricted` and
// /finance/budget sets `categories_partially_restricted`, with the comment
// "Tell the reader their panel is incomplete rather than letting a short total
// read as the truth".
//
// Until now nothing on the screen said so. The Finance dashboard rendered the
// shortened operating-expense figure as plain "Operating Expenses" and the
// shortened budget table as the budget - a screen stating something the system
// knows is not true, which is the same class of defect PR #960 was written to
// kill (four false "coming online" screens).
//
// WHAT IT MAY AND MAY NOT SAY. It must let a store manager tell AT A GLANCE
// that the figure excludes something. It must NOT name the withheld head or
// hint at its size: on a 1-5 person store, "salaries: about a lakh" IS an
// individual's pay packet, which is the whole point of the owner's 2026-08-09
// ruling. So: no head name, no amount, no count, and a clear next step.
//
// Rendered ONLY when the flag is true. A banner that is always on is exactly
// as useless as one that never appears - the frontend test asserts both
// directions for that reason.

interface Props {
  /** The backend's own flag. Undefined / false renders nothing at all. */
  show?: boolean;
  /** Optional extra sentence naming the panel, e.g. "budget". */
  scope?: string;
  className?: string;
}

export default function RestrictedTotalsNotice({ show, scope, className }: Props) {
  if (!show) return null;
  return (
    <div
      role="status"
      data-testid="restricted-totals-notice"
      className={`rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 ${className || ''}`}
    >
      Some costs are not shown to your role, and the {scope || 'totals'} on this
      screen leave them out too - so this is not the full operating cost. Ask an
      administrator for the complete picture.
    </div>
  );
}
