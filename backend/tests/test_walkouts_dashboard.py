"""
IMS 2.0 - Walkouts Dashboard QA Fix Tests
=========================================
Locks the fix from the 2026-05-27 QA sweep on /walkouts/dashboard:

  Defect 1 - "WALK-INS TODAY" top card disagreed with the per-staff
             grid totals for the same period.
  Defect 2 - per-salesperson grid rendered raw Mongo user_ids
             (user-areamgr-jh, user-sales-bok1, ...) instead of names.

Canonical reconciliation (see dashboard_per_staff docstring):

  walkin_counter.get_today(store).total
    == sum(items[i].walk_ins_today for i in items)

When walk-ins are logged without sales_person_id, the remainder is
surfaced as a synthetic "unattributed" row so the grid always sums to
the headline KPI. Display names use the priority chain
``full_name -> username -> user_id`` with "Unknown user" as the
deleted-user fallback (NEVER a raw user_id).

The tests reuse the in-memory FakeDB / patched_walkouts fixture from
test_walkouts.py so we exercise the live FastAPI router end-to-end
without a real Mongo.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Reuse the FakeDB + patched_walkouts fixture wiring.
from tests.test_walkouts import (  # noqa: E402  (path setup before import)
    patched_walkouts,
    _full_payload,
)


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _seed_users(patched, *user_docs):
    """Drop user docs into the FakeDB so _resolve_sales_person_name can
    find them. Each doc must carry user_id; full_name / username / name
    are optional."""
    users_coll = patched["db"].get_collection("users")
    for u in user_docs:
        users_coll.insert_one(dict(u))


def _user_repo_for_fake_db(patched):
    """Return a UserRepository wrapping the FakeDB users collection so
    the dashboard endpoint's _resolve_sales_person_name actually talks
    to our seeded data instead of the test-default stub that returns
    User-{uid} for every id."""
    from database.repositories.user_repository import UserRepository
    return UserRepository(patched["db"].get_collection("users"))


def _create_walkout(client, headers, **overrides):
    resp = client.post(
        "/api/v1/walkouts", json=_full_payload(**overrides), headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Defect 1 - count reconciliation
# ---------------------------------------------------------------------------


def test_count_reconciliation_grid_sums_to_top_card(
    client, auth_headers, patched_walkouts, monkeypatch
):
    """Top-card 'Walk-ins today' total must equal the sum of per-staff
    grid rows. The router emits a `totals.walk_ins_today` field that
    matches `GET /walkins/today.total`, and `sum(items[].walk_ins_today)`
    reconciles to it.
    """
    # Seed users so resolved names don't fall back to "Unknown user".
    _seed_users(
        patched_walkouts,
        {"user_id": "user-akshay", "full_name": "Akshay Sales"},
        {"user_id": "user-rupesh", "full_name": "Rupesh Sales"},
    )
    from api.routers import walkouts as walkouts_module
    monkeypatch.setattr(
        walkouts_module,
        "get_user_repository",
        lambda: _user_repo_for_fake_db(patched_walkouts),
    )

    walkin_repo = patched_walkouts["walkin_repo"]
    for mob in ("9888880001", "9888880002", "9888880003"):
        walkin_repo.auto_increment(
            store_id="BV-TEST-01",
            sales_person_id="user-akshay",
            mobile=mob,
        )
    for mob in ("9777770001", "9777770002"):
        walkin_repo.auto_increment(
            store_id="BV-TEST-01",
            sales_person_id="user-rupesh",
            mobile=mob,
        )

    # Top-card endpoint
    top_resp = client.get("/api/v1/walkouts/walkins/today", headers=auth_headers)
    assert top_resp.status_code == 200, top_resp.text
    top = top_resp.json()
    assert top["total"] == 5  # 3 + 2

    # Per-staff endpoint
    grid_resp = client.get(
        "/api/v1/walkouts/dashboard/per-staff", headers=auth_headers
    )
    assert grid_resp.status_code == 200, grid_resp.text
    grid = grid_resp.json()

    # Headline value matches between the two endpoints.
    assert grid["totals"]["walk_ins_today"] == top["total"]

    # Per-row sum reconciles to the headline.
    row_sum = sum(int(i["walk_ins_today"]) for i in grid["items"])
    assert row_sum == top["total"], (
        f"per-staff walk_ins_today rows sum to {row_sum} but headline "
        f"reports {top['total']} — grid must reconcile to top card"
    )


def test_count_reconciliation_with_unattributed_remainder(
    client, auth_headers, patched_walkouts, monkeypatch
):
    """When a walk-in is logged WITHOUT sales_person_id, it counts in
    the headline but not in any attributable per-staff row. The grid
    surfaces the remainder as a synthetic 'unattributed' row so the
    sum still reconciles to the top card.
    """
    _seed_users(
        patched_walkouts,
        {"user_id": "user-akshay", "full_name": "Akshay Sales"},
    )
    from api.routers import walkouts as walkouts_module
    monkeypatch.setattr(
        walkouts_module,
        "get_user_repository",
        lambda: _user_repo_for_fake_db(patched_walkouts),
    )

    walkin_repo = patched_walkouts["walkin_repo"]
    walkin_repo.auto_increment(
        store_id="BV-TEST-01",
        sales_person_id="user-akshay",
        mobile="9111111111",
    )
    # Manual topup without a sales_person_id - lives in the total but
    # not in per_staff.
    walkin_repo.manual_topup(
        store_id="BV-TEST-01",
        added_by="test-admin-001",
        delta=2,
        reason="2 customers browsed sunglasses, walked",
        sales_person_id=None,
    )

    top_resp = client.get("/api/v1/walkouts/walkins/today", headers=auth_headers)
    assert top_resp.status_code == 200
    top = top_resp.json()
    assert top["total"] == 3  # 1 POS attributed + 2 manual unattributed
    assert top["manual_topup"] == 2

    grid_resp = client.get(
        "/api/v1/walkouts/dashboard/per-staff", headers=auth_headers
    )
    assert grid_resp.status_code == 200
    grid = grid_resp.json()

    assert grid["totals"]["walk_ins_today"] == 3

    rows_by_id = {r["sales_person_id"]: r for r in grid["items"]}
    # Attributable row exists with its real walk_ins_today.
    assert rows_by_id["user-akshay"]["walk_ins_today"] == 1
    # Synthesized unattributed row carries the remainder.
    assert "unattributed" in rows_by_id, (
        "Expected a synthetic 'unattributed' row for un-attributed "
        "manual topups so the grid sums to the top card"
    )
    assert rows_by_id["unattributed"]["walk_ins_today"] == 2

    # And the sum reconciles to the headline.
    row_sum = sum(int(i["walk_ins_today"]) for i in grid["items"])
    assert row_sum == top["total"]


def test_no_unattributed_row_when_remainder_zero(
    client, auth_headers, patched_walkouts, monkeypatch
):
    """When every walk-in is attributed to a salesperson, the synthetic
    'unattributed' row must NOT appear (keeps the grid clean for the
    common case)."""
    _seed_users(
        patched_walkouts,
        {"user_id": "user-akshay", "full_name": "Akshay"},
    )
    from api.routers import walkouts as walkouts_module
    monkeypatch.setattr(
        walkouts_module,
        "get_user_repository",
        lambda: _user_repo_for_fake_db(patched_walkouts),
    )

    walkin_repo = patched_walkouts["walkin_repo"]
    walkin_repo.auto_increment(
        store_id="BV-TEST-01",
        sales_person_id="user-akshay",
        mobile="9000000001",
    )
    grid = client.get(
        "/api/v1/walkouts/dashboard/per-staff", headers=auth_headers
    ).json()
    row_ids = {r["sales_person_id"] for r in grid["items"]}
    assert "unattributed" not in row_ids


# ---------------------------------------------------------------------------
# Defect 2 - user IDs leaking into the UI
# ---------------------------------------------------------------------------


def test_user_id_resolved_to_name_never_bare_id(
    client, auth_headers, patched_walkouts, monkeypatch
):
    """Every per-staff row must carry a populated sales_person_name.
    The name must NOT be a bare 'user-*' Mongo id even for staff who
    appeared only via the walk-in counter (no walkout record)."""
    _seed_users(
        patched_walkouts,
        {"user_id": "user-areamgr-jh", "full_name": "Area Manager Jharkhand"},
        {"user_id": "user-sales-bok1", "full_name": "Sales Staff Bokaro 1"},
        {"user_id": "user-superadmin", "full_name": "Admin"},
    )
    from api.routers import walkouts as walkouts_module
    monkeypatch.setattr(
        walkouts_module,
        "get_user_repository",
        lambda: _user_repo_for_fake_db(patched_walkouts),
    )

    # These three appear ONLY via the walk-in counter (no walkout).
    walkin_repo = patched_walkouts["walkin_repo"]
    walkin_repo.auto_increment(
        store_id="BV-TEST-01", sales_person_id="user-areamgr-jh",
        mobile="9100000001",
    )
    walkin_repo.auto_increment(
        store_id="BV-TEST-01", sales_person_id="user-sales-bok1",
        mobile="9100000002",
    )
    for mob in ("9100000003", "9100000004", "9100000005"):
        walkin_repo.auto_increment(
            store_id="BV-TEST-01", sales_person_id="user-superadmin",
            mobile=mob,
        )

    grid = client.get(
        "/api/v1/walkouts/dashboard/per-staff", headers=auth_headers
    ).json()

    assert len(grid["items"]) == 3
    for row in grid["items"]:
        sp_id = row["sales_person_id"]
        sp_name = row["sales_person_name"]
        assert sp_name, f"sales_person_name empty for {sp_id}"
        assert sp_name != sp_id, (
            f"sales_person_name equals raw id ({sp_id}) - the UI would "
            f"display the bare Mongo id instead of the human name"
        )
        assert not sp_name.startswith("user-"), (
            f"sales_person_name starts with 'user-' ({sp_name}) - that "
            f"looks like a raw Mongo id leaked into the response"
        )

    by_id = {r["sales_person_id"]: r["sales_person_name"] for r in grid["items"]}
    assert by_id["user-areamgr-jh"] == "Area Manager Jharkhand"
    assert by_id["user-sales-bok1"] == "Sales Staff Bokaro 1"
    assert by_id["user-superadmin"] == "Admin"


def test_missing_user_falls_back_gracefully(
    client, auth_headers, patched_walkouts, monkeypatch
):
    """A walk-in / walkout attributed to a user_id that no longer
    exists in the users collection (deleted, archived, never seeded)
    must resolve to 'Unknown user' - never the raw user_id."""
    # Seed NO users at all.
    from api.routers import walkouts as walkouts_module
    monkeypatch.setattr(
        walkouts_module,
        "get_user_repository",
        lambda: _user_repo_for_fake_db(patched_walkouts),
    )

    walkin_repo = patched_walkouts["walkin_repo"]
    walkin_repo.auto_increment(
        store_id="BV-TEST-01",
        sales_person_id="user-ghost-deleted",
        mobile="9999999999",
    )

    grid = client.get(
        "/api/v1/walkouts/dashboard/per-staff", headers=auth_headers
    ).json()

    rows = grid["items"]
    assert len(rows) == 1
    row = rows[0]
    assert row["sales_person_id"] == "user-ghost-deleted"
    assert row["sales_person_name"] == "Unknown user", (
        f"Expected 'Unknown user' fallback for deleted user, got "
        f"{row['sales_person_name']!r}"
    )


# ---------------------------------------------------------------------------
# Mixed scenario - the exact QA case
# ---------------------------------------------------------------------------


def test_qa_repro_grid_reconciles_and_no_raw_ids(
    client, auth_headers, patched_walkouts, monkeypatch
):
    """Reproduce the 2026-05-27 QA observation:

      - Top card 'WALK-INS TODAY' = 2 (2 POS, 0 manual)
      - Grid previously showed 3 rows summing to 5 (walkouts MTD, not
        walk-ins today) AND used raw user_ids.

    Post-fix:
      - Grid `walk_ins_today` rows sum to 2 (matches top card).
      - All rows display human names, not raw 'user-*' Mongo ids.
    """
    _seed_users(
        patched_walkouts,
        {"user_id": "user-areamgr-jh", "full_name": "Area Manager Jharkhand"},
        {"user_id": "user-sales-bok1", "full_name": "Sales Staff Bokaro 1"},
        {"user_id": "user-superadmin", "full_name": "Admin"},
    )
    from api.routers import walkouts as walkouts_module
    monkeypatch.setattr(
        walkouts_module,
        "get_user_repository",
        lambda: _user_repo_for_fake_db(patched_walkouts),
    )

    # 2 POS walk-ins (one to areamgr, one to sales-bok1) to match the
    # top-card "2 POS - 0 manual".
    walkin_repo = patched_walkouts["walkin_repo"]
    walkin_repo.auto_increment(
        store_id="BV-TEST-01",
        sales_person_id="user-areamgr-jh",
        mobile="9100000010",
    )
    walkin_repo.auto_increment(
        store_id="BV-TEST-01",
        sales_person_id="user-sales-bok1",
        mobile="9100000011",
    )

    # Some walkouts MTD: 1 by areamgr, 1 by bok1, 3 by superadmin
    # (matches the QA report's "1 + 1 + 3 = 5" grid sum that confused
    # the user).
    _create_walkout(
        client, auth_headers,
        mobile="9200000001", sales_person_id="user-areamgr-jh",
    )
    _create_walkout(
        client, auth_headers,
        mobile="9200000002", sales_person_id="user-sales-bok1",
    )
    for i in range(3):
        _create_walkout(
            client, auth_headers,
            mobile=f"9200001{i:03d}",
            sales_person_id="user-superadmin",
        )

    # Top card
    top = client.get(
        "/api/v1/walkouts/walkins/today", headers=auth_headers
    ).json()
    assert top["total"] == 2
    assert top["pos_auto_count"] == 2
    assert top["manual_topup"] == 0

    # Grid
    grid = client.get(
        "/api/v1/walkouts/dashboard/per-staff", headers=auth_headers
    ).json()

    # 1) walk_ins_today rows reconcile to the top card.
    walk_ins_sum = sum(int(r["walk_ins_today"]) for r in grid["items"])
    assert walk_ins_sum == top["total"] == 2

    # 2) walkouts_mtd sum is still surfaced for the user, but as a
    # separate `totals` field so it's clearly a different dimension.
    assert grid["totals"]["walkouts_mtd"] == 5

    # 3) No row's sales_person_name is a raw 'user-*' id.
    for row in grid["items"]:
        sp_name = row["sales_person_name"]
        assert sp_name and not sp_name.startswith("user-"), (
            f"Row {row['sales_person_id']} displays raw id "
            f"({sp_name!r}) instead of a human name"
        )


# ---------------------------------------------------------------------------
# Unit test for the resolver name fallback (no FastAPI needed)
# ---------------------------------------------------------------------------


def test_resolve_name_priority_chain(patched_walkouts, monkeypatch):
    """Pure-unit: _resolve_sales_person_name uses
    ``name -> full_name -> username -> sales_person_id`` (existing
    behaviour, asserted so a future refactor doesn't change it
    silently)."""
    from api.routers import walkouts as walkouts_module

    _seed_users(
        patched_walkouts,
        {"user_id": "user-only-full", "full_name": "Full Name Only"},
        {"user_id": "user-only-name", "name": "Just Name"},
        {"user_id": "user-only-username", "username": "uname"},
        {"user_id": "user-empty"},  # no display fields at all
    )
    monkeypatch.setattr(
        walkouts_module,
        "get_user_repository",
        lambda: _user_repo_for_fake_db(patched_walkouts),
    )

    assert (
        walkouts_module._resolve_sales_person_name("user-only-name")
        == "Just Name"
    )
    assert (
        walkouts_module._resolve_sales_person_name("user-only-full")
        == "Full Name Only"
    )
    assert (
        walkouts_module._resolve_sales_person_name("user-only-username")
        == "uname"
    )
    # When the user is found but no display field is set, the existing
    # contract returns the id (last-resort). The dashboard endpoint
    # converts that to "Unknown user" itself; we don't change the
    # resolver to avoid breaking unrelated callers.
    assert (
        walkouts_module._resolve_sales_person_name("user-empty")
        == "user-empty"
    )
    # Truly missing user -> None.
    assert walkouts_module._resolve_sales_person_name("user-ghost") is None
