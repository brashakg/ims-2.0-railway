"""Unit tests for the pure POS promo engine (backend/api/services/promo_engine.py).

Covers the locked decisions (#11 exclusive-by-default, N10 2nd-pair-50%) plus the
adversarial edge cases: junk prices, zero/negative qty, NaN percent, over-cap
clamping, ties, and the cart-subtotal ceiling.
"""

import os

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import promo_engine as pe  # noqa: E402
from api.services.promo_engine import CartLine, Promo  # noqa: E402


def _line(line_id, price, qty=1, category="FRAME", product_id=None):
    return CartLine(
        line_id=line_id,
        product_id=product_id or line_id,
        category=category,
        unit_price=price,
        quantity=qty,
    )


# --- PERCENT promo ---------------------------------------------------------


def test_percent_simple():
    promo = Promo(promo_id="P", kind=pe.PROMO_PERCENT, percent=10)
    res = pe.evaluate_cart([_line("a", 1000, qty=2)], [promo])
    assert res.total_discount == 200.0  # 10% of 2000
    assert res.applied == ["P"]
    assert res.exclusive_winner == "P"


def test_percent_category_filter_excludes_nonmatching():
    promo = Promo(
        promo_id="P", kind=pe.PROMO_PERCENT, percent=20, categories=frozenset({"frame"})
    )
    lines = [_line("a", 1000, category="FRAME"), _line("b", 500, category="LENS")]
    res = pe.evaluate_cart(lines, [promo])
    assert res.total_discount == 200.0  # only the FRAME line, 20% of 1000


def test_percent_product_filter():
    promo = Promo(
        promo_id="P", kind=pe.PROMO_PERCENT, percent=50, product_ids=frozenset({"x"})
    )
    lines = [_line("a", 1000, product_id="x"), _line("b", 1000, product_id="y")]
    res = pe.evaluate_cart(lines, [promo])
    assert res.total_discount == 500.0


# --- N10 second-pair ------------------------------------------------------


def test_second_pair_default_50_off_cheaper():
    promo = Promo(promo_id="N10", kind=pe.PROMO_SECOND_PAIR)  # percent omitted -> 50
    # two pairs (frames) at 2000 and 1200 -> cheaper (1200) gets 50% = 600
    lines = [_line("a", 2000), _line("b", 1200)]
    res = pe.evaluate_cart(lines, [promo])
    assert res.total_discount == 600.0


def test_second_pair_needs_two_units():
    promo = Promo(promo_id="N10", kind=pe.PROMO_SECOND_PAIR)
    res = pe.evaluate_cart([_line("a", 2000)], [promo])
    assert res.total_discount == 0.0
    assert res.applied == []


def test_second_pair_three_units_one_unpaired():
    promo = Promo(promo_id="N10", kind=pe.PROMO_SECOND_PAIR, percent=50)
    # units 3000, 2000, 1000 sorted desc -> discount the 2nd (2000) at 50% = 1000;
    # the 3rd (1000) is unpaired -> nothing.
    lines = [_line("a", 3000), _line("b", 2000), _line("c", 1000)]
    res = pe.evaluate_cart(lines, [promo])
    assert res.total_discount == 1000.0


def test_second_pair_uses_quantity_expansion():
    promo = Promo(promo_id="N10", kind=pe.PROMO_SECOND_PAIR, percent=50)
    # one line, qty 2 at 1500 -> two units -> 2nd gets 50% = 750
    res = pe.evaluate_cart([_line("a", 1500, qty=2)], [promo])
    assert res.total_discount == 750.0


# --- #11 exclusive-by-default ---------------------------------------------


def test_exclusive_only_best_fires():
    p10 = Promo(promo_id="P10", kind=pe.PROMO_PERCENT, percent=10)
    p25 = Promo(promo_id="P25", kind=pe.PROMO_PERCENT, percent=25)
    res = pe.evaluate_cart([_line("a", 1000)], [p10, p25])
    assert res.total_discount == 250.0  # 25% wins, 10% suppressed
    assert res.exclusive_winner == "P25"
    assert res.applied == ["P25"]
    assert res.suppressed == ["P10"]


def test_stackable_adds_on_top_of_best_exclusive():
    stack = Promo(promo_id="S", kind=pe.PROMO_PERCENT, percent=5, stackable=True)
    ex_a = Promo(promo_id="A", kind=pe.PROMO_PERCENT, percent=10)
    ex_b = Promo(promo_id="B", kind=pe.PROMO_PERCENT, percent=20)
    res = pe.evaluate_cart([_line("a", 1000)], [stack, ex_a, ex_b])
    # stackable 5% (50) + best exclusive 20% (200) = 250; A suppressed
    assert res.total_discount == 250.0
    assert set(res.applied) == {"S", "B"}
    assert res.suppressed == ["A"]
    assert res.exclusive_winner == "B"


