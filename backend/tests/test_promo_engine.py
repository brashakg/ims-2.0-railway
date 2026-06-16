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
