"""
IMS 2.0 - Codex audit batch b07 (returns): L2-RETURN-F01, RUN-03, L2-RETURN-F03
===============================================================================
L2-RETURN-F01  the Returns screen sends a line with no product; the GOOD piece
               was credited and left SOLD, and the return said "stock applied".
RUN-03         the quote priced a quantity the create then refused.
L2-RETURN-F03  Needs completing told staff to put pieces back by hand even
               when the kept record says they are already back. A piece still
               out is never added by hand (the restock retry then adds it a
               second time): the panel's Retry stock step puts it back once.
(L2-STOCK-F03, the barcode trace, is tested beside the till's own trace tests
in test_till_records_scanned_unit.py.)

The return door runs on the fakes of test_returns_restock (their order
collection runs the real returnable-quantity claim); the recovery door on the
StrictDB of the money-door suites. No emoji (cp1252).
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from tests.test_pos_money_doors import STORE, till  # noqa: E402,F401
from tests.test_pos_money_doors_r9 import _ago  # noqa: E402
from tests.test_pos_money_doors_r12 import _row, _spec, tasks  # noqa: E402,F401
from tests.test_pos_money_doors_r13 import _bill_with_hold, _complete  # noqa: E402
from tests.test_returns_restock import _hdr, _payload, ctx  # noqa: E402,F401

pytestmark = pytest.mark.usefixtures("gst_numbers")


# What ReturnsPage.buildPayload sends for one line: the bill line's id, never
# a product_id (the screen's line model holds none).
SCREEN_LINE = {
    "order_item_id": "li1",
    "product_name": "Ray-Ban Aviator",
    "sku": "RB-1",
    "return_qty": 1,
    "unit_price": 500,
    "gst_rate": 0,
    "reason": "CUSTOMER_CHANGED_MIND",
    "condition": "GOOD",
    "notes": "",
}


def _screen(return_type="RETURN", **line):
    return _payload(return_type=return_type, customer_id="CUST-1", items=[{**SCREEN_LINE, **line}])


def _post(ctx, body, roles=("SALES_STAFF",), key=None, path=""):  # noqa: F811
    return ctx["client"].post(f"/api/v1/returns{path}", json=body, headers=_hdr(list(roles), key=key))


def _units(ctx):  # noqa: F811
    return sorted((u["stock_id"], u["product_id"], u["status"]) for u in ctx["stock_repo"].units)


def _line(ctx, item_id="li1"):  # noqa: F811
    return next(i for i in ctx["order_repo"]._order["items"] if i["item_id"] == item_id)


# =========================================================================== #
# L2-RETURN-F01: the screen's line puts the GOOD piece back in stock
# =========================================================================== #
@pytest.mark.parametrize("return_type", ["RETURN", "CREDIT_NOTE"])
def test_the_returns_screen_line_puts_the_good_piece_back_in_stock(ctx, return_type):  # noqa: F811
    r = _post(ctx, _screen(return_type))
    assert r.status_code == 201, r.text
    data = r.json()
    # The original unit, sellable again -- and nothing invented beside it.
    assert _units(ctx) == [("STK-OLD-1", "PRD-1", "AVAILABLE")]
    assert data["restock_applied"] is True and data["restock_stock_ids"] == ["STK-OLD-1"]
    saved = ctx["returns_coll"].docs[-1]
    assert [(i["order_item_id"], i["product_id"]) for i in saved["items"]] == [("li1", "PRD-1")]
    assert saved["restock_applied"] is True and saved["restock_stock_ids"] == ["STK-OLD-1"]
    assert [(row["product_id"], row["reactivated"], row["minted"]) for row in saved["restocked"]] == [
        ("PRD-1", 1, 0)]
    # One unit transition, on the stock log, for this return.
    assert [(a["stock_id"], a["prior_status"], a["new_status"], a["return_id"])
            for a in ctx["stock_audit_coll"].docs] == [("STK-OLD-1", "SOLD", "AVAILABLE", data["return_id"])]
    # One return booked on the bill line; one credit / refund, not two.
    assert _line(ctx)["returned_qty"] == 1 and len(ctx["returns_coll"].docs) == 1
    if return_type == "CREDIT_NOTE":
        assert data["credit_amount"] == 500.0 and len(ctx["ledger_coll"].docs) == 1
    else:
        assert data["refund_amount"] == 500.0 and ctx["ledger_coll"].docs == []


def test_a_damaged_piece_from_the_screen_is_set_aside_not_left_sold(ctx, monkeypatch):  # noqa: F811
    import agents.registry as registry
    from api.services import item_events as ie

    async def _quiet(event, payload, source=""):
        return None

    monkeypatch.setattr(registry, "dispatch_event", _quiet)
    monkeypatch.setattr(ie, "record_post_write_event", lambda db, **kw: None)
    r = _post(ctx, _screen(condition="DAMAGED"), roles=("ADMIN",))
    assert r.status_code == 201, r.text
    assert _units(ctx) == [("STK-OLD-1", "PRD-1", "QUARANTINED")]
    saved = ctx["returns_coll"].docs[-1]
    assert saved["quarantined_stock_ids"] == ["STK-OLD-1"] and saved["restock_stock_ids"] == []
    assert saved["items"][0]["product_id"] == "PRD-1"


def test_an_opened_piece_is_set_aside_for_the_manager_not_left_reading_sold(ctx, monkeypatch):  # noqa: F811
    """Decision D6 (2026-11-11): an OPENED piece is in the shop, so stock
    shows it -- QUARANTINED (off sale) for the manager to decide, with the
    same follow-up task as a damaged piece. It used to stay SOLD."""
    import agents.registry as registry
    from api.services import item_events as ie

    heard = []

    async def _hear(event, payload, source=""):
        heard.append((event, payload["stock_id"], payload["reason"]))

    monkeypatch.setattr(registry, "dispatch_event", _hear)
    monkeypatch.setattr(ie, "record_post_write_event", lambda db, **kw: None)
    r = _post(ctx, _screen(condition="OPENED"), roles=("ADMIN",))
    assert r.status_code == 201, r.text
    assert _units(ctx) == [("STK-OLD-1", "PRD-1", "QUARANTINED")]
    unit = ctx["stock_repo"].units[0]
    assert unit["quarantine_reason"] == "CUSTOMER_RETURN_OPENED"
    assert unit["quarantine_notes"] == f"Opened, customer return {r.json()['return_id']}"
    saved = ctx["returns_coll"].docs[-1]
    # Set aside, never put back on sale.
    assert saved["quarantined_stock_ids"] == ["STK-OLD-1"] and saved["restock_stock_ids"] == []
    assert saved["restocked"] == []
    assert heard == [("stock.quarantined", "STK-OLD-1", "CUSTOMER_RETURN_OPENED")]
    assert [(a["prior_status"], a["new_status"]) for a in ctx["stock_audit_coll"].docs] == [("SOLD", "QUARANTINED")]


def test_a_good_piece_marked_not_for_restock_stays_out_as_the_return_said(ctx):  # noqa: F811
    """The till's own switch (restock False on a GOOD piece) is unchanged."""
    r = _post(ctx, _screen(restock=False))
    assert r.status_code == 201, r.text
    assert _units(ctx) == [("STK-OLD-1", "PRD-1", "SOLD")]
    assert ctx["returns_coll"].docs[-1]["quarantined_stock_ids"] == []


