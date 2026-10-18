"""Owner decision 2026-10-18: a supplier return names the exact pieces.

A frame (anything whose pieces carry their own unit barcode) goes back piece by
piece; a piece on the shelf is set aside (QUARANTINED, off sale and off the
website) when it is added; shipping moves every piece to RTV; cancelling before
shipping puts back what the return took off the shelf. A piece that cannot go
is a plain 422, never skipped. Contact lenses / solutions (dated pieces) go by
count, earliest expiry first. Real routes, one mongomock database.
"""
import pytest

from tests.test_receipt_rejection_handoff_a08 import app_world, world  # noqa: F401
from tests.test_receipt_without_po import MANAGER, STORE, FRAME, LENS, DEALER
from api.routers import vendor_returns as vr
from api.services import item_events as ie
from api.services import online_stock_writeback as osw


@pytest.fixture
def w(app_world, monkeypatch):  # noqa: F811
    calls = []
    monkeypatch.setattr(osw, "writeback_after_units_left",
                        lambda db, pids, store, source: calls.append((list(pids), store, source)))
    app_world["website"] = calls
    return app_world


def unit(w, sid, product=FRAME, status="AVAILABLE", store=STORE, **extra):
    w["db"].stock_units.insert_one({"stock_id": sid, "product_id": product, "store_id": store,
                                    "status": status, "barcode": f"BC{sid}", "quantity": 1, **extra})


def create(w, *lines, stock_ids=None):
    body = {"vendor_id": DEALER, "vendor_name": "Bank More", "store_id": STORE,
            "return_type": "credit_note", "items": list(lines)}
    if stock_ids is not None:
        body["stock_ids"] = stock_ids
    return w["as_"](MANAGER).post("/vendor-returns/", json=body)


def line(qty=1, product=FRAME, name="Acme Aviator 5420", stock_ids=None):
    out = {"product_id": product, "product_name": name, "quantity": qty, "reason": "defective"}
    if stock_ids is not None:
        out["stock_ids"] = stock_ids
    return out


def move(w, rid, status):
    return w["as_"](MANAGER).patch(f"/vendor-returns/{rid}/status", json={"status": status})


def returned(w, rid):
    return w["db"].vendor_returns.find_one({"return_id": rid})


def state(w, sid):
    return w["db"].stock_units.find_one({"stock_id": sid})


def on_hand(w, product=FRAME):
    return w["db"].stock_units.count_documents({"product_id": product, "store_id": STORE,
                                                **ie.on_hand_match()})


def events(w, sid):
    return [(e["event_type"], e["from_state"], e["to_state"], e["source_type"])
            for e in w["db"].item_events.find({"stock_id": sid}).sort("event_seq", 1)]


def test_a_shelf_piece_is_set_aside_then_shipped_to_rtv_and_leaves_stock_and_website(w):
    unit(w, "U1")
    unit(w, "U2")
    assert on_hand(w) == 2
    made = create(w, line(stock_ids=["U1"]))
    assert made.status_code == 201, made.text
    rid = made.json()["return_id"]
    assert state(w, "U1")["status"] == "QUARANTINED" and state(w, "U1")["rtv_vendor_id"] == rid
    assert state(w, "U2")["status"] == "AVAILABLE"  # only the named piece
    assert on_hand(w) == 1
    assert (["P-FR-NOPO"], STORE, "ledger:quarantine.in") in w["website"]  # website count goes down
    doc = returned(w, rid)
    assert doc["stock_ids"] == ["U1"] and doc["set_aside_stock_ids"] == ["U1"]
    assert move(w, rid, "approved").status_code == 200
    shipped = move(w, rid, "shipped")
    assert shipped.status_code == 200, shipped.text
    assert state(w, "U1")["status"] == "RTV"
    assert on_hand(w) == 1
    assert events(w, "U1") == [("quarantine.in", "AVAILABLE", "QUARANTINED", "VENDOR_RETURN"),
                               ("quarantine.out", "QUARANTINED", "RTV", "VENDOR_RETURN")]
    # A shipped return cannot be cancelled back onto the shelf.
    assert move(w, rid, "cancelled").status_code == 400
    assert state(w, "U1")["status"] == "RTV"


