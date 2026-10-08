"""
IMS 2.0 — vendors router write gating
=====================================
Vendor / purchase-order / goods-receipt mutations had NO server-side role
check — any authenticated user could create POs or accept GRNs (which adjust
stock and vendor liability) by hitting the API directly, despite the frontend
/purchase/* routes being restricted. The 8 write endpoints are now gated to
the roles those routes allow (ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT;
SUPERADMIN auto-passes) -- receiving goods only the managers (owner ruling
2026-09-28, not the accountant). Reads intentionally stay open (they may feed
inventory views for catalog/workshop roles).

End-to-end via the conftest TestClient fixtures.
"""

from __future__ import annotations

import pytest


def _headers(roles):
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": "t-1",
            "username": "t",
            "roles": roles,
            "store_ids": ["BV-TEST-01"],
            "active_store_id": "BV-TEST-01",
        }
    )
    return {"Authorization": f"Bearer {token}"}


_VENDOR_BODY = {
    "legal_name": "Acme Optics Pvt Ltd",
    "trade_name": "Acme",
    "gstin_status": "REGISTERED",
    "address": "1 Main St",
    "city": "Pune",
    "state": "MH",
    "mobile": "9000000000",
}
_PO_BODY = {
    "vendor_id": "v1",
    # Match the caller's own store (BV-TEST-01): this is a ROLE-gating test, and a
    # PO is legitimately raised for one's own store. Cross-store PO creation is
    # denied by validate_store_access -- covered by test_po_store_boundary.py.
    "delivery_store_id": "BV-TEST-01",
    "items": [
        {
            "product_id": "p1",
            "product_name": "Frame",
            "sku": "SKU1",
            "quantity": 10,
            "unit_price": 100.0,
        }
    ],
}
_GRN_BODY = {
    "po_id": "po1",
    "vendor_invoice_no": "INV-1",
    "vendor_invoice_date": "2026-05-21",
    "items": [
        {"po_item_id": "pi1", "product_id": "p1", "received_qty": 10, "accepted_qty": 10}
    ],
}

# (method, path, json_body, query_params)
WRITES = [
    ("post", "/api/v1/vendors", _VENDOR_BODY, None),
    ("put", "/api/v1/vendors/v1", {"city": "Mumbai"}, None),
    ("post", "/api/v1/vendors/purchase-orders", _PO_BODY, None),
    ("post", "/api/v1/vendors/purchase-orders/po1/send", None, None),
    ("post", "/api/v1/vendors/purchase-orders/po1/cancel", None, {"reason": "dup"}),
    ("put", "/api/v1/vendors/purchase-orders/po1", {"items": _PO_BODY["items"]}, None),
    (
        "post",
        "/api/v1/vendors/purchase-orders/po1/items/0/cancel",
        {"reason": "vendor out of stock"},
        None,
    ),
    ("post", "/api/v1/vendors/grn", _GRN_BODY, None),
    ("post", "/api/v1/vendors/grn/g1/accept", None, None),
    ("post", "/api/v1/vendors/grn/g1/escalate", None, {"note": "short"}),
    ("post", "/api/v1/vendors/grn/g1/void", None, None),
    ("post", "/api/v1/vendors/grn/express", _GRN_BODY, None),
    ("post", "/api/v1/vendors/grn/upload-doc", None, None),
    ("get", "/api/v1/vendors/goods-receipt/cockpit", None, {"vendor_id": "v1"}),
]

# Receiving goods into stock (and the receiving screen): MANAGERS ONLY.
_RECEIVING = [w for w in WRITES if "/vendors/grn" in w[1] or "goods-receipt" in w[1]]


def _send(client, method, path, json_body, params, headers):
    kwargs = {"headers": headers}
    if json_body is not None:
        kwargs["json"] = json_body
    if params is not None:
        kwargs["params"] = params
    return getattr(client, method)(path, **kwargs)


