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
# F11/F12 additions (cross-category bundling + offer-tallying). These extend the
# original PR-#677 engine without changing PERCENT / SECOND_PAIR behaviour.
PROMO_THRESHOLD = "THRESHOLD"  # spend >= min_cart_value, get % off eligible lines
PROMO_BOGO = "BOGO"  # buy N eligible units, get M units % off (cheapest)
PROMO_COMBO = "COMBO"  # cross-category bundle: all groups present -> % off reward set
PROMO_KINDS = frozenset(
    {
        PROMO_PERCENT,
        PROMO_SECOND_PAIR,
        PROMO_THRESHOLD,
        PROMO_BOGO,
        PROMO_COMBO,
    }
)

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
    per-line manual discount the cashier already applied). unit_price is per unit.

    `brand` + `discount_category` feed the pricing-caps clamp in the F11/F12
    layer (evaluate_promos) -- they are optional so the original PR-#677 callers
    that build a CartLine without them keep working unchanged."""

    line_id: str
    product_id: str
    category: str  # discount/product category, matched case-insensitively
    unit_price: float
    quantity: int
    # F11/F12: drive the pricing_caps clamp + cross-category bundle matching.
    brand: Optional[str] = None
    discount_category: Optional[str] = None
    item_type: Optional[str] = None
    cost_at_sale: Optional[float] = None


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
    # --- F11/F12 fields (ignored by PERCENT / SECOND_PAIR) ---
    # THRESHOLD: minimum cart subtotal that must be reached before the promo fires.
    min_cart_value: Optional[float] = None
    # BOGO: buy `buy_quantity` eligible units -> `get_quantity` units at `percent` off.
    buy_quantity: int = 1
    get_quantity: int = 1
    # COMBO: every group (a category/item_type/brand filter) must be present in the
    # cart; the reward (percent) then applies to the eligible lines.
    combo_groups: Optional[tuple] = None
    # Hard rupee ceiling on this single promo's discount (F12 max_discount_amount).
    max_discount_amount: Optional[float] = None
    # CRM gating (optional, fail-open).
    customer_tiers: Optional[frozenset] = None
    first_purchase_only: bool = False


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


def _discount_threshold(promo: Promo, lines: List[CartLine]) -> float:
    """THRESHOLD: only fires when the WHOLE cart subtotal reaches
    `min_cart_value`; then `percent` off every eligible unit (same line filter
    as PERCENT). An unset/zero min_cart_value behaves like a plain PERCENT."""
    if promo.min_cart_value is not None:
        if cart_subtotal(lines) + 1e-9 < _safe_price(promo.min_cart_value):
            return 0.0
    return _discount_percent(promo, lines)


def _discount_bogo(promo: Promo, lines: List[CartLine]) -> float:
    """BOGO: for every (buy_quantity) eligible units, (get_quantity) units get
    `percent` off (default 100 = free). The discount lands on the CHEAPEST
    eligible units (the store gives away its least valuable stock)."""
    units = _eligible_unit_prices(promo, lines)
    buy = max(1, int(promo.buy_quantity or 1))
    get = max(1, int(promo.get_quantity or 1))
    group = buy + get
    if len(units) < group:
        return 0.0
    # percent unset for BOGO defaults to 100% (the classic "get one free").
    pct = 100.0 if promo.percent is None else _clamp_pct(promo.percent)
    num_groups = len(units) // group
    free_count = num_groups * get
    units.sort()  # cheapest first -> those become the discounted units
    return round(sum(u * pct / 100.0 for u in units[:free_count]), 2)


def _discount_combo(promo: Promo, lines: List[CartLine]) -> float:
    """COMBO (cross-category bundle): every group in `combo_groups` must be
    present in the cart; the reward is `percent` off every eligible unit (the
    line filter on the promo, or all lines if none). Each group is a dict with
    optional category / item_type / brand keys."""
    groups = promo.combo_groups or ()
    if groups:
        for grp in groups:
            cat = _norm(grp.get("category")) if grp.get("category") else None
            it = _norm(grp.get("item_type")) if grp.get("item_type") else None
            br = _norm(grp.get("brand")) if grp.get("brand") else None
            present = any(
                (cat is None or _norm(ln.discount_category or ln.category) == cat)
                and (it is None or _norm(ln.item_type) == it)
                and (br is None or _norm(ln.brand) == br)
                for ln in lines
            )
            if not present:
                return 0.0
    return _discount_percent(promo, lines)


def _discount_for(promo: Promo, lines: List[CartLine]) -> float:
    if promo.kind == PROMO_PERCENT:
        return _discount_percent(promo, lines)
    if promo.kind == PROMO_SECOND_PAIR:
        return _discount_second_pair(promo, lines)
    if promo.kind == PROMO_THRESHOLD:
        return _discount_threshold(promo, lines)
    if promo.kind == PROMO_BOGO:
        return _discount_bogo(promo, lines)
    if promo.kind == PROMO_COMBO:
        return _discount_combo(promo, lines)
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
        # F12: hard rupee ceiling per promo (max_discount_amount). Applied here
        # so it bounds the promo BEFORE stacking/exclusive resolution + the
        # subtotal clamp below. Unset -> no ceiling (original behaviour).
        if p.max_discount_amount is not None:
            disc = min(disc, _safe_price(p.max_discount_amount))
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


# ===========================================================================
# F11 / F12 high-level layer: rule-doc adapter + cap-clamping evaluate_promos
# ===========================================================================
# The functions below adapt persisted promo-rule documents (from the promotions
# router) + a POS cart payload into the pure CartLine/Promo core above, then
# enforce the OUTER hardlock: a promo can NEVER drive a line below its
# category / luxury-brand discount cap (pricing_caps). This is the supreme
# authority -- the engine clamps to it BEFORE returning a discount. The router
# layer owns the DB read, the atomic uses-count guard, and the audit write;
# everything here is pure + deterministic + never raises.

from api.services.pricing_caps import effective_discount_cap  # noqa: E402

_KIND_BY_PROMO_TYPE = {
    "THRESHOLD": PROMO_THRESHOLD,
    "BOGO": PROMO_BOGO,
    "COMBO": PROMO_COMBO,
    "SECOND_PAIR": PROMO_SECOND_PAIR,
    "PERCENT": PROMO_PERCENT,
    # F12 cross-category bundle synonym.
    "CROSS_CATEGORY": PROMO_COMBO,
    "BUNDLE": PROMO_COMBO,
}


def _frozen(values) -> Optional[frozenset]:
    if not values:
        return None
    if isinstance(values, str):
        values = [values]
    out = frozenset(str(v) for v in values if v not in (None, ""))
    return out or None


def cart_line_from_item(item: Dict[str, object], index: int) -> CartLine:
    """Build a pure CartLine from an order-create / POS item dict. The category
    used for FILTER matching prefers discount_category (the real cap tier) but
    falls back to the item's category. brand + item_type feed COMBO + the cap."""
    disc_cat = item.get("discount_category") or item.get("category") or ""
    line_id = (
        item.get("line_id")
        or item.get("item_id")
        or (f"L{index}")
    )
    return CartLine(
        line_id=str(line_id),
        product_id=str(item.get("product_id") or line_id),
        category=str(disc_cat),
        unit_price=_safe_price(item.get("unit_price")),
        quantity=int(item.get("quantity") or 0)
        if isinstance(item.get("quantity"), (int, float))
        else 0,
        brand=(str(item.get("brand")) if item.get("brand") else None),
        discount_category=(
            str(item.get("discount_category"))
            if item.get("discount_category")
            else None
        ),
        item_type=(str(item.get("item_type")) if item.get("item_type") else None),
        cost_at_sale=(
            _safe_price(item.get("cost_at_sale"))
            if item.get("cost_at_sale") is not None
            else None
        ),
    )


