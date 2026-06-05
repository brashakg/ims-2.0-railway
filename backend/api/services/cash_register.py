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

# Canonical denomination ladder, highest first. (face, kind).
# Notes Rs 500..10, then coins Rs 10..1. The UI renders rows in this order.
NOTE_FACES = (500, 200, 100, 50, 20, 10)
COIN_FACES = (10, 5, 2, 1)


def denomination_ladder() -> list[dict]:
    """The blank denomination grid the UI starts from (pieces all zero)."""
    rows: list[dict] = []
    for face in NOTE_FACES:
        rows.append({"face": face, "kind": "note", "pieces": 0})
    for face in COIN_FACES:
        rows.append({"face": face, "kind": "coin", "pieces": 0})
    return rows


def _coerce_pieces(value) -> int:
    """A denomination piece count: a non-negative integer. Junk -> 0."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def _coerce_face(value) -> Optional[int]:
    try:
        f = int(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def normalize_denominations(rows: Optional[Iterable[dict]]) -> list[dict]:
    """Clean a list of {face, kind, pieces} dicts: drop bad faces, clamp
    pieces to non-negative ints, default kind to 'note', and attach the
    computed line total (face * pieces). Order is preserved as supplied so
    the stored doc mirrors what the cashier entered."""
    out: list[dict] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        face = _coerce_face(r.get("face"))
        if face is None:
            continue
        pieces = _coerce_pieces(r.get("pieces"))
        kind = str(r.get("kind") or "note").lower()
        if kind not in ("note", "coin"):
            kind = "note"
        out.append(
            {
                "face": face,
                "kind": kind,
                "pieces": pieces,
                "line_total": face * pieces,
            }
        )
    return out


def total_from_denominations(rows: Optional[Iterable[dict]]) -> float:
    """Sum of face * pieces across denomination rows. Pure."""
    total = 0
    for r in normalize_denominations(rows):
        total += r["line_total"]
    return float(total)


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
    tolerance-aware status. Pure -- the router stamps identity/time."""
    norm = normalize_denominations(denominations)
    counted = float(sum(r["line_total"] for r in norm))
    expected = compute_expected_cash(
        opening_float, cash_sales, cash_refunds, cash_expenses, bank_deposit
    )
    variance = compute_variance(counted, expected)
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
        "variance_status": variance_status(variance, tolerance),
        "tolerance": round(abs(float(tolerance or 0)), 2),
    }