def test_a_piece_whose_own_sold_record_is_not_found_gets_a_new_record_never_another_bills(ctx):  # noqa: F811
    """Decision D5 (2026-11-11): the bill's own sold record is not there (old
    or untidy data), and another customer's sold piece of the same product is
    at the shop. The return used to flip THAT customer's record back on sale.
    It now leaves it alone and gives the returned piece a NEW record and
    barcode, and says so (the label has to be printed)."""
    ctx["stock_repo"].units[0]["order_id"] = "ORD-OTHER-CUSTOMER"
    r = _post(ctx, _screen(), key="K-NEW")
    assert r.status_code == 201, r.text
    data = r.json()
    # A lost reply pressed again: the saved return, the same note, no third unit.
    again = _post(ctx, _screen(), key="K-NEW")
    assert again.json()["_idempotent_replay"] is True and again.json()["stock_note"] == data["stock_note"]
    units = {u["stock_id"]: u for u in ctx["stock_repo"].units}
    assert units["STK-OLD-1"]["status"] == "SOLD" and units["STK-OLD-1"]["order_id"] == "ORD-OTHER-CUSTOMER"
    assert len(units) == 2
    new = next(u for sid, u in units.items() if sid != "STK-OLD-1")
    assert (new["product_id"], new["status"], new["source_type"], new["source_id"]) == (
        "PRD-1", "AVAILABLE", "RETURN", data["return_id"])
    assert new["returned_from_order_id"] == "ORD-1" and new["barcode"] and new["barcode_printed"] is False
    saved = ctx["returns_coll"].docs[-1]
    assert [(row["reactivated"], row["minted"]) for row in saved["restocked"]] == [(0, 1)]
    assert saved["restock_applied"] is True and saved["restock_stock_ids"] == [new["stock_id"]]
    assert data["stock_note"].startswith("1 piece(s) went back into stock under a NEW barcode")
    assert "print the new label" in data["stock_note"]


def test_a_piece_put_back_on_its_own_record_asks_for_no_new_label(ctx):  # noqa: F811
    r = _post(ctx, _screen())
    assert r.status_code == 201 and r.json()["stock_note"] == "", r.text


def test_a_product_the_client_names_beside_the_line_never_decides_the_stock(ctx):  # noqa: F811
    """The bill line is PRD-1. A request naming PRD-9 beside that line used to
    mint a sellable PRD-9 piece nobody returned."""
    r = _post(ctx, _screen(product_id="PRD-9"))
    assert r.status_code == 201, r.text
    assert _units(ctx) == [("STK-OLD-1", "PRD-1", "AVAILABLE")]
    assert ctx["returns_coll"].docs[-1]["items"][0]["product_id"] == "PRD-1"


