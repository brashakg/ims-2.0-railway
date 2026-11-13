"""A piece an edit took off a shipped bill comes back: its return is durable, on REAL Mongo.

Codex round 5 of PR #1393 (12 Nov 2026), native Mongo: the goods-only return
claimed the removed piece and restocked it BEFORE its return doc existed, outside
the counter return's recovery protocol. A failure just before the doc's insert
left removed_lines.returned_qty = 1, the unit AVAILABLE, no return record and no
recoverable obligation; the identical retry answered 400 "already returned".

The door now runs THE counter return's protocol: the refund hold on the bill is
the press's intent, a failure before any unit moves gives the claim back, the
hold is KEPT with the press's facts before the restock, an unsaved doc rides on
the hold, and the identical retry saves it. Every case reads the stored rows
(unit, removed line, returns, the hold, tasks), not the reply.

Real Mongo because mongomock cannot run the claim's positional $elemMatch-with-$or
update (the in-memory suite stubs it). Locally: CI=1 PSI_NATIVE_MONGO_URL=... and
PSI_NATIVE_MONGO_DB=...; on CI the backend job's mongo:7.0 service. No emoji.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

pytest.importorskip("mongomock")  # the shared helpers import it

from tests.test_order_edit_recovery import _parcel_with_a_piece_removed  # noqa: E402
from tests.test_order_edit_stock_identity import (  # noqa: E402,F401  (fixtures)
    ADMIN,
    _order,
    _token,
    _unit,
    invoiced,
)
from tests.test_pos_stock_identity import seed_shop  # noqa: E402

_DB = os.getenv("PSI_NATIVE_MONGO_DB") or "psi_removed_goods_native"


@pytest.fixture
def shop(monkeypatch):
    """The same shop as the in-memory suite, on a real Mongo database of its own
    (dropped and seeded per test)."""
    from pymongo import MongoClient

    from api.routers import returns
    from database.repositories.order_repository import OrderRepository
    from database.repositories.product_repository import StockRepository

    url = os.getenv("PSI_NATIVE_MONGO_URL") or os.getenv("MONGODB_URL") or "mongodb://localhost:27017"
    client = MongoClient(url, serverSelectionTimeoutMS=3000)
    assert _DB.startswith(("psi_", "ims_claude_r6_1393_")), _DB
    client.drop_database(_DB)
    db = client[_DB]
    assert db.name == _DB
    shop = seed_shop(db, monkeypatch)
    monkeypatch.setattr(returns, "get_order_repository", lambda: OrderRepository(db.get_collection("orders")))
    monkeypatch.setattr(returns, "get_stock_repository", lambda: StockRepository(db.get_collection("stock_units")))
    from api import dependencies as dm
    from database.repositories.task_repository import TaskRepository

    monkeypatch.setattr(dm, "get_task_repository", lambda: TaskRepository(db.get_collection("tasks")))
    yield shop
    client.close()


KEY = "press-removed-piece-1"


def _press(client, oid, item_id, key=KEY, **over):
    quiet = type(client)(client.app, raise_server_exceptions=False)
    return quiet.post(
        "/api/v1/returns",
        json={"order_id": oid, "return_type": "RETURN",
              "items": [{"order_item_id": item_id, "return_qty": 1, "unit_price": 0,
                         "condition": "GOOD", "restock": True, **over}]},
        headers={**_token(ADMIN), "Idempotency-Key": key},
    )


def _rows(shop, oid):
    """What is stored: the piece, the removed line's count, the return docs, the
    bill's holds, the money, the open tasks."""
    db = shop["db"]
    order = _order(shop, oid)
    unit = _unit(shop, "SU-FR-A")
    return {
        "unit": (unit["status"], unit.get("order_id")),
        "returned_qty": float(order["removed_lines"][0].get("returned_qty") or 0),
        "returns": [(d["return_id"], d.get("status"), d.get("goods_only"), d.get("net_refund"))
                    for d in db.get_collection("returns").find({"order_id": oid})],
        "holds": order.get("refund_holds") or {},
        "money": (order.get("grand_total"), order.get("amount_paid"), order.get("balance_due")),
        "credits": db.get_collection("credit_note_ledger").count_documents({"order_id": oid}),
        "open_tasks": sorted(t["source_ref"] for t in db.get_collection("tasks").find(
            {"source_ref": {"$regex": "^return_"}, "status": {"$nin": ["COMPLETED", "CANCELLED"]}})),
    }


def _fail_insert(monkeypatch, oid, land: bool):
    """The return doc's insert errors once: before it landed, or after (lost reply)."""
    from pymongo.collection import Collection

    real = Collection.insert_one
    fired = []

    def insert(self, doc, *a, **k):
        if self.name == "returns" and doc.get("order_id") == oid and not fired:
            fired.append(True)
            if land:
                real(self, doc, *a, **k)
            raise TimeoutError("returns write timed out")
        return real(self, doc, *a, **k)

    monkeypatch.setattr(Collection, "insert_one", insert)
    return fired


