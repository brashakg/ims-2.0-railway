"""
IMS 2.0 -- POS "My day" tile: GET /incentive/points/my-day is the caller's OWN day
================================================================================
The tile on the till shows the signed-in salesperson their own figures for
today. Two things must hold and both are pinned here against the REAL handler,
driven through a strict in-memory Mongo double that honours the filter it is
handed (a permissive fake would return every order to everybody and leave the
own-data tests unable to fail):

  1. OWN DATA ONLY. Two salespeople at one store; each sees only the bills
     credited to them, and the per-staff walk-in bucket is theirs alone.
  2. IST DAY BOUNDS (BUG-104). An order rung up at 00:30 IST is TODAY's sale
     even though it is 19:00 UTC of the previous calendar day on the box, and
     an order at 23:30 IST yesterday is NOT today's. A naive UTC-midnight bound
     gets both of those wrong; ``[:10]`` date faces are never used here.

Every assertion is on the RESPONSE BODY. The order fixtures carry exactly the
fields production writes: ``salesperson_id`` (orders.py stamps the POS picker
onto it; name_resolver.order_actor_id credits on it), ``grand_total``,
``status``, ``store_id`` and a BSON-Date ``created_at`` (BaseRepository's
_add_timestamps writes a datetime).
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strict_fakes import StrictDB  # noqa: E402

from api.routers import points as points_module  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.utils.ist import ist_day_start_utc  # noqa: E402

STORE = "BV-RAN-01"
PRIYA = "user-priya"
RAHUL = "user-rahul"


def _order(order_id, salesperson, created_at, total, status="CONFIRMED", store=STORE):
    return {
        "order_id": order_id,
        "store_id": store,
        "salesperson_id": salesperson,
        "grand_total": total,
        "status": status,
        "created_at": created_at,
    }


class FakeWalkinRepo:
    """Stand-in for WalkInCounterRepository.get_today: the per_staff bucket the
    POS "+1 walk-in" door writes, keyed by the salesperson's user id."""

    def __init__(self, per_staff=None):
        self.per_staff = dict(per_staff or {})
        self.asked_for = []

    def get_today(self, store_id, date_str=None):
        self.asked_for.append(store_id)
        return {
            "store_id": store_id,
            "total": sum(self.per_staff.values()),
            "per_staff": dict(self.per_staff),
        }


def _client(monkeypatch, *, user_id, roles=("SALES_STAFF",), db=None, walkins=None, store=STORE):
    app = FastAPI()
    app.include_router(points_module.router, prefix="/incentive/points")

    async def _fake_user():
        u = {
            "full_name": "Test User",
            "active_store_id": store,
            "store_ids": [store],
            "roles": list(roles),
        }
        if user_id is not None:
            u["user_id"] = user_id
        return u

    app.dependency_overrides[get_current_user] = _fake_user
    monkeypatch.setattr(points_module, "get_db", lambda: db if db is not None else StrictDB())
    monkeypatch.setattr(points_module, "get_walkin_counter_repository", lambda: walkins)
    return TestClient(app)


@pytest.fixture
def seeded_db():
    """Both salespeople bill today; Priya also has a bill from last night."""
    midnight = ist_day_start_utc()
    db = StrictDB()
    db.seed(
        "orders",
        [
            # 00:30 IST today == 19:00 UTC YESTERDAY on the box clock. A naive
            # UTC-midnight bound drops this bill; the IST bound keeps it.
            _order("o-priya-early", PRIYA, midnight + timedelta(minutes=30), 4500.0),
            _order("o-priya-noon", PRIYA, midnight + timedelta(hours=12), 7990.0),
            # 23:30 IST YESTERDAY -- not today's sale under either clock frame,
            # but a bound-less read would count it.
            _order("o-priya-last-night", PRIYA, midnight - timedelta(minutes=30), 100000.0),
            # Cancelled and still-draft bills are not sales.
            _order("o-priya-cancelled", PRIYA, midnight + timedelta(hours=2), 2000.0, status="CANCELLED"),
            _order("o-priya-draft", PRIYA, midnight + timedelta(hours=3), 3000.0, status="DRAFT"),
            # A colleague's bill today at the same store.
            _order("o-rahul-noon", RAHUL, midnight + timedelta(hours=12), 12490.0),
            # Priya's bill at ANOTHER store today -- not this session's store.
            _order("o-priya-other-store", PRIYA, midnight + timedelta(hours=5), 5000.0, store="BV-PUN-01"),
        ],
    )
    return db