def test_a_line_with_no_product_on_the_bill_restocks_nothing(ctx):  # noqa: F811
    """A fee / made-to-order line is not tracked stock: nothing is minted for
    it, whatever product the request names."""
    ctx["order_repo"]._order["items"].append(
        {"item_id": "li-fee", "product_name": "Fitting", "quantity": 1, "returned_qty": 0})
    r = _post(ctx, _screen(order_item_id="li-fee", product_id="PRD-9", product_name="Fitting", sku=""))
    assert r.status_code == 201, r.text
    assert _units(ctx) == [("STK-OLD-1", "PRD-1", "SOLD")]
    saved = ctx["returns_coll"].docs[-1]
    assert saved["items"][0]["product_id"] is None and saved["restock_stock_ids"] == []


# The till's own shapes for a bill line that takes NO stock unit at the sale
# (the sale's one rule, restock_engine.takes_serialized_stock): a manual Rx
# lens ("lens-<stamp>", LensDetailsModal), a catalogue lens (its stock is the
# lens grid's), a service, an eye test, a custom item.
NOT_STOCK_LINES = [("LENS", "lens-1731300000000"), ("LENS", "PRD-LENS-1"), ("SERVICE", "PRD-FIT"),
                   ("EYE_TEST", "PRD-EXAM"), ("ACCESSORY", "custom-55")]


@pytest.mark.parametrize("condition", ["GOOD", "DAMAGED"])
@pytest.mark.parametrize("item_type,product", NOT_STOCK_LINES)
def test_a_line_that_took_no_stock_unit_at_the_sale_puts_none_back(ctx, item_type, product, condition):  # noqa: F811
    """Such a line carries a product id on the bill, but no stock row left
    for it: its return used to MINT a sellable unit per piece."""
    ctx["order_repo"]._order["items"].append({
        "item_id": "li-x", "item_type": item_type, "product_id": product, "product_name": "Rx lens",
        "quantity": 2, "returned_qty": 0})
    # The screen's payload, then a client that names the product itself.
    for extra in ({}, {"product_id": product}):
        r = _post(ctx, _screen(order_item_id="li-x", condition=condition, product_name="Rx lens", sku="",
                               **extra), roles=("ADMIN",))
        assert r.status_code == 201, r.text
        # Not one stock row invented, moved or set aside.
        assert _units(ctx) == [("STK-OLD-1", "PRD-1", "SOLD")]
        saved = ctx["returns_coll"].docs[-1]
        assert (saved["items"][0]["order_item_id"], saved["items"][0]["product_id"]) == ("li-x", None)
        assert saved["restocked"] == [] and saved["restock_stock_ids"] == []
        assert saved["quarantined_stock_ids"] == []
    # No unit transition on the stock log; the return itself stands.
    assert ctx["stock_audit_coll"].docs == []
    assert _line(ctx, "li-x")["returned_qty"] == 2 and len(ctx["returns_coll"].docs) == 2


def test_a_legacy_line_with_no_id_that_took_no_stock_keeps_its_product_and_restocks_nothing(ctx):  # noqa: F811
    """A bill line with no id is matched BY its product, so the saved row
    keeps it (the returnable-quantity book reads it) -- and moves no stock."""
    ctx["order_repo"]._order["items"] = [
        {"item_type": "LENS", "product_id": "PRD-LENS-1", "product_name": "Stock lens", "quantity": 1}]
    body = _screen(order_item_id=None, product_id="PRD-LENS-1", product_name="Stock lens", sku="")
    r = _post(ctx, body)
    assert r.status_code == 201, r.text
    assert _units(ctx) == [("STK-OLD-1", "PRD-1", "SOLD")]
    saved = ctx["returns_coll"].docs[-1]
    assert saved["items"][0]["product_id"] == "PRD-LENS-1" and saved["items"][0]["restock"] is False
    assert saved["restock_stock_ids"] == []
    again = _post(ctx, body)
    assert again.status_code == 400 and "returnable quantity 0" in again.text, again.text


def test_the_restock_plan_never_plans_a_unit_for_a_virtual_till_item():
    """Every door plans through restock_engine (the create, the restock retry
    of a record saved earlier, the Shopify refund): a virtual till id is
    never stock, whatever line carries it."""
    from api.services import restock_engine

    plan = restock_engine.plan_restock([
        {"product_id": pid, "condition": "GOOD", "return_qty": 2}
        for pid in ("lens-1731300000000", "lens-sug-4", "custom-55")])
    assert plan["unit_count"] == 0 and plan["units"] == []
    assert {row["reason"] for row in plan["skipped"]} == {"NOT_STOCK"}
    # The sale and the return ask ONE rule which lines take a stock unit.
    from api.routers.orders import _takes_serialized_stock

    assert _takes_serialized_stock is restock_engine.takes_serialized_stock
    for item_type, product in NOT_STOCK_LINES:
        line = {"item_type": item_type, "product_id": product}
        assert restock_engine.line_stock_product_id(line) is None, line
        assert restock_engine.line_product_id(line) == product  # still the line's identity
    assert restock_engine.line_stock_product_id({"item_type": "FRAME", "product_id": "PRD-1"}) == "PRD-1"
    # An online line: Shopify's product id beside the IMS one the stock rows carry.
    assert restock_engine.line_stock_product_id(
        {"item_type": "SUNGLASS", "product_id": None, "ims_product_id": "PRD-1"}) == "PRD-1"


