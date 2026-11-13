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


def test_the_return_slot_backstop_is_in_the_apps_index_set(native):
    idx = native.stock_units.index_information()["uniq_stock_unit_return_slot"]
    assert idx["unique"] is True and idx["key"] == [("return_slot", 1)]
