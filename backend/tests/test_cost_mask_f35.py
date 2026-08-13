"""F35 cost & margin masking (#35) -- INTENT-LEVEL tests.

The intent: cost_price + every derived margin/COGS figure is stripped from an API
payload for any role not authorised to see cost. SUPERADMIN/ADMIN/ACCOUNTANT always
see it; CATALOG_MANAGER only in the product-edit form (catalog_edit context);
AREA_MANAGER and below never. No emoji.
"""
import os
import sys

import pytest

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


# WHAT USED TO BE HERE, AND WHY IT WAS WORTHLESS
# ----------------------------------------------
# `test_pnl_strip_logic_mirrors_endpoint` never imported finance. It declared its
# OWN copy of the strip tuple, popped from that copy in the test body, and then
# asserted the copy no longer had the keys it had just popped -- true by
# construction, incapable of failing. The auditor mutated the REAL guard in
# finance.py to `if False:` and 56 tests stayed green while a STORE_MANAGER
# received payroll_cost=47777.0.
#
# The replacement below CALLS THE ENDPOINT and asserts on the RESPONSE. The full
# role matrix, the salary gate and the arithmetic-recovery search live in
# tests/test_salary_aggregate_leak.py; this one stays here because this file is
# what a reader looking for "the cost mask test" opens.


class _EmptyCol:
    def find(self, *a, **k):
        return []

    def aggregate(self, *a, **k):
        return []


class _EmptyDB:
    def get_collection(self, _name):
        return _EmptyCol()


def _pnl_body(role, monkeypatch):
    """Drive the real GET /pnl handler as `role` and return the parsed body."""
    import os as _os

    _os.environ.setdefault("JWT_SECRET_KEY", "test-secret-cost-mask")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routers import finance
    from api.routers.auth import get_current_user

    monkeypatch.setattr(finance, "_get_db", lambda: _EmptyDB())
    monkeypatch.setattr(finance, "_cost_by_product", lambda _db: {})
    monkeypatch.setattr(finance, "_payroll_cost", lambda *a, **k: 8000.0)

    app = FastAPI()
    app.include_router(finance.router, prefix="/api/v1/finance")

    async def _user():
        return {
            "user_id": "u1",
            "roles": [role],
            "store_ids": ["S1"],
            "active_store_id": "S1",
        }

    app.dependency_overrides[get_current_user] = _user
    r = TestClient(app).get("/api/v1/finance/pnl?store_id=S1")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize(
    "role", ["SALES_CASHIER", "SALES_STAFF", "STORE_MANAGER", "AREA_MANAGER"]
)
def test_pnl_endpoint_strips_cost_for_roles_without_the_cost_grant(role, monkeypatch):
    body = _pnl_body(role, monkeypatch)
    for field in ("cogs", "gross_profit", "gross_margin", "cogs_is_estimated"):
        assert field not in body, f"{role} received {field}"
    # Top line stays -- the mask must not blank the revenue panel.
    assert "revenue" in body and "tax_collected" in body


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN", "ACCOUNTANT"])
def test_pnl_endpoint_keeps_cost_for_the_cost_grant(role, monkeypatch):
    body = _pnl_body(role, monkeypatch)
    for field in ("cogs", "gross_profit", "gross_margin"):
        assert field in body, f"{role} lost {field}"


def test_pnl_endpoint_payroll_answers_to_the_salary_gate_not_the_cost_gate(monkeypatch):
    """ACCOUNTANT passes can_see_cost and must STILL not get the wage bill: cost
    and pay are different secrets with different gates (owner ruling
    2026-08-09). This is the assertion the hollow test could never make, because
    it never called anything."""
    assert can_see_cost(_u("ACCOUNTANT")) is True
    body = _pnl_body("ACCOUNTANT", monkeypatch)
    for field in ("payroll_cost", "net_profit", "net_margin"):
        assert field not in body, f"ACCOUNTANT received {field}={body.get(field)}"
    assert _pnl_body("ADMIN", monkeypatch)["payroll_cost"] == 8000.0
