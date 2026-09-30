"""F35 cost & margin masking (#35) -- INTENT-LEVEL tests.

The intent: cost_price + every derived margin/COGS figure is stripped from an API
payload for any role not authorised to see cost. SUPERADMIN/ADMIN/ACCOUNTANT always
see it; the managers (area, store, catalogue) see per-unit product cost (the
"product" context, owner ruling 2026-09-28) but not operational aggregates;
counter roles never. No emoji.
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
    # The managers: per-unit product cost yes, operational aggregates no.
    for r in ("AREA_MANAGER", "STORE_MANAGER", "CATALOG_MANAGER"):
        assert can_see_cost(_u(r)) is False, r
        assert can_see_cost(_u(r), context="product") is True, r
    # Counter roles: never, in any context (owner ruling D7).
    for r in ("OPTOMETRIST", "SALES_CASHIER", "SALES_STAFF", "CASHIER",
              "WORKSHOP_STAFF"):
        for ctx in ("default", "product", "purchase"):
            assert can_see_cost(_u(r), context=ctx) is False, (r, ctx)
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


def test_catalog_manager_product_cost_vs_operational():
    # a product read -> sees the per-unit cost
    edit = mask_cost(_product(), _u("CATALOG_MANAGER"), context="product")
    assert edit["cost_price"] == 2200
    # an operational aggregate (default context) -> stripped
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


# --------------------------------------------------------------- one rule, one place


# A router that decides who sees cost with its own role list is how this rule
# drifted: /catalog/products said no to the managers while /products said yes,
# and the purchase recommendations handed cost to the counter. Every router
# asks can_see_cost / mask_cost here. This guard fails when a file under
# api/routers (a) binds role names to a name that says COST or MARGIN, or
# (b) strips a raw cost field by hand (x.pop("cost_price"), del x["cost_price"],
# or a pop / del inside a loop over a literal list naming one).
import ast  # noqa: E402
from pathlib import Path  # noqa: E402

from api.services import cost_mask as _cost_mask  # noqa: E402
from api.services.rbac_policy import ALL_ROLES  # noqa: E402

_ROUTERS = Path(__file__).resolve().parents[1] / "api" / "routers"
_ROLE_NAMES = set(ALL_ROLES) | {"INVESTOR"}
_RAW_COST = _cost_mask._COST_FIELDS


def _consts(node):
    return {n.value for n in ast.walk(node) if isinstance(n, ast.Constant)}


def _is_strip(node):
    """A `del ...` or an `x.pop(...)` call."""
    return isinstance(node, ast.Delete) or (
        isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "pop"
    )


def _own_cost_rules(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [t.id.upper() for t in targets if isinstance(t, ast.Name)]
            if any("COST" in n or "MARGIN" in n for n in names) and (
                _consts(node.value) & _ROLE_NAMES
            ):
                yield node.lineno, "role set named for cost"
        if _is_strip(node):
            args = node.targets if isinstance(node, ast.Delete) else node.args[:1]
            for arg in args:
                key = arg.slice if isinstance(arg, ast.Subscript) else arg
                if isinstance(key, ast.Constant) and key.value in _RAW_COST:
                    yield node.lineno, f"hand-stripped {key.value}"
        if (
            isinstance(node, ast.For)
            and isinstance(node.iter, (ast.Tuple, ast.List, ast.Set))
            and _consts(node.iter) & _RAW_COST
            and any(_is_strip(n) for stmt in node.body for n in ast.walk(stmt))
        ):
            yield node.lineno, "hand-stripped cost fields in a loop"


def test_no_router_keeps_its_own_cost_rule():
    found = sorted(
        {
            (path.relative_to(_ROUTERS).as_posix(), line, why)
            for path in _ROUTERS.rglob("*.py")
            for line, why in _own_cost_rules(ast.parse(path.read_text(encoding="utf-8")))
        }
    )
    assert not found, (
        "cost visibility is decided in services/cost_mask.py only -- call "
        f"can_see_cost / mask_cost there instead: {found}"
    )


def test_the_guard_sees_a_router_local_cost_rule():
    """The guard is not vacuous: each shape it forbids trips it."""
    for src in (
        '_COST_ROLES = ("ADMIN", "ACCOUNTANT")',
        'if x:\n    row.pop("cost_price", None)',
        'del row["landed_cost"]',
        'for f in ("cost_price", "mrp"):\n    row.pop(f, None)',
    ):
        assert list(_own_cost_rules(ast.parse(src))), src
    assert not list(_own_cost_rules(ast.parse('row.pop("created_by", None)')))
