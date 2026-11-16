"""Batch b18 (CRM), Codex PR-1428 round-2 report corrections + lead decision D69.

R2-1  two overlapping identical reactivation calls (no pre-created follow-up)
      store ONE outcome and ONE next reminder (ids are the records' _id).
R2-2  a retry with CHANGED content after a lost answer is refused (409); the
      outcome already saved is kept and nothing else is written.
R2-3  D69: when the call is saved but the requested next reminder is not, the
      answer is a 503 "Call saved. The next reminder was NOT set.", the card
      stays on the list, and the same request pressed again (twice) sets the
      reminder exactly once. A lost answer after the closing write or after
      the reminder write is repaired the same way.
R2-4  the store refill work-list reads the whole received-CL history: 2000
      newer CL orders of another customer no longer hide an overdue patient
      the per-customer door shows.
D69   lines of the same product and eye in ONE receipt are one supply.

StrictDB handlers (unknown Mongo operators raise) plus the same write paths on
a NATIVE Mongo when one is reachable (CI's service container, or locally
CRM_R6_NATIVE_MONGO=host:port/database on loopback).
"""

from __future__ import annotations

import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import urlparse

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("DISPATCH_MODE", "off")

import jwt  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.routers import auth as auth_mod  # noqa: E402
from api.routers import crm as crm_mod  # noqa: E402
from api.services import cl_refill as clr  # noqa: E402
from api.services import lapsed_reactivation as react  # noqa: E402
from api.services import nba_call_list as nba  # noqa: E402
from api.utils.ist import ist_today  # noqa: E402
from strict_fakes import StrictDB  # noqa: E402

STORE = "BV-1"
NEXT = (ist_today() + timedelta(days=7)).isoformat()


def _hdr():
    tok = jwt.encode({"sub": "u1", "user_id": "u1", "username": "t", "roles": ["STORE_MANAGER"],
                      "store_ids": [STORE], "active_store_id": STORE,
                      "exp": datetime.utcnow() + timedelta(hours=1)},
                     auth_mod.SECRET_KEY, algorithm=auth_mod.ALGORITHM)
    return {"Authorization": f"Bearer {tok}"}


def _fu(fid, cid):
    return {"follow_up_id": fid, "customer_id": cid, "customer_name": cid, "store_id": STORE,
            "type": "nba_call", "scheduled_date": ist_today().isoformat(), "status": "pending",
            "outcome": None, "notes": "KEEP CONTEXT", "created_at": "2026-11-01T00:00:00"}


def _seed(db, run=""):
    """C1 linked to a pre-created follow-up on both lists; C2 unlinked."""
    c1, c2 = f"C1{run}", f"C2{run}"
    db.get_collection("nba_scores").insert_one(nba.build_nba_doc(STORE, [
        {"rank": 1, "customer_id": c1, "customer_name": c1, "follow_up_id": f"FU-1{run}",
         "dismissed": False},
        {"rank": 2, "customer_id": c2, "customer_name": c2, "follow_up_id": None,
         "dismissed": False}]))
    db.get_collection("reactivation_cohorts").insert_one(react.build_cohort_doc(
        STORE, [{"customer_id": c1, "customer_name": c1, "follow_up_id": f"FU-1{run}"},
                {"customer_id": c2, "customer_name": c2}],
        date_str=react._today_ist(), lapse_months=24))
    db.get_collection("follow_ups").insert_one(_fu(f"FU-1{run}", c1))
    return c1, c2


def _client(monkeypatch, db):
    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: db)
    monkeypatch.setattr(crm_mod, "get_audit_repository", lambda: None)
    app = FastAPI()
    app.include_router(crm_mod.router, prefix="/api/v1/crm")
    return TestClient(app)


def _complete(cid):
    return (f"/api/v1/crm/nba/{STORE}/complete",
            {"customer_id": cid, "outcome_notes": "Called, will visit.",
             "follow_up_scheduled_date": NEXT})


def _react(cid, **over):
    return (f"/api/v1/crm/reactivation/{STORE}/log",
            {"customer_id": cid, "outcome": "scheduled_visit", "notes": "Synthetic call",
             "follow_up_scheduled_date": NEXT, **over})


