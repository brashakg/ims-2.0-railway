"""Money paid to suppliers, and AP owed, are one number per shop on every
screen (F56 one payable rule, F63 one shop rule).

Before:
  * Finance > Cash Flow counted Rs 0 paid to suppliers for ONE shop (a shop
    accountant, or an admin with a shop picked): get_cash_flow folded the
    supplier payments in only for the org view ("AP is org-level"), while the
    owner dashboard's 'Paid to vendors' and the Purchases report counted the
    shop's payments -- every payment now carries its shop.
  * The survival view gave an admin who picked Dhanbad every shop's AP
    (ORG_WIDE, 7500) while Dhanbad's accountant read 7000 and AP aging for
    the same admin pick said 7000.
Now both follow the one shop rule.
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

from api.routers import finance as finance_pkg  # noqa: E402
from api.routers import vendors as vendors_pkg  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.routers.finance import cash_flow as cf_mod  # noqa: E402
from api.routers.finance import receivables as rec_mod  # noqa: E402
from api.routers.finance import survival as sv_mod  # noqa: E402
from api.services import ap_engine  # noqa: E402

DHN, PUN = "BV-DHN-01", "WO-PUN-01"
NOW = datetime(2026, 10, 12, 12, 0, 0)


def _user(role, store=None):
    return {"user_id": f"u-{role}-{store}", "username": role.lower(), "roles": [role],
            "store_ids": [store] if store else [], "active_store_id": store}


ADMIN = _user("ADMIN")
ACCT = {DHN: _user("ACCOUNTANT", DHN), PUN: _user("ACCOUNTANT", PUN)}

BILLS = [
    {"bill_id": "B1", "vendor_id": "V1", "store_id": DHN, "bill_number": "1",
     "bill_date": "2026-09-05", "due_date": "2026-10-05", "total_amount": 10000.0},
    {"bill_id": "B2", "vendor_id": "V1", "store_id": PUN, "bill_number": "2",
     "bill_date": "2026-09-20", "due_date": "2026-10-20", "total_amount": 4000.0},
    {"bill_id": "B3", "vendor_id": "V2", "store_id": DHN, "bill_number": "3",
     "bill_date": "2026-09-02", "due_date": "2026-10-02", "total_amount": 5000.0},
    {"bill_id": "B4", "vendor_id": "V2", "store_id": PUN, "bill_number": "4",
     "bill_date": "2026-10-03", "due_date": "2026-11-02", "total_amount": 2000.0},
]
PAYMENTS = [
    {"payment_id": "P1", "vendor_id": "V1", "bill_id": "B1", "store_id": DHN,
     "amount": 3000.0, "tds_amount": 0.0, "payment_date": "2026-10-02"},
    {"payment_id": "P2", "vendor_id": "V1", "bill_id": None, "store_id": PUN,
     "amount": 2000.0, "tds_amount": 0.0, "payment_date": "2026-10-05"},
    {"payment_id": "P4", "vendor_id": "V2", "bill_id": "B3", "store_id": DHN,
     "amount": 6000.0, "tds_amount": 0.0, "payment_date": "2026-10-04"},
    {"payment_id": "P5", "vendor_id": "V2", "bill_id": "B4", "store_id": PUN,
     "amount": 900.0, "tds_amount": 100.0, "payment_date": "2026-10-07"},
    # Post-dated: not paid yet on 12 Oct.
    {"payment_id": "P6", "vendor_id": "V1", "bill_id": "B2", "store_id": PUN,
     "amount": 1000.0, "tds_amount": 0.0, "payment_date": "2026-12-01"},
]
# Cash paid in October (amount, not gross of TDS): Dhanbad 3000 + 6000;
# Pune 2000 + 900.
CASH_PAID = {DHN: 9000.0, PUN: 2900.0, None: 11900.0}


class _Proxy:
    def __init__(self, db):
        self._db = db
        self.is_connected = True

    def get_collection(self, n):
        return self._db[n]

    def __getitem__(self, n):
        return self._db[n]

    def __getattr__(self, n):
        return self._db[n]


@pytest.fixture
def get(monkeypatch):
    db = mongomock.MongoClient()["paid_one_shop"]
    db["vendors"].insert_many([{"vendor_id": v, "legal_name": v} for v in ("V1", "V2")])
    db["vendor_bills"].insert_many([dict(b) for b in BILLS])
    db["vendor_payments"].insert_many([dict(p) for p in PAYMENTS])
    proxy = _Proxy(db)
    from database.repositories.vendor_repository import VendorRepository
    for mod in (vendors_pkg, finance_pkg, cf_mod, rec_mod, sv_mod):
        if hasattr(mod, "_get_db"):
            monkeypatch.setattr(mod, "_get_db", lambda: proxy)
    monkeypatch.setattr(vendors_pkg, "get_vendor_repository",
                        lambda: VendorRepository(db["vendors"]))
    for mod in (finance_pkg, cf_mod, sv_mod):
        if hasattr(mod, "now_ist_naive"):
            monkeypatch.setattr(mod, "now_ist_naive", lambda: NOW)
        if hasattr(mod, "ist_today"):
            monkeypatch.setattr(mod, "ist_today", lambda: NOW.date())
    monkeypatch.setattr(ap_engine, "now_ist_naive", lambda: NOW)
    import api.routers.vendors.purchases_report as pr
    monkeypatch.setattr(pr, "now_ist_naive", lambda: NOW)
    app = FastAPI()
    app.include_router(vendors_pkg.router, prefix="/vendors")
    app.include_router(finance_pkg.router, prefix="/finance")
    tc = TestClient(app)

    def _get(path, user, **params):
        async def _u():
            return dict(user)
        app.dependency_overrides[get_current_user] = _u
        r = tc.get(path, params={k: v for k, v in params.items() if v is not None})
        assert r.status_code == 200, (path, r.status_code, r.text)
        return r.json()

    return _get


@pytest.mark.parametrize("shop", (DHN, PUN))
def test_a_shop_accountants_cash_flow_counts_his_shops_supplier_payments(get, shop):
    cf = get("/finance/cash-flow", ACCT[shop])
    dash = get("/finance/owner-dashboard", ACCT[shop])
    assert cf["vendor_payment_outflow"] == CASH_PAID[shop]
    # The same figure the owner dashboard's 'Paid to vendors' shows him...
    assert cf["vendor_payment_outflow"] == dash["this_month"]["vendor_payments"]
    # ...and inside the cash outflows it is part of.
    assert cf["outflows"] >= CASH_PAID[shop]


@pytest.mark.parametrize("shop", (DHN, PUN, None))
def test_an_admins_cash_flow_counts_the_picked_shops_payments(get, shop):
    cf = get("/finance/cash-flow", ADMIN, store_id=shop)
    assert cf["vendor_payment_outflow"] == CASH_PAID[shop]
    report = get("/vendors/purchases-this-month", ADMIN, month="2026-10", store_id=shop)
    tds = 100.0 if shop in (PUN, None) else 0.0
    # The report's `paid` is gross of TDS; the cash that left is gross - TDS.
    assert report["totals"]["paid"] - tds == pytest.approx(cf["vendor_payment_outflow"])


def test_the_org_view_is_unchanged(get):
    assert (get("/finance/cash-flow", ADMIN)["vendor_payment_outflow"]
            == get("/finance/owner-dashboard", ADMIN)["this_month"]["vendor_payments"]
            == CASH_PAID[None])


def _survival_ap(body):
    sv = body["survival"]
    return (sv["must_pay_ap_paise"] + sv["deferrable_ap_paise"]) / 100


@pytest.mark.parametrize("shop", (DHN, PUN))
def test_one_shops_survival_ap_is_one_number(get, shop):
    admin_pick = get("/finance/survival-cashflow", ADMIN, store_id=shop)
    own = get("/finance/survival-cashflow", ACCT[shop])
    aging = get("/vendors/ap-aging", ADMIN, store_id=shop)["totals"]["owed"]
    assert _survival_ap(admin_pick) == _survival_ap(own) == pytest.approx(aging)
    assert admin_pick["survival"]["ap_scope"] == own["survival"]["ap_scope"] == "STORE"
