"""Owner decision 2026-10-18: a supplier return names the exact pieces.

Every piece IMS holds carries its own unit barcode, so every piece going back
is named. A piece on the shelf is set aside (QUARANTINED, off sale and off the
website) when it is added -- a manager's act; shipping moves every piece to
RTV BEFORE the status changes; cancelling before shipping puts back what the
return took off the shelf and restores each piece's earlier link. A piece
that cannot go is a plain 422, never skipped. A line naming no piece is only
for something the shop holds no piece of, with a note. Real routes, one
mongomock database.
"""
import pytest

from tests.test_receipt_rejection_handoff_a08 import app_world, world  # noqa: F401
from tests.test_receipt_without_po import (
    ACCOUNTANT, DEALER, ENTITY, FRAME, LENS, MANAGER, SHARMA, SHOP_GSTIN, STORE,
)
from api.routers import item_events as items_router
from api.routers import vendor_returns as vr
from api.services import item_events as ie
from api.services import online_stock_writeback as osw
from scripts.count_stuck_return_pieces import stuck_pieces

WORKSHOP = {"user_id": "u-ws", "username": "ws", "roles": ["WORKSHOP_STAFF"],
            "store_ids": [STORE], "active_store_id": STORE}
NOTE = "Made-to-order lens, never held in stock"


@pytest.fixture
def w(app_world, monkeypatch):  # noqa: F811
    calls = []
    monkeypatch.setattr(osw, "writeback_after_units_left",
                        lambda db, pids, store, source: calls.append((list(pids), store, source)))
    app_world["website"] = calls
    app_world["as_"](MANAGER).app.include_router(items_router.router, prefix="/items")
    return app_world


def unit(w, sid, product=FRAME, status="AVAILABLE", store=STORE, **extra):
    w["db"].stock_units.insert_one({"stock_id": sid, "product_id": product, "store_id": store,
                                    "status": status, "barcode": f"BC{sid}", "quantity": 1, **extra})


def create(w, *lines, user=MANAGER, stock_ids=None, **extra):
    body = {"vendor_id": DEALER, "vendor_name": "Bank More", "store_id": STORE,
            "return_type": "credit_note", "items": list(lines), **extra}
    if stock_ids is not None:
        body["stock_ids"] = stock_ids
    return w["as_"](user).post("/vendor-returns/", json=body)


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


def approved(w, *sids, product=FRAME):
    for sid in sids:
        unit(w, sid, product=product)
    rid = create(w, line(qty=len(sids), product=product, stock_ids=list(sids))).json()["return_id"]
    assert move(w, rid, "approved").status_code == 200
    return rid


# --------------------------------------------------------------------------
# Create: name the pieces, take them off sale
# --------------------------------------------------------------------------

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
    assert ([FRAME], STORE, "ledger:quarantine.in") in w["website"]  # website count goes down
    doc = returned(w, rid)
    assert doc["stock_ids"] == ["U1"] and doc["set_aside_stock_ids"] == ["U1"]
    # The manager reads the piece list (no money in it).
    seen = w["as_"](MANAGER).get(f"/vendor-returns/{rid}").json()
    assert seen["pieces"] == [{"stock_id": "U1", "barcode": "BCU1", "product_id": FRAME,
                               "no_receipt": True}]
    assert move(w, rid, "approved").status_code == 200
    shipped = move(w, rid, "shipped")
    assert shipped.status_code == 200, shipped.text
    assert state(w, "U1")["status"] == "RTV"
    assert "ship_fence" not in returned(w, rid)
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


def test_a_named_piece_of_a_different_product_is_refused(w):
    unit(w, "OTHER", product=LENS)
    wrong = create(w, line(stock_ids=["OTHER"]))
    assert wrong.status_code == 422 and "is not Acme Aviator 5420" in wrong.json()["detail"]
    assert state(w, "OTHER")["status"] == "AVAILABLE"


