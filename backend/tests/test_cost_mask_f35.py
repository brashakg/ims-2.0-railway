"""F35 cost & margin masking (#35) -- INTENT-LEVEL tests.

The intent: cost_price + every derived margin/COGS figure is stripped from an API
payload for any role not authorised to see cost. SUPERADMIN/ADMIN/ACCOUNTANT always
see it; CATALOG_MANAGER only in the product-edit form (catalog_edit context);
AREA_MANAGER and below never. No emoji.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services.cost_mask import (  # noqa: E402
    can_see_cost, mask_cost, mask_cost_list, COST_VISIBLE_ROLES,
)


def _u(*roles):
    return {"roles": list(roles), "user_id": "u"}


# --------------------------------------------------------------- role matrix


def test_can_see_cost_role_matrix():
    assert can_see_cost(_u("SUPERADMIN")) is True
    assert can_see_cost(_u("ADMIN")) is True
    assert can_see_cost(_u("ACCOUNTANT")) is True
    # CATALOG_MANAGER: only in the edit-form context
    assert can_see_cost(_u("CATALOG_MANAGER")) is False
    assert can_see_cost(_u("CATALOG_MANAGER"), context="catalog_edit") is True
    # AREA_MANAGER and below: never (DECISIONS sec 9)
    for r in ("AREA_MANAGER", "STORE_MANAGER", "OPTOMETRIST", "SALES_CASHIER",
              "SALES_STAFF", "WORKSHOP_STAFF"):
        assert can_see_cost(_u(r)) is False, r
        assert can_see_cost(_u(r), context="catalog_edit") is False, r
    # activeRole fallback (no roles[] list)
    assert can_see_cost({"activeRole": "ADMIN"}) is True
    assert can_see_cost({"activeRole": "SALES_CASHIER"}) is False


def test_cost_visible_roles_excludes_area_manager():
    # G1 + DECISIONS sec 9: AREA_MANAGER must NOT see cost.
    assert "AREA_MANAGER" not in COST_VISIBLE_ROLES
    assert COST_VISIBLE_ROLES == {"SUPERADMIN", "ADMIN", "ACCOUNTANT"}


# --------------------------------------------------------------- field stripping


def _product():
    return {
        "product_id": "P1", "name": "Ray-Ban", "mrp": 5000, "offer_price": 4500,
        "cost_price": 2200, "margin_pct": 51.1, "cost_value": 2200,
        "pricing": {"mrp": 5000, "cost_price": 2200, "offer_price": 4500},
    }


def test_sales_cashier_sees_no_cost_or_margin():
    masked = mask_cost(_product(), _u("SALES_CASHIER"))
    assert "cost_price" not in masked
    assert "margin_pct" not in masked
    assert "cost_value" not in masked
    assert "cost_price" not in masked["pricing"]   # nested stripped too
    # non-cost fields survive
    assert masked["mrp"] == 5000 and masked["offer_price"] == 4500
    assert masked["pricing"]["mrp"] == 5000


def test_accountant_sees_real_cost():
    doc = mask_cost(_product(), _u("ACCOUNTANT"))
    assert doc["cost_price"] == 2200
    assert doc["margin_pct"] == 51.1
    assert doc["pricing"]["cost_price"] == 2200


def test_catalog_manager_edit_form_vs_operational():
    # edit form -> sees cost
    edit = mask_cost(_product(), _u("CATALOG_MANAGER"), context="catalog_edit")
    assert edit["cost_price"] == 2200
    # operational list (default context) -> stripped
    op = mask_cost(_product(), _u("CATALOG_MANAGER"))
    assert "cost_price" not in op and "cost_price" not in op["pricing"]


def test_mask_cost_list_pages():
    docs = [_product(), _product(), {"not_a_dict": True}]  # tolerant of odd entries
    out = mask_cost_list(docs, _u("STORE_MANAGER"))
    assert all("cost_price" not in d for d in out[:2])
    assert mask_cost_list(docs, _u("ADMIN")) is docs  # privileged -> untouched (same ref)


# --------------------------------------------------------------- P&L (finance G1)


def test_pnl_strip_logic_mirrors_endpoint():
    # The finance.py /pnl handler strips this exact set when not can_see_cost.
    pnl = {"revenue": 100000, "cogs": 60000, "gross_profit": 40000, "gross_margin": 40.0,
           "net_profit": 25000, "net_margin": 25.0, "payroll_cost": 8000,
           "cogs_is_estimated": False, "cogs_estimated_lines": 0, "cogs_total_lines": 50,
           "tax_collected": 5000, "total_expenses": 7000}
    strip = ("cogs", "cogs_is_estimated", "cogs_estimated_lines", "cogs_total_lines",
             "gross_profit", "gross_margin", "net_profit", "net_margin", "payroll_cost")
    cashier = dict(pnl)
    if not can_see_cost(_u("SALES_CASHIER")):
        for f in strip:
            cashier.pop(f, None)
    assert "gross_margin" not in cashier and "cogs" not in cashier
    assert "net_profit" not in cashier
    assert cashier["revenue"] == 100000 and cashier["tax_collected"] == 5000  # top line stays
    # ACCOUNTANT keeps everything
    assert can_see_cost(_u("ACCOUNTANT")) is True