def test_two_pieces_come_back_one_by_one_each_once_and_a_retry_moves_nothing(ctx):  # noqa: F811
    ctx["stock_repo"].units.append({"stock_id": "STK-OLD-2", "product_id": "PRD-1", "store_id": "BV-PUN-01",
                                    "status": "SOLD", "order_id": "ORD-1"})
    _line(ctx)["quantity"] = 2
    first = _post(ctx, _screen(), key="K-1")
    assert first.status_code == 201, first.text
    assert sorted(s for _, _, s in _units(ctx)) == ["AVAILABLE", "SOLD"]
    # The same press again (a lost reply): the saved return, nothing moved.
    again = _post(ctx, _screen(), key="K-1")
    assert again.status_code == 201 and again.json()["_idempotent_replay"] is True, again.text
    assert sorted(s for _, _, s in _units(ctx)) == ["AVAILABLE", "SOLD"]
    second = _post(ctx, _screen(), key="K-2")
    assert second.status_code == 201, second.text
    assert _units(ctx) == [("STK-OLD-1", "PRD-1", "AVAILABLE"), ("STK-OLD-2", "PRD-1", "AVAILABLE")]
    assert sorted(first.json()["restock_stock_ids"] + second.json()["restock_stock_ids"]) == [
        "STK-OLD-1", "STK-OLD-2"]
    # Both pieces are back: a third is refused, and nothing is invented.
    third = _post(ctx, _screen(), key="K-3")
    assert third.status_code == 400 and "returnable quantity 0" in third.text, third.text
    assert len(ctx["stock_repo"].units) == 2 and _line(ctx)["returned_qty"] == 2
    assert len(ctx["returns_coll"].docs) == 2


def test_an_online_line_returned_by_its_shop_product_restocks_the_ims_one_and_replays(ctx):  # noqa: F811
    """An online bill line keeps Shopify's product id beside the IMS one; the
    stock rows carry the IMS one. The press is fingerprinted as sent, so its
    retry is still the same press after the line takes the sale's identity."""
    ctx["order_repo"]._order["items"] = [
        {"item_id": "li-web", "product_id": "SHOPIFY-777", "ims_product_id": "PRD-1", "quantity": 1,
         "returned_qty": 0}]
    body = _screen(order_item_id=None, product_id="SHOPIFY-777")
    first = _post(ctx, body, key="K-WEB")
    assert first.status_code == 201, first.text
    assert _units(ctx) == [("STK-OLD-1", "PRD-1", "AVAILABLE")]
    saved_line = ctx["returns_coll"].docs[-1]["items"][0]
    assert (saved_line["order_item_id"], saved_line["product_id"]) == ("li-web", "PRD-1")
    again = _post(ctx, body, key="K-WEB")
    assert again.status_code == 201 and again.json()["_idempotent_replay"] is True, again.text
    assert len(ctx["returns_coll"].docs) == 1


# =========================================================================== #
# RUN-03: the quote never prices what the create refuses
# =========================================================================== #
def test_the_quote_names_what_is_left_after_a_return_made_elsewhere(ctx):  # noqa: F811
    _line(ctx)["quantity"] = 3
    # The form opens: all three are returnable.
    opened = _post(ctx, _screen(return_qty=3), path="/quote")
    assert opened.status_code == 200 and opened.json()["net_refund"] == 1500.0, opened.text
    # Another till returns one meanwhile.
    assert _post(ctx, _screen(return_qty=1)).status_code == 201
    # The open form's quote refreshes: no payable figure, the remaining
    # quantity named -- exactly what the create answers.
    stale = _post(ctx, _screen(return_qty=3), path="/quote")
    assert stale.status_code == 400, stale.text
    assert "returnable quantity 2" in stale.text and "already returned 1" in stale.text
    assert "net_refund" not in stale.text
    create = _post(ctx, _screen(return_qty=3))
    assert create.status_code == 400 and create.json()["detail"] == stale.json()["detail"]
    # What is left still quotes and returns.
    left = _post(ctx, _screen(return_qty=2), path="/quote")
    assert left.status_code == 200 and left.json()["net_refund"] == 1000.0, left.text
    # A quote reads only: nothing booked on the line, no return saved by it.
    assert _line(ctx)["returned_qty"] == 1 and len(ctx["returns_coll"].docs) == 1
    assert _post(ctx, _screen(return_qty=2)).status_code == 201
    done = _post(ctx, _screen(return_qty=1), path="/quote")
    assert done.status_code == 400 and "returnable quantity 0" in done.text


def test_the_quote_refuses_a_line_that_is_not_on_the_sale_like_the_create(ctx):  # noqa: F811
    body = _screen(order_item_id="li-not-on-this-bill")
    quote, create = _post(ctx, body, path="/quote"), _post(ctx, body)
    assert quote.status_code == create.status_code == 400
    assert "does not match any line on the original order" in quote.text
    assert quote.json()["detail"] == create.json()["detail"]
    assert ctx["returns_coll"].docs == []


