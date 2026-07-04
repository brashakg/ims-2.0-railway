"""
IMS 2.0 — vendors router write gating
=====================================
Vendor / purchase-order / goods-receipt mutations had NO server-side role
check — any authenticated user could create POs or accept GRNs (which adjust
stock and vendor liability) by hitting the API directly, despite the frontend
/purchase/* routes being restricted. The 8 write endpoints are now gated to
the roles those routes allow (ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT;
SUPERADMIN auto-passes). Reads intentionally stay open (they may feed
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
    ("post", "/api/v1/vendors/grn", _GRN_BODY, None),
    ("post", "/api/v1/vendors/grn/g1/accept", None, None),
    ("post", "/api/v1/vendors/grn/g1/escalate", None, {"note": "short"}),
]


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

    @pytest.mark.parametrize("method,path,body,params", WRITES)
    def test_accountant_allowed(self, client, method, path, body, params):
        resp = _send(client, method, path, body, params, _headers(["ACCOUNTANT"]))
        assert resp.status_code != 403

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