def test_every_piece_is_named_dated_stock_included(w):
    unit(w, "U1")
    unit(w, "CL1", product=LENS, expiry_date="2026-11-30")
    for product, name in ((FRAME, "Acme Aviator 5420"), (LENS, "Acme Daily -2.00")):
        refused = create(w, line(product=product, name=name), notes=NOTE)
        assert refused.status_code == 422, refused.text
        assert "holds pieces of: scan or pick" in refused.json()["detail"]
    mismatch = create(w, line(qty=2, stock_ids=["U1"]))
    assert mismatch.status_code == 422 and "1 piece(s) named" in mismatch.json()["detail"]
    assert state(w, "U1")["status"] == "AVAILABLE" and state(w, "CL1")["status"] == "AVAILABLE"


def test_a_typed_name_of_a_held_product_cannot_bypass_the_pieces(w):
    unit(w, "U1")
    typed = create(w, line(product="acme-aviator-5420", name="acme aviator 5420"), notes=NOTE)
    assert typed.status_code == 422 and "scan or pick" in typed.json()["detail"]


def test_a_typed_line_for_something_not_held_needs_a_note_and_moves_nothing(w):
    bare = create(w, line(qty=3, product="made-to-order-lens", name="Made-to-order lens"))
    assert bare.status_code == 422 and "needs a note" in bare.json()["detail"]
    made = create(w, line(qty=3, product="made-to-order-lens", name="Made-to-order lens"), notes=NOTE)
    assert made.status_code == 201, made.text
    assert returned(w, made.json()["return_id"])["stock_ids"] == []


def test_a_piece_already_on_another_open_return_is_refused(w):
    unit(w, "U1")
    first = create(w, line(stock_ids=["U1"])).json()["return_id"]
    again = create(w, line(stock_ids=["U1"]))
    assert again.status_code == 422 and first in again.json()["detail"]


def test_a_piece_rejected_at_receiving_stays_with_its_own_draft(w):
    w["db"].vendor_returns.insert_one({"return_id": "VR-GRN-X", "status": "draft", "receipt_rejection": True,
                                       "store_id": STORE, "stock_ids": ["REJ"]})
    unit(w, "REJ", status="QUARANTINED", receipt_disposition="REJECTED", rtv_vendor_id="VR-GRN-X")
    refused = create(w, line(stock_ids=["REJ"]))
    assert refused.status_code == 409
    assert state(w, "REJ")["rtv_vendor_id"] == "VR-GRN-X"


def test_a_piece_from_another_supplier_is_refused_and_one_without_a_receipt_is_flagged(w):
    w["db"].grns.insert_many([{"grn_id": "GRN-SH", "vendor_id": SHARMA},
                              {"grn_id": "GRN-DL", "vendor_id": DEALER}])
    unit(w, "SH1", grn_id="GRN-SH")
    unit(w, "DL1", grn_id="GRN-DL")
    unit(w, "OPEN1")  # opening stock: no receipt
    other = create(w, line(stock_ids=["SH1"]))
    assert other.status_code == 422 and "different supplier" in other.json()["detail"]
    assert state(w, "SH1")["status"] == "AVAILABLE"
    made = create(w, line(qty=2, stock_ids=["DL1", "OPEN1"]))
    assert made.status_code == 201, made.text
    flags = {p["stock_id"]: p["no_receipt"] for p in returned(w, made.json()["return_id"])["pieces"]}
    assert flags == {"DL1": False, "OPEN1": True}


@pytest.mark.parametrize("user", [WORKSHOP, ACCOUNTANT])
def test_only_a_manager_takes_a_piece_off_the_shelf(w, user):
    unit(w, "SHELF")
    unit(w, "ASIDE", status="QUARANTINED", quarantine_reason="DEFECTIVE")
    refused = create(w, line(stock_ids=["SHELF"]), user=user)
    assert refused.status_code == 403
    assert state(w, "SHELF")["status"] == "AVAILABLE"
    named = create(w, line(stock_ids=["ASIDE"]), user=user)
    assert named.status_code == 201, named.text
    assert state(w, "ASIDE")["rtv_vendor_id"] == named.json()["return_id"]


def test_the_same_form_submitted_twice_is_one_return(w):
    unit(w, "U1")
    first = create(w, line(stock_ids=["U1"]), client_request_id="form-1")
    again = create(w, line(stock_ids=["U1"]), client_request_id="form-1")
    assert first.status_code == 201 and again.status_code == 201, again.text
    assert first.json()["return_id"] == again.json()["return_id"]
    assert w["db"].vendor_returns.count_documents({}) == 1
    assert state(w, "U1")["rtv_vendor_id"] == first.json()["return_id"]