# =========================================================================== #
# L2-RETURN-F03: the recovery message follows the record's own stock state
# =========================================================================== #
OLD_BLANKET = "Put its units back in stock by hand"
BILL_LINES = [{"item_id": "li1", "item_type": "FRAME", "product_id": "P1", "sku": "F1", "quantity": 2},
              {"item_id": "li2", "item_type": "FRAME", "product_id": "P2", "sku": "F2", "quantity": 1},
              {"item_id": "li-lens", "item_type": "LENS", "product_id": "lens-17", "quantity": 2}]


def _item(item="li1", product="P1", sku="F1", qty=2.0, condition="GOOD", restock=True):
    return {"order_item_id": item, "product_id": product, "sku": sku, "product_name": "Frame",
            "return_qty": qty, "unit_price": 500.0, "gst_rate": 0.0, "condition": condition,
            "restock": restock}


def _kept(till, rid, **stock):  # noqa: F811
    """A return whose money moved and whose stock step ran, but whose record
    could not be saved: the record rides on the bill's kept hold."""
    doc = {
        "return_id": rid, "order_id": "ORD-1", "order_number": "ORD-1", "store_id": STORE,
        "customer_id": "CUST-1", "return_type": "RETURN", "status": "COMPLETED",
        "items": [_item()],
        "gross_refund": 1000.0, "net_refund": 1000.0, "refund_amount": 1000.0,
        "refund_tenders": [{"method": "CASH", "amount": 1000.0}], "credit_note_number": "TST01/C2627/0031",
        "restocked": [], "restock_applied": False, "restock_stock_ids": [], "quarantined_stock_ids": [],
        "created_at": _ago(30),
    }
    doc.update(stock)
    _bill_with_hold(till, rid, amount=1000.0, money_out=True, doc=dict(doc))
    till["db"].get_collection("orders").update_one(
        {"order_id": "ORD-1"}, {"$set": {"items": [dict(ln) for ln in BILL_LINES],
                                         "historical": bool(doc.get("historical"))}})
    return doc


def _row_of(product="P1", reactivated=0, minted=0, **more):
    return {"product_id": product, "sku": "F1", "quantity": reactivated + minted,
            "reactivated": reactivated, "minted": minted, "applied": True, **more}


BY_HAND = "Put these back in stock by hand"
NEVER_BY_HAND = "Do not add these by hand - IMS would count them twice."
RETRY = "Press Retry stock step on this return (Returns > Needs completing): IMS puts each piece back once."


def _msg(rid, stock_step_open=False):
    out = _complete(rid, cash_leg_paid=True)
    msg = out["message"]
    assert OLD_BLANKET not in msg and BY_HAND not in msg
    # The button is offered exactly when the message sends staff to it.
    assert out["stock_step_open"] is stock_step_open and (RETRY in msg) is stock_step_open, msg
    return msg


def _open_rows(role="ACCOUNTANT"):
    """The panel's "Stock still to put back" list (GET /returns/needs-completing)."""
    import asyncio

    from api.routers import returns as returns_router
    from tests.test_pos_money_doors import _user

    return asyncio.run(returns_router.needs_completing(_user(role)))["stock_open"]


def test_a_fully_restored_return_never_asks_for_a_second_stock_entry(till, tasks):  # noqa: F811
    kept = _kept(till, "RET-FULL", restock_applied=True, restock_stock_ids=["SU-1", "SU-2"],
                 restocked=[_row_of(reactivated=2)])
    out = _complete("RET-FULL", cash_leg_paid=True)
    msg = out["message"]
    assert OLD_BLANKET not in msg
    assert "Already dealt with: 2 piece(s) back in stock." in msg and "Nothing to add by hand." in msg
    assert "by hand" not in msg.replace("Nothing to add by hand.", "")
    assert out["stock_step_open"] is False and _open_rows() == []
    # The recovered record keeps the press's own stock answer and its money.
    saved = till["db"].get_collection("returns").find_one({"return_id": "RET-FULL"})
    for key in ("restock_applied", "restock_stock_ids", "restocked", "net_refund", "refund_amount",
                "refund_tenders", "credit_note_number", "items"):
        assert saved[key] == kept[key], key


def test_a_damaged_piece_set_aside_is_reported_as_dealt_with(till, tasks):  # noqa: F811
    _kept(till, "RET-DMG", items=[_item(qty=1.0, condition="DAMAGED")], restock_applied=True,
          quarantined_stock_ids=["SU-9"])
    msg = _msg("RET-DMG")
    assert "Already dealt with: 1 set aside (damaged or opened)." in msg and "Nothing to add by hand." in msg
    assert "NOT" not in msg


def test_an_opened_piece_set_aside_is_reported_as_dealt_with(till, tasks):  # noqa: F811
    _kept(till, "RET-OPN", items=[_item(qty=1.0, condition="OPENED")], restock_applied=True,
          quarantined_stock_ids=["SU-9"])
    msg = _msg("RET-OPN")
    assert "Already dealt with: 1 set aside (damaged or opened)." in msg and "Nothing to add by hand." in msg
    assert "NOT" not in msg and "Left out of stock" not in msg


