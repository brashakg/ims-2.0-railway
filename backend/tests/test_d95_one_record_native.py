"""
IMS 2.0 - D95 (#1397, Claude's reviewers on head a72aec88): ONE record per
counter return, written insert-once, decides who owns it. NATIVE Mongo.
==========================================================================
Before (handovers/evidence/round13-d95/PR-1397, claude_d95_probe.py defect,
native Mongo, real routes): the press's failed record insert answered 201
beside the record Needs completing wrote (cash handed twice); without the
fail-soft uniq_returns_return_id index the press and "Cash paid" each wrote a
COMPLETED record of one return (Rs 1,800 netted for Rs 900); a landed record
whose 503 was abandoned was netted at Day-End and netted again by the OWED
path; "Cash paid" on a stopped press left its unit SOLD with no task.

These tests run the real routes over a REAL MongoDB (pymongo, no doubles for
the database) and assert the stored ROWS -- returns, stock units, the credit
ledger, the customer balance, tasks -- never call counts. Every test database
is created WITHOUT any index on returns.return_id: nothing here may depend on
that backstop (D95(1)).

Where: CI's mongo service (MONGODB_URL). On a PC: MONGO_HOST / MONGO_PORT /
MONGO_DATABASE of a loopback Mongo; with no Mongo the tests skip locally and
FAIL in CI. Each test creates its own database "<MONGO_DATABASE>_<8 hex>" and
drops it. CAUTION: with those variables set the app itself connects to the
database named exactly MONGO_DATABASE, and conftest (_isolate_db) empties its
transactional collections before every test -- give a NEW, empty name, never
a database that holds data. No emoji (cp1252).
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from pymongo import MongoClient  # noqa: E402
from pymongo.collection import Collection  # noqa: E402
from pymongo.errors import AutoReconnect, DuplicateKeyError  # noqa: E402

from api.routers import customers as cust_router  # noqa: E402
from api.services import store_credit_ledger as scl  # noqa: E402
from database.repositories.audit_repository import AuditRepository  # noqa: E402
from database.repositories.product_repository import StockRepository  # noqa: E402
from database.repositories.task_repository import TaskRepository  # noqa: E402
from tests.test_pos_money_doors import STORE, _advance_client, _hdr, _order, _user, _wire, returns_router  # noqa: E402

pytestmark = pytest.mark.usefixtures("gst_numbers")

REAL_INSERT, REAL_FIND_ONE = Collection.insert_one, Collection.find_one


def _connect(url):
    return MongoClient(url, serverSelectionTimeoutMS=2000)


def _open_native():
    """(client, a fresh database name). conftest's own no-Mongo skip probes
    MONGODB_URL / localhost:27017 only, so this file decides for itself."""
    host, port = os.getenv("MONGO_HOST"), os.getenv("MONGO_PORT")
    if host and port:
        base = os.getenv("MONGO_DATABASE") or "ims_d95_unit"
        assert host in ("127.0.0.1", "localhost"), host  # a developer's own loopback Mongo only
        url = f"mongodb://{host}:{port}"
    else:
        url, base = os.getenv("MONGODB_URL") or "mongodb://localhost:27017", "ims_test_d95"
    client = _connect(url)
    try:
        client.admin.command("ping")
    except Exception:  # noqa: BLE001
        if os.getenv("CI"):
            raise
        pytest.skip("no MongoDB reachable - CI runs this (on a PC: MONGO_HOST / MONGO_PORT / MONGO_DATABASE)")
    return client, f"{base}_{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def native(monkeypatch):
    """The real doors over one fresh native database: a paid two-unit cash
    bill, its two units SOLD, real stock / task / ledger collections."""
    client, name = _open_native()
    db = client[name]
    assert db.name == name and name not in client.list_database_names(), name  # fresh, this test's own
    env = _wire(monkeypatch, db)
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: StockRepository(db["stock_units"]))
    monkeypatch.setattr(returns_router, "get_product_repository", lambda: None)
    monkeypatch.setattr("api.dependencies.get_task_repository", lambda: TaskRepository(db["tasks"]), raising=False)
    # The real audit trail (hash-chained, append-only): its rows are asserted (D84).
    monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: AuditRepository(db["audit_logs"]),
                        raising=False)
    line = {"item_id": "li1", "product_id": "P1", "product_name": "Frame", "sku": "F1", "quantity": 2,
            "unit_price": 1525.42, "gst_rate": 18.0, "taxable_value": 3050.85, "tax_amount": 549.15}
    db["orders"].insert_one(_order(
        status="DELIVERED", grand_total=3600.0, amount_paid=3600.0, balance_due=0.0, payment_status="PAID",
        items=[line], payments=[{"payment_id": "p1", "method": "CASH", "amount": 3600.0}]))
    db["customers"].insert_one({"customer_id": "CUST-1", "name": "Asha", "store_credit": 0.0})
    for n in (1, 2):
        db["stock_units"].insert_one({"_id": f"U{n}", "stock_id": f"U{n}", "product_id": "P1", "store_id": STORE,
                                      "status": "SOLD", "order_id": "ORD-1", "quantity": 1, "barcode": f"BC{n}"})
    # D95(1): no unique index on return_id anywhere in these tests.
    db["returns"].insert_one({"_id": "seed"})
    db["returns"].delete_one({"_id": "seed"})
    assert [i["name"] for i in db["returns"].list_indexes()] == ["_id_"]
    env["name"] = name
    yield env
    client.drop_database(name)


def _payload(return_type="RETURN", condition="GOOD"):
    body = {"order_id": "ORD-1", "return_type": return_type, "items": [{
        "order_item_id": "li1", "product_id": "P1", "product_name": "Frame", "sku": "F1",
        "return_qty": 1, "unit_price": 1525.42, "reason": "DEFECTIVE", "condition": condition,
        "restock": condition == "GOOD"}]}
    if return_type == "RETURN":
        body.update(refund_method="CASH", refund_tenders=[{"method": "CASH", "amount": 1800.0}])
    return body


def _press(payload, key=None):
    hdr = {**_hdr("STORE_MANAGER"), **({"Idempotency-Key": key} if key else {})}
    return _advance_client().post("/api/v1/returns", json=payload, headers=hdr)


def _complete(rid, **answer):
    return asyncio.run(returns_router.complete_return_record(
        rid, returns_router.CompleteRecordBody(**answer), _user("ACCOUNTANT")))


def _restock(rid):
    return asyncio.run(returns_router.retry_restock(rid, current_user=_user("STORE_MANAGER")))


def _age_hold(db):
    """Age ONLY the press's own hold past the Needs-completing timeout."""
    old = (datetime.now(timezone.utc) - timedelta(seconds=cust_router.PENDING_TIMEOUT_SECONDS + 5)).isoformat()
    holds = db["orders"].find_one({"order_id": "ORD-1"})["refund_holds"]
    rid = [k for k in holds if k.startswith("RET-")][0]
    db["orders"].update_one({"order_id": "ORD-1"}, {"$set": {f"refund_holds.{rid}.at": old}})
    return rid


