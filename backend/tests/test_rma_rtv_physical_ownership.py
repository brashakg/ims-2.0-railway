"""L2-RMA-F01 + L2-RTV-F01: who physically owns the pieces going back.

RMA-F01: a stock-backed vendor RMA (a line for a product this shop holds
pieces of) could be raised, dispatched, credited and closed while its pieces
stayed AVAILABLE (sellable). The RMA now goes back only on the pieces of the
vendor return it links (return_id): that return moves them (QUARANTINED ->
RTV, once), the RMA never moves a piece. Codex 11-06: "holds" means the shop
still owns the piece, sellable or not (a reserved one is refused, never moved);
a return and its RMA have one financial owner for the same pieces; and each
line says whose piece it is (owner ruling 2026-11-06: customer_owned plus a
customer or order reference, no shop stock touched). An unticked line the shop
holds no piece of is unchanged.

RTV-F01: a cancel whose piece could not be put back left it QUARANTINED with
its link cleared, and a retry was a 400. The piece now stays linked to the
cancelled return, the cancel says so, and Cancel again finishes it -- or says
it already did. Real routes, one mongomock database.
"""
from collections import Counter
from datetime import datetime
from uuid import uuid4

import pytest

from tests.test_receipt_rejection_handoff_a08 import app_world, world  # noqa: F401
from tests.test_receipt_without_po import ACCOUNTANT, ADMIN, DEALER, FRAME, MANAGER, SHOP_B, STORE
from tests.test_vendor_return_pieces import (  # noqa: F401
    create, events, line, move, on_hand, returned, state, unit, w,
)
from rma_credit_fixtures import credit_payload
from api.routers import vendor_rma as rma_router
from api.routers import vendor_returns as vr
from api.routers.purchase_recon import _pending_return_credit_notes
from api.services import item_events as ie
from api.services import rma_supplier_credit as rma_credit
from api.services.supplier_credit_reads import confirmed_ap_rows
from api.services.vendor_rma import VendorRMAEngine

OFF_INVENTORY = "P-CUSTOMER-OWN"


@pytest.fixture
def rw(w, monkeypatch):  # noqa: F811
    monkeypatch.setattr(rma_router, "_get_db", lambda: w["db"])
    w["as_"](MANAGER).app.include_router(rma_router.router, prefix="/vendor-rma")
    return w


def statuses(w):
    return Counter(u["status"] for u in w["db"].stock_units.find({"product_id": FRAME}))


def posted(w):
    rows = list(w["db"].supplier_credit_confirmations.find({"posting_status": "POSTED"}))
    return len(rows), sum(r["received_paise"] for r in rows)


def raise_rma(w, qty=2, product=FRAME, user=MANAGER, **extra):
    body = {"vendor_id": DEALER, "vendor_name": "Bank More", "store_id": STORE,
            "lines": [{"product_id": product, "product_name": "Acme Aviator 5420" if product == FRAME
                       else "Customer's own frame", "quantity": qty, "reason": "DEFECTIVE"}], **extra}
    return w["as_"](user).post("/vendor-rma/", json=body)


def walk(w, rma_id, amount=1000.0):
    """authorize -> price -> dispatch -> credit -> close; the status codes."""
    http = w["as_"]
    out = [http(MANAGER).post(f"/vendor-rma/{rma_id}/authorize", json={"vendor_rma_number": "VR-1"}).status_code,
           http(ACCOUNTANT).put(f"/vendor-rma/{rma_id}/prices", json={"unit_costs": [amount / 2]}).status_code,
           http(MANAGER).post(f"/vendor-rma/{rma_id}/dispatch", json={"carrier": "DTDC", "awb": "AWB1"}).status_code]
    note = credit_payload(w["db"], rma_id, amount=amount)
    out.append(http(ACCOUNTANT).post(f"/vendor-rma/{rma_id}/credit-note", json=note).status_code)
    out.append(http(MANAGER).post(f"/vendor-rma/{rma_id}/close", json={}).status_code)
    return out, note


