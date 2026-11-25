"""
L1-F17: GET /crm/customers/{id}/cl-refill-status must apply THE customer
ownership rule (customers.customer_in_scope) the 360 view uses, with the same
non-disclosing 404, before it reads any order history. Its POLICY row must
match the store worklist's roles (GET /crm/cl-refill/{store_id}/due) instead
of AUTHENTICATED.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import crm as crm_mod  # noqa: E402
from api.services.rbac_policy._lookup import policy_for  # noqa: E402

_CUSTOMERS = {
    "CUST-A": {"customer_id": "CUST-A", "home_store_id": "STORE-A"},
    "CUST-B": {"customer_id": "CUST-B", "home_store_id": "STORE-B"},
    "CUST-NOSHOP": {"customer_id": "CUST-NOSHOP"},
}


class _Cur(list):
    def sort(self, *_a, **_k):
        return self

    def limit(self, _n):
        return self


class _Orders:
    def find(self, query, _proj=None):
        cid = query.get("customer_id")
        return _Cur(
            [
                {
                    "order_id": f"ORD-{cid}",
                    "customer_id": cid,
                    "status": "DELIVERED",
                    "created_at": (datetime.utcnow() - timedelta(days=5)).isoformat(),
                    "items": [{"sku": "CL-SKU", "category": "CONTACT_LENS",
                               "modality": "DAILY", "pack_size": 30, "quantity": 2}],
                }
            ]
        )


class _DB:
    def get_collection(self, _name):
        return _Orders()


class _Adapter:
    def query_customer(self, cid):
        return _CUSTOMERS.get(cid)


@pytest.fixture(autouse=True)
def _wire(monkeypatch):
    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: _DB())
    monkeypatch.setattr(crm_mod, "db", _Adapter())


def _user(roles, stores=("STORE-A",)):
    stores = list(stores)
    return {"user_id": "u", "roles": list(roles), "store_ids": stores,
            "active_store_id": stores[0] if stores else None}


def _call(cid, user):
    return asyncio.run(crm_mod.get_cl_refill_status(customer_id=cid, current_user=user))


@pytest.mark.parametrize("roles", [("SALES_STAFF",), ("STORE_MANAGER",), ("OPTOMETRIST",), ("AREA_MANAGER",)])
@pytest.mark.parametrize("cid", ["CUST-B", "CUST-NOSHOP", "CUST-MISSING"])
def test_out_of_scope_customer_is_404_with_no_history(roles, cid):
    with pytest.raises(HTTPException) as exc:
        _call(cid, _user(roles))
    assert exc.value.status_code == 404
    assert exc.value.detail == "Customer not found"


@pytest.mark.parametrize("roles", [("SALES_STAFF",), ("STORE_MANAGER",), ("AREA_MANAGER",)])
def test_own_shop_customer_still_reads(roles):
    out = _call("CUST-A", _user(roles))
    assert out["has_cl_history"] is True
    assert out["last_cl_order_id"] == "ORD-CUST-A"


@pytest.mark.parametrize("roles", [("ADMIN",), ("SUPERADMIN",)])
def test_hq_reads_any_shop(roles):
    assert _call("CUST-B", _user(roles, stores=()))["last_cl_order_id"] == "ORD-CUST-B"


def test_multi_shop_user_reads_each_own_shop():
    u = _user(("AREA_MANAGER",), stores=("STORE-A", "STORE-B"))
    assert _call("CUST-B", u)["has_cl_history"] is True


def test_policy_row_matches_worklist_roles():
    one = policy_for("GET", "/api/v1/crm/customers/{customer_id}/cl-refill-status")
    worklist = policy_for("GET", "/api/v1/crm/cl-refill/{store_id}/due")
    assert one["allowed"] != "AUTHENTICATED"
    assert set(one["allowed"]) == set(worklist["allowed"])
