"""What an edit owes the shelf and the books, however its request ends.

Codex round 4 of PR #1393, 7 November 2026. Each test below failed on the reviewed
head a57a07c9 for the reported reason (real repositories over mongomock; the same
cases were run over real HTTP on real Mongo, see the handover). Every outcome is
read back off the collections, never the reply. No emoji (Windows cp1252).

  1  a landed edit whose settle never ran was finished by nothing
  2  a claim that wrote and lost its reply on a saved bill stranded the unit
  3  a payment during an edit left a paid bill showing money due
  4  a correction erased frozen cost and, on an id-only line, SKU / category / HSN
  5  a raised lens quantity outlived its bill; positional commits misread new lines
  6  the edit doors skipped the spectacle-prescription gate
  7  till and add-item stored a stock_id on a line that took no unit
  8  a piece taken off a bill that left had no return path; a failed give-back
     of an unsaved edit lived only in a log
  R  an identical revised-invoice retry spent a second serial (owner ruling 11-07)
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

pytest.importorskip("mongomock")

from tests.test_order_edit_stock_identity import (  # noqa: E402,F401  (fixtures)
    ADMIN,
    DOORS,
    FR,
    SVC,
    STAFF,
    STORE,
    _as_sent,
    _cancel,
    _edit,
    _lens,
    _lens_bill,
    _lens_line,
    _lens_sent,
    _line,
    _no_flag_left,
    _order,
    _other_line,
    _saved_bill,
    _sell,
    _order_id,
    _sold_to,
    _token,
    _unit,
    cells,
    door,
    invoiced,
    shop,
)

FLAG = "line_remove_stock_release_failed"


def _ops(shop, oid):
    return list(shop["db"].get_collection("order_edit_ops").find({"order_id": oid}, {"_id": 0}))


def _op_states(shop, oid):
    return [op["state"] for op in _ops(shop, oid)]


def _sweep(shop):
    """Run the scheduler's tick with every row past an in-flight press's lifetime."""
    from api.routers.orders import edit_ops

    shop["db"].get_collection("order_edit_ops").update_many(
        {}, {"$set": {"created_at": datetime.now() - timedelta(hours=1), "lease_until": None}}
    )
    return asyncio.run(edit_ops.sweep_edit_ops())


@pytest.fixture
def tasks(shop, monkeypatch):
    from api import dependencies as dm
    from database.repositories.task_repository import TaskRepository

    repo = TaskRepository(shop["db"].get_collection("tasks"))
    monkeypatch.setattr(dm, "get_task_repository", lambda: repo)
    return shop["db"].get_collection("tasks")


def _press_unreadable(client, monkeypatch, door, oid, lines):
    """The save lands, its reply is lost and the bill cannot be read back."""
    from database.repositories.order_repository import OrderRepository

    real_write, real_read = OrderRepository.update_if, OrderRepository.find_by_id
    fired = []

    def landed_then_dark(self, key, expected, data, *a, **k):
        answer = real_write(self, key, expected, data, *a, **k)
        if "items" in data and not fired:
            fired.append(True)
            raise TimeoutError("reply lost and the database gone")
        return answer

    def dark(self, key, *a, **k):
        return None if fired else real_read(self, key, *a, **k)

    quiet = type(client)(client.app, raise_server_exceptions=False)
    with monkeypatch.context() as m:
        m.setattr(OrderRepository, "update_if", landed_then_dark)
        m.setattr(OrderRepository, "find_by_id", dark)
        r = quiet.put(
            f"/api/v1/orders/{oid}/{door.name}",
            json={"reason": "correcting this bill", "items": lines, **DOORS[door.name]},
            headers=_token(ADMIN),
        )
    assert fired and r.status_code >= 500, r.text


def _unreadable_bill(client, shop, door, monkeypatch):
    """A bill whose landed edit dropped a frame and could not settle it."""
    oid = door.arm(_saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A"), _line(SVC)]))
    keep = next(ln for ln in _order(shop, oid)["items"] if ln["product_id"] == SVC)
    lines = [_as_sent(keep)]
    _press_unreadable(client, monkeypatch, door, oid, lines)
    assert len(_order(shop, oid)["items"]) == 1
    assert _unit(shop, "SU-FR-A")["status"] == "SOLD", "never freed on a guess"
    assert _order(shop, oid)[FLAG] is True
    assert _op_states(shop, oid) == ["OPEN"]
    return oid, lines


# ---------------------------------------------------------------------------
# 1 -- a landed edit that could not settle is finished, not flagged and forgotten
# ---------------------------------------------------------------------------


def test_an_identical_retry_finishes_the_settle(client, shop, door, monkeypatch):
    oid, lines = _unreadable_bill(client, shop, door, monkeypatch)
    r = door.press(oid, lines)
    assert r.status_code == 200, r.text
    unit = _unit(shop, "SU-FR-A")
    assert (unit["status"], unit.get("order_id")) == ("AVAILABLE", None)
    _no_flag_left(shop, oid)
    assert "SETTLED" in _op_states(shop, oid) and "OPEN" not in _op_states(shop, oid)


def test_the_sweep_finishes_a_settle_nobody_retried(client, shop, door, monkeypatch):
    oid, _lines = _unreadable_bill(client, shop, door, monkeypatch)
    assert _sweep(shop)["settled"] == 1
    assert _unit(shop, "SU-FR-A")["status"] == "AVAILABLE"
    _no_flag_left(shop, oid)
    assert _sweep(shop) == {"settled": 0, "given_back": 0, "unfinished": 0, "tasks": 0}


def test_a_settle_that_died_halfway_resumes_by_name(client, shop, door, monkeypatch):
    """A first-available unit has no name on the bill, so a blind re-run would
    free the unit the bill still sells. The op named it before the save."""
    from database.repositories.product_repository import StockRepository

    shop["db"].get_collection("stock_units").insert_one(
        {"stock_id": "SU-FR-C", "barcode": "BC-C", "product_id": FR, "store_id": STORE,
         "status": "AVAILABLE", "quantity": 1}
    )
    oid = door.arm(_saved_bill(client, shop, [_line(FR, quantity=3), _line(SVC)]))
    assert len(_sold_to(shop, oid)) == 3
    frame = next(ln for ln in _order(shop, oid)["items"] if ln["product_id"] == FR)
    other = next(ln for ln in _order(shop, oid)["items"] if ln["product_id"] == SVC)

    def dead(self, *a, **k):
        raise TimeoutError("stock store gone")

    with monkeypatch.context() as m:
        m.setattr(StockRepository, "release_sold_units_for_order", dead)
        r = door.press(oid, [_as_sent(frame, quantity=2), _as_sent(other)])
    assert r.status_code == 200, r.text
    assert len(_sold_to(shop, oid)) == 3 and _order(shop, oid)[FLAG] is True
    assert _op_states(shop, oid) == ["OPEN"]
    assert _sweep(shop)["settled"] == 1
    assert len(_sold_to(shop, oid)) == 2, "one piece back, and only one"
    assert _sweep(shop)["settled"] == 0
    assert len(_sold_to(shop, oid)) == 2
    _no_flag_left(shop, oid)


def test_a_retried_settle_gives_a_lowered_lens_back_once(client, shop, cells, door, monkeypatch):
    oid, stored = _lens_bill(client, shop, cells, qty=3)
    door.arm(oid)
    lines = [_other_line(shop, oid), _lens_sent(stored, quantity=1)]
    _press_unreadable(client, monkeypatch, door, oid, lines)
    assert cells.read() == (5, 3)
    assert door.press(oid, lines).status_code == 200
    assert cells.read() == (5, 1)
    assert door.press(oid, lines).status_code == 200
    assert cells.read() == (5, 1)
    _no_flag_left(shop, oid)


def test_an_edit_that_never_landed_is_not_settled(client, shop, door, monkeypatch):
    """Its id is not on the bill, so the dropped line's unit stays with the bill."""
    from database.repositories.order_repository import OrderRepository

    oid = door.arm(_saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A"), _line(SVC)]))
    keep = next(ln for ln in _order(shop, oid)["items"] if ln["product_id"] == SVC)
    with monkeypatch.context() as m:
        m.setattr(OrderRepository, "update_if", lambda *a, **k: False)
        assert door.press(oid, [_as_sent(keep)]).status_code == 409
    assert _sold_to(shop, oid) == ["SU-FR-A"]
    assert len(_order(shop, oid)["items"]) == 2
    assert _op_states(shop, oid) == ["GIVEN_BACK"]
    _no_flag_left(shop, oid)


def test_what_cannot_finish_reaches_the_tasks_board(client, shop, door, tasks, monkeypatch):
    from database.repositories.product_repository import StockRepository

    oid, _lines = _unreadable_bill(client, shop, door, monkeypatch)

    def dead(self, *a, **k):
        raise TimeoutError("stock store gone")

    with monkeypatch.context() as m:
        m.setattr(StockRepository, "release_sold_units_for_order", dead)
        assert _sweep(shop)["tasks"] == 1
        assert _sweep(shop)["tasks"] == 1  # the same task, not a second
    rows = list(tasks.find({"source_ref": f"order_stock_unsettled:{oid}"}))
    assert len(rows) == 1 and rows[0]["status"] == "OPEN" and rows[0]["store_id"] == STORE
    assert _order(shop, oid)["order_number"] in rows[0]["title"]
    assert _sweep(shop)["settled"] == 1
    _no_flag_left(shop, oid)
    assert tasks.find_one({"source_ref": f"order_stock_unsettled:{oid}"})["status"] == "COMPLETED"


def test_the_remove_item_doors_flag_is_read_too(client, shop, tasks):
    """The flag nothing read: any bill carrying it reaches the board."""
    oid = _saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A")], status="DRAFT")
    shop["db"].get_collection("orders").update_one(
        {"order_id": oid}, {"$set": {FLAG: True, "line_remove_stock_failed_item_id": "L-1"}}
    )
    assert _sweep(shop)["tasks"] == 1
    assert "L-1" in tasks.find_one({"source_ref": f"order_stock_unsettled:{oid}"})["description"]


# ---------------------------------------------------------------------------
# 2 -- a claim that cannot be given back at once ends in the sweep
# ---------------------------------------------------------------------------


def test_a_lost_claim_that_will_not_go_back_is_swept(client, shop, door, monkeypatch):
    from database.repositories.product_repository import StockRepository

    oid = door.arm(_saved_bill(client, shop, [_line(SVC)]))
    keep = _as_sent(_order(shop, oid)["items"][0])
    real = StockRepository.mark_sold

    def wrote_then_lost(self, *a, **k):
        real(self, *a, **k)
        return False

    def dead(self, *a, **k):
        raise TimeoutError("stock store gone")

    with monkeypatch.context() as m:
        m.setattr(StockRepository, "mark_sold", wrote_then_lost)
        m.setattr(StockRepository, "release_sold_units_for_order", dead)
        assert door.press(oid, [keep, _line(FR, stock_id="SU-FR-A")]).status_code == 409
    assert _unit(shop, "SU-FR-A")["status"] == "SOLD"
    assert _op_states(shop, oid) == ["OPEN"]
    assert _sweep(shop)["given_back"] == 1
    assert _unit(shop, "SU-FR-A")["status"] == "AVAILABLE"
    r = door.press(oid, [keep, _line(FR, stock_id="SU-FR-A")])
    assert r.status_code == 200, r.text
    assert _sold_to(shop, oid) == ["SU-FR-A"]


def test_a_refused_press_never_frees_another_presses_claim(client, shop, door, monkeypatch):
    """Two presses, one bill: the loser's refusal must not free the winner's unit
    while the winner's save is still on its way."""
    from database.repositories.product_repository import StockRepository

    oid = door.arm(_saved_bill(client, shop, [_line(SVC)]))
    keep = _as_sent(_order(shop, oid)["items"][0])
    repo = StockRepository(shop["db"].get_collection("stock_units"))
    assert repo.mark_sold("SU-FR-A", oid, STORE, [FR], claim_op="the-other-press")
    r = door.press(oid, [keep, _line(FR, stock_id="SU-FR-A")])
    assert r.status_code == 409, r.text
    assert _unit(shop, "SU-FR-A")["status"] == "SOLD"
    assert _unit(shop, "SU-FR-A")["order_id"] == oid


def test_add_item_gives_back_a_lost_claim_too(client, shop, monkeypatch):
    from database.repositories.product_repository import StockRepository

    oid = _saved_bill(client, shop, [_line(SVC)], status="DRAFT")
    real = StockRepository.mark_sold

    def wrote_then_lost(self, *a, **k):
        real(self, *a, **k)
        return False

    with monkeypatch.context() as m:
        m.setattr(StockRepository, "mark_sold", wrote_then_lost)
        r = client.post(f"/api/v1/orders/{oid}/items", json=_line(FR, stock_id="SU-FR-A"),
                        headers=_token(STAFF))
    assert r.status_code == 409, r.text
    assert _unit(shop, "SU-FR-A")["status"] == "AVAILABLE"
    r = client.post(f"/api/v1/orders/{oid}/items", json=_line(FR, stock_id="SU-FR-A"),
                    headers=_token(STAFF))
    assert r.status_code == 200, r.text
    assert _sold_to(shop, oid) == ["SU-FR-A"]


# ---------------------------------------------------------------------------
# 3 -- a payment during an edit
# ---------------------------------------------------------------------------


def _pay_during_the_save(shop, monkeypatch, oid, amount):
    from database.repositories.order_repository import OrderRepository

    real = OrderRepository.update_if
    fired = []

    def paid_first(self, key, expected, data, *a, **k):
        if key == oid and "items" in data and not fired:
            fired.append(True)
            shop["db"].get_collection("orders").update_one(
                {"order_id": oid},
                {"$push": {"payments": {"method": "CASH", "amount": amount}},
                 "$inc": {"amount_paid": amount}},
            )
        return real(self, key, expected, data, *a, **k)

    monkeypatch.setattr(OrderRepository, "update_if", paid_first)
    return fired


def test_a_payment_during_an_edit_stops_a_false_receivable(client, shop, door, monkeypatch):
    oid = door.arm(_saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A"), _line(SVC)]))
    keep = next(ln for ln in _order(shop, oid)["items"] if ln["product_id"] == SVC)
    with monkeypatch.context() as m:
        fired = _pay_during_the_save(shop, m, oid, 200.0)
        r = door.press(oid, [_as_sent(keep)])
    assert fired and r.status_code == 409, r.text
    order = _order(shop, oid)
    assert len(order["items"]) == 2, "the edit read a different money picture: it did not land"
    assert order["amount_paid"] == 200.0
    assert _sold_to(shop, oid) == ["SU-FR-A"]
    r = door.press(oid, [_as_sent(keep)])
    assert r.status_code == 200, r.text
    order = _order(shop, oid)
    assert (order["grand_total"], order["amount_paid"], order["balance_due"]) == (200.0, 200.0, 0.0)
    assert order["payment_status"] == "PAID"
    assert _unit(shop, "SU-FR-A")["status"] == "AVAILABLE"


# ---------------------------------------------------------------------------
# 4 -- the frozen cost and the server's line fields
# ---------------------------------------------------------------------------


def test_a_price_correction_keeps_the_frozen_cost(client, shop, door):
    oid = door.arm(_saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A")]))
    stored = _order(shop, oid)["items"][0]
    assert stored["cost_at_sale"] == 2000.0
    r = door.press(oid, [_as_sent(stored, unit_price=4000.0)])
    assert r.status_code == 200, r.text
    line = _order(shop, oid)["items"][0]
    assert line["cost_at_sale"] == 2000.0, "erased the frozen cost"
    assert line["stock_id"] == "SU-FR-A"


def test_the_request_cannot_set_the_frozen_cost(client, shop, door):
    oid = door.arm(_saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A")]))
    stored = _order(shop, oid)["items"][0]
    assert door.press(oid, [_as_sent(stored, unit_price=4000.0, cost_at_sale=1.0)]).status_code == 200
    assert _order(shop, oid)["items"][0]["cost_at_sale"] == 2000.0


def test_an_id_only_line_keeps_sku_category_hsn_and_tax(client, shop, door):
    oid = door.arm(_saved_bill(client, shop, [_line(FR, stock_id="SU-FR-A", category="FRAME")]))
    orders = shop["db"].get_collection("orders")
    orders.update_one({"order_id": oid}, {"$set": {"items.0.hsn_code": "900311", "items.0.mrp": 5000.0}})
    stored = _order(shop, oid)["items"][0]
    r = door.press(oid, [{"item_id": stored["item_id"], "item_type": stored["item_type"],
                          "unit_price": stored["unit_price"]}])
    assert r.status_code == 200, r.text
    line = _order(shop, oid)["items"][0]
    for field in ("sku", "category", "hsn_code", "brand", "mrp", "product_name", "cost_at_sale"):
        assert line.get(field) == stored.get(field), field
    assert line["gst_rate"] == stored["gst_rate"]
    assert line["tax_amount"] == stored["tax_amount"]


def test_a_new_line_takes_its_cost_and_fields_from_the_master(client, shop, door):
    oid = door.arm(_saved_bill(client, shop, [_line(SVC)]))
    keep = _as_sent(_order(shop, oid)["items"][0])
    line = {"item_type": "FRAME", "product_id": FR, "product_name": "x", "unit_price": 5000.0,
            "stock_id": "SU-FR-A", "cost_at_sale": 1.0}
    r = door.press(oid, [keep, line])
    assert r.status_code == 200, r.text
    new = next(ln for ln in _order(shop, oid)["items"] if ln["product_id"] == FR)
    assert new["cost_at_sale"] == 2000.0, "the master's cost, never the request's"
    assert new["sku"] == "FR-CARRERA-CA1-54"
    assert new["category"] == "FRAME"
    assert new["brand"] == "Generic"


# ---------------------------------------------------------------------------
# 5 -- every reservation has one identity through its whole life
# ---------------------------------------------------------------------------


def _cut(oid, line, position=1):
    """The workshop's own hook, as its MOUNTED door calls it."""
    from api.services.lens_stock_hook import commit_for_workshop_dispatch

    return asyncio.run(commit_for_workshop_dispatch(
        order_item=line, order_id=oid, line_index=position, store_id=STORE,
        user={"user_id": "workshop", "roles": ["SUPERADMIN"], "store_ids": [STORE],
              "active_store_id": STORE},
    ))


def test_a_raised_cut_lens_cancels_back_to_the_cut_piece_only(client, shop, cells):
    oid, stored = _lens_bill(client, shop, cells)
    assert _cut(oid, stored)["status"] == "committed"
    assert cells.read() == (4, 0)
    lines = [_other_line(shop, oid), _lens_sent(stored, quantity=2)]
    assert _edit(client, oid, lines).status_code == 200
    assert cells.read() == (4, 1)
    _cancel(client, oid)
    assert cells.read() == (4, 0), "the uncut extra piece stayed held on a cancelled bill"
    client.post(f"/api/v1/orders/{oid}/cancel", params={"reason": "customer left"}, headers=_token(ADMIN))
    assert cells.read() == (4, 0), "a second cancel moves nothing"


def test_a_raise_on_a_line_with_no_reservation_still_goes_back(client, shop, cells):
    """The line's own reserve failed soft at sale: its extra piece has its own key."""
    oid, stored = _lens_bill(client, shop, cells)
    shop["db"].get_collection("lens_stock_audit").delete_many({"source_id": f"{oid}#{stored['item_id']}"})
    shop["db"].get_collection("lens_stock_lines").update_many({}, {"$set": {"reserved": 0}})
    assert _edit(client, oid, [_other_line(shop, oid), _lens_sent(stored, quantity=2)]).status_code == 200
    assert cells.read() == (5, 1)
    _cancel(client, oid)
    assert cells.read() == (5, 0)


def test_a_raised_lens_mounted_commits_every_piece_once(client, shop, cells):
    oid, stored = _lens_bill(client, shop, cells)
    assert _edit(client, oid, [_other_line(shop, oid), _lens_sent(stored, quantity=3)]).status_code == 200
    assert cells.read() == (5, 3)
    line = _lens_line(shop, oid)
    _cut(oid, line)
    assert cells.read() == (2, 0)
    _cut(oid, line)
    assert cells.read() == (2, 0)


def test_lowering_gives_back_the_uncut_extra_first(client, shop, cells):
    oid, stored = _lens_bill(client, shop, cells)
    _cut(oid, stored)
    assert _edit(client, oid, [_other_line(shop, oid), _lens_sent(stored, quantity=3)]).status_code == 200
    assert cells.read() == (4, 2)
    line = _lens_line(shop, oid)
    assert _edit(client, oid, [_other_line(shop, oid), _lens_sent(line, quantity=2)]).status_code == 200
    assert cells.read() == (4, 1)
    assert _lens_line(shop, oid)["lens_holds"][0]["qty"] == 1
    _cancel(client, oid)
    assert cells.read() == (4, 0)


def test_a_replacement_at_a_cut_position_is_its_own_lens(client, shop, cells):
    """The new line sits where the cut one sat. It is not cut: the workshop's
    commit reaches it, and a cancel before that gives it back."""
    oid, stored = _lens_bill(client, shop, cells)
    _cut(oid, stored)
    assert cells.read() == (4, 0)
    cells(sph=-2.0, on_hand=2)
    lines = [_other_line(shop, oid), _lens(power=-2.0)]
    assert _edit(client, oid, lines).status_code == 200
    assert cells.read(-2.0) == (2, 1)
    new = _lens_line(shop, oid)
    assert new["item_id"] != stored["item_id"] and new["lens_key_own"] is True
    _cancel(client, oid)
    assert cells.read(-2.0) == (2, 0)
    assert cells.read() == (4, 0), "the cut piece stays consumed"


def test_a_replacement_at_a_cut_position_commits_when_mounted(client, shop, cells):
    oid, stored = _lens_bill(client, shop, cells)
    _cut(oid, stored)
    cells(sph=-2.0, on_hand=2)
    assert _edit(client, oid, [_other_line(shop, oid), _lens(power=-2.0)]).status_code == 200
    assert _cut(oid, _lens_line(shop, oid))["status"] == "committed"
    assert cells.read(-2.0) == (1, 0)


def test_an_older_positional_commit_still_counts_for_an_older_line(client, shop, cells):
    """A line sold before this build, cut under its position: never released."""
    cells(on_hand=5)
    r = _sell(client, [_lens()], roles=ADMIN)
    assert r.status_code in (200, 201), r.text
    oid = _order_id(r)
    assert cells.read() == (5, 1)
    shop["db"].get_collection("lens_stock_audit").insert_one(
        {"source_id": f"{oid}#0#commit", "action": "commit"}
    )
    shop["db"].get_collection("lens_stock_lines").update_many({}, {"$set": {"on_hand": 4, "reserved": 0}})
    assert _cut(oid, _lens_line(shop, oid), position=0)["status"] == "already_committed"
    _cancel(client, oid)
    assert cells.read() == (4, 0)


def _shipped_bill(shop, invoiced, items):
    oid = invoiced(items)
    shop["db"].get_collection("orders").update_one({"order_id": oid}, {"$set": {"status": "SHIPPED"}})
    return oid


def _revise(client, oid, lines):
    return _edit(client, oid, lines, route="superadmin-invoice-change", mode="REVISED_INVOICE")


def test_a_lens_added_to_a_shipped_bill_is_out_at_once(client, shop, cells, invoiced):
    cells(on_hand=5)
    oid = _shipped_bill(shop, invoiced, [_line(SVC)])
    keep = _as_sent(_order(shop, oid)["items"][0])
    r = _revise(client, oid, [keep, _lens()])
    assert r.status_code == 200, r.text
    assert cells.read() == (4, 0), "a reservation nothing would ever commit"
    assert _revise(client, oid, [keep, _lens_sent(_lens_line(shop, oid))]).status_code == 200
    assert cells.read() == (4, 0)


def test_a_lost_reply_on_a_shipped_lens_still_commits_once(client, shop, cells, invoiced, monkeypatch):
    from database.repositories.order_repository import OrderRepository

    cells(on_hand=5)
    oid = _shipped_bill(shop, invoiced, [_line(SVC)])
    keep = _as_sent(_order(shop, oid)["items"][0])
    lines = [keep, _lens()]
    real = OrderRepository.update_if

    def lost(self, *a, **k):
        real(self, *a, **k)
        raise TimeoutError("reply lost")

    quiet = type(client)(client.app, raise_server_exceptions=False)
    with monkeypatch.context() as m:
        m.setattr(OrderRepository, "update_if", lost)
        r = quiet.put(f"/api/v1/orders/{oid}/superadmin-invoice-change",
                      json={"reason": "correcting this bill", "items": lines, "mode": "REVISED_INVOICE"},
                      headers=_token(ADMIN))
    assert r.status_code >= 500
    assert cells.read() == (4, 0)
    assert _revise(client, oid, lines).status_code == 200
    assert cells.read() == (4, 0)


def test_handover_commits_what_an_edit_added_past_the_workshop(client, shop, cells):
    oid, stored = _lens_bill(client, shop, cells)
    _cut(oid, stored)
    assert _edit(client, oid, [_other_line(shop, oid), _lens_sent(stored, quantity=2)]).status_code == 200
    assert cells.read() == (4, 1)
    shop["db"].get_collection("orders").update_one(
        {"order_id": oid},
        {"$set": {"status": "READY", "amount_paid": 2200.0, "balance_due": 0.0, "payment_status": "PAID"}},
    )
    r = client.post(f"/api/v1/orders/{oid}/deliver", headers=_token(ADMIN))
    assert r.status_code == 200, r.text
    assert cells.read() == (3, 0), "the extra piece left with the customer"


# ---------------------------------------------------------------------------
# 6 -- the prescription gate
# ---------------------------------------------------------------------------


def _no_rx(**over):
    line = _lens(**over)
    line.pop("prescription_id")
    return line


def test_the_same_rx_less_lens_is_refused_at_the_till_and_at_the_edit(client, shop, cells, door):
    cells(on_hand=5)
    assert _sell(client, [_no_rx()], roles=ADMIN).status_code == 422
    oid = door.arm(_saved_bill(client, shop, [_line(SVC)]))
    keep = _as_sent(_order(shop, oid)["items"][0])
    bill = _order(shop, oid)
    r = door.press(oid, [keep, _no_rx()])
    assert r.status_code == 422, r.text
    assert "prescription" in r.json()["detail"]
    assert cells.read() == (5, 0)
    assert _order(shop, oid) == bill


def test_an_rx_that_is_not_this_customers_is_refused(client, shop, cells, door):
    cells(on_hand=5)
    shop["db"].get_collection("prescriptions").insert_one(
        {"prescription_id": "RX-OTHER", "customer_id": "someone-else", "prescription_date": "2026-11-01"}
    )
    oid = door.arm(_saved_bill(client, shop, [_line(SVC)]))
    keep = _as_sent(_order(shop, oid)["items"][0])
    assert door.press(oid, [keep, _lens(prescription_id="RX-OTHER")]).status_code == 422
    assert cells.read() == (5, 0)


def test_an_admin_may_bill_on_an_expired_rx_as_at_the_till(client, shop, cells, door):
    cells(on_hand=5)
    shop["db"].get_collection("prescriptions").insert_one(
        {"prescription_id": "RX-OLD", "customer_id": "cust-psi", "prescription_date": "2019-01-01"}
    )
    oid = door.arm(_saved_bill(client, shop, [_line(SVC)]))
    keep = _as_sent(_order(shop, oid)["items"][0])
    r = door.press(oid, [keep, _lens(prescription_id="RX-OLD")])
    assert r.status_code == 200, r.text


def test_a_price_correction_of_an_old_rx_less_lens_still_saves(client, shop, cells, door):
    """Historical: the stored line predates the gate and the edit decides nothing
    clinical about it."""
    oid, stored = _lens_bill(client, shop, cells)
    door.arm(oid)
    shop["db"].get_collection("orders").update_one(
        {"order_id": oid, "items.item_id": stored["item_id"]},
        {"$set": {"items.$.prescription_id": None}},
    )
    stored = _lens_line(shop, oid)
    r = door.press(oid, [_other_line(shop, oid), _lens_sent(stored, unit_price=900.0)])
    assert r.status_code == 200, r.text


def test_a_changed_rx_on_a_stored_line_is_checked(client, shop, cells, door):
    oid, stored = _lens_bill(client, shop, cells)
    door.arm(oid)
    r = door.press(oid, [_other_line(shop, oid), _lens_sent(stored, prescription_id="RX-GHOST")])
    assert r.status_code == 422, r.text


def test_a_changed_power_keeps_its_rx_and_answers_to_the_gate(client, shop, cells):
    oid, stored = _lens_bill(client, shop, cells)
    cells(sph=-2.0, on_hand=2)
    r = _edit(client, oid, [_other_line(shop, oid), _lens_sent(stored, sph=-2.0)])
    assert r.status_code == 200, r.text
    assert _lens_line(shop, oid)["prescription_id"] == "RX-PSI-1"


# ---------------------------------------------------------------------------
# 7 -- QUIETLY STRIP (owner ruling 2026-11-07)
# ---------------------------------------------------------------------------


def test_the_till_drops_a_stock_id_from_a_service_line(client, shop):
    before = _unit(shop, "SU-FR-A")
    r = _sell(client, [_line(SVC, stock_id="SU-FR-A")])
    assert r.status_code == 201, r.text
    assert _order(shop, _order_id(r))["items"][0].get("stock_id") is None
    assert _unit(shop, "SU-FR-A") == before


def test_add_item_drops_a_stock_id_from_a_service_line(client, shop):
    oid = _saved_bill(client, shop, [_line(SVC)], status="DRAFT")
    before = _unit(shop, "SU-FR-A")
    r = client.post(f"/api/v1/orders/{oid}/items", json=_line(SVC, stock_id="SU-FR-A"),
                    headers=_token(STAFF))
    assert r.status_code == 200, r.text
    assert all(ln.get("stock_id") is None for ln in _order(shop, oid)["items"])
    assert _unit(shop, "SU-FR-A") == before


def test_a_virtual_made_to_order_line_drops_it_too(client, shop):
    r = _sell(client, [{"item_type": "FRAME", "product_id": "custom-1", "product_name": "Made to order",
                        "quantity": 1, "unit_price": 300.0, "stock_id": "SU-FR-A"}])
    assert r.status_code == 201, r.text
    assert _order(shop, _order_id(r))["items"][0].get("stock_id") is None


def test_a_scanned_lens_keeps_its_id_and_a_frame_keeps_its_unit(client, shop, cells):
    cells(on_hand=5)
    r = _sell(client, [_lens(stock_id="SCANNED-LENS"), _line(FR, stock_id="SU-FR-A")])
    assert r.status_code == 201, r.text
    lens, frame = _order(shop, _order_id(r))["items"]
    assert lens["stock_id"] == "SCANNED-LENS"
    assert frame["stock_id"] == "SU-FR-A"
    assert _unit(shop, "SU-FR-A")["status"] == "SOLD"


# ---------------------------------------------------------------------------
# 8 -- the piece that left with the parcel, and the give-back that failed
# ---------------------------------------------------------------------------


@pytest.fixture
def counter(shop, monkeypatch):
    """The real return door on this shop's repositories."""
    from api.routers import returns
    from database.repositories.order_repository import OrderRepository
    from database.repositories.product_repository import StockRepository

    db = shop["db"]
    monkeypatch.setattr(returns, "get_order_repository",
                        lambda: OrderRepository(db.get_collection("orders")))
    monkeypatch.setattr(returns, "get_stock_repository",
                        lambda: StockRepository(db.get_collection("stock_units")))

    def claim(order_id, line, qty, token=None, **_):
        """mongomock cannot run the door's positional $elemMatch-with-$or update,
        so this stands in for that one write, by the same rule. The real write
        was run on real Mongo over HTTP (the handover's return probe)."""
        orders = db.get_collection("orders")
        lines = orders.find_one({"order_id": order_id})["removed_lines"]
        at = next(i for i, ln in enumerate(lines) if ln["item_id"] == line["item_id"])
        if float(lines[at].get("returned_qty") or 0) + qty > float(lines[at]["quantity"]):
            return False
        orders.update_one({"order_id": order_id}, {"$inc": {f"removed_lines.{at}.returned_qty": qty}})
        return True

    monkeypatch.setattr(returns, "_claim_returnable_qty", claim)


def _return_piece(client, oid, item_id, qty=1, **over):
    return client.post(
        "/api/v1/returns",
        json={"order_id": oid, "return_type": "RETURN",
              "items": [{"order_item_id": item_id, "return_qty": qty, "unit_price": 0, **over}]},
        headers=_token(ADMIN),
    )


def _parcel_with_a_piece_removed(client, shop, invoiced):
    oid = _shipped_bill(shop, invoiced, [_line(FR, stock_id="SU-FR-A"), _line(SVC)])
    frame = next(ln for ln in _order(shop, oid)["items"] if ln["product_id"] == FR)
    keep = next(ln for ln in _order(shop, oid)["items"] if ln["product_id"] == SVC)
    assert _revise(client, oid, [_as_sent(keep)]).status_code == 200
    return oid, frame


def test_a_piece_off_a_parcel_keeps_its_lineage(client, shop, invoiced):
    oid, frame = _parcel_with_a_piece_removed(client, shop, invoiced)
    order = _order(shop, oid)
    assert _sold_to(shop, oid) == ["SU-FR-A"], "the parcel holds it"
    (gone,) = order["removed_lines"]
    assert gone["item_id"] == frame["item_id"] and gone["stock_ids"] == ["SU-FR-A"]
    assert gone["removed_from_invoice"] == "BV-INV-1" and gone["status_when_removed"] == "SHIPPED"
    _no_flag_left(shop, oid)


def test_the_returned_piece_comes_back_with_no_money(client, shop, invoiced, counter):
    oid, frame = _parcel_with_a_piece_removed(client, shop, invoiced)
    money = {k: _order(shop, oid).get(k) for k in ("grand_total", "amount_paid", "balance_due", "payments")}
    r = _return_piece(client, oid, frame["item_id"])
    assert r.status_code == 201, r.text
    assert r.json()["net_refund"] == 0.0 and r.json()["goods_only"] is True
    unit = _unit(shop, "SU-FR-A")
    assert (unit["status"], unit.get("order_id")) == ("AVAILABLE", None)
    order = _order(shop, oid)
    assert {k: order.get(k) for k in money} == money
    assert order["removed_lines"][0]["returned_qty"] == 1
    doc = shop["db"].get_collection("returns").find_one({"order_id": oid}, {"_id": 0})
    assert doc["net_refund"] == 0.0 and doc["restock_stock_ids"] == ["SU-FR-A"]
    assert shop["db"].get_collection("credit_note_ledger").count_documents({"order_id": oid}) == 0


def test_the_same_piece_cannot_come_back_twice(client, shop, invoiced, counter):
    oid, frame = _parcel_with_a_piece_removed(client, shop, invoiced)
    assert _return_piece(client, oid, frame["item_id"]).status_code == 201
    r = _return_piece(client, oid, frame["item_id"])
    assert r.status_code == 400, r.text
    assert shop["db"].get_collection("returns").count_documents({"order_id": oid}) == 1


def test_a_removed_piece_is_not_returned_with_billed_goods(client, shop, invoiced, counter):
    oid, frame = _parcel_with_a_piece_removed(client, shop, invoiced)
    keep = _order(shop, oid)["items"][0]
    r = client.post(
        "/api/v1/returns",
        json={"order_id": oid, "return_type": "RETURN",
              "items": [{"order_item_id": frame["item_id"], "return_qty": 1, "unit_price": 0},
                        {"order_item_id": keep["item_id"], "return_qty": 1, "unit_price": 200}]},
        headers=_token(ADMIN),
    )
    assert r.status_code == 400, r.text
    assert _sold_to(shop, oid) == ["SU-FR-A"]


def test_a_lowered_quantity_on_a_parcel_gets_its_own_lineage_id(client, shop, invoiced, counter):
    oid = _shipped_bill(shop, invoiced, [_line(FR, stock_id="SU-FR-A", quantity=2)])
    assert len(_sold_to(shop, oid)) == 2
    stored = _order(shop, oid)["items"][0]
    assert _revise(client, oid, [_as_sent(stored, quantity=1)]).status_code == 200
    (gone,) = _order(shop, oid)["removed_lines"]
    assert gone["item_id"] != stored["item_id"] and gone["quantity"] == 1
    assert gone["stock_ids"] == ["SU-FR-B"], "the piece the line no longer bills, by name"
    assert _return_piece(client, oid, gone["item_id"]).status_code == 201
    assert _sold_to(shop, oid) == ["SU-FR-A"]


def test_a_failed_give_back_of_an_unsaved_edit_is_recoverable(client, shop, cells, door, tasks, monkeypatch):
    """The edit never saved and its give-back failed too: nothing strands silently."""
    from api.routers import lens_stock as stock_router
    from database.repositories.order_repository import OrderRepository
    from database.repositories.product_repository import StockRepository

    cells(on_hand=5)
    oid = door.arm(_saved_bill(client, shop, [_line(SVC)]))
    keep = _as_sent(_order(shop, oid)["items"][0])

    def dead_units(self, *a, **k):
        raise TimeoutError("stock store gone")

    async def dead_cells(*a, **k):
        raise TimeoutError("lens store gone")

    with monkeypatch.context() as m:
        m.setattr(OrderRepository, "update_if", lambda *a, **k: False)
        m.setattr(StockRepository, "release_sold_units_for_order", dead_units)
        m.setattr(stock_router, "release_cell", dead_cells)
        r = door.press(oid, [keep, _lens(), _line(FR, stock_id="SU-FR-A")])
        assert r.status_code == 409, r.text
        assert _op_states(shop, oid) == ["OPEN"], "the record of what it still holds"
        assert (_unit(shop, "SU-FR-A")["status"], cells.read()) == ("SOLD", (5, 1))
        assert _sweep(shop)["tasks"] == 1
    assert _order(shop, oid)[FLAG] is True
    assert tasks.count_documents({"source_ref": f"order_stock_unsettled:{oid}"}) == 1
    assert _sweep(shop)["given_back"] == 1
    assert (_unit(shop, "SU-FR-A")["status"], cells.read()) == ("AVAILABLE", (5, 0))
    _no_flag_left(shop, oid)
    assert tasks.find_one({"source_ref": f"order_stock_unsettled:{oid}"})["status"] == "COMPLETED"


# ---------------------------------------------------------------------------
# R -- REUSE THE FIRST NUMBER (owner ruling 2026-11-07)
# ---------------------------------------------------------------------------


@pytest.fixture
def serials(shop, monkeypatch):
    from database.repositories.order_repository import OrderRepository

    minted = []

    def mint(self, *a, **k):
        minted.append(f"BV-INV-REV-{len(minted) + 1}")
        return minted[-1]

    monkeypatch.setattr(OrderRepository, "next_invoice_number", mint)
    return minted


def _revisions(shop, oid):
    return list(shop["db"].get_collection("revised_invoices").find({"order_id": oid}, {"_id": 0}))


def _revised_bill(client, shop, invoiced, serials):
    oid = invoiced([_line(FR, stock_id="SU-FR-A"), _line(SVC)])
    keep = next(ln for ln in _order(shop, oid)["items"] if ln["product_id"] == SVC)
    return oid, [_as_sent(keep)]


def test_an_identical_retry_after_a_lost_reply_is_the_first_revision(
    client, shop, invoiced, serials, monkeypatch
):
    from database.repositories.order_repository import OrderRepository

    oid, lines = _revised_bill(client, shop, invoiced, serials)
    real = OrderRepository.update_if

    def lost(self, key, expected, data, *a, **k):
        answer = real(self, key, expected, data, *a, **k)
        if "items" in data:
            raise TimeoutError("reply lost")
        return answer

    quiet = type(client)(client.app, raise_server_exceptions=False)
    body = {"reason": "correcting this bill", "items": lines, "mode": "REVISED_INVOICE"}
    with monkeypatch.context() as m:
        m.setattr(OrderRepository, "update_if", lost)
        r = quiet.put(f"/api/v1/orders/{oid}/superadmin-invoice-change", json=body, headers=_token(ADMIN))
    assert r.status_code >= 500
    first = _order(shop, oid)
    r = client.put(f"/api/v1/orders/{oid}/superadmin-invoice-change", json=body, headers=_token(ADMIN))
    assert r.status_code == 200, r.text
    answer = r.json()
    assert answer["revised_invoice_number"] == "BV-INV-REV-1"
    assert answer["original_invoice_number"] == "BV-INV-1"
    assert answer["revision_id"] == first["invoice_revision_id"]
    assert serials == ["BV-INV-REV-1"], "no spare serial"
    assert len(_revisions(shop, oid)) == 1
    assert _order(shop, oid)["invoice_number"] == "BV-INV-REV-1"
    assert _unit(shop, "SU-FR-A")["status"] == "AVAILABLE"


def test_a_retry_with_the_reason_retyped_is_still_the_first_revision(
    client, shop, invoiced, serials, monkeypatch
):
    """Codex's round-4 probe retries with a new reason text: the bill it asks for
    is the same, so the number is too (owner ruling 2026-11-07)."""
    from database.repositories.order_repository import OrderRepository

    oid, lines = _revised_bill(client, shop, invoiced, serials)
    real = OrderRepository.update_if

    def lost(self, key, expected, data, *a, **k):
        answer = real(self, key, expected, data, *a, **k)
        if "items" in data:
            raise TimeoutError("reply lost")
        return answer

    quiet = type(client)(client.app, raise_server_exceptions=False)
    body = {"reason": "first try at this", "items": lines, "mode": "REVISED_INVOICE"}
    with monkeypatch.context() as m:
        m.setattr(OrderRepository, "update_if", lost)
        r = quiet.put(f"/api/v1/orders/{oid}/superadmin-invoice-change", json=body, headers=_token(ADMIN))
    assert r.status_code >= 500
    r = client.put(
        f"/api/v1/orders/{oid}/superadmin-invoice-change",
        json={**body, "reason": "retyped after the error"}, headers=_token(ADMIN),
    )
    assert r.status_code == 200, r.text
    assert r.json()["revised_invoice_number"] == "BV-INV-REV-1"
    assert serials == ["BV-INV-REV-1"], "no spare serial"
    assert len(_revisions(shop, oid)) == 1


def test_a_save_that_never_landed_keeps_its_number_for_the_retry(
    client, shop, invoiced, serials, monkeypatch
):
    from database.repositories.order_repository import OrderRepository

    oid, lines = _revised_bill(client, shop, invoiced, serials)

    def failed(self, *a, **k):
        raise TimeoutError("write failed")

    quiet = type(client)(client.app, raise_server_exceptions=False)
    body = {"reason": "correcting this bill", "items": lines, "mode": "REVISED_INVOICE"}
    with monkeypatch.context() as m:
        m.setattr(OrderRepository, "update_if", failed)
        r = quiet.put(f"/api/v1/orders/{oid}/superadmin-invoice-change", json=body, headers=_token(ADMIN))
    assert r.status_code >= 500
    assert _order(shop, oid)["invoice_number"] == "BV-INV-1"
    r = client.put(f"/api/v1/orders/{oid}/superadmin-invoice-change", json=body, headers=_token(ADMIN))
    assert r.status_code == 200, r.text
    assert serials == ["BV-INV-REV-1"]
    assert _order(shop, oid)["invoice_number"] == "BV-INV-REV-1"
    assert len(_revisions(shop, oid)) == 1


def test_a_different_edit_gets_a_new_number(client, shop, invoiced, serials):
    oid, lines = _revised_bill(client, shop, invoiced, serials)
    assert _revise(client, oid, lines).status_code == 200
    r = _revise(client, oid, [{**lines[0], "unit_price": 150.0}])
    assert r.status_code == 200, r.text
    assert r.json()["original_invoice_number"] == "BV-INV-REV-1"
    assert r.json()["revised_invoice_number"] == "BV-INV-REV-2"
    assert len(_revisions(shop, oid)) == 2


def test_the_first_edit_again_after_another_is_a_new_revision(client, shop, invoiced, serials):
    oid, lines = _revised_bill(client, shop, invoiced, serials)
    assert _revise(client, oid, lines).status_code == 200
    other = [{**lines[0], "unit_price": 150.0}]
    assert _revise(client, oid, other).status_code == 200
    r = _revise(client, oid, lines)
    assert r.status_code == 200, r.text
    assert r.json()["revised_invoice_number"] == "BV-INV-REV-3"


def test_a_refused_revision_is_recorded_unused_and_not_reused(client, shop, invoiced, serials, monkeypatch):
    oid, lines = _revised_bill(client, shop, invoiced, serials)
    with monkeypatch.context() as m:
        _pay_during_the_save(shop, m, oid, 200.0)
        r = _revise(client, oid, lines)
    assert r.status_code == 409, r.text
    assert [row["status"] for row in _revisions(shop, oid)] == ["REFUSED_SERIAL_UNUSED"]
    assert _revise(client, oid, lines).status_code == 200
    assert _order(shop, oid)["invoice_number"] == "BV-INV-REV-2"


# ---------------------------------------------------------------------------
# 9 -- Codex round 5 (12 Nov): the serial rule on a removed piece, the
# customer change, and an unreadable revision link. (The removed piece's
# durable record runs on real Mongo: test_removed_goods_return_native.py.)
# ---------------------------------------------------------------------------


def test_a_wrong_serial_on_a_removed_piece_is_refused_like_a_billed_one(client, shop, invoiced, counter):
    """P2b: the removed-goods door entered before the serial guard, so a wrong
    scan freed the original unit (201). The guard now runs before EITHER door."""
    shop["db"].get_collection("stock_units").update_one({"stock_id": "SU-FR-A"}, {"$set": {"serial": "SN-A"}})
    oid, frame = _parcel_with_a_piece_removed(client, shop, invoiced)
    r = _return_piece(client, oid, frame["item_id"], serial="WRONG-PIECE")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["reason"] == "SERIAL_MISMATCH"
    unit = _unit(shop, "SU-FR-A")
    assert (unit["status"], unit.get("order_id")) == ("SOLD", oid)
    assert not _order(shop, oid)["removed_lines"][0].get("returned_qty")
    assert shop["db"].get_collection("returns").count_documents({"order_id": oid}) == 0
    r = _return_piece(client, oid, frame["item_id"], serial="sn-a")
    assert r.status_code == 201, r.text
    assert _unit(shop, "SU-FR-A")["status"] == "AVAILABLE"


@pytest.fixture
def other_customer(shop):
    shop["db"].get_collection("customers").insert_one(
        {"customer_id": "cust-other", "name": "Ravi", "mobile": "9100000072", "phone": "9100000072"})
    return "cust-other"


def test_moving_a_bill_to_another_customer_rechecks_its_prescription(client, shop, cells, door, other_customer):
    """P2c: an unchanged Rx line stayed on the bill when its customer changed (200).
    The till's ownership rule now runs on every Rx line when the customer moves."""
    oid, stored = _lens_bill(client, shop, cells)
    door.arm(oid)
    bill = _order(shop, oid)
    lines = [_other_line(shop, oid), _lens_sent(stored)]
    r = _edit(client, oid, lines, route=door.name, customer_id=other_customer, **DOORS[door.name])
    assert r.status_code == 422, r.text
    assert "does not belong" in r.json()["detail"]
    assert _order(shop, oid) == bill
    assert cells.read() == (5, 1)


def test_a_customer_correction_with_that_customers_rx_saves(client, shop, cells, door, other_customer):
    shop["db"].get_collection("prescriptions").insert_one(
        {"prescription_id": "RX-OTHER-1", "customer_id": other_customer, "prescription_date": "2026-11-01"})
    oid, stored = _lens_bill(client, shop, cells)
    door.arm(oid)
    lines = [_other_line(shop, oid), _lens_sent(stored, prescription_id="RX-OTHER-1")]
    r = _edit(client, oid, lines, route=door.name, customer_id=other_customer, **DOORS[door.name])
    assert r.status_code == 200, r.text
    saved = _order(shop, oid)
    assert saved["customer_id"] == other_customer
    assert _lens_line(shop, oid)["prescription_id"] == "RX-OTHER-1"


def test_a_price_only_correction_of_an_old_rx_line_needs_no_new_check(client, shop, cells, door):
    """Same customer, nothing clinical changed: a historical Rx line whose Rx is
    no longer on file still takes a price correction."""
    oid, stored = _lens_bill(client, shop, cells)
    door.arm(oid)
    shop["db"].get_collection("prescriptions").delete_many({"prescription_id": "RX-PSI-1"})
    r = _edit(client, oid, [_other_line(shop, oid), _lens_sent(stored, unit_price=900.0)],
              route=door.name, customer_id="cust-psi", **DOORS[door.name])
    assert r.status_code == 200, r.text


def test_an_unreadable_revision_link_takes_no_second_number(client, shop, invoiced, serials, monkeypatch):
    """P2d: a failed read of the saved link was read as 'no revision', so the
    retry minted a second number. It refuses (503) and takes nothing; the next
    retry reuses the first number (owner ruling 2026-11-07)."""
    import mongomock
    from database.repositories.order_repository import OrderRepository

    oid, lines = _revised_bill(client, shop, invoiced, serials)

    def failed(self, *a, **k):
        raise TimeoutError("write failed")

    quiet = type(client)(client.app, raise_server_exceptions=False)
    body = {"reason": "correcting this bill", "items": lines, "mode": "REVISED_INVOICE"}
    url = f"/api/v1/orders/{oid}/superadmin-invoice-change"
    with monkeypatch.context() as m:
        m.setattr(OrderRepository, "update_if", failed)
        assert quiet.put(url, json=body, headers=_token(ADMIN)).status_code >= 500
    assert serials == ["BV-INV-REV-1"] and len(_revisions(shop, oid)) == 1
    real_find_one = mongomock.collection.Collection.find_one

    def unreadable(self, *a, **k):
        if self.name == "revised_invoices":
            raise TimeoutError("link row unreadable")
        return real_find_one(self, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(mongomock.collection.Collection, "find_one", unreadable)
        r = quiet.put(url, json={**body, "reason": "retyped"}, headers=_token(ADMIN))
    assert r.status_code == 503, r.text
    assert serials == ["BV-INV-REV-1"], "no second number"
    assert _order(shop, oid)["invoice_number"] == "BV-INV-1"
    assert [row["revised_invoice_number"] for row in _revisions(shop, oid)] == ["BV-INV-REV-1"]
    assert _unit(shop, "SU-FR-A")["status"] == "SOLD", "the refused press moved nothing"
    r = client.put(url, json=body, headers=_token(ADMIN))
    assert r.status_code == 200, r.text
    assert r.json()["revised_invoice_number"] == "BV-INV-REV-1"
    assert serials == ["BV-INV-REV-1"] and len(_revisions(shop, oid)) == 1
    assert _order(shop, oid)["invoice_number"] == "BV-INV-REV-1"
    assert _unit(shop, "SU-FR-A")["status"] == "AVAILABLE"
