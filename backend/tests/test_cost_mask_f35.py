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
# and the purchase recommendations handed cost to the counter. Every router and
# service asks can_see_cost / mask_cost here. This guard reads every file under
# api/routers AND api/services and fails on:
#   (a) role names bound to a name that says COST or MARGIN;
#   (b) a cost field stripped by hand -- x.pop("cost_price"), del x[...], a pop /
#       del in a loop over a literal list naming one, a key filter against a
#       collection naming one (`k not in _hide`, a dict comprehension's shape),
#       or a Mongo projection / $project that sets one to 0;
#   (c) a function that decides by role -- a role name, a role set of ANY name
#       declared at module level under api/routers or api/services, or a call
#       to a role predicate (a function answering True / False from roles) --
#       and touches a cost / margin field without calling cost_mask. That is
#       also the shape of an allow-list pick: the function holding the cost is
#       the one that would pick it out.
# Not a mask: `if <role test>: raise ...` and require_roles(...) -- those are
# gates, held by rbac_policy and its tests.
# Ceiling: a role test in one function whose hand-made projection lives in
# another that names no cost field is not seen; the per-role differential tests
# (test_counter_roles_no_purchase_reads) hold those routes.
import ast  # noqa: E402
import functools  # noqa: E402
from pathlib import Path  # noqa: E402

from api.services import cost_mask as _cost_mask  # noqa: E402
from api.services.rbac_policy import ALL_ROLES  # noqa: E402

_API = Path(__file__).resolve().parents[1] / "api"
_ROLE_NAMES = set(ALL_ROLES) | {"INVESTOR"}
_RAW_COST = _cost_mask._COST_FIELDS
_ANY_COST = _cost_mask._ALL_MASKED
_MASKERS = {
    n for n, v in vars(_cost_mask).items()
    if callable(v) and getattr(v, "__module__", "") == _cost_mask.__name__
}
# Writes that stamp cost onto a sale line under a role-decided discount cap:
# the cost is stored, never shown. (path under api/, function)
_COST_WRITES = {
    ("routers/orders/create.py", "create_order"),
    ("routers/orders/items.py", "add_order_item"),
}


def _consts(node):
    return {n.value for n in ast.walk(node) if isinstance(n, ast.Constant)}


def _ref(node):
    return node.id if isinstance(node, ast.Name) else getattr(node, "attr", None)


def _called(node):
    return _ref(node.func) if isinstance(node, ast.Call) else None


def _is_strip(node):
    """A `del ...` or an `x.pop(...)` call."""
    return isinstance(node, ast.Delete) or (
        isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "pop"
    )


def _bound(nodes, pool):
    """Names assigned a value that names a member of `pool`."""
    out = set()
    for n in nodes:
        if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None:
            if _consts(n.value) & pool:
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                out |= {t.id for t in targets if isinstance(t, ast.Name)}
    return out


@functools.lru_cache(maxsize=None)
def _nodes(tree):
    """Every node of the tree, walked once."""
    return tuple(ast.walk(tree))