def _in_thread(fn):
    out = {}

    def run():
        try:
            out["value"] = fn()
        except Exception as exc:  # noqa: BLE001
            out["error"] = exc
    t = threading.Thread(target=run)
    t.start()
    t.join(120)
    return out


def _books(db):
    """The stored rows a reviewer reconciles."""
    recs = list(db["returns"].find({"payout_of": None}))
    return {
        "units": sorted(u["status"] for u in db["stock_units"].find({"_id": {"$in": ["U1", "U2"]}})),
        "unit_rows": db["stock_units"].count_documents({}),  # a mint would add a row
        "records": [(r["_id"], r["status"], bool(r.get("recovered"))) for r in recs],
        "cash_netted": round(sum(float(t["amount"]) for r in db["returns"].find({"drawer_auto_netted": True})
                                 for t in (r.get("refund_tenders") or []) if t.get("method") == "CASH"), 2),
        "returned_qty": db["orders"].find_one({"order_id": "ORD-1"})["items"][0].get("returned_qty") or 0,
        "restock_tasks": sorted(t["status"] for t in db["tasks"].find(
            {"source_ref": {"$regex": "^return_restock_blocked:"}})),
        "owed_tasks": db["tasks"].count_documents({"source_ref": {"$regex": "^return_cash_owed:"}}),
        "ledger": sorted((r["entry_id"], r.get("type")) for r in db["credit_note_ledger"].find({})),
        "balance": db["customers"].find_one({"customer_id": "CUST-1"}).get("store_credit"),
        # D84: the audit rows of the return itself, by who wrote its record.
        "audits": sorted(a["action"] for a in db["audit_logs"].find(
            {"action": {"$in": ["RETURN_CREATED", "RETURN_RECORD_COMPLETED"]}})),
    }


def _insert_fails_once(monkeypatch, state):
    """The press's record insert raises and nothing lands (it may still land)."""
    def insert(self, doc, *a, **k):
        if _press_doc(self, doc) and "failed" not in state:
            state["failed"] = doc["return_id"]
            raise AutoReconnect("insert failed, nothing landed")
        return REAL_INSERT(self, doc, *a, **k)

    monkeypatch.setattr(Collection, "insert_one", insert)


