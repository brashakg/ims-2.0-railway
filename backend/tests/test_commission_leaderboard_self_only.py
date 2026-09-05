"""IMS 2.0 - the sales/commission leaderboards redact TO SELF, like the points board.

OWNER RULING 2026-09-03, verbatim: "only admins and superadmins see all, rest
all including managers see their own only" -- plus their RANK. PR #1088 built
this for the /incentive/points boards behind ONE predicate,
points._viewer_visibility (over services/salary_visibility.is_salary_admin).
This file covers the three OTHER ranked-people lists that still handed every
name and rupee figure to a store manager:

    GET /api/v1/payroll/commission/leaderboard   (the /hr/leaderboard screen)
    GET /api/v1/payroll/commission/summary       (the "Commission This Month"
                                                  table on the same screen)
    GET /api/v1/analytics-v2/staff-leaderboard   (payroll's declared twin)

All three now go through points.self_only_rows -- the same trim, imported, never
re-typed -- so the rule cannot drift between routers.

WHY THESE TESTS LOOK THE WAY THEY DO (same discipline as
test_incentive_self_only.py): every test drives the ENDPOINT through the REAL
app (the session `client`), so the payroll router's mount gate is in force, and
asserts on the RESPONSE BODY including "the colleague's name/id appears nowhere
in the raw response text". None re-implements the rule; none asserts on prose.

DISCRIMINATING POWER, measured by reverting: with the self_only_rows call
removed from each handler, that handler's non-admin tests fail on the leak
assertion while the ADMIN positive controls stay green (see the PR body).

THE DATASET: one store, three sellers with three distinct revenues, so the
reader's rank (2 of 3) can only be right if it was computed against the FULL
field BEFORE the trim -- an implementation that trims first would rank the
reader 1 of 1.

Hermetic: strict in-memory Mongo doubles (tests/strict_fakes.py), frozen IST
clock, no live DB. No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-commission-self-only")
os.environ.setdefault("ENVIRONMENT", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.strict_fakes import StrictDB  # noqa: E402


# ===========================================================================
# The planted dataset -- three distinct sellers, three distinct revenues
# ===========================================================================

STORE = "ZZ-COMM-01"

COLLEAGUE_ID = "ZZ-EMP-COLLEAGUE"
COLLEAGUE_NAME = "Rekha Colleague"     # must never reach a non-admin
COLLEAGUE_REVENUE = 90000.0             # rank 1

READER_ID = "ZZ-EMP-READER"            # whoever is signed in, in every role
READER_NAME = "Reader Themselves"
READER_REVENUE = 60000.0                # rank 2 of 3 -- mid-field on purpose

THIRD_ID = "ZZ-EMP-THIRD"
THIRD_NAME = "Tarun Third"             # must never reach a non-admin
THIRD_REVENUE = 30000.0                 # rank 3

# Frozen IST clock: 15 June 2026 midday. Orders are stored naive-UTC; 10 June
# 10:00 UTC sits inside June for the payroll month window, the IST-month
# leaderboard window and the analytics twin alike.
FROZEN_NOW_IST = datetime(2026, 6, 15, 12, 0)
ORDER_AT = datetime(2026, 6, 10, 10, 0)

# Roles the payroll mount admits (main._FINANCE_ROLES) that are NOT salary
# admins -- the whole point of the ruling is that these see self only.
PAYROLL_NON_ADMIN_ROLES = ["STORE_MANAGER", "AREA_MANAGER", "ACCOUNTANT"]
# Roles the payroll mount REFUSES outright. Pinned so the report is honest:
# a floor role gets its own standing from /incentive/points/leaderboard, not
# from the HR screen. If the owner ever wants floor roles on the HR board,
# the change is the mount gate in api/main.py, and this is the test to edit.
PAYROLL_REFUSED_ROLES = ["SALES_STAFF", "CASHIER"]
# analytics-v2 is mounted with no gate: every signed-in role reaches it.
ANALYTICS_NON_ADMIN_ROLES = PAYROLL_NON_ADMIN_ROLES + PAYROLL_REFUSED_ROLES


def _seed_order(db, oid, staff_id, staff_name, amount):
    db.get_collection("orders").insert_one(
        {
            "order_id": oid,
            "store_id": STORE,
            "status": "COMPLETED",
            "created_at": ORDER_AT,
            "salesperson_id": staff_id,
            "salesperson_name": staff_name,
            "total_amount": amount,
            "grand_total": amount,
            "items": [],
        }
    )


@pytest.fixture
def seeded_orders(monkeypatch):
    """StrictDB wired into BOTH routers, frozen IST clock, 3 sellers."""
    fake_db = StrictDB()
    from api.routers import analytics_v2 as analytics_mod
    from api.routers import payroll as payroll_mod

    monkeypatch.setattr(payroll_mod, "_get_db", lambda: fake_db)
    monkeypatch.setattr(payroll_mod, "now_ist", lambda: FROZEN_NOW_IST)
    monkeypatch.setattr(analytics_mod, "_get_db", lambda: fake_db)
    monkeypatch.setattr(analytics_mod, "now_ist", lambda: FROZEN_NOW_IST)

    for uid, name in (
        (COLLEAGUE_ID, COLLEAGUE_NAME),
        (READER_ID, READER_NAME),
        (THIRD_ID, THIRD_NAME),
    ):
        fake_db.get_collection("users").insert_one({"user_id": uid, "full_name": name})
        fake_db.get_collection("salary_config").insert_one(
            {"employee_id": uid, "store_id": STORE, "commission_rate_percent": 10}
        )
    _seed_order(fake_db, "ZZ-O-1", COLLEAGUE_ID, COLLEAGUE_NAME, COLLEAGUE_REVENUE)
    _seed_order(fake_db, "ZZ-O-2", READER_ID, READER_NAME, READER_REVENUE)
    _seed_order(fake_db, "ZZ-O-3", THIRD_ID, THIRD_NAME, THIRD_REVENUE)
    return fake_db


def _headers(roles, user_id=READER_ID):
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": user_id,
            "username": user_id,
            "roles": roles,
            "store_ids": [STORE],
            "active_store_id": STORE,
        }
    )
    return {"Authorization": f"Bearer {token}"}


def _assert_no_colleague_in_raw_body(resp):
    """The leak check the UI cannot fake: the raw response TEXT must not
    carry the other people's ids or names. This is what devtools sees."""
    assert COLLEAGUE_ID not in resp.text
    assert COLLEAGUE_NAME not in resp.text
    assert THIRD_ID not in resp.text
    assert THIRD_NAME not in resp.text