def test_two_submissions_racing_past_the_lookup_still_make_one_return(w, monkeypatch):
    real = w["db"].vendor_returns.find_one
    monkeypatch.setattr(w["db"].vendor_returns, "find_one",
                        lambda flt=None, *a, **k: None if "client_request_id" in (flt or {}) else real(flt, *a, **k))
    body = dict(notes=NOTE, client_request_id="form-2")
    first = create(w, line(product="made-to-order-lens", name="Lens"), **body)
    again = create(w, line(product="made-to-order-lens", name="Lens"), **body)
    assert first.status_code == 201 and again.status_code == 201
    assert first.json()["return_id"] == again.json()["return_id"]
    assert w["db"].vendor_returns.count_documents({}) == 1


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


# --------------------------------------------------------------------------
# All or nothing
# --------------------------------------------------------------------------

def test_a_piece_lost_to_a_race_undoes_the_pieces_already_set_aside(w, monkeypatch):
    unit(w, "U1")
    unit(w, "U2")
    real = vr._ie.record_event_atomic

    def second_loses(db, **kw):
        if kw["stock_id"] == "U2" and kw["to_state"] is ie.StockState.QUARANTINED:
            return False, None  # sold at the till between the check and the write
        return real(db, **kw)

    monkeypatch.setattr(vr._ie, "record_event_atomic", second_loses)
    lost = create(w, line(qty=2, stock_ids=["U1", "U2"]))
    assert lost.status_code == 409
    assert state(w, "U1")["status"] == "AVAILABLE" and state(w, "U1")["rtv_vendor_id"] is None
    assert state(w, "U2")["status"] == "AVAILABLE"
    assert w["db"].vendor_returns.count_documents({}) == 0


def test_a_link_lost_after_the_ledger_move_never_strands_the_piece_off_sale(w, monkeypatch):
    unit(w, "U1")
    real = w["db"].stock_units.update_one

    class _Missed:
        matched_count = 0
        modified_count = 0

    def link_misses(flt, change, *a, **k):
        if "rtv_vendor_id" in change.get("$set", {}) and str(change["$set"]["rtv_vendor_id"]).startswith("VR-"):
            return _Missed()
        return real(flt, change, *a, **k)

    monkeypatch.setattr(w["db"].stock_units, "update_one", link_misses)
    lost = create(w, line(stock_ids=["U1"]))
    assert lost.status_code == 409
    assert state(w, "U1")["status"] == "AVAILABLE"  # not left QUARANTINED and unlinked
    assert on_hand(w) == 1


def test_an_unexpected_failure_while_setting_aside_undoes_it(w, monkeypatch):
    unit(w, "U1")
    unit(w, "U2")
    real = vr._ie.record_event_atomic

    def blows_up(db, **kw):
        if kw["stock_id"] == "U2":
            raise RuntimeError("ledger offline")
        return real(db, **kw)

    monkeypatch.setattr(vr._ie, "record_event_atomic", blows_up)
    with pytest.raises(RuntimeError):
        create(w, line(qty=2, stock_ids=["U1", "U2"]))
    assert state(w, "U1")["status"] == "AVAILABLE" and state(w, "U1")["rtv_vendor_id"] is None


def test_a_return_that_could_not_be_saved_gives_its_pieces_back(w, monkeypatch):
    unit(w, "U1")

    def offline(*_a, **_k):
        raise RuntimeError("offline")

    monkeypatch.setattr(w["db"].vendor_returns, "update_one", offline)
    failed = create(w, line(stock_ids=["U1"]))
    assert failed.status_code == 500
    assert state(w, "U1")["status"] == "AVAILABLE" and state(w, "U1")["rtv_vendor_id"] is None
    assert w["db"].vendor_returns.count_documents({}) == 0  # the claim was given up


# --------------------------------------------------------------------------
# Ship: the pieces leave first, the status follows
# --------------------------------------------------------------------------

def test_shipping_is_refused_while_a_named_piece_is_no_longer_set_aside(w):
    rid = approved(w, "U1")
    w["db"].stock_units.update_one({"stock_id": "U1"}, {"$set": {"status": "SOLD"}})
    refused = move(w, rid, "shipped")
    assert refused.status_code == 409 and "BCU1" in refused.json()["detail"]
    assert returned(w, rid)["status"] == "approved"
    assert state(w, "U1")["status"] == "SOLD"