def test_two_stackable_both_apply():
    s1 = Promo(promo_id="S1", kind=pe.PROMO_PERCENT, percent=5, stackable=True)
    s2 = Promo(promo_id="S2", kind=pe.PROMO_PERCENT, percent=10, stackable=True)
    res = pe.evaluate_cart([_line("a", 1000)], [s1, s2])
    assert res.total_discount == 150.0  # 50 + 100
    assert set(res.applied) == {"S1", "S2"}


def test_exclusive_tie_breaks_on_promo_id():
    a = Promo(promo_id="Z", kind=pe.PROMO_PERCENT, percent=10)
    b = Promo(promo_id="A", kind=pe.PROMO_PERCENT, percent=10)
    res = pe.evaluate_cart([_line("x", 1000)], [a, b])
    assert res.exclusive_winner == "A"  # tie -> lexicographically smallest id


# --- caps / clamps / adversarial ------------------------------------------


def test_total_never_exceeds_subtotal():
    s1 = Promo(promo_id="S1", kind=pe.PROMO_PERCENT, percent=80, stackable=True)
    s2 = Promo(promo_id="S2", kind=pe.PROMO_PERCENT, percent=80, stackable=True)
    res = pe.evaluate_cart([_line("a", 1000)], [s1, s2])
    # 800 + 800 would be 1600; clamped to subtotal 1000
    assert res.total_discount == 1000.0


def test_exclusive_gets_remaining_headroom_after_stackable():
    s = Promo(promo_id="S", kind=pe.PROMO_PERCENT, percent=70, stackable=True)
    ex = Promo(promo_id="E", kind=pe.PROMO_PERCENT, percent=80)
    res = pe.evaluate_cart([_line("a", 1000)], [s, ex])
    # stackable 700, exclusive wants 800 but only 300 headroom -> total 1000
    assert res.total_discount == 1000.0
    assert res.breakdown["S"] == 700.0
    assert res.breakdown["E"] == 300.0


def test_negative_and_junk_prices_are_ignored():
    promo = Promo(promo_id="P", kind=pe.PROMO_PERCENT, percent=10)
    lines = [_line("a", -500), _line("b", 1000)]
    res = pe.evaluate_cart(lines, [promo])
    assert res.total_discount == 100.0  # only the 1000 line counts


def test_zero_quantity_line_ignored():
    promo = Promo(promo_id="P", kind=pe.PROMO_PERCENT, percent=10)
    res = pe.evaluate_cart([_line("a", 1000, qty=0)], [promo])
    assert res.total_discount == 0.0


def test_percent_over_100_clamped():
    promo = Promo(promo_id="P", kind=pe.PROMO_PERCENT, percent=250)
    res = pe.evaluate_cart([_line("a", 1000)], [promo])
    assert res.total_discount == 1000.0  # clamped to 100%


def test_nan_percent_treated_as_zero():
    promo = Promo(promo_id="P", kind=pe.PROMO_PERCENT, percent=float("nan"))
    res = pe.evaluate_cart([_line("a", 1000)], [promo])
    assert res.total_discount == 0.0
    assert res.applied == []


def test_unknown_kind_never_discounts():
    promo = Promo(promo_id="P", kind="MYSTERY", percent=50)
    res = pe.evaluate_cart([_line("a", 1000)], [promo])
    assert res.total_discount == 0.0


def test_empty_inputs():
    assert pe.evaluate_cart([], []).total_discount == 0.0
    assert pe.evaluate_cart([_line("a", 1000)], []).total_discount == 0.0
    assert (
        pe.evaluate_cart([], [Promo("P", pe.PROMO_PERCENT, 10)]).total_discount == 0.0
    )


def test_min_units_threshold():
    promo = Promo(promo_id="P", kind=pe.PROMO_PERCENT, percent=10, min_units=3)
    # only 2 units -> below threshold -> no discount
    assert pe.evaluate_cart([_line("a", 1000, qty=2)], [promo]).total_discount == 0.0
    # 3 units -> fires
    assert pe.evaluate_cart([_line("a", 1000, qty=3)], [promo]).total_discount == 300.0