def promo_from_rule(rule: Dict[str, object]) -> Optional[Promo]:
    """Adapt a persisted promo-rule document into a pure Promo. Returns None for
    an unknown promo_type so a malformed rule is simply skipped (fail-soft)."""
    ptype = (str(rule.get("promo_type") or rule.get("kind") or "")).strip().upper()
    kind = _KIND_BY_PROMO_TYPE.get(ptype)
    if kind is None:
        return None
    combo_groups = rule.get("combo_groups") or rule.get("trigger_rules")
    combo_tuple = (
        tuple(combo_groups) if isinstance(combo_groups, (list, tuple)) else None
    )
    # reward percent: prefer explicit reward_value; fall back to type-specific
    # legacy field names for compatibility with the campaigns promo_templates.
    percent = rule.get("reward_value")
    if percent is None:
        percent = (
            rule.get("threshold_discount_pct")
            or rule.get("combo_discount_pct")
            or rule.get("percent")
        )
    return Promo(
        promo_id=str(rule.get("promo_id") or rule.get("_id") or rule.get("name") or "?"),
        kind=kind,
        percent=(float(percent) if percent is not None else None),
        stackable=bool(rule.get("stackable")),
        categories=_frozen(rule.get("trigger_categories") or rule.get("categories")),
        product_ids=_frozen(rule.get("product_ids")),
        min_units=int(rule.get("min_qty") or rule.get("min_units") or 1),
        label=str(rule.get("name") or ""),
        min_cart_value=(
            float(rule["min_cart_value"])
            if rule.get("min_cart_value") is not None
            else (
                float(rule["min_order_value"])
                if rule.get("min_order_value") is not None
                else None
            )
        ),
        buy_quantity=int(rule.get("buy_quantity") or rule.get("min_qty") or 1),
        get_quantity=int(rule.get("get_quantity") or 1),
        combo_groups=combo_tuple,
        max_discount_amount=(
            float(rule["max_discount_amount"])
            if rule.get("max_discount_amount") is not None
            else None
        ),
        customer_tiers=_frozen(rule.get("customer_tiers")),
        first_purchase_only=bool(rule.get("first_purchase_only")),
    )


