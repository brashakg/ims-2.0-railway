"""
IMS 2.0 - E5 Tender routing (pure mapper, NO DB)
================================================
Canonicalize every ``order.payments[]`` row to a known tender, map it to its
correct Tally ledger, split a day's tenders by mode (paise-exact), and emit the
receipt/bank legs a sales day-voucher needs so the JV BALANCES to zero paise.

This module is PURE: no DB, no I/O, no globals mutated. It READS the existing
payment rows captured by POS -- it never re-captures and never edits a capture
field. The DB-facing layer (``tender_reconciliation.py``) calls these helpers.

The bug E5 fixes: an unknown / blank ``method`` used to behave as CASH (cash
sales over-counted, drawer variance wrong) and instruments other than cash were
booked to a single Cash ledger in the Tally JV (a 60% UPI / 40% CARD sale never
hit the bank ledgers). Here an unknown method maps to ``UNKNOWN -> Suspense
A/c`` (NEVER cash), and non-cash-in instruments (GIFT_VOUCHER, LOYALTY, CREDIT,
STORE_CREDIT) route to liability / receivable ledgers, never a bank ledger.

No emoji (Windows cp1252).
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

# ---------------------------------------------------------------------------
# Canonical tenders + default ledger routing
# ---------------------------------------------------------------------------

# Canonical tender names. These mirror orders.PaymentMethod EXACTLY for the
# wired tenders, plus STORE_CREDIT (forward-compat only -- see CORRECTIONS:
# STORE_CREDIT is in the default map but is NOT a PaymentMethod value today and
# is NOT wired at capture) and UNKNOWN (the safe sink for a blank/garbage row).
CANONICAL_TENDERS = (
    "CASH",
    "UPI",
    "CARD",
    "BANK_TRANSFER",
    "EMI",
    "CREDIT",
    "GIFT_VOUCHER",
    "LOYALTY",
    "STORE_CREDIT",
    "UNKNOWN",
)

# Common legacy / alias spellings -> canonical. The keys are upper-cased before
# lookup. Anything not here AND not a canonical tender -> UNKNOWN.
_TENDER_ALIASES: Dict[str, str] = {
    "CASH": "CASH",
    "UPI": "UPI",
    "GPAY": "UPI",
    "PHONEPE": "UPI",
    "PAYTM": "UPI",
    "QR": "UPI",
    "CARD": "CARD",
    "DEBIT": "CARD",
    "DEBIT_CARD": "CARD",
    "CREDIT_CARD": "CARD",
    "EDC": "CARD",
    "POS": "CARD",
    "BANK_TRANSFER": "BANK_TRANSFER",
    "BANK": "BANK_TRANSFER",
    "NEFT": "BANK_TRANSFER",
    "RTGS": "BANK_TRANSFER",
    "IMPS": "BANK_TRANSFER",
    "EMI": "EMI",
    "CREDIT": "CREDIT",
    "PAY_LATER": "CREDIT",
    "GIFT_VOUCHER": "GIFT_VOUCHER",
    "VOUCHER": "GIFT_VOUCHER",
    "GIFT_CARD": "GIFT_VOUCHER",
    "LOYALTY": "LOYALTY",
    "POINTS": "LOYALTY",
    "STORE_CREDIT": "STORE_CREDIT",
}

# Tally ledger names per canonical tender. Owner overrides these per scope via
# the tender_ledger_map settings doc; these are the code-resident defaults.
# Non-cash-in instruments route to liability / receivable ledgers (NOT a bank
# ledger): GIFT_VOUCHER + LOYALTY + STORE_CREDIT are liabilities the store owes,
# CREDIT + EMI are receivables. UNKNOWN parks on a Suspense A/c for a human to
# reclassify -- it is NEVER silently folded into Cash.
IMS_DEFAULT_LEDGERS: Dict[str, str] = {
    "CASH": "Cash A/c",
    "UPI": "Bank A/c - UPI",
    "CARD": "Bank A/c - Card EDC",
    "BANK_TRANSFER": "Bank A/c",
    "GIFT_VOUCHER": "Gift Voucher Liability",
    "LOYALTY": "Loyalty Points Liability",
    "EMI": "EMI Finance Receivable",
    "CREDIT": "Sundry Debtors",
    "STORE_CREDIT": "Customer Store Credit Liability",
    "UNKNOWN": "Suspense A/c",
}

# Tenders that are real cash-in to a bank/cash account (vs a liability/receivable
# that does not increase the store's bank balance). Used by reporting/JV to tell
# a "money received" leg from a "promise / liability draw-down" leg.
CASH_IN_TENDERS = frozenset({"CASH", "UPI", "CARD", "BANK_TRANSFER"})


def _round2(x: float) -> float:
    """Paise-exact round to 2 dp. Round ONLY at a boundary (per packet)."""
    return round(float(x or 0), 2)


def canonicalize_tender(method: Optional[str], mode: Optional[str] = None) -> str:
    """Normalize a payment row's ``method`` (``mode`` is a tolerated legacy alias
    for the same field) to a canonical tender name.

    A blank / None / unrecognized value maps to ``"UNKNOWN"`` -- NEVER ``"CASH"``
    (the latter was the silent over-count bug). ``method`` wins over ``mode``
    when both are present.
    """
    raw = method if (method not in (None, "")) else mode
    key = str(raw or "").strip().upper()
    if not key:
        return "UNKNOWN"
    if key in CANONICAL_TENDERS:
        return key
    return _TENDER_ALIASES.get(key, "UNKNOWN")


def resolve_ledger(
    tender: str,
    tender_map: Optional[Dict[str, str]] = None,
    *,
    is_refund: bool = False,
) -> str:
    """Map a CANONICAL tender to its Tally ledger name.

    Resolution: ``tender_map[tender]`` (the E2-resolved override) falls back to
    ``IMS_DEFAULT_LEDGERS[tender]``, finally to the Suspense ledger for a tender
    not present in either map. A refund posts to the SAME ledger as the original
    capture (a negative leg, NEVER a separate reversal ledger) -- so
    ``is_refund`` does not change the ledger; it is accepted for call-site
    clarity and forward-compat.
    """
    _ = is_refund  # refunds contra the same ledger (negative leg); no remap.
    canon = tender if tender in CANONICAL_TENDERS else canonicalize_tender(tender)
    tender_map = tender_map or {}
    ledger = tender_map.get(canon)
    if ledger:
        return ledger
    return IMS_DEFAULT_LEDGERS.get(canon, IMS_DEFAULT_LEDGERS["UNKNOWN"])


def split_payments_by_mode(payments: Optional[Iterable[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """Aggregate ``order.payments[]`` rows by canonical tender.

    Returns ``{tender: {collected, refunded, net, count}}``. The sign split is
    the same as finance._cash_sales_for_window: a positive amount is collected,
    a negative amount is a refund (returned as a positive magnitude). ``net`` =
    collected - refunded. All money is paise-exact (rounded only at the boundary
    when read out).

    The by-mode net over an order's payments sums to the order's paid total
    (acceptance test #3) -- it reads the SAME amounts add_payment used.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for p in payments or []:
        tender = canonicalize_tender(p.get("method"), p.get("mode"))
        try:
            amt = float(p.get("amount", 0) or 0)
        except (TypeError, ValueError):
            amt = 0.0
        row = out.setdefault(
            tender, {"collected": 0.0, "refunded": 0.0, "net": 0.0, "count": 0}
        )
        if amt >= 0:
            row["collected"] += amt
        else:
            row["refunded"] += -amt
        row["net"] += amt
        row["count"] += 1
    # Round at the boundary so intermediate float noise never leaks out.
    for row in out.values():
        row["collected"] = _round2(row["collected"])
        row["refunded"] = _round2(row["refunded"])
        row["net"] = _round2(row["net"])
    return out


def build_tender_jv_legs(
    payments: Optional[Iterable[Dict[str, Any]]],
    tender_map: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Produce the receipt/bank ledger legs for a set of payment rows.

    One leg per canonical tender with a non-zero net. Each leg carries the
    resolved ledger name + the net amount (positive = money received against
    that ledger; a net-negative tender, i.e. a refund-dominant day, posts a
    negative amount to the SAME ledger -- never a separate reversal ledger).

    This is what the Tally day-voucher builder calls so a 60% UPI / 40% CARD
    sale hits ``Bank A/c - UPI`` + ``Bank A/c - Card EDC`` with ZERO on
    ``Cash A/c`` (acceptance test #1), and an UNKNOWN row surfaces on
    ``Suspense A/c`` instead of being folded into cash (test #2).
    """
    by_mode = split_payments_by_mode(payments)
    legs: List[Dict[str, Any]] = []
    for tender, agg in by_mode.items():
        net = _round2(agg.get("net", 0))
        if net == 0:
            continue
        legs.append(
            {
                "tender": tender,
                "ledger": resolve_ledger(tender, tender_map),
                "amount": net,
                "is_cash_in": tender in CASH_IN_TENDERS,
            }
        )
    # Stable order so the emitted XML / report is deterministic across runs.
    legs.sort(key=lambda leg: leg["tender"])
    return legs


def assert_voucher_balanced(legs: Optional[Iterable[Dict[str, Any]]], *, tolerance: float = 0.0) -> None:
    """Raise ``ValueError`` unless the signed ledger legs net to zero paise.

    A Tally voucher must balance (debits == credits) or Tally rejects it on
    import. Precedent: finance._jv_cgst_sgst_split keeps the GST split exact so
    the voucher balances; this is the explicit balance gate before any emit.

    Each leg must carry a signed ``amount`` (DEBITs positive, CREDITs negative,
    or whatever convention the caller chose -- the only contract is the SIGNED
    sum is ~0). ``tolerance`` defaults to 0.0 (paise-exact); a caller may allow
    a half-paise rounding slack if it builds legs from independently-rounded
    sources.
    """
    total = 0.0
    for leg in legs or []:
        try:
            total += float(leg.get("amount", 0) or 0)
        except (TypeError, ValueError):
            total += 0.0
    if abs(round(total, 2)) > (tolerance + 1e-9):
        raise ValueError(
            f"Tally voucher does not balance: signed legs net to {round(total, 2)} "
            f"(tolerance {tolerance}); debits must equal credits"
        )