@pytest.mark.parametrize("status,store,word", [
    ("SOLD", STORE, "is sold"),
    ("RESERVED", STORE, "is reserved for an order"),
    ("RTV", STORE, "is already returned to a supplier"),
    ("AVAILABLE", "OTHER-SHOP", "is not in this shop's stock"),
])
def test_a_piece_that_cannot_go_is_a_plain_422_and_nothing_moves(w, status, store, word):
    unit(w, "OK1")
    unit(w, "BAD", status=status, store=store)
    refused = create(w, line(qty=2, stock_ids=["OK1", "BAD"]))
    assert refused.status_code == 422
    assert word in refused.json()["detail"]
    assert state(w, "OK1")["status"] == "AVAILABLE" and not state(w, "OK1").get("rtv_vendor_id")
    assert state(w, "BAD")["status"] == status
    assert w["db"].vendor_returns.count_documents({}) == 0


def test_an_unknown_piece_and_a_piece_named_twice_are_refused(w):
    unit(w, "U1")
    assert "is not in this shop's stock" in create(w, line(stock_ids=["NOPE"])).json()["detail"]
    twice = create(w, line(qty=2, stock_ids=["U1", "U1"]))
    assert twice.status_code == 422 and "named twice" in twice.json()["detail"]


def test_a_frame_must_be_named_piece_by_piece(w):
    unit(w, "U1")
    refused = create(w, line(qty=1))
    assert refused.status_code == 422
    assert "tracked piece by piece" in refused.json()["detail"]
    mismatch = create(w, line(qty=2, stock_ids=["U1"]))
    assert mismatch.status_code == 422 and "1 piece(s) named" in mismatch.json()["detail"]
    assert state(w, "U1")["status"] == "AVAILABLE"


def test_a_piece_already_on_another_open_return_is_refused(w):
    unit(w, "U1")
    first = create(w, line(stock_ids=["U1"])).json()["return_id"]
    again = create(w, line(stock_ids=["U1"]))
    assert again.status_code == 422 and first in again.json()["detail"]


def test_dated_stock_goes_by_count_earliest_expiry_first(w):
    unit(w, "CL-LATE", product=LENS, expiry_date="2028-01-31")
    unit(w, "CL-SOON", product=LENS, expiry_date="2026-11-30")
    unit(w, "CL-MID", product=LENS, expiry_date="2027-03-31")
    unit(w, "CL-SOLD", product=LENS, status="SOLD", expiry_date="2026-01-31")
    short = create(w, line(qty=4, product=LENS, name="Acme Daily"))
    assert short.status_code == 422 and "holds only 3" in short.json()["detail"]
    made = create(w, line(qty=2, product=LENS, name="Acme Daily"))
    assert made.status_code == 201, made.text
    rid = made.json()["return_id"]
    assert sorted(returned(w, rid)["stock_ids"]) == ["CL-MID", "CL-SOON"]
    assert on_hand(w, LENS) == 1
    assert move(w, rid, "approved").status_code == 200
    assert move(w, rid, "shipped").status_code == 200
    assert [state(w, s)["status"] for s in ("CL-SOON", "CL-MID", "CL-LATE")] == ["RTV", "RTV", "AVAILABLE"]


def test_something_the_shop_holds_no_piece_of_moves_nothing(w):
    made = create(w, line(qty=3, product="made-to-order-lens", name="Made-to-order lens"))
    assert made.status_code == 201, made.text
    assert returned(w, made.json()["return_id"])["stock_ids"] == []


