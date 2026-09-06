"""Payment method + order-item/payment request models and line pricing
enforcement: discount stacking caps, MRP/floor guards, GST recompute.

Moved verbatim out of the 6,649-line api/routers/orders.py (Wave 5 package
split): no path, method, dependency, status code, response_model, default,
rounding or validation was changed.
"""

import math
from enum import Enum
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Any, Optional
from ...services import cash_denominations as cash_denom
from ._shared import logger
from .stock import _VIRTUAL_PID_PREFIXES


class PaymentMethod(str, Enum):
    CASH = "CASH"
    UPI = "UPI"
    CARD = "CARD"
    BANK_TRANSFER = "BANK_TRANSFER"
    EMI = "EMI"
    CREDIT = "CREDIT"
    GIFT_VOUCHER = "GIFT_VOUCHER"
    # Loyalty-points redemption recorded as an internal tender. The points are
    # already atomically debited by POST /loyalty/redeem BEFORE this payment is
    # recorded, so add_payment does NOT re-redeem -- it just records the rupee
    # value so it counts toward amount_paid (non-CREDIT) and reduces balance_due.
    # Previously absent from the enum: the POS LOYALTY tender 422'd and was
    # swallowed, so the customer's points were burned yet the order still showed
    # that amount as owing (a double charge).
    LOYALTY = "LOYALTY"
    # Store-credit redemption. The spend happens server-side in
    # add_payment via customers.redeem_store_credit_atomic (an atomic
    # guarded decrement plus a credit_note_ledger row, ref=order_id),
    # always against the ORDER's customer -- never a customer id taken
    # from the request body. Every refund in this app ISSUES store credit
    # and the till DISPLAYS the balance; until this member existed there
    # was no way to spend it, so the liability had no discharge door.
    STORE_CREDIT = "STORE_CREDIT"


class OrderItemCreate(BaseModel):
    item_type: str  # FRAME, LENS, CONTACT_LENS, ACCESSORY, SERVICE
    product_id: str
    # POS-9: length cap on product_name — receipts/Tally/reports truncate or
    # 500 on very long names; 200 chars covers the widest real product name.
    product_name: Optional[str] = Field(None, max_length=200)
    sku: Optional[str] = None
    brand: Optional[str] = None
    subbrand: Optional[str] = None
    category: Optional[str] = None
    # C-3: GENEROUS upper bounds that can never reject a real optical order but
    # stop a malicious/garbage payload (e.g. unit_price=1.7e308 or quantity=1e9)
    # from overflowing unit_price*quantity to Infinity and 500ing on JSON
    # serialisation. 1 crore per unit / 1000 units is far above any real line.
    quantity: int = Field(default=1, ge=1, le=1000)
    unit_price: float = Field(..., ge=0, le=10_000_000)
    discount_percent: float = Field(default=0, ge=0, le=100)
    # C-4 (DELTA 2): per-line discount accountability. The POS already sends
    # these (posStore CartLineItem.discount_approved_by / discount_reason);
    # they were silently dropped before. Captured here so a 100%-line-discount
    # order can carry its own approver + reason for the required-approval gate.
    discount_approved_by: Optional[str] = None
    # POS-9: cap discount_reason at 200 chars (free text, shown on audit rows).
    discount_reason: Optional[str] = Field(None, max_length=200)
    prescription_id: Optional[str] = None
    lens_options: Optional[dict] = None  # coating, tint, etc.
    lens_details: Optional[dict] = None  # type, material, coatings
    # POS-10: per-line staff note (e.g. "wrap bifocal, tight frame").
    # Persisted on the order item so workshop + invoice can display it.
    # POS-9: capped at 200 chars to protect receipts/Tally from runaway text.
    item_note: Optional[str] = Field(None, max_length=200)
    # Optional explicit serialized stock unit (when the POS knows which unit
    # is being sold, e.g. barcode-scan flow). Used by _mark_units_sold to flip
    # the unit to SOLD with the order_id so a future return can re-activate
    # exactly the unit that left. Absent -> FIFO allocation by product+store.
    stock_id: Optional[str] = None
    # Lens catalog cell coordinates (Branch B', sub-PR 4). Set by the FE
    # when a LENS item is configured via the Power Grid. The lens_stock_hook
    # uses these to atomically reserve the cell at POS Step 6; missing values
    # mean the line is a legacy / free-text lens entry and the hook no-ops.
    lens_line_id: Optional[str] = None
    sph: Optional[float] = None
    cyl: Optional[float] = None
    add: Optional[float] = None
    # BUG-005: cylinder axis (whole degree 1-180). Required when cyl is non-zero
    # (a toric lens is un-grindable without an axis). Optional so a sphere-only /
    # frame-only line is unaffected. Enforced by _validate_order_line_rx, not a
    # field_validator, because the cyl-requires-axis rule is cross-field.
    axis: Optional[int] = None

    @field_validator("unit_price")
    @classmethod
    def _unit_price_finite(cls, v: float) -> float:
        # C-3: explicitly reject NaN / +-Infinity so a non-finite price can
        # never reach the money math (the le/ge bounds already catch these,
        # but this is the contract the bound enforces -- belt and braces).
        if not math.isfinite(v):
            raise ValueError("unit_price must be a finite number")
        return v