def test_the_removed_piece_comes_back_once_on_real_mongo(client, shop, invoiced):
    oid, frame = _parcel_with_a_piece_removed(client, shop, invoiced)
    money = _rows(shop, oid)["money"]
    r = _press(client, oid, frame["item_id"])
    assert r.status_code == 201, r.text
    rid = r.json()["return_id"]
    rows = _rows(shop, oid)
    assert rows["unit"] == ("AVAILABLE", None) and rows["returned_qty"] == 1
    assert rows["returns"] == [(rid, "COMPLETED", True, 0.0)]
    assert rows["holds"][rid]["amount"] == 0.0 and rows["holds"][rid]["kept"] is True
    assert rows["money"] == money and rows["credits"] == 0 and rows["open_tasks"] == []
    again = _press(client, oid, frame["item_id"])
    assert again.status_code == 201 and again.json()["return_id"] == rid
    assert _rows(shop, oid) == rows


def test_a_doc_that_never_saved_is_saved_by_the_identical_retry(client, shop, invoiced, monkeypatch):
    """The reported case: the insert fails before it lands."""
    oid, frame = _parcel_with_a_piece_removed(client, shop, invoiced)
    money = _rows(shop, oid)["money"]
    with monkeypatch.context() as m:
        fired = _fail_insert(m, oid, land=False)
        first = _press(client, oid, frame["item_id"])
    assert fired
    assert first.status_code == 201, first.text
    rid = first.json()["return_id"]
    rows = _rows(shop, oid)
    assert rows["unit"] == ("AVAILABLE", None) and rows["returned_qty"] == 1
    assert rows["returns"] == [], "the doc did not land"
    assert rows["holds"][rid]["doc"]["return_id"] == rid, "it rides on the kept hold"
    assert rows["open_tasks"] == [f"return_doc_missing:{rid}"]
    retry = _press(client, oid, frame["item_id"])
    assert retry.status_code == 201, retry.text
    assert retry.json()["return_id"] == rid and retry.json()["_idempotent_replay"] is True
    rows = _rows(shop, oid)
    assert rows["returns"] == [(rid, "COMPLETED", True, 0.0)]
    assert rows["unit"] == ("AVAILABLE", None) and rows["returned_qty"] == 1, "one physical effect"
    assert rows["money"] == money and rows["credits"] == 0
    assert rows["open_tasks"] == [], "the retry closed the task"
    assert _press(client, oid, frame["item_id"]).json()["return_id"] == rid
    assert len(_rows(shop, oid)["returns"]) == 1


def test_a_doc_that_landed_but_lost_its_reply_is_replayed(client, shop, invoiced, monkeypatch):
    oid, frame = _parcel_with_a_piece_removed(client, shop, invoiced)
    with monkeypatch.context() as m:
        fired = _fail_insert(m, oid, land=True)
        first = _press(client, oid, frame["item_id"])
    assert fired and first.status_code == 201, first.text
    rid = first.json()["return_id"]
    rows = _rows(shop, oid)
    assert rows["returns"] == [(rid, "COMPLETED", True, 0.0)] and rows["open_tasks"] == []
    assert "doc" not in rows["holds"][rid], "read back: it landed"
    retry = _press(client, oid, frame["item_id"])
    assert retry.status_code == 201 and retry.json()["return_id"] == rid
    assert _rows(shop, oid) == rows


def test_a_failure_before_the_piece_moves_gives_the_claim_back(client, shop, invoiced, monkeypatch):
    from api.routers import returns

    oid, frame = _parcel_with_a_piece_removed(client, shop, invoiced)
    with monkeypatch.context() as m:
        m.setattr(returns, "_keep_order_refund_hold", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("x")))
        first = _press(client, oid, frame["item_id"])
    assert first.status_code == 500
    rows = _rows(shop, oid)
    assert rows["unit"] == ("SOLD", oid) and rows["returned_qty"] == 0
    assert rows["returns"] == [] and rows["holds"] == {}, "claim and hold given back"
    retry = _press(client, oid, frame["item_id"])
    assert retry.status_code == 201, retry.text
    rows = _rows(shop, oid)
    assert rows["unit"] == ("AVAILABLE", None) and rows["returned_qty"] == 1 and len(rows["returns"]) == 1


