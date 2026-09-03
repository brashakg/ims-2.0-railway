"""
IMS 2.0 - The 30-day browse horizon on the CUSTOMER BOOK
========================================================
GET /customers is the customer book, and it pages. It is the single biggest
exfiltration surface in the app: an unbounded list hands a departing employee
every name, number and lifetime-value figure the chain has. Owner ruling
2026-09-01: everyone except ADMIN / SUPERADMIN browses only the last 30 days;
a lookup that NAMES one customer returns that person with no date limit.

What these tests are actually guarding, in order of importance:

  1. The BROWSE branch is clamped -- rows AND the total. The same filter dict
     feeds find_many() and count(); clamping only the page would leave the
     total announcing the size of the book the rows are hiding.
  2. The SEARCH branch is not a bypass. A fuzzy fragment matching many people
     is browsing and stays clamped; only a query that resolves to ONE customer
     and really names them (their name / number) lifts the window.
  3. ADMIN is untouched -- the positive control. Without it, a clamp that
     returned nothing at all would pass every negative test above.

The fake repository below is deliberately BSON-faithful about types: a datetime
bound never matches a string field, exactly as Mongo brackets by type. That is
the trap this repo has hit three times, so the fake must be able to reproduce
it rather than paper over it -- see test_created_at_is_a_real_datetime_on_the
_write_path, which pins the stored type at the write door itself.

ASCII only (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import customers as customers_router  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services.data_horizon import BROWSE_HORIZON_DAYS  # noqa: E402

NOW = datetime.now()
DAY_3 = NOW - timedelta(days=3)                          # inside the window
DAY_31 = NOW - timedelta(days=BROWSE_HORIZON_DAYS + 1)   # outside it
YEARS_AGO = NOW - timedelta(days=900)


# ============================================================================
# A BSON-faithful in-memory customer repository
# ============================================================================


def _cmp_gte(value: Any, bound: Any) -> bool:
    """Mongo compares within a type bracket only. A datetime bound against a
    string field matches NOTHING -- it does not raise, and it does not coerce.
    Reproducing that here is the point: a fake that happily compared a string
    date to a datetime would hide the exact bug class this clamp can cause."""
    if isinstance(bound, datetime):
        return isinstance(value, datetime) and value >= bound
    if isinstance(bound, str):
        return isinstance(value, str) and value >= bound
    return False


def _match_clause(doc: Dict[str, Any], key: str, cond: Any) -> bool:
    if key == "$and":
        return all(_matches(doc, c) for c in cond)
    if key == "$or":
        return any(_matches(doc, c) for c in cond)
    if key == "$nor":
        return not any(_matches(doc, c) for c in cond)
    value = doc.get(key)
    if isinstance(cond, dict):
        for op, operand in cond.items():
            if op == "$gte":
                if not _cmp_gte(value, operand):
                    return False
            elif op == "$lte":
                if not (isinstance(value, type(operand)) and value <= operand):
                    return False
            elif op == "$ne":
                if value == operand:
                    return False
            elif op == "$exists":
                if (key in doc) != bool(operand):
                    return False
            else:  # pragma: no cover - unsupported operator in a test fake
                raise AssertionError("fake repo cannot evaluate " + op)
        return True
    return value == cond


def _matches(doc: Dict[str, Any], flt: Optional[Dict[str, Any]]) -> bool:
    return all(_match_clause(doc, k, v) for k, v in (flt or {}).items())


class _FakeCustomerRepo:
    """Stand-in for CustomerRepository. find_many/count evaluate the filter for
    real, so deleting the clamp changes what comes back."""

    def __init__(self, docs: List[Dict[str, Any]]):
        self.docs = docs
        self.last_filter: Optional[Dict[str, Any]] = None

    # -- the browse path ----------------------------------------------------
    def find_many(self, filter=None, skip=0, limit=50, sort=None):
        self.last_filter = filter
        rows = [d for d in self.docs if _matches(d, filter)]
        return [dict(r) for r in rows[skip: skip + limit]]

    def count(self, filter=None):
        return len([d for d in self.docs if _matches(d, filter)])

    # -- the search path ----------------------------------------------------
    def search_customers(self, query: str, store_id: str = None):
        """Same fields the real repo searches (name / mobile / phone / email /
        patients.name / patients.mobile), every whitespace token required."""
        tokens = [t.lower() for t in (query or "").split() if t]
        out = []
        for d in self.docs:
            if store_id and store_id not in (
                d.get("home_store_id"),
                d.get("preferred_store_id"),
            ):
                continue
            hay = " ".join(
                str(d.get(k) or "") for k in ("name", "mobile", "phone", "email")
            )
            for p in d.get("patients") or []:
                hay += " " + str(p.get("name") or "") + " " + str(p.get("mobile") or "")
            hay = hay.lower()
            if all(t in hay for t in tokens):
                out.append(dict(d))
        return out


# ============================================================================
# Fixture data - one recent customer, one old one, in the same store
# ============================================================================

STORE = "store-001"


def _book() -> List[Dict[str, Any]]:
    return [
        {
            "customer_id": "C-NEW",
            "name": "Recent Rahul",
            "mobile": "9800000001",
            "phone": "9800000001",
            "home_store_id": STORE,
            "created_at": DAY_3,
            "patients": [],
        },
        {
            "customer_id": "C-OLD",
            "name": "Ancient Anita",
            "mobile": "9800000002",
            "phone": "9800000002",
            "home_store_id": STORE,
            "created_at": DAY_31,
            "patients": [],
        },
        {
            "customer_id": "C-ANCIENT",
            "name": "Ancient Arjun",
            "mobile": "9800000003",
            "phone": "9800000003",
            "home_store_id": STORE,
            "created_at": YEARS_AGO,
            "patients": [],
        },
    ]


def _client(monkeypatch, roles=("SALES_STAFF",), docs=None) -> TestClient:
    repo = _FakeCustomerRepo(_book() if docs is None else docs)

    # The router's own handle...
    monkeypatch.setattr(customers_router, "get_customer_repository", lambda: repo)
    # ...and the one data_horizon.query_names_one_customer resolves lazily.
    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_customer_repository", lambda: repo)

    app = FastAPI()
    app.include_router(customers_router.router, prefix="/customers")
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "u-test",
        "username": "tester",
        "roles": list(roles),
        "active_store_id": STORE,
    }
    client = TestClient(app)
    client.repo = repo  # type: ignore[attr-defined]
    return client


def _ids(resp) -> set:
    body = resp.json()
    return {r.get("customer_id") for r in body.get("customers", [])}


# ============================================================================
# 1. THE BROWSE BRANCH - the customer book itself
# ============================================================================


@pytest.mark.parametrize("role", ["SALES_STAFF", "STORE_MANAGER", "OPTOMETRIST"])
def test_browsing_the_book_is_clamped_to_30_days(monkeypatch, role):
    """THE negative test. A non-admin paging /customers must not receive a
    31-day-old customer record."""
    client = _client(monkeypatch, roles=(role,))
    r = client.get("/customers", params={"limit": 500})
    assert r.status_code == 200, r.text
    got = _ids(r)
    assert "C-NEW" in got, got
    assert "C-OLD" not in got, (
        "a %s browsed a 31-day-old customer: the book is unclamped" % role
    )
    assert "C-ANCIENT" not in got, got


def test_the_total_is_clamped_too_not_just_the_page(monkeypatch):
    """The same filter dict feeds find_many() and count(). If the clamp were
    applied inside the else-branch instead of before the split, the rows would
    narrow while `total` kept announcing the size of the whole book."""
    client = _client(monkeypatch)
    r = client.get("/customers", params={"limit": 1})
    assert r.status_code == 200, r.text
    total = r.json()["pagination"]["total"]
    assert total == 1, (
        "total leaked the size of the hidden book: got %s, the clamped book has "
        "1 customer" % total
    )


def test_admin_still_browses_the_whole_book(monkeypatch):
    """The POSITIVE control. Without it a clamp that returned NOTHING - a
    datetime bound against a string field, say - would pass every test above."""
    client = _client(monkeypatch, roles=("ADMIN",))
    r = client.get("/customers", params={"limit": 500})
    assert r.status_code == 200, r.text
    got = _ids(r)
    assert {"C-NEW", "C-OLD", "C-ANCIENT"} == got, got
    assert r.json()["pagination"]["total"] == 3


def test_superadmin_too(monkeypatch):
    client = _client(monkeypatch, roles=("SUPERADMIN",))
    r = client.get("/customers", params={"limit": 500})
    assert _ids(r) == {"C-NEW", "C-OLD", "C-ANCIENT"}


def test_the_recent_customer_is_actually_returned(monkeypatch):
    """A clamp that empties the screen is a worse bug than the one it fixes."""
    client = _client(monkeypatch)
    r = client.get("/customers", params={"limit": 500})
    body = r.json()
    assert body["customers"], "the clamp emptied the customer list entirely"
    assert body["customers"][0]["customer_id"] == "C-NEW"


def test_paging_deeper_cannot_walk_past_the_window(monkeypatch):
    """skip/limit are applied to the CLAMPED result set, so page 2 is not a
    second door onto the old rows."""
    client = _client(monkeypatch)
    r = client.get("/customers", params={"limit": 1, "skip": 1})
    assert _ids(r) == set(), _ids(r)


def test_the_store_and_channel_filters_survive_the_clamp(monkeypatch):
    """apply_horizon must ADD a bound, not replace the filter it is given."""
    client = _client(monkeypatch)
    client.get("/customers", params={"limit": 500})
    flt = client.repo.last_filter  # type: ignore[attr-defined]
    assert "created_at" in flt
    assert flt["$or"] == [
        {"home_store_id": STORE},
        {"preferred_store_id": STORE},
    ], flt


# ============================================================================
# 2. THE SEARCH BRANCH - the exemption, and the bypass it must not become
# ============================================================================


def test_naming_one_customer_returns_them_however_old(monkeypatch):
    """The owner's exemption. Staff serving Anita get Anita, full stop."""
    client = _client(monkeypatch)
    r = client.get("/customers", params={"search": "Ancient Anita"})
    assert r.status_code == 200, r.text
    assert _ids(r) == {"C-OLD"}, _ids(r)
    assert r.json()["pagination"]["total"] == 1


