"""Batch b18 (CRM): refill supply, follow-up completion, NPS distribution.

L1-F18  a CANCELLED / DRAFT / fully-returned newer order must not reset a
        customer's contact-lens supply; only goods the customer received count.
L1-F19  coloured contact lenses are contact lenses at BOTH refill doors (the
        per-customer status and the store worklist), and an unreadable orders
        collection is a 503, never "no contact-lens history".
L1-F48  completing a follow-up keeps its context notes; a completion note goes
        into the attempt history.
L1-F50  owner ruling: Rescheduled asks a new date, saves it, and the follow-up
        STAYS PENDING (attempt kept). No/invalid/past date changes nothing.
L1-F51  the NPS score distribution covers the same population as the summary,
        not only the latest twenty responses.

Each test drives the real handler over strict in-memory doubles (unknown Mongo
operators raise). The same scenarios ran against a native Mongo clone; see the
PR body for that evidence.
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
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.routers import auth as auth_mod  # noqa: E402
from api.routers import crm as crm_mod  # noqa: E402
from api.routers import follow_ups as fu_mod  # noqa: E402
from api.routers import marketing as mkt_mod  # noqa: E402
from api.services import cl_refill as clr  # noqa: E402
from api.utils.ist import ist_today  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402

STORE = "BV-1"


def _run(coro):
    return asyncio.run(coro)


def _mgr(store=STORE):
    return {"user_id": "M1", "roles": ["STORE_MANAGER"], "store_ids": [store],
            "active_store_id": store}


# ---------------------------------------------------------------------------
# Contact-lens refill (L1-F18, L1-F19)
# ---------------------------------------------------------------------------


def _cl(oid, cid, days_ago, status, *, category="CONTACT_LENS", qty=1, returned=None):
    item = {"sku": "CL-1", "category": category, "modality": "MONTHLY", "pack_size": 1,
            "quantity": qty}
    if returned is not None:
        item["returned_qty"] = returned
    return {"order_id": oid, "customer_id": cid, "customer_name": cid, "store_id": STORE,
            "status": status,
            "created_at": (datetime.utcnow() - timedelta(days=days_ago)).isoformat(),
            "items": [item]}


class _Customers:
    def query_customer(self, cid):
        return {"customer_id": cid, "home_store_id": STORE}


def _wire_refill(monkeypatch, orders):
    sdb = StrictDB()
    sdb.seed("orders", orders)
    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: sdb)
    monkeypatch.setattr(crm_mod, "db", _Customers())
    return sdb


def _both_doors(cid):
    one = _run(crm_mod.get_cl_refill_status(customer_id=cid, current_user=_mgr()))
    due = _run(crm_mod.get_cl_refill_worklist(store_id=STORE, due_within_days=14,
                                              current_user=_mgr()))
    row = next((r for r in due["items"] if r["customer_id"] == cid), None)
    return one, row


@pytest.mark.parametrize("newer_status", ["CANCELLED", "DRAFT", "cancelled", "REFUNDED",
                                          "CONFIRMED", "READY"])
def test_newer_order_without_goods_does_not_reset_supply(monkeypatch, newer_status):
    _wire_refill(monkeypatch, [_cl("OLD", "C1", 40, "DELIVERED"),
                               _cl("NEW", "C1", 1, newer_status)])
    one, row = _both_doors("C1")
    assert one["last_cl_order_id"] == "OLD"
    assert one["days_remaining"] < 0  # still overdue
    assert row is not None and row["last_cl_order_id"] == "OLD" and row["overdue"] is True


@pytest.mark.parametrize("done", ["DELIVERED", "SHIPPED", "COMPLETED", "completed", "FULFILLED"])
def test_newer_received_order_does_reset_supply(monkeypatch, done):
    _wire_refill(monkeypatch, [_cl("OLD", "C1", 40, "DELIVERED"), _cl("NEW", "C1", 1, done)])
    one, row = _both_doors("C1")
    assert one["last_cl_order_id"] == "NEW" and one["days_remaining"] > 14
    assert row is None  # not due any more


def test_fully_returned_line_is_not_supply(monkeypatch):
    _wire_refill(monkeypatch, [_cl("OLD", "C1", 40, "DELIVERED"),
                               _cl("NEW", "C1", 2, "DELIVERED", returned=1)])
    one, row = _both_doors("C1")
    assert one["last_cl_order_id"] == "OLD"
    assert row is not None and row["last_cl_order_id"] == "OLD"


def test_partial_return_counts_only_kept_boxes():
    order = _cl("NEW", "C1", 0, "DELIVERED", qty=3, returned=2)
    line = clr.supplied_cl_line(order)
    assert line["quantity"] == 1
    assert clr.compute_refill(line, order["created_at"])["days_remaining"] in (29, 30)


@pytest.mark.parametrize("category", ["COLORED_CONTACT_LENS", "COLOUR_CONTACT_LENS", "CCL",
                                      "CONTACTLENS", "CONTACT_LENS"])
def test_every_cl_alias_gives_the_same_answer_at_both_doors(monkeypatch, category):
    _wire_refill(monkeypatch, [_cl("COL", "C1", 40, "DELIVERED", category=category)])
    one, row = _both_doors("C1")
    assert one["has_cl_history"] is True and one["last_cl_order_id"] == "COL"
    assert row is not None and row["last_cl_order_id"] == "COL"
    assert row["days_remaining"] == one["days_remaining"]


def test_campaign_cl_reorder_uses_the_same_supply_rule():
    from api.services import campaign_segments as seg

    sdb = StrictDB()
    sdb.seed("orders", [_cl("OLD", "C1", 40, "DELIVERED", category="COLORED_CONTACT_LENS"),
                        _cl("NEW", "C1", 1, "CANCELLED", category="COLORED_CONTACT_LENS")])
    sdb.seed("customers", [{"customer_id": "C1", "name": "C1", "phone": "9000000001",
                            "home_store_id": STORE}])
    rows = seg._resolve_cl_reorder(sdb, STORE)
    assert [r.get("customer_id") for r in rows] == ["C1"]


class _BrokenOrders:
    def find(self, *_a, **_k):
        raise RuntimeError("orders unreadable")


class _BrokenDB:
    is_connected = True

    def get_collection(self, _name):
        return _BrokenOrders()


@pytest.mark.parametrize("dbobj", [None, _BrokenDB()])
def test_unreadable_orders_is_503_at_both_doors(monkeypatch, dbobj):
    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: dbobj)
    monkeypatch.setattr(crm_mod, "db", _Customers())
    with pytest.raises(HTTPException) as one:
        _run(crm_mod.get_cl_refill_status(customer_id="C1", current_user=_mgr()))
    assert one.value.status_code == 503
    with pytest.raises(HTTPException) as due:
        _run(crm_mod.get_cl_refill_worklist(store_id=STORE, due_within_days=14,
                                            current_user=_mgr()))
    assert due.value.status_code == 503


def test_no_cl_orders_is_an_honest_no_history(monkeypatch):
    _wire_refill(monkeypatch, [])
    one = _run(crm_mod.get_cl_refill_status(customer_id="C1", current_user=_mgr()))
    assert one["has_cl_history"] is False


# ---------------------------------------------------------------------------
# Follow-up completion (L1-F48, L1-F50)
# ---------------------------------------------------------------------------


def _token(roles=("STORE_MANAGER",), store=STORE):
    return jwt.encode({"sub": "u1", "user_id": "u1", "username": "t", "roles": list(roles),
                       "store_ids": [store], "active_store_id": store,
                       "exp": datetime.utcnow() + timedelta(hours=1)},
                      auth_mod.SECRET_KEY, algorithm=auth_mod.ALGORITHM)


@pytest.fixture
def fu(monkeypatch):
    sdb = StrictDB()
    today = ist_today().isoformat()
    sdb.seed("follow_ups", [{"follow_up_id": "FU-1", "customer_id": "C1", "customer_name": "A",
                             "customer_phone": "9", "store_id": STORE, "type": "prescription_expiry",
                             "scheduled_date": today, "status": "pending", "outcome": None,
                             "notes": "call after 6pm", "created_at": "2026-11-01T00:00:00",
                             "completed_at": None, "completed_by": None}])
    monkeypatch.setattr(fu_mod, "_get_db", lambda: sdb)
    app = FastAPI()
    app.include_router(fu_mod.router, prefix="/api/v1/follow-ups")
    return TestClient(app), sdb.get_collection("follow_ups"), {"Authorization": f"Bearer {_token()}"}


def _doc(coll):
    return coll.find_one({"follow_up_id": "FU-1"})


@pytest.mark.parametrize("outcome", ["called_interested", "called_not_interested", "no_answer",
                                     "completed"])
def test_completing_keeps_the_context_notes(fu, outcome):
    client, coll, h = fu
    r = client.patch("/api/v1/follow-ups/FU-1/complete", headers=h,
                     json={"outcome": outcome, "notes": ""})
    assert r.status_code == 200, r.text
    d = _doc(coll)
    assert d["notes"] == "call after 6pm" and r.json()["notes"] == "call after 6pm"
    assert d["status"] == "completed" and d["outcome"] == outcome
    assert [a["outcome"] for a in d["attempts"]] == [outcome]


def test_a_completion_note_is_kept_beside_the_context(fu):
    client, coll, h = fu
    r = client.patch("/api/v1/follow-ups/FU-1/complete", headers=h,
                     json={"outcome": "called_interested", "notes": "will visit Sunday"})
    assert r.status_code == 200
    d = _doc(coll)
    assert d["notes"] == "call after 6pm"
    assert d["attempts"][0]["notes"] == "will visit Sunday"


@pytest.mark.parametrize("bad", [None, "", "not-a-date", "PAST"])
def test_rescheduled_without_a_valid_future_date_changes_nothing(fu, bad):
    client, coll, h = fu
    before = dict(_doc(coll))
    body = {"outcome": "rescheduled", "notes": ""}
    if bad == "PAST":
        body["next_date"] = (ist_today() - timedelta(days=1)).isoformat()
    elif bad is not None:
        body["next_date"] = bad
    r = client.patch("/api/v1/follow-ups/FU-1/complete", headers=h, json=body)
    assert r.status_code == 422, r.text
    assert _doc(coll) == before


def test_rescheduled_saves_the_new_date_and_stays_pending(fu):
    client, coll, h = fu
    new = (ist_today() + timedelta(days=10)).isoformat()
    old = _doc(coll)["scheduled_date"]
    s0 = client.get(f"/api/v1/follow-ups/summary?store_id={STORE}", headers=h).json()
    r = client.patch("/api/v1/follow-ups/FU-1/complete", headers=h,
                     json={"outcome": "rescheduled", "notes": "busy, call next week",
                           "next_date": new})
    assert r.status_code == 200, r.text
    d = _doc(coll)
    assert d["status"] == "pending" and d["scheduled_date"] == new
    assert d["notes"] == "call after 6pm"
    assert d.get("completed_at") is None and d.get("outcome") is None
    a = d["attempts"][-1]
    assert (a["outcome"], a["previous_date"], a["next_date"], a["notes"]) == (
        "rescheduled", old, new, "busy, call next week")
    s1 = client.get(f"/api/v1/follow-ups/summary?store_id={STORE}", headers=h).json()
    assert s0["due_today"] == 1 and s1["due_today"] == 0
    assert s1["pending_total"] == 1 and s1["completed_this_month"] == s0["completed_this_month"]
    # still workable: a later call can close it
    r2 = client.patch("/api/v1/follow-ups/FU-1/complete", headers=h,
                      json={"outcome": "called_interested", "notes": ""})
    assert r2.status_code == 200
    assert [x["outcome"] for x in _doc(coll)["attempts"]] == ["rescheduled", "called_interested"]


def test_a_closed_follow_up_is_not_overwritten(fu):
    client, coll, h = fu
    client.patch("/api/v1/follow-ups/FU-1/complete", headers=h,
                 json={"outcome": "called_interested", "notes": ""})
    r = client.patch("/api/v1/follow-ups/FU-1/complete", headers=h,
                     json={"outcome": "no_answer", "notes": ""})
    assert r.status_code == 409
    d = _doc(coll)
    assert d["outcome"] == "called_interested" and len(d["attempts"]) == 1


# ---------------------------------------------------------------------------
# NPS distribution (L1-F51)
# ---------------------------------------------------------------------------


def _nps(n_promoters, extra=()):
    base = datetime(2026, 11, 1)
    docs = [{"nps_id": f"P{i}", "store_id": STORE, "customer_id": f"C{i}", "score": 10,
             "created_at": (base - timedelta(minutes=i)).isoformat()} for i in range(n_promoters)]
    return docs + list(extra)


@pytest.mark.parametrize("n", [20, 250])
def test_distribution_covers_the_summary_population(monkeypatch, n):
    old_detractor = {"nps_id": "OLD", "store_id": STORE, "customer_id": "COLD", "score": 0,
                     "created_at": "2026-01-01T00:00:00"}
    unanswered = {"nps_id": "SENT", "store_id": STORE, "customer_id": "CX", "score": None,
                  "created_at": "2026-11-02T00:00:00"}
    sdb = StrictDB()
    sdb.seed("nps_responses", _nps(n, [old_detractor, unanswered]))
    monkeypatch.setattr(mkt_mod, "_get_db", lambda: sdb)
    out = _run(mkt_mod.get_nps_dashboard(store_id=STORE, customer_id=None, current_user=_mgr()))
    dist = out["score_distribution"]
    assert out["total_responses"] == n + 1 and out["total_surveys"] == n + 2
    assert sum(dist.values()) == out["total_responses"]
    assert dist["0"] == 1 == out["detractors"] and dist["10"] == n == out["promoters"]
    assert len(out["responses"]) == 20  # verbatim list stays the recent twenty


def test_customer_scope_reaches_an_older_response(monkeypatch):
    target = {"nps_id": "OLD", "store_id": STORE, "customer_id": "TARGET", "score": 3,
              "created_at": "2026-01-01T00:00:00"}
    sdb = StrictDB()
    sdb.seed("nps_responses", _nps(30, [target]))
    monkeypatch.setattr(mkt_mod, "_get_db", lambda: sdb)
    out = _run(mkt_mod.get_nps_dashboard(store_id=STORE, customer_id="TARGET",
                                         current_user=_mgr()))
    assert [r["customer_id"] for r in out["responses"]] == ["TARGET"]
    assert out["total_responses"] == 1 and out["score_distribution"]["3"] == 1