def test_an_unapplied_return_names_each_piece_still_out(till, tasks):  # noqa: F811
    _kept(till, "RET-NONE")
    msg = _msg("RET-NONE", stock_step_open=True)
    assert f"NOT back in stock: 2 x F1. {NEVER_BY_HAND} {RETRY}" in msg
    assert "Already dealt with" not in msg and "Nothing to add by hand" not in msg
    # The pieces stay on the panel, by name, until the retry puts them back.
    assert _open_rows() == [{"return_id": "RET-NONE", "order_id": "ORD-1", "order_number": "ORD-1",
                             "store_id": STORE, "pieces": "2 x F1"}]


# The three ways a kept record's stock step is still open. Each is retryable
# (POST /returns/{id}/restock puts the piece back) and the press raised its
# own task saying so: a hand entry beside that retry is one frame counted twice.
OPEN_STEPS = {
    # An older imported order whose stock-in task could not be saved.
    "historical": {"historical": True},
    # An online order whose receiving shop could not be resolved.
    "online": {"restock_store_reason": "ONLINE_STORE_UNRESOLVED",
               "restocked": [{"product_id": "P1", "sku": "F1", "quantity": 2, "applied": False}]},
    # An ordinary counter bill whose stock write failed during the press.
    "write_failed": {},
}


@pytest.mark.parametrize("state", sorted(OPEN_STEPS))
def test_an_open_stock_step_is_never_handed_to_a_manual_entry(till, tasks, state):  # noqa: F811
    _kept(till, "RET-OPEN", **OPEN_STEPS[state])
    msg = _msg("RET-OPEN", stock_step_open=True)
    assert f"NOT back in stock: 2 x F1. {NEVER_BY_HAND} {RETRY}" in msg
    assert ("raises one stock-in task for the store manager" in msg) is (state == "historical")
    assert [row["pieces"] for row in _open_rows()] == ["2 x F1"]


def test_a_mixed_return_separates_the_piece_already_back_from_the_one_pending(till, tasks):  # noqa: F811
    _kept(till, "RET-MIX", restock_stock_ids=["SU-1"], restocked=[_row_of(reactivated=1)])
    msg = _msg("RET-MIX", stock_step_open=True)
    assert "Already dealt with: 1 piece(s) back in stock." in msg
    assert f"NOT back in stock: 1 x F1. {NEVER_BY_HAND}" in msg and "Nothing to add by hand" not in msg
    assert [row["pieces"] for row in _open_rows()] == ["1 x F1"]


def test_an_older_imported_orders_pieces_go_through_the_stock_in_task_only(till, tasks):  # noqa: F811
    """A HISTORICAL order's return puts nothing back by rule and raises the
    store manager's stock-in task (restock_applied True, no unit moved): the
    message must not contradict that task."""
    _kept(till, "RET-HIST", historical=True, restock_applied=True)
    msg = _msg("RET-HIST")
    assert "2 piece(s) are from an older imported order" in msg and "stock-in task" in msg
    assert "do not add them a second time" in msg
    assert "Nothing to add by hand" not in msg and "NOT back in stock" not in msg
    assert "Already dealt with" not in msg


def test_a_damaged_piece_that_was_not_set_aside_is_named(till, tasks):  # noqa: F811
    """restock_applied speaks for the GOOD pieces only; a DAMAGED piece whose
    set-aside did not happen still reads sold."""
    _kept(till, "RET-DMG-OUT", restock_applied=True, restock_stock_ids=["SU-1"],
          restocked=[_row_of(reactivated=1)],
          items=[_item(qty=1.0), _item("li2", "P2", "F2", 1.0, "DAMAGED")])
    msg = _msg("RET-DMG-OUT")
    assert "Already dealt with: 1 piece(s) back in stock." in msg
    assert "Damaged or opened and NOT set aside by IMS: 1 x F2." in msg
    assert "keep it off the shelf" in msg
    assert "Nothing to add by hand" not in msg


def test_a_record_saved_before_the_line_took_its_product_names_the_piece_from_the_bill(till, tasks):  # noqa: F811
    """Saved before L2-RETURN-F01: no product on the line, so nothing moved
    while the record says restock_applied. The bill line names the piece."""
    _kept(till, "RET-OLD", restock_applied=True, items=[_item(product=None, qty=1.0)])
    msg = _msg("RET-OLD")
    assert "NOT back in stock: 1 x F1." in msg and "Nothing to add by hand" not in msg
    # Its stock step reads finished, so no retry can take it: never a hand
    # entry (the sold record stays beside the new one), never a dead button.
    assert "stock step is closed" in msg and "ask the IMS admin" in msg
    assert _open_rows() == []


def test_pieces_the_return_kept_out_of_stock_are_said_so_not_called_finished(till, tasks):  # noqa: F811
    _kept(till, "RET-HELD", restock_applied=True,
          items=[_item(qty=1.0, condition="OPENED"), _item("li2", "P2", "F2", 1.0, restock=False)])
    msg = _msg("RET-HELD")
    # The opened piece was to be set aside and was not; the other was kept
    # out of stock by the return itself.
    assert "Damaged or opened and NOT set aside by IMS: 1 x F1." in msg
    assert "Left out of stock, as the return said (not for resale): 1 x F2." in msg
    assert "Already dealt with" not in msg and "Nothing to add by hand" not in msg