def shipped_return(w, *sids):
    for sid in sids:
        unit(w, sid)
    rid = create(w, line(qty=len(sids), stock_ids=list(sids))).json()["return_id"]
    assert move(w, rid, "approved").status_code == 200
    assert move(w, rid, "shipped").status_code == 200
    return rid


# --------------------------------------------------------------------------
# L2-RMA-F01
# --------------------------------------------------------------------------

def test_a_stock_backed_rma_without_its_pieces_is_refused_and_they_stay_on_sale(rw):
    unit(rw, "U1")
    unit(rw, "U2")
    refused = raise_rma(rw)
    assert refused.status_code == 422, refused.text
    assert "scan or pick" in refused.json()["detail"]
    assert rw["db"].vendor_rmas.count_documents({}) == 0
    assert statuses(rw) == Counter({"AVAILABLE": 2}) and on_hand(rw) == 2
    assert posted(rw) == (0, 0)


@pytest.mark.parametrize("door", ["dispatch", "credit-note", "close"])
def test_an_unlinked_rma_raised_before_the_rule_cannot_dispatch_credit_or_close(rw, door):
    unit(rw, "U1")
    unit(rw, "U2")
    eng = VendorRMAEngine(rw["db"])
    rma_id = eng.raise_rma(vendor_id=DEALER, vendor_name="Bank More", store_id=STORE, created_by="old",
                           lines=[{"product_id": FRAME, "product_name": "Acme Aviator 5420",
                                   "quantity": 2, "reason": "DEFECTIVE", "unit_cost": 500}])["rma_id"]
    eng.authorize(rma_id, vendor_rma_number="VR-1", actor="old")
    http = rw["as_"]
    if door == "dispatch":
        res = http(MANAGER).post(f"/vendor-rma/{rma_id}/dispatch", json={"carrier": "DTDC", "awb": "AWB1"})
        want = "AUTHORIZED"
    else:
        eng.dispatch(rma_id, carrier="DTDC", awb="AWB1", dispatch_date=None, actor="old")
        note = credit_payload(rw["db"], rma_id, amount=1000.0)
        if door == "close":
            # A credit recorded before the rule: the close is refused all the same.
            eng.record_credit_note(rma_id, credit_note_number="OLD-CN", received_amount=1000, actor="old")
            res = http(MANAGER).post(f"/vendor-rma/{rma_id}/close", json={})
            want = "CREDIT_RECEIVED"
        else:
            res = http(ACCOUNTANT).post(f"/vendor-rma/{rma_id}/credit-note", json=note)
            want = "DISPATCHED"
    assert res.status_code == 409, res.text
    assert "scan or pick" in str(res.json()["detail"])
    assert rw["db"].vendor_rmas.find_one({"rma_id": rma_id})["status"] == want
    assert posted(rw) == (0, 0)
    assert statuses(rw) == Counter({"AVAILABLE": 2})


def test_a_linked_shipped_return_carries_the_rma_and_no_piece_leaves_twice(rw):
    rid = shipped_return(rw, "U1", "U2")
    assert statuses(rw) == Counter({"RTV": 2}) and on_hand(rw) == 0
    before = {sid: events(rw, sid) for sid in ("U1", "U2")}
    made = raise_rma(rw, return_id=rid)
    assert made.status_code == 201, made.text
    codes, note = walk(rw, made.json()["rma_id"])
    assert codes == [200, 200, 200, 200, 200]
    rma = rw["db"].vendor_rmas.find_one({"rma_id": made.json()["rma_id"]})
    assert rma["status"] == "CLOSED" and rma["received_credit_paise"] == 100000
    assert posted(rw) == (1, 100000)
    # The RMA moved nothing: each piece left once, through the return.
    assert {sid: events(rw, sid) for sid in ("U1", "U2")} == before
    assert statuses(rw) == Counter({"RTV": 2})
    # A lost reply: the same note again is the original, nothing posts twice.
    again = rw["as_"](ACCOUNTANT).post(f"/vendor-rma/{rma['rma_id']}/credit-note", json=note)
    assert again.status_code == 200 and again.json()["idempotent"] is True
    assert posted(rw) == (1, 100000)