def test_a_give_back_that_did_not_finish_is_finished_by_nothing_was_paid(client, shop, invoiced, monkeypatch):
    """Review of round 6: the press fails before the piece moves AND its give-back
    does not finish (one short outage across two writes), so the hold is kept with
    the claimed line. 'Nothing was paid' must give the claim back on the REMOVED
    line (not the billed items), so the piece can be recorded again."""
    from api.routers import returns

    oid, frame = _parcel_with_a_piece_removed(client, shop, invoiced)
    real_keep, real_release = returns._keep_order_refund_hold, returns._release_returnable_qty
    keeps, releases = [], []

    def keep(*a, **k):
        keeps.append(1)
        if len(keeps) == 1:  # the keep before the restock
            raise TimeoutError("orders write timed out")
        return real_keep(*a, **k)

    def release(*a, **k):
        releases.append(1)
        return False if len(releases) == 1 else real_release(*a, **k)

    with monkeypatch.context() as m:
        m.setattr(returns, "_keep_order_refund_hold", keep)
        m.setattr(returns, "_release_returnable_qty", release)
        first = _press(client, oid, frame["item_id"])
    assert first.status_code == 500
    rows = _rows(shop, oid)
    (rid,) = rows["holds"]
    assert rows["unit"] == ("SOLD", oid) and rows["returned_qty"] == 1, "the claim is still held"
    assert rows["returns"] == [] and rows["holds"][rid]["kept"] is True
    assert not rows["holds"][rid].get("money_out")
    assert rows["holds"][rid]["lines"][0].get("removed_from_bill") is True, "the hold says which array"
    assert rows["holds"][rid].get("claims_released") and not rows["holds"][rid].get("claims_released_done")
    # An hour passes: the hold and its lost give-back are both stale.
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    shop["db"].get_collection("orders").update_one({"order_id": oid}, {"$set": {
        f"refund_holds.{rid}.at": old, f"refund_holds.{rid}.claims_released_at": old}})
    done = client.post(f"/api/v1/returns/{rid}/complete-record", json={"nothing_paid": True},
                       headers=_token(ADMIN))
    assert done.status_code == 201, done.text
    rows = _rows(shop, oid)
    assert rows["returned_qty"] == 0, "the removed line's claim was given back"
    assert rows["unit"] == ("SOLD", oid) and rows["holds"] == {}
    assert [(r[0], r[1]) for r in rows["returns"]] == [(rid, "NOTHING_PAID")]
    again = _press(client, oid, frame["item_id"], key="press-removed-piece-2")
    assert again.status_code == 201, again.text
    rows = _rows(shop, oid)
    assert rows["unit"] == ("AVAILABLE", None) and rows["returned_qty"] == 1


def test_a_press_that_died_after_the_piece_moved_is_finished_once(client, shop, invoiced, monkeypatch):
    """Stopped after the restock and before any doc existed: the retry is refused
    with the Needs-completing pointer (never a second restock); Complete record
    writes it, and the press's retry then replays that record."""
    from api.routers import returns

    oid, frame = _parcel_with_a_piece_removed(client, shop, invoiced)
    units = shop["db"].get_collection("stock_units").count_documents({"product_id": frame["product_id"]})

    def died(*a, **k):
        raise TimeoutError("worker died")

    with monkeypatch.context() as m:
        m.setattr(returns, "_quarantine_damaged_units", died)
        first = _press(client, oid, frame["item_id"])
    assert first.status_code == 500
    rows = _rows(shop, oid)
    (rid,) = rows["holds"]
    assert rows["unit"] == ("AVAILABLE", None) and rows["returned_qty"] == 1
    assert rows["returns"] == [] and rows["holds"][rid]["money_out"] is True
    retry = _press(client, oid, frame["item_id"])
    assert retry.status_code == 409, retry.text
    assert rid in retry.json()["detail"] and "Needs completing" in retry.json()["detail"]
    assert _rows(shop, oid)["returned_qty"] == 1
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    shop["db"].get_collection("orders").update_one({"order_id": oid}, {"$set": {f"refund_holds.{rid}.at": old}})
    done = client.post(f"/api/v1/returns/{rid}/complete-record", json={"cash_leg_paid": True},
                       headers=_token(ADMIN))
    assert done.status_code == 201, done.text
    # The piece is already on the shelf: the rebuilt record says so (from the
    # restock kept on the hold) and never asks for a second, hand-made unit.
    assert done.json()["restock_applied"] is True, done.json()
    assert "by hand" not in done.json()["message"], done.json()["message"]
    rows = _rows(shop, oid)
    assert rows["returns"] == [(rid, "COMPLETED", True, 0.0)]
    assert shop["db"].get_collection("returns").find_one({"return_id": rid})["restock_applied"] is True
    assert shop["db"].get_collection("stock_units").count_documents({"product_id": frame["product_id"]}) == units
    again = _press(client, oid, frame["item_id"])
    assert again.status_code == 201 and again.json()["return_id"] == rid
    assert len(_rows(shop, oid)["returns"]) == 1 and _rows(shop, oid)["unit"] == ("AVAILABLE", None)


def test_a_wrong_serial_on_a_removed_piece_moves_nothing_on_real_mongo(client, shop, invoiced):
    shop["db"].get_collection("stock_units").update_one({"stock_id": "SU-FR-A"}, {"$set": {"serial": "SN-A"}})
    oid, frame = _parcel_with_a_piece_removed(client, shop, invoiced)
    r = _press(client, oid, frame["item_id"], serial="WRONG-PIECE")
    assert r.status_code == 409, r.text
    rows = _rows(shop, oid)
    assert rows["unit"] == ("SOLD", oid) and rows["returned_qty"] == 0
    assert rows["returns"] == [] and rows["holds"] == {}