def test_a_piece_that_will_not_move_holds_the_ship_and_ship_again_finishes(w, monkeypatch):
    rid = approved(w, "U1", "U2")
    real = vr._ie.record_event_atomic

    def u2_stuck(db, **kw):
        if kw["stock_id"] == "U2" and kw["to_state"] is ie.StockState.RTV:
            return False, None
        return real(db, **kw)

    monkeypatch.setattr(vr._ie, "record_event_atomic", u2_stuck)
    held = move(w, rid, "shipped")
    assert held.status_code == 409 and "BCU2" in held.json()["detail"]
    doc = returned(w, rid)
    assert doc["status"] == "approved" and "ship_fence" not in doc
    assert (state(w, "U1")["status"], state(w, "U2")["status"]) == ("RTV", "QUARANTINED")
    # Part of it left: it cannot be cancelled back onto the shelf.
    refused = move(w, rid, "cancelled")
    assert refused.status_code == 409 and "BCU1" in refused.json()["detail"]
    monkeypatch.setattr(vr._ie, "record_event_atomic", real)
    done = move(w, rid, "shipped")
    assert done.status_code == 200, done.text
    assert returned(w, rid)["status"] == "shipped"
    assert state(w, "U2")["status"] == "RTV"


def test_ship_again_on_a_shipped_return_finishes_a_stuck_piece(w):
    rid = approved(w, "U1", "U2")
    assert move(w, rid, "shipped").status_code == 200
    w["db"].stock_units.update_one({"stock_id": "U2"}, {"$set": {"status": "QUARANTINED"}})
    assert [(r[0], r[2], r[5]) for r in stuck_pieces(w["db"])] == [(rid, "U2", True)]
    again = move(w, rid, "shipped")
    assert again.status_code == 200, again.text
    assert returned(w, rid)["status"] == "shipped"
    assert state(w, "U2")["status"] == "RTV"
    assert stuck_pieces(w["db"]) == []


def test_a_cancel_cannot_land_while_a_ship_is_moving_the_pieces(w, monkeypatch):
    rid = approved(w, "U1")
    real = vr._ship_pieces
    raced = []

    def cancel_mid_ship(db, doc, actor):
        raced.append(move(w, rid, "cancelled").status_code)
        return real(db, doc, actor)

    monkeypatch.setattr(vr, "_ship_pieces", cancel_mid_ship)
    assert move(w, rid, "shipped").status_code == 200
    assert raced == [409]
    assert returned(w, rid)["status"] == "shipped" and state(w, "U1")["status"] == "RTV"


# --------------------------------------------------------------------------
# Cancel and the doors out of quarantine
# --------------------------------------------------------------------------

def test_cancel_before_shipping_puts_back_only_what_the_return_took_and_restores_links(w):
    unit(w, "SHELF")
    unit(w, "ASIDE", status="QUARANTINED", quarantine_reason="DEFECTIVE", rtv_vendor_id=DEALER)
    made = create(w, line(qty=2, stock_ids=["SHELF", "ASIDE"]))
    assert made.status_code == 201, made.text
    rid = made.json()["return_id"]
    assert on_hand(w) == 0
    w["website"].clear()
    assert move(w, rid, "cancelled").status_code == 200
    assert state(w, "SHELF")["status"] == "AVAILABLE" and state(w, "SHELF")["rtv_vendor_id"] is None
    # The defective piece was set aside before the return: it stays set aside,
    # with the supplier the Quarantine screen named for it.
    assert state(w, "ASIDE")["status"] == "QUARANTINED" and state(w, "ASIDE")["rtv_vendor_id"] == DEALER
    assert on_hand(w) == 1
    assert w["website"] == [([FRAME], STORE, "vendor_return_release")]
    assert events(w, "SHELF")[-1] == ("quarantine.out", "QUARANTINED", "AVAILABLE", "VENDOR_RETURN")


