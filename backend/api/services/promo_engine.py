"""
IMS 2.0 - POS promo engine (PURE + DARK)
========================================
The money math for campaign promos. This module is PURE and deterministic --
no DB, no I/O, no clock -- so it can be unit-tested exhaustively and adversarially
before it is ever wired into the revenue-critical POS path.

It is NOT yet called by the live POS order flow. Wiring it in (behind a dark
`pos.promo_engine_enabled` flag, OFF by default) is a deliberate, separate,
owner-gated step -- promos touch revenue and POS is "ask before touching".

Locked owner decisions (docs/roadmap/DECISIONS.md sec 3):
  * #11 Promo stacking is EXCLUSIVE by default -- only the single BEST promo
    fires. A campaign may opt into stacking (stackable=True); stackable promos
    all apply, and among the non-stackable (exclusive) promos only the one
    giving the largest discount is chosen.
  * N10 "2nd pair 50%" -- when two qualifying units are bought, the CHEAPER one
    (the "second pair") gets a discount (default 50%). Generalised to every
    consecutive pair of eligible units.

Money is rupee floats; every returned amount is rounded to 2 dp (paisa). The
engine returns the RAW promo discount -- the caller is still responsible for the
category/luxury discount caps (pricing_caps), the promo ceiling, and the
cost-price floor (cost_floor). The engine never lets a promo drive a line (or the
cart) below zero, but it does NOT know cost, so it cannot enforce the cost floor.

No emoji in this file (Windows cp1252).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Promo kinds ---------------------------------------------------------------
PROMO_PERCENT = "PERCENT"  # flat % off every eligible unit
PROMO_SECOND_PAIR = "SECOND_PAIR"  # % off the cheaper unit of each eligible pair
PROMO_KINDS = frozenset({PROMO_PERCENT, PROMO_SECOND_PAIR})

# Default discount for the "2nd pair" promo when a campaign omits it.
DEFAULT_SECOND_PAIR_PCT = 50.0


def _clamp_pct(value: object) -> float:
    """Coerce to a sane 0..100 percentage. Junk / None -> 0."""
    try:
        pct = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if pct != pct:  # NaN
        return 0.0
    if pct < 0:
        return 0.0
    if pct > 100:
        return 100.0
    return pct


def _safe_price(value: object) -> float:
    """A non-negative unit price. Junk / negative -> 0 (never a credit)."""
    try:
        p = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if p != p or p < 0:  # NaN or negative
        return 0.0
    return p


@dataclass(frozen=True)
class CartLine:
    """One POS cart line, priced BEFORE any promo (i.e. after MRP->offer and any
    per-line manual discount the cashier already applied). unit_price is per unit."""

    line_id: str
    product_id: str
    category: str  # discount/product category, matched case-insensitively
    unit_price: float
    quantity: int


@dataclass(frozen=True)
class Promo:
    """A campaign promo. `categories` / `product_ids` None => applies to all
    lines; otherwise the line must match (case-insensitive for category)."""

    promo_id: str
    kind: str
    # None = unset. For SECOND_PAIR an unset percent defaults to 50; an EXPLICIT
    # percent=0 means 0% (disabled), never the 50% default.
    percent: Optional[float] = None
    stackable: bool = False
    categories: Optional[frozenset] = None
    product_ids: Optional[frozenset] = None
    min_units: int = 1  # promo only fires once this many eligible units are present
    label: str = ""


@dataclass
class PromoResult:
    total_discount: float = 0.0
    applied: List[str] = field(default_factory=list)  # promo_ids that fired
    suppressed: List[str] = field(default_factory=list)  # exclusive promos that lost
    breakdown: Dict[str, float] = field(default_factory=dict)  # promo_id -> discount
    exclusive_winner: Optional[str] = None


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _eligible_unit_prices(promo: Promo, lines: List[CartLine]) -> List[float]:
    """Expand matching lines into a flat list of per-unit prices (one entry per
    physical unit). A line matches when the promo's category/product filters (if
    any) include it. Quantities <= 0 and junk prices are dropped."""
    cats = (
        frozenset(_norm(c) for c in promo.categories)
        if promo.categories is not None
        else None
    )
    prods = promo.product_ids if promo.product_ids is not None else None
    units: List[float] = []
    for ln in lines:
        if cats is not None and _norm(ln.category) not in cats:
            continue
        if prods is not None and ln.product_id not in prods:
            continue
        qty = int(ln.quantity) if isinstance(ln.quantity, (int, float)) else 0
        if qty <= 0:
            continue
        price = _safe_price(ln.unit_price)
        units.extend([price] * qty)
    return units


def _discount_percent(promo: Promo, lines: List[CartLine]) -> float:
    units = _eligible_unit_prices(promo, lines)
    if len(units) < max(1, promo.min_units):
        return 0.0
    pct = _clamp_pct(promo.percent)
    return round(sum(u * pct / 100.0 for u in units), 2)


def _discount_second_pair(promo: Promo, lines: List[CartLine]) -> float:
    """% off the CHEAPER unit of each consecutive pair. Sort eligible units
    high->low; units at odd indices (the 2nd of each pair) are discounted. An
    unpaired trailing unit gets nothing."""
    units = _eligible_unit_prices(promo, lines)
    if len(units) < max(2, promo.min_units):
        return 0.0
    # Unset (None) -> 50% default; an EXPLICIT percent (incl. 0) is honoured.
    pct = (
        DEFAULT_SECOND_PAIR_PCT if promo.percent is None else _clamp_pct(promo.percent)
    )
    units.sort(reverse=True)
    discounted = units[1::2]  # the cheaper one in each (full, discounted) pair
    return round(sum(u * pct / 100.0 for u in discounted), 2)


def _discount_for(promo: Promo, lines: List[CartLine]) -> float:
    if promo.kind == PROMO_PERCENT:
        return _discount_percent(promo, lines)
    if promo.kind == PROMO_SECOND_PAIR:
        return _discount_second_pair(promo, lines)
    return 0.0  # unknown kind never discounts


def cart_subtotal(lines: List[CartLine]) -> float:
    return round(
        sum(_safe_price(ln.unit_price) * max(0, int(ln.quantity)) for ln in lines), 2
    )


def evaluate_cart(lines: List[CartLine], promos: List[Promo]) -> PromoResult:
    """Resolve the promos against the cart per the EXCLUSIVE-by-default rule.

    - Every STACKABLE promo applies; its discount adds to the total.
    - Among NON-STACKABLE (exclusive) promos, only the single one with the
      largest discount fires; the rest are suppressed.
    - The total promo discount is capped at the cart subtotal (a promo can never
      make the cart negative). Stackable promos are clamped first (deterministic
      order: input order), then the best exclusive promo takes whatever headroom
      remains -- so a fully-discounted cart can't be over-credited.
    """
    result = PromoResult()
    if not lines or not promos:
        return result

    subtotal = cart_subtotal(lines)
    if subtotal <= 0:
        return result

    stackable: List[Tuple[Promo, float]] = []
    exclusive: List[Tuple[Promo, float]] = []
    # promo_id keys `breakdown`, so it MUST be unique within one call -- a
    # duplicate id would silently overwrite its breakdown entry while `total`
    # still summed both, breaking the invariant total_discount == sum(breakdown).
    # First occurrence wins; later duplicates are skipped (fail-soft -- a config
    # list with two promos sharing an id is a caller bug, not a checkout-blocker).
    seen_ids: set = set()
    for p in promos:
        if p.kind not in PROMO_KINDS:
            continue
        if p.promo_id in seen_ids:
            continue
        seen_ids.add(p.promo_id)
        disc = _discount_for(p, lines)
        if disc <= 0:
            continue
        (stackable if p.stackable else exclusive).append((p, disc))

    remaining = subtotal
    total = 0.0

    # Stackable promos first, in input order, each clamped to remaining headroom.
    for p, disc in stackable:
        take = min(disc, remaining)
        if take <= 0:
            continue
        take = round(take, 2)
        result.applied.append(p.promo_id)
        result.breakdown[p.promo_id] = take
        total += take
        remaining = round(remaining - take, 2)

    # Best single exclusive promo takes the remaining headroom.
    if exclusive:
        # Highest discount wins; ties broken by promo_id for determinism.
        exclusive.sort(key=lambda pd: (-pd[1], pd[0].promo_id))
        winner, win_disc = exclusive[0]
        take = round(min(win_disc, remaining), 2)
        if take > 0:
            result.applied.append(winner.promo_id)
            result.breakdown[winner.promo_id] = take
            result.exclusive_winner = winner.promo_id
            total += take
            remaining = round(remaining - take, 2)
        result.suppressed = [p.promo_id for p, _ in exclusive[1:]]
        if take <= 0:
            # Winner produced no usable discount (cart already fully discounted);
            # it is suppressed too, not "applied".
            result.suppressed = [p.promo_id for p, _ in exclusive]
            result.exclusive_winner = None

    result.total_discount = round(min(total, subtotal), 2)
    return result


def allocate_discount(lines: List[CartLine], total_discount: float) -> Dict[str, float]:
    """Split one promo `total_discount` across cart lines proportional to each
    line's value (unit_price * quantity), PAISA-EXACT: the per-line shares are
    rounded to 2 dp yet provably sum to `total_discount` to the paisa.

    The POS GST tax invoice needs a per-line discount to compute CGST/SGST/IGST
    per line; `evaluate_cart` returns a single authoritative total (rounded once),
    so a naive proportional split would drift by a paisa or two. This does the
    largest-remainder split in integer paisa and hands the residual paisa to the
    lines with the biggest fractional remainder, so sum(result) == total_discount
    exactly. Returns {line_id: discount_rupees}. Lines with no value get 0.
    """
    out: Dict[str, float] = {ln.line_id: 0.0 for ln in lines}
    total_paisa = int(round(_safe_price(total_discount) * 100))
    if total_paisa <= 0 or not lines:
        return out

    values = [
        (ln.line_id, _safe_price(ln.unit_price) * max(0, int(ln.quantity)))
        for ln in lines
    ]
    value_total = sum(v for _, v in values)
    if value_total <= 0:
        return out

    # Never allocate more than the lines are worth (caller should have capped the
    # total at subtotal already, but guard anyway).
    total_paisa = min(total_paisa, int(round(value_total * 100)))

    floors: Dict[str, int] = {}
    remainders: List[Tuple[float, str]] = []
    assigned = 0
    for line_id, val in values:
        exact = val / value_total * total_paisa
        f = int(exact)  # floor for non-negative
        floors[line_id] = f
        assigned += f
        remainders.append((exact - f, line_id))

    # Distribute the leftover paisa to the largest fractional remainders (ties ->
    # line_id for determinism).
    leftover = total_paisa - assigned
    remainders.sort(key=lambda r: (-r[0], r[1]))
    for i in range(leftover):
        floors[remainders[i % len(remainders)][1]] += 1

    return {lid: round(p / 100.0, 2) for lid, p in floors.items()}