def test_a_line_that_was_never_stock_asks_for_nothing(till, tasks):  # noqa: F811
    # A made-to-order lens line: no stock piece on the return at all.
    _kept(till, "RET-LENS", restock_applied=True, items=[_item("li-lens", None, "", 2.0)])
    assert _msg("RET-LENS").endswith("No piece of this return is waiting to go back into stock.")


def test_an_online_orders_unit_its_claim_never_covered_is_not_asked_for(till, tasks):  # noqa: F811
    _kept(till, "RET-NC", restock_applied=True, restocked=[_row_of(never_claimed=2)])
    assert _msg("RET-NC").endswith("No piece of this return is waiting to go back into stock.")


def test_a_rebuilt_record_with_no_stock_provenance_never_asks_for_a_blind_entry(till, tasks):  # noqa: F811
    """Nothing kept says whether the stock step ran (the record is rebuilt
    from the credit line): never a blind "put them back"."""
    from api.services import store_credit_ledger as scl

    _bill_with_hold(till, "RET-REB")
    till["db"].get_collection("orders").update_one(
        {"order_id": "ORD-1"}, {"$set": {"items": [dict(ln) for ln in BILL_LINES]}})
    till["db"].get_collection("credit_note_ledger").insert_one(_row(
        "RET-REB:credit", type=scl.ISSUED, applied=True, delta=1200.0, order_id="ORD-1",
        note_number="TST01/C2627/0040", taxable=1525.42, tax=274.58, gst_rate=18.0,
        undo_on_void=_spec("RET-REB"), store_id=STORE))
    out = _complete("RET-REB", cash_leg_paid=True)
    msg = out["message"]
    assert OLD_BLANKET not in msg
    assert "did not keep whether its pieces went back into stock" in msg
    assert "only a piece that is not already in stock" in msg
    assert "Its pieces: 1 x F1." in msg and BY_HAND not in msg
    # No retry for it: nothing says which pieces the press already put back.
    assert out["stock_step_open"] is False and RETRY not in msg and _open_rows() == []
    # The recovery's accounting is what it was.
    assert out["net_refund"] == 1800.0 and out["credit_amount"] == 1200.0
    assert out["restock_applied"] is False and out["restock_stock_ids"] == []


# --------------------------------------------------------------------------- #
# Retry stock step (Needs completing): the one action for a piece still out
# --------------------------------------------------------------------------- #
def _recovered(ctx, rid="RET-REC", source="unsaved doc", **over):  # noqa: F811
    """A record Accounts completed in Needs completing, as that door saves it."""
    doc = {"return_id": rid, "order_id": "ORD-1", "order_number": "INV-1001", "store_id": "BV-PUN-01",
           "return_type": "RETURN", "status": "COMPLETED",
           "items": [{"order_item_id": "li1", "product_id": "PRD-1", "sku": "RB-1", "return_qty": 1.0,
                      "unit_price": 500.0, "condition": "GOOD", "restock": True}],
           "restocked": [], "restock_applied": False, "restock_stock_ids": [],
           "recovered": {"by": "U-ACC", "source": source}}
    doc.update(over)
    ctx["returns_coll"].docs.append(doc)
    return doc


def _retry(ctx, rid="RET-REC", roles=("ACCOUNTANT",), door="/complete-record/restock"):  # noqa: F811
    return ctx["client"].post(f"/api/v1/returns/{rid}{door}", headers=_hdr(list(roles)))


def test_retry_stock_step_puts_a_piece_still_out_back_exactly_once(ctx):  # noqa: F811
    _recovered(ctx)
    first = _retry(ctx)
    assert first.status_code == 200, first.text
    assert first.json()["restock_applied"] is True and first.json()["restock_stock_ids"] == ["STK-OLD-1"]
    assert _units(ctx) == [("STK-OLD-1", "PRD-1", "AVAILABLE")]
    saved = ctx["returns_coll"].docs[-1]
    assert saved["restock_applied"] is True and saved["restock_stock_ids"] == ["STK-OLD-1"]
    # Pressed again (or by the store's own retry): nothing more moves.
    for again in (_retry(ctx), _retry(ctx, roles=("ADMIN",), door="/restock")):
        assert again.status_code == 200 and again.json()["message"] == "Already restocked", again.text
    assert _units(ctx) == [("STK-OLD-1", "PRD-1", "AVAILABLE")]
    assert len(ctx["stock_audit_coll"].docs) == 1


