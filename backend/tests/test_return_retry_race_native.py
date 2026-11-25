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
from tests.test_returns_restock import ctx  # noqa: E402,F401  (the press fixture)

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
    return _stale(), seen["b"]


def _stale():
    """Retry A's answer: a dict, or -- when it reaches a further unit write
    after the takeover -- the 409 of a stale claim (D79)."""
    from fastapi import HTTPException

    try:
        return R._retry_restock(RID, USER, STORE)
    except HTTPException as exc:
        assert exc.status_code == 409, exc.detail
        return exc


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
    a = _stale()
    assert seen["b"]["restock_applied"] is True
    assert not isinstance(a, dict) or a["restock_applied"] is False
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

    def booked(order, lines, rid, restock, processing_store_id=None, owner=None, **_kw):
        if seen:
            return None  # B: A's booking fills the refund's mark
        seen["a"] = True
        native.returns.update_one({"return_id": RID}, {"$set": {
            "restock_started_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()}})
        seen["b"] = R._retry_restock(RID, USER, STORE)
        # A woke; its writes failed and its booking was taken back off the line.
        return {"applied": False, "restocked": [], "restock_stock_ids": [], "unbooked": 1.0}

    monkeypatch.setattr(SR, "_restock_booked", booked)
    _stale()  # A: its save misses on the token -- 409 (D79)
    assert native.stock_units.find_one({"_id": "U1"})["status"] == "SOLD"
    doc = native.returns.find_one({"return_id": RID})
    assert doc["restock_applied"] is False, "record reads applied, the piece is not in stock"
    assert doc["restock_in_progress"] is False


@pytest.mark.parametrize("how", ["takeover", "renew_error"])
def test_a_shopify_retry_stopped_mid_loop_keeps_the_booking_of_the_unit_it_landed(native, monkeypatch, how):
    """#1413 review (D79 fence): a Shopify refund's retry A puts its first
    piece back, then stops before the second -- taken over (B finds A's
    booking) between two flips, or its claim renewal fails right before a
    mint (the returned line's pieces were never stock rows; the bill's two
    SOLD units are named by its OTHER lines, D43). The landed piece must stay
    booked: the Shopify doors read only the booking, so a released piece is
    put back again by the next retry (a second unit flipped or a phantom
    minted). The second piece alone is then put back -- by the next retry
    (renew_error), or by the taker itself (takeover, D122(3): it releases
    only the stopped worker's booking of the piece that never landed and
    places that piece on the slot it was given) -- and the refund's mark
    ends at 2."""
    _seed(native, units=["U1", "U2"], qty=2)
    native.returns.update_one({"return_id": RID}, {"$set": {"shopify_refund_id": "RF-1",
                                                             "items.0.return_qty": 2}})
    if how == "renew_error":  # li1's pieces mint: U1 / U2 are li2's and li3's
        native.orders.update_one({"order_id": OID}, {"$push": {"items": {"$each": [
            {"item_id": f"li{n}", "product_id": PID, "sku": "F1", "product_name": "Frame",
             "quantity": 1, "unit_price": 1000.0, "stock_id": f"U{n - 1}"} for n in (2, 3)]}}})
    real, seen = R._audit_stock_transition, {}

    def landed(*a, **k):
        out = real(*a, **k)
        if not seen:  # A's first piece is back: it stalls before the second
            seen["b"] = "running"
            if how == "takeover":
                native.returns.update_one({"return_id": RID}, {"$set": {
                    "restock_started_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()}})
                seen["b"] = R._retry_restock(RID, USER, STORE)
            else:
                fence = R._RESTOCK_FENCE.get()

                def renew_fails():
                    R._RESTOCK_FENCE.set(fence)
                    raise RuntimeError("socket reset on the renew")

                R._RESTOCK_FENCE.set(renew_fails)
        return out

    monkeypatch.setattr(R, "_audit_stock_transition", landed)
    try:
        R._retry_restock(RID, USER, STORE)
    except Exception:  # noqa: BLE001 -- A answers 409 / 500
        pass
    monkeypatch.setattr(R, "_audit_stock_transition", real)
    line = native.orders.find_one({"order_id": OID})["items"][0]
    doc = native.returns.find_one({"return_id": RID})
    if how == "takeover":  # B finished the step: A's first piece credited, the second placed once
        assert seen["b"]["restock_applied"] is True and doc["restock_applied"] is True
        assert float(line["restocked_refunds"]["RF-1"]) == 2
        assert sorted(u["return_slot"] for u in native.stock_units.find({})) == [
            f"{RID}|back|{PID}|0", f"{RID}|back|{PID}|1"]
    else:
        assert native.stock_units.count_documents({"status": "AVAILABLE"}) == 1
        # The booking names both slots: it stays whole (slot 0 held -> landed,
        # slot 1 free -> the next run places it there), never taken back by
        # a per-product count (review of 22f53ec0).
        assert float(line["restocked_refunds"]["RF-1"]) == 2, "the booking was taken back"
        assert doc["restock_applied"] is False and doc["restock_in_progress"] is False
        again = R._retry_restock(RID, USER, STORE)
        assert again["restock_applied"] is True
    assert native.stock_units.count_documents({}) == (2 if how == "takeover" else 4), (
        "a second record was added for a piece")
    assert native.stock_units.count_documents({"status": "AVAILABLE"}) == 2
    line = native.orders.find_one({"order_id": OID})["items"][0]
    assert float(line["restocked_refunds"]["RF-1"]) == 2
    assert R._retry_restock(RID, USER, STORE)["message"] == "Already restocked"


def test_the_return_slot_backstop_is_in_the_apps_index_set(native):
    idx = native.stock_units.index_information()["uniq_stock_unit_return_slot"]
    assert idx["unique"] is True and idx["key"] == [("return_slot", 1)]
    # P1-A: every slot a unit ever carried stays unique too.
    idx = native.stock_units.index_information()["uniq_stock_unit_return_slots"]
    assert idx["unique"] is True and idx["key"] == [("return_slots", 1)]


def _shopify_race(native, monkeypatch, wake):
    """Real cap, real booking, real stock writes. Retry A books U1 for refund
    RF-1 and stalls in its flip; B takes the stopped step over and runs to
    the end. Then A wakes and `wake()` decides its flip."""
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


def _booked_rf1(db):
    line = db.orders.find_one({"order_id": OID})["items"][0]
    return float((line.get("restocked_refunds") or {}).get("RF-1") or 0)


def test_a_shopify_taker_of_a_stopped_booking_places_the_unit_itself(native, monkeypatch):
    """D122(3) on the real path (no cap or booking mocked). Before: B's cap
    read A's booking and trimmed its restock to nothing, so B answered
    'applied' on it; when A then failed and un-booked, the record had to be
    reopened by A -- and a DEAD A never did (the unit stayed SOLD behind
    'Restock applied'). Now B releases the booking of the stopped worker
    that has no unit behind it and places U1 itself; A's failed write and
    its un-booking (by a token that is gone) change nothing."""
    def fails(real, *a, **k):
        raise RuntimeError("socket reset")

    a, b = _shopify_race(native, monkeypatch, fails)
    assert b["restock_applied"] is True and _back(native) == ["U1"]
    assert native.stock_units.count_documents({}) == 1 and _booked_rf1(native) == 1
    doc = native.returns.find_one({"return_id": RID})
    assert doc["restock_applied"] is True and doc["restock_in_progress"] is False
    assert doc["restock_applied_by_booking"] is False  # it rests on B's own unit
    assert R._retry_restock(RID, USER, STORE)["message"] == "Already restocked"
    assert _back(native) == ["U1"] and _booked_rf1(native) == 1


def test_a_shopify_taker_of_a_stopped_booking_whose_holder_then_lands_stays_one_row(native, monkeypatch):
    """The same race where A's flip goes on after B finished: U1 is no
    longer SOLD on the bill and its slot is used. One piece back, booked
    once, the record applied, no second unit."""
    a, b = _shopify_race(native, monkeypatch, lambda real, *a, **k: real(*a, **k))
    assert _back(native) == ["U1"]
    assert native.stock_units.count_documents({}) == 1 and _booked_rf1(native) == 1
    doc = native.returns.find_one({"return_id": RID})
    assert doc["restock_applied"] is True and doc["restock_in_progress"] is False


# --- A stale retry that booked LATE, under its taker (D129) ------------------------------------
# A stalls between its claim check and its booking. B takes the step over
# (nothing of A's is booked yet), reads the order, caps and picks its slots;
# only then does A's booking land, naming the SAME slot. Before D129 B's own
# booking found the refund's mark full and B saved 'applied' on A's booking;
# A then stopped at its first unit write and un-booked, and the record had to
# reopen (1413-R7-SHOPIFY-BOOKING). Now B's booking is refused on the slot
# A's names (shopify_refund.SlotConflict), B plans again, TAKES OVER A's
# booking (no unit behind it) and places the piece on that slot: the record
# is never 'applied' on a booking with no unit, whatever the order of writes.


def _late_booking(native, monkeypatch, b_after_booking=None):
    """Threads, real Mongo, real cap, booking and stock writes. Returns
    (thread A, run B, go: A holds its late booking until go is set).
    `b_after_booking`: called in B right after its own booking found the
    mark full, before it saves."""
    import threading

    from api.services import shopify_refund as SR

    _seed(native, units=["U1"])
    native.returns.update_one({"return_id": RID}, {"$set": {"shopify_refund_id": "RF-1"}})
    a_ready, b_capped, a_booked, go = (threading.Event() for _ in range(4))
    real_hold, out = SR._hold_returned_qty, {}

    def hold(*a, **k):
        if threading.current_thread().name == "retry-a":
            a_ready.set()
            assert b_capped.wait(30)  # B has read the order and capped: A's booking is not in it
            held = real_hold(*a, **k)
            a_booked.set()
            assert go.wait(30)
            return held
        if out.get("b"):
            return real_hold(*a, **k)  # a later run: the race is over
        out["b"] = True
        b_capped.set()
        assert a_booked.wait(30)
        with pytest.raises(SR.SlotConflict):  # A's late booking names B's slot
            real_hold(*a, **k)
        if b_after_booking:
            b_after_booking()
        raise SR.SlotConflict("RF-1")  # B plans again (the real refusal, re-raised after the hook)

    def run_a():
        try:
            out["a"] = R._retry_restock(RID, USER, STORE)
        except Exception as exc:  # noqa: BLE001
            out["a"] = exc

    monkeypatch.setattr(SR, "_hold_returned_qty", hold)
    thread_a = threading.Thread(target=run_a, name="retry-a")
    thread_a.start()
    assert a_ready.wait(30)
    native.returns.update_one({"return_id": RID}, {"$set": {
        "restock_started_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()}})
    return thread_a, (lambda: R._retry_restock(RID, USER, STORE)), go


def _reopened_and_finished_once(native, by):
    """The piece is back ONCE, booked once, the record applied -- and it stays
    so through the next press and the hourly sweep (`by`: which runs first)."""
    for _ in range(2):
        assert _back(native) == ["U1"] and native.stock_units.count_documents({}) == 1
        assert _booked_rf1(native) == 1
        doc = native.returns.find_one({"return_id": RID})
        assert doc["restock_applied"] is True, "record does not read applied with the piece back"
        assert doc["restock_in_progress"] is False
        if by == "sweep":
            assert R.finish_stopped_stock_steps() == 0
            assert R._retry_restock(RID, USER, STORE)["message"] == "Already restocked"
        else:
            assert R._retry_restock(RID, USER, STORE)["message"] == "Already restocked"
            assert R.finish_stopped_stock_steps() == 0


@pytest.mark.parametrize("by", ["press", "sweep"])
def test_a_shopify_taker_on_a_late_stale_booking_that_is_then_given_up_keeps_the_step_open(native, monkeypatch, by):
    """B finishes first: it takes over A's late booking and places the piece
    on its slot. A then stops at its first unit write; its un-booking finds
    its token gone. One piece back, booked once (D129)."""
    thread_a, run_b, go = _late_booking(native, monkeypatch)
    b = run_b()
    go.set()
    thread_a.join(30)
    assert not thread_a.is_alive() and b["return_id"] == RID
    _reopened_and_finished_once(native, by)


def test_a_stale_shopify_retry_that_gives_up_while_its_taker_runs_keeps_the_takers_save(native, monkeypatch):
    """A un-books while B still holds the claim (between B's refused booking
    and its second plan). A never drops B's claim; B books the piece afresh
    and places it: one piece back, booked once."""
    box = {}
    thread_a, run_b, go = _late_booking(native, monkeypatch, lambda: (box["go"].set(), box["a"].join(30)))
    box["a"], box["go"] = thread_a, go
    b = run_b()
    assert not thread_a.is_alive() and b["return_id"] == RID
    _reopened_and_finished_once(native, "press")


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
    """Round-9 must-fix (correctness + money-stock lenses). A un-books, and
    its reopen runs its FIRST write while B is still running; B then saves
    'applied' on the booking and checks for a release; only then does A run
    its SECOND write. Whatever the order, the record must reopen: U1 is SOLD
    and no longer booked, so the Retry stock step must stay (D7).

    Since the review of 22f53ec0 a booking that names its slots is never
    taken back once a unit write was tried, so A never un-books and never
    runs that reopen at all: B waits for A to finish instead, A writes
    nothing to the record outside its own claim, and the end state is the
    same -- the piece back once, booked once, the record applied."""
    import threading

    a_between, b_done = threading.Event(), threading.Event()
    real_coll = R._returns_coll
    monkeypatch.setattr(R, "_returns_coll", lambda: _PausingReturns(real_coll(), a_between, b_done))
    box = {}
    thread_a, run_b, go = _late_booking(
        native, monkeypatch, lambda: (box["go"].set(), box["a"].join(30)))
    box["go"], box["a"] = go, thread_a
    try:
        run_b()  # B: saves after A has finished
        assert not a_between.is_set(), "A took its slot-named booking back and reopened the record"
    finally:
        b_done.set()
        thread_a.join(30)
    assert not thread_a.is_alive()
    _reopened_and_finished_once(native, "press")


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


@pytest.mark.parametrize("field", ["return_slot", "return_slots"])
def test_the_retry_refuses_when_the_return_slot_index_is_missing(native, field):
    """The slot guarantee (D45a) IS the index: without it two live retries can
    both move one piece. So the Retry stock step refuses -- nothing claimed,
    nothing moved -- until the index is back, rather than running unguarded.
    The same for the slot history (P1-A)."""
    from fastapi import HTTPException

    _seed(native, units=["U1"])
    name = "uniq_stock_unit_" + field
    native.stock_units.drop_index(name)
    with pytest.raises(HTTPException) as err:
        R._retry_restock(RID, USER, STORE, panel=True)
    assert err.value.status_code == 503
    assert native.stock_units.find_one({"_id": "U1"})["status"] == "SOLD"
    doc = native.returns.find_one({"return_id": RID})
    assert doc["restock_in_progress"] is False and not doc.get("restock_claim")
    native.stock_units.create_index(field, unique=True, name=name,
                                    partialFilterExpression={field: {"$type": "string"}})
    assert R._retry_restock(RID, USER, STORE)["restock_applied"] is True
    assert _back(native) == ["U1"]


# ---------------------------------------------------------------------------
# #1413 round 1, P1-A (Codex 2026-11-15, decision D79): a stale retry must
# never unsell a unit a new customer bought (and paid for) since it read it.
# ---------------------------------------------------------------------------
NEW_OID = "ORD-RACE-NEW"


def _resell(db, sid):
    """What the till's sale does to an AVAILABLE unit (claim_one_available):
    SOLD again, on the NEW bill."""
    res = db.stock_units.update_one({"stock_id": sid, "status": "AVAILABLE"}, {"$set": {
        "status": "SOLD", "order_id": NEW_OID, "sold_at": datetime.now(), "sold_to_customer_id": "C-NEW"}})
    assert res.matched_count == 1


@pytest.mark.parametrize("condition,taker_status", [("GOOD", "AVAILABLE"), ("DAMAGED", "QUARANTINED")])
def test_a_stale_retry_never_unsells_a_unit_resold_since_it_read_it(native, monkeypatch,
                                                                    condition, taker_status):
    """Codex's reproduction: A reads U1 (SOLD on the bill) and stalls at its
    flip; its claim ages, B takes over and moves U1 back; U1 is sold to a new
    customer (and, for a set-aside piece, released first). A wakes: U1 must
    stay SOLD on the NEW bill, nothing minted, and A stops with 409."""
    _seed(native, units=["U1"])
    native.returns.update_one({"return_id": RID}, {"$set": {"items.0.condition": condition}})
    real, seen = R._flip_sold_unit, {}

    def stalled(*a, **k):
        if not seen:
            seen["a"] = True
            native.returns.update_one({"return_id": RID}, {"$set": {
                "restock_started_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()}})
            seen["b"] = R._retry_restock(RID, USER, STORE)
            assert native.stock_units.find_one({"_id": "U1"})["status"] == taker_status
            if taker_status == "QUARANTINED":  # the manager releases it to sale
                native.stock_units.update_one({"_id": "U1"}, {"$set": {"status": "AVAILABLE"}})
            _resell(native, "U1")
        return real(*a, **k)

    monkeypatch.setattr(R, "_flip_sold_unit", stalled)
    a = _stale()
    assert seen["b"]["restock_applied"] is True
    unit = native.stock_units.find_one({"_id": "U1"})
    assert (unit["status"], unit["order_id"]) == ("SOLD", NEW_OID), f"the new bill's unit was unsold: {unit}"
    assert native.stock_units.count_documents({}) == 1, "a stale retry minted a second record"
    assert not isinstance(a, dict), f"the stale retry completed: {a}"
    doc = native.returns.find_one({"return_id": RID})
    assert doc["restock_applied"] is True and doc["restock_in_progress"] is False
    assert R._retry_restock(RID, USER, STORE)["message"] == "Already restocked"
    unit = native.stock_units.find_one({"_id": "U1"})
    assert (unit["status"], unit["order_id"]) == ("SOLD", NEW_OID)


def test_a_stale_retry_whose_flip_lands_after_the_resale_without_its_own_fence_still_misses(native, monkeypatch):
    """The ownership filter alone (no claim fence: the press and the Shopify
    doors hold no restock claim): a candidate read SOLD on this bill and
    resold before the write is never touched -- the flip answers False."""
    _seed(native, units=["U1"])
    native.stock_units.update_one({"_id": "U1"}, {"$set": {"status": "SOLD", "order_id": NEW_OID}})
    from api.dependencies import get_stock_repository

    repo = get_stock_repository()
    ok = R._flip_sold_unit(repo, "U1", {"status": "AVAILABLE", "returned_at": "x", "order_id": None},
                           expect={"order_id": OID})
    assert ok is False
    assert native.stock_units.find_one({"_id": "U1"})["order_id"] == NEW_OID


def test_a_later_return_of_the_resold_unit_never_frees_its_slot_for_a_second_record(native):
    """Sequential, no race: return R puts U1 back (slot 0) but its record save
    is lost (the stock is the truth). U1 is resold and returned again by
    return C, whose flip writes ITS slot and name over U1. R's next retry no
    longer finds U1 as its own: it must never mint a second record for the
    piece -- U1 keeps R's slot in its history (return_slots)."""
    from api.dependencies import get_stock_repository

    _seed(native, units=["U1"])
    assert R._retry_restock(RID, USER, STORE)["restock_applied"] is True
    # The record's save was lost: it reads as never restocked.
    native.returns.update_one({"return_id": RID}, {"$set": {
        "restock_applied": False, "restock_stock_ids": [], "restocked": []},
        "$unset": {"restock_claim": ""}})
    _resell(native, "U1")
    sid = R._reactivate_original_unit(get_stock_repository(), PID, STORE, NEW_OID, set(),
                                      return_id="RET-RACE-C", slot=f"RET-RACE-C|back|{PID}|0")
    assert sid == "U1" and native.stock_units.find_one({"_id": "U1"})["returned_by_return_id"] == "RET-RACE-C"
    # R's stock_audit row is fail-soft (_audit_stock_transition): without it
    # nothing on the stock names R any more.
    native.stock_audit.delete_many({"return_id": RID})
    again = R._retry_restock(RID, USER, STORE)
    assert native.stock_units.count_documents({}) == 1, (
        f"one piece became two records: {list(native.stock_units.find({}, {'_id': 1, 'source_id': 1}))}")
    assert again["restock_applied"] is True and again["restock_stock_ids"] == ["U1"]


# --------------------------------------------------------------------------- #
# The counter press (POST /returns) still running when Accounts completed its
# record (Returns > Needs completing) and ran the panel's Retry stock step.
# --------------------------------------------------------------------------- #
@pytest.fixture
def press(native, ctx, gst_numbers, monkeypatch):  # noqa: F811
    """The press on the fakes of test_returns_restock, its serialized stock on
    the REAL Mongo (the app's unique return-slot indexes)."""
    from database.repositories.product_repository import StockRepository

    repo = StockRepository(native.stock_units)
    monkeypatch.setattr(R, "get_stock_repository", lambda: repo)
    monkeypatch.setattr(R.barcode_svc, "mint_unit_barcode", lambda *a, **k: "BC-" + uuid.uuid4().hex)
    import agents.registry as registry
    from api.services import item_events as ie

    async def _quiet(event, payload, source=""):
        return None

    monkeypatch.setattr(registry, "dispatch_event", _quiet)
    monkeypatch.setattr(ie, "record_post_write_event", lambda db, **kw: None)
    return ctx, native


@pytest.mark.parametrize("units,condition,status", [
    (["STK-1"], "GOOD", "AVAILABLE"),            # the reviewer's case: the press would mint
    (["STK-1", "STK-2"], "GOOD", "AVAILABLE"),   # the press would put back the bill's other unit
    (["STK-1", "STK-2"], "DAMAGED", "QUARANTINED"),
])
def test_a_press_resuming_after_its_rebuilt_record_was_retried_adds_no_second_record(
        press, monkeypatch, units, condition, status):
    """D45(a), D79: the press issued the credit and stalled before its stock
    step; its hold aged, Accounts completed the record and the panel's Retry
    stock step moved the piece (its snapshot: nothing moved yet, slot 0).
    The press then resumes: it must take the same slot, so its write is
    refused and the piece is counted as the retry's unit -- never a second
    stock record, never the bill's other unit still with the customer."""
    from tests.test_returns_restock import _hdr, _payload

    ctx, db = press
    for sid in units:
        db.stock_units.insert_one({"_id": sid, "stock_id": sid, "product_id": "PRD-1", "store_id": "BV-PUN-01",
                                   "status": "SOLD", "order_id": "ORD-1", "quantity": 1})
    target = "_restock_good_items" if condition == "GOOD" else "_quarantine_damaged_units"
    real, retried = getattr(R, target), []

    def stalled(*a, **k):
        if not retried:  # meanwhile: the panel's Retry stock step, the shared rule
            retried.append(real(*a, **{**k, "moved_ids": set()}))
        return real(*a, **k)

    monkeypatch.setattr(R, target, stalled)
    body = _payload()
    body["items"][0]["condition"] = condition
    r = ctx["client"].post("/api/v1/returns", json=body, headers=_hdr(["ADMIN"]))
    assert r.status_code == 201, r.text
    moved = sorted(u["stock_id"] for u in db.stock_units.find({"status": status}))
    assert len(moved) == 1, f"one returned piece became {len(moved)} stock records: {moved}"
    assert db.stock_units.count_documents({}) == len(units), "the press minted a second record"
    assert db.stock_units.count_documents({"status": "SOLD"}) == len(units) - 1
    assert r.json()["restock_applied"] is True
    saved = ctx["returns_coll"].docs[-1]  # the press's own record (Accounts' rebuilt one is elsewhere)
    key = "restock_stock_ids" if condition == "GOOD" else "quarantined_stock_ids"
    assert saved["restock_applied"] is True and saved[key] == moved, saved


@pytest.mark.parametrize("field", ["return_slot", "return_slots"])
@pytest.mark.parametrize("condition,status", [("GOOD", "AVAILABLE"), ("DAMAGED", "QUARANTINED")])
def test_the_counter_press_refuses_when_a_return_slot_index_is_missing(press, field, condition, status):
    """D82: the slot guarantee IS the index, for the counter press as for the
    Retry stock step. Without it the press refuses (503) before it writes
    anything -- no stock, no credit, no record, no hold or claim left on the
    bill -- and the identical press, once the index is back, returns the
    piece once."""
    from tests.test_returns_restock import _hdr, _payload

    ctx, db = press
    db.stock_units.insert_one({"_id": "STK-1", "stock_id": "STK-1", "product_id": "PRD-1",
                               "store_id": "BV-PUN-01", "status": "SOLD", "order_id": "ORD-1", "quantity": 1})
    name = "uniq_stock_unit_" + field
    db.stock_units.drop_index(name)
    body = _payload()
    body["items"][0]["condition"] = condition
    r = ctx["client"].post("/api/v1/returns", json=body, headers=_hdr(["ADMIN"]))
    assert r.status_code == 503, r.text
    assert "Nothing was changed" in r.json()["detail"]
    assert db.stock_units.find_one({"_id": "STK-1"})["status"] == "SOLD"
    assert db.stock_units.count_documents({}) == 1
    assert ctx["returns_coll"].docs == [] and ctx["ledger_coll"].docs == []
    bill = ctx["order_repo"]._order
    assert not bill.get("refund_holds"), bill.get("refund_holds")
    assert not any(li.get("claim_tokens") or li.get("returned_qty") for li in bill["items"]), bill["items"]

    db.stock_units.create_index(field, unique=True, name=name,
                                partialFilterExpression={field: {"$type": "string"}})
    again = ctx["client"].post("/api/v1/returns", json=body, headers=_hdr(["ADMIN"]))
    assert again.status_code == 201, again.text
    assert [u["stock_id"] for u in db.stock_units.find({"status": status})] == ["STK-1"]
    assert db.stock_units.count_documents({}) == 1


def test_the_counter_press_refuses_when_the_index_list_cannot_be_read(press, monkeypatch):
    """D82: an unreadable index list is a missing guarantee -- 503, nothing written."""
    from pymongo.collection import Collection
    from pymongo.errors import AutoReconnect

    from tests.test_returns_restock import _hdr, _payload

    ctx, db = press
    db.stock_units.insert_one({"_id": "STK-1", "stock_id": "STK-1", "product_id": "PRD-1",
                               "store_id": "BV-PUN-01", "status": "SOLD", "order_id": "ORD-1", "quantity": 1})

    def unreadable(self, *a, **k):
        raise AutoReconnect("index list lost")

    monkeypatch.setattr(Collection, "index_information", unreadable)
    r = ctx["client"].post("/api/v1/returns", json=_payload(), headers=_hdr(["ADMIN"]))
    assert r.status_code == 503, r.text
    assert db.stock_units.find_one({"_id": "STK-1"})["status"] == "SOLD"
    assert ctx["returns_coll"].docs == [] and ctx["ledger_coll"].docs == []
    assert not ctx["order_repo"]._order.get("refund_holds")