class TestVendorWriteGating:
    @pytest.mark.parametrize("method,path,body,params", WRITES)
    def test_sales_staff_blocked(self, client, staff_headers, method, path, body, params):
        resp = _send(client, method, path, body, params, staff_headers)
        assert resp.status_code == 403

    @pytest.mark.parametrize(
        "method,path,body,params", [w for w in WRITES if w not in _RECEIVING]
    )
    def test_accountant_allowed(self, client, method, path, body, params):
        resp = _send(client, method, path, body, params, _headers(["ACCOUNTANT"]))
        assert resp.status_code != 403

    @pytest.mark.parametrize("method,path,body,params", _RECEIVING)
    def test_accountant_does_not_receive_goods(self, client, method, path, body, params):
        """Owner ruling 2026-09-28: RECEIVING IS MANAGERS ONLY."""
        resp = _send(client, method, path, body, params, _headers(["ACCOUNTANT"]))
        assert resp.status_code == 403, (method, path)

    @pytest.mark.parametrize("method,path,body,params", WRITES)
    def test_superadmin_allowed(self, client, auth_headers, method, path, body, params):
        resp = _send(client, method, path, body, params, auth_headers)
        assert resp.status_code != 403


class TestVendorReadsStayOpen:
    def test_staff_can_list_vendors(self, client, staff_headers):
        # Reads intentionally remain open (may feed inventory views).
        assert client.get("/api/v1/vendors", headers=staff_headers).status_code != 403

    def test_staff_can_list_purchase_orders(self, client, staff_headers):
        resp = client.get("/api/v1/vendors/purchase-orders", headers=staff_headers)
        assert resp.status_code != 403


class TestCatalogManagerRaisesDraftOnly:
    """Owner ruling 2026-09-28: the catalogue manager raises a DRAFT from the
    Buy Desk; the store manager checks and sends it. Everything that changes an
    order after that stays with the managers."""

    def test_catalog_manager_may_raise_a_draft(self, client):
        resp = client.post(
            "/api/v1/vendors/purchase-orders",
            json=_PO_BODY,
            headers=_headers(["CATALOG_MANAGER"]),
        )
        assert resp.status_code != 403



# The managers who send, edit and cancel an order (SUPERADMIN passes every gate).
_ORDER_MANAGERS = {"ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT"}
# Who receives goods into stock (owner ruling 2026-09-28: MANAGERS ONLY).
_RECEIVING_MANAGERS = {"ADMIN", "AREA_MANAGER", "STORE_MANAGER"}


def _everyone_else(managers=_ORDER_MANAGERS):
    from api.services.rbac_policy import ALL_ROLES

    return [r for r in ALL_ROLES if r != "SUPERADMIN" and r not in managers]


# Everything a manager does to an order once it exists, and receiving goods
# into stock.
_MANAGER_WRITES = [w for w in WRITES if "/purchase-orders/po1" in w[1]] + _RECEIVING
_REFUSED = [
    (role, *w)
    for w in _MANAGER_WRITES
    for role in _everyone_else(_RECEIVING_MANAGERS if w in _RECEIVING else _ORDER_MANAGERS)
]
_ROLE_GATE_DETAIL = "Your role does not have access to this resource"


class TestOnlyManagersChangeAnOrder:
    """Send, edit, cancel, line-cancel and receiving stay with the managers:
    every other role -- cashier and workshop staff included, the catalogue
    manager too, and the accountant on receiving -- is refused on every one."""

    @pytest.mark.parametrize("role,method,path,body,params", _REFUSED)
    def test_refused(self, client, role, method, path, body, params):
        resp = _send(client, method, path, body, params, _headers([role]))
        assert resp.status_code == 403, (role, method, path)

    @pytest.mark.parametrize(
        "method,path,body,params", [w for w in _MANAGER_WRITES if w[0] != "get"]
    )
    def test_a_vendors_write_grant_does_not_open_them(
        self, client, monkeypatch, method, path, body, params
    ):
        """The policy row alone answers 403 to a role test, so a route that
        lost its require_roles still looked gated. It is not: the middleware
        honours a per-user grant, require_roles does not. An ADMIN may grant
        vendors:write to a salesperson; the route's own gate must still say no
        -- and it is that gate (its message) that answers."""
        from api import dependencies as deps

        class _Users:
            def find_by_id(self, uid):
                return {"user_id": uid, "permissions": {"grant": {"vendors:write": True}}}

        monkeypatch.setattr(deps, "get_user_repository", lambda: _Users())
        resp = _send(client, method, path, body, params, _headers(["SALES_STAFF"]))
        assert resp.status_code == 403, (method, path)
        assert resp.json().get("detail") == _ROLE_GATE_DETAIL, (method, path)

    def test_the_refused_list_covers_the_counter_and_the_workshop(self):
        assert {"CASHIER", "WORKSHOP_STAFF", "SALES_STAFF", "CATALOG_MANAGER"} <= set(
            _everyone_else()
        )
        assert "ACCOUNTANT" in _everyone_else(_RECEIVING_MANAGERS)


