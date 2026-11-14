"""Decision D45(a), PR #1413: two Retry stock steps of ONE return, interleaved
on a REAL Mongo (native PyMongo; an in-memory double has no unique index and
no lost writes, so it hid this).

Retry A takes the claim, reads what is still out, then stalls (a hung socket:
the client sets no socket timeout). Its claim goes stale (120 s), retry B takes
it over and runs to the end. Then A wakes and writes. One returned piece must
never become two stock records:

  * mint: the bill has no SOLD unit of its own for the piece (decision D5), so
    each retry mints -- two new records for one piece;
  * flip: B puts the bill's unit U1 back; A's flip of U1 finds it no longer
    SOLD and moves on to U2 -- two units back for one piece.

Runs against MONGODB_URL in CI; locally against IMS_NATIVE_MONGO_URL (a
throwaway ims_claude_* database, dropped after the test)."""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.routers import returns as R  # noqa: E402

URL = os.getenv("IMS_NATIVE_MONGO_URL") or os.getenv("MONGODB_URL") or "mongodb://localhost:27017"
STORE, RID, OID, PID = "BV-RACE-01", "RET-RACE-1", "ORD-RACE-1", "P-RACE-1"
USER = {"user_id": "u-race", "roles": ["SUPERADMIN"], "store_ids": [STORE], "active_store_id": STORE}


@pytest.fixture
def native(monkeypatch):
    from pymongo import MongoClient

    from database.connection import DatabaseConnection

    client = MongoClient(URL, serverSelectionTimeoutMS=3000)
    name = f"ims_claude_r7_1413_{uuid.uuid4().hex[:8]}"
    db = client[name]
    assert db.name == name and name.startswith("ims_claude_")
    conn = object.__new__(DatabaseConnection)
    conn._client, conn._db, conn._connected = client, db, True
    conn.ensure_indexes()  # the app's own index set, its backstops included
    monkeypatch.setattr("api.dependencies.get_db", lambda: conn)
    # No provider is ever contacted: the Shopify write-back is a no-op here.
    monkeypatch.setattr("api.services.online_stock_writeback.writeback_after_restock",
                        lambda *a, **k: None)
    yield db
    assert db.name == name
    client.drop_database(name)


def _seed(db, units, qty=1):
    db.stores.insert_one({"store_id": STORE, "store_type": "RETAIL"})
    db.orders.insert_one({
        "_id": OID, "order_id": OID, "store_id": STORE, "order_number": "R-1",
        "items": [{"item_id": "li1", "product_id": PID, "sku": "F1", "product_name": "Frame",
                   "quantity": qty, "unit_price": 1000.0}]})
    for sid in units:
        db.stock_units.insert_one({"_id": sid, "stock_id": sid, "product_id": PID, "store_id": STORE,
                                   "status": "SOLD", "order_id": OID, "quantity": 1})
    # An ordinary return whose stock step failed at the press: still open.
    db.returns.insert_one({
        "return_id": RID, "store_id": STORE, "order_id": OID, "status": "COMPLETED",
        "items": [{"order_item_id": "li1", "product_id": PID, "sku": "F1", "product_name": "Frame",
                   "return_qty": 1, "unit_price": 1000.0, "condition": "GOOD", "restock": True}],
        "restock_applied": False, "restock_in_progress": False, "restocked": []})