def _customer_passes(promo: Promo, customer: Optional[Dict[str, object]]) -> bool:
    """Optional CRM gate (tier / first-purchase). Fail-OPEN: a promo with no
    customer conditions always passes; a condition with no customer to check also
    passes (the engine never blocks a sale on missing CRM)."""
    if promo.customer_tiers:
        if customer:
            tier = _norm(
                str(customer.get("loyalty_tier") or customer.get("tier") or "")
            )
            allowed = {_norm(t) for t in promo.customer_tiers}
            if tier and tier not in allowed:
                return False
    if promo.first_purchase_only and customer:
        orders = customer.get("total_orders")
        try:
            if orders is not None and int(orders) > 0:
                return False
        except (TypeError, ValueError):
            pass
    return True


def _clamp_to_caps(
    lines: List[CartLine], result: PromoResult
) -> Tuple[float, Dict[str, float]]:
    """Clamp the engine's per-line allocation of the total promo discount so NO
    line is discounted beyond its category / luxury-brand cap (pricing_caps).
    Returns (capped_total, per_line_capped_discount).

    The pure engine already clamps the cart total to the subtotal but does not
    know category/brand caps. This is the F11/F12 OUTER hardlock: the supreme
    authority a promo can never breach (DECISIONS + F11 business rules)."""
    per_line = allocate_discount(lines, result.total_discount)
    capped: Dict[str, float] = {}
    total = 0.0
    by_id = {ln.line_id: ln for ln in lines}
    for line_id, disc in per_line.items():
        ln = by_id.get(line_id)
        if ln is None or disc <= 0:
            capped[line_id] = 0.0
            continue
        line_value = _safe_price(ln.unit_price) * max(0, int(ln.quantity))
        cap_pct = effective_discount_cap(
            ln.discount_category or ln.category, ln.brand
        )
        max_disc = round(line_value * cap_pct / 100.0, 2)
        allowed = min(disc, max_disc)
        capped[line_id] = round(allowed, 2)
        total += allowed
    return round(total, 2), capped


