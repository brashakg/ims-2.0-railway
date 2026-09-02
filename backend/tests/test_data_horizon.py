# ============================================================================
# The 30-day browse horizon (owner ruling 2026-09-01)
# ============================================================================
# Everyone except ADMIN / SUPERADMIN sees only the last 30 days when BROWSING.
# Looking up ONE customer by name or phone returns that person's ENTIRE history.
#
# Staff need everything about the person in front of them and nothing about the
# business. A 30-day browse window still supports the job - today's bills, this
# month's follow-ups, recent returns - while an unbounded list view hands a
# departing employee the customer book, the sales history and the turnover.
# This is a data-exfiltration control, so the NEGATIVE cases below matter more
# than the positive ones: a suite that only proved "an admin can still browse"
# would pass with the control deleted.

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from api.services.data_horizon import (  # noqa: E402
    BROWSE_HORIZON_DAYS,
    apply_horizon,
    horizon_start,
    is_unrestricted,
)

NOW = datetime(2026, 9, 1, 12, 0)
DAY_29 = NOW - timedelta(days=29)     # inside the window
DAY_31 = NOW - timedelta(days=31)     # outside it
CASHIER = {"user_id": "u1", "roles": ["SALES_STAFF"]}
MANAGER = {"user_id": "u2", "roles": ["STORE_MANAGER"]}
ADMIN = {"user_id": "u3", "roles": ["ADMIN"]}
SUPER = {"user_id": "u4", "roles": ["SUPERADMIN"]}


# --- who is limited ---------------------------------------------------------

@pytest.mark.parametrize("user", [CASHIER, MANAGER])
def test_ordinary_staff_are_limited_when_browsing(user):
    start = horizon_start(user, now=NOW)
    assert start is not None
    assert start == NOW - timedelta(days=BROWSE_HORIZON_DAYS)


@pytest.mark.parametrize("user", [ADMIN, SUPER])
def test_admin_and_superadmin_are_not(user):
    assert horizon_start(user, now=NOW) is None
    assert is_unrestricted(user)


def test_a_store_manager_is_NOT_unrestricted():
    """The rule says ADMIN and SUPERADMIN only. A manager runs a shop; he is not
    entitled to the whole chain's history."""
    assert not is_unrestricted(MANAGER)


def test_an_unknown_or_roleless_user_is_limited():
    """Fail CLOSED. A malformed token must not read as unrestricted."""
    assert horizon_start({}, now=NOW) is not None
    assert horizon_start(None, now=NOW) is not None
    assert horizon_start({"roles": "SALES_STAFF"}, now=NOW) is not None


# --- the exemption ----------------------------------------------------------

def test_naming_one_customer_lifts_the_window_entirely():
    """The whole point: serving a customer gives you their full history."""
    assert horizon_start(CASHIER, customer_scoped=True, now=NOW) is None


# --- the bypasses that would make this decorative ---------------------------

def test_asking_for_older_data_cannot_widen_the_window():
    """`?from_date=2020-01-01` must NOT defeat the clamp. Without this the rule
    is one query parameter away from being nothing at all."""
    q = {"created_at": {"$gte": datetime(2020, 1, 1)}}
    apply_horizon(q, CASHIER, now=NOW)
    assert q["created_at"]["$gte"] == NOW - timedelta(days=BROWSE_HORIZON_DAYS)


def test_a_narrower_request_keeps_its_own_bound():
    """Restricting yourself further is always allowed - only widening is not."""
    narrow = NOW - timedelta(days=3)
    q = {"created_at": {"$gte": narrow}}
    apply_horizon(q, CASHIER, now=NOW)
    assert q["created_at"]["$gte"] == narrow


def test_an_upper_bound_survives_the_clamp():
    q = {"created_at": {"$gte": datetime(2020, 1, 1), "$lte": NOW}}
    apply_horizon(q, CASHIER, now=NOW)
    assert q["created_at"]["$lte"] == NOW


def test_a_query_with_no_date_gains_one():
    q = {"store_id": "S1"}
    apply_horizon(q, CASHIER, now=NOW)
    assert q["created_at"]["$gte"] == NOW - timedelta(days=BROWSE_HORIZON_DAYS)
    assert q["store_id"] == "S1"


def test_an_admin_query_is_left_completely_alone():
    q = {"store_id": "S1"}
    apply_horizon(q, ADMIN, now=NOW)
    assert "created_at" not in q


def test_a_named_lookup_query_is_left_alone_for_ordinary_staff():
    q = {"customer_id": "C1"}
    apply_horizon(q, CASHIER, customer_scoped=True, now=NOW)
    assert "created_at" not in q


# --- the boundary itself ----------------------------------------------------

def test_day_29_is_visible_and_day_31_is_not():
    """The assertion that actually describes the rule to a reader."""
    start = horizon_start(CASHIER, now=NOW)
    assert DAY_29 >= start, "a bill from 29 days ago must still be browsable"
    assert DAY_31 < start, "a bill from 31 days ago must not be"


# ---------------------------------------------------------------------------
# THE FIELD THE CLAMP HANGS OFF. A pure-function test cannot catch this, and
# the first version of this control shipped past 32 green tests with the bug
# live: it bounded `prescription_date`, which the create door writes with
# `.isoformat()` (a STRING) and which is MISSING on 5 of the 8 prescriptions in
# production. A datetime `$gte` on a string field matches NOTHING in Mongo, so
# staff would have browsed to an EMPTY prescription list, and rows with no
# clinical date would have been hidden from a clamp meant to show 30 days.
# `created_at` is a real BSON Date on 100% of rows in both collections.
# ---------------------------------------------------------------------------