def _interleave(db, monkeypatch, target, attr):
    """Stall retry A at its first call of `target.attr` (the write); meanwhile
    its claim goes stale and retry B takes it over and finishes."""
    real = getattr(target, attr)
    seen = {"b": None}

    def stalled(*a, **k):
        if seen["b"] is None:
            seen["b"] = "running"
            db.returns.update_one({"return_id": RID}, {"$set": {
                "restock_started_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()}})
            seen["b"] = R._retry_restock(RID, USER, STORE)
        return real(*a, **k)

    monkeypatch.setattr(target, attr, stalled)
    a = R._retry_restock(RID, USER, STORE)
    return a, seen["b"]


def _back(db):
    return sorted(u["stock_id"] for u in db.stock_units.find({"product_id": PID, "status": "AVAILABLE"}))


def test_two_interleaved_retries_mint_one_record_for_one_piece(native, monkeypatch):
    _seed(native, units=[])  # no SOLD unit of its own: the piece is minted (D5)
    a, b = _interleave(native, monkeypatch, R.barcode_svc, "mint_unit_barcode")
    assert b["restock_applied"] is True
    back = _back(native)
    assert len(back) == 1, f"one returned piece became {len(back)} stock records: {back}"
    doc = native.returns.find_one({"return_id": RID})
    assert doc["restock_applied"] is True and doc["restock_in_progress"] is False
    assert doc["restock_stock_ids"] == back


def test_two_interleaved_retries_put_back_one_unit_for_one_piece(native, monkeypatch):
    _seed(native, units=["U1", "U2"], qty=2)  # a 2-piece line, ONE piece returned
    a, b = _interleave(native, monkeypatch, R, "_flip_sold_unit")
    assert b["restock_applied"] is True
    back = _back(native)
    assert len(back) == 1, f"one returned piece put back {len(back)} units: {back}"
    assert native.stock_units.count_documents({"product_id": PID, "status": "SOLD"}) == 1
    assert native.stock_units.count_documents({}) == 2  # nothing minted
    doc = native.returns.find_one({"return_id": RID})
    assert doc["restock_applied"] is True and doc["restock_stock_ids"] == back


def _seed_damaged(db):
    _seed(db, units=["U1", "U2"], qty=2)
    db.returns.update_one({"return_id": RID}, {"$set": {"items.0.condition": "DAMAGED"}})


def test_two_interleaved_retries_set_aside_one_unit_for_one_piece(native, monkeypatch):
    _seed_damaged(native)
    a, b = _interleave(native, monkeypatch, R, "_flip_sold_unit")
    aside = sorted(u["stock_id"] for u in native.stock_units.find({"status": "QUARANTINED"}))
    assert len(aside) == 1, f"one damaged piece set aside {len(aside)} units: {aside}"
    assert native.stock_units.count_documents({"status": "SOLD"}) == 1
    doc = native.returns.find_one({"return_id": RID})
    assert doc["restock_applied"] is True and doc["quarantined_stock_ids"] == aside


def test_the_stale_retry_never_overwrites_the_record_its_taker_finished(native, monkeypatch):
    """A wakes after B finished and its own write fails: its "partial, retry
    again" never replaces B's finished record (the claim token)."""
    _seed(native, units=["U1", "U2"], qty=2)
    real = R._flip_sold_unit
    seen = {}

    def stalled(*a, **k):
        if not seen:
            seen["b"] = "running"
            native.returns.update_one({"return_id": RID}, {"$set": {
                "restock_started_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()}})
            seen["b"] = R._retry_restock(RID, USER, STORE)
        if isinstance(seen["b"], dict):
            raise RuntimeError("socket reset")  # every write of A, once it wakes
        return real(*a, **k)

    monkeypatch.setattr(R, "_flip_sold_unit", stalled)
    a = R._retry_restock(RID, USER, STORE)
    assert seen["b"]["restock_applied"] is True and a["restock_applied"] is False
    doc = native.returns.find_one({"return_id": RID})
    assert doc["restock_applied"] is True and doc["restock_in_progress"] is False
    assert doc["restock_stock_ids"] == seen["b"]["restock_stock_ids"] == _back(native)


def test_a_retry_after_a_partial_mint_adds_only_the_piece_still_out(native, monkeypatch):
    """Sequential, no race: a 2-piece mint whose second record failed; the next
    retry mints the second piece under the next slot, never a third record."""
    _seed(native, units=[], qty=2)
    native.returns.update_one({"return_id": RID}, {"$set": {"items.0.return_qty": 2}})
    real, calls = R.barcode_svc.mint_unit_barcode, []

    def second_fails(*a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("barcode counter unreachable")
        return real(*a, **k)

    monkeypatch.setattr(R.barcode_svc, "mint_unit_barcode", second_fails)
    first = R._retry_restock(RID, USER, STORE)
    assert first["restock_applied"] is False and len(_back(native)) == 1
    second = R._retry_restock(RID, USER, STORE)
    assert second["restock_applied"] is True
    assert len(_back(native)) == 2
    assert sorted(u["return_slot"] for u in native.stock_units.find({"source_id": RID})) == [
        f"{RID}|back|{PID}|0", f"{RID}|back|{PID}|1"]
    assert R._retry_restock(RID, USER, STORE)["message"] == "Already restocked"
    assert len(_back(native)) == 2


@pytest.mark.parametrize("condition,status", [("GOOD", "AVAILABLE"), ("DAMAGED", "QUARANTINED")])
def test_a_retry_after_a_partial_flip_of_legacy_units_moves_the_piece_still_out(native, monkeypatch,
                                                                               condition, status):
    """Sequential, no race: legacy units carry only a BSON _id (no stock_id).
    The first attempt moves U1 under slot 0 and U2's write fails; the next
    retry must move U2 under the next slot, never read U1's slot as free and
    count U2 as landed on U1 (U2 left SOLD, the record 'applied')."""
    from bson import ObjectId

    _seed(native, units=[], qty=2)
    native.returns.update_one({"return_id": RID}, {"$set": {"items.0.return_qty": 2,
                                                             "items.0.condition": condition}})
    u1, u2 = ObjectId(), ObjectId()
    for oid in (u1, u2):
        native.stock_units.insert_one({"_id": oid, "product_id": PID, "store_id": STORE,
                                       "status": "SOLD", "order_id": OID, "quantity": 1})
    real, calls = R._flip_sold_unit, []

    def second_fails(*a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("socket reset")
        return real(*a, **k)

    monkeypatch.setattr(R, "_flip_sold_unit", second_fails)
    first = R._retry_restock(RID, USER, STORE)
    assert first["restock_applied"] is False
    assert native.stock_units.find_one({"_id": u2})["status"] == "SOLD"
    second = R._retry_restock(RID, USER, STORE)
    assert second["restock_applied"] is True
    assert native.stock_units.find_one({"_id": u2})["status"] == status, "the piece still out stayed SOLD"
    assert native.stock_units.count_documents({"status": status}) == 2
    assert native.stock_units.count_documents({}) == 2  # nothing minted
    kind = "back" if condition == "GOOD" else "aside"
    assert sorted(u["return_slot"] for u in native.stock_units.find({})) == [
        f"{RID}|{kind}|{PID}|0", f"{RID}|{kind}|{PID}|1"]


def test_a_taker_after_a_stalled_landing_never_adds_a_second_unit_over_a_trace_gap(native, monkeypatch):
    """A record saved before this PR whose `restocked` rows count MORE pieces
    back than the stock shows (U2 went back unstamped). Retry A puts the last
    piece (U3) back, then stalls before it saves; B takes over. B must read
    U3 as A's -- one more piece back -- never mint a fourth record."""
    _seed(native, units=["U3"], qty=3)
    native.stock_units.insert_many([
        {"_id": "U1", "stock_id": "U1", "product_id": PID, "store_id": STORE, "status": "AVAILABLE",
         "returned_by_return_id": RID, "quantity": 1},
        {"_id": "U2", "stock_id": "U2", "product_id": PID, "store_id": STORE, "status": "AVAILABLE",
         "quantity": 1}])
    native.returns.update_one({"return_id": RID}, {"$set": {
        "items.0.return_qty": 3, "restock_stock_ids": ["U1"],
        "restocked": [{"product_id": PID, "sku": "F1", "quantity": 2, "reactivated": 2, "minted": 0,
                       "applied": False}]}})
    a, b = _interleave(native, monkeypatch, R, "_audit_stock_transition")
    assert native.stock_units.count_documents({}) == 3, "a fourth record was minted for one piece"
    assert native.stock_units.count_documents({"status": "AVAILABLE"}) == 3
    assert native.returns.find_one({"return_id": RID})["restock_in_progress"] is False


def test_a_shopify_retry_never_reads_a_stale_booking_that_failed_as_applied(native, monkeypatch):
    """A Shopify refund's retry A books its units, then stalls; B takes over and
    finds A's booking (None: 'another door booked them'). A then wakes and its
    stock writes fail, so A un-books. The record must not stay 'applied' with
    the piece out of stock: the Retry stock step stays."""
    from api.services import shopify_refund as SR

    _seed(native, units=["U1"])
    native.returns.update_one({"return_id": RID}, {"$set": {"shopify_refund_id": "RF-1"}})
    monkeypatch.setattr(SR, "_cap_restock_to_returnable", lambda lines, order, rid: (lines, None, []))
    seen = {}

    def booked(order, lines, rid, restock, processing_store_id=None):
        if seen:
            return None  # B: A's booking fills the refund's mark
        seen["a"] = True
        native.returns.update_one({"return_id": RID}, {"$set": {
            "restock_started_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()}})
        seen["b"] = R._retry_restock(RID, USER, STORE)
        return {"applied": False, "restocked": [], "restock_stock_ids": []}  # A woke; its writes failed

    monkeypatch.setattr(SR, "_restock_booked", booked)
    R._retry_restock(RID, USER, STORE)
    assert native.stock_units.find_one({"_id": "U1"})["status"] == "SOLD"
    doc = native.returns.find_one({"return_id": RID})
    assert doc["restock_applied"] is False, "record reads applied, the piece is not in stock"
    assert doc["restock_in_progress"] is False


def test_the_return_slot_backstop_is_in_the_apps_index_set(native):
    idx = native.stock_units.index_information()["uniq_stock_unit_return_slot"]
    assert idx["unique"] is True and idx["key"] == [("return_slot", 1)]


def _shopify_race(native, monkeypatch, wake):
    """Real cap, real booking, real stock writes. Retry A books U1 for refund
    RF-1 and stalls in its flip; B takes over and runs to the end on A's
    booking. Then A wakes and `wake()` decides its flip."""
    _seed(native, units=["U1"])
    native.returns.update_one({"return_id": RID}, {"$set": {"shopify_refund_id": "RF-1"}})
    real, seen = R._flip_sold_unit, {}

    def stalled(*a, **k):
        if not seen:
            seen["b"] = "running"
            native.returns.update_one({"return_id": RID}, {"$set": {
                "restock_started_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()}})
            seen["b"] = R._retry_restock(RID, USER, STORE)
            return wake(real, *a, **k)
        return real(*a, **k)

    monkeypatch.setattr(R, "_flip_sold_unit", stalled)
    try:
        a = R._retry_restock(RID, USER, STORE)
    except Exception as exc:  # noqa: BLE001 -- A's failed writes answer 500
        a = exc
    return a, seen["b"]


def test_a_shopify_taker_on_a_stale_booking_that_then_fails_keeps_the_step_open(native, monkeypatch):
    """1413-R7-SHOPIFY-BOOKING on the real path (no cap or booking mocked):
    B's own cap reads A's booking and trims its restock to nothing, so B
    answers 'applied' -- not None. A's flip then fails and A un-books. The
    record must reopen: U1 is still SOLD and no longer booked (D7)."""
    def fails(real, *a, **k):
        raise RuntimeError("socket reset")

    a, b = _shopify_race(native, monkeypatch, fails)
    assert native.stock_units.find_one({"_id": "U1"})["status"] == "SOLD"
    line = native.orders.find_one({"order_id": OID})["items"][0]
    assert float((line.get("restocked_refunds") or {}).get("RF-1") or 0) == 0
    doc = native.returns.find_one({"return_id": RID})
    assert doc["restock_applied"] is False, "record reads applied, the piece is SOLD and un-booked"
    assert doc["restock_in_progress"] is False
    # The next retry puts the one piece back, once.
    again = R._retry_restock(RID, USER, STORE)
    assert again["restock_applied"] is True
    assert _back(native) == ["U1"]
    assert R._retry_restock(RID, USER, STORE)["message"] == "Already restocked"


def test_a_shopify_taker_on_a_stale_booking_that_lands_stays_applied(native, monkeypatch):
    """The same race where A's flip lands: one piece back, the record applied,
    nothing reopened, no second unit."""
    a, b = _shopify_race(native, monkeypatch, lambda real, *a, **k: real(*a, **k))
    assert _back(native) == ["U1"]
    assert native.stock_units.count_documents({}) == 1
    doc = native.returns.find_one({"return_id": RID})
    assert doc["restock_applied"] is True and doc["restock_in_progress"] is False


def test_a_stale_shopify_retry_that_fails_while_its_taker_runs_keeps_the_takers_save(native, monkeypatch):
    """Two threads, real Mongo, real cap and booking. A books U1 and stalls in
    its flip; B takes over and reads A's booking. While B is still running, A
    wakes, its flip fails and it un-books. A never drops B's claim: B's own
    save reaches the record (its ids would be lost otherwise), then the
    record reopens because B's 'applied' rested on the booking A released."""
    import threading

    _seed(native, units=["U1"])
    native.returns.update_one({"return_id": RID}, {"$set": {"shopify_refund_id": "RF-1"}})
    a_booked, b_loaded = threading.Event(), threading.Event()
    real_flip, real_goods = R._flip_sold_unit, R._restock_good_items
    out = {}

    def flip(*a, **k):
        if threading.current_thread().name == "retry-a":
            a_booked.set()
            assert b_loaded.wait(30)
            raise RuntimeError("socket reset")
        return real_flip(*a, **k)

    def goods(*a, **k):
        if threading.current_thread().name != "retry-a":
            b_loaded.set()
            thread_a.join(30)  # A fails and releases while B holds the claim
        return real_goods(*a, **k)

    def run_a():
        try:
            out["a"] = R._retry_restock(RID, USER, STORE)
        except Exception as exc:  # noqa: BLE001
            out["a"] = exc

    monkeypatch.setattr(R, "_flip_sold_unit", flip)
    monkeypatch.setattr(R, "_restock_good_items", goods)
    thread_a = threading.Thread(target=run_a, name="retry-a")
    thread_a.start()
    assert a_booked.wait(30)
    native.returns.update_one({"return_id": RID}, {"$set": {
        "restock_started_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()}})
    b = R._retry_restock(RID, USER, STORE)
    assert not thread_a.is_alive()
    assert native.stock_units.find_one({"_id": "U1"})["status"] == "SOLD"
    doc = native.returns.find_one({"return_id": RID})
    assert doc.get("restock_completed_at"), "the taker's save never reached the record"
    assert doc["restock_applied"] is False and doc["restock_in_progress"] is False
    again = R._retry_restock(RID, USER, STORE)
    assert again["restock_applied"] is True and _back(native) == ["U1"]
    assert b["return_id"] == RID


class _PausingReturns:
    """The returns collection as the retry sees it, except that retry A
    (thread "retry-a") pauses right after the FIRST of _reopen_by_booking's
    two writes -- the record writes that are not its own (no claim token in
    the filter) -- until `resume` is set."""

    def __init__(self, real, between, resume):
        self._real, self._between, self._resume, self._paused = real, between, resume, False

    def __getattr__(self, name):
        return getattr(self._real, name)

    def update_one(self, flt, *a, **k):
        import threading

        res = self._real.update_one(flt, *a, **k)
        if (threading.current_thread().name == "retry-a" and not self._paused
                and "restock_claim" not in flt):
            self._paused = True
            self._between.set()
            assert self._resume.wait(30)
        return res


def test_a_taker_that_saves_between_the_stale_retrys_two_reopen_writes_still_reopens(native, monkeypatch):
    """Round-9 must-fix (correctness + money-stock lenses). A books U1, stalls
    in its flip; B takes over on A's booking. A's flip fails, it un-books, and
    its reopen runs its FIRST write while B is still running; B then saves
    'applied' on the booking and checks for a release; only then does A run
    its SECOND write. Whatever the order, the record must reopen: U1 is SOLD
    and no longer booked, so the Retry stock step must stay (D7)."""
    import threading

    _seed(native, units=["U1"])
    native.returns.update_one({"return_id": RID}, {"$set": {"shopify_refund_id": "RF-1"}})
    a_booked, b_loaded, a_between, b_done = (threading.Event() for _ in range(4))
    real_flip, real_goods, real_coll = R._flip_sold_unit, R._restock_good_items, R._returns_coll
    out = {}

    def flip(*a, **k):
        if threading.current_thread().name == "retry-a":
            a_booked.set()
            assert b_loaded.wait(30)
            raise RuntimeError("socket reset")
        return real_flip(*a, **k)

    def goods(*a, **k):
        if threading.current_thread().name != "retry-a":
            b_loaded.set()
            assert a_between.wait(30), "A never reached its reopen"
        return real_goods(*a, **k)

    def run_a():
        try:
            out["a"] = R._retry_restock(RID, USER, STORE)
        except Exception as exc:  # noqa: BLE001
            out["a"] = exc

    monkeypatch.setattr(R, "_flip_sold_unit", flip)
    monkeypatch.setattr(R, "_restock_good_items", goods)
    monkeypatch.setattr(R, "_returns_coll", lambda: _PausingReturns(real_coll(), a_between, b_done))
    thread_a = threading.Thread(target=run_a, name="retry-a")
    thread_a.start()
    assert a_booked.wait(30)
    native.returns.update_one({"return_id": RID}, {"$set": {
        "restock_started_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()}})
    try:
        R._retry_restock(RID, USER, STORE)  # B: its save lands between A's two writes
    finally:
        b_done.set()
        thread_a.join(30)
    assert not thread_a.is_alive()
    assert native.stock_units.find_one({"_id": "U1"})["status"] == "SOLD"
    line = native.orders.find_one({"order_id": OID})["items"][0]
    assert float((line.get("restocked_refunds") or {}).get("RF-1") or 0) == 0
    doc = native.returns.find_one({"return_id": RID})
    assert doc.get("restock_completed_at"), "the taker's save never reached the record"
    assert doc["restock_applied"] is False, "record reads applied, the piece is SOLD and un-booked"
    assert doc["restock_in_progress"] is False
    again = R._retry_restock(RID, USER, STORE)
    assert again["restock_applied"] is True and _back(native) == ["U1"]
    assert R._retry_restock(RID, USER, STORE)["message"] == "Already restocked"


def test_two_live_retries_over_a_trace_gap_put_back_one_unit_for_the_last_piece(native, monkeypatch):
    """Limit 3 (the old _most path), the other interleaving: a pre-PR record
    whose `restocked` rows count more pieces back than the stock shows. A and
    B both read the pieces still out BEFORE either lands (A stalls before its
    flip; B takes over and finishes). They pick the same slot, so A's late
    write is refused: never a fourth record, never a second unit for the
    last piece (D45a)."""
    _seed(native, units=["U3"], qty=3)
    native.stock_units.insert_many([
        {"_id": "U1", "stock_id": "U1", "product_id": PID, "store_id": STORE, "status": "AVAILABLE",
         "returned_by_return_id": RID, "quantity": 1},
        {"_id": "U2", "stock_id": "U2", "product_id": PID, "store_id": STORE, "status": "AVAILABLE",
         "quantity": 1}])
    native.returns.update_one({"return_id": RID}, {"$set": {
        "items.0.return_qty": 3, "restock_stock_ids": ["U1"],
        "restocked": [{"product_id": PID, "sku": "F1", "quantity": 2, "reactivated": 2, "minted": 0,
                       "applied": False}]}})
    a, b = _interleave(native, monkeypatch, R, "_flip_sold_unit")
    assert b["restock_applied"] is True
    assert native.stock_units.count_documents({}) == 3, "a fourth record was minted for one piece"
    assert native.stock_units.count_documents({"status": "AVAILABLE"}) == 3
    assert native.stock_units.count_documents({"return_slot": {"$type": "string"}}) == 1
    doc = native.returns.find_one({"return_id": RID})
    assert doc["restock_applied"] is True and doc["restock_in_progress"] is False


def test_the_retry_refuses_when_the_return_slot_index_is_missing(native):
    """The slot guarantee (D45a) IS the index: without it two live retries can
    both move one piece. So the Retry stock step refuses -- nothing claimed,
    nothing moved -- until the index is back, rather than running unguarded."""
    from fastapi import HTTPException

    _seed(native, units=["U1"])
    native.stock_units.drop_index("uniq_stock_unit_return_slot")
    with pytest.raises(HTTPException) as err:
        R._retry_restock(RID, USER, STORE, panel=True)
    assert err.value.status_code == 503
    assert native.stock_units.find_one({"_id": "U1"})["status"] == "SOLD"
    doc = native.returns.find_one({"return_id": RID})
    assert doc["restock_in_progress"] is False and not doc.get("restock_claim")
    native.stock_units.create_index("return_slot", unique=True, name="uniq_stock_unit_return_slot",
                                    partialFilterExpression={"return_slot": {"$type": "string"}})
    assert R._retry_restock(RID, USER, STORE)["restock_applied"] is True
    assert _back(native) == ["U1"]
