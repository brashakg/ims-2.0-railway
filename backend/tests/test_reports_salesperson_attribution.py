"""The two owner-facing staff reports must credit the SALESPERSON, not the biller.

Live-in-stores bug: POS writes the salesperson picker to ``salesperson_id`` (and
denormalises ``salesperson_name`` beside it) -- see orders.py, which stamps both
onto the order doc. Both staff reports read ``sales_person_id``, the WALKOUTS
spelling that an order has never carried, so:

  * ``or order.get("created_by")`` always won -> every sale in the leaderboard
    was credited to whoever was logged in at the till, and
  * ``order.get("sales_person_name", person)`` fell back to its default -> the
    "name" column printed a raw user id.

These drive the real endpoint functions over the strict Mongo double, so the
credit rule and the name lookup are both exercised end to end.
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timedelta  # noqa: E402

import api.routers.reports as reports  # noqa: E402
from api.utils.ist import ist_today  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402

STORE = "BV-TEST-01"
# BUG-104: the reports window on the IST BUSINESS DAY, so the fixture's "today"
# must come from the IST clock too. `date.today()` is the box clock (UTC on CI),
# which between 18:30 and 24:00 UTC names the PREVIOUS IST day - the seeded
# orders would fall outside the window and the suite would fail only overnight.
TODAY = ist_today()

# Deliberately mirrors production: the seller has a full_name, the biller is the
# SUPERADMIN whose only name is a username, and a retired account names nobody.
USERS = [
    {"user_id": "u-sales", "full_name": "Rekha Sharma"},
    {"user_id": "u-biller", "username": "admin"},
]


class _CountingUsers:
    """users collection that counts reads -- an N+1 must fail, not merely crawl."""

    def __init__(self, inner):
        self._inner = inner
        self.reads = 0

    def find(self, flt=None, projection=None):
        self.reads += 1
        return self._inner.find(flt, projection)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _order(order_id, *, salesperson_id=None, salesperson_name=None,
           created_by="u-biller", grand_total=1000.0):
    doc = {
        "order_id": order_id,
        "store_id": STORE,
        "status": "CONFIRMED",
        "grand_total": grand_total,
        "created_by": created_by,
        # Stored naive-UTC instant, midday so the IST business-day window that
        # the router builds contains it whatever hour the suite runs at.
        "created_at": datetime.combine(TODAY, datetime.min.time())
        + timedelta(hours=12),
    }
    if salesperson_id:
        doc["salesperson_id"] = salesperson_id
    if salesperson_name:
        doc["salesperson_name"] = salesperson_name
    return doc


def _wire(monkeypatch, orders, users=USERS):
    db = StrictDB()
    db.seed("orders", orders)
    db.seed("users", users)
    counting = _CountingUsers(db.get_collection("users"))
    db._collections["users"] = counting

    from database.repositories.order_repository import OrderRepository

    order_repo = OrderRepository(db.get_collection("orders"))
    monkeypatch.setattr(reports, "get_db", lambda: db)
    monkeypatch.setattr(reports, "get_order_repository", lambda: order_repo)
    monkeypatch.setattr(reports, "validate_store_access", lambda sid, user: STORE)
    return db, counting


def _user():
    return {"user_id": "u-biller", "roles": ["ADMIN"], "active_store_id": STORE}


def _by_salesperson():
    return asyncio.run(
        reports.sales_by_salesperson(STORE, TODAY, TODAY, _user())
    )["data"]


def _ranking():
    return asyncio.run(reports.staff_ranking(STORE, TODAY, TODAY, _user()))["data"]


# ---------------------------------------------------------------------------
# The credit rule -- salesperson_id beats created_by, in BOTH reports
# ---------------------------------------------------------------------------


def test_by_salesperson_credits_the_seller_not_the_biller(monkeypatch):
    _wire(monkeypatch, [_order("O1", salesperson_id="u-sales")])
    rows = _by_salesperson()
    assert [r["id"] for r in rows] == ["u-sales"]
    assert rows[0]["name"] == "Rekha Sharma"
    assert rows[0]["name"] != "u-sales"
    assert rows[0]["orders"] == 1


def test_staff_ranking_credits_the_seller_not_the_biller(monkeypatch):
    _wire(monkeypatch, [_order("O1", salesperson_id="u-sales")])
    rows = _ranking()
    assert [r["staff_id"] for r in rows] == ["u-sales"]
    assert rows[0]["staff_name"] == "Rekha Sharma"
    assert rows[0]["staff_name"] != "u-sales"
    assert rows[0]["order_count"] == 1


def test_the_two_reports_agree_on_who_sold(monkeypatch):
    """One month, two screens: a manager must not see two different rosters."""
    _wire(
        monkeypatch,
        [
            _order("O1", salesperson_id="u-sales", grand_total=1000.0),
            _order("O2", salesperson_id="u-sales", grand_total=500.0),
            _order("O3", created_by="u-biller", grand_total=300.0),
        ],
    )
    a = {r["id"]: (r["name"], r["sales"]) for r in _by_salesperson()}
    b = {r["staff_id"]: (r["staff_name"], r["total_sales"]) for r in _ranking()}
    assert a == b
    assert a["u-sales"] == ("Rekha Sharma", 1500.0)


# ---------------------------------------------------------------------------
# Fallbacks -- old data must not become "Unknown", names must not be ids
# ---------------------------------------------------------------------------


def test_historical_order_without_a_salesperson_still_credits_created_by(monkeypatch):
    """Pre-picker orders carry created_by only. They keep attributing to that
    real person -- never to "Unknown"."""
    _wire(monkeypatch, [_order("OLD", created_by="u-biller")])
    rows = _by_salesperson()
    assert [r["id"] for r in rows] == ["u-biller"]
    assert rows[0]["name"] == "admin"
    assert [r["staff_id"] for r in _ranking()] == ["u-biller"]


def test_deleted_seller_falls_back_to_the_name_stored_on_the_order(monkeypatch):
    """The account is gone from users; the order still knows who sold."""
    _wire(
        monkeypatch,
        [_order("O1", salesperson_id="u-gone", salesperson_name="Anil Kumar")],
    )
    assert _by_salesperson()[0]["name"] == "Anil Kumar"
    assert _ranking()[0]["staff_name"] == "Anil Kumar"


def test_unresolvable_id_prints_as_the_id_never_an_invented_name(monkeypatch):
    _wire(monkeypatch, [_order("O1", salesperson_id="qa-efe824be3bc8")])
    assert _by_salesperson()[0]["name"] == "qa-efe824be3bc8"
    assert _ranking()[0]["staff_name"] == "qa-efe824be3bc8"


def test_response_shape_is_unchanged(monkeypatch):
    """The frontend reads these keys -- only the VALUES were wrong."""
    _wire(monkeypatch, [_order("O1", salesperson_id="u-sales")])
    assert set(_by_salesperson()[0]) == {"id", "name", "sales", "orders"}
    assert set(_ranking()[0]) == {
        "staff_id",
        "staff_name",
        "total_sales",
        "order_count",
        "avg_bill",
    }


# ---------------------------------------------------------------------------
# Degrading honestly
# ---------------------------------------------------------------------------


def test_names_are_batched_not_one_read_per_order(monkeypatch):
    _, users = _wire(
        monkeypatch,
        [_order("O%d" % i, salesperson_id="u-sales") for i in range(25)],
    )
    assert _by_salesperson()[0]["orders"] == 25
    assert users.reads == 1, users.reads


def test_a_failing_users_read_never_breaks_the_report(monkeypatch):
    """Names are decoration on a report that must still open."""
    db, _ = _wire(monkeypatch, [_order("O1", salesperson_id="u-sales")])

    class _Boom:
        def find(self, *a, **k):
            raise RuntimeError("users read failed")

    db._collections["users"] = _Boom()
    rows = _by_salesperson()
    assert rows[0]["id"] == "u-sales"
    assert rows[0]["name"] == "u-sales"
    assert _ranking()[0]["staff_id"] == "u-sales"
