"""
Test that store-credit operations reject Infinity/NaN amounts.
BUG-108: Pydantic v2 accepts Infinity by default; the fix hardens
both the request model and make_entry helper.
"""

from __future__ import annotations

import os
import sys
import math
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-storecredit-infinity")


def test_storecreditentryrequest_rejects_infinity():
    """Pydantic model should reject Infinity in the amount field."""
    from pydantic import ValidationError
    from api.routers.customers import StoreCreditEntryRequest

    # Test Infinity
    with pytest.raises(ValidationError) as exc_info:
        StoreCreditEntryRequest(amount=float('inf'), reason="Attack")
    assert "Infinity" in str(exc_info.value) or "finite" in str(exc_info.value)

    # Test -Infinity
    with pytest.raises(ValidationError) as exc_info:
        StoreCreditEntryRequest(amount=float('-inf'), reason="Attack")
    assert "Infinity" in str(exc_info.value) or "finite" in str(exc_info.value)

    # Test NaN
    with pytest.raises(ValidationError) as exc_info:
        StoreCreditEntryRequest(amount=float('nan'), reason="Attack")
    assert "NaN" in str(exc_info.value) or "finite" in str(exc_info.value)


def test_storecreditentryrequest_accepts_normal_floats():
    """Normal finite floats should still be accepted."""
    from api.routers.customers import StoreCreditEntryRequest

    req = StoreCreditEntryRequest(amount=100.50, reason="Legit credit")
    assert req.amount == 100.50

    req = StoreCreditEntryRequest(amount=0.01, reason="One paise")
    assert req.amount == 0.01

    req = StoreCreditEntryRequest(amount=999999.99, reason="Large amount")
    assert req.amount == 999999.99


def test_make_entry_rejects_infinity_amount():
    """make_entry should reject Infinity in the amount parameter."""
    from api.services import store_credit_ledger as scl

    # Test ISSUED with Infinity
    with pytest.raises(ValueError) as exc_info:
        scl.make_entry(
            customer_id="test-cust",
            entry_type="ISSUED",
            amount=float('inf'),
            current_balance=0.0,
        )
    assert "finite" in str(exc_info.value)

    # Test REDEEMED with -Infinity
    with pytest.raises(ValueError) as exc_info:
        scl.make_entry(
            customer_id="test-cust",
            entry_type="REDEEMED",
            amount=float('-inf'),
            current_balance=100.0,
        )
    assert "finite" in str(exc_info.value)

    # Test ADJUSTED with NaN
    with pytest.raises(ValueError) as exc_info:
        scl.make_entry(
            customer_id="test-cust",
            entry_type="ADJUSTED",
            amount=float('nan'),
            current_balance=50.0,
        )
    assert "finite" in str(exc_info.value)


def test_make_entry_rejects_infinity_balance():
    """make_entry should reject Infinity in the current_balance parameter."""
    from api.services import store_credit_ledger as scl

    # Test with infinite current_balance
    with pytest.raises(ValueError) as exc_info:
        scl.make_entry(
            customer_id="test-cust",
            entry_type="ISSUED",
            amount=100.0,
            current_balance=float('inf'),
        )
    assert "finite" in str(exc_info.value)

    # Test REDEEMED with NaN balance
    with pytest.raises(ValueError) as exc_info:
        scl.make_entry(
            customer_id="test-cust",
            entry_type="REDEEMED",
            amount=50.0,
            current_balance=float('nan'),
        )
    assert "finite" in str(exc_info.value)


def test_make_entry_still_accepts_normal_floats():
    """Normal finite values should still work after the guard."""
    from api.services import store_credit_ledger as scl

    # Test ISSUED with normal values
    entry = scl.make_entry(
        customer_id="cust-123",
        entry_type="ISSUED",
        amount=500.00,
        current_balance=0.0,
        reason="Store return credit",
    )
    assert entry["amount"] == 500.00
    assert entry["delta"] == 500.00
    assert entry["balance_after"] == 500.00
    assert math.isfinite(entry["balance_after"])

    # Test REDEEMED
    entry = scl.make_entry(
        customer_id="cust-123",
        entry_type="REDEEMED",
        amount=200.00,
        current_balance=500.00,
    )
    assert entry["delta"] == -200.00
    assert entry["balance_after"] == 300.00

    # Test ADJUSTED with negative value
    entry = scl.make_entry(
        customer_id="cust-123",
        entry_type="ADJUSTED",
        amount=-50.00,
        current_balance=300.00,
    )
    assert entry["delta"] == -50.00
    assert entry["balance_after"] == 250.00


def test_edge_case_very_large_finite_float():
    """Very large but finite floats should be accepted and rejected properly later."""
    from api.services import store_credit_ledger as scl

    # A very large but finite number should pass the isfinite check
    large = 1e308  # Close to float max
    entry = scl.make_entry(
        customer_id="cust-big",
        entry_type="ISSUED",
        amount=large,
        current_balance=0.0,
    )
    # Should be accepted since it's finite
    assert math.isfinite(entry["balance_after"])