def _two_at_once(monkeypatch, payload, key):
    """Two identical re-presses that reach the record insert together."""
    gate, real_own, answers = threading.Barrier(2), returns_router._own_record_or_refuse, []

    async def together(*a, **k):
        try:
            gate.wait(20)
        except threading.BrokenBarrierError:
            pass
        return await real_own(*a, **k)

    monkeypatch.setattr(returns_router, "_own_record_or_refuse", together)
    threads = [threading.Thread(target=lambda: answers.append(_press(payload, key))) for _ in range(2)]
    [t.start() for t in threads]
    [t.join(120) for t in threads]
    return answers


def _press_doc(coll, doc):
    return coll.name == "returns" and isinstance(doc, dict) and doc.get("press_token") and not doc.get("recovered")


# --- control -----------------------------------------------------------------------------------


def test_a_plain_press_writes_one_record_and_puts_one_unit_back(native):
    db = native["db"]
    r = _press(_payload())
    assert r.status_code == 201 and r.json()["restock_applied"] is True, r.text
    rid = r.json()["return_id"]
    b = _books(db)
    assert b["records"] == [(f"ret:{rid}", "COMPLETED", False)] and b["units"] == ["AVAILABLE", "SOLD"]
    assert b["unit_rows"] == 2 and b["cash_netted"] == 1800.0 and b["returned_qty"] == 1 and b["restock_tasks"] == []
    assert b["audits"] == ["RETURN_CREATED"]
    assert db["audit_logs"].find_one({"action": "RETURN_CREATED"})["_id"] == f"return_created:{rid}"
    rec = db["returns"].find_one({"_id": f"ret:{rid}"})
    assert rec["stock_pending"] is False and rec["restock_in_progress"] is False and rec["loyalty_reversal_failed"] is False
    assert _restock(rid)["message"] == "Already restocked" and _books(db)["units"] == ["AVAILABLE", "SOLD"]


def test_one_id_decides_between_two_writers_at_once(native):
    """D95(1): two writers of one return's record at the same moment, with no
    unique index but _id: exactly one insert lands; the other reads it."""
    coll = native["db"]["returns"]
    gate, results = threading.Barrier(2), []

    def write(who):
        gate.wait(30)
        results.append((who, returns_router._insert_record(coll, {"return_id": "RET-RACE", "status": "COMPLETED",
                                                                 "writer": who})))

    threads = [threading.Thread(target=write, args=(w,)) for w in ("press", "needs-completing")]
    [t.start() for t in threads]
    [t.join(60) for t in threads]
    won = [who for who, stored in results if stored is None]
    lost = [stored for _who, stored in results if stored is not None]
    assert len(won) == 1 and len(lost) == 1 and lost[0]["writer"] == won[0]
    assert coll.count_documents({"return_id": "RET-RACE"}) == 1


# --- D95(6): the exact D92(1) sequence -----------------------------------------------------------


def test_the_exact_d92_1_sequence_puts_one_unit_back(native, monkeypatch):
    """A cash press stalls after its money-out mark; Accounts answer "cash
    paid" (the record); a later counter return drops the press's hold; the
    press wakes. Before D92: "no hold" read as "may restock" and two units
    went back for one frame. D95: its insert meets the record -- 409, nothing
    placed; the record's Restock puts ONE unit back."""
    db = native["db"]
    real_keep, out = returns_router._keep_order_refund_hold, {}

    def stalls_after_money_out(order_id, rid, *a, extra=None, **k):
        res = real_keep(order_id, rid, *a, extra=extra, **k)
        if "money_out" in (extra or {}) and "record" not in out:
            out["rid"] = _age_hold(db)
            out["record"] = _in_thread(lambda: _complete(out["rid"], cash_leg_paid=True)).get("value")
            # What the next counter return's reservation does once the record counts.
            holds = dict(db["orders"].find_one({"order_id": "ORD-1"})["refund_holds"])
            holds.pop(out["rid"])
            db["orders"].update_one({"order_id": "ORD-1"}, {"$set": {"refund_holds": holds}})
        return res

    monkeypatch.setattr(returns_router, "_keep_order_refund_hold", stalls_after_money_out)
    r = _press(_payload())
    rid = out["rid"]
    assert out["record"]["status"] == "COMPLETED" and out["record"]["stock_pending"] is True
    assert r.status_code == 409 and r.json()["detail"] == returns_router.RECORD_TAKEN_MESSAGE, r.text
    b = _books(db)
    assert b["records"] == [(f"ret:{rid}", "COMPLETED", True)] and b["units"] == ["SOLD", "SOLD"]
    assert b["restock_tasks"] == ["OPEN"] and b["cash_netted"] == 1800.0
    first, second = _restock(rid), _restock(rid)
    assert first["restock_applied"] is True and second["message"] == "Already restocked"
    b = _books(db)
    assert b["units"] == ["AVAILABLE", "SOLD"] and b["unit_rows"] == 2 and b["restock_tasks"] == ["COMPLETED"]
    assert b["cash_netted"] == 1800.0 and b["returned_qty"] == 1


