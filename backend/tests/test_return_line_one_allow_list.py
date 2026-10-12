"""Every return line -- a vendor return's item and an RMA's line -- is cut by
ONE allow-list (cost_mask.return_line_for_reader, owner ruling 2026-10-12 R3).

Before: vendor returns used cost_mask's allow-list while RMAs used a separate
deny-list of price keys in payables_mask (_RETURN_LINE_PRICE), so a price
stored under a new name was hidden on a vendor return and shown on an RMA.
Now a manager reads item, quantity and reason on both, whatever else a line
carries, and narrowing the one allow-list moves both.
"""
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from api.services import cost_mask  # noqa: E402
from api.services.payables_mask import strip_rma_credit  # noqa: E402

MANAGERS = ("STORE_MANAGER", "AREA_MANAGER", "WORKSHOP_STAFF")
ACCOUNTS = ("ACCOUNTANT", "ADMIN", "SUPERADMIN")
LINE_KEYS = {"product_id", "product_name", "quantity", "reason"}


def _user(role):
    return {"user_id": f"u-{role}", "roles": [role], "store_ids": ["S1"],
            "active_store_id": "S1"}


def _line(**price):
    return {"product_id": "P1", "product_name": "Lens", "quantity": 2,
            "reason": "DEFECTIVE", **price}


def _rma():
    # A price under every name the code knows, plus two it does not.
    return {"rma_id": "R1", "store_id": "S1", "status": "DRAFT",
            "lines": [_line(unit_cost_paise=150000, line_expected_paise=300000,
                            unit_cost=1500.0, list_price_paise=200000, mrp=2500.0)]}


def _vendor_return():
    return {"return_id": "VR1", "store_id": "S1", "status": "created",
            "items": [_line(unit_price=1500.0, list_price_paise=200000, mrp=2500.0)]}


@pytest.mark.parametrize("role", MANAGERS)
def test_a_manager_reads_item_quantity_and_reason_on_both_kinds_of_return(role):
    user = _user(role)
    rma_lines = strip_rma_credit(_rma(), user)["lines"]
    ret_items = cost_mask.mask_vendor_return(_vendor_return(), user)["items"]
    assert [set(ln) for ln in rma_lines] == [LINE_KEYS]
    assert [set(it) for it in ret_items] == [LINE_KEYS]
    assert rma_lines == ret_items


@pytest.mark.parametrize("role", ACCOUNTS)
def test_the_accounts_roles_read_every_price(role):
    user = _user(role)
    assert strip_rma_credit(_rma(), user) == _rma()
    assert cost_mask.mask_vendor_return(_vendor_return(), user) == _vendor_return()


def test_narrowing_the_one_allow_list_moves_both(monkeypatch):
    user = _user("STORE_MANAGER")
    monkeypatch.setattr(
        cost_mask, "_RETURN_ITEM_KEYS",
        tuple(k for k in cost_mask._RETURN_ITEM_KEYS if k != "reason"),
    )
    rma_line = strip_rma_credit(_rma(), user)["lines"][0]
    ret_item = cost_mask.mask_vendor_return(_vendor_return(), user)["items"][0]
    assert "reason" not in rma_line and "reason" not in ret_item
    assert rma_line == ret_item == {"product_id": "P1", "product_name": "Lens", "quantity": 2}