@pytest.mark.parametrize("door", ["lift", "release"])
def test_a_piece_on_an_open_return_cannot_leave_quarantine_by_hand(w, door):
    unit(w, "U1")
    rid = create(w, line(stock_ids=["U1"])).json()["return_id"]
    http = w["as_"](MANAGER)
    if door == "lift":
        out = http.patch("/inventory/stock/U1/lift-quarantine", json={"lift_reason": "Looks fine now"})
    else:
        out = http.post("/items/U1/quarantine/release", json={"disposition": "RESTOCK"})
    assert out.status_code == 409 and out.json()["detail"]["code"] == "on_supplier_return"
    assert rid in out.json()["detail"]["message"]
    assert state(w, "U1")["status"] == "QUARANTINED"


def test_the_ledger_release_also_refuses_a_piece_rejected_at_receiving(w):
    unit(w, "REJ", status="QUARANTINED", receipt_disposition="REJECTED")
    out = w["as_"](MANAGER).post("/items/REJ/quarantine/release", json={"disposition": "RTV"})
    assert out.status_code == 409 and out.json()["detail"]["code"] == "receipt_rejected"
    assert state(w, "REJ")["status"] == "QUARANTINED"


# --------------------------------------------------------------------------
# Review round 2
# --------------------------------------------------------------------------

def _overlap(w, monkeypatch, hook):
    """Two submissions of ONE form naming the same piece, the second landing
    inside the first at `hook` (Q1: while the first sets its pieces aside;
    Q2: before the first claims the return's id)."""
    unit(w, "U1")
    real = getattr(vr, hook)
    inner, fired = [], []

    def second_lands_here(*args, **kwargs):
        if not fired:
            fired.append(True)  # before the call: the second's own pass runs straight through
            inner.append(create(w, line(stock_ids=["U1"]), client_request_id="form-q"))
        return real(*args, **kwargs)

    monkeypatch.setattr(vr, hook, second_lands_here)
    outer = create(w, line(stock_ids=["U1"]), client_request_id="form-q")
    monkeypatch.setattr(vr, hook, real)
    return outer, inner[0]


@pytest.mark.parametrize("hook", ["_set_aside", "_claim_return"])
def test_overlapping_submissions_of_one_form_save_one_return_holding_its_pieces(w, monkeypatch, hook):
    outer, inner = _overlap(w, monkeypatch, hook)
    assert outer.status_code == 201 and inner.status_code == 201, (outer.text, inner.text)
    rid = outer.json()["return_id"]
    assert inner.json()["return_id"] == rid
    assert w["db"].vendor_returns.count_documents({}) == 1
    doc = returned(w, rid)
    assert doc["status"] == "created" and doc["stock_ids"] == ["U1"]
    assert state(w, "U1")["status"] == "QUARANTINED" and state(w, "U1")["rtv_vendor_id"] == rid
    assert on_hand(w) == 0
    assert move(w, rid, "approved").status_code == 200
    assert move(w, rid, "shipped").status_code == 200
    assert state(w, "U1")["status"] == "RTV"


def test_the_same_request_id_with_different_details_is_refused(w):
    unit(w, "U1")
    unit(w, "U2")
    first = create(w, line(stock_ids=["U1"]), client_request_id="form-x")
    assert first.status_code == 201
    other = create(w, line(stock_ids=["U2"]), client_request_id="form-x")
    assert other.status_code == 409 and "different details" in other.json()["detail"]
    assert state(w, "U2")["status"] == "AVAILABLE"
    assert w["db"].vendor_returns.count_documents({}) == 1


def test_a_failed_save_gives_its_claim_up_so_the_form_can_be_sent_again(w, monkeypatch):
    unit(w, "U1")
    real = vr._ie.record_event_atomic
    monkeypatch.setattr(vr._ie, "record_event_atomic", lambda db, **kw: (False, None))
    assert create(w, line(stock_ids=["U1"]), client_request_id="form-r").status_code == 409
    assert w["db"].vendor_returns.count_documents({}) == 0
    monkeypatch.setattr(vr._ie, "record_event_atomic", real)
    again = create(w, line(stock_ids=["U1"]), client_request_id="form-r")
    assert again.status_code == 201, again.text
    assert state(w, "U1")["rtv_vendor_id"] == again.json()["return_id"]