# --- D95(6): a lost acknowledgement on the press insert ------------------------------------------


def _lands_but_unacknowledged(monkeypatch, state, read_back_fails):
    def insert(self, doc, *a, **k):
        if _press_doc(self, doc) and "landed" not in state:
            REAL_INSERT(self, doc, *a, **k)
            state["landed"] = doc["return_id"]
            raise AutoReconnect("insert landed, acknowledgement lost")
        return REAL_INSERT(self, doc, *a, **k)

    def find_one(self, flt=None, *a, **k):
        if (read_back_fails and self.name == "returns" and state.get("landed") and not state.get("healed")
                and isinstance(flt, dict) and flt.get("return_id") == state["landed"]):
            raise AutoReconnect("returns unreadable")
        return REAL_FIND_ONE(self, flt, *a, **k)

    monkeypatch.setattr(Collection, "insert_one", insert)
    monkeypatch.setattr(Collection, "find_one", find_one)


def test_a_lost_acknowledgement_that_reads_back_is_the_press_own_record(native, monkeypatch):
    """D95(3): the insert landed, only its answer was lost; the read-back
    finds the press's own token: 201, one record, one unit back."""
    db, state = native["db"], {}
    _lands_but_unacknowledged(monkeypatch, state, read_back_fails=False)
    r = _press(_payload())
    assert r.status_code == 201 and r.json()["return_id"] == state["landed"], r.text
    b = _books(db)
    assert b["records"] == [(f"ret:{state['landed']}", "COMPLETED", False)]
    assert b["units"] == ["AVAILABLE", "SOLD"] and b["unit_rows"] == 2 and b["cash_netted"] == 1800.0


def test_a_lost_acknowledgement_that_cannot_be_read_is_settled_by_the_identical_re_press(native, monkeypatch):
    """D95(3): landed, answer lost, read-back failed: 503 'Not confirmed. Do
    not pay yet'. The identical re-press finds the record by its key: 201
    (pay), never a second record; its unit goes back once."""
    db, state = native["db"], {}
    _lands_but_unacknowledged(monkeypatch, state, read_back_fails=True)
    r = _press(_payload(), "K-LOST")
    assert r.status_code == 503 and r.json()["detail"] == returns_router.RECORD_NOT_CONFIRMED_MESSAGE, r.text
    state["healed"] = True
    rid = state["landed"]
    b = _books(db)
    assert b["records"] == [(f"ret:{rid}", "COMPLETED", False)] and b["units"] == ["SOLD", "SOLD"]
    changed = {**_payload(), "refund_tenders": [{"method": "UPI", "amount": 1800.0}], "refund_method": "UPI"}
    assert _press(changed, "K-LOST").status_code == 409  # other content under the same key
    assert b["audits"] == []  # the 503 said press again: the row is the re-press's (D84)
    again = _press(_payload(), "K-LOST")
    assert again.status_code == 201 and again.json()["return_id"] == rid, again.text
    assert "Update stock" in again.json()["stock_note"]
    # The re-press IS the "pay" answer: it says what the record says, so the
    # till shows "recorded in Day-End", never "enter it as cash paid out" for
    # cash Day-End already nets (read as not netted, it was deducted twice).
    rec, answer = db["returns"].find_one({"_id": f"ret:{rid}"}), again.json()
    assert rec["drawer_auto_netted"] is True and answer["drawer_auto_netted"] is True
    assert answer["refund_tenders"] == rec["refund_tenders"]
    assert [(t["method"], t["amount"]) for t in answer["refund_tenders"]] == [("CASH", 1800.0)]
    for field in ("credit_note_number", "credit_entry", "collect_method", "round_off", "refund_amount",
                  "net_refund", "points_paid_share"):
        assert field in answer and answer[field] == rec.get(field), field
    assert answer["points_waived"] is False and answer["credit_note_number"]
    assert _restock(rid)["restock_applied"] is True and _restock(rid)["message"] == "Already restocked"
    b = _books(db)
    assert b["records"] == [(f"ret:{rid}", "COMPLETED", False)] and b["units"] == ["AVAILABLE", "SOLD"]
    assert b["unit_rows"] == 2 and b["cash_netted"] == 1800.0 and b["returned_qty"] == 1


