"""
IMS 2.0 - Cash register / EOD reconciliation (pure money math)
==============================================================
Denomination counting + expected-vs-counted variance for the cash drawer.

This module is INTENTIONALLY pure: no Mongo, no FastAPI. The router
(backend/api/routers/finance.py) owns persistence + store scoping and calls
these helpers. That keeps the money math unit-testable without a DB.

Indian currency in circulation (RBI):
  Notes: Rs 500 / 200 / 100 / 50 / 20 / 10   (no Rs 2000 -- withdrawn)
  Coins: Rs 10 / 5 / 2 / 1
A Rs 10 note and a Rs 10 coin share the same face value, so a denomination
line carries a `kind` ("note" | "coin") to disambiguate for the count sheet.

Expected cash at close:
    expected = opening_float
             + cash_sales        (POS CASH tenders for the session window)
             - cash_refunds      (negative CASH tenders / refunds)
             - cash_expenses      (CASH payouts from the drawer)
             - bank_deposit       (cash physically removed and banked)
Variance = counted - expected.  Positive = OVER (excess), negative = SHORT.

No money values are ever rounded away: amounts are rounded to 2 dp only at
the boundary so paise noise from float sums doesn't accumulate.
"""

from __future__ import annotations

from typing import Iterable, Optional

from . import cash_denominations as denom

# The ladder, the row shape and the arithmetic all live in ONE module
# (services/cash_denominations.py). What remains here is a thin RUPEE-facing
# adapter: historical `cash_register_sessions` documents (and the manager
# console that reads them) carry a rupee `line_total` key, so this door keeps
# emitting exactly that shape. No second ladder, no second normaliser.
NOTE_FACES = denom.NOTE_FACES
COIN_FACES = denom.COIN_FACES


def denomination_ladder() -> list[dict]:
    """The blank denomination grid the UI starts from (pieces all zero)."""
    return denom.denomination_ladder()


def normalize_denominations(rows: Optional[Iterable[dict]]) -> list[dict]:
    """Clean a list of {face, kind, pieces} dicts: drop bad faces, clamp
    pieces to non-negative ints, default kind to 'note', and attach the
    computed line total (face * pieces, in RUPEES -- the legacy stored shape).
    Order is preserved as supplied so the stored doc mirrors what the cashier
    entered."""
    return [
        {
            "face": r["face"],
            "kind": r["kind"],
            "pieces": r["pieces"],
            "line_total": r["line_total_paisa"] // 100,
        }
        for r in denom.normalize_rows(rows)
    ]


def total_from_denominations(rows: Optional[Iterable[dict]]) -> float:
    """Sum of face * pieces across denomination rows, in RUPEES. Pure."""
    return float(denom.total_paisa(rows) // 100)


def compute_expected_cash(
    opening_float: float,
    cash_sales: float,
    cash_refunds: float = 0.0,
    cash_expenses: float = 0.0,
    bank_deposit: float = 0.0,
) -> float:
    """expected = opening + sales - refunds - expenses - bank_deposit.

    All inputs are coerced to float (None/junk -> 0). Returns a 2-dp value."""

    def f(x) -> float:
        try:
            return float(x or 0)
        except (TypeError, ValueError):
            return 0.0

    expected = (
        f(opening_float)
        + f(cash_sales)
        - f(cash_refunds)
        - f(cash_expenses)
        - f(bank_deposit)
    )
    return round(expected, 2)


def compute_variance(counted: float, expected: float) -> float:
    """counted - expected. Positive = drawer OVER, negative = SHORT."""

    def f(x) -> float:
        try:
            return float(x or 0)
        except (TypeError, ValueError):
            return 0.0

    return round(f(counted) - f(expected), 2)


def variance_status(variance: float, tolerance: float = 0.0) -> str:
    """Classify a variance against a tolerance band (absolute rupees).

    Returns one of: "BALANCED" (|variance| within tolerance), "OVER" (excess
    cash beyond tolerance), "SHORT" (missing cash beyond tolerance)."""
    try:
        v = float(variance or 0)
        tol = abs(float(tolerance or 0))
    except (TypeError, ValueError):
        return "BALANCED"
    if abs(v) <= tol:
        return "BALANCED"
    return "OVER" if v > 0 else "SHORT"


# Advisory raised when the arithmetic says the drawer should hold LESS THAN
# NOTHING. That is never a real expectation -- it means cash left the drawer
# that it never took in (e.g. a refund funded from the safe, which IMS has no
# cash-in concept for). Presenting the resulting "overage" as a verdict would
# credit the cashier with money they never held, so the verdict is suppressed
# and this advisory is surfaced instead. The number itself is NEVER clamped or
# hidden -- a real figure is always shown.
# The withheld verdict for a drawer NOBODY COUNTED. There is no variance to
# report against a count that was never taken, and the sum of a blank grid
# (Rs 0.00) is not a count -- reporting it as one accused a manager who skipped
# the count of emptying the till. Absence is a state, here as everywhere else
# in the count block.
NOT_COUNTED = "NOT_COUNTED"
NOT_COUNTED_MESSAGE = (
    "This drawer was closed without a count, so it is recorded as never "
    "counted - not as an empty drawer. There is no variance to report."
)

NEGATIVE_EXPECTED = "NEGATIVE_EXPECTED"
NEGATIVE_EXPECTED_MESSAGE = (
    "More cash was refunded than this drawer took in - a cash-in is missing "
    "(e.g. a refund funded from the safe). Record the cash-in before trusting "
    "this variance."
)


def build_close_summary(
    opening_float: float,
    cash_sales: float,
    cash_refunds: float,
    cash_expenses: float,
    bank_deposit: float,
    denominations: Optional[Iterable[dict]],
    tolerance: float = 0.0,
) -> dict:
    """One-shot reconciliation block for the close endpoint + the Z-report.

    Computes counted (from denominations), expected, variance, and a
    tolerance-aware status. Pure -- the router stamps identity/time.

    When `expected` is NEGATIVE the variance verdict is suppressed
    (variance_status = NEGATIVE_EXPECTED) rather than reporting a phantom
    OVERAGE: see NEGATIVE_EXPECTED_MESSAGE."""
    norm = normalize_denominations(denominations)
    counted = float(sum(r["line_total"] for r in norm))
    expected = compute_expected_cash(
        opening_float, cash_sales, cash_refunds, cash_expenses, bank_deposit
    )
    variance = compute_variance(counted, expected)
    negative_expected = expected < 0
    return {
        "opening_float": round(float(opening_float or 0), 2),
        "cash_sales": round(float(cash_sales or 0), 2),
        "cash_refunds": round(float(cash_refunds or 0), 2),
        "cash_expenses": round(float(cash_expenses or 0), 2),
        "bank_deposit": round(float(bank_deposit or 0), 2),
        "denominations": norm,
        "counted": round(counted, 2),
        "expected": expected,
        "variance": variance,
        "variance_status": (
            NEGATIVE_EXPECTED if negative_expected
            else variance_status(variance, tolerance)
        ),
        "negative_expected_advisory": negative_expected,
        "negative_expected_message": (
            NEGATIVE_EXPECTED_MESSAGE if negative_expected else None
        ),
        "tolerance": round(abs(float(tolerance or 0)), 2),
    }
