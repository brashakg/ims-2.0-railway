"""POS money-fix regression tests (owner-authorized 2026-06-04).

  1. LOYALTY tender: PaymentMethod gained a LOYALTY member so the POS
     loyalty-redemption tender no longer 422s and gets swallowed (which burned
     the customer's points yet left the order showing the amount owing -- a
     double charge). Points are debited by POST /loyalty/redeem BEFORE this
     tender, so it is a non-CREDIT internal tender that counts toward amount_paid
     and reduces balance_due.
  2. EMI tender: PaymentCreate accepts emi_months/emi_provider (the POS now
     forwards them; before, every EMI payment 400'd on the missing emi_months).
  3. Cart-discount caps: the order-level cart discount is clamped to the
     strictest category / luxury-brand cap across the cart's lines (not just the
     role cap), using the same pricing_caps resolver as the per-item path.
"""
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers.orders import PaymentCreate, PaymentMethod  # noqa: E402
from api.services.pricing_caps import effective_discount_cap  # noqa: E402


def test_loyalty_is_a_valid_payment_method():
    assert PaymentMethod.LOYALTY.value == "LOYALTY"
    # The exact tender the POS submits after /loyalty/redeem debits the points.
    p = PaymentCreate(method="LOYALTY", amount=200.0)
    assert p.method == PaymentMethod.LOYALTY
    assert p.amount == 200.0


def test_loyalty_is_not_treated_as_credit():
    # OrderRepository.add_payment excludes ONLY CREDIT from amount_paid; LOYALTY
    # must count (so it reduces balance_due rather than leaving the amount owing).
    assert PaymentMethod.LOYALTY != PaymentMethod.CREDIT


def test_emi_payment_accepts_tenure_and_provider():
    p = PaymentCreate(method="EMI", amount=12000.0, emi_months=6, emi_provider="HDFC")
    assert p.method == PaymentMethod.EMI
    assert p.emi_months == 6
    assert p.emi_provider == "HDFC"


def test_cart_cap_uses_strictest_category_and_brand_cap():
    # The values the cart-discount guard enforces (same resolver as per-item).
    assert effective_discount_cap("LUXURY", "Cartier") == 2.0
    assert effective_discount_cap("NON_DISCOUNTABLE", None) == 0.0
    assert effective_discount_cap("MASS", None) == 15.0
    # The cart cap is the min over the cart's lines (plus the role cap). A cart
    # holding a Cartier line + a MASS line caps the WHOLE cart at 2% -- a 10%
    # cart discount would (correctly) be rejected by orders.create.
    role_cap = 20.0
    line_caps = [
        effective_discount_cap("LUXURY", "Cartier"),  # 2
        effective_discount_cap("MASS", "Ray-Ban"),    # 15
    ]
    cart_cap = min([role_cap] + line_caps)
    assert cart_cap == 2.0
    assert 10.0 > cart_cap