def _functions(tree):
    return [n for n in _nodes(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _is_gate(node):
    return _called(node) in ("require_roles", "Depends") or (
        isinstance(node, ast.If)
        and not node.orelse
        and all(isinstance(b, ast.Raise) for b in node.body)
    )


@functools.lru_cache(maxsize=None)
def _body(fn):
    """The function body's nodes, minus gates (require_roles / Depends calls and
    `if <test>: raise` blocks)."""
    nodes = [n for stmt in fn.body for n in ast.walk(stmt)]
    skip = {id(x) for g in nodes if _is_gate(g) for x in ast.walk(g)}
    return tuple(n for n in nodes if id(n) not in skip)


def _decides(nodes, role_sets, preds):
    return any(
        (isinstance(n, ast.Constant) and n.value in _ROLE_NAMES)
        or _ref(n) in role_sets
        or _called(n) in preds
        for n in nodes
    )


def _answers_bool(fn):
    rets = [n.value for n in ast.walk(fn) if isinstance(n, ast.Return)]
    return bool(rets) and all(
        isinstance(v, (ast.Compare, ast.BoolOp))
        or (isinstance(v, ast.UnaryOp) and isinstance(v.op, ast.Not))
        or _called(v) in ("any", "all", "bool")
        or (isinstance(v, ast.Constant) and isinstance(v.value, bool))
        for v in rets
    )


def _role_context(trees):
    """(role sets of any name, role predicates) declared across `trees`."""
    role_sets = set().union(set(), *(_bound(t.body, _ROLE_NAMES) for t in trees))
    preds = {
        fn.name
        for t in trees
        for fn in _functions(t)
        if _answers_bool(fn) and _decides(_body(fn), role_sets, ())
    }
    return role_sets, preds


def _own_cost_rules(tree, context=None):
    role_sets, preds = context or _role_context([tree])
    cost_sets = _bound(_nodes(tree), _ANY_COST)
    for fn in _functions(tree):
        nodes = _body(fn)
        if (
            _decides(nodes, role_sets, preds - {fn.name})
            and any(
                (isinstance(n, ast.Constant) and n.value in _ANY_COST)
                or _ref(n) in cost_sets
                for n in nodes
            )
            and not any(_called(n) in _MASKERS for n in nodes)
        ):
            yield fn.lineno, f"{fn.name} decides by role and touches cost"
    for node in _nodes(tree):
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
        if isinstance(node, ast.Compare) and any(
            isinstance(o, (ast.In, ast.NotIn)) for o in node.ops
        ):
            if any(
                (isinstance(c, (ast.Tuple, ast.List, ast.Set)) and _consts(c) & _ANY_COST)
                or _ref(c) in cost_sets
                for c in node.comparators
            ):
                yield node.lineno, "keys filtered against cost fields"
        projections = []
        if _called(node) in ("find", "find_one", "find_many"):
            projections = node.args[1:2] + [
                k.value for k in node.keywords if k.arg == "projection"
            ]
        if isinstance(node, ast.Dict):
            projections += [
                v
                for k, v in zip(node.keys, node.values)
                if isinstance(k, ast.Constant) and k.value == "$project"
            ]
        for proj in projections:
            if isinstance(proj, ast.Dict) and any(
                isinstance(k, ast.Constant)
                and k.value in _ANY_COST
                and isinstance(v, ast.Constant)
                and v.value in (0, False)
                for k, v in zip(proj.keys, proj.values)
            ):
                yield node.lineno, "cost projected out"


@functools.lru_cache(maxsize=None)
def _api_trees():
    return {
        path.relative_to(_API).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
        for folder in ("routers", "services")
        for path in (_API / folder).rglob("*.py")
        if path.name != "cost_mask.py"
    }


@functools.lru_cache(maxsize=None)
def _api_context():
    return _role_context(list(_api_trees().values()))


def test_no_router_or_service_keeps_its_own_cost_rule():
    trees = _api_trees()
    assert "routers/reports/purchase.py" in trees
    assert "services/rtv_debit_note.py" in trees
    found = sorted(
        (path, line, why)
        for path, tree in trees.items()
        for line, why in _own_cost_rules(tree, _api_context())
        if (path, why.split()[0]) not in _COST_WRITES
    )
    assert not found, (
        "cost visibility is decided in services/cost_mask.py only -- call "
        f"can_see_cost / mask_cost there instead: {found}"
    )


def test_the_cost_write_exemptions_are_still_needed():
    """The exemption list only shrinks: each entry still trips the guard."""
    trees = _api_trees()
    for path, fn in _COST_WRITES:
        whys = {why.split()[0] for _l, why in _own_cost_rules(trees[path], _api_context())}
        assert fn in whys, (path, fn)


# The panel's mutation of routers/reports/purchase.py, verbatim in shape.
_PANEL_MUTATION = """
_BUYERS = {"SUPERADMIN", "ADMIN", "ACCOUNTANT", "AREA_MANAGER", "STORE_MANAGER"}
_hide = {"cost_price", "unit_margin", "estimated_purchase_cost"}

def recommendations(current_user, recs):
    if not set(current_user.get("roles") or []) & _BUYERS:
        recs = [{k: v for k, v in r.items() if k not in _hide} for r in recs]
    return recs
"""

_ALLOW_LIST_PICK = """
_INSIGHT_ROLES = ("ADMIN", "AREA_MANAGER")

def report(user, rows):
    spend = sum(r["landed_cost"] for r in rows)
    if set(user["roles"]) & _INSIGHT_ROLES:
        return {"rows": rows, "spend": spend}
    return {"rows": [{k: r[k] for k in ("sku", "mrp")} for r in rows]}
"""


@pytest.mark.parametrize(
    "src",
    [
        '_COST_ROLES = ("ADMIN", "ACCOUNTANT")',
        'if x:\n    row.pop("cost_price", None)',
        'del row["landed_cost"]',
        'for f in ("cost_price", "mrp"):\n    row.pop(f, None)',
        _PANEL_MUTATION,
        _ALLOW_LIST_PICK,
        'rows = [{k: v for k, v in r.items() if k not in ("cost_price", "mrp")} for r in rs]',
        'docs = db.products.find({}, {"cost_price": 0, "_id": 0})',
        'pipe = [{"$project": {"landed_cost": 0}}]',
        'def h(user):\n    out = {}\n    if "ADMIN" in user["roles"]:\n        out["cogs"] = 5\n    return out',
    ],
)
def test_the_guard_sees_a_router_local_cost_rule(src):
    """The guard is not vacuous: each shape it forbids trips it."""
    assert list(_own_cost_rules(ast.parse(src))), src


def test_the_guard_sees_a_role_predicate_from_another_module():
    """A predicate declared elsewhere under api/ (salary_visibility's
    is_salary_admin) is a role decision too."""
    src = (
        "def h(user, out):\n"
        "    if is_salary_admin(user):\n"
        '        out["net_margin"] = 1\n'
        "    return out"
    )
    assert list(_own_cost_rules(ast.parse(src), _api_context())), src


@pytest.mark.parametrize(
    "src",
    [
        'row.pop("created_by", None)',
        # a gate, not a mask
        'def h(user, p):\n    if "ADMIN" not in user["roles"]:\n        raise E()\n'
        '    return p["cost_price"]',
        # asks cost_mask
        'def h(user, rows):\n    if "ADMIN" in user["roles"]:\n        rows = rows[:1]\n'
        '    return mask_cost_list(rows, user)',
    ],
)
def test_the_guard_passes_what_is_not_a_cost_rule(src):
    assert not list(_own_cost_rules(ast.parse(src))), src
