"""The Finance GST tab reads ONE shop scope (round 4, panel items 6 / 9).

The summary cards (/finance/gst/summary) were already the caller's shop by
resolve_store_scope, while the 'GST reconciliation by entity' table under
them (/finance/gst/reconciliation) added up every entity: a Pune accountant
read input credit 40 on the cards and 340 in the table on the same tab. Both
now follow the one rule -- an accountant his own shop, ADMIN / SUPERADMIN
every shop -- so the cards and the table agree for every login.
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

from api.routers import finance  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.routers.finance import _shared, gst, gst_crosscheck  # noqa: E402

DHN, PUN = "BV-DHN-01", "WO-PUN-01"
OCT = {"month": 10, "year": 2026}


def _user(role, shop=None):
    return {"user_id": f"u-{role}-{shop}", "username": role.lower(), "roles": [role],
            "store_ids": [shop] if shop else [], "active_store_id": shop}


ADMIN = _user("ADMIN")
ACCT_PUN = _user("ACCOUNTANT", PUN)


@pytest.fixture
def get(monkeypatch):
    db = mongomock.MongoClient()["gst_tab"]
    db["stores"].insert_many([
        {"store_id": DHN, "entity_id": "BV", "state": "Jharkhand"},
        {"store_id": PUN, "entity_id": "WO", "state": "Maharashtra"},
    ])
    db["entities"].insert_many([
        {"entity_id": "BV", "name": "Better Vision", "gstins": [{"gstin": "20ABCDE1234F1Z5"}]},
        {"entity_id": "WO", "name": "WizOpt", "gstins": [{"gstin": "27ABCDE1234F1Z5"}]},
    ])
    on = datetime(2026, 10, 10, 6, 0, 0)
    for shop, sale_tax, input_tax in ((PUN, 100.0, 40.0), (DHN, 900.0, 300.0)):
        db["orders"].insert_one({"order_id": f"O-{shop}", "store_id": shop, "status": "DELIVERED",
                                 "created_at": on, "tax_amount": sale_tax})
        db["vendor_bills"].insert_one({"bill_id": f"B-{shop}", "store_id": shop,
                                       "bill_date": "2026-10-05", "tax_amount": input_tax})
        db["purchase_orders"].insert_one({"po_id": f"PO-{shop}", "delivery_store_id": shop,
                                          "status": "RECEIVED", "created_at": on,
                                          "tax_amount": input_tax})
    for mod in (finance, _shared, gst, gst_crosscheck):
        monkeypatch.setattr(mod, "_get_db", lambda: db, raising=False)
    app = FastAPI()
    app.include_router(finance.router, prefix="/finance")
    who = {}

    async def _current():
        return dict(who["user"])

    app.dependency_overrides[get_current_user] = _current
    client = TestClient(app)

    def _get(path, user, **extra):
        who["user"] = user
        r = client.get(path, params={**OCT, **extra})
        assert r.status_code == 200, (path, r.text[:200])
        return r.json()

    return _get


def _cards(get, user):
    s = get("/finance/gst/summary", user)
    return (s["gst_collected"], s["gst_input_credit"], s["net_gst_payable"])


def _table(get, user):
    return {e["entity_name"]: (e["gst_collected"], e["input_credit"], e["net_payable"])
            for e in get("/finance/gst/reconciliation", user)["entities"]}


def test_a_pune_accountant_reads_pune_on_the_cards_and_in_the_table(get):
    assert _cards(get, ACCT_PUN) == (100.0, 40.0, 60.0)
    assert _table(get, ACCT_PUN) == {"WizOpt": (100.0, 40.0, 60.0)}


def test_an_admin_reads_every_shop_on_the_cards_and_in_the_table(get):
    assert _cards(get, ADMIN) == (1000.0, 340.0, 660.0)
    assert _table(get, ADMIN) == {"Better Vision": (900.0, 300.0, 600.0),
                                  "WizOpt": (100.0, 40.0, 60.0)}


def test_an_accountant_asking_for_another_entity_reads_nothing_of_it(get):
    """?entity_id narrows inside the caller's shops, never past them."""
    r = get("/finance/gst/reconciliation", ACCT_PUN, entity_id="BV")
    assert r["entities"] == [], r