class TestOwnDataOnly:
    def test_priya_sees_only_her_own_bills(self, monkeypatch, seeded_db):
        client = _client(monkeypatch, user_id=PRIYA, db=seeded_db)
        body = client.get("/incentive/points/my-day").json()
        assert body["user_id"] == PRIYA
        assert body["store_id"] == STORE
        assert body["bills_today"] == 2
        assert body["sales_today"] == pytest.approx(4500.0 + 7990.0)

    def test_rahul_sees_only_his_own_bill_never_priyas(self, monkeypatch, seeded_db):
        client = _client(monkeypatch, user_id=RAHUL, db=seeded_db)
        body = client.get("/incentive/points/my-day").json()
        assert body["user_id"] == RAHUL
        assert body["bills_today"] == 1
        assert body["sales_today"] == pytest.approx(12490.0)

    def test_someone_with_no_bills_gets_zero_not_a_colleagues_figures(self, monkeypatch, seeded_db):
        client = _client(monkeypatch, user_id="user-newjoiner", db=seeded_db)
        body = client.get("/incentive/points/my-day").json()
        assert body["bills_today"] == 0
        assert body["sales_today"] == 0

    def test_an_unidentifiable_session_is_refused_not_shown_the_store(self, monkeypatch, seeded_db):
        client = _client(monkeypatch, user_id=None, db=seeded_db)
        assert client.get("/incentive/points/my-day").status_code == 403


class TestIstDayBounds:
    def test_a_bill_at_00_30_ist_counts_as_today(self, monkeypatch):
        db = StrictDB()
        db.seed("orders", [_order("o1", PRIYA, ist_day_start_utc() + timedelta(minutes=30), 4500.0)])
        body = _client(monkeypatch, user_id=PRIYA, db=db).get("/incentive/points/my-day").json()
        assert body["bills_today"] == 1
        assert body["sales_today"] == pytest.approx(4500.0)

    def test_a_bill_at_23_30_ist_yesterday_does_not(self, monkeypatch):
        db = StrictDB()
        db.seed("orders", [_order("o1", PRIYA, ist_day_start_utc() - timedelta(minutes=30), 4500.0)])
        body = _client(monkeypatch, user_id=PRIYA, db=db).get("/incentive/points/my-day").json()
        assert body["bills_today"] == 0
        assert body["sales_today"] == 0


class TestConversion:
    def test_conversion_uses_the_callers_own_walkin_bucket(self, monkeypatch, seeded_db):
        walkins = FakeWalkinRepo({PRIYA: 4, RAHUL: 1})
        body = _client(monkeypatch, user_id=PRIYA, db=seeded_db, walkins=walkins).get(
            "/incentive/points/my-day"
        ).json()
        assert walkins.asked_for == [STORE]
        assert body["walkins_today"] == 4
        # 2 bills / 4 walk-ins
        assert body["conversion_pct"] == pytest.approx(50.0)

    def test_no_walkins_logged_against_me_means_no_conversion_field(self, monkeypatch, seeded_db):
        # Rahul has bills but nobody logged a walk-in under him: the
        # denominator does not exist, so the figure is omitted, never faked.
        walkins = FakeWalkinRepo({PRIYA: 4})
        body = _client(monkeypatch, user_id=RAHUL, db=seeded_db, walkins=walkins).get(
            "/incentive/points/my-day"
        ).json()
        assert body["bills_today"] == 1
        assert "conversion_pct" not in body
        assert "walkins_today" not in body

    def test_no_target_is_ever_invented(self, monkeypatch, seeded_db):
        body = _client(monkeypatch, user_id=PRIYA, db=seeded_db).get("/incentive/points/my-day").json()
        assert not any(k.startswith("target") for k in body)


def test_policy_row_is_open_to_every_pos_role():
    """New route => POLICY row (CI coverage lock), and it must not be a role
    list that could drop a cashier: the handler is self-only by construction."""
    from api.services import rbac_policy

    entry = rbac_policy.policy_for("GET", "/api/v1/incentive/points/my-day")
    assert entry is not None
    assert entry["allowed"] == rbac_policy.AUTHENTICATED
