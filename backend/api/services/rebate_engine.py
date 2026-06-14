"""Vendor volume-rebate engine -- pure, deterministic, integer paise.

Feature #18. Tiered earned-rebate computation over a vendor's accepted
purchase-invoice spend within a period. All money is integer paise. No I/O
here -- callers pass plain dicts; the router (vendor_rebates.py) wires DB + the
AP reduction (a credit note against the vendor) + the Tally JV intent.

Owner decision (binding, memory owner_decisions_2026_06_12_gates)
----------------------------------------------------------------
  * The EARNED rebate REDUCES VENDOR AP -- a credit note against the vendor (you
    owe them less). Tally: CREDIT the vendor ledger, DEBIT a
    "Rebates Receivable / Discount Received" head.
  * MANUAL post first (auto_post=false). NO TASKMASTER auto-posting in this PR.

Money convention
----------------
Everything is integer paise. The spend basis comes from accepted purchase
invoices (persisted as ``vendor_bills`` with ``doc_type == "PURCHASE_INVOICE"``)
whose money fields are stored in RUPEE floats; the router converts to paise once
at the boundary (``non_adapt.rupees_to_paise``) and passes paise in here.

A TIER is ``{min_spend_paise, rebate_pct?, rebate_flat_paise?, cap_paise?}``.
Exactly one of ``rebate_pct`` / ``rebate_flat_paise`` drives the earn; an
optional ``cap_paise`` clamps the result. Tiers must be a strictly-increasing
ladder on ``min_spend_paise`` (guarded -- a malformed / duplicate / decreasing
ladder RAISES so a misconfigured agreement can never silently mis-pay).
"""

from __future__ import annotations

from typing import Any, Optional


class RebateConfigError(ValueError):
    """A malformed tier ladder (non-monotonic, duplicate, or no earn rule)."""


# ---------------------------------------------------------------------------
# Coercion helpers (defensive -- a garbage field reads as 0, never raises)
# ---------------------------------------------------------------------------