# ===========================================================================
# GET /payroll/commission/leaderboard  (the /hr/leaderboard screen)
# ===========================================================================

LB = "/api/v1/payroll/commission/leaderboard?period=month"


@pytest.mark.parametrize("role", PAYROLL_NON_ADMIN_ROLES)
def test_commission_leaderboard_non_admin_gets_own_row_only(client, seeded_orders, role):
    """REQUIREMENT (owner 2026-09-03): a store/area manager or accountant
    receives ONLY their own row -- named, with revenue -- ranked against the
    full field, and no colleague's name or rupee figure anywhere in the body."""
    resp = client.get(LB, headers=_headers([role]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["visibility"] == "self"
    assert body["total_participants"] == 3
    assert [r["staff_id"] for r in body["leaderboard"]] == [READER_ID]
    own = body["leaderboard"][0]
    assert own["name"] == READER_NAME          # own name is NOT anonymised
    assert own["revenue"] == READER_REVENUE
    assert own["rank"] == 2                    # against the FULL field
    assert own["is_self"] is True
    _assert_no_colleague_in_raw_body(resp)
    assert str(COLLEAGUE_REVENUE) not in resp.text  # nor their money


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
def test_commission_leaderboard_admin_gets_full_board(client, seeded_orders, role):
    """POSITIVE CONTROL: the whole board for salary admins, every row named
    and ranked."""
    resp = client.get(LB, headers=_headers([role], user_id="ZZ-ADMIN-1"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["visibility"] == "all"
    assert body["total_participants"] == 3
    assert [r["staff_id"] for r in body["leaderboard"]] == [
        COLLEAGUE_ID, READER_ID, THIRD_ID,
    ]
    assert [r["name"] for r in body["leaderboard"]] == [
        COLLEAGUE_NAME, READER_NAME, THIRD_NAME,
    ]
    assert [r["rank"] for r in body["leaderboard"]] == [1, 2, 3]


def test_commission_leaderboard_manager_with_no_sales_gets_field_size_only(
    client, seeded_orders
):
    """A manager who sold nothing this month (the usual case) gets an empty
    list -- not the team's -- but still learns how many people are on it."""
    resp = client.get(LB, headers=_headers(["STORE_MANAGER"], user_id="ZZ-MGR-UNSOLD"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["visibility"] == "self"
    assert body["leaderboard"] == []
    assert body["total_participants"] == 3
    _assert_no_colleague_in_raw_body(resp)
    assert READER_NAME not in resp.text  # nobody's row, not just "not all"


@pytest.mark.parametrize("role", PAYROLL_REFUSED_ROLES)
def test_commission_leaderboard_floor_roles_are_refused_at_the_mount(
    client, seeded_orders, role
):
    """The payroll router is mounted behind require_roles(*_FINANCE_ROLES), so
    a floor role never reaches this handler: 403, and nothing leaks in the
    refusal. Their own standing lives on /incentive/points/leaderboard
    (test_incentive_self_only.py covers SALES_STAFF and CASHIER there)."""
    resp = client.get(LB, headers=_headers([role]))
    assert resp.status_code == 403, resp.text
    _assert_no_colleague_in_raw_body(resp)
    assert READER_NAME not in resp.text


# ===========================================================================
# GET /payroll/commission/summary  (the commission table on the same screen)
# ===========================================================================

SUMMARY = "/api/v1/payroll/commission/summary?month=6&year=2026"


@pytest.mark.parametrize("role", PAYROLL_NON_ADMIN_ROLES)
def test_commission_summary_non_admin_gets_own_row_and_own_total(
    client, seeded_orders, role
):
    """The per-person commission ledger is the same leak in rupees of PAY, so
    it gets the same trim -- and the store TOTAL is totalled AFTER the trim,
    because 'store total' beside 'my row' is the colleagues' commission by one
    subtraction (services/salary_visibility, second corollary)."""
    resp = client.get(SUMMARY, headers=_headers([role]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["visibility"] == "self"
    assert body["total_participants"] == 3
    assert [r["employee_id"] for r in body["items"]] == [READER_ID]
    own = body["items"][0]
    assert own["name"] == READER_NAME
    assert own["rank"] == 2
    assert own["commission_amount"] == READER_REVENUE * 0.10
    assert body["total_commission"] == own["commission_amount"]
    _assert_no_colleague_in_raw_body(resp)


def test_commission_summary_admin_gets_everyone_and_the_store_total(
    client, seeded_orders
):
    resp = client.get(SUMMARY, headers=_headers(["ADMIN"], user_id="ZZ-ADMIN-1"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["visibility"] == "all"
    assert [r["employee_id"] for r in body["items"]] == [
        COLLEAGUE_ID, READER_ID, THIRD_ID,
    ]
    assert body["total_commission"] == pytest.approx(
        (COLLEAGUE_REVENUE + READER_REVENUE + THIRD_REVENUE) * 0.10
    )


# ===========================================================================
# GET /analytics-v2/staff-leaderboard  (payroll's declared twin, no mount gate)
# ===========================================================================

TWIN = "/api/v1/analytics-v2/staff-leaderboard?period=month"


@pytest.mark.parametrize("role", ANALYTICS_NON_ADMIN_ROLES)
def test_analytics_twin_non_admin_gets_own_row_only(client, seeded_orders, role):
    """Every non-admin role -- SALES_STAFF and CASHIER included, since this
    router has no mount gate -- gets exactly their own row and their rank."""
    resp = client.get(TWIN, headers=_headers([role]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["visibility"] == "self"
    assert body["total_participants"] == 3
    assert [r["staff_id"] for r in body["leaderboard"]] == [READER_ID]
    assert body["leaderboard"][0]["name"] == READER_NAME
    assert body["leaderboard"][0]["rank"] == 2
    _assert_no_colleague_in_raw_body(resp)


def test_analytics_twin_admin_gets_full_board(client, seeded_orders):
    resp = client.get(TWIN, headers=_headers(["SUPERADMIN"], user_id="ZZ-ADMIN-1"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["visibility"] == "all"
    assert [r["staff_id"] for r in body["leaderboard"]] == [
        COLLEAGUE_ID, READER_ID, THIRD_ID,
    ]


# ===========================================================================
# ONE rule: the three handlers import the trim, they do not re-type it
# ===========================================================================


def test_the_three_handlers_share_the_points_trim():
    """Reintroduction guard (a supplement to the behavioural tests above,
    never a substitute): each router binds the SAME function object, so a
    private copy in any of them is a failure by name."""
    from api.routers import analytics_v2, payroll, points

    assert payroll.self_only_rows is points.self_only_rows
    assert analytics_v2.self_only_rows is points.self_only_rows


# ===========================================================================
# Fail closed: a self-only session the server cannot key on gets NOTHING
# ===========================================================================


def test_unidentifiable_non_admin_session_gets_nothing(client, seeded_orders):
    """A token with no user_id cannot be narrowed to self, so it must be
    refused outright -- never handed the whole board by accident."""
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "username": "ghost",
            "roles": ["STORE_MANAGER"],
            "store_ids": [STORE],
            "active_store_id": STORE,
        }
    )
    for path in (LB, SUMMARY, TWIN):
        resp = client.get(path, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (401, 403), f"{path}: {resp.status_code}"
        _assert_no_colleague_in_raw_body(resp)
        assert READER_ID not in resp.text
