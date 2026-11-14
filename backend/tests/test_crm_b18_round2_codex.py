"""Batch b18 (CRM), Codex PR-1428 round 1 corrections + lead decision D50.

P2-a  the NBA call list (dismiss / complete) and the reactivation work-list
      close a follow-up through THE open predicate the follow-ups page uses:
      a follow-up already closed keeps its outcome (409), never overwritten.
P2-b  a linked follow-up write that FAILS is a 503 and the card stays; never a
      200 "done" while the follow-up is still pending.
P2-c  newer non-CL orders never hide a customer's CL history: both refill
      doors give the same answer after 51 frame sales.
P2-d  Auto-generate reads native BSON datetime eye-test / prescription dates
      (no 500), keeping the BUG-104 day; an unreadable date skips one row.
P2-e  the same reschedule repeated (a retry after a lost answer) records ONE
      attempt.
D50   refill clock: received goods only (not SHIPPED / PAID); each product /
      eye keeps its own clock from its latest received line; the earliest due
      drives the reminder.

Real handlers over StrictDB (unknown Mongo operators raise). The same cases
ran against a native Mongo clone through the real ASGI app with real
authentication (PR body / Codex retest commands).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("DISPATCH_MODE", "off")

import jwt  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.routers import auth as auth_mod  # noqa: E402
from api.routers import crm as crm_mod  # noqa: E402
from api.routers import follow_ups as fu_mod  # noqa: E402
from api.services import lapsed_reactivation as react  # noqa: E402
from api.services import nba_call_list as nba  # noqa: E402
from api.utils.ist import ist_today  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402

STORE = "BV-1"


def _run(coro):
    return asyncio.run(coro)


def _mgr(store=STORE):
    return {"user_id": "M1", "roles": ["STORE_MANAGER"], "store_ids": [store],
            "active_store_id": store}


def _hdr(roles=("STORE_MANAGER",), store=STORE):
    tok = jwt.encode({"sub": "u1", "user_id": "u1", "username": "t", "roles": list(roles),
                      "store_ids": [store], "active_store_id": store,
                      "exp": datetime.utcnow() + timedelta(hours=1)},
                     auth_mod.SECRET_KEY, algorithm=auth_mod.ALGORITHM)
    return {"Authorization": f"Bearer {tok}"}


# ---------------------------------------------------------------------------
# Refill (P2-c, D50)
# ---------------------------------------------------------------------------


class _Customers:
    def query_customer(self, cid):
        return {"customer_id": cid, "home_store_id": STORE}


def _order(oid, days_ago, items, status="DELIVERED", cid="C1"):
    return {"order_id": oid, "customer_id": cid, "customer_name": cid, "store_id": STORE,
            "status": status,
            "created_at": (datetime.utcnow() - timedelta(days=days_ago)).isoformat(),
            "items": items}


def _line(sku="CL-1", qty=1, eye=None, category="CONTACT_LENS"):
    item = {"sku": sku, "category": category, "modality": "MONTHLY", "pack_size": 1,
            "quantity": qty}
    if eye:
        item["eye"] = eye
    return item


def _both_doors(monkeypatch, orders, cid="C1"):
    sdb = StrictDB()
    sdb.seed("orders", orders)
    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: sdb)
    monkeypatch.setattr(crm_mod, "db", _Customers())
    one = _run(crm_mod.get_cl_refill_status(customer_id=cid, current_user=_mgr()))
    due = _run(crm_mod.get_cl_refill_worklist(store_id=STORE, due_within_days=120,
                                              current_user=_mgr()))
    row = next((r for r in due["items"] if r["customer_id"] == cid), None)
    return one, row


def test_newer_frame_sales_do_not_hide_cl_history(monkeypatch):
    # Codex CODEX1428-CAP: one CCL order 80 days ago, then 51 frame sales.
    orders = [_order("CL-OLD", 80, [_line(category="CCL")])]
    orders += [_order(f"FR-{i}", 79 - i, [{"sku": f"F{i}", "category": "FRAME", "quantity": 1}])
               for i in range(51)]
    one, row = _both_doors(monkeypatch, orders)
    assert one["has_cl_history"] is True and one["last_cl_order_id"] == "CL-OLD"
    assert one["days_remaining"] < 0
    assert row is not None and row["days_remaining"] == one["days_remaining"]


def test_each_eye_keeps_its_own_clock_earliest_due_drives(monkeypatch):
    # One order, the LEFT line first with three boxes (90 days), the RIGHT with
    # one (30 days). First-line-wins said "not due"; the right eye is overdue.
    orders = [_order("BOTH", 40, [_line(qty=3, eye="L"), _line(qty=1, eye="R")])]
    one, row = _both_doors(monkeypatch, orders)
    assert one["days_remaining"] < 0 and one["last_cl_order_id"] == "BOTH"
    assert row is not None and row["days_remaining"] == one["days_remaining"]


def test_each_product_keeps_its_own_clock(monkeypatch):
    # Product X ran out (40 days, one month); a newer order of product Y does
    # not restart X's clock. A newer order of X itself does (latest line).
    orders = [_order("X-OLD", 40, [_line(sku="X")]), _order("Y-NEW", 5, [_line(sku="Y", qty=6)])]
    one, row = _both_doors(monkeypatch, orders)
    assert one["last_cl_order_id"] == "X-OLD" and one["days_remaining"] < 0
    assert row is not None and row["last_cl_order_id"] == "X-OLD"

    orders.append(_order("X-NEW", 2, [_line(sku="X", qty=2)]))
    one, row = _both_doors(monkeypatch, orders)
    assert one["days_remaining"] > 14 and row["days_remaining"] == one["days_remaining"]


@pytest.mark.parametrize("status", ["SHIPPED", "PAID", "FULFILLED", "shipped"])
def test_shipped_or_paid_is_not_received(monkeypatch, status):
    orders = [_order("OLD", 40, [_line()]), _order("NEW", 1, [_line(qty=3)], status=status)]
    one, row = _both_doors(monkeypatch, orders)
    assert one["last_cl_order_id"] == "OLD" and row["last_cl_order_id"] == "OLD"


# ---------------------------------------------------------------------------
# NBA / reactivation closing doors (P2-a, P2-b)
# ---------------------------------------------------------------------------


def _fu(fid, cid, status="pending", outcome=None):
    return {"follow_up_id": fid, "customer_id": cid, "customer_name": cid, "customer_phone": "9",
            "store_id": STORE, "type": "nba_call", "scheduled_date": ist_today().isoformat(),
            "status": status, "outcome": outcome, "notes": "KEEP CONTEXT",
            "created_at": "2026-11-01T00:00:00", "completed_at": None, "completed_by": None}


def _card(cid, fid):
    return {"rank": 1, "customer_id": cid, "customer_name": cid, "customer_mobile": "9",
            "score": 50, "signals": [], "headline": "h", "follow_up_id": fid, "dismissed": False}


@pytest.fixture
def lists(monkeypatch):
    sdb = StrictDB()
    sdb.seed("nba_scores", [nba.build_nba_doc(STORE, [_card("C1", "FU-1")])])
    sdb.seed("reactivation_cohorts", [react.build_cohort_doc(
        STORE, [{"customer_id": "C1", "customer_name": "C1", "follow_up_id": "FU-1"},
                {"customer_id": "C2", "customer_name": "C2"}],
        date_str=react._today_ist(), lapse_months=24)])
    sdb.seed("follow_ups", [_fu("FU-1", "C1")])
    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: sdb)
    monkeypatch.setattr(crm_mod, "get_audit_repository", lambda: None)
    monkeypatch.setattr(fu_mod, "_get_db", lambda: sdb)
    app = FastAPI()
    app.include_router(crm_mod.router, prefix="/api/v1/crm")
    app.include_router(fu_mod.router, prefix="/api/v1/follow-ups")
    return TestClient(app), sdb


DOORS = {
    "nba_dismiss": (f"/api/v1/crm/nba/{STORE}/dismiss",
                    {"customer_id": "C1", "reason": "wrong_number"}),
    "nba_complete": (f"/api/v1/crm/nba/{STORE}/complete",
                     {"customer_id": "C1", "outcome_notes": "Called, will visit.",
                      "follow_up_scheduled_date": "2099-01-01"}),
    "reactivation": (f"/api/v1/crm/reactivation/{STORE}/log",
                     {"customer_id": "C1", "outcome": "wrong_number", "notes": "x"}),
}


@pytest.mark.parametrize("door", list(DOORS))
def test_a_closed_follow_up_keeps_its_outcome_at_every_door(lists, door):
    client, sdb = lists
    r = client.patch("/api/v1/follow-ups/FU-1/complete", headers=_hdr(),
                     json={"outcome": "called_interested", "notes": ""})
    assert r.status_code == 200
    url, body = DOORS[door]
    r = client.post(url, headers=_hdr(), json=body)
    assert r.status_code == 409, r.text
    fu = sdb.get_collection("follow_ups").find_one({"follow_up_id": "FU-1"})
    assert (fu["status"], fu["outcome"], len(fu["attempts"])) == (
        "completed", "called_interested", 1)
    assert fu["notes"] == "KEEP CONTEXT"
    # no second follow-up was scheduled on the refused call
    assert sdb.get_collection("follow_ups").count_documents({}) == 1


@pytest.mark.parametrize("door", list(DOORS))
def test_a_repeat_at_the_same_door_records_nothing_twice(lists, door):
    client, sdb = lists
    url, body = DOORS[door]
    assert client.post(url, headers=_hdr(), json=body).status_code == 200
    n = sdb.get_collection("follow_ups").count_documents({})
    r = client.post(url, headers=_hdr(), json=body)
    assert r.status_code == 409, r.text
    fu = sdb.get_collection("follow_ups").find_one({"follow_up_id": "FU-1"})
    assert len(fu["attempts"]) == 1
    assert sdb.get_collection("follow_ups").count_documents({}) == n


def test_reactivation_record_without_a_precreated_follow_up_is_written_once(lists):
    client, sdb = lists
    body = {"customer_id": "C2", "outcome": "reached", "notes": ""}
    r = client.post(f"/api/v1/crm/reactivation/{STORE}/log", headers=_hdr(), json=body)
    assert r.status_code == 200
    assert client.post(f"/api/v1/crm/reactivation/{STORE}/log", headers=_hdr(),
                       json=body).status_code == 409
    assert sdb.get_collection("follow_ups").count_documents({"customer_id": "C2"}) == 1


class _FailingFollowUps:
    """follow_ups whose writes fail (AutoReconnect-like); reads still work."""

    def __init__(self, coll):
        self._c = coll

    def find_one_and_update(self, *_a, **_k):
        raise ConnectionError("AutoReconnect: follow_ups write lost")

    update_one = find_one_and_update

    def __getattr__(self, name):
        return getattr(self._c, name)


@pytest.mark.parametrize("door", list(DOORS))
def test_a_failed_follow_up_write_is_never_a_success(lists, monkeypatch, door):
    client, sdb = lists
    real_get = sdb.get_collection

    def _get(name):
        coll = real_get(name)
        return _FailingFollowUps(coll) if name == "follow_ups" else coll

    monkeypatch.setattr(sdb, "get_collection", _get)
    url, body = DOORS[door]
    r = client.post(url, headers=_hdr(), json=body)
    assert r.status_code == 503, r.text
    monkeypatch.setattr(sdb, "get_collection", real_get)
    fu = sdb.get_collection("follow_ups").find_one({"follow_up_id": "FU-1"})
    assert fu["status"] == "pending" and not fu.get("attempts")
    # the card / entry is still on today's list for the retry, which then works
    nba_doc = sdb.get_collection("nba_scores").find_one({"store_id": STORE})
    coh = sdb.get_collection("reactivation_cohorts").find_one({"store_id": STORE})
    assert not any(c.get("dismissed") for c in nba_doc["cards"])
    assert not any(e.get("dismissed") for e in coh["entries"])
    assert client.post(url, headers=_hdr(), json=body).status_code == 200
    fu = sdb.get_collection("follow_ups").find_one({"follow_up_id": "FU-1"})
    assert fu["status"] in ("completed", "skipped") and len(fu["attempts"]) == 1


# ---------------------------------------------------------------------------
# Reschedule replay (P2-e)
# ---------------------------------------------------------------------------


def test_the_same_reschedule_twice_records_one_attempt(lists):
    client, sdb = lists
    new = (ist_today() + timedelta(days=5)).isoformat()
    body = {"outcome": "rescheduled", "notes": "", "next_date": new}
    for _ in range(2):  # the second press is the retry after a lost answer
        r = client.patch("/api/v1/follow-ups/FU-1/complete", headers=_hdr(), json=body)
        assert r.status_code == 200, r.text
        assert r.json()["scheduled_date"] == new and r.json()["status"] == "pending"
    fu = sdb.get_collection("follow_ups").find_one({"follow_up_id": "FU-1"})
    assert [a["outcome"] for a in fu["attempts"]] == ["rescheduled"]
    # a genuinely new date is still a new move
    later = (ist_today() + timedelta(days=9)).isoformat()
    r = client.patch("/api/v1/follow-ups/FU-1/complete", headers=_hdr(),
                     json={**body, "next_date": later})
    assert r.status_code == 200
    fu = sdb.get_collection("follow_ups").find_one({"follow_up_id": "FU-1"})
    assert len(fu["attempts"]) == 2 and fu["scheduled_date"] == later


# ---------------------------------------------------------------------------
# Auto-generate over native BSON dates (P2-d)
# ---------------------------------------------------------------------------


def test_auto_generate_reads_bson_datetime_dates(lists, monkeypatch):
    from datetime import date as _date

    client, sdb = lists
    day = ist_today()

    class _PinnedDate(_date):
        @classmethod
        def today(cls):
            return day

    monkeypatch.setattr(fu_mod, "date", _PinnedDate)
    # Native rows: BSON datetimes (late evening UTC keeps the UTC calendar day).
    test_dt = datetime.combine(day - timedelta(days=300), datetime.min.time()) + timedelta(hours=20)
    rx_dt = datetime.combine(day - timedelta(days=200), datetime.min.time()) + timedelta(hours=20)
    sdb.seed("eye_tests", [{"customer_id": "E1", "store_id": STORE, "created_at": test_dt}])
    sdb.seed("prescriptions", [{"customer_id": "P1", "store_id": STORE, "issue_date": rx_dt},
                               {"customer_id": "BAD", "store_id": STORE,
                                "issue_date": "15/01/2026"}])
    r = client.post(f"/api/v1/follow-ups/auto-generate?store_id={STORE}", headers=_hdr())
    assert r.status_code == 200, r.text
    assert r.json()["generated_count"] == 2 and r.json()["skipped_unreadable_dates"] == 1
    eye = sdb.get_collection("follow_ups").find_one({"customer_id": "E1"})
    rx = sdb.get_collection("follow_ups").find_one({"customer_id": "P1"})
    # The same BUG-104 day the ISO strings gave: the stored date part.
    assert eye["scheduled_date"] == (test_dt.date() + timedelta(days=365)).isoformat()
    assert rx["scheduled_date"] == (rx_dt.date() + timedelta(days=365)).isoformat()
    # and a rerun dedupes
    r = client.post(f"/api/v1/follow-ups/auto-generate?store_id={STORE}", headers=_hdr())
    assert r.json()["generated_count"] == 0
