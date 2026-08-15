# ============================================================================
# Only the claimant may declare their own expense finished
# ============================================================================
# POST /expenses/{expense_id}/submit flips a DRAFT to PENDING. It took
# `Depends(get_current_user)` and NOTHING else -- no ownership check, no store
# check. It was the only single-expense mutation in expenses.py with no
# object-level guard at all, so any authenticated user at any store could push
# another employee's draft into the approval queue by guessing its id.
#
# WHY OWN-ONLY, AND NOT THE USUAL APPROVER ESCAPE HATCH.
# The bill routes let approvers through, which is right: reading a colleague's
# receipt is reviewing the claim. Submitting is not reviewing -- it is the
# claimant saying "this one is finished". A DRAFT is unfinished working state by
# definition: the amount may be wrong, the receipt not yet attached. And the
# separation-of-duties guard on /approve only blocks the REQUESTER from
# approving; the requester stays the original employee_id, so an approver who
# could submit someone else's draft could then approve it -- advancing a claim
# its author never said was ready, end to end, alone.
#
# EVERY ASSERTION HERE IS ON THE WRITE, not on the status code alone and never
# on a message string. A handler that returns a polite 403 while still flipping
# the row is not a closed door -- that exact standard caught a live defect in
# the payroll work this week.

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

OWNER = "u-asha"
COLLEAGUE = "u-rupesh"
MY_STORE = "BV-BOK-01"
OTHER_STORE = "WO-MUM-01"

DRAFT = {
    "expense_id": "EXP-DRAFT-1",
    "store_id": MY_STORE,
    "employee_id": OWNER,
    "employee_name": "Asha Kumari",
    "status": "DRAFT",
    "amount": 2500.0,
    "category": "travel",
    "description": "Bus fare - receipt still to attach",
}


class _RecordingRepo:
    """Records every update() so the tests can assert on the WRITE.

    A fake that only returned rows would let a handler answer 403 while still
    flipping the status, and every test here would pass while the door stood
    open.
    """

    def __init__(self, row):
        self._row = dict(row)
        self.updates = []

    def find_by_id(self, expense_id):
        return dict(self._row) if expense_id == self._row["expense_id"] else None

    def update(self, expense_id, changes):
        self.updates.append((expense_id, dict(changes)))
        self._row.update(changes)
        return True


@pytest.fixture
def repo(monkeypatch):
    r = _RecordingRepo(DRAFT)
    monkeypatch.setattr(expenses, "get_expense_repository", lambda: r)
    return r


def _client(user_id, roles, store=MY_STORE):
    app = FastAPI()
    app.include_router(expenses.router, prefix="/expenses")

    async def _fake_user():
        return {
            "user_id": user_id,
            "full_name": "Test User",
            "active_store_id": store,
            "store_ids": [store],
            "roles": roles,
        }

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


def _submit(user_id, roles, store=MY_STORE):
    return _client(user_id, roles, store).post(
        f"/expenses/{DRAFT['expense_id']}/submit"
    )


# ---------------------------------------------------------------------------
# THE REQUIREMENT -- asserted on the write
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "roles",
    [["SALES_STAFF"], ["OPTOMETRIST"], ["CASHIER"], ["WORKSHOP_STAFF"]],
)
def test_a_colleague_cannot_submit_someone_elses_draft(repo, roles):
    r = _submit(COLLEAGUE, roles)

    # THE REQUIREMENT, first: nothing was written.
    assert repo.updates == [], f"{roles} advanced another employee's draft"
    assert repo._row["status"] == "DRAFT"
    assert r.status_code == 403, r.text


@pytest.mark.parametrize(
    "roles", [["STORE_MANAGER"], ["AREA_MANAGER"], ["ACCOUNTANT"], ["ADMIN"], ["SUPERADMIN"]]
)
def test_an_approver_cannot_submit_someone_elses_draft_either(repo, roles):
    """THE DELIBERATE DECISION, asserted in its own right.

    The bill routes DO let these roles through. Submit does not, and that
    difference is the point -- so it is pinned here rather than left to be
    inferred from the helper's default.
    """
    r = _submit(COLLEAGUE, roles)

    assert repo.updates == [], f"{roles} advanced another employee's draft"
    assert repo._row["status"] == "DRAFT"
    assert r.status_code == 403, r.text


def test_a_user_at_another_store_cannot_submit(repo):
    """The store dimension, which was equally missing."""
    r = _submit(COLLEAGUE, ["STORE_MANAGER"], store=OTHER_STORE)

    assert repo.updates == []
    assert repo._row["status"] == "DRAFT"
    assert r.status_code == 403, r.text


def test_a_stranger_cannot_tell_a_draft_from_a_pending_claim(repo):
    """Authz runs BEFORE the status check, so the 403 is identical whatever
    state the claim is in. Checking status first would have turned this route
    into an oracle for 'does expense X exist and is it a draft'."""
    repo._row["status"] = "PENDING"
    r = _submit(COLLEAGUE, ["SALES_STAFF"])

    assert r.status_code == 403, r.text
    assert "draft" not in r.text.lower()
    assert repo.updates == []


# ---------------------------------------------------------------------------
# POSITIVE CONTROLS -- without these, "refuse everyone" would pass
# ---------------------------------------------------------------------------

def test_the_owner_can_still_submit_their_own_draft(repo):
    r = _submit(OWNER, ["SALES_STAFF"])

    assert r.status_code == 200, r.text
    assert len(repo.updates) == 1, "the owner's submit did not write"
    _id, changes = repo.updates[0]
    assert _id == DRAFT["expense_id"]
    assert changes["status"] == "PENDING"
    assert changes.get("submitted_at")


def test_an_approver_can_still_submit_their_OWN_draft(repo):
    """Own-only cuts both ways: holding an approver role must not COST you the
    ability to submit your own claim."""
    repo._row["employee_id"] = COLLEAGUE
    r = _submit(COLLEAGUE, ["STORE_MANAGER"])

    assert r.status_code == 200, r.text
    assert len(repo.updates) == 1
    assert repo.updates[0][1]["status"] == "PENDING"


def test_the_owner_still_gets_the_not_a_draft_error(repo):
    """The status rule survives the new guard, for the person entitled to see it."""
    repo._row["status"] = "PENDING"
    r = _submit(OWNER, ["SALES_STAFF"])

    assert r.status_code == 400, r.text
    assert repo.updates == []