def _code_gate(dependant):
    """The roles a route's own require_roles dependency lets in (None if the
    route has none). Nested dependencies intersect, like the requests do."""
    import inspect

    gates = []

    def walk(dep):
        for d in dep.dependencies:
            fn = d.call
            if getattr(fn, "__qualname__", "") == "require_roles.<locals>._dep":
                gates.append(set(inspect.getclosurevars(fn).nonlocals["allowed"]))
            walk(d)

    walk(dependant)
    return set.intersection(*gates) - {"SUPERADMIN"} if gates else None


# Writes whose role check lives inside the handler rather than a
# require_roles dependency (portal tokens: _require_admin).
_GATED_IN_THE_HANDLER = {
    ("POST", "/api/v1/vendors/{vendor_id}/portal-token"),
    ("DELETE", "/api/v1/vendors/{vendor_id}/portal-token/{token_id}"),
}


def test_every_vendors_write_has_a_code_gate(app):
    """A write with no require_roles is gated by its policy row alone -- which
    a per-user grant opens. So every /vendors write carries its own gate (or is
    named above with where its check lives); losing one fails here."""
    from fastapi.routing import APIRoute

    missing, writes = [], 0
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/v1/vendors"):
            continue
        for method in route.methods - {"GET", "HEAD"}:
            writes += 1
            if (method, route.path) in _GATED_IN_THE_HANDLER:
                continue
            if _code_gate(route.dependant) is None:
                missing.append((method, route.path))
    assert writes >= 10
    assert not missing, missing


def _gate_of(app, method, path):
    from fastapi.routing import APIRoute

    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            return _code_gate(route.dependant)
    raise AssertionError(f"no route {method} {path}")


# Every door that receives goods into stock, and the receiving screen.
_RECEIVING_ROUTES = [
    ("POST", "/api/v1/vendors/grn"),
    ("POST", "/api/v1/vendors/grn/express"),
    ("POST", "/api/v1/vendors/grn/{grn_id}/accept"),
    ("POST", "/api/v1/vendors/grn/{grn_id}/void"),
    ("POST", "/api/v1/vendors/grn/{grn_id}/escalate"),
    ("POST", "/api/v1/vendors/grn/upload-doc"),
    ("GET", "/api/v1/vendors/goods-receipt/cockpit"),
]
# What the accountant keeps: orders, the receipt document (to match the bill),
# bills and payments.
_ACCOUNTANT_KEEPS = [
    ("POST", "/api/v1/vendors/purchase-orders/{po_id}/send"),
    ("POST", "/api/v1/vendors/purchase-orders/{po_id}/cancel"),
    ("GET", "/api/v1/vendors/grn/{grn_id}/document"),
    ("POST", "/api/v1/vendors/{vendor_id}/bills"),
    ("POST", "/api/v1/vendors/{vendor_id}/payments"),
    ("POST", "/api/v1/vendors/purchase-invoices"),
]


def test_receiving_is_managers_only_and_the_accountant_keeps_the_rest(app):
    """Owner ruling 2026-09-28: RECEIVING IS MANAGERS ONLY. The route's own gate
    (not just its policy row) decides, and narrowing it must not take the
    accountant's bills, payments or orders with it."""
    for method, path in _RECEIVING_ROUTES:
        assert _gate_of(app, method, path) == _RECEIVING_MANAGERS, (method, path)
    for method, path in _ACCOUNTANT_KEEPS:
        assert "ACCOUNTANT" in _gate_of(app, method, path), (method, path)


def test_every_vendors_policy_row_matches_its_code_gate(app):
    """Two gates guard each vendors write: the policy row (middleware, and the
    role union the grant guard reasons from) and the route's require_roles.
    A row that drifts from its code gate is invisible to a 403 test -- the other
    gate still answers 403 -- so compare them directly."""
    from fastapi.routing import APIRoute

    from api.services import rbac_policy

    drift, checked = [], 0
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/v1/vendors"):
            continue
        code = _code_gate(route.dependant)
        if code is None:
            continue
        for method in route.methods:
            checked += 1
            row = rbac_policy.policy_for(method, route.path) or {}
            allowed = row.get("allowed")
            row_roles = (
                set(allowed) - {"SUPERADMIN"} if isinstance(allowed, (list, tuple, set)) else allowed
            )
            if row_roles != code:
                drift.append((method, route.path, sorted(code), row_roles))
    assert checked >= 5  # the PO change routes alone are five
    assert not drift, drift