def test_the_prescription_repo_clamps_created_at_not_the_clinical_date():
    """Reads the real repository, so a change of field fails here."""
    import inspect

    from database.repositories.prescription_repository import PrescriptionRepository

    src = inspect.getsource(PrescriptionRepository.find_by_store)
    assert "created_after" in src, "the horizon bound is gone"
    # Strip the docstring first - it TALKS about created_at, and matching prose
    # instead of code is how an assertion passes while the code is wrong.
    body = src.split('"""')[-1]
    flat = " ".join(body.split())
    assert 'filter["created_at"] = {"$gte": created_after}' in flat, flat
    # ... and the clinical window must compare in the frame it is STORED in.
    assert ".isoformat()" in flat, (
        "prescription_date is stored as an ISO string; a datetime bound there "
        "matches nothing"
    )


def test_the_orders_repo_bounds_a_real_date_field():
    import inspect

    from database.repositories.order_repository import OrderRepository

    src = inspect.getsource(OrderRepository.find_by_store)
    assert 'snake["created_at"] = {"$gte": start}' in src


# ---------------------------------------------------------------------------
# GET /orders/search -- the horizon and its named-lookup exemption
# ---------------------------------------------------------------------------

import os as _os  # noqa: E402
import sys as _sys  # noqa: E402

_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), ".."))

from test_pos_cap_failclosed import priced_orders  # noqa: E402,F401


def _seed_two_orders(priced):
    """One order inside the window, one at day 45, same customer.

    Inserted straight into the collection: BaseRepository.create() overwrites
    created_at with now(), so a repo-seeded "old" order is not old and the
    probe would pass with the clamp removed.
    """
    coll = priced["db"].get_collection("orders")
    for oid, age in (("ORD-RECENT", 3), ("ORD-ANCIENT", 45)):
        coll.insert_one({
            "_id": oid, "order_id": oid, "order_number": oid,
            "store_id": "BV-TEST-01", "customer_id": "cust-x",
            "customer_name": "Test", "customer_phone": "9100000099",
            "status": "COMPLETED", "items": [],
            "created_at": datetime.utcnow() - timedelta(days=age),
        })


def _found(resp):
    # order_to_frontend RENAMES snake_case -> camelCase on the way out, so the
    # key on the wire is orderNumber. Read both or this probe silently compares
    # a set of Nones and passes whatever happens.
    return {
        o.get("orderNumber") or o.get("order_number")
        for o in (resp.json() or {}).get("orders", [])
    }


def test_order_number_search_is_clamped_to_the_window(
    client, staff_headers, priced_orders
):
    """Searching "ORD" is BROWSING -- it names no customer at all."""
    _seed_two_orders(priced_orders)
    r = client.get("/api/v1/orders/search?q=ORD", headers=staff_headers)
    assert r.status_code == 200, r.text
    got = _found(r)
    assert "ORD-RECENT" in got, got
    assert "ORD-ANCIENT" not in got, (
        "a two-letter order-number search returned a 45-day-old order: the "
        "browse horizon is bypassable from the POS search box"
    )


def _real_matching_customer_repo(priced):
    """A customer repo that actually matches on substrings.

    The FakeDB's collection does not implement $regex, so its search returns []
    for every query -- which would make the exemption untestable at the door
    and quietly report "clamped" as a pass. This stub matches the way the real
    Mongo search does, so the door test exercises the exemption rather than the
    fake's blind spot.
    """
    from api.routers import orders as orders_module

    class _Repo:
        def search_customers(self, q, store_id=None):
            n = (q or "").strip().lower()
            c = {"customer_id": "cust-x", "name": "Test",
                 "mobile": "9100000099", "phone": "9100000099"}
            hay = " ".join(str(c[k]).lower() for k in ("name", "mobile", "phone"))
            return [c] if n and n in hay else []

    priced["monkeypatch"].setattr(
        orders_module, "get_customer_repository", lambda: _Repo()
    )


def test_searching_one_customers_phone_returns_their_whole_history(
    client, staff_headers, priced_orders
):
    """The owner's exemption: the person in front of you, all of their history."""
    _seed_two_orders(priced_orders)
    _real_matching_customer_repo(priced_orders)
    r = client.get("/api/v1/orders/search?q=9100000099", headers=staff_headers)
    assert r.status_code == 200, r.text
    assert "ORD-ANCIENT" in _found(r), (
        "a named phone lookup was clamped -- staff cannot see the history of "
        "the customer they are serving"
    )


def test_a_string_that_names_nobody_stays_clamped_even_with_one_customer(
    client, staff_headers, priced_orders
):
    """"Resolved to exactly one customer" is not sufficient on its own.

    The stub here matches EVERY query -- a matcher looser than the Mongo regex
    we assume, which is what a search-field change or a name-normalising
    "improvement" would produce. Under a count-only exemption every string is
    then a named lookup and the window is gone. The exemption also verifies the
    query really is that customer's name or number, so a loosened matcher
    cannot silently open the book.
    """
    _seed_two_orders(priced_orders)
    from api.routers import orders as orders_module

    class _LooseRepo:
        def search_customers(self, q, store_id=None):
            return [{"customer_id": "cust-x", "name": "Test",
                     "mobile": "9100000099", "phone": "9100000099"}]

    priced_orders["monkeypatch"].setattr(
        orders_module, "get_customer_repository", lambda: _LooseRepo()
    )
    r = client.get("/api/v1/orders/search?q=ORD", headers=staff_headers)
    assert r.status_code == 200, r.text
    assert "ORD-ANCIENT" not in _found(r), (
        "a query naming nobody was treated as a named-customer lookup"
    )


def test_admin_search_is_never_clamped(client, auth_headers, priced_orders):
    _seed_two_orders(priced_orders)
    r = client.get("/api/v1/orders/search?q=ORD", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert "ORD-ANCIENT" in _found(r)