def test_a_return_being_saved_is_not_listed(w):
    w["db"].vendor_returns.insert_one({"_id": "VR-SAVING", "return_id": "VR-SAVING", "status": "saving",
                                       "store_id": STORE, "created_at": "2026-10-19"})
    listed = w["as_"](MANAGER).get("/vendor-returns/", params={"store_id": STORE}).json()
    assert [r["return_id"] for r in listed["returns"]] == []


def test_a_release_never_puts_back_a_piece_another_return_now_holds(w):
    unit(w, "U1")
    rid = create(w, line(stock_ids=["U1"])).json()["return_id"]
    # The piece is now held by another return (e.g. relinked since).
    w["db"].stock_units.update_one({"stock_id": "U1"}, {"$set": {"rtv_vendor_id": "VR-OTHER"}})
    vr._release_pieces(w["db"], rid, ["U1"], {"U1"}, "u-mgr", {})
    assert state(w, "U1")["status"] == "QUARANTINED" and state(w, "U1")["rtv_vendor_id"] == "VR-OTHER"


def _second_registration(w):
    """A shop of another GSTIN (BV-PUN-X) and a shop under the SAME GSTIN
    as the returning shop (BV-ONL-X), with a receipt at each."""
    w["db"].entities.insert_one({"entity_id": "ENT-MH", "legal_name": "MH Co",
                                 "gstins": [{"state_code": "27", "gstin": "27AABCB1234C1ZT"}]})
    w["db"].stores.insert_many([
        {"store_id": "BV-PUN-X", "store_name": "BV Pune", "state_code": "27", "entity_id": "ENT-MH",
         "gstin": "27AABCB1234C1ZT", "is_active": True},
        {"store_id": "BV-ONL-X", "store_name": "BV Online", "state_code": "20", "entity_id": ENTITY,
         "gstin": SHOP_GSTIN, "is_active": True},
    ])
    w["db"].grns.insert_many([{"grn_id": "GRN-PUN", "vendor_id": DEALER, "store_id": "BV-PUN-X"},
                              {"grn_id": "GRN-ONL", "vendor_id": DEALER, "store_id": "BV-ONL-X"}])


def test_a_piece_bought_on_another_gstin_goes_back_from_there(w):
    _second_registration(w)
    unit(w, "PUN1", grn_id="GRN-PUN")   # moved here by a transfer
    unit(w, "ONL1", grn_id="GRN-ONL")
    refused = create(w, line(stock_ids=["PUN1"]))
    assert refused.status_code == 422
    assert "bought on BV Pune's GSTIN - transfer it back there first" in refused.json()["detail"]
    assert state(w, "PUN1")["status"] == "AVAILABLE"
    same_gstin = create(w, line(stock_ids=["ONL1"]))
    assert same_gstin.status_code == 201, same_gstin.text


def test_a_typed_line_whose_pieces_are_all_on_other_returns_says_so(w):
    unit(w, "U1")
    create(w, line(stock_ids=["U1"]))
    typed = create(w, line(product="x", name="Acme Aviator 5420"), notes=NOTE)
    assert typed.status_code == 422
    assert "already on another supplier return" in typed.json()["detail"]


def test_the_unit_list_marks_pieces_already_on_an_open_return(w):
    unit(w, "U1")
    unit(w, "U2")
    rid = create(w, line(stock_ids=["U1"])).json()["return_id"]
    units = w["as_"](MANAGER).get("/inventory/units", params={"product_id": FRAME, "store_id": STORE}).json()["units"]
    marks = {u["stock_id"]: u["on_supplier_return"] for u in units}
    assert marks == {"U1": rid, "U2": None}


def _rejection_draft(w, status="cancelled", unit_status="QUARANTINED"):
    w["db"].grns.insert_one({"grn_id": "GRN-R", "vendor_id": DEALER, "store_id": STORE})
    w["db"].vendor_returns.insert_one({
        "return_id": "VR-GRN-R", "status": status, "receipt_rejection": True, "store_id": STORE,
        "vendor_id": DEALER, "stock_ids": ["REJ1"], "return_type": "credit_note",
        "items": [{"product_id": FRAME, "product_name": "Frame", "quantity": 1, "reason": "damaged"}],
        "debit_note_draft_status": "CANCELLED" if status == "cancelled" else "DRAFT"})
    unit(w, "REJ1", status=unit_status, receipt_disposition="REJECTED", rtv_vendor_id="VR-GRN-R",
         grn_id="GRN-R")