def test_the_re_press_of_a_landed_record_writes_its_audit_row_once(native, monkeypatch):
    """D84: the 503 press stopped before its RETURN_CREATED row. The identical
    re-press (the replay) writes it, under the return's fixed id, once --
    before, a completed and paid cash return had no such row at all."""
    db, state = native["db"], {}
    _lands_but_unacknowledged(monkeypatch, state, read_back_fails=True)
    assert _press(_payload(), "K-AUD").status_code == 503
    state["healed"] = True
    assert _books(db)["audits"] == []
    for _ in range(2):
        assert _press(_payload(), "K-AUD").status_code == 201
    rows = list(db["audit_logs"].find({"action": "RETURN_CREATED"}))
    assert [r["_id"] for r in rows] == [f"return_created:{state['landed']}"], rows
    rec = db["returns"].find_one({"return_id": state["landed"]})
    assert rows[0]["entity_id"] == state["landed"] and rows[0]["user_id"] == rec["created_by"]
    assert rows[0]["details"]["net_refund"] == 1800.0 and rows[0]["seq"] >= 1  # chained like every audit row


def test_an_insert_that_never_landed_is_settled_by_the_identical_re_press(native, monkeypatch):
    """D95(3): the insert raised and nothing reads back (it may still land):
    503. The identical re-press makes the SAME insert again from the doc kept
    on the hold -- two of them at once still leave ONE record and ONE unit
    back, and nothing is claimed or priced a second time."""
    db, state = native["db"], {}
    _insert_fails_once(monkeypatch, state)
    r = _press(_payload(), "K-FAIL")
    assert r.status_code == 503 and r.json()["detail"] == returns_router.RECORD_NOT_CONFIRMED_MESSAGE, r.text
    rid = state["failed"]
    b = _books(db)
    assert b["records"] == [] and b["units"] == ["SOLD", "SOLD"] and b["returned_qty"] == 1 and b["audits"] == []
    hold = db["orders"].find_one({"order_id": "ORD-1"})["refund_holds"][rid]
    assert hold["kept"] is True and hold["doc"]["return_id"] == rid
    answers = _two_at_once(monkeypatch, _payload(), "K-FAIL")
    assert sorted(a.status_code for a in answers) == [201, 201], [a.text for a in answers]
    assert {a.json()["return_id"] for a in answers} == {rid}
    assert all(a.json()["drawer_auto_netted"] is True for a in answers)
    b = _books(db)
    assert b["records"] == [(f"ret:{rid}", "COMPLETED", False)] and b["units"] == ["AVAILABLE", "SOLD"]
    assert b["unit_rows"] == 2 and b["cash_netted"] == 1800.0 and b["returned_qty"] == 1
    assert b["audits"] == ["RETURN_CREATED"]  # one return, one row -- both re-presses finished it
    assert _restock(rid)["message"] == "Already restocked"


def test_two_finishers_at_once_set_one_damaged_unit_aside(native, monkeypatch):
    """D92(2) + D95(2): the bill line is 2 units, ONE comes back DAMAGED. Two
    identical re-presses finish the return together and each reads "which
    units has this return set aside" before the other's flip lands. Counting
    and then flipping set BOTH units aside -- the piece still with the
    customer became QUARANTINED stock. The units are named ONCE on the
    return's record (one compare-and-set) and only those are flipped."""
    db, state = native["db"], {}
    _insert_fails_once(monkeypatch, state)
    r = _press(_payload(condition="DAMAGED"), "K-DMG")
    assert r.status_code == 503, r.text
    rid = state["failed"]
    assert _books(db)["units"] == ["SOLD", "SOLD"]
    gate, real_find = threading.Barrier(2), returns_router._find_stock

    def find(stock_repo, query):
        rows = real_find(stock_repo, query)
        if "quarantine_notes" in query:  # both have read; neither has flipped
            try:
                gate.wait(5)
            except threading.BrokenBarrierError:
                pass
        return rows

    monkeypatch.setattr(returns_router, "_find_stock", find)
    answers = _two_at_once(monkeypatch, _payload(condition="DAMAGED"), "K-DMG")
    assert sorted(a.status_code for a in answers) == [201, 201], [a.text for a in answers]
    b = _books(db)
    assert b["units"] == ["QUARANTINED", "SOLD"] and b["unit_rows"] == 2 and b["returned_qty"] == 1
    rec = db["returns"].find_one({"_id": f"ret:{rid}"})
    assert len(rec["quarantined_stock_ids"]) == 1 and rec["damaged_unit_ids"] == rec["quarantined_stock_ids"]
    kept = db["stock_units"].find_one({"status": "SOLD"})
    assert kept["order_id"] == "ORD-1"  # the customer's other piece keeps its sale
    _restock(rid)
    assert _books(db)["units"] == ["QUARANTINED", "SOLD"]


# --- D95(6): press vs "Cash paid", both orders ---------------------------------------------------