def test_hq_raises_on_a_set_aside_return_and_dispatch_waits_for_its_ship(rw):
    unit(rw, "U1")
    unit(rw, "U2")
    rid = create(rw, line(qty=2, stock_ids=["U1", "U2"])).json()["return_id"]
    assert move(rw, rid, "approved").status_code == 200
    made = raise_rma(rw, user=ADMIN, return_id=rid)
    assert made.status_code == 201, made.text
    rma_id = made.json()["rma_id"]
    http = rw["as_"]
    assert http(MANAGER).post(f"/vendor-rma/{rma_id}/authorize", json={"vendor_rma_number": "VR-1"}).status_code == 200
    early = http(MANAGER).post(f"/vendor-rma/{rma_id}/dispatch", json={"carrier": "DTDC", "awb": "AWB1"})
    assert early.status_code == 409 and f"Ship vendor return {rid}" in early.json()["detail"]
    assert statuses(rw) == Counter({"QUARANTINED": 2})
    assert move(rw, rid, "shipped").status_code == 200
    late = http(MANAGER).post(f"/vendor-rma/{rma_id}/dispatch", json={"carrier": "DTDC", "awb": "AWB1"})
    assert late.status_code == 200, late.text
    assert statuses(rw) == Counter({"RTV": 2})
    # The return that went back with the RMA can no longer be cancelled onto the shelf.
    assert move(rw, rid, "cancelled").status_code == 400


def test_a_mismatched_or_unusable_return_is_refused(rw):
    rid = shipped_return(rw, "U1", "U2")
    assert raise_rma(rw, qty=1, return_id=rid).status_code == 422          # quantity
    assert raise_rma(rw, return_id="VR-NOPE").status_code == 422           # no such return
    other_vendor = raise_rma(rw, return_id=rid, vendor_id="V-OTHER")
    assert other_vendor.status_code == 422                                 # supplier
    other_shop = raise_rma(rw, user=ADMIN, return_id=rid, store_id=SHOP_B)
    assert other_shop.status_code == 422 and "another shop" in other_shop.json()["detail"]
    unit(rw, "C1")
    cancelled = create(rw, line(stock_ids=["C1"])).json()["return_id"]
    assert move(rw, cancelled, "cancelled").status_code == 200
    assert raise_rma(rw, qty=1, return_id=cancelled).status_code == 422   # cancelled
    first = raise_rma(rw, return_id=rid)
    assert first.status_code == 201
    second = raise_rma(rw, return_id=rid)
    assert second.status_code == 422 and "another RMA" in second.json()["detail"]
    assert rw["db"].vendor_rmas.count_documents({}) == 1


def test_a_line_for_something_ims_holds_no_piece_of_is_unchanged(rw):
    unit(rw, "U1")  # a frame on the shelf is not this line's product
    made = raise_rma(rw, product=OFF_INVENTORY)
    assert made.status_code == 201, made.text
    codes, _ = walk(rw, made.json()["rma_id"])
    assert codes == [200, 200, 200, 200, 200]
    assert posted(rw) == (1, 100000)
    assert statuses(rw) == Counter({"AVAILABLE": 1})


# --------------------------------------------------------------------------
# L2-RTV-F01
# --------------------------------------------------------------------------

def _refuse_put_back(monkeypatch, sid):
    real = vr._ie.record_event_atomic

    def refused(db, **kw):
        if kw["stock_id"] == sid and kw["to_state"] is ie.StockState.AVAILABLE:
            return False, None
        return real(db, **kw)

    monkeypatch.setattr(vr._ie, "record_event_atomic", refused)
    return real