def test_naming_one_customer_by_full_number_also_lifts_the_window(monkeypatch):
    """The POS path: a cashier types the phone number in front of them."""
    client = _client(monkeypatch)
    r = client.get("/customers", params={"search": "9800000003"})
    assert _ids(r) == {"C-ANCIENT"}, _ids(r)


def test_a_fuzzy_search_matching_many_is_still_browsing(monkeypatch):
    """THE bypass test. 'Ancient' matches two customers - that is the book
    being browsed through the search box, not a person being served."""
    client = _client(monkeypatch)
    r = client.get("/customers", params={"search": "Ancient"})
    assert r.status_code == 200, r.text
    got = _ids(r)
    assert got == set(), (
        "a fuzzy multi-match search walked past the horizon and returned %s" % got
    )
    assert r.json()["pagination"]["total"] == 0, (
        "the search total leaked the hidden rows"
    )


def test_a_two_letter_fragment_is_not_a_named_lookup(monkeypatch):
    """One-character-search-empties-the-book, the failure mode the verifier
    exists to prevent. 'An' matches two here."""
    client = _client(monkeypatch)
    r = client.get("/customers", params={"search": "An"})
    assert _ids(r) == set(), _ids(r)


def test_a_fragment_that_resolves_to_one_but_names_nobody_is_not_exempt(
    monkeypatch,
):
    """'Resolved to one' is not enough on its own: a store holding a single
    customer would make ANY string a named lookup. The verifier checks the
    query against the returned record, so a fragment that the searcher matched
    only incidentally does not lift the window."""
    docs = [
        {
            "customer_id": "C-ONLY",
            "name": "Ancient Anita",
            "mobile": "9800000002",
            "home_store_id": STORE,
            "created_at": DAY_31,
            "notes": "walkin",
            "patients": [],
        }
    ]

    class _LooseRepo(_FakeCustomerRepo):
        def search_customers(self, query, store_id=None):
            # A matcher looser than we assume - it returns the single customer
            # for anything. This is the scenario condition (2) guards.
            return [dict(self.docs[0])]

    repo = _LooseRepo(docs)
    monkeypatch.setattr(customers_router, "get_customer_repository", lambda: repo)
    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_customer_repository", lambda: repo)

    app = FastAPI()
    app.include_router(customers_router.router, prefix="/customers")
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "u1",
        "roles": ["SALES_STAFF"],
        "active_store_id": STORE,
    }
    r = TestClient(app).get("/customers", params={"search": "zzz"})
    assert r.status_code == 200, r.text
    assert r.json()["customers"] == [], (
        "a string that names nobody was treated as a named lookup"
    )