def _rows(db, cid):
    return list(db.get_collection("follow_ups").find({"customer_id": cid}))


def _entry(db, coll, array, cid):
    doc = db.get_collection(coll).find_one({f"{array}.customer_id": cid})
    return next(e for e in doc[array] if e["customer_id"] == cid)


class _Faulty:
    """follow_ups with one injected fault: ``fail`` raises BEFORE the write,
    ``lose`` performs the write and then loses the answer."""

    def __init__(self, coll, method, when, mode):
        self._c, self._m, self._when, self._mode = coll, method, when, mode

    def __getattr__(self, name):
        real = getattr(self._c, name)
        if name != self._m:
            return real

        def wrapped(*a, **k):
            if not self._when(*a):
                return real(*a, **k)
            if self._mode == "fail":
                raise ConnectionError("AutoReconnect: injected write failure")
            real(*a, **k)
            raise ConnectionError("AutoReconnect: write landed, answer lost")
        return wrapped


def _inject(monkeypatch, db, method, when, mode):
    real_get = db.get_collection

    def _get(name):
        coll = real_get(name)
        return _Faulty(coll, method, when, mode) if name == "follow_ups" else coll

    monkeypatch.setattr(db, "get_collection", _get)
    return lambda: monkeypatch.setattr(db, "get_collection", real_get)


def _is_next(doc, *_):
    return isinstance(doc, dict) and doc.get("status") == "pending"


# ---------------------------------------------------------------------------
# Native Mongo (CI service container, or an explicitly named local clone)
# ---------------------------------------------------------------------------


def _native_db():
    spec = os.environ.get("CRM_R6_NATIVE_MONGO")
    try:
        from pymongo import MongoClient
    except ImportError:
        return None
    if spec:
        hostport, name = spec.split("/", 1)
        host, port = hostport.split(":")
        assert host in ("127.0.0.1", "localhost") and name.startswith("ims_claude_"), spec
    elif os.environ.get("CI") and os.environ.get("MONGODB_URL", "").startswith("mongodb://localhost"):
        u = urlparse(os.environ["MONGODB_URL"])
        host, port, name = u.hostname, str(u.port or 27017), "ims_test_crm_r6_codex"
    else:
        return None
    url = f"mongodb://{host}:{port}"
    from conftest import mongo_reachable  # THE suite's one connect probe

    if not mongo_reachable(url):
        return None
    db = MongoClient(url, connect=False)[name]
    assert db.name == name
    return db


class _NativeDB:
    """A real pymongo database, with ``get_collection`` patchable per test."""

    def __init__(self, db):
        self._db = db

    def get_collection(self, name):
        return self._db.get_collection(name)


@pytest.fixture(params=["strict", "native"])
def store(request, monkeypatch):
    if request.param == "strict":
        db = StrictDB()
        run = ""
    else:
        raw = _native_db()
        if raw is None:
            pytest.skip("no native Mongo reachable")
        db = _NativeDB(raw)
        run = "-" + uuid.uuid4().hex[:8]  # this run's own rows only
    c1, c2 = _seed(db, run)
    return _client(monkeypatch, db), db, c1, c2


# ---------------------------------------------------------------------------
# R2-1 overlapping identical calls
# ---------------------------------------------------------------------------


def test_two_overlapping_identical_calls_store_one_outcome_and_one_reminder(store, monkeypatch):
    client, db, _c1, c2 = store
    barrier = threading.Barrier(2)
    real_get = db.get_collection

    class _Hold:
        def __init__(self, coll):
            self._c = coll

        def __getattr__(self, name):
            return getattr(self._c, name)

        def find_one(self, q=None, *a, **k):
            out = self._c.find_one(q, *a, **k)
            if q and q.get("entries.customer_id") == c2:
                barrier.wait(timeout=10)  # both requests have read the list
            return out

    monkeypatch.setattr(db, "get_collection",
                        lambda n: _Hold(real_get(n)) if n == "reactivation_cohorts" else real_get(n))
    url, body = _react(c2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(lambda _: client.post(url, headers=_hdr(), json=body), [1, 2]))
    monkeypatch.setattr(db, "get_collection", real_get)
    assert [a.status_code for a in answers] == [200, 200], [a.text for a in answers]
    assert answers[0].json() == answers[1].json()
    rows = _rows(db, c2)
    assert sorted(r["status"] for r in rows) == ["completed", "pending"], rows
    assert len({r["follow_up_id"] for r in rows}) == 2


