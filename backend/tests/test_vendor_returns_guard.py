"""
IMS 2.0 — vendor-return (debit note) RBAC + input validation
=============================================================
A vendor return mints a debit/credit note -- a financial instrument against a
vendor. create + status-change were gated only by Depends(get_current_user), so
ANY authenticated user (down to a cashier) could create debit notes; and the
return items had an unbounded `quantity: int` / `unit_price: float`, so a
zero/negative qty produced a bogus (or negative) debit-note amount.
"""

from __future__ import annotations

import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-vendor-returns")


def _dep_names(func):
    names = []
    for p in inspect.signature(func).parameters.values():
        dep = getattr(p.default, "dependency", None)
        if dep is not None:
            names.append(getattr(dep, "__name__", repr(dep)))
    return names


def test_create_is_role_gated_not_plain_auth():
    from api.routers.vendor_returns import create_vendor_return

    assert "get_current_user" not in _dep_names(create_vendor_return)
    assert _dep_names(create_vendor_return)  # carries a role-gated dependency


def test_status_update_is_role_gated_not_plain_auth():
    from api.routers.vendor_returns import update_return_status

    assert "get_current_user" not in _dep_names(update_return_status)
    assert _dep_names(update_return_status)


def test_role_set_excludes_junior_roles():
    from api.routers.vendor_returns import _VENDOR_RETURN_ROLES

    for junior in ("SALES_CASHIER", "SALES_STAFF", "WORKSHOP_STAFF", "OPTOMETRIST"):
        assert junior not in _VENDOR_RETURN_ROLES
    for ap in ("ADMIN", "ACCOUNTANT", "STORE_MANAGER", "AREA_MANAGER"):
        assert ap in _VENDOR_RETURN_ROLES


def test_return_item_rejects_zero_and_negative_quantity():
    from pydantic import ValidationError

    from api.routers.vendor_returns import ReturnItemCreate

    for bad in (0, -3):
        with pytest.raises(ValidationError):
            ReturnItemCreate(
                product_id="P1", product_name="Frame", quantity=bad,
                reason="defective", unit_price=100.0,
            )


def test_return_item_rejects_negative_price():
    from pydantic import ValidationError

    from api.routers.vendor_returns import ReturnItemCreate

    with pytest.raises(ValidationError):
        ReturnItemCreate(
            product_id="P1", product_name="Frame", quantity=1,
            reason="defective", unit_price=-50.0,
        )


def test_valid_return_item_accepted():
    from api.routers.vendor_returns import ReturnItemCreate

    it = ReturnItemCreate(
        product_id="P1", product_name="Frame", quantity=2,
        reason="defective", unit_price=100.0,
    )
    assert it.quantity == 2 and it.unit_price == 100.0