@pytest.mark.parametrize("cash_leg_paid", [True, False])
def test_cash_paid_first_the_press_owns_nothing(native, monkeypatch, cash_leg_paid):
    """The press is paused at its record insert; Accounts answer Needs
    completing. With no unique index on return_id the two inserts still meet
    on one _id: the press answers 409 and places nothing."""
    db, out = native["db"], {}

    def insert(self, doc, *a, **k):
        if _press_doc(self, doc) and "record" not in out:
            out["rid"] = _age_hold(db)
            out["record"] = _in_thread(lambda: _complete(out["rid"], cash_leg_paid=cash_leg_paid)).get("value")
            try:
                return REAL_INSERT(self, doc, *a, **k)
            except DuplicateKeyError:
                out["collided"] = True
                raise
        return REAL_INSERT(self, doc, *a, **k)

    monkeypatch.setattr(Collection, "insert_one", insert)
    r = _press(_payload(), "K-CP")
    rid = out["rid"]
    assert out.get("collided") and out["record"]["status"] == "COMPLETED"
    assert r.status_code == 409 and r.json()["detail"] == returns_router.RECORD_TAKEN_MESSAGE, r.text
    b = _books(db)
    assert b["records"] == [(f"ret:{rid}", "COMPLETED", True)] and b["units"] == ["SOLD", "SOLD"]
    assert b["restock_tasks"] == ["OPEN"] and b["returned_qty"] == 1
    assert b["audits"] == ["RETURN_RECORD_COMPLETED"]  # the press that paid nothing created nothing
    rec = db["returns"].find_one({"_id": f"ret:{rid}"})
    if cash_leg_paid:
        assert b["cash_netted"] == 1800.0 and b["owed_tasks"] == 0 and not rec.get("owed_amount")
    else:
        assert b["cash_netted"] == 0 and b["owed_tasks"] == 1 and rec["owed_amount"] == 1800.0
    # The identical re-press pays nothing either.
    again = _press(_payload(), "K-CP")
    assert again.status_code == 409 and again.json()["detail"] == returns_router.RECORD_TAKEN_MESSAGE
    assert _restock(rid)["restock_applied"] is True and _restock(rid)["message"] == "Already restocked"
    b = _books(db)
    assert b["units"] == ["AVAILABLE", "SOLD"] and b["unit_rows"] == 2 and b["restock_tasks"] == ["COMPLETED"]
    assert len(b["records"]) == 1 and b["audits"] == ["RETURN_RECORD_COMPLETED"]


def test_the_press_first_cash_paid_writes_nothing(native, monkeypatch):
    """The press's record landed; while it is still placing its units
    Accounts answer Needs completing: 'The counter completed this return;
    nothing to do here.' Nothing is written; the press answers 201."""
    db, out = native["db"], {}
    real_restock = returns_router._restock_good_items

    def placing(items, store_id, return_id, **k):
        if "answer" not in out:
            out["before"] = db["returns"].find_one({"return_id": return_id})
            _age_hold(db)
            out["answer"] = _in_thread(lambda: _complete(return_id, cash_leg_paid=False)).get("value")
            out["restock"] = _in_thread(lambda: _restock(return_id)).get("value")  # pressed meanwhile
            out["after"] = db["returns"].find_one({"return_id": return_id})
        return real_restock(items, store_id, return_id, **k)

    monkeypatch.setattr(returns_router, "_restock_good_items", placing)
    r = _press(_payload())
    rid = r.json()["return_id"]
    assert r.status_code == 201 and r.json()["restock_applied"] is True, r.text
    assert out["answer"]["message"] == returns_router.COUNTER_COMPLETED_MESSAGE and out["answer"]["replayed"] is True
    assert out["after"] == out["before"]  # not one field written by Needs completing or the Restock
    assert out["restock"]["restock_applied"] is False and "in progress" in out["restock"]["message"]
    b = _books(db)
    assert b["records"] == [(f"ret:{rid}", "COMPLETED", False)] and b["units"] == ["AVAILABLE", "SOLD"]
    assert b["unit_rows"] == 2 and b["cash_netted"] == 1800.0 and b["owed_tasks"] == 0 and b["restock_tasks"] == []


# --- D95(6): the abandoned 503 (D95(5)) ----------------------------------------------------------


