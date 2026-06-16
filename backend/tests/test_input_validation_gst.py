"""
BUG-108 residual input-validation + GST inter/intra state-normalization.

- _split_output_tax must classify intra- vs inter-state using the CANONICAL GST
  state code, so a sale whose store/customer states are stored in different
  formats ('Jharkhand' vs 'JH' vs '20') is not misclassified inter-state.
- BulkPriceRequest.amount and PaymentCreate.amount/emi_principal must reject
  NaN/Infinity (else NaN corrupts mrp/offer_price or inf lands on an order).
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402


def test_split_output_tax_normalizes_mixed_state_formats():
    from api.routers.finance import _split_output_tax

    orders = [{"store_id": "S1", "customer_id": "C1", "tax_amount": 100.0}]
    # Same state, different stored formats (full name vs 2-letter abbr) -> INTRA.
    cgst, sgst, igst = _split_output_tax(orders, {"S1": "Jharkhand"}, {"C1": "JH"})
    assert igst == 0.0, "full-name vs abbr (same state) must be intra-state"
    assert round(cgst + sgst, 2) == 100.0
    # Name vs 2-digit GST code, same state -> INTRA.
    _, _, igst2 = _split_output_tax(orders, {"S1": "Maharashtra"}, {"C1": "27"})
    assert igst2 == 0.0
    # Genuinely different states -> IGST.
    c3, s3, i3 = _split_output_tax(orders, {"S1": "Jharkhand"}, {"C1": "Maharashtra"})
    assert i3 == 100.0 and c3 == 0.0 and s3 == 0.0


def test_bulk_price_rejects_non_finite():
    from api.routers.products import BulkPriceRequest

    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(Exception):
            BulkPriceRequest(amount=bad)
    # Finite values (incl. a negative flat delta) still accepted.
    assert BulkPriceRequest(amount=-500.0).amount == -500.0
    assert BulkPriceRequest(amount=10.0).amount == 10.0


def test_payment_rejects_non_finite():
    from api.routers.orders import PaymentCreate

    for bad in (float("inf"), float("nan"), float("-inf")):
        with pytest.raises(Exception):
            PaymentCreate(method="CASH", amount=bad)
    with pytest.raises(Exception):
        PaymentCreate(method="CASH", amount=999.0, emi_principal=float("inf"))
    # A normal cash payment still validates.
    assert PaymentCreate(method="CASH", amount=999.0).amount == 999.0