def test_cancel_before_shipping_puts_back_only_what_the_return_took_off_the_shelf(w):
    unit(w, "SHELF")
    unit(w, "ASIDE", status="QUARANTINED", quarantine_reason="DEFECTIVE")
    made = create(w, line(qty=2, stock_ids=["SHELF", "ASIDE"]))
    assert made.status_code == 201, made.text
    rid = made.json()["return_id"]
    assert on_hand(w) == 0
    w["website"].clear()
    assert move(w, rid, "cancelled").status_code == 200
    assert state(w, "SHELF")["status"] == "AVAILABLE" and state(w, "SHELF")["rtv_vendor_id"] is None
    # The defective piece was set aside before the return: it stays set aside.
    assert state(w, "ASIDE")["status"] == "QUARANTINED" and state(w, "ASIDE")["rtv_vendor_id"] is None
    assert on_hand(w) == 1
    assert w["website"] == [([FRAME], STORE, "vendor_return_release")]
    assert events(w, "SHELF")[-1] == ("quarantine.out", "QUARANTINED", "AVAILABLE", "VENDOR_RETURN")


def test_shipping_is_refused_while_a_named_piece_is_no_longer_set_aside(w):
    unit(w, "U1")
    rid = create(w, line(stock_ids=["U1"])).json()["return_id"]
    assert move(w, rid, "approved").status_code == 200
    w["db"].stock_units.update_one({"stock_id": "U1"}, {"$set": {"status": "SOLD"}})
    refused = move(w, rid, "shipped")
    assert refused.status_code == 409 and "BCU1" in refused.json()["detail"]
    assert w["db"].vendor_returns.find_one({"return_id": rid})["status"] == "approved"
    assert state(w, "U1")["status"] == "SOLD"


def test_a_piece_on_an_open_return_cannot_be_lifted_back_onto_the_shelf(w):
    unit(w, "U1")
    rid = create(w, line(stock_ids=["U1"])).json()["return_id"]
    lifted = w["as_"](MANAGER).patch("/inventory/stock/U1/lift-quarantine",
                                     json={"lift_reason": "Looks fine now"})
    assert lifted.status_code == 409 and lifted.json()["detail"]["code"] == "on_supplier_return"
    assert rid in lifted.json()["detail"]["message"]
    assert state(w, "U1")["status"] == "QUARANTINED"


def test_the_quarantine_queue_link_joins_its_piece_to_the_line_of_its_product(w):
    unit(w, "ASIDE", status="QUARANTINED", quarantine_reason="DEFECTIVE", rtv_vendor_id=DEALER)
    made = create(w, line(), stock_ids=["ASIDE"])
    assert made.status_code == 201, made.text
    rid = made.json()["return_id"]
    assert state(w, "ASIDE")["rtv_vendor_id"] == rid
    assert state(w, "ASIDE")["status"] == "QUARANTINED"
    linked = list(w["db"].audit_logs.find({"action": "QUARANTINE_LINKED_RTV", "entity_id": "ASIDE"}))
    assert len(linked) == 1 and linked[0]["after_state"] == {"rtv_vendor_id": rid}
    assert returned(w, rid)["set_aside_stock_ids"] == []  # already set aside: not taken off the shelf
    stray = create(w, line(product="OTHER", name="Other"), stock_ids=["ASIDE"])
    assert stray.status_code == 422


def test_a_piece_lost_to_a_race_undoes_the_pieces_already_set_aside(w, monkeypatch):
    unit(w, "U1")
    unit(w, "U2")
    real = vr._ie.record_event_atomic
    calls = []

    def second_loses(db, **kw):
        calls.append(kw["stock_id"])
        if kw["stock_id"] == "U2" and kw["to_state"] is ie.StockState.QUARANTINED:
            return False, None  # sold at the till between the check and the write
        return real(db, **kw)

    monkeypatch.setattr(vr._ie, "record_event_atomic", second_loses)
    lost = create(w, line(qty=2, stock_ids=["U1", "U2"]))
    assert lost.status_code == 409
    assert state(w, "U1")["status"] == "AVAILABLE" and state(w, "U1")["rtv_vendor_id"] is None
    assert state(w, "U2")["status"] == "AVAILABLE"
    assert w["db"].vendor_returns.count_documents({}) == 0