def test_an_abandoned_unconfirmed_press_is_netted_once_and_owes_nothing(native, monkeypatch):
    """Landed, answer and read-back lost (503), never pressed again. The
    record stays as the counter wrote it: Needs completing never lists it and
    writes nothing on it; no OWED leg, no payout row -- its cash is netted
    exactly once. Its unit is not stranded: the sweep raises one task and the
    record's Restock puts it back."""
    db, state = native["db"], {}
    _lands_but_unacknowledged(monkeypatch, state, read_back_fails=True)
    r = _press(_payload(), "K-AB")
    assert r.status_code == 503, r.text
    state["healed"] = True
    rid = state["landed"]
    before = db["returns"].find_one({"return_id": rid})
    _age_hold(db)
    listed = asyncio.run(returns_router.needs_completing(current_user=_user("ACCOUNTANT")))
    assert rid not in [i["return_id"] for i in listed["items"]] and listed["owed"] == []
    for paid in (False, True):
        assert _complete(rid, cash_leg_paid=paid)["message"] == returns_router.COUNTER_COMPLETED_MESSAGE
    assert db["returns"].find_one({"return_id": rid}) == before
    with pytest.raises(returns_router.HTTPException) as owed:
        asyncio.run(returns_router.mark_owed_paid(rid, returns_router.OwedPaidBody(
            method="CASH", cash_paid_from="STORE_DRAWER"), current_user=_user("ACCOUNTANT")))
    assert owed.value.status_code == 409
    b = _books(db)
    assert b["records"] == [(f"ret:{rid}", "COMPLETED", False)] and b["cash_netted"] == 1800.0
    assert db["returns"].count_documents({}) == 1 and b["owed_tasks"] == 0  # no payout row either
    assert b["units"] == ["SOLD", "SOLD"] and b["restock_tasks"] == []
    assert returns_router.sweep_stuck_return_holds() == 0  # younger than any press
    db["returns"].update_one({"return_id": rid}, {"$set": {"created_at": "2026-01-01T00:00:00+00:00"}})
    assert returns_router.sweep_stuck_return_holds() == 1 and returns_router.sweep_stuck_return_holds() == 0
    assert _books(db)["restock_tasks"] == ["OPEN"]
    assert _restock(rid)["restock_applied"] is True and _restock(rid)["message"] == "Already restocked"
    b = _books(db)
    assert b["units"] == ["AVAILABLE", "SOLD"] and b["unit_rows"] == 2 and b["restock_tasks"] == ["COMPLETED"]
    assert b["cash_netted"] == 1800.0


# --- the store-credit ledger row is written once, whoever owns the record ------------------------


@pytest.mark.parametrize("needs_completing_first", [True, False])
def test_a_credit_note_has_one_ledger_row_and_one_balance_move(native, monkeypatch, needs_completing_first):
    db, out = native["db"], {}

    def insert(self, doc, *a, **k):
        if _press_doc(self, doc) and needs_completing_first and "record" not in out:
            out["rid"] = _age_hold(db)
            out["record"] = _in_thread(lambda: _complete(out["rid"], cash_leg_paid=True))
        return REAL_INSERT(self, doc, *a, **k)

    monkeypatch.setattr(Collection, "insert_one", insert)
    r = _press(_payload("CREDIT_NOTE"))
    if needs_completing_first:
        rid = out["rid"]
        assert out["record"]["value"]["status"] == "COMPLETED", out
        assert r.status_code == 409 and r.json()["detail"] == returns_router.RECORD_TAKEN_MESSAGE, r.text
    else:
        assert r.status_code == 201, r.text
        rid = r.json()["return_id"]
        _age_hold(db)
        assert _complete(rid, cash_leg_paid=True)["message"] == returns_router.COUNTER_COMPLETED_MESSAGE
    b = _books(db)
    assert b["ledger"] == [(f"{rid}:credit", scl.ISSUED)] and b["balance"] == 1800.0
    assert b["records"] == [(f"ret:{rid}", "COMPLETED", needs_completing_first)]
    rec = db["returns"].find_one({"_id": f"ret:{rid}"})
    assert rec["credit_amount"] == 1800.0 and rec["credit_entry"]["entry_id"] == f"{rid}:credit"
    if needs_completing_first:
        assert _restock(rid)["restock_applied"] is True
    assert _restock(rid)["message"] == "Already restocked"
    b = _books(db)
    assert b["units"] == ["AVAILABLE", "SOLD"] and b["unit_rows"] == 2 and b["balance"] == 1800.0
    assert b["ledger"] == [(f"{rid}:credit", scl.ISSUED)]


# --- D92(2), kept: a DAMAGED unit is always set aside --------------------------------------------