class PaymentCreate(BaseModel):
    # Accept both `method` (canonical) and `mode` (legacy, still used by
    # the Orders-page Collect Payment modal). pydantic aliasing means the
    # request can send either — we canonicalize on the model.
    method: PaymentMethod = Field(..., validation_alias="method")
    # BUG-108 residual: gt=0 already rejects NaN/<=0; the le bound additionally
    # rejects +Infinity so a payment can't store inf on the order doc.
    amount: float = Field(..., gt=0, le=100_000_000)
    reference: Optional[str] = None
    # Gift-voucher code (only used when method=GIFT_VOUCHER). The POS sends
    # both `reference` and `voucher_code` set to the card code; we prefer
    # this explicit field and fall back to `reference` for older callers.
    voucher_code: Optional[str] = None
    # EMI-specific fields (only required when method=EMI)
    emi_months: Optional[int] = Field(None, ge=3, le=24)
    emi_provider: Optional[str] = None  # e.g., "BAJAJ", "HDFC", "ICICI"
    # POS-2: the loan principal (order_total - down_payment). When provided,
    # build_emi_schedule uses this amount so the schedule reflects the full
    # financed balance, not just the down-payment recorded in `amount`.
    emi_principal: Optional[float] = Field(None, gt=0, le=100_000_000)
    # ---- Denominated cash accountability (CASH legs only) -------------------
    # Which notes and coins the customer HANDED OVER, and which were HANDED
    # BACK as change. Both are OPTIONAL and are attached records: nothing here
    # can change `amount`, the change arithmetic, amount_paid, balance_due,
    # payment_status or a single GST figure. Omit them entirely and the sale
    # completes exactly as it always has, with the breakdown recorded as
    # NOT_CAPTURED (never as a zero count).
    #
    # The scalars are ANCHORED TO THIS LEG, never to the bill: on a
    # UPI Rs 1,000 + CASH Rs 850 split, tendered_amount is measured against the
    # Rs 850 cash leg. tendered - change should equal `amount`; when it does
    # not, the row is flagged for a human and the amount is left alone.
    cash_tendered: Optional[cash_denom.CashCountInput] = None
    cash_change: Optional[cash_denom.CashCountInput] = None
    #
    # ACCEPTED LOOSELY ON PURPOSE. These two ride alongside `amount` as part of
    # the attached record, so a junk, negative or absurd figure is COERCED and
    # FLAGGED (cash_leg_balanced False), never a 422. A rejected payment POST is
    # swallowed by POSLayout's bare catch and leaves the ORDER SAVED WITH NO
    # PAYMENT ROW -- a count sheet must never be able to do that to a sale.
    tendered_amount: Any = None
    change_amount: Any = None

    model_config = ConfigDict(populate_by_name=True)

    def __init__(self, **data):
        # pydantic 2 validation_alias is restrictive; accept "mode" as a
        # fallback if "method" isn't present. Normalises legacy callers.
        if "method" not in data and "mode" in data:
            data["method"] = data["mode"]
        super().__init__(**data)


def effective_line_discount_pct(
    discount_percent: float, unit_price: float, mrp: float
) -> float:
    """How much a line is REALLY discounted, in percent of MRP.

    An explicit ``discount_percent`` is not the whole story: a line can also
    arrive already marked down, with unit_price below MRP and no percent set.
    The cap has to see the larger of the two or it is trivially side-stepped by
    editing the price instead of the percent.

    ONE implementation, used by the per-line cap AND by the stacking cap below.
    Two copies of this is how the two checks would start disagreeing about what
    "discounted" means.
    """
    try:
        pct = float(discount_percent or 0.0)
    except (TypeError, ValueError):
        pct = 0.0
    try:
        up = float(unit_price or 0.0)
        m = float(mrp or 0.0)
    except (TypeError, ValueError):
        return pct
    if m > 0 and up < m - 1e-6:
        pct = max(pct, (m - up) / m * 100.0)
    return pct


