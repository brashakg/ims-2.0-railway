"""A login with no shop reads no shop's GST (owner ruling 2026-10-12, R4).

Ruling: "A non-admin login with NO shop assigned (no active_store_id) sees NO
shop until an admin assigns one: empty lists and a clear 'No shop is assigned
to this login' message; never every shop."

THE HOLE (found in the R5 files). Both GSTR-3B handlers picked their shop as
``validate_store_access(store_id, user) or user["active_store_id"] or
"store-001"``: with no store_id and no active shop that fell through to
store-001, so a storeless STORE_MANAGER read store-001's return and a
storeless ACCOUNTANT downloaded store-001's portal file. The GST summary asked
no shop rule at all, so a storeless login read every shop's output GST (and an
accountant every shop's input credit).

THE RULE NOW. Both read the ONE shop rule (api.dependencies.resolve_store_scope
and its no-shop verdict, is_no_shop_scope):
  * GET /reports/gstr3b and /reports/gstr3b/gstn-json -- a return is ONE
    shop's document, so a storeless non-admin is refused 403 in the owner's
    words (NO_SHOP_DETAIL), named shop or not, before anything is computed;
  * GET /finance/gst/summary -- the caller's shop (F63, the one shop rule:
    every shop for ADMIN / SUPERADMIN, else its own -- the OS-031 org-wide
    reading for every login was retired when the parallel session's at_shop
    filter was merged, panel 2026-10-08); a storeless non-admin is scoped to
    NO_SHOP_SCOPE, which matches no sale and no bill: the EMPTY summary.
Unchanged: ADMIN / SUPERADMIN (every shop; the GSTR-3B's old default shop),
and a multi-store login with no active shop -- which now gets its OWN first
shop's return instead of store-001's.

The only non-admin either route admits is the ACCOUNTANT (owner ruling
2026-10-07, R1: a manager is refused both outright, shop or not --
test_gst_input_accounts_only), so the no-shop rule is pinned on it.

Handlers run for real against mongomock; the last test goes through the live
app (middleware + routers). No emoji (cp1252).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any, Dict, List

import mongomock
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.dependencies import NO_SHOP_DETAIL  # noqa: E402
from api.routers import finance  # noqa: E402
from api.routers import reports  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

S1 = "S1"  # Bokaro
S0 = "store-001"  # the old fall-through shop
S1_GSTIN = "20AAACB1234C1Z5"
S0_GSTIN = "20AAACB9999C1Z5"


def _user(role: str, active=None, stores=()) -> Dict[str, Any]:
    return {
        "user_id": f"u-{role.lower()}-{active or 'none'}-{len(stores)}",
        "username": role.lower(),
        "roles": [role],
        "store_ids": list(stores),
        "active_store_id": active,
    }


def _storeless(role: str) -> List[Dict[str, Any]]:
    """Every shape of 'no shop' a token can carry."""
    return [
        _user(role),
        {**_user(role), "store_ids": None},
        {**_user(role), "store_ids": [None, ""], "active_store_id": ""},
    ]


# The roles the GST summary / GSTR-3B admit that are not admins (R1).
NON_ADMIN_FINANCE = ("ACCOUNTANT",)


# ============================================================================
# Two shops, each with a May 2026 sale; three supplier bills
# ============================================================================


def _world() -> Any:
    db = mongomock.MongoClient().db
    db["entities"].insert_one({"entity_id": "E1", "legal_name": "BV Optics"})
    db["stores"].insert_many([
        {"store_id": S1, "store_name": "Bokaro", "entity_id": "E1",
         "state": "Jharkhand", "gstin": S1_GSTIN},
        {"store_id": S0, "store_name": "Other", "entity_id": "E1",
         "state": "Jharkhand", "gstin": S0_GSTIN},
    ])
    # Bokaro: GST 180 (90 + 90). store-001: GST 360 (180 + 180).
    db["orders"].insert_many([
        {"order_id": "O-1", "store_id": S1, "status": "DELIVERED",
         "created_at": datetime(2026, 5, 10, 6, 0), "grand_total": 1180.0,
         "tax_amount": 180.0},
        {"order_id": "O-2", "store_id": S0, "status": "DELIVERED",
         "created_at": datetime(2026, 5, 11, 6, 0), "grand_total": 2360.0,
         "tax_amount": 360.0},
    ])
    db["vendor_bills"].insert_many([
        {"bill_id": "B-1", "store_id": S1, "bill_number": "PB-1", "vendor_id": "V1",
         "bill_date": "2026-05-04", "invoice_date": "2026-05-04",
         "recipient_entity_id": "E1", "recipient_gstin": None,
         "taxable_amount": 1111.0, "tax_amount": 100.0, "cgst_total": 50.0,
         "sgst_total": 50.0, "igst_total": 0.0, "status": "OUTSTANDING"},
        {"bill_id": "B-2", "store_id": S1, "bill_number": "RCM-1", "vendor_id": "V2",
         "bill_date": "2026-05-06", "invoice_date": "2026-05-06",
         "reverse_charge": True, "recipient_entity_id": "E1",
         "recipient_gstin": None, "taxable_amount": 400.0, "tax_amount": 20.0,
         "cgst_total": 10.0, "sgst_total": 10.0, "igst_total": 0.0,
         "status": "OUTSTANDING"},
        {"bill_id": "B-3", "store_id": S1, "bill_number": "PB-3", "vendor_id": "V1",
         "bill_date": "2026-05-08", "invoice_date": "2026-05-08",
         "recipient_entity_id": "E1", "recipient_gstin": None,
         "taxable_amount": 300.0, "tax_amount": 33.0, "cgst_total": 16.5,
         "sgst_total": 16.5, "igst_total": 0.0, "itc_blocked": True,
         "itc_eligible": False, "status": "OUTSTANDING"},
    ])
    return db


class _Spy:
    """The world's db, recording every collection a handler opens."""

    def __init__(self, db):
        self._db = db
        self.read: List[str] = []

    def get_collection(self, name, *a, **k):
        self.read.append(name)
        return self._db.get_collection(name, *a, **k)

    def __getitem__(self, name):
        self.read.append(name)
        return self._db[name]

    def __getattr__(self, name):
        return getattr(self._db, name)