def test_fully_discounted_cart_suppresses_exclusive_winner():
    s = Promo(promo_id="S", kind=pe.PROMO_PERCENT, percent=100, stackable=True)
    ex = Promo(promo_id="E", kind=pe.PROMO_PERCENT, percent=50)
    res = pe.evaluate_cart([_line("a", 1000)], [s, ex])
    assert res.total_discount == 1000.0
    assert res.applied == ["S"]
    assert res.exclusive_winner is None
    assert "E" in res.suppressed


# --- review fixes: dedupe, explicit-0 second-pair, per-line allocation ------


def test_duplicate_promo_id_skipped_invariant_holds():
    # Two promos share id 'D'; only the first is honoured so the invariant
    # total_discount == sum(breakdown.values()) holds.
    d1 = Promo(promo_id="D", kind=pe.PROMO_PERCENT, percent=10, stackable=True)
    d2 = Promo(promo_id="D", kind=pe.PROMO_PERCENT, percent=20, stackable=True)
    res = pe.evaluate_cart([_line("a", 1000)], [d1, d2])
    assert res.total_discount == 100.0  # only the first 'D' (10%) fires
    assert res.applied == ["D"]
    assert round(sum(res.breakdown.values()), 2) == res.total_discount


def test_second_pair_explicit_zero_percent_disables():
    # An EXPLICIT percent=0 means 0% (disabled), not the 50% default.
    promo = Promo(promo_id="N10", kind=pe.PROMO_SECOND_PAIR, percent=0)
    res = pe.evaluate_cart([_line("a", 2000), _line("b", 1200)], [promo])
    assert res.total_discount == 0.0
    assert res.applied == []


def test_second_pair_none_percent_defaults_50():
    promo = Promo(promo_id="N10", kind=pe.PROMO_SECOND_PAIR)  # percent=None
    res = pe.evaluate_cart([_line("a", 2000), _line("b", 1200)], [promo])
    assert res.total_discount == 600.0  # 50% of the cheaper (1200)


def test_allocate_discount_sums_exactly_paisa():
    # round-at-end total vs per-line split must reconcile to the paisa.
    lines = [_line(str(i), 14.29) for i in range(7)]
    promo = Promo(promo_id="N", kind=pe.PROMO_PERCENT, percent=50)
    res = pe.evaluate_cart(lines, [promo])
    alloc = pe.allocate_discount(lines, res.total_discount)
    assert round(sum(alloc.values()), 2) == res.total_discount
    assert all(v >= 0 for v in alloc.values())


def test_allocate_discount_proportional_and_exact():
    lines = [_line("big", 1000), _line("small", 100)]
    alloc = pe.allocate_discount(lines, 110.0)
    # 110 split over 1100 value -> 100 to big, 10 to small, exact.
    assert alloc["big"] == 100.0
    assert alloc["small"] == 10.0
    assert round(sum(alloc.values()), 2) == 110.0


def test_allocate_discount_zero_and_empty():
    assert pe.allocate_discount([_line("a", 1000)], 0.0) == {"a": 0.0}
    assert pe.allocate_discount([], 50.0) == {}


def test_allocate_discount_never_exceeds_line_value():
    # asking to allocate more than the cart is worth is capped at line value.
    lines = [_line("a", 100)]
    alloc = pe.allocate_discount(lines, 999.0)
    assert alloc["a"] == 100.0


# ===========================================================================
# F11 / F12 high-level layer: evaluate_promos(cart, customer, store, rules)
# ===========================================================================
# Best-promo selection across the new rule types, the OUTER cap clamp
# (category/luxury), EXCLUSIVE-by-default stacking, and the margin estimator.


def _item(pid, price, qty=1, brand=None, item_type=None, disc_cat="MASS",
          cost=None):
    return {
        "product_id": pid,
        "item_id": pid,
        "brand": brand,
        "item_type": item_type,
        "discount_category": disc_cat,
        "quantity": qty,
        "unit_price": float(price),
        "cost_at_sale": cost,
    }


# --- THRESHOLD ------------------------------------------------------------


def test_evaluate_threshold_fires_above_min_cart():
    cart = {"items": [_item("a", 1000)]}
    rule = {
        "promo_id": "T1", "name": "Spend 500 get 10%", "promo_type": "THRESHOLD",
        "reward_value": 10, "min_cart_value": 500,
    }
    out = pe.evaluate_promos(cart, None, None, [rule])
    assert out["applied"] is True
    assert out["total_discount"] == 100.0  # 10% of 1000
    assert out["fired"] == ["T1"]


