# ============================================================================
# The duplicate-bill watch-list must not show one shop's claims to another's
# ============================================================================
# GET /expenses/duplicate-bills is an anti-fraud surface: expenses whose receipt
# hash matched an earlier one. `store_id` was an OPTIONAL, UNVALIDATED query
# parameter, so a STORE_MANAGER who simply left it off received flagged rows --
# employee_id, employee_name, amount, description, category -- from every store
# in the group. It was the only route in expenses.py where a store-level role
# could read across stores by omitting a parameter.
#
# Duplicates are detected WITHIN a store (same SHA-256 as a prior expense in the
# same store), so a cross-store row was never useful to a store manager either.
# It was purely a leak.
#
# THE OTHER DIRECTION MATTERS JUST AS MUCH, and is why the positive controls
# below outnumber the leak tests. _REVIEW_ROLES mixes ORG-WIDE reviewers
# (SUPERADMIN / ADMIN / ACCOUNTANT), for whom spotting the same bill claimed at
# two DIFFERENT shops is the entire point of the screen, with STORE-LEVEL ones
# (AREA_MANAGER / STORE_MANAGER). Scoping everyone would have removed the
# finding this screen exists to surface, and broken a live anti-fraud review
# workflow across four shops. A leak traded for a silent outage is not a fix.

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import expenses  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from strict_fakes import matches  # noqa: E402

MINE = "BV-BOK-01"      # the caller's own shop, Jharkhand
THEIRS = "WO-MUM-01"    # a different shop, different state, different entity

# Planted rows. The amounts and names are what a leak would carry, so the
# assertions search the WHOLE serialised body for them rather than trusting a
# particular key to be the one that leaked.
MY_ROW = {
    "expense_id": "EXP-MINE-1",
    "store_id": MINE,
    "duplicate_bill": True,
    "duplicate_of": "EXP-MINE-0",
    "employee_id": "u-mine",
    "employee_name": "Asha Kumari",
    "amount": 4321.5,
    "description": "Courier - twice claimed",
    "category": "supplies",
}
THEIR_ROW = {
    "expense_id": "EXP-THEIRS-1",
    "store_id": THEIRS,
    "duplicate_bill": True,
    "duplicate_of": "EXP-THEIRS-0",
    "employee_id": "u-theirs",
    "employee_name": "Rupesh Patil",
    "amount": 98765.25,
    "description": "Hotel - twice claimed",
    "category": "travel",
}
# A non-duplicate row, so a handler that dropped the duplicate_bill filter
# entirely would be caught rather than silently "passing".
NOT_FLAGGED = {
    "expense_id": "EXP-MINE-CLEAN",
    "store_id": MINE,
    "duplicate_bill": False,
    "employee_id": "u-mine",
    "employee_name": "Asha Kumari",
    "amount": 111.0,
    "description": "Ordinary claim",
    "category": "supplies",
}

ALL_ROWS = [MY_ROW, THEIR_ROW, NOT_FLAGGED]


class _FakeExpenseRepo:
    """Honours the filter through strict_fakes.matches.

    A fake that ignored the filter and returned every row would make the leak
    tests pass by accident and the scope tests fail for the wrong reason -- the
    "lying mock" this repo has been bitten by repeatedly. matches() also RAISES
    on an operator it does not implement, so a filter shape nobody anticipated
    fails loudly here instead of quietly matching everything.
    """

    def __init__(self, rows):
        self._rows = rows

    def find_many(self, filter_dict, limit=None, **kwargs):
        return [r for r in self._rows if matches(r, filter_dict)]


def _client(roles, *, active_store=MINE, store_ids=None):
    app = FastAPI()
    app.include_router(expenses.router, prefix="/expenses")

    async def _fake_user():
        return {
            "user_id": "u-mine",
            "full_name": "Asha Kumari",
            "active_store_id": active_store,
            "store_ids": store_ids if store_ids is not None else ([active_store] if active_store else []),
            "roles": roles,
        }

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


@pytest.fixture(autouse=True)
def _repo(monkeypatch):
    monkeypatch.setattr(
        expenses, "get_expense_repository", lambda: _FakeExpenseRepo(ALL_ROWS)
    )


def _ids(body):
    return {e["expense_id"] for e in body["expenses"]}


