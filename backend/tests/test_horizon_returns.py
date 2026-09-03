"""
IMS 2.0 - 30-day browse horizon on the RETURNS history door
===========================================================
Owner ruling 2026-09-01: every role except ADMIN / SUPERADMIN browses only the
last 30 days. `GET /returns` is a pure BROWSE - a whole store's return history,
every row carrying a customer name and a refund amount - so it is clamped.

Two things these tests are built to catch, because both have bitten this repo:

  1. THE TYPE TRAP. `returns.created_at` is an ISO STRING (create_return stamps
     `datetime.now().isoformat()`; shopify_refund.py and shopify_ingest.py stamp
     `.isoformat()` too). A datetime bound against a string field brackets by
     BSON type and matches NOTHING - the screen goes EMPTY instead of narrowing.
     The fake collection below reproduces that bracketing, so swapping
     `horizon_start_iso_date` for `horizon_start` FAILS the positive control.

  2. THE DUPLICATE ROUTE. `list_returns` is registered at BOTH "" and "/". Both
     paths are exercised, so a clamp on only one of them would be caught.

The negative tests were measured by DELETING the clamp - see the module report.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

import jwt  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import auth as auth_mod  # noqa: E402
from api.routers import returns as returns_router  # noqa: E402
from api.utils.ist import ist_today  # noqa: E402

# Both registered paths for the ONE handler (a duplicate route decorator: a
# clamp on only one of them would be a bypass).
BOTH_PATHS = ("/api/v1/returns", "/api/v1/returns/")

STORE = "BV-PUN-01"


def _iso(days_ago: int) -> str:
    """A return's `created_at` exactly as create_return writes it: a naive ISO
    STRING with a time component (`datetime.now().isoformat()`)."""
    return (
        datetime.combine(ist_today(), datetime.min.time())
        - timedelta(days=days_ago)
        + timedelta(hours=11, minutes=42, seconds=7)
    ).isoformat()


# ---------------------------------------------------------------------------
# Fake `returns` collection with the Mongo semantics this door depends on:
# equality match, `$gte`, and BSON TYPE BRACKETING.
# ---------------------------------------------------------------------------


def _matches(doc, query) -> bool:
    for key, cond in (query or {}).items():
        val = doc.get(key)
        if isinstance(cond, dict):
            for op, bound in cond.items():
                if op != "$gte":
                    raise AssertionError("fake coll does not implement " + op)
                # BSON type bracketing: a value of a different type than the
                # bound never compares - it is simply not matched. This is the
                # real Mongo behaviour that makes a datetime bound on a string
                # field return zero rows.
                if type(val) is not type(bound):
                    return False
                if not (val >= bound):
                    return False
        elif val != cond:
            return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, field, direction=1):
        self._docs.sort(key=lambda d: d.get(field) or "", reverse=direction < 0)
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)


class _ReturnsColl:
    def __init__(self, docs):
        self.docs = docs

    def find(self, query=None, projection=None):
        return _Cursor(d for d in self.docs if _matches(d, query))

    def count_documents(self, query=None):
        return sum(1 for d in self.docs if _matches(d, query))


def _token(roles, store_id=STORE, uid="u1"):
    return jwt.encode(
        {
            "sub": uid,
            "user_id": uid,
            "username": "tester",
            "roles": roles,
            "active_store_id": store_id,
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        auth_mod.SECRET_KEY,
        algorithm=auth_mod.ALGORITHM,
    )


def _hdr(roles, **kw):
    return {"Authorization": "Bearer " + _token(roles, **kw)}


RECENT = {
    "return_id": "RET-RECENT",
    "store_id": STORE,
    "return_type": "RETURN",
    "customer_name": "Asha",
    "net_refund": 5900.0,
    "created_at": _iso(5),
}
OLD = {
    "return_id": "RET-OLD",
    "store_id": STORE,
    "return_type": "RETURN",
    "customer_name": "Bhavna",
    "net_refund": 12500.0,
    "created_at": _iso(31),
}
ANCIENT = {
    "return_id": "RET-ANCIENT",
    "store_id": STORE,
    "return_type": "CREDIT_NOTE",
    "customer_name": "Chetan",
    "net_refund": 40000.0,
    "created_at": _iso(400),
}


def _app_with(docs, coll_cls=None):
    app = FastAPI()
    app.include_router(returns_router.router, prefix="/api/v1/returns")
    coll = (coll_cls or _ReturnsColl)(docs)

    class _FakeDB:
        is_connected = True

        def __init__(self):
            self.db = self

        def get_collection(self, name):
            assert name == "returns"
            return coll

    return app, _FakeDB(), coll


@pytest.fixture
def client(monkeypatch):
    app, db, _coll = _app_with([RECENT, OLD, ANCIENT])
    monkeypatch.setattr("api.dependencies.get_db", lambda: db, raising=False)
    return TestClient(app)


def _ids(body):
    return {r["return_id"] for r in body["returns"]}


# ---------------------------------------------------------------------------
# NEGATIVE: a non-admin cannot see a 31-day-old return
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", BOTH_PATHS)
@pytest.mark.parametrize(
    "role", ["STORE_MANAGER", "CASHIER", "SALES_STAFF", "AREA_MANAGER"]
)
def test_staff_cannot_see_returns_older_than_30_days(client, path, role):
    r = client.get(path, headers=_hdr([role]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "RET-OLD" not in _ids(body)
    assert "RET-ANCIENT" not in _ids(body)


# ---------------------------------------------------------------------------
# POSITIVE control: the recent row IS returned, and an ADMIN still sees it all.
# This is also the TYPE-TRAP guard: a datetime bound would return NOTHING here.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", BOTH_PATHS)
def test_staff_still_see_the_recent_return(client, path):
    r = client.get(path, headers=_hdr(["STORE_MANAGER"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert _ids(body) == {"RET-RECENT"}, "clamp must narrow the list, not empty it"
    assert body["returns"][0]["net_refund"] == 5900.0


@pytest.mark.parametrize("path", BOTH_PATHS)
@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
def test_admin_still_sees_the_whole_book(client, path, role):
    r = client.get(path, headers=_hdr([role]), params={"store_id": STORE})
    assert r.status_code == 200, r.text
    body = r.json()
    assert _ids(body) == {"RET-RECENT", "RET-OLD", "RET-ANCIENT"}
    assert body["total"] == 3


# ---------------------------------------------------------------------------
# The TOTAL is clamped too - a truthful page over a leaking count would still
# tell a departing employee how big the book is.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", BOTH_PATHS)
def test_total_is_clamped_not_just_the_page(client, path):
    r = client.get(path, headers=_hdr(["SALES_STAFF"]))
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1


def test_total_is_clamped_under_paging(client):
    """`skip`/`limit` must not be the thing that hides the old rows - with a
    page large enough to hold everything, the count is still 1."""
    r = client.get(
        BOTH_PATHS[0], headers=_hdr(["SALES_STAFF"]), params={"limit": 200}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert len(body["returns"]) == 1


# ---------------------------------------------------------------------------
# A filter is not a bypass: `return_type` narrows, it never widens.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", BOTH_PATHS)
def test_return_type_filter_is_not_a_bypass(client, path):
    r = client.get(
        path, headers=_hdr(["CASHIER"]), params={"return_type": "CREDIT_NOTE"}
    )
    assert r.status_code == 200, r.text
    # RET-ANCIENT is the only CREDIT_NOTE, and it is 400 days old.
    assert r.json() == {"returns": [], "total": 0}


# ---------------------------------------------------------------------------
# The bound is an ISO STRING at the horizon date - asserted on the query the
# handler actually builds, so a future refactor that stringifies a datetime (or
# drops the clamp) is caught at the source and not only through the fake.
# ---------------------------------------------------------------------------


def test_clamp_bound_is_an_iso_string_at_the_horizon(monkeypatch):
    seen = {}

    class _SpyColl(_ReturnsColl):
        def count_documents(self, query=None):
            seen["query"] = dict(query or {})
            return super().count_documents(query)

    app, db, _coll = _app_with([RECENT, OLD], coll_cls=_SpyColl)
    monkeypatch.setattr("api.dependencies.get_db", lambda: db, raising=False)
    c = TestClient(app)

    assert c.get(BOTH_PATHS[0], headers=_hdr(["STORE_MANAGER"])).status_code == 200
    bound = seen["query"]["created_at"]["$gte"]
    assert isinstance(bound, str), "a datetime bound would match no string row"
    assert bound == (ist_today() - timedelta(days=30)).isoformat()

    # ADMIN: no clamp key at all.
    seen.clear()
    assert c.get(BOTH_PATHS[0], headers=_hdr(["ADMIN"])).status_code == 200
    assert "created_at" not in seen["query"]


# ---------------------------------------------------------------------------
# Store scoping still holds under the clamp (it was already there; this is the
# guard that the horizon edit did not disturb it).
# ---------------------------------------------------------------------------


def test_staff_cannot_read_another_store_via_store_id(client):
    r = client.get(
        BOTH_PATHS[0],
        headers=_hdr(["SALES_STAFF"], store_id="BV-RAN-01"),
        params={"store_id": STORE},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"returns": [], "total": 0}