def test_evaluate_threshold_below_min_does_not_fire():
    cart = {"items": [_item("a", 300)]}
    rule = {
        "promo_id": "T1", "name": "Spend 500 get 10%", "promo_type": "THRESHOLD",
        "reward_value": 10, "min_cart_value": 500,
    }
    out = pe.evaluate_promos(cart, None, None, [rule])
    assert out["applied"] is False
    assert out["total_discount"] == 0.0


# --- BOGO -----------------------------------------------------------------


def test_evaluate_bogo_raw_engine_picks_cheapest_unit():
    # The pure core (evaluate_cart) computes the RAW BOGO discount: buy 1 get 1
    # free on 2 frames at 2000 + 1000 -> the cheapest (1000) is free.
    promo = pe.Promo(promo_id="B1", kind=pe.PROMO_BOGO, percent=100,
                     buy_quantity=1, get_quantity=1)
    lines = [_line("a", 2000), _line("b", 1000)]
    res = pe.evaluate_cart(lines, [promo])
    assert res.total_discount == 1000.0  # cheaper unit free (pre-cap)


def test_evaluate_bogo_is_clamped_to_category_cap():
    # Through the F11/F12 layer the BOGO is clamped to the category cap (the
    # OUTER hardlock): a "free" MASS frame can't exceed the 15% cap. This is the
    # task's locked rule -- a promo NEVER breaches the category/luxury caps.
    cart = {"items": [_item("a", 2000, item_type="FRAME", disc_cat="MASS"),
                      _item("b", 1000, item_type="FRAME", disc_cat="MASS")]}
    rule = {
        "promo_id": "B1", "name": "BOGO frames", "promo_type": "BOGO",
        "buy_quantity": 1, "get_quantity": 1, "reward_value": 100,
    }
    out = pe.evaluate_promos(cart, None, None, [rule])
    assert out["applied"] is True
    # Raw 1000 free, but clamped: no line may be discounted beyond its 15% cap.
    assert out["total_discount"] <= 0.15 * 3000 + 0.01
    # Every per-line discount is within that line's cap.
    for lid, disc in out["per_line_discount"].items():
        # each MASS line value -> 15% ceiling
        line_val = 2000 if lid == "a" else 1000
        assert disc <= 0.15 * line_val + 0.01


# --- COMBO (cross-category bundle) ----------------------------------------


def test_evaluate_combo_requires_all_groups_present():
    # Watch + Sunglass bundle: 10% off when BOTH categories are in the cart.
    rule = {
        "promo_id": "C1", "name": "Watch+Sunglass", "promo_type": "COMBO",
        "reward_value": 10,
        "combo_groups": [{"item_type": "WATCH"}, {"item_type": "SUNGLASS"}],
    }
    # Only a watch -> does NOT fire.
    cart_one = {"items": [_item("w", 5000, item_type="WATCH", disc_cat="PREMIUM")]}
    assert pe.evaluate_promos(cart_one, None, None, [rule])["applied"] is False
    # Watch + Sunglass present -> fires, 10% off both (PREMIUM cap is 20% so OK).
    cart_two = {"items": [
        _item("w", 5000, item_type="WATCH", disc_cat="PREMIUM"),
        _item("s", 3000, item_type="SUNGLASS", disc_cat="PREMIUM"),
    ]}
    out = pe.evaluate_promos(cart_two, None, None, [rule])
    assert out["applied"] is True
    assert out["total_discount"] == 800.0  # 10% of 8000


# --- best-promo selection (EXCLUSIVE by default) --------------------------


def test_evaluate_exclusive_only_best_fires():
    cart = {"items": [_item("a", 1000)]}
    r10 = {"promo_id": "P10", "name": "10", "promo_type": "PERCENT", "reward_value": 10}
    r15 = {"promo_id": "P15", "name": "15", "promo_type": "PERCENT", "reward_value": 15}
    out = pe.evaluate_promos(cart, None, None, [r10, r15])
    # MASS cap is 15% -> 15% wins (150), 10% suppressed (single best fires).
    assert out["total_discount"] == 150.0
    assert out["fired"] == ["P15"]
    assert "P10" in out["suppressed"]


def test_evaluate_stackable_combines_when_opted_in():
    cart = {"items": [_item("a", 1000)]}
    s1 = {"promo_id": "S1", "name": "s1", "promo_type": "PERCENT",
          "reward_value": 5, "stackable": True}
    s2 = {"promo_id": "S2", "name": "s2", "promo_type": "PERCENT",
          "reward_value": 8, "stackable": True}
    out = pe.evaluate_promos(cart, None, None, [s1, s2])
    # 5% + 8% = 13% of 1000 = 130 (both stack; under the 15% MASS cap).
    assert out["total_discount"] == 130.0
    assert set(out["fired"]) == {"S1", "S2"}


