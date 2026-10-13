"""A vendor's purchase history and scorecard follow THE shop rule
(resolve_store_scope; audit F63, owner ruling 2026-10-12 R4).

Before: GET /vendors/{id}/purchase-history and /vendors/{id}/performance read
every shop's purchase orders and receipts for any purchase role. A Pune
manager, a Pune accountant and logins with NO shop all read Dhanbad's Rs 1,000
of PO spend, its monthly breakdown and top products, and the scorecard counted
Dhanbad's GRN -- while every other Purchase read answered empty, 403 or 404.

Now the orders (delivery_store_id), receipts and QC signals (store_id) are the
caller's shop's: every shop for an admin with none picked (?store_id narrows
it), the own shop for anyone else, none for a non-admin login with no shop (an
explicit shop from it is a 403). The accounts roles' MTD spend stays org-wide
(R8) and is not asserted here.
"""
import os
import sys
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mongomock  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.dependencies import NO_SHOP_DETAIL  # noqa: E402
from api.routers import vendors  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

DHN, PUN = "BV-DHN-01", "WO-PUN-01"


def _user(role, shops=()):
    shops = list(shops)
    return {"user_id": f"u-{role.lower()}", "roles": [role], "store_ids": shops,
            "active_store_id": shops[0] if shops else None}


class _VendorRepo:
    def find_by_id(self, _vid):
        return {"vendor_id": "V1", "trade_name": "Ven"}


@pytest.fixture
def call(monkeypatch):
    db = mongomock.MongoClient()["vendor_history"]
    now = datetime.now()
    db["purchase_orders"].insert_one({
        "po_id": "PO-D", "po_number": "PO-D", "vendor_id": "V1",
        "delivery_store_id": DHN, "store_id": DHN, "status": "SENT",
        "items": [{"product_id": "P1", "product_name": "Frame", "quantity": 2,
                   "unit_price": 500}],
        "total_amount": 1000, "created_at": now,
    })
    db["grns"].insert_one({
        "grn_id": "G-D", "po_id": "PO-D", "vendor_id": "V1", "store_id": DHN,
        "status": "ACCEPTED", "created_at": now, "total_received": 2,
        "total_accepted": 2,
    })
    db["workshop_jobs"].insert_one({"job_id": "J-D", "vendor_id": "V1",
                                    "store_id": DHN, "qc_passed": True})
    monkeypatch.setattr(vendors, "_get_db", lambda: db)
    monkeypatch.setattr(vendors, "get_vendor_repository", lambda: _VendorRepo())
    app = FastAPI()
    app.include_router(vendors.router, prefix="/vendors")
    who = {}

    async def _current():
        return dict(who["user"])

    app.dependency_overrides[get_current_user] = _current
    client = TestClient(app)

    def _call(user, path, **params):
        who["user"] = user
        return client.get(path, params=params)

    return _call


OUTSIDERS = (
    _user("STORE_MANAGER", [PUN]),
    _user("AREA_MANAGER", [PUN]),
    _user("ACCOUNTANT", [PUN]),
    _user("STORE_MANAGER"),
    _user("ACCOUNTANT"),
)


@pytest.mark.parametrize("user", OUTSIDERS, ids=lambda u: f"{u['roles'][0]}-{u['active_store_id']}")
def test_another_shops_orders_are_not_in_the_vendors_purchase_history(call, user):
    r = call(user, "/vendors/V1/purchase-history")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["total_pos"], body["total_spend"]) == (0, 0.0), body
    assert body["monthly"] == [] and body["top_products"] == []


@pytest.mark.parametrize("user", OUTSIDERS, ids=lambda u: f"{u['roles'][0]}-{u['active_store_id']}")
def test_another_shops_receipts_and_qc_are_not_in_the_scorecard(call, user):
    body = call(user, "/vendors/V1/performance").json()
    assert body["grns_evaluated"] == 0, body
    assert body["qc_sample_size"] == 0 and body["qc_pass_rate"] is None, body


@pytest.mark.parametrize("user", (_user("STORE_MANAGER", [DHN]), _user("ACCOUNTANT", [DHN]),
                                  _user("ADMIN"), _user("SUPERADMIN")),
                         ids=lambda u: f"{u['roles'][0]}-{u['active_store_id']}")
def test_the_own_shop_and_an_admin_read_it(call, user):
    hist = call(user, "/vendors/V1/purchase-history").json()
    assert (hist["total_pos"], hist["total_spend"]) == (1, 1000.0), hist
    perf = call(user, "/vendors/V1/performance").json()
    assert perf["grns_evaluated"] == 1 and perf["qc_sample_size"] == 3, perf


def test_an_admins_shop_pick_narrows_it(call):
    admin = _user("ADMIN")
    assert call(admin, "/vendors/V1/purchase-history", store_id=PUN).json()["total_pos"] == 0
    assert call(admin, "/vendors/V1/purchase-history", store_id=DHN).json()["total_pos"] == 1
    assert call(admin, "/vendors/V1/performance", store_id=PUN).json()["grns_evaluated"] == 0


@pytest.mark.parametrize("path", ("/vendors/V1/purchase-history", "/vendors/V1/performance"))
def test_a_named_shop_from_a_login_with_no_shop_is_refused(call, path):
    r = call(_user("ACCOUNTANT"), path, store_id=DHN)
    assert r.status_code == 403
    assert r.json()["detail"] == NO_SHOP_DETAIL
    r = call(_user("STORE_MANAGER", [PUN]), path, store_id=DHN)
    assert r.status_code == 403