# ---------------------------------------------------------------------------
# R2-2 changed-content retry after a lost answer
# ---------------------------------------------------------------------------


def test_changed_retry_after_a_lost_answer_is_refused_and_keeps_the_outcome(store, monkeypatch):
    client, db, _c1, c2 = store
    url, body = _react(c2)
    undo = _inject(monkeypatch, db, "insert_one",
                   lambda doc, *_: doc.get("customer_id") == c2 and doc.get("status") != "pending",
                   "lose")
    assert client.post(url, headers=_hdr(), json=body).status_code == 503
    undo()
    changed = {**body, "outcome": "wrong_number", "notes": "CHANGED after uncertain answer"}
    r = client.post(url, headers=_hdr(), json=changed)
    assert r.status_code == 409, r.text
    rows = _rows(db, c2)
    assert [(x["outcome"], x["notes"]) for x in rows] == [("scheduled_visit", "Synthetic call")]
    # the original action, pressed again, finishes (its next reminder) once
    r = client.post(url, headers=_hdr(), json=body)
    assert r.status_code == 200, r.text
    assert sorted(x["status"] for x in _rows(db, c2)) == ["completed", "pending"]


def test_changed_retry_on_a_linked_follow_up_is_refused(store, monkeypatch):
    client, db, c1, _c2 = store
    url, body = _complete(c1)
    undo = _inject(monkeypatch, db, "find_one_and_update", lambda *a: True, "lose")
    assert client.post(url, headers=_hdr(), json=body).status_code == 503
    undo()
    r = client.post(url, headers=_hdr(), json={**body, "outcome_notes": "Something else said."})
    assert r.status_code == 409, r.text
    fu = db.get_collection("follow_ups").find_one({"customer_id": c1, "status": "completed"})
    assert [a["notes"] for a in fu["attempts"]] == ["Called, will visit."]
    assert db.get_collection("follow_ups").count_documents({"customer_id": c1}) == 1


# ---------------------------------------------------------------------------
# R2-3 / D69 next reminder not saved, lost answers around it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("door", ["complete", "reactivation"])
def test_call_saved_but_next_reminder_not_set_says_so_and_retry_sets_it_once(store, monkeypatch, door):
    client, db, c1, _c2 = store
    url, body = _complete(c1) if door == "complete" else _react(c1)
    coll, array = (("nba_scores", "cards") if door == "complete"
                   else ("reactivation_cohorts", "entries"))
    undo = _inject(monkeypatch, db, "insert_one", _is_next, "fail")
    r = client.post(url, headers=_hdr(), json=body)
    undo()
    assert r.status_code == 503, r.text
    assert r.json()["detail"].startswith("Call saved. The next reminder was NOT set.")
    assert [x["status"] for x in _rows(db, c1)] == ["completed"]
    assert not _entry(db, coll, array, c1).get("dismissed")  # never shown as done
    first = client.post(url, headers=_hdr(), json=body)
    second = client.post(url, headers=_hdr(), json=body)  # Retry pressed twice
    assert first.status_code == second.status_code == 200, (first.text, second.text)
    assert first.json() == second.json() and first.json()["next_follow_up_id"]
    rows = _rows(db, c1)
    assert sorted(x["status"] for x in rows) == ["completed", "pending"]
    done = next(x for x in rows if x["status"] == "completed")
    assert len(done["attempts"]) == 1
    assert _entry(db, coll, array, c1)["dismissed"] is True