# --- the OUTER cap clamp (category + luxury) ------------------------------


def test_evaluate_clamps_to_category_cap():
    # A LUXURY line (cap 5%) with a 30% promo is clamped to 5%.
    cart = {"items": [_item("a", 10000, disc_cat="LUXURY")]}
    rule = {"promo_id": "P", "name": "big", "promo_type": "PERCENT", "reward_value": 30}
    out = pe.evaluate_promos(cart, None, None, [rule])
    assert out["total_discount"] == 500.0  # 5% of 10000, not 30%


def test_evaluate_clamps_to_luxury_brand_cap():
    # Cartier (brand cap 2%) overrides even the LUXURY 5% category cap.
    cart = {"items": [_item("a", 10000, brand="Cartier", disc_cat="LUXURY")]}
    rule = {"promo_id": "P", "name": "big", "promo_type": "PERCENT", "reward_value": 30}
    out = pe.evaluate_promos(cart, None, None, [rule])
    assert out["total_discount"] == 200.0  # 2% of 10000


def test_evaluate_non_discountable_clamps_to_zero():
    cart = {"items": [_item("a", 10000, disc_cat="NON_DISCOUNTABLE")]}
    rule = {"promo_id": "P", "name": "big", "promo_type": "PERCENT", "reward_value": 30}
    out = pe.evaluate_promos(cart, None, None, [rule])
    assert out["total_discount"] == 0.0
    assert out["applied"] is False


def test_evaluate_max_discount_amount_ceiling():
    cart = {"items": [_item("a", 10000, disc_cat="PREMIUM")]}  # cap 20%
    rule = {"promo_id": "P", "name": "big", "promo_type": "PERCENT",
            "reward_value": 20, "max_discount_amount": 500}
    out = pe.evaluate_promos(cart, None, None, [rule])
    # 20% of 10000 = 2000, but capped at the 500 rupee ceiling.
    assert out["total_discount"] == 500.0


# --- empty / dark inputs --------------------------------------------------


def test_evaluate_no_rules_is_noop():
    cart = {"items": [_item("a", 1000)]}
    out = pe.evaluate_promos(cart, None, None, [])
    assert out["applied"] is False
    assert out["total_discount"] == 0.0
    assert out["per_line_discount"] == {}


def test_evaluate_empty_cart_is_noop():
    out = pe.evaluate_promos({"items": []}, None, None,
                            [{"promo_id": "P", "promo_type": "PERCENT",
                              "reward_value": 10, "name": "x"}])
    assert out["applied"] is False
    assert out["total_discount"] == 0.0


def test_evaluate_unknown_promo_type_skipped():
    cart = {"items": [_item("a", 1000)]}
    out = pe.evaluate_promos(cart, None, None,
                            [{"promo_id": "P", "promo_type": "MYSTERY",
                              "reward_value": 50, "name": "x"}])
    assert out["applied"] is False


# --- customer gating (fail-open) ------------------------------------------


def test_evaluate_first_purchase_only_excludes_returning_customer():
    cart = {"items": [_item("a", 1000)]}
    rule = {"promo_id": "P", "name": "welcome", "promo_type": "PERCENT",
            "reward_value": 10, "first_purchase_only": True}
    returning = {"total_orders": 5}
    assert pe.evaluate_promos(cart, returning, None, [rule])["applied"] is False
    new_cust = {"total_orders": 0}
    assert pe.evaluate_promos(cart, new_cust, None, [rule])["applied"] is True


# --- margin estimator -----------------------------------------------------


def test_estimate_margin_uses_cost_when_present():
    cart = {"items": [_item("a", 1000, cost=400)]}
    rule = {"promo_id": "P", "name": "10", "promo_type": "PERCENT", "reward_value": 10}
    ev = pe.evaluate_promos(cart, None, None, [rule])
    m = pe.estimate_margin_impact(cart, ev)
    assert m["estimated_cogs"] == 400.0
    assert m["cogs_is_estimated"] is False
    assert m["total_discount_given"] == 100.0


def test_estimate_margin_flags_estimated_cogs():
    cart = {"items": [_item("a", 1000)]}  # no cost_at_sale
    rule = {"promo_id": "P", "name": "10", "promo_type": "PERCENT", "reward_value": 10}
    ev = pe.evaluate_promos(cart, None, None, [rule])
    m = pe.estimate_margin_impact(cart, ev)
    assert m["cogs_is_estimated"] is True
    assert m["estimated_cogs"] == 600.0  # 60% fallback