def _int_paise(v: Any) -> int:
    """Coerce to a non-negative integer paise value. Junk -> 0."""
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def _parse_date(s: Any):
    """Tolerant ISO parse to a date. None on junk."""
    from datetime import datetime, date

    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    if not s or not isinstance(s, str):
        return None
    txt = s.strip()
    if not txt:
        return None
    try:
        return date.fromisoformat(txt[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 1. Period spend (pure)
# ---------------------------------------------------------------------------


# A bill counts toward rebate spend unless it is voided/cancelled/reversed.
_EXCLUDED_BILL_STATUSES = {"VOID", "VOIDED", "CANCELLED", "CANCELED", "REVERSED", "DELETED"}


def compute_period_spend(invoices, vendor_id, period_start, period_end) -> int:
    """Sum eligible accepted purchase-invoice spend (integer paise) for one
    vendor over the half-open window [period_start, period_end).

    Pure. ``invoices`` is a list of dicts each carrying:
      - vendor_id
      - bill_date / invoice_date (ISO 'YYYY-MM-DD')
      - taxable_amount_paise  (the pre-tax spend basis, already in paise)
      - status (optional; voided/cancelled bills are excluded)
      - doc_type (optional; only PURCHASE_INVOICE counts when present)

    The window is HALF-OPEN: a bill dated exactly on ``period_end`` belongs to
    the NEXT period, never double-counted.
    """
    start = _parse_date(period_start)
    end = _parse_date(period_end)
    if start is None or end is None or start > end:
        return 0

    total = 0
    for inv in invoices or []:
        if not isinstance(inv, dict):
            continue
        if inv.get("vendor_id") != vendor_id:
            continue
        # Only accepted purchase invoices form the rebate basis. A row that
        # declares a doc_type must be a PURCHASE_INVOICE; a legacy row with no
        # doc_type is accepted (the caller already scoped the query to bills).
        doc_type = inv.get("doc_type")
        if doc_type is not None and doc_type != "PURCHASE_INVOICE":
            continue
        status = str(inv.get("status") or "").strip().upper()
        if status in _EXCLUDED_BILL_STATUSES:
            continue
        bdate = _parse_date(inv.get("bill_date") or inv.get("invoice_date"))
        if bdate is None:
            continue
        # half-open [start, end)
        if bdate < start or bdate >= end:
            continue
        total += _int_paise(inv.get("taxable_amount_paise"))
    return total


# ---------------------------------------------------------------------------
# 2. Tier resolution (pure) + monotonicity guard
# ---------------------------------------------------------------------------


def _validate_tiers(tiers) -> list:
    """Return tiers sorted ascending by min_spend_paise after asserting the
    ladder is well-formed. Raises RebateConfigError on a malformed ladder.

    A valid ladder:
      * is a non-empty list of dicts
      * every min_spend_paise is a non-negative integer
      * min_spend_paise values are STRICTLY INCREASING (no duplicates, no
        decrease) -- otherwise tier resolution would be ambiguous
      * each tier carries exactly one earn rule: rebate_pct OR rebate_flat_paise
    """
    if not isinstance(tiers, (list, tuple)) or not tiers:
        raise RebateConfigError("tiers must be a non-empty list")

    norm = []
    for t in tiers:
        if not isinstance(t, dict):
            raise RebateConfigError("each tier must be a dict")
        if "min_spend_paise" not in t:
            raise RebateConfigError("tier missing min_spend_paise")
        try:
            ms = int(t["min_spend_paise"])
        except (TypeError, ValueError) as exc:
            raise RebateConfigError("min_spend_paise must be an integer") from exc
        if ms < 0:
            raise RebateConfigError("min_spend_paise must be >= 0")
        has_pct = t.get("rebate_pct") is not None
        has_flat = t.get("rebate_flat_paise") is not None
        if has_pct == has_flat:
            raise RebateConfigError(
                "each tier needs exactly one of rebate_pct / rebate_flat_paise"
            )
        if has_pct:
            try:
                pct = float(t["rebate_pct"])
            except (TypeError, ValueError) as exc:
                raise RebateConfigError("rebate_pct must be numeric") from exc
            if pct < 0 or pct > 100:
                raise RebateConfigError("rebate_pct must be within 0..100")
        norm.append((ms, t))

    norm.sort(key=lambda x: x[0])
    prev = None
    for ms, _t in norm:
        if prev is not None and ms <= prev:
            # duplicate or decreasing min_spend -> ambiguous ladder
            raise RebateConfigError(
                "tier min_spend_paise must be strictly increasing (no duplicates)"
            )
        prev = ms
    return [t for _ms, t in norm]


def resolve_tier(spend_paise, tiers) -> Optional[dict]:
    """Return the HIGHEST tier whose min_spend_paise <= spend_paise, or None if
    no tier clears. Raises RebateConfigError on a malformed ladder."""
    ordered = _validate_tiers(tiers)
    spend = _int_paise(spend_paise)
    chosen = None
    for t in ordered:  # ascending -> last match is the highest cleared tier
        if int(t["min_spend_paise"]) <= spend:
            chosen = t
        else:
            break
    return chosen


# ---------------------------------------------------------------------------
# 3. Rebate amount (pure, paise-exact)
# ---------------------------------------------------------------------------


def _round_half_up(numerator: int, denominator: int) -> int:
    """Integer round-half-up of numerator/denominator (both positive)."""
    if denominator <= 0:
        return 0
    return (numerator * 2 + denominator) // (2 * denominator)


def compute_rebate_paise(spend_paise, tier) -> int:
    """Paise-exact rebate for the resolved tier.

      * percentage:  round-half-up(spend_paise * pct / 100)  (integer paise)
      * flat:        the flat paise amount
      * optional cap_paise clamps the result: min(rebate, cap)
      * returns 0 if tier is None / falsy
    """
    if not tier or not isinstance(tier, dict):
        return 0
    spend = _int_paise(spend_paise)

    if tier.get("rebate_pct") is not None:
        try:
            pct = float(tier["rebate_pct"])
        except (TypeError, ValueError):
            return 0
        if pct <= 0 or spend <= 0:
            rebate = 0
        else:
            # spend(paise) * pct / 100, half-up, with all-integer arithmetic to
            # avoid binary-float drift: numerator = spend * pct_scaled.
            # pct may carry up to 2 dp (e.g. 1.25%); scale by 100.
            pct_scaled = int(round(pct * 100))  # 1.25% -> 125
            rebate = _round_half_up(spend * pct_scaled, 10000)  # /100 (pct) /100 (scale)
    else:
        rebate = _int_paise(tier.get("rebate_flat_paise"))

    cap = tier.get("cap_paise")
    if cap is not None:
        rebate = min(rebate, _int_paise(cap))
    return rebate if rebate > 0 else 0


# ---------------------------------------------------------------------------
# 4. One-call convenience (still pure) -- compute the full earn for a period
# ---------------------------------------------------------------------------


def compute_earn(invoices, vendor_id, period_start, period_end, tiers) -> dict:
    """Compute {spend_paise, tier, rebate_paise} for a vendor + period + ladder.

    Pure. Returns the resolved tier dict (or None) and the paise-exact rebate.
    Raises RebateConfigError on a malformed ladder (so a misconfigured agreement
    fails loudly at preview/post time rather than mis-paying)."""
    spend = compute_period_spend(invoices, vendor_id, period_start, period_end)
    tier = resolve_tier(spend, tiers)
    rebate = compute_rebate_paise(spend, tier)
    return {"spend_paise": spend, "tier": tier, "rebate_paise": rebate}