def test_a_cancel_that_could_not_put_a_piece_back_keeps_it_and_cancel_again_finishes(rw, monkeypatch):
    unit(rw, "U1")
    unit(rw, "U2")
    rid = create(rw, line(qty=2, stock_ids=["U1", "U2"])).json()["return_id"]
    real = _refuse_put_back(monkeypatch, "U2")
    held = move(rw, rid, "cancelled")
    assert held.status_code == 409, held.text
    assert "BCU2" in held.json()["detail"]
    assert returned(rw, rid)["status"] == "cancelled"
    assert (state(rw, "U1")["status"], state(rw, "U1")["rtv_vendor_id"]) == ("AVAILABLE", None)
    # Still off the shelf and still owned by the cancelled return: a retry finds it.
    assert (state(rw, "U2")["status"], state(rw, "U2")["rtv_vendor_id"]) == ("QUARANTINED", rid)
    monkeypatch.setattr(vr._ie, "record_event_atomic", real)
    # HQ presses Cancel again (any return role may; the same-shop manager below).
    done = rw["as_"](ADMIN).patch(f"/vendor-returns/{rid}/status", json={"status": "cancelled"})
    assert done.status_code == 200, done.text
    assert (state(rw, "U2")["status"], state(rw, "U2")["rtv_vendor_id"]) == ("AVAILABLE", None)
    assert statuses(rw) == Counter({"AVAILABLE": 2}) and on_hand(rw) == 2
    # A lost reply: cancelling again says it is done and moves nothing.
    ledger = {sid: events(rw, sid) for sid in ("U1", "U2")}
    again = move(rw, rid, "cancelled")
    assert again.status_code == 200, again.text
    assert {sid: events(rw, sid) for sid in ("U1", "U2")} == ledger
    for sid in ("U1", "U2"):
        assert [e[0] for e in ledger[sid]] == ["quarantine.in", "quarantine.out"]


def test_a_cancel_retry_never_takes_a_piece_another_return_now_holds(rw, monkeypatch):
    unit(rw, "U1")
    rid = create(rw, line(stock_ids=["U1"])).json()["return_id"]
    real = _refuse_put_back(monkeypatch, "U1")
    assert move(rw, rid, "cancelled").status_code == 409
    monkeypatch.setattr(vr._ie, "record_event_atomic", real)
    # The piece, still set aside, goes on a fresh return before anyone retries.
    other = create(rw, line(stock_ids=["U1"]))
    assert other.status_code == 201, other.text
    assert move(rw, rid, "cancelled").status_code == 200
    assert (state(rw, "U1")["status"], state(rw, "U1")["rtv_vendor_id"]) == (
        "QUARANTINED", other.json()["return_id"])


def test_a_cancel_retry_leaves_a_piece_another_flow_set_aside_since(rw, monkeypatch):
    unit(rw, "U1")
    rid = create(rw, line(stock_ids=["U1"])).json()["return_id"]
    real = _refuse_put_back(monkeypatch, "U1")
    assert move(rw, rid, "cancelled").status_code == 409
    monkeypatch.setattr(vr._ie, "record_event_atomic", real)
    # The Quarantine screen lifts it and sets it aside again (its link stays).
    for frm, to, kind in ((ie.StockState.QUARANTINED, ie.StockState.AVAILABLE, ie.ItemEventType.QUARANTINE_OUT),
                          (ie.StockState.AVAILABLE, ie.StockState.QUARANTINED, ie.ItemEventType.QUARANTINE_IN)):
        ok, _ = real(rw["db"], event_type=kind, actor_id="u-mgr", stock_id="U1", from_state=frm,
                     to_state=to, store_id=STORE, product_id=FRAME, source_type="QUARANTINE",
                     source_id="Q-1", payload={})
        assert ok
    ledger = events(rw, "U1")
    assert move(rw, rid, "cancelled").status_code == 200
    assert state(rw, "U1")["status"] == "QUARANTINED" and events(rw, "U1") == ledger


def test_a_normal_cancel_and_its_retry_put_each_piece_back_once(rw):
    unit(rw, "U1")
    rid = create(rw, line(stock_ids=["U1"])).json()["return_id"]
    assert move(rw, rid, "cancelled").status_code == 200
    again = move(rw, rid, "cancelled")
    assert again.status_code == 200, again.text
    assert state(rw, "U1")["status"] == "AVAILABLE" and state(rw, "U1")["rtv_vendor_id"] is None
    assert [e[0] for e in events(rw, "U1")] == ["quarantine.in", "quarantine.out"]


# --------------------------------------------------------------------------
# Codex 11-06, P1: stock the shop still owns, whether or not it is sellable
# --------------------------------------------------------------------------

