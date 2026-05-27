"""
IMS 2.0 - Walkouts follow-up approval tests (Module i, 3-round + approval)
==========================================================================
Owner asked: "walkouts also need to be tied with a follow up section,
making sure each walkout has been followed up properly with notes, 3
times follow up, call/text/visit notes with date and time, manager
approval that the follow up actually happened etc".

This suite covers:
  * Round 3 schedule (was rejected before)
  * Sales-staff marking DONE -> auto PENDING_APPROVAL with approval_required=True
  * Manager marking DONE -> auto APPROVED with their stamp
  * Sales-staff cannot call /approve endpoint (403, anti-fake-closure)
  * Manager APPROVES a round -> approval_status flips and audit row written
  * Manager REJECTS a round with manager_note -> note preserved
  * NOT REACHABLE / NOT REQUIRED statuses skip approval (approval_status=None)
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Re-use the patched_walkouts fixture + helpers from the main suite.
from test_walkouts import (  # noqa: E402,F401
    patched_walkouts,
    staff_headers_pune,
    manager_headers,
    _full_payload,
    _create_walkout,
    _today_iso,
)


# ----------------------------------------------------------------------------
# 3-round support
# ----------------------------------------------------------------------------


def test_followup_round_3_now_accepted(client, auth_headers, patched_walkouts):
    """Round 3 used to 422; the new schema accepts 1/2/3."""
    walkout = _create_walkout(client, auth_headers)
    wid = walkout["walkout_id"]

    resp = client.post(
        f"/api/v1/walkouts/{wid}/followups",
        json={
            "round": 3,
            "scheduled_date": _today_iso(),
            "scheduled_time": "11:00",
            "mode": "CALL",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert len(body["followups"]) == 1
    assert body["followups"][0]["round"] == 3
    assert body["followups"][0]["status"] == "PENDING"
    # Approval scaffolding present but inactive (nothing's DONE yet).
    fu = body["followups"][0]
    assert fu["approval_required"] is False
    assert fu["approval_status"] is None


def test_followup_round_4_still_rejected(client, auth_headers, patched_walkouts):
    """Round 4 is outside Literal[1,2,3] -> 422."""
    walkout = _create_walkout(client, auth_headers)
    resp = client.post(
        f"/api/v1/walkouts/{walkout['walkout_id']}/followups",
        json={"round": 4, "scheduled_date": _today_iso(), "mode": "CALL"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


# ----------------------------------------------------------------------------
# Salesperson vs manager DONE handling
# ----------------------------------------------------------------------------


def _seed_followup(client, headers, walkout_id, round_num=1, mode="CALL"):
    resp = client.post(
        f"/api/v1/walkouts/{walkout_id}/followups",
        json={
            "round": round_num,
            "scheduled_date": _today_iso(),
            "mode": mode,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text


def test_salesperson_marks_done_goes_pending_approval(
    client, auth_headers, staff_headers_pune, patched_walkouts
):
    """A salesperson flipping a follow-up to DONE leaves it awaiting a
    manager approval (approval_required + PENDING_APPROVAL). The
    completed_by stamp still says the salesperson."""
    # The walkout must be owned by the staff user (user-akshay) so the
    # staff token can edit the follow-up at all.
    walkout = _create_walkout(
        client, auth_headers, sales_person_id="user-akshay"
    )
    wid = walkout["walkout_id"]

    _seed_followup(client, staff_headers_pune, wid, round_num=1)
    resp = client.patch(
        f"/api/v1/walkouts/{wid}/followups/1",
        json={"status": "DONE", "notes": "Spoke to customer, will visit Sat"},
        headers=staff_headers_pune,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    fu = next(f for f in body["followups"] if f["round"] == 1)
    assert fu["status"] == "DONE"
    assert fu["completed_by"] == "user-akshay"  # the salesperson
    assert fu["approval_required"] is True
    assert fu["approval_status"] == "PENDING_APPROVAL"
    assert fu["approved_by_user_id"] is None
    assert fu["approved_at"] is None


def test_manager_marks_done_auto_approved(
    client, auth_headers, manager_headers, patched_walkouts
):
    """A manager flipping DONE self-approves: approval_status=APPROVED
    with their user_id stamped."""
    walkout = _create_walkout(client, auth_headers)
    wid = walkout["walkout_id"]

    _seed_followup(client, manager_headers, wid, round_num=2)
    resp = client.patch(
        f"/api/v1/walkouts/{wid}/followups/2",
        json={"status": "DONE", "notes": "Reached customer myself"},
        headers=manager_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    fu = next(f for f in body["followups"] if f["round"] == 2)
    assert fu["status"] == "DONE"
    assert fu["approval_required"] is True
    assert fu["approval_status"] == "APPROVED"
    assert fu["approved_by_user_id"] == "test-manager-001"
    assert fu["approved_at"]  # truthy ISO date


# ----------------------------------------------------------------------------
# /approve endpoint RBAC
# ----------------------------------------------------------------------------


def test_salesperson_cannot_call_approve_endpoint(
    client, auth_headers, staff_headers_pune, patched_walkouts
):
    """403 for sales staff trying to approve a follow-up — anti-fake-
    closure. Even if they themselves marked it DONE moments earlier."""
    walkout = _create_walkout(
        client, auth_headers, sales_person_id="user-akshay"
    )
    wid = walkout["walkout_id"]

    _seed_followup(client, staff_headers_pune, wid, round_num=1)
    client.patch(
        f"/api/v1/walkouts/{wid}/followups/1",
        json={"status": "DONE"},
        headers=staff_headers_pune,
    )

    resp = client.post(
        f"/api/v1/walkouts/{wid}/followups/1/approve",
        json={"decision": "APPROVED", "manager_note": "I worked hard"},
        headers=staff_headers_pune,
    )
    assert resp.status_code == 403, resp.text


def test_manager_approves_pending_followup(
    client, auth_headers, staff_headers_pune, manager_headers, patched_walkouts
):
    """A salesperson's DONE follow-up flips PENDING_APPROVAL -> APPROVED
    when the manager calls /approve. Audit row is written."""
    audit_repo = patched_walkouts["audit_repo"]
    walkout = _create_walkout(
        client, auth_headers, sales_person_id="user-akshay"
    )
    wid = walkout["walkout_id"]
    _seed_followup(client, staff_headers_pune, wid, round_num=1)
    client.patch(
        f"/api/v1/walkouts/{wid}/followups/1",
        json={"status": "DONE"},
        headers=staff_headers_pune,
    )

    resp = client.post(
        f"/api/v1/walkouts/{wid}/followups/1/approve",
        json={"decision": "APPROVED", "manager_note": "Verified with customer"},
        headers=manager_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    fu = next(f for f in body["followups"] if f["round"] == 1)
    assert fu["approval_status"] == "APPROVED"
    assert fu["approved_by_user_id"] == "test-manager-001"
    assert fu["manager_note"] == "Verified with customer"

    # Audit row exists
    approves = [
        d
        for d in audit_repo.collection.docs
        if d.get("action") == "walkout.followup.approve"
    ]
    assert len(approves) == 1
    audit = approves[0]
    assert audit["entity_id"] == wid
    assert audit["detail"]["round"] == 1
    assert audit["detail"]["from"] == "PENDING_APPROVAL"
    assert audit["detail"]["to"] == "APPROVED"


def test_manager_rejects_with_note(
    client, auth_headers, staff_headers_pune, manager_headers, patched_walkouts
):
    """REJECTED preserves the manager_note + writes a reject audit row."""
    audit_repo = patched_walkouts["audit_repo"]
    walkout = _create_walkout(
        client, auth_headers, sales_person_id="user-akshay"
    )
    wid = walkout["walkout_id"]
    _seed_followup(client, staff_headers_pune, wid, round_num=2)
    client.patch(
        f"/api/v1/walkouts/{wid}/followups/2",
        json={"status": "DONE"},
        headers=staff_headers_pune,
    )

    resp = client.post(
        f"/api/v1/walkouts/{wid}/followups/2/approve",
        json={
            "decision": "REJECTED",
            "manager_note": "Customer says nobody called",
        },
        headers=manager_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    fu = next(f for f in body["followups"] if f["round"] == 2)
    assert fu["approval_status"] == "REJECTED"
    assert fu["manager_note"] == "Customer says nobody called"

    rejects = [
        d
        for d in audit_repo.collection.docs
        if d.get("action") == "walkout.followup.reject"
    ]
    assert len(rejects) == 1
    assert rejects[0]["detail"]["manager_note"] == "Customer says nobody called"


def test_approve_rejects_non_done_followup(
    client, auth_headers, manager_headers, patched_walkouts
):
    """Approving a PENDING follow-up is 422 — there's nothing to verify."""
    walkout = _create_walkout(client, auth_headers)
    wid = walkout["walkout_id"]
    _seed_followup(client, manager_headers, wid, round_num=1)

    resp = client.post(
        f"/api/v1/walkouts/{wid}/followups/1/approve",
        json={"decision": "APPROVED"},
        headers=manager_headers,
    )
    assert resp.status_code == 422


# ----------------------------------------------------------------------------
# Non-DONE statuses skip approval
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["NOT REACHABLE", "NOT REQUIRED"])
def test_non_done_status_skips_approval(
    client, auth_headers, staff_headers_pune, patched_walkouts, status
):
    """NOT REACHABLE / NOT REQUIRED -> approval_required stays False
    and approval_status stays None; nothing for a manager to chase."""
    walkout = _create_walkout(
        client, auth_headers, sales_person_id="user-akshay"
    )
    wid = walkout["walkout_id"]
    _seed_followup(client, staff_headers_pune, wid, round_num=3)
    resp = client.patch(
        f"/api/v1/walkouts/{wid}/followups/3",
        json={"status": status, "notes": "tried"},
        headers=staff_headers_pune,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    fu = next(f for f in body["followups"] if f["round"] == 3)
    assert fu["status"] == status
    assert fu["approval_required"] is False
    assert fu["approval_status"] is None