@pytest.mark.parametrize("door", ["complete", "reactivation"])
@pytest.mark.parametrize("where", ["close", "next"])
def test_a_lost_answer_is_repaired_by_the_identical_retry(store, monkeypatch, door, where):
    client, db, c1, _c2 = store
    url, body = _complete(c1) if door == "complete" else _react(c1)
    if where == "close":
        undo = _inject(monkeypatch, db, "find_one_and_update", lambda *a: True, "lose")
    else:
        undo = _inject(monkeypatch, db, "insert_one", _is_next, "lose")
    assert client.post(url, headers=_hdr(), json=body).status_code == 503
    undo()
    r = client.post(url, headers=_hdr(), json=body)
    assert r.status_code == 200, r.text
    rows = _rows(db, c1)
    assert sorted(x["status"] for x in rows) == ["completed", "pending"]
    assert len(next(x for x in rows if x["status"] == "completed")["attempts"]) == 1


def test_unlinked_nba_card_reminder_failure_is_not_called_saved(store, monkeypatch):
    # No follow-up behind the card: nothing about the call is stored yet, so
    # the answer must not claim it was saved.
    client, db, _c1, c2 = store
    url, body = _complete(c2)
    undo = _inject(monkeypatch, db, "insert_one", _is_next, "fail")
    r = client.post(url, headers=_hdr(), json=body)
    undo()
    assert r.status_code == 503 and not r.json()["detail"].startswith("Call saved")
    assert not _entry(db, "nba_scores", "cards", c2).get("dismissed")
    assert client.post(url, headers=_hdr(), json=body).status_code == 200
    assert client.post(url, headers=_hdr(), json=body).status_code == 200
    assert [x["status"] for x in _rows(db, c2)] == ["pending"]


# ---------------------------------------------------------------------------
# R2-4 and D69 refill
# ---------------------------------------------------------------------------


def _order(oid, cid, when, items):
    return {"order_id": oid, "customer_id": cid, "customer_name": cid, "store_id": STORE,
            "status": "DELIVERED", "created_at": when.isoformat(), "items": items}


def _cl(sku="P", qty=1):
    return {"sku": sku, "category": "CCL", "modality": "MONTHLY", "pack_size": 1, "quantity": qty}


def test_the_store_work_list_reads_past_2000_newer_cl_orders(monkeypatch):
    now = datetime.utcnow()
    orders = [_order("OLD", "OLD-C", now - timedelta(days=80), [_cl("OLDP")])]
    orders += [_order(f"F{i}", "FILLER", now - timedelta(seconds=i), [_cl("FILL", 20)])
               for i in range(2000)]
    sdb = StrictDB()
    sdb.seed("orders", orders)

    class _Customers:
        def query_customer(self, cid):
            return {"customer_id": cid, "home_store_id": STORE}

    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: sdb)
    monkeypatch.setattr(crm_mod, "db", _Customers())
    client = _client(monkeypatch, sdb)
    one = client.get("/api/v1/crm/customers/OLD-C/cl-refill-status", headers=_hdr()).json()
    due = client.get(f"/api/v1/crm/cl-refill/{STORE}/due?due_within_days=120", headers=_hdr()).json()
    row = next((x for x in due["items"] if x["customer_id"] == "OLD-C"), None)
    assert one["days_remaining"] < 0
    assert row is not None and row["days_remaining"] == one["days_remaining"]


def test_same_product_lines_in_one_receipt_are_one_supply():
    now = datetime.utcnow()
    when = now - timedelta(days=40)

    def refill(items):
        order = _order("O", "C", when, items)
        return clr.earliest_refill([(order, line) for line in clr.supplied_cl_lines(order)], now=now)

    split = refill([_cl(qty=1), _cl(qty=1)])
    combined = refill([_cl(qty=2)])
    assert split["days_remaining"] == combined["days_remaining"] > 0
    # different eyes stay separate clocks (D50): the one-box eye drives
    eyes = refill([{**_cl(qty=3), "eye": "L"}, {**_cl(qty=1), "eye": "R"}])
    assert eyes["days_remaining"] < 0
    # an OLDER receipt of the same product is not added to the newer one
    older = _order("OLD", "C", now - timedelta(days=200), [_cl(qty=12)])
    newer = _order("NEW", "C", when, [_cl(qty=1)])
    supplies = [(newer, line) for line in clr.supplied_cl_lines(newer)]
    supplies += [(older, line) for line in clr.supplied_cl_lines(older)]
    assert clr.earliest_refill(supplies, now=now)["days_remaining"] < 0