def combined_discount_pct(line_pct: float, cart_pct: float) -> float:
    """Total discount a line actually carries once the BILL discount lands too.

    The bill discount applies on top of the already-discounted line, so the two
    MULTIPLY -- they do not add. 10% and 10% is 19%, not 20%. Capping each term
    separately (which is what the two checks did before) let a 10%-capped user
    reach 19%, and a 2%-capped Cartier line reach 3.96%.
    """
    keep = (1.0 - max(0.0, line_pct) / 100.0) * (1.0 - max(0.0, cart_pct) / 100.0)
    return (1.0 - keep) * 100.0


def assert_stack_within_cap(
    label: str, line_pct: float, cart_pct: float, cap: float
) -> None:
    """Raise 403 when a line discount and the bill discount MULTIPLY past ``cap``.

    ONE implementation, called by every door that can change what a line or a
    bill is discounted by. create_order checked the stack and
    POST /{order_id}/items did not, so the cap was walked past simply by saving
    the DRAFT with its bill discount first and adding the line afterwards.
    """
    combined = combined_discount_pct(line_pct, cart_pct)
    if combined > cap + 1e-9:
        raise HTTPException(
            status_code=403,
            detail=(
                f"{label}: a {line_pct:.2f}% item discount and a "
                f"{cart_pct}% bill discount together come to "
                f"{combined:.2f}%, over the {cap}% allowed for this item "
                f"(role + category/brand caps). Lower one of them, or get "
                f"a manager's approval."
            ),
        )