def test_a_damaged_unit_is_set_aside_whoever_owns_the_record(native, monkeypatch):
    """Cash paid wrote the record while the press was paused: the press
    answers 409 and still sets its DAMAGED unit aside, noted on that record;
    the record's stock step run afterwards moves nothing more."""
    db, out = native["db"], {}

    def insert(self, doc, *a, **k):
        if _press_doc(self, doc) and "record" not in out:
            out["rid"] = _age_hold(db)
            out["record"] = _in_thread(lambda: _complete(out["rid"], cash_leg_paid=True)).get("value")
        return REAL_INSERT(self, doc, *a, **k)

    monkeypatch.setattr(Collection, "insert_one", insert)
    r = _press(_payload(condition="DAMAGED"))
    rid = out["rid"]
    assert r.status_code == 409, r.text
    assert _books(db)["units"] == ["QUARANTINED", "SOLD"]
    set_aside = db["returns"].find_one({"_id": f"ret:{rid}"})["quarantined_stock_ids"]
    assert len(set_aside) == 1 and db["stock_units"].find_one({"_id": set_aside[0]})["status"] == "QUARANTINED"
    _restock(rid)
    b = _books(db)
    assert b["units"] == ["QUARANTINED", "SOLD"] and b["unit_rows"] == 2
    assert db["returns"].find_one({"_id": f"ret:{rid}"})["quarantined_stock_ids"] == set_aside


def test_a_press_that_stops_after_its_record_leaves_the_damaged_unit_to_the_stock_step(native, monkeypatch):
    """D95(2): the press stopped between its record and its stock step. The
    record says the step is pending; 'Update stock' sets the DAMAGED unit
    aside, once."""
    db = native["db"]

    async def dies(*a, **k):
        raise RuntimeError("the press stopped after its record")

    monkeypatch.setattr(returns_router, "_finish_press", dies)
    with pytest.raises(RuntimeError):
        _press(_payload(condition="DAMAGED"))
    monkeypatch.undo()
    rec = db["returns"].find_one({})
    assert rec["status"] == "COMPLETED" and rec["stock_pending"] is True
    assert sorted(u["status"] for u in db["stock_units"].find({})) == ["SOLD", "SOLD"]
    raw = StockRepository(db["stock_units"])
    asyncio.run(_update_stock(db, raw, rec["return_id"]))
    assert sorted(u["status"] for u in db["stock_units"].find({})) == ["QUARANTINED", "SOLD"]
    again = db["returns"].find_one({})
    assert again["stock_pending"] is False and len(again["quarantined_stock_ids"]) == 1
    asyncio.run(_update_stock(db, raw, rec["return_id"]))
    assert sorted(u["status"] for u in db["stock_units"].find({})) == ["QUARANTINED", "SOLD"]
    assert db["stock_units"].count_documents({}) == 2


async def _update_stock(db, stock_repo, rid):
    """The record's Restock with the doors wired by hand (the fixture's
    patches were undone above to let the real _finish_press back in)."""
    from unittest.mock import patch

    from database.repositories.order_repository import OrderRepository

    class _Conn:
        is_connected = True

        def get_collection(self, name):
            return db.get_collection(name)

    conn = _Conn()
    conn.db = db
    with patch.object(returns_router, "get_stock_repository", lambda: stock_repo), \
            patch.object(returns_router, "get_order_repository", lambda: OrderRepository(db["orders"])), \
            patch("api.dependencies.get_db", lambda: conn), \
            patch("api.dependencies.get_task_repository", lambda: TaskRepository(db["tasks"])):
        return await returns_router.retry_restock(rid, current_user=_user("STORE_MANAGER"))


def test_a_named_unit_another_return_took_is_replaced_by_one_other(native, monkeypatch):
    """The record names U1 as its DAMAGED unit; another return of the bill put
    U1 back on the shelf meanwhile. The set-aside names U2 in its place (a
    compare-and-set on what was named) and flips that one; a second run moves
    nothing; a record that cannot be read sets nothing aside."""
    db = native["db"]
    db["returns"].insert_one({"_id": "ret:RET-X", "return_id": "RET-X", "order_id": "ORD-1", "status": "COMPLETED",
                              "damaged_unit_ids": ["U1"]})
    db["stock_units"].update_one({"_id": "U1"}, {"$set": {"status": "AVAILABLE", "order_id": None}})
    lines = [returns_router.ReturnLine(**_payload(condition="DAMAGED")["items"][0])]
    order = db["orders"].find_one({"order_id": "ORD-1"})

    def unreadable(self, flt=None, *a, **k):
        if self.name == "returns":
            raise AutoReconnect("returns unreadable")
        return REAL_FIND_ONE(self, flt, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(Collection, "find_one", unreadable)
        assert returns_router._quarantine_damaged_units(lines, "ORD-1", order, "RET-X", "U1") == []
    assert _books(db)["units"] == ["AVAILABLE", "SOLD"]
    for moved in (["U2"], []):
        out = returns_router._quarantine_damaged_units(lines, "ORD-1", order, "RET-X", "U1")
        assert [u["stock_id"] for u in out] == moved
    assert db["returns"].find_one({"_id": "ret:RET-X"})["damaged_unit_ids"] == ["U2"]
    units = {u["_id"]: u["status"] for u in db["stock_units"].find({})}
    assert units == {"U1": "AVAILABLE", "U2": "QUARANTINED"}
