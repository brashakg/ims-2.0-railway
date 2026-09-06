"""Order number minting, the EMI schedule builder and the create response
shape.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.
"""

import uuid
from datetime import datetime


def generate_order_number(store_id: str) -> str:
    """Generate unique order number: ORD-BOK01-2026-A1B2C3

    Audit 2026-04-21 turned up a stray legacy order formatted as
    `BV-BV--2026-D639D3` — missing ORD- prefix and double dash. That's
    not something this function can produce, so it's either seed data
    or was minted before this helper existed. This tightened version:
      - Always emits ORD- prefix (no path can bypass it).
      - Strips the chain prefix (BV/WO) + merges the remaining segments
        without trailing dashes so pathological store_ids can't leak
        through as `ORD-BV--2026-...`.
      - Falls back to `IMS` when store_id is unusable so we never
        produce an empty prefix slot.
    """
    raw = (store_id or "").strip().upper()
    # Drop the chain prefix (BV / WO / BVO) if present so prefix focuses
    # on the store part. "BV-BOK-01" → ["BOK", "01"].
    parts = [p for p in raw.split("-") if p and p not in ("BV", "WO", "BVO")]
    if len(parts) >= 2:
        prefix = (parts[0] + parts[1])[:8]  # BOK01
    elif len(parts) == 1:
        prefix = parts[0][:8]
    else:
        prefix = "IMS"
    # Sanitize: alnum only, upper, non-empty.
    prefix = "".join(c for c in prefix if c.isalnum()) or "IMS"
    year = datetime.now().year
    short_uuid = str(uuid.uuid4())[:6].upper()
    return f"ORD-{prefix}-{year}-{short_uuid}"


# The EMI rate has ONE definition -- services/policy_registry.resolve_emi_annual_rate
# -- shared with the store-detail read the POS screen quotes from. An alias,
# not a copy: re-inlining it here is how the screen and the charge drift apart.
from ...services.policy_registry import resolve_emi_annual_rate as _emi_annual_rate


def build_emi_schedule(principal: float, annual_rate: float, months: int) -> dict:
    """Reconcile an EMI plan so the installments sum EXACTLY to total_payable.

    P3-C. The equal monthly installment (standard amortization formula) is
    rounded to paise for display, but `monthly_emi * months` then drifts from
    the true cost of credit by up to a few paise. A customer paying N equal
    rounded installments would under/over-pay the principal+interest.

    Fix: `total_payable` is the AUTHORITATIVE total (unrounded EMI x months,
    rounded once to paise). The schedule pays `monthly_emi` for the first
    (months - 1) installments and a `last_installment` that absorbs the
    rounding remainder, so:

        monthly_emi * (months - 1) + last_installment == total_payable   (exact)

    `interest_amount = total_payable - principal`. All values are display /
    documentation only -- the recorded payment amount (and therefore what
    reduces balance_due) is unchanged elsewhere.

    Returns the dict embedded as `emi_details` (minus provider, which the
    caller adds).
    """
    months = int(months)
    principal = float(principal)
    monthly_rate = annual_rate / 12 / 100
    if monthly_rate > 0:
        emi_amount = (
            principal
            * monthly_rate
            * (1 + monthly_rate) ** months
            / ((1 + monthly_rate) ** months - 1)
        )
    else:
        emi_amount = principal / months

    monthly_emi = round(emi_amount, 2)
    # Authoritative total cost of credit (rounded once from the exact EMI).
    total_payable = round(emi_amount * months, 2)
    # Last installment absorbs the accumulated rounding so the schedule sums
    # to total_payable to the paisa. round() tames float noise (e.g. a
    # 4999.999999999 -> 5000.00).
    last_installment = round(total_payable - monthly_emi * (months - 1), 2)
    interest_amount = round(total_payable - principal, 2)

    return {
        "tenure_months": months,
        "annual_rate": annual_rate,
        "monthly_emi": monthly_emi,
        "last_installment": last_installment,
        "total_payable": total_payable,
        "interest_amount": interest_amount,
    }


def _order_create_response(order: dict) -> dict:
    """The POST /orders success envelope. Shared by a fresh create and the C-5
    idempotency replay so a duplicated request gets a byte-identical response.
    Reads from a persisted order doc (snake_case)."""
    return {
        "order_id": order.get("order_id"),
        "order_number": order.get("order_number"),
        "status": order.get("status") or "DRAFT",
        "grand_total": order.get("grand_total"),
        "message": "Order created successfully",
    }