def _client(router, prefix: str, user: Dict[str, Any]) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix=prefix)

    async def _fake_user():
        return dict(user)

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


# ============================================================================
# GET /finance/gst/summary -- the caller's shop; NO shop's for no shop
# ============================================================================

SUMMARY_URL = "/finance/gst/summary?month=5&year=2026"


@pytest.fixture
def summary_db():
    original = finance._get_db
    spy = _Spy(_world())
    finance._get_db = lambda: spy
    yield spy
    finance._get_db = original


def _summary(user):
    r = _client(finance.router, "/finance", user).get(SUMMARY_URL)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_world_is_not_empty_admins_and_shop_logins_read_every_shop(summary_db):
    # ADMIN with no shop of its own still reads every shop (R4 is non-admins).
    for admin in (_user("ADMIN"), _user("SUPERADMIN"), _user("ADMIN", S1, (S1,))):
        body = _summary(admin)
        assert body["gst_collected"] == 540.0, body
        assert (body["gst_input_credit"], body["net_gst_payable"]) == (120.0, 420.0)
    # A login that holds a shop reads its OWN shop's (F63, the one shop rule;
    # it used to read every shop's 540 -- OS-031, retired by the merge of the
    # parallel session's at_shop filter, panel 2026-10-08). Not yet an owner
    # ruling: PLAN_STATUS section 5 records it as OPEN 2026-10-12.
    for role in NON_ADMIN_FINANCE:
        assert _summary(_user(role, S1, (S1,)))["gst_collected"] == 180.0, role
    # A multi-store login with no active shop reads its first own shop.
    assert _summary(_user("ACCOUNTANT", None, (S1, S0)))["gst_collected"] == 180.0


def test_a_storeless_accountant_reads_no_shops_output_or_input(summary_db):
    for token in _storeless("ACCOUNTANT"):
        summary_db.read.clear()
        body = _summary(token)
        assert (body["gst_collected"], body["cgst"], body["sgst"], body["igst"]) == (
            0.0, 0.0, 0.0, 0.0,
        ), (token, body)
        assert (
            body["gst_input_credit"],
            body["gst_input_credit_excluded"],
            body["net_gst_payable"],
        ) == (0.0, 0.0, 0.0), body
    # The same accountant once it holds a shop: its shop's figures (control).
    full = _summary(_user("ACCOUNTANT", S1, (S1,)))
    assert (full["gst_collected"], full["gst_input_credit"]) == (180.0, 120.0)