def test_retry_stock_step_is_for_accounts_and_admin_on_a_completed_record_only(ctx):  # noqa: F811
    from api.services import capabilities
    from api.services.rbac_policy import POLICY

    _recovered(ctx)
    # The counter's roles keep their own door; they do not get this one.
    for role in ("CASHIER", "SALES_STAFF", "STORE_MANAGER"):
        assert _retry(ctx, roles=(role,)).status_code == 403
    # Accounts get this door only: the store's retry door stays closed to them.
    assert _retry(ctx, door="/restock").status_code == 403
    # An ordinary return (never completed in Needs completing) is not theirs.
    _recovered(ctx, "RET-PLAIN", recovered=None)
    assert _retry(ctx, "RET-PLAIN").status_code == 404
    assert _retry(ctx, "RET-NOT-THERE").status_code == 404
    assert _units(ctx) == [("STK-OLD-1", "PRD-1", "SOLD")]
    assert _retry(ctx, roles=("ADMIN",)).status_code == 200
    # Catalogued beside Complete record, on its capability (Accounts never
    # join the counter's returns:write).
    path = "/api/v1/returns/{return_id}/complete-record/restock"
    row = next(r for r in POLICY if r["path"] == path and r["method"] == "POST")
    assert sorted(row["allowed"]) == ["ACCOUNTANT", "ADMIN"]
    assert capabilities.capability_for("POST", path) == "returns:complete-record"


@pytest.mark.parametrize("door,roles", [("/restock", ("ADMIN",)), ("/complete-record/restock", ("ACCOUNTANT",))])
def test_no_retry_restocks_a_rebuilt_record(ctx, door, roles):  # noqa: F811
    """A rebuilt record keeps nothing of the press's stock step. The customer
    bought two and returned one, which the press put back before it died
    (STK-BACK); STK-OLD-1 is the one still worn. A retry used to flip the
    worn one to AVAILABLE, or mint a third."""
    ctx["stock_repo"].units.append({"stock_id": "STK-BACK", "product_id": "PRD-1", "store_id": "BV-PUN-01",
                                    "status": "AVAILABLE", "returned_from_order_id": "ORD-1"})
    _line(ctx)["quantity"] = 2
    _recovered(ctx, source="credit line",
               items=[{"order_item_id": "li1", "item_id": "li1", "product_id": "PRD-1", "return_qty": 1.0}])
    before = _units(ctx)
    r = _retry(ctx, roles=roles, door=door)
    assert r.status_code == 409 and "did not keep whether its pieces went back" in r.text, r.text
    assert _units(ctx) == before and ctx["stock_audit_coll"].docs == []
    saved = ctx["returns_coll"].docs[-1]
    assert saved["restock_applied"] is False and not saved.get("restock_in_progress")


# =========================================================================== #
# L2-RETURN-F01: the read-only list of returns saved before the fix
# =========================================================================== #
def test_the_read_only_report_lists_old_returns_saved_without_a_product():
    import copy

    from scripts.count_returns_missing_product import returns_missing_product
    from tests.strict_fakes import StrictDB

    db = StrictDB()
    db.get_collection("orders").insert_one({
        "order_id": "ORD-1", "order_number": "BV-1",
        "items": [{"item_id": "li1", "product_id": "P1", "quantity": 2}, {"item_id": "li-fee", "quantity": 1},
                  # Never stock: a manual Rx lens, a catalogue lens, a service.
                  {"item_id": "li-rx", "item_type": "LENS", "product_id": "lens-1731300000000", "quantity": 2},
                  {"item_id": "li-cat", "item_type": "LENS", "product_id": "PRD-LENS-1", "quantity": 2},
                  {"item_id": "li-svc", "item_type": "SERVICE", "product_id": "PRD-FIT", "quantity": 1}]})

    def _ret(rid, at, items, **over):
        return {"return_id": rid, "order_id": "ORD-1", "store_id": "S1", "status": "COMPLETED",
                "created_at": at, "restock_applied": True, "items": items, **over}

    def _it(item="li1", product=None, condition="GOOD"):
        return {"order_item_id": item, "product_id": product, "return_qty": 1.0, "condition": condition,
                "restock": True}

    returns = db.get_collection("returns")
    returns.insert_one(_ret("RET-OLD", "2026-11-04T04:00:00+00:00", [
        _it(), _it("li-fee"), _it("li-rx"), _it("li-cat"), _it("li-svc")]))
    returns.insert_one(_ret("RET-DMG", "2026-11-05T04:00:00+00:00", [_it(condition="DAMAGED")]))
    returns.insert_one(_ret("RET-OPENED", "2026-11-05T05:00:00+00:00", [_it(condition="OPENED")]))
    returns.insert_one(_ret("RET-NEW", "2026-11-11T04:00:00+00:00", [_it(product="P1")]))
    returns.insert_one(_ret("RET-WEB", "2026-11-06T04:00:00+00:00", [_it()], shopify_refund_id="77"))
    before = copy.deepcopy([list(db.get_collection(n).find({})) for n in ("orders", "returns")])
    assert returns_missing_product(db) == [
        ("RET-OLD", "S1", "BV-1", "2026-11-04T04:00:00+00:00", "li1", "P1", 1.0, "GOOD"),
        ("RET-DMG", "S1", "BV-1", "2026-11-05T04:00:00+00:00", "li1", "P1", 1.0, "DAMAGED"),
    ]
    assert [list(db.get_collection(n).find({})) for n in ("orders", "returns")] == before, "reads only"