def test_a_cancelled_rejection_draft_piece_can_go_on_a_new_return_to_its_supplier(w):
    _rejection_draft(w)
    other = w["as_"](MANAGER).post("/vendor-returns/", json={
        "vendor_id": SHARMA, "vendor_name": "Sharma", "store_id": STORE, "return_type": "credit_note",
        "items": [line(stock_ids=["REJ1"])]})
    assert other.status_code == 422 and "different supplier" in other.json()["detail"]
    made = create(w, line(stock_ids=["REJ1"]))
    assert made.status_code == 201, made.text
    assert state(w, "REJ1")["rtv_vendor_id"] == made.json()["return_id"]
    lifted = w["as_"](MANAGER).patch("/inventory/stock/REJ1/lift-quarantine",
                                     json={"lift_reason": "Looks fine now"})
    assert lifted.status_code == 409 and lifted.json()["detail"]["code"] == "receipt_rejected"
    reopened = move(w, "VR-GRN-R", "draft")
    assert reopened.status_code == 409 and made.json()["return_id"] in reopened.json()["detail"]
    assert returned(w, "VR-GRN-R")["status"] == "cancelled"


def test_a_rejection_draft_with_a_piece_already_shipped_cannot_be_cancelled(w):
    _rejection_draft(w, status="approved", unit_status="RTV")
    refused = move(w, "VR-GRN-R", "cancelled")
    assert refused.status_code == 409 and "already left stock" in refused.json()["detail"]
    assert returned(w, "VR-GRN-R")["status"] == "approved"


def test_a_save_that_never_finished_stops_holding_its_pieces(w):
    # A crashed request left its claim 'saving' with the piece linked to it.
    w["db"].vendor_returns.insert_one({"_id": "VR-DEAD", "return_id": "VR-DEAD", "status": "saving",
                                       "store_id": STORE, "claimed_at": "2026-01-01T00:00:00.000000"})
    unit(w, "U1", status="QUARANTINED", rtv_vendor_id="VR-DEAD")
    made = create(w, line(stock_ids=["U1"]))
    assert made.status_code == 201, made.text
    assert state(w, "U1")["rtv_vendor_id"] == made.json()["return_id"]
    # A save still in progress holds them.
    w["db"].vendor_returns.insert_one({"_id": "VR-LIVE", "return_id": "VR-LIVE", "status": "saving",
                                       "store_id": STORE, "claimed_at": "9999-01-01T00:00:00.000000"})
    unit(w, "U2", status="QUARANTINED", rtv_vendor_id="VR-LIVE")
    held = create(w, line(stock_ids=["U2"]))
    assert held.status_code == 422 and "VR-LIVE" in held.json()["detail"]


def test_a_piece_from_another_shop_with_no_gstin_on_file_is_refused_loudly(w):
    # Another shop, no GSTIN on file: IMS cannot tell the registration -> refuse.
    # (A shop whose company holds no registration for its state.)
    w["db"].stores.insert_one({"store_id": "BV-NOGST", "store_name": "BV No GST", "state_code": "27",
                               "entity_id": ENTITY, "is_active": True})
    w["db"].grns.insert_many([{"grn_id": "GRN-NOGST", "vendor_id": DEALER, "store_id": "BV-NOGST"},
                              {"grn_id": "GRN-HERE", "vendor_id": DEALER, "store_id": STORE}])
    unit(w, "NG1", grn_id="GRN-NOGST")
    refused = create(w, line(stock_ids=["NG1"]))
    assert refused.status_code == 422
    assert refused.json()["detail"] == (
        "GSTIN missing for BV No GST - set it in Settings before returning this piece")
    assert state(w, "NG1")["status"] == "AVAILABLE"
    # The same shop needs no GSTIN check at all, even with none on file.
    w["db"].stores.update_one({"store_id": STORE}, {"$unset": {"gstin": "", "state_code": "", "state": ""}})
    unit(w, "HERE1", grn_id="GRN-HERE")
    same_shop = create(w, line(stock_ids=["HERE1"]))
    assert same_shop.status_code == 201, same_shop.text
