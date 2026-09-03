"""IMS 2.0 - incentive leaderboard/scorecard reads redact TO SELF.

OWNER RULING 2026-09-03, verbatim: "only admins and superadmins see all, rest
all including managers see their own only." Deliberately stricter than the old
manager carve-out and consistent with the standing salary rule (ADMIN/SUPERADMIN
only, no accountant exception). The predicate is
services/salary_visibility.is_salary_admin -- ONE definition, enforced where the
data is produced (backend/api/routers/points.py), never in React.

WHY THESE TESTS LOOK THE WAY THEY DO (same discipline as
test_kicker_self_only.py): every test drives the ENDPOINT and asserts on the
RESPONSE BODY, including "the colleague's name/id appears nowhere in the raw
response text" -- because this exact leak class shipped twice in the last week
(rows hidden in the UI but sitting in the JSON). None of them re-implements the
rule; none asserts on a comment string.

DISCRIMINATING POWER, measured: with _viewer_visibility neutered to always
return ("all", None) -- i.e. the pre-ruling behaviour -- every self-only test in
this file fails on its leak assertion while the ADMIN positive controls stay
green. See the PR body for the mutation-run roll-call.

THE DATASET: one store, three scored people with three distinct totals, so the
reader's rank (2 of 3) can only be right if it was computed against the FULL
field BEFORE the trim -- a wrong implementation that trims first would rank the
reader 1 of 1.

Hermetic: strict in-memory Mongo doubles (tests/strict_fakes.py), no live DB.
No emoji (Windows cp1252).
"""

from __future__ import annotations