# ---------------------------------------------------------------------------
# THE REQUIREMENT
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["STORE_MANAGER", "AREA_MANAGER"])
def test_a_store_level_reviewer_omitting_store_id_does_not_see_another_shop(role):
    """The leak, asserted on the response BODY.

    Omitting the parameter used to mean "every store". It now means "mine".
    """
    r = _client([role]).get("/expenses/duplicate-bills")
    assert r.status_code == 200, r.text

    # Asserted FIRST so nothing later can shadow it, and against the raw body
    # so a leak through any key -- not just the one we expect -- is caught.
    assert THEIRS not in r.text, f"{role} received another shop's store_id"
    assert "Rupesh Patil" not in r.text, f"{role} received another shop's employee"
    assert "98765.25" not in r.text, f"{role} received another shop's amount"
    assert "Hotel - twice claimed" not in r.text

    assert _ids(r.json()) == {"EXP-MINE-1"}


@pytest.mark.parametrize("role", ["STORE_MANAGER", "AREA_MANAGER"])
def test_an_explicit_foreign_store_id_is_refused_not_honoured(role):
    """The other half of the same door. Defaulting the filter without validating
    an explicit value would leave the parameter itself as the bypass."""
    r = _client([role]).get(f"/expenses/duplicate-bills?store_id={THEIRS}")
    assert r.status_code == 403, r.text
    assert "Rupesh Patil" not in r.text
    assert "98765.25" not in r.text


def test_a_store_level_session_with_no_store_fails_closed():
    """A session we cannot pin to a store gets an error, never the unfiltered
    list. Falling through to "no filter" is precisely the bug being fixed, and
    it is the shape a careless refactor would reintroduce."""
    r = _client(["STORE_MANAGER"], active_store=None, store_ids=[]).get(
        "/expenses/duplicate-bills"
    )
    assert r.status_code == 400, r.text
    assert "Rupesh Patil" not in r.text
    assert "Asha Kumari" not in r.text


# ---------------------------------------------------------------------------
# POSITIVE CONTROLS -- over-tightening breaks a live anti-fraud workflow
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["STORE_MANAGER", "AREA_MANAGER"])
def test_a_store_level_reviewer_still_sees_their_own_flagged_rows(role):
    """Without this, "return nothing to everyone" would pass every test above."""
    body = _client([role]).get("/expenses/duplicate-bills").json()
    assert _ids(body) == {"EXP-MINE-1"}
    row = body["expenses"][0]
    assert row["amount"] == 4321.5
    assert row["employee_name"] == "Asha Kumari"
    assert row["duplicate_of"] == "EXP-MINE-0"


@pytest.mark.parametrize("role", ["ADMIN", "SUPERADMIN", "ACCOUNTANT"])
def test_an_org_wide_reviewer_still_sees_every_shop(role):
    """THE CONTROL THAT SHAPED THE FIX.

    Spotting the same bill claimed at two DIFFERENT shops is the whole point of
    this screen for these three. validate_store_access() returns the caller's
    active_store_id when no store_id is passed, so applying it uniformly -- the
    obvious fix, and the one the ticket proposed -- would have quietly narrowed
    an admin's watch-list to one shop and removed the finding it exists to
    surface. These roles have an active_store_id set here deliberately, because
    that is the case where the naive fix silently breaks.
    """
    body = _client([role]).get("/expenses/duplicate-bills").json()
    assert _ids(body) == {"EXP-MINE-1", "EXP-THEIRS-1"}


@pytest.mark.parametrize("role", ["ADMIN", "ACCOUNTANT"])
def test_an_org_wide_reviewer_may_still_filter_to_one_shop(role):
    """And the filter still works for them -- including for ACCOUNTANT, whom
    validate_store_access treats as store-level, so routing them through it
    would have 403'd them out of a store they are entitled to review."""
    body = _client([role]).get(f"/expenses/duplicate-bills?store_id={THEIRS}").json()
    assert _ids(body) == {"EXP-THEIRS-1"}


def test_only_flagged_rows_are_returned():
    """Guards the OTHER filter key: a handler that lost `duplicate_bill: True`
    would turn an anti-fraud watch-list into a full expense dump."""
    body = _client(["ADMIN"]).get("/expenses/duplicate-bills").json()
    assert "EXP-MINE-CLEAN" not in _ids(body)