def evaluate_promos(
    cart: Dict[str, object],
    customer: Optional[Dict[str, object]] = None,
    store: Optional[Dict[str, object]] = None,
    rules: Optional[List[Dict[str, object]]] = None,
) -> Dict[str, object]:
    """High-level F11/F12 entrypoint: evaluate persisted promo-rule docs against
    a POS cart and return the BEST applicable outcome, with the discount clamped
    to the category/luxury caps.

    Args:
        cart:     {"items": [ {product_id, brand, item_type, discount_category,
                  quantity, unit_price, cost_at_sale, ...}, ... ]}.
        customer: optional customer doc (tier / first-purchase gating; fail-open).
        store:    optional store doc (reserved; store filtering is done by the
                  router before passing `rules`).
        rules:    pre-filtered ACTIVE rule docs (the router owns the DB read +
                  store/date/active filtering so the engine stays pure).

    Returns a JSON-safe dict:
        {
          "applied": bool,
          "total_discount": float,            # cap-clamped rupee total
          "raw_total_discount": float,        # pre-cap engine total (for audit)
          "fired": [promo_id, ...],
          "suppressed": [promo_id, ...],
          "exclusive_winner": promo_id | None,
          "breakdown": {promo_id: rupees},    # pre-cap per-promo (for the tally)
          "per_line_discount": {line_id: rupees},  # cap-clamped, paisa-exact
          "evaluated_count": int,
          "names": {promo_id: name},
        }

    When no rule applies (or `rules` is empty / flag-off path), ``applied`` is
    False and every amount is 0.0 -- order totals are then byte-identical to the
    no-engine path. Pure + fail-soft: never raises.
    """
    empty = {
        "applied": False,
        "total_discount": 0.0,
        "raw_total_discount": 0.0,
        "fired": [],
        "suppressed": [],
        "exclusive_winner": None,
        "breakdown": {},
        "per_line_discount": {},
        "evaluated_count": 0,
        "names": {},
    }
    try:
        items = (cart or {}).get("items") or []
        if not isinstance(items, list) or not items:
            return empty
        rule_list = rules or []
        empty["evaluated_count"] = len(rule_list)
        if not rule_list:
            return empty

        lines = [cart_line_from_item(it, i) for i, it in enumerate(items)]
        names: Dict[str, str] = {}
        promos: List[Promo] = []
        for rule in rule_list:
            p = promo_from_rule(rule)
            if p is None:
                continue
            if not _customer_passes(p, customer):
                continue
            names[p.promo_id] = p.label or p.promo_id
            promos.append(p)
        if not promos:
            return {**empty, "evaluated_count": len(rule_list)}

        result = evaluate_cart(lines, promos)
        if result.total_discount <= 0:
            return {**empty, "evaluated_count": len(rule_list), "names": names}

        capped_total, per_line = _clamp_to_caps(lines, result)
        return {
            "applied": capped_total > 0,
            "total_discount": capped_total,
            "raw_total_discount": result.total_discount,
            "fired": list(result.applied),
            "suppressed": list(result.suppressed),
            "exclusive_winner": result.exclusive_winner,
            "breakdown": dict(result.breakdown),
            "per_line_discount": per_line,
            "evaluated_count": len(rule_list),
            "names": names,
        }
    except Exception:  # noqa: BLE001 - engine is fail-soft, never blocks a sale
        return empty


def estimate_margin_impact(
    cart: Dict[str, object], evaluation: Dict[str, object]
) -> Dict[str, object]:
    """Best-effort margin impact of an evaluation for the audit row + Offer
    Tally. COGS is read from each line's cost_at_sale when present; else
    ESTIMATED at 60% of the pre-promo line value (the same fallback finance
    uses) and flagged cogs_is_estimated=True so the dashboard never presents
    estimated margin as real. Pure; never raises."""
    try:
        items = (cart or {}).get("items") or []
        total_discount = _safe_price(evaluation.get("total_discount"))
        est_cogs = 0.0
        cogs_estimated = False
        for it in items:
            qty = int(it.get("quantity") or 0) if isinstance(
                it.get("quantity"), (int, float)
            ) else 0
            cost = it.get("cost_at_sale")
            if cost is not None and _safe_price(cost) > 0:
                est_cogs += _safe_price(cost) * qty
            else:
                est_cogs += _safe_price(it.get("unit_price")) * 0.60 * qty
                cogs_estimated = True
        return {
            "total_discount_given": round(total_discount, 2),
            "estimated_cogs": round(est_cogs, 2),
            # The promo's marginal P&L effect is the giveaway itself.
            "net_margin_after_promo": round(-total_discount, 2),
            "cogs_is_estimated": cogs_estimated,
        }
    except Exception:  # noqa: BLE001
        return {
            "total_discount_given": 0.0,
            "estimated_cogs": 0.0,
            "net_margin_after_promo": 0.0,
            "cogs_is_estimated": True,
        }