import os
import sys
from datetime import date as date_type

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-incentive-self-only")
os.environ.setdefault("ENVIRONMENT", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.strict_fakes import StrictDB  # noqa: E402


# ===========================================================================
# The planted dataset -- three distinct people, three distinct totals
# ===========================================================================

STORE = "ZZ-SELF-01"

COLLEAGUE_ID = "ZZ-EMP-COLLEAGUE"
COLLEAGUE_NAME = "Rekha Colleague"     # must never reach a non-admin
COLLEAGUE_TOTAL = 90                    # rank 1

READER_ID = "ZZ-EMP-READER"            # whoever is signed in, in every role
READER_NAME = "Reader Themselves"
READER_TOTAL = 60                       # rank 2 of 3 -- mid-field on purpose

THIRD_ID = "ZZ-EMP-THIRD"
THIRD_NAME = "Tarun Third"             # must never reach a non-admin
THIRD_TOTAL = 30                        # rank 3

FROZEN_TODAY = date_type(2026, 6, 15)
SEED_DATE = "2026-06-10"                # inside the 30-day window and June MTD

# Every role that is NOT a salary admin must land on the self-only branch --
# managers and accountant explicitly included (that is the point of the ruling).
NON_ADMIN_ROLES = [
    "STORE_MANAGER",
    "AREA_MANAGER",
    "ACCOUNTANT",
    "SALES_STAFF",
    "SALES_CASHIER",
    "CASHIER",
]


def _seed_log(db, staff_id, staff_name, total, date_str=SEED_DATE, store=STORE):
    db.get_collection("points_log").insert_one(
        {
            "store_id": store,
            "staff_id": staff_id,
            "staff_name": staff_name,
            "date_str": date_str,
            "deleted_at": None,
            "total": total,
            "eligibility": 0.8,
            "attendance": 9,
            "conversion": 16,
            "task": 9,
            "visufit": 8,
            "punctuality": 9,
            "behaviour": 9,
            "kicker_1": 5,
            "kicker_2": 5,
            "reviews": 8,
        }
    )


@pytest.fixture
def seeded_points(monkeypatch):
    """StrictDB wired into the points router, frozen clock, 3 scored staff."""
    fake_db = StrictDB()
    from api.routers import points as points_module

    monkeypatch.setattr(points_module, "get_db", lambda: fake_db)
    monkeypatch.setattr(points_module, "get_audit_repository", lambda: None)
    monkeypatch.setattr(points_module, "ist_today", lambda: FROZEN_TODAY)

    _seed_log(fake_db, COLLEAGUE_ID, COLLEAGUE_NAME, COLLEAGUE_TOTAL)
    _seed_log(fake_db, READER_ID, READER_NAME, READER_TOTAL)
    _seed_log(fake_db, THIRD_ID, THIRD_NAME, THIRD_TOTAL)
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
# GET /leaderboard
# ===========================================================================


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_leaderboard_non_admin_gets_own_row_only(client, seeded_points, role):
    """REQUIREMENT (owner 2026-09-03): a non-admin -- store manager and area
    manager included -- receives ONLY their own row, ranked against the full
    field."""
    resp = client.get(
        "/api/v1/incentive/points/leaderboard",
        headers=_headers([role]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["visibility"] == "self"
    assert body["total_participants"] == 3
    assert [r["staff_id"] for r in body["items"]] == [READER_ID]
    # Rank computed against the FULL field, not the trimmed list:
    assert body["items"][0]["rank"] == 2
    _assert_no_colleague_in_raw_body(resp)


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN"])
def test_leaderboard_admin_still_gets_full_board(client, seeded_points, role):
    """POSITIVE CONTROL: the whole board for salary admins, every row ranked."""
    resp = client.get(
        "/api/v1/incentive/points/leaderboard",
        headers=_headers([role], user_id="ZZ-ADMIN-1"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["visibility"] == "all"
    assert body["total_participants"] == 3
    assert [r["staff_id"] for r in body["items"]] == [
        COLLEAGUE_ID, READER_ID, THIRD_ID,
    ]
    assert [r["rank"] for r in body["items"]] == [1, 2, 3]


def test_leaderboard_manager_with_no_score_row_gets_empty_but_field_size(
    client, seeded_points
):
    """A manager who is not on the scoresheet (the usual case) gets an empty
    board -- not the team's -- but still learns how many people are on it."""
    resp = client.get(
        "/api/v1/incentive/points/leaderboard",
        headers=_headers(["STORE_MANAGER"], user_id="ZZ-MGR-UNSCORED"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["visibility"] == "self"
    assert body["items"] == []
    assert body["total_participants"] == 3
    _assert_no_colleague_in_raw_body(resp)
    assert READER_NAME not in resp.text  # nobody's row, not just "not all"


# ===========================================================================
# GET /mtd (the Module iii contract carries the same figures)
# ===========================================================================


def test_mtd_non_admin_self_only_admin_full(client, seeded_points):
    hdrs_mgr = _headers(["AREA_MANAGER"])
    resp = client.get(
        "/api/v1/incentive/points/mtd?year=2026&month=6", headers=hdrs_mgr
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["visibility"] == "self"
    assert body["total_participants"] == 3
    assert [r["staff_id"] for r in body["items"]] == [READER_ID]
    assert body["items"][0]["rank"] == 2
    _assert_no_colleague_in_raw_body(resp)

    resp = client.get(
        "/api/v1/incentive/points/mtd?year=2026&month=6",
        headers=_headers(["ADMIN"], user_id="ZZ-ADMIN-1"),
    )
    body = resp.json()
    assert body["visibility"] == "all"
    assert {r["staff_id"] for r in body["items"]} == {
        COLLEAGUE_ID, READER_ID, THIRD_ID,
    }


# ===========================================================================
# GET /daily (the scorecard screen's list)
# ===========================================================================


def test_daily_list_non_admin_gets_own_row_only(client, seeded_points):
    """The day sheet: a STORE_MANAGER no longer sees colleagues' saved rows.
    (They can still WRITE rows for anyone -- the write path is untouched.)"""
    resp = client.get(
        f"/api/v1/incentive/points/daily?date={SEED_DATE}",
        headers=_headers(["STORE_MANAGER"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["visibility"] == "self"
    assert [r["staff_id"] for r in body["items"]] == [READER_ID]
    _assert_no_colleague_in_raw_body(resp)


def test_daily_list_admin_gets_all_rows(client, seeded_points):
    resp = client.get(
        f"/api/v1/incentive/points/daily?date={SEED_DATE}",
        headers=_headers(["SUPERADMIN"], user_id="ZZ-ADMIN-1"),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["visibility"] == "all"
    assert {r["staff_id"] for r in body["items"]} == {
        COLLEAGUE_ID, READER_ID, THIRD_ID,
    }


# ===========================================================================
# GET /staff/{staff_id}/history
# ===========================================================================


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
def test_history_colleague_refused_for_non_admin(client, seeded_points, role):
    resp = client.get(
        f"/api/v1/incentive/points/staff/{COLLEAGUE_ID}/history"
        "?date_from=2026-06-01&date_to=2026-06-30",
        headers=_headers([role]),
    )
    assert resp.status_code == 403, resp.text
    _assert_no_colleague_in_raw_body(resp)


def test_history_own_and_admin_still_work(client, seeded_points):
    """POSITIVE CONTROLS: your own history is yours; an admin reads anyone's."""
    resp = client.get(
        f"/api/v1/incentive/points/staff/{READER_ID}/history"
        "?date_from=2026-06-01&date_to=2026-06-30",
        headers=_headers(["STORE_MANAGER"]),
    )
    assert resp.status_code == 200, resp.text
    assert [r["staff_id"] for r in resp.json()["items"]] == [READER_ID]

    resp = client.get(
        f"/api/v1/incentive/points/staff/{COLLEAGUE_ID}/history"
        "?date_from=2026-06-01&date_to=2026-06-30",
        headers=_headers(["ADMIN"], user_id="ZZ-ADMIN-1"),
    )
    assert resp.status_code == 200, resp.text
    assert [r["staff_id"] for r in resp.json()["items"]] == [COLLEAGUE_ID]


# ===========================================================================
# Fail closed: a self-only session the server cannot key on gets NOTHING
# ===========================================================================


def test_unidentifiable_non_admin_session_gets_nothing(client, seeded_points):
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
    for path in (
        "/api/v1/incentive/points/leaderboard",
        "/api/v1/incentive/points/mtd",
        f"/api/v1/incentive/points/daily?date={SEED_DATE}",
    ):
        resp = client.get(path, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code in (401, 403), f"{path}: {resp.status_code}"
        _assert_no_colleague_in_raw_body(resp)
        assert READER_ID not in resp.text