# ============================================================================
# GET /reports/gstr3b  +  /reports/gstr3b/gstn-json -- one shop's return
# ============================================================================

GSTR3B = "/reports/gstr3b?month=2026-05"
GSTN = "/reports/gstr3b/gstn-json?month=2026-05"


@pytest.fixture
def reports_world(monkeypatch):
    db = _world()
    computed: List[str] = []
    real = reports._compute_gstr3b

    def _counting(month, active_store):
        computed.append(active_store)
        return real(month, active_store)

    # The reports package fans a setattr out to the sub-module that binds it.
    monkeypatch.setattr(reports, "_get_raw_db", lambda: db)
    monkeypatch.setattr(reports, "_compute_gstr3b", _counting)
    return computed


def _get(url: str, user: Dict[str, Any]):
    return _client(reports.router, "/reports", user).get(url)


@pytest.mark.parametrize("role", NON_ADMIN_FINANCE)
def test_a_storeless_login_is_refused_the_return_in_the_owners_words(reports_world, role):
    for token in _storeless(role):
        for url in (GSTR3B, f"{GSTR3B}&store_id={S1}", f"{GSTR3B}&store_id={S0}"):
            r = _get(url, token)
            assert r.status_code == 403, (url, token, r.text)
            assert r.json()["detail"] == NO_SHOP_DETAIL, r.text
    # Nothing was computed for it -- no shop's return was read.
    assert reports_world == []


def test_a_storeless_accountant_gets_no_portal_file(reports_world):
    for token in _storeless("ACCOUNTANT"):
        for url in (GSTN, f"{GSTN}&store_id={S0}"):
            r = _get(url, token)
            assert r.status_code == 403, (url, r.text)
            assert r.json()["detail"] == NO_SHOP_DETAIL, r.text
    assert reports_world == []


def test_a_multi_store_login_with_no_active_shop_gets_its_own_return(reports_world):
    """Its token holds Bokaro but no active shop: it reads Bokaro's return,
    never store-001's (the old fall-through)."""
    multi = _user("ACCOUNTANT", None, (S1,))
    r = _get(GSTR3B, multi)
    assert r.status_code == 200, r.text
    assert (r.json()["gstin"], r.json()["legalName"]) == (S1_GSTIN, "Bokaro")
    acct = _user("ACCOUNTANT", None, (S1,))
    r = _get(GSTN, acct)
    assert r.status_code == 200, r.text
    assert r.json()["gstin"] == S1_GSTIN
    assert reports_world == [S1, S1]


def test_shop_logins_and_admins_are_unchanged(reports_world):
    for role in NON_ADMIN_FINANCE:
        body = _get(GSTR3B, _user(role, S1, (S1,))).json()
        assert body["gstin"] == S1_GSTIN, role
        # Another shop is still the ordinary 403.
        r = _get(f"{GSTR3B}&store_id={S0}", _user(role, S1, (S1,)))
        assert r.status_code == 403 and r.json()["detail"] != NO_SHOP_DETAIL
    # ADMIN: any shop named; omitted -> its active shop, else store-001.
    assert _get(f"{GSTR3B}&store_id={S0}", _user("ADMIN", S1)).json()["gstin"] == S0_GSTIN
    assert _get(GSTR3B, _user("ADMIN", S1)).json()["gstin"] == S1_GSTIN
    assert _get(GSTR3B, _user("ADMIN")).json()["gstin"] == S0_GSTIN
    assert _get(GSTN, _user("ADMIN")).json()["gstin"] == S0_GSTIN


# ============================================================================
# The live app (middleware + routers)
# ============================================================================


def _bearer(role: str) -> Dict[str, str]:
    from api.routers.auth import create_access_token

    token = create_access_token(
        {"user_id": f"u-{role.lower()}-noshop", "username": role.lower(),
         "roles": [role], "store_ids": [], "active_store_id": None}
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("role", NON_ADMIN_FINANCE)
def test_live_app_refuses_a_storeless_login_its_gstr3b(client, role):
    r = client.get(f"/api/v1{GSTR3B}", headers=_bearer(role))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == NO_SHOP_DETAIL, r.text


def test_live_app_refuses_a_storeless_accountant_the_portal_file(client):
    r = client.get(f"/api/v1{GSTN}", headers=_bearer("ACCOUNTANT"))
    assert r.status_code == 403, r.text
    assert r.json()["detail"] == NO_SHOP_DETAIL, r.text