def _enforce_line_pricing(item, product, *, is_admin: bool, role_cap: float) -> dict:
    """THE per-line price-integrity + discount-cap gate.

    ONE implementation, shared by create_order and POST /{order_id}/items.
    It existed twice and the copies had drifted: the add-item door resolved
    the product with a narrower lookup (so every guard here silently no-op'd
    for SKU/_id/catalog references) and re-implemented
    effective_line_discount_pct inline. Never fork this again -- a rule
    written twice is how the caps stop agreeing.

    ``item``    -- an OrderItemCreate (both doors use the same model).
    ``product`` -- the doc from _resolve_billable_product (None for virtual
                   lines, or when no product repo is available).

    Enforces (BUG-119/BUG-118 + SYSTEM_INTENT discount matrix):
      * unit_price <= catalog ceiling (offer_price when HQ-discounted,
        else MRP)                                           -> 400
      * unit_price >= cost on priced lines                  -> 400
      * NO further store discount on an HQ-discounted item
        (offer < MRP), non-admin                            -> 403
      * effective discount (explicit %, or implied by a unit_price under
        MRP -- effective_line_discount_pct) <= min(role cap, category cap,
        luxury-brand cap), non-admin                        -> 403
      * owner ruling 2026-08-30: any manual discount (explicit % OR a typed
        price below the catalog ceiling) carries a written reason
        (>= 4 chars)                                        -> 400

    Returns a dict consumed by both doors:
      eff_disc      -- effective discount % for the cap math
      loyalty_eff   -- loyalty-engine effective discount (vs the ceiling)
      below_ceiling -- typed unit_price under the catalog price
      cap           -- the tightened cap (ALWAYS computed; the stacking
                       check needs it even when the line discount is 0)
      cost / mrp / offer -- normalised catalog snapshot (None when unknown)
    """

    def _num(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f

    pid = item.product_id or ""
    is_real = bool(pid) and not pid.startswith(_VIRTUAL_PID_PREFIXES)
    eff_disc = item.discount_percent
    loyalty_eff = float(item.discount_percent or 0.0)
    below_ceiling = False
    mrp = offer = cost = None

    if is_real and product:
        m = _num(product.get("mrp"))
        mrp = m if (m is not None and m > 0) else None
        o = _num(product.get("offer_price"))
        offer = o if (o is not None and o > 0) else None
        cost = _num(product.get("cost_price"))
        up = item.unit_price
        hq_discounted = bool(offer and mrp and offer < mrp)
        ceiling = offer if hq_discounted else mrp
        below_ceiling = bool(ceiling and up < ceiling - 1e-6)
        if ceiling and up > ceiling + 1e-6:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"unit_price Rs{up} exceeds the catalog "
                    f"{'offer price' if hq_discounted else 'MRP'} "
                    f"Rs{ceiling} for {item.product_name or pid}."
                ),
            )
        # Cost floor: never sell a PRICED line below cost. A Rs0 line is a
        # free / 100%-discount item gated by the approval requirement (C-4),
        # so it is exempt here.
        if cost and cost > 0 and up > 1e-6 and up < cost - 1e-6:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"unit_price Rs{up} is below cost Rs{cost} for "
                    f"{item.product_name or pid}. Contact a manager."
                ),
            )
        # BUG-118 (SYSTEM_INTENT s3): an HQ-discounted item (offer<MRP) sells
        # at exactly offer_price -- no further store discount. A lower
        # unit_price OR any explicit discount_percent is a further discount.
        if (
            not is_admin
            and hq_discounted
            and (item.discount_percent > 0 or up < offer - 1e-6)
        ):
            logger.warning(
                "[ORDERS] BUG-118 blocked further discount on HQ-discounted %s",
                pid,
            )
            raise HTTPException(
                status_code=403,
                detail=(
                    f"{item.product_name or pid} is already discounted by HQ "
                    f"(offer Rs{offer} < MRP Rs{mrp}); no further store "
                    f"discount is allowed. Contact an administrator to override."
                ),
            )
        # Effective discount for the cap check (max of explicit + implied).
        if mrp and up < mrp - 1e-6:
            eff_disc = effective_line_discount_pct(item.discount_percent, up, mrp)
        from api.services.loyalty_engine import implied_ceiling_discount

        loyalty_eff = max(loyalty_eff, implied_ceiling_discount(up, mrp, offer))

    # Category + luxury-brand cap. ALWAYS computed (not only when a discount
    # is present): the stacking check downstream measures a 0%-discount line
    # against a bill discount too.
    cap = role_cap
    if is_real:
        if product:
            try:
                from api.services.pricing_caps import (
                    effective_discount_cap as product_discount_cap,
                )

                cap = min(
                    role_cap,
                    product_discount_cap(
                        product.get("discount_category"), product.get("brand")
                    ),
                )
            except Exception:  # noqa: BLE001
                # FAIL-CLOSED on the cap-TIGHTENING path: a thrown resolver
                # must never widen the discount back to the loose role cap.
                # Tighten from the strongest signal left on the payload -- its
                # brand (pure, DB-free). A plain/MASS line keeps the role cap.
                try:
                    from api.services.pricing_caps import (
                        brand_cap_for as _luxury_brand_cap,
                    )

                    bcap = _luxury_brand_cap(item.brand)
                    if bcap is not None:
                        cap = min(cap, bcap)
                except Exception:  # noqa: BLE001
                    pass  # even the pure fallback failed: do not loosen
        else:
            # No product doc for a real pid: only reachable when no product
            # repo exists (mock/DB-less mode) -- _resolve_billable_product
            # refuses unresolvable pids outright otherwise. Same payload-brand
            # fail-closed floor as above.
            try:
                from api.services.pricing_caps import (
                    brand_cap_for as _luxury_brand_cap,
                )

                bcap = _luxury_brand_cap(item.brand)
                if bcap is not None:
                    cap = min(cap, bcap)
            except Exception:  # noqa: BLE001
                pass

    if not is_admin and eff_disc > cap + 1e-9:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Discount {round(eff_disc, 2)}% on "
                f"{item.product_name or pid or 'this line'} (explicit discount "
                f"and/or unit price below MRP) exceeds your limit of {cap}%. "
                f"Contact a manager for approval."
            ),
        )

    # Owner ruling 2026-08-30: a MANUAL discount always carries a written
    # reason -- whether given as a discount_percent OR by typing a unit_price
    # under the catalog price. Selling AT the catalog offer/MRP is not a
    # manual discount; promo-engine discounts ride applied_promos.
    if (item.discount_percent or 0) > 0 or below_ceiling:
        if len(str(item.discount_reason or "").strip()) < 4:
            why = (
                "price is below the current catalog price"
                if not (item.discount_percent or 0) > 0
                else "manual discount with no offer applied"
            )
            raise HTTPException(
                status_code=400,
                detail=(
                    f"A discount reason (at least 4 characters) is required "
                    f"for {item.product_name or item.product_id} — {why}. "
                    f"Use the item's Discount button to add the reason."
                ),
            )

    return {
        "eff_disc": eff_disc,
        "loyalty_eff": loyalty_eff,
        "below_ceiling": below_ceiling,
        "cap": cap,
        "cost": cost,
        "mrp": mrp,
        "offer": offer,
    }