def old_rma(w, product=FRAME, name="Acme Aviator 5420", qty=2, **extra):
    """An RMA that never met the raise rule (engine direct): a live historical row."""
    eng = VendorRMAEngine(w["db"])
    rma_id = eng.raise_rma(vendor_id=DEALER, vendor_name="Bank More", store_id=STORE, created_by="old",
                           lines=[{"product_id": product, "product_name": name, "quantity": qty,
                                   "reason": "DEFECTIVE", "unit_cost": 500}], **extra)["rma_id"]
    eng.authorize(rma_id, vendor_rma_number="VR-1", actor="old")
    return eng, rma_id


@pytest.mark.parametrize("held", ["RESERVED", "reserved ", "DAMAGED", "UNDER_AUDIT", "BLIND_COUNT"])
def test_a_rma_for_pieces_the_shop_still_owns_is_refused_however_they_are_held(rw, held):
    unit(rw, "U1", status=held)
    unit(rw, "U2", status=held)
    refused = raise_rma(rw, grn_id="GRN-1")
    assert refused.status_code == 422, refused.text
    assert rw["db"].vendor_rmas.count_documents({}) == 0
    assert statuses(rw) == Counter({held: 2})
    assert events(rw, "U1") == [] and posted(rw) == (0, 0)


def test_the_refusal_of_a_reserved_piece_says_release_it_first(rw):
    unit(rw, "U1", status="RESERVED")
    refused = raise_rma(rw, qty=1)
    assert refused.status_code == 422
    assert "reserved for an order" in refused.json()["detail"]


@pytest.mark.parametrize("door", ["dispatch", "credit-note", "close"])
def test_an_old_rma_over_reserved_pieces_cannot_pass_a_later_door(rw, door):
    unit(rw, "U1", status="RESERVED")
    unit(rw, "U2", status="RESERVED")
    eng, rma_id = old_rma(rw)
    http = rw["as_"]
    if door == "dispatch":
        res = http(MANAGER).post(f"/vendor-rma/{rma_id}/dispatch", json={"carrier": "DTDC", "awb": "AWB1"})
    else:
        eng.dispatch(rma_id, carrier="DTDC", awb="AWB1", dispatch_date=None, actor="old")
        note = credit_payload(rw["db"], rma_id, amount=1000.0)
        if door == "close":
            eng.record_credit_note(rma_id, credit_note_number="OLD-CN", received_amount=1000, actor="old")
            res = http(MANAGER).post(f"/vendor-rma/{rma_id}/close", json={})
        else:
            res = http(ACCOUNTANT).post(f"/vendor-rma/{rma_id}/credit-note", json=note)
    assert res.status_code == 409, res.text
    assert posted(rw) == (0, 0)
    assert statuses(rw) == Counter({"RESERVED": 2}) and events(rw, "U1") == []


def test_a_typed_vendor_return_line_cannot_skip_reserved_pieces_either(w):
    unit(w, "U1", status="RESERVED")
    typed = create(w, line(), notes="Typed, no piece named")
    assert typed.status_code == 422, typed.text
    assert "reserved for an order" in typed.json()["detail"]
    assert w["db"].vendor_returns.count_documents({"status": "created"}) == 0
    assert state(w, "U1")["status"] == "RESERVED"


def test_once_released_the_same_pieces_go_back_and_reconcile_end_to_end(rw):
    unit(rw, "U1", status="RESERVED")
    unit(rw, "U2", status="RESERVED")
    assert raise_rma(rw).status_code == 422
    rw["db"].stock_units.update_many({"product_id": FRAME}, {"$set": {"status": "AVAILABLE"}})
    rid = create(rw, line(qty=2, stock_ids=["U1", "U2"])).json()["return_id"]
    assert move(rw, rid, "approved").status_code == 200
    assert move(rw, rid, "shipped").status_code == 200
    made = raise_rma(rw, return_id=rid)
    assert made.status_code == 201, made.text
    codes, _ = walk(rw, made.json()["rma_id"])
    assert codes == [200, 200, 200, 200, 200]
    assert statuses(rw) == Counter({"RTV": 2}) and posted(rw) == (1, 100000)
    for sid in ("U1", "U2"):
        assert [e[0] for e in events(rw, sid)] == ["quarantine.in", "quarantine.out"]