def test_a_family_member_name_names_the_account(monkeypatch):
    """Searching a patient's own name is serving that person - the exemption
    covers it (the same rule base_repository.search already honours)."""
    docs = [
        {
            "customer_id": "C-FAM",
            "name": "Parent Prakash",
            "mobile": "9800000004",
            "home_store_id": STORE,
            "created_at": YEARS_AGO,
            "patients": [{"patient_id": "P1", "name": "Little Leela"}],
        }
    ]
    client = _client(monkeypatch, docs=docs)
    r = client.get("/customers", params={"search": "Little Leela"})
    assert _ids(r) == {"C-FAM"}, _ids(r)


def test_admin_search_is_untouched(monkeypatch):
    """Positive control on the search branch."""
    client = _client(monkeypatch, roles=("ADMIN",))
    r = client.get("/customers", params={"search": "Ancient"})
    assert _ids(r) == {"C-OLD", "C-ANCIENT"}, _ids(r)
    assert r.json()["pagination"]["total"] == 2


# ============================================================================
# 3. THE TYPE TRAP - what created_at actually IS at the write door
# ============================================================================


def test_created_at_is_a_real_datetime_on_the_write_path():
    """The clamp binds a datetime to `created_at`. If customers were stored
    with an ISO-STRING created_at the query would match NOTHING and the screen
    would go empty instead of narrowing.

    Pinned at the write door itself, not asserted about a fixture: this drives
    BaseRepository._add_timestamps, which every customer create goes through
    (POST /customers -> repo.create, and customer_service.ensure_customer ->
    repo.create). Note the second door builds a skeleton whose created_at is an
    ISO string - _add_timestamps OVERWRITES it, which is exactly what this
    test would catch if that ever changed."""
    from database.repositories.customer_repository import CustomerRepository

    class _Coll:
        def __init__(self):
            self.saved = None

        def insert_one(self, doc):
            self.saved = doc
            return type("R", (), {"inserted_id": doc.get("_id")})()

    coll = _Coll()
    repo = CustomerRepository(coll)
    repo.create({"name": "Type Trap", "mobile": "9800000009"})
    assert isinstance(coll.saved["created_at"], datetime), (
        "customers.created_at is not a BSON Date -- the datetime clamp on "
        "GET /customers would match nothing and empty the screen"
    )

    from api.services.customer_service import _build_skeleton

    skeleton = _build_skeleton(
        mobile="9800000010",
        name="Online Buyer",
        store_id=STORE,
        source="ONLINE",
        raw_phone=None,
        validated_extra={},
    )
    assert isinstance(skeleton["created_at"], str)  # the ISO string going in
    coll2 = _Coll()
    CustomerRepository(coll2).create(dict(skeleton))
    assert isinstance(coll2.saved["created_at"], datetime), (
        "the online/walkout customer minter stored an ISO-string created_at; "
        "those rows would be invisible to the browse clamp forever"
    )
