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
from api.services.return_provenance import ReturnProvenanceError, resolve_return_provenance
from api.services.supplier_credit_evidence import _goods_known, source_key
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


@pytest.mark.parametrize("held", ["RESERVED", "reserved ", "DAMAGED", "UNDER_AUDIT", "BLIND_COUNT", "TRANSFERRED"])
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


@pytest.mark.parametrize("held", ["RESERVED", "TRANSFERRED"])
@pytest.mark.parametrize("door", ["dispatch", "credit-note", "close"])
def test_an_old_rma_over_reserved_pieces_cannot_pass_a_later_door(rw, door, held):
    unit(rw, "U1", status=held)
    unit(rw, "U2", status=held)
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
    assert statuses(rw) == Counter({held: 2}) and events(rw, "U1") == []


@pytest.mark.parametrize("held, words", [("RESERVED", "reserved for an order"),
                                         ("TRANSFERRED", "in transit to another shop")])
def test_a_typed_vendor_return_line_cannot_skip_reserved_pieces_either(w, held, words):
    unit(w, "U1", status=held)
    typed = create(w, line(), notes="Typed, no piece named")
    assert typed.status_code == 422, typed.text
    assert words in typed.json()["detail"]
    assert w["db"].vendor_returns.count_documents({"status": "created"}) == 0
    assert state(w, "U1")["status"] == held


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


# --------------------------------------------------------------------------
# Codex 11-06, P1: one physical return, one financial owner
# --------------------------------------------------------------------------

def bought(w, *sids, qty=4):
    """A coherent purchase: `qty` accepted at Rs 500 on one zero-tax bill; the
    named pieces carry that receipt."""
    db = w["db"]
    db.grns.insert_one({"grn_id": "G-OWN", "store_id": STORE, "vendor_id": DEALER, "status": "ACCEPTED",
                        "items": [{"product_id": FRAME, "accepted_qty": qty}]})
    db.vendor_bills.insert_one({
        "bill_id": "B-OWN", "grn_id": "G-OWN", "store_id": STORE, "vendor_id": DEALER, "status": "UNPAID",
        "received": True, "doc_type": "PURCHASE_INVOICE", "bill_number": "INV-OWN",
        "bill_date": datetime(2026, 1, 1), "recipient_entity_id": "E-OWN",
        "recipient_gstin": "20AAAAA0000A1Z5", "vendor_gstin": "20BBBBB0000B1Z5",
        "lines": [{"product_id": FRAME, "qty": qty, "taxable": qty * 500, "cgst": 0, "sgst": 0, "igst": 0,
                   "gst_rate": 0, "line_total": qty * 500}],
        "taxable_amount": qty * 500, "tax_amount": 0, "cgst_total": 0, "sgst_total": 0, "igst_total": 0})
    db.entities.insert_one({"entity_id": "E-OWN", "gstins": [{"gstin": "20AAAAA0000A1Z5"}]})
    for sid in sids:
        unit(w, sid, grn_id="G-OWN", source_type="GRN", source_id="G-OWN")


def received_return(w, *sids):
    rid = create(w, line(qty=len(sids), stock_ids=list(sids))).json()["return_id"]
    for step in ("approved", "shipped", "received_by_vendor"):
        assert move(w, rid, step).status_code == 200, step
    return rid


def linked_rma(w, rid, qty=2):
    made = raise_rma(w, qty=qty, return_id=rid, grn_id="G-OWN")
    assert made.status_code == 201, made.text
    return to_the_supplier(w, made.json()["rma_id"])


def to_the_supplier(w, rma_id):
    http = w["as_"]
    assert http(MANAGER).post(f"/vendor-rma/{rma_id}/authorize", json={"vendor_rma_number": "VR-1"}).status_code == 200
    assert http(ACCOUNTANT).put(f"/vendor-rma/{rma_id}/prices", json={"unit_costs": [500]}).status_code == 200
    assert http(MANAGER).post(f"/vendor-rma/{rma_id}/dispatch", json={"carrier": "DTDC", "awb": "AWB1"}).status_code == 200
    return rma_id


def note_for(w, path, amount, number, route):
    """The form Accounts submits: preview, then one allocation on its source."""
    http = w["as_"](ACCOUNTANT)
    preview = http.post(f"{path}/credit-preview", json={})
    assert preview.status_code == 200 and preview.json()["source_status"] == "RESOLVED", preview.text
    body = {"credit_note_number": number, "credit_note_date": "2026-10-18", "idempotency_key": str(uuid4()),
            "received_amount": amount, "source_selections": preview.json()["source_selections"],
            "allocations": [{"source_key": preview.json()["sources"][0]["source_key"],
                             "taxable": amount, "cgst": 0, "sgst": 0, "igst": 0}]}
    return body, http.post(f"{path}/{route}", json=body)


def rtv_note(w, rid, amount, number):
    return note_for(w, f"/vendor-returns/{rid}", amount, number, "credit-confirmations")


def rma_note(w, rma_id, amount, number):
    return note_for(w, f"/vendor-rma/{rma_id}", amount, number, "credit-note")


def credited(w):
    """What the supplier's account actually carries, read from the AP rows."""
    return sum(r["amount_paise"] for r in confirmed_ap_rows(w["db"], vendor_id=DEALER))


def test_a_linked_rma_owns_the_credit_and_the_return_door_refuses_before_writing(rw):
    bought(rw, "U1", "U2", "U3", "U4")
    rid = received_return(rw, "U1", "U2")
    rma_id = linked_rma(rw, rid)
    _, second = rtv_note(rw, rid, 1000, "CN-RTV")
    assert second.status_code == 409, second.text
    assert rma_id in second.json()["detail"]
    # Nothing was written, so the note number is still free for the right door.
    assert rw["db"].supplier_credit_confirmations.count_documents({}) == 0
    assert rw["db"].supplier_credit_note_identities.count_documents({}) == 0
    _, res = rma_note(rw, rma_id, 1000, "CN-RTV")
    assert res.status_code == 200, res.text
    assert posted(rw) == (1, 100000) and credited(rw) == 100000
    assert statuses(rw) == Counter({"RTV": 2, "AVAILABLE": 2})
    assert sum(len(events(rw, s)) for s in ("U1", "U2", "U3", "U4")) == 4


def in_transit(w, *sids):
    """transfers._claim_packed: the piece leaves the shelf, keeps its shop."""
    w["db"].stock_units.update_many({"stock_id": {"$in": list(sids)}}, {"$set": {
        "status": "TRANSFERRED", "transfer_id": "TR-1", "transfer_to_store_id": SHOP_B}})


def test_pieces_on_the_way_to_a_sister_shop_are_still_the_chains_and_cannot_be_credited(rw):
    """Codex's request A: a fresh receipt, both pieces shipped to the sister shop."""
    bought(rw, "U1", "U2", qty=2)
    in_transit(rw, "U1", "U2")
    refused = raise_rma(rw, grn_id="G-OWN")
    assert refused.status_code == 422, refused.text
    assert "in transit to another shop" in refused.json()["detail"]
    assert rw["db"].vendor_rmas.count_documents({}) == 0
    assert posted(rw) == (0, 0) and credited(rw) == 0
    assert statuses(rw) == Counter({"TRANSFERRED": 2}) and events(rw, "U1") == []


def test_the_reported_double_credit_with_the_other_pieces_on_the_way_is_refused(rw):
    """Codex's request B: RTV credits two, the other two are in transit."""
    bought(rw, "U1", "U2", "U3", "U4")
    rid = received_return(rw, "U1", "U2")
    rw["as_"](ACCOUNTANT).put(f"/vendor-returns/{rid}/prices", json={"unit_prices": [500]})
    _, first = rtv_note(rw, rid, 1000, "CN-RTV")
    assert first.status_code == 200, first.text
    in_transit(rw, "U3", "U4")
    assert raise_rma(rw, grn_id="G-OWN").status_code == 422
    assert rw["db"].vendor_rmas.count_documents({}) == 0
    assert posted(rw) == (1, 100000) and credited(rw) == 100000
    assert statuses(rw) == Counter({"RTV": 2, "TRANSFERRED": 2})


def sold_rest(w, *sids):
    w["db"].stock_units.update_many({"stock_id": {"$in": list(sids)}}, {"$set": {"status": "SOLD"}})


def test_an_unlinked_rma_cannot_claim_pieces_a_return_already_sent_back(rw):
    """Nothing is held, so only the return's RTV pieces show where the goods went."""
    bought(rw, "U1", "U2", "U3", "U4")
    rid = received_return(rw, "U1", "U2")
    sold_rest(rw, "U3", "U4")
    rw["as_"](ACCOUNTANT).put(f"/vendor-returns/{rid}/prices", json={"unit_prices": [500]})
    _, first = rtv_note(rw, rid, 1000, "CN-RTV")
    assert first.status_code == 200, first.text
    refused = raise_rma(rw, grn_id="G-OWN")
    assert refused.status_code == 422, refused.text
    assert rid in refused.json()["detail"] and "customer_owned" in refused.json()["detail"]
    assert rw["db"].vendor_rmas.count_documents({}) == 0
    assert posted(rw) == (1, 100000) and credited(rw) == 100000
    assert statuses(rw) == Counter({"RTV": 2, "SOLD": 2})


def test_it_is_refused_before_the_return_is_credited_too_and_the_return_keeps_its_door(rw):
    bought(rw, "U1", "U2", "U3", "U4")
    rid = received_return(rw, "U1", "U2")
    sold_rest(rw, "U3", "U4")
    assert raise_rma(rw, grn_id="G-OWN").status_code == 422
    rw["as_"](ACCOUNTANT).put(f"/vendor-returns/{rid}/prices", json={"unit_prices": [500]})
    _, own = rtv_note(rw, rid, 1000, "CN-RTV")
    assert own.status_code == 200, own.text
    assert posted(rw) == (1, 100000)


@pytest.mark.parametrize("door", ["dispatch", "credit-note"])
def test_an_old_unlinked_rma_cannot_take_money_for_pieces_a_return_sent_back(rw, door):
    bought(rw, "U1", "U2", "U3", "U4")
    eng, rma_id = old_rma(rw, grn_id="G-OWN")  # raised before the return shipped, through no door
    rid = received_return(rw, "U1", "U2")
    sold_rest(rw, "U3", "U4")
    http = rw["as_"]
    if door == "dispatch":
        res = http(MANAGER).post(f"/vendor-rma/{rma_id}/dispatch", json={"carrier": "DTDC", "awb": "AWB1"})
    else:
        eng.dispatch(rma_id, carrier="DTDC", awb="AWB1", dispatch_date=None, actor="old")
        res = http(ACCOUNTANT).post(f"/vendor-rma/{rma_id}/credit-note",
                                    json=credit_payload(rw["db"], rma_id, amount=1000.0))
    assert res.status_code == 409, res.text
    assert rid in str(res.json()["detail"])
    assert posted(rw) == (0, 0)


def test_an_old_credited_rma_can_still_close_closing_moves_no_money(rw):
    """It has no door left but close and can gain no link; the pieces have left."""
    bought(rw, "U1", "U2", "U3", "U4")
    eng, rma_id = old_rma(rw, grn_id="G-OWN")
    eng.dispatch(rma_id, carrier="DTDC", awb="AWB1", dispatch_date=None, actor="old")
    eng.record_credit_note(rma_id, credit_note_number="OLD-CN", received_amount=1000, actor="old")
    rid = received_return(rw, "U1", "U2")
    sold_rest(rw, "U3", "U4")
    res = rw["as_"](MANAGER).post(f"/vendor-rma/{rma_id}/close", json={})
    assert res.status_code == 200, res.text
    assert returned(rw, rid)["status"] == "received_by_vendor" and posted(rw) == (0, 0)


def test_a_customers_own_piece_passes_a_return_that_sent_the_shops_back(rw):
    rw["db"].customers.insert_one({"customer_id": "CUST-OWN-2", "store_id": STORE, "name": "A Customer"})
    bought(rw, "U1", "U2", qty=2)
    received_return(rw, "U1", "U2")
    made = rw["as_"](MANAGER).post("/vendor-rma/", json={
        "vendor_id": DEALER, "vendor_name": "Bank More", "store_id": STORE, "lines": [
            {"product_id": FRAME, "product_name": "Acme Aviator 5420", "quantity": 1, "reason": "WARRANTY",
             "customer_owned": True, "customer_id": "CUST-OWN-2"}]})
    assert made.status_code == 201, made.text


def test_pieces_another_supplier_took_back_or_no_return_sent_do_not_block_this_supplier(rw):
    """The rule is this supplier's return: another supplier's holds no credit
    from this one, and a Quarantine-screen RTV release has no other credit door."""
    unit(rw, "U1", status="RTV", rtv_vendor_id="VR-OTHER")
    unit(rw, "U2", status="RTV", rtv_vendor_id=DEALER)
    unit(rw, "U3", status="RTV")
    rw["db"].vendor_returns.insert_one({"return_id": "VR-OTHER", "vendor_id": "V-OTHER", "store_id": STORE,
                                        "status": "received_by_vendor"})
    made = raise_rma(rw, qty=1)
    assert made.status_code == 201, made.text


def test_another_shops_return_does_not_block_this_shops_line(rw):
    unit(rw, "U1", status="RTV", store=SHOP_B, rtv_vendor_id="VR-B")
    rw["db"].vendor_returns.insert_one({"return_id": "VR-B", "vendor_id": DEALER, "store_id": SHOP_B,
                                        "status": "received_by_vendor"})
    assert raise_rma(rw, qty=1).status_code == 201


def test_a_typed_return_line_cannot_claim_pieces_another_return_sent_back_either(rw):
    """The twin door: the same second credit through a typed vendor return."""
    bought(rw, "U1", "U2", "U3", "U4")
    rid = received_return(rw, "U1", "U2")
    sold_rest(rw, "U3", "U4")
    typed = create(rw, line(qty=2), notes="Typed, no piece named", grn_id="G-OWN")
    assert typed.status_code == 422, typed.text
    assert rid in typed.json()["detail"]
    assert rw["db"].vendor_returns.count_documents({}) == 1


def landed_at_b(w, *sids):
    """A received transfer: the piece is the sister shop's, its origin receipt stays."""
    w["db"].stock_units.update_many({"stock_id": {"$in": list(sids)}}, {"$set": {"store_id": SHOP_B}})


def test_pieces_landed_at_a_sister_shop_are_not_this_shops_to_credit(rw):
    """Codex's request A one step later: the receiving shop has them on sale."""
    bought(rw, "U1", "U2", qty=2)
    landed_at_b(rw, "U1", "U2")
    refused = raise_rma(rw, grn_id="G-OWN")
    assert refused.status_code == 422, refused.text
    assert "are now held at" in refused.json()["detail"]
    assert rw["db"].vendor_rmas.count_documents({}) == 0 and posted(rw) == (0, 0)
    assert statuses(rw) == Counter({"AVAILABLE": 2})


def test_leaving_the_receipt_off_finds_the_same_pieces(rw):
    bought(rw, "U1", "U2", qty=2)
    landed_at_b(rw, "U1", "U2")
    assert raise_rma(rw).status_code == 422


def test_a_return_shipped_from_the_sister_shop_is_not_credited_again_here(rw):
    """The double credit through two shops: bought here, sent back from there."""
    bought(rw, "U1", "U2", "U3", "U4")
    sold_rest(rw, "U3", "U4")
    landed_at_b(rw, "U1", "U2")
    rw["db"].vendor_returns.insert_one({"return_id": "VR-B", "vendor_id": DEALER, "store_id": SHOP_B,
                                        "status": "credit_issued", "stock_ids": ["U1", "U2"]})
    rw["db"].stock_units.update_many({"stock_id": {"$in": ["U1", "U2"]}},
                                     {"$set": {"status": "RTV", "rtv_vendor_id": "VR-B"}})
    refused = raise_rma(rw, grn_id="G-OWN")
    assert refused.status_code == 422, refused.text
    assert "VR-B" in refused.json()["detail"]
    assert rw["db"].vendor_rmas.count_documents({}) == 0


def test_a_typed_return_line_cannot_claim_pieces_landed_at_a_sister_shop_either(rw):
    bought(rw, "U1", "U2", qty=2)
    landed_at_b(rw, "U1", "U2")
    typed = create(rw, line(qty=2), notes="Typed, no piece named", grn_id="G-OWN")
    assert typed.status_code == 422, typed.text
    assert "are now held at" in typed.json()["detail"]


def test_a_sister_shops_own_purchase_is_not_this_shops_concern(rw):
    """Bought there, held there: a line here claims none of it, as before."""
    rw["db"].grns.insert_one({"grn_id": "G-B", "store_id": SHOP_B, "vendor_id": DEALER, "status": "ACCEPTED",
                              "items": [{"product_id": FRAME, "accepted_qty": 1}]})
    unit(rw, "U1", store=SHOP_B, grn_id="G-B", source_type="GRN", source_id="G-B")
    assert raise_rma(rw, qty=1).status_code == 201


def test_every_piece_sold_needs_the_tick_or_a_real_piece(rw):
    """Owner ruling 2026-11-06: bought piece by piece and every one sold, an
    unticked line takes no supplier credit: nothing the shop holds is going back."""
    bought(rw, "U1", "U2", qty=2)
    sold_rest(rw, "U1", "U2")
    refused = raise_rma(rw, grn_id="G-OWN")
    assert refused.status_code == 422, refused.text
    assert "been sold" in refused.json()["detail"] and "customer-owned" in refused.json()["detail"]
    assert rw["db"].vendor_rmas.count_documents({}) == 0
    assert statuses(rw) == Counter({"SOLD": 2}) and posted(rw) == (0, 0)


def test_a_return_with_its_own_credit_cannot_gain_an_rma(rw):
    bought(rw, "U1", "U2", "U3", "U4")
    rid = received_return(rw, "U1", "U2")
    rw["as_"](ACCOUNTANT).put(f"/vendor-returns/{rid}/prices", json={"unit_prices": [500]})
    _, first = rtv_note(rw, rid, 600, "CN-1")
    assert first.status_code == 200, first.text
    refused = raise_rma(rw, return_id=rid, grn_id="G-OWN")
    assert refused.status_code == 422, refused.text
    assert "Vendor Returns" in refused.json()["detail"]
    assert rw["db"].vendor_rmas.count_documents({}) == 0
    # The return's own partial notes carry on, and its replay stays a replay.
    body, rest = rtv_note(rw, rid, 400, "CN-2")
    assert rest.status_code == 200, rest.text
    again = rw["as_"](ACCOUNTANT).post(f"/vendor-returns/{rid}/credit-confirmations", json=body)
    assert again.status_code == 200 and again.json()["idempotent"] is True
    assert posted(rw) == (2, 100000) and credited(rw) == 100000
    assert returned(rw, rid)["status"] == "credit_issued"


def test_the_reported_double_credit_an_rma_that_predates_the_rule_is_refused(rw):
    """Codex's fixture: the RMA already sits on the return when the return's own
    credit posts (an old row, or one raised through no door)."""
    bought(rw, "U1", "U2", "U3", "U4")
    rid = received_return(rw, "U1", "U2")
    _, first = rtv_note(rw, rid, 1000, "CN-RTV")
    assert first.status_code == 200, first.text
    eng, rma_id = old_rma(rw, return_id=rid, grn_id="G-OWN")
    eng.dispatch(rma_id, carrier="DTDC", awb="AWB1", dispatch_date=None, actor="old")
    _, res = rma_note(rw, rma_id, 1000, "CN-RMA")
    assert res.status_code == 409, res.text
    assert posted(rw) == (1, 100000) and credited(rw) == 100000
    assert rw["db"].vendor_rmas.find_one({"rma_id": rma_id})["received_credit_paise"] == 0
    assert statuses(rw) == Counter({"RTV": 2, "AVAILABLE": 2})
    # The history is not rewritten: the first confirmation stands.
    assert rw["db"].supplier_credit_confirmations.count_documents({}) == 1


def test_the_piece_guard_holds_even_if_the_door_checks_are_passed_by(rw, monkeypatch):
    """Both doors at once: the atomic physical reservation is the last word."""
    bought(rw, "U1", "U2", "U3", "U4")
    rid = received_return(rw, "U1", "U2")
    rma_id = linked_rma(rw, rid)
    _, first = rma_note(rw, rma_id, 1000, "CN-RMA")
    assert first.status_code == 200, first.text
    rw["db"].vendor_returns.update_one({"return_id": rid}, {"$set": {"status": "received_by_vendor"}})
    monkeypatch.setattr(rma_credit, "return_door_problem", lambda *a: None)
    _, second = rtv_note(rw, rid, 1000, "CN-RTV")
    assert second.status_code == 422 and "reserved by another supplier return" in second.text
    assert posted(rw) == (1, 100000) and credited(rw) == 100000


def test_partial_notes_on_the_rma_reconcile_the_return_and_replay_once(rw):
    bought(rw, "U1", "U2", "U3", "U4")
    rid = received_return(rw, "U1", "U2")
    rma_id = linked_rma(rw, rid)
    body, first = rma_note(rw, rma_id, 600, "CN-1")
    assert first.status_code == 200, first.text
    shown = returned(rw, rid)
    assert shown["status"] == "received_by_vendor" and shown["confirmed_credit_amount"] == 600
    assert shown["credit_settled_on_rma"] == rma_id
    http = rw["as_"](ACCOUNTANT)
    again = http.post(f"/vendor-rma/{rma_id}/credit-note", json=body)
    assert again.status_code == 200 and again.json()["idempotent"] is True
    changed = http.post(f"/vendor-rma/{rma_id}/credit-note", json={**body, "received_amount": 601})
    assert changed.status_code == 409
    assert posted(rw) == (1, 60000) and credited(rw) == 60000
    assert rw["as_"](MANAGER).post(f"/vendor-rma/{rma_id}/close", json={}).status_code == 409
    _, rest = rma_note(rw, rma_id, 400, "CN-2")
    assert rest.status_code == 200, rest.text
    assert rw["as_"](MANAGER).post(f"/vendor-rma/{rma_id}/close", json={}).status_code == 200
    settled = returned(rw, rid)
    assert settled["status"] == "credit_issued" and settled["confirmed_credit_amount"] == 1000
    assert settled["credit_note_number"] == "CN-1, CN-2"
    assert posted(rw) == (2, 100000) and credited(rw) == 100000
    # Open credit-note lists stop asking for a credit the RMA already took.
    assert _pending_return_credit_notes(rw["db"], STORE) == []
    assert rw["as_"](ACCOUNTANT).put(f"/vendor-returns/{rid}/prices", json={"unit_prices": [1]}).status_code == 409


def old_pair(rw, monkeypatch):
    """A pre-rule pair: return R holds POSTED CN-RTV Rs 500 (partial, so still
    received_by_vendor); its linked RMA holds POSTED CN-RMA Rs 1,000. The doors
    refuse to build this now, so the RMA's note posts unlinked, as it did then."""
    bought(rw, "U1", "U2", "U3", "U4")
    rid = received_return(rw, "U1", "U2")
    rw["as_"](ACCOUNTANT).put(f"/vendor-returns/{rid}/prices", json={"unit_prices": [500]})
    _, own = rtv_note(rw, rid, 500, "CN-RTV")
    assert own.status_code == 200, own.text
    eng, rma_id = old_rma(rw, grn_id="G-OWN")
    eng.price_lines(rma_id, unit_costs_paise=[50000], actor="old")
    eng.dispatch(rma_id, carrier="DTDC", awb="AWB1", dispatch_date=None, actor="old")
    with monkeypatch.context() as patched:
        patched.setattr(rma_router, "_physical_return_problem", lambda *a, **k: None)
        body, res = rma_note(rw, rma_id, 1000, "CN-RMA")
    assert res.status_code == 200, res.text
    before_the_rule(rw)
    rw["db"].vendor_rmas.update_one({"rma_id": rma_id}, {"$set": {"return_id": rid}})
    before = returned(rw, rid)
    assert before["credit_note_number"] == "CN-RTV" and before["credit_note_amount"] == 500
    assert "credit_settled_on_rma" not in before
    return rid, rma_id, body, before


def test_replaying_the_rma_of_an_old_pair_leaves_the_returns_own_figures_alone(rw, monkeypatch):
    rid, rma_id, body, before = old_pair(rw, monkeypatch)
    again = rw["as_"](ACCOUNTANT).post(f"/vendor-rma/{rma_id}/credit-note", json=body)
    assert again.status_code == 200 and again.json()["idempotent"] is True
    assert returned(rw, rid) == before
    assert posted(rw) == (2, 150000) and credited(rw) == 150000


def test_closing_the_rma_of_an_old_pair_leaves_the_returns_own_figures_alone(rw, monkeypatch):
    rid, rma_id, _, before = old_pair(rw, monkeypatch)
    closed = rw["as_"](MANAGER).post(f"/vendor-rma/{rma_id}/close", json={})
    assert closed.status_code == 200, closed.text
    assert rw["db"].vendor_rmas.find_one({"rma_id": rma_id})["status"] == "CLOSED"
    assert returned(rw, rid) == before
    assert posted(rw) == (2, 150000) and credited(rw) == 150000


def test_closing_an_old_rma_over_a_replacement_return_records_no_credit_on_it(rw):
    for sid in ("U1", "U2"):
        unit(rw, sid)
    rid = create(rw, line(qty=2, stock_ids=["U1", "U2"]), return_type="replacement").json()["return_id"]
    assert move(rw, rid, "approved").status_code == 200
    assert move(rw, rid, "shipped").status_code == 200
    before = returned(rw, rid)
    eng, rma_id = old_rma(rw, return_id=rid)
    eng.dispatch(rma_id, carrier="DTDC", awb="AWB1", dispatch_date=None, actor="old")
    eng.record_credit_note(rma_id, credit_note_number="OLD-CN", received_amount=1000, actor="old")
    assert rw["as_"](MANAGER).post(f"/vendor-rma/{rma_id}/close", json={}).status_code == 200
    assert returned(rw, rid) == before


def test_closing_a_zero_acknowledged_rma_cannot_blank_a_returns_own_note(rw):
    bought(rw, "U1", "U2", "U3", "U4")
    rid = received_return(rw, "U1", "U2")
    rw["as_"](ACCOUNTANT).put(f"/vendor-returns/{rid}/prices", json={"unit_prices": [500]})
    _, own = rtv_note(rw, rid, 500, "CN-RTV")
    assert own.status_code == 200, own.text
    before = returned(rw, rid)
    eng, rma_id = old_rma(rw, return_id=rid, grn_id="G-OWN")
    eng.dispatch(rma_id, carrier="DTDC", awb="AWB1", dispatch_date=None, actor="old")
    eng.record_credit_note(rma_id, credit_note_number="ZERO-ACK", received_amount=0, actor="old")
    closed = rw["as_"](ACCOUNTANT).post(f"/vendor-rma/{rma_id}/close", json={"write_off_variance": True})
    assert closed.status_code == 200, closed.text
    assert returned(rw, rid) == before


def test_a_lost_reply_repairs_the_return_summary_on_the_unchanged_retry(rw, monkeypatch):
    bought(rw, "U1", "U2")
    rid = received_return(rw, "U1", "U2")
    rma_id = linked_rma(rw, rid)
    real = rma_credit.settle_return
    monkeypatch.setattr(rma_credit, "settle_return", lambda *a: (_ for _ in ()).throw(RuntimeError("lost")))
    body, lost = rma_note(rw, rma_id, 1000, "CN-1")
    assert lost.status_code == 503, lost.text
    assert posted(rw) == (1, 100000) and returned(rw, rid)["status"] == "received_by_vendor"
    monkeypatch.setattr(rma_credit, "settle_return", real)
    again = rw["as_"](ACCOUNTANT).post(f"/vendor-rma/{rma_id}/credit-note", json=body)
    assert again.status_code == 200, again.text
    assert returned(rw, rid)["status"] == "credit_issued"
    assert posted(rw) == (1, 100000) and credited(rw) == 100000
    assert rw["db"].vendor_rmas.find_one({"rma_id": rma_id})["received_credit_paise"] == 100000


def test_rejecting_the_rma_gives_the_credit_back_to_the_return(rw):
    bought(rw, "U1", "U2")
    rid = received_return(rw, "U1", "U2")
    rma_id = linked_rma(rw, rid)
    _, shut = rtv_note(rw, rid, 1000, "CN-1")
    assert shut.status_code == 409
    assert rw["as_"](ACCOUNTANT).post(f"/vendor-rma/{rma_id}/reject", json={"reason": "wrong door"}).status_code == 200
    rw["as_"](ACCOUNTANT).put(f"/vendor-returns/{rid}/prices", json={"unit_prices": [500]})
    _, opened = rtv_note(rw, rid, 1000, "CN-1")
    assert opened.status_code == 200, opened.text
    assert posted(rw) == (1, 100000) and returned(rw, rid)["status"] == "credit_issued"


def test_a_return_settled_by_replacement_never_earns_an_rma_credit(rw):
    unit(rw, "U1")
    rid = create(rw, line(stock_ids=["U1"]), return_type="replacement").json()["return_id"]
    assert move(rw, rid, "approved").status_code == 200
    refused = raise_rma(rw, qty=1, return_id=rid)
    assert refused.status_code == 422 and "replacement" in refused.json()["detail"]


def test_a_cancel_and_its_retry_still_work_under_a_linked_rma(rw, monkeypatch):
    unit(rw, "U1")
    rid = create(rw, line(stock_ids=["U1"])).json()["return_id"]
    assert move(rw, rid, "approved").status_code == 200
    made = raise_rma(rw, qty=1, return_id=rid)
    assert made.status_code == 201, made.text
    rma_id = made.json()["rma_id"]
    real = _refuse_put_back(monkeypatch, "U1")
    assert move(rw, rid, "cancelled").status_code == 409
    monkeypatch.setattr(vr._ie, "record_event_atomic", real)
    assert move(rw, rid, "cancelled").status_code == 200
    assert (state(rw, "U1")["status"], state(rw, "U1")["rtv_vendor_id"]) == ("AVAILABLE", None)
    http = rw["as_"](MANAGER)
    assert http.post(f"/vendor-rma/{rma_id}/authorize", json={"vendor_rma_number": "VR-1"}).status_code == 200
    assert http.post(f"/vendor-rma/{rma_id}/dispatch", json={"carrier": "D", "awb": "A"}).status_code == 409


def test_two_raises_racing_for_one_return_leave_one_live_rma(rw, monkeypatch):
    rid = shipped_return(rw, "U1", "U2")
    real = VendorRMAEngine.raise_rma

    def rival_lands_first(self, **kw):
        monkeypatch.setattr(VendorRMAEngine, "raise_rma", real)
        real(self, **kw)  # the other request passed the same check a moment earlier
        return real(self, **kw)

    monkeypatch.setattr(VendorRMAEngine, "raise_rma", rival_lands_first)
    lost = raise_rma(rw, return_id=rid)
    assert lost.status_code == 409, lost.text
    live = [r for r in rw["db"].vendor_rmas.find({"return_id": rid}) if r["status"] != "REJECTED"]
    assert len(live) == 1


# --------------------------------------------------------------------------
# Owner ruling 2026-11-06: each RMA line says whose piece it is
# --------------------------------------------------------------------------

CUSTOMER = "CUST-OWN-1"


def own_line(**extra):
    return {"product_id": FRAME, "product_name": "Acme Aviator 5420", "quantity": 1, "reason": "WARRANTY",
            "customer_owned": True, **extra}


def raise_lines(w, *lines, user=MANAGER, **extra):
    return w["as_"](user).post("/vendor-rma/", json={
        "vendor_id": DEALER, "vendor_name": "Bank More", "store_id": STORE, "lines": list(lines), **extra})


def test_a_customers_own_piece_of_a_stocked_model_raises_and_touches_no_stock(rw):
    rw["db"].customers.insert_one({"customer_id": CUSTOMER, "store_id": STORE, "name": "A Customer"})
    unit(rw, "U1")
    unit(rw, "U2", status="RESERVED")
    made = raise_lines(rw, own_line(customer_id=CUSTOMER), notes="The shelf pieces are unrelated.")
    assert made.status_code == 201, made.text
    rma_id = made.json()["rma_id"]
    saved = rw["db"].vendor_rmas.find_one({"rma_id": rma_id})["lines"][0]
    assert saved["customer_owned"] is True and saved["customer_id"] == CUSTOMER
    # The manager reads back what was declared, without the money.
    seen = rw["as_"](MANAGER).get(f"/vendor-rma/{rma_id}").json()["lines"][0]
    assert seen["customer_owned"] is True and seen["customer_id"] == CUSTOMER
    assert "unit_cost_paise" not in seen
    http = rw["as_"]
    assert http(MANAGER).post(f"/vendor-rma/{rma_id}/authorize", json={"vendor_rma_number": "VR-1"}).status_code == 200
    assert http(ACCOUNTANT).put(f"/vendor-rma/{rma_id}/prices", json={"unit_costs": [500]}).status_code == 200
    assert http(MANAGER).post(f"/vendor-rma/{rma_id}/dispatch", json={"carrier": "D", "awb": "A"}).status_code == 200
    note = credit_payload(rw["db"], rma_id, amount=500.0)
    assert http(ACCOUNTANT).post(f"/vendor-rma/{rma_id}/credit-note", json=note).status_code == 200
    assert http(MANAGER).post(f"/vendor-rma/{rma_id}/close", json={}).status_code == 200
    assert statuses(rw) == Counter({"AVAILABLE": 1, "RESERVED": 1})
    assert events(rw, "U1") == [] and events(rw, "U2") == []
    assert posted(rw) == (1, 50000)


def test_the_same_line_unticked_is_shop_stock_and_is_refused(rw):
    unit(rw, "U1")
    refused = raise_lines(rw, {**own_line(), "customer_owned": False})
    assert refused.status_code == 422 and "customer_owned" in refused.json()["detail"]
    assert rw["db"].vendor_rmas.count_documents({}) == 0


@pytest.mark.parametrize("ref", [{}, {"customer_id": "  "}, {"customer_id": "CUST-NOPE"},
                                 {"order_id": "ORD-NOPE"}, {"order_id": "ORD-B"}])
def test_a_ticked_line_needs_a_real_customer_or_order_in_reach(rw, ref):
    rw["db"].orders.insert_one({"order_id": "ORD-B", "store_id": SHOP_B})
    unit(rw, "U1")
    refused = raise_lines(rw, own_line(**ref))
    assert refused.status_code == 422, refused.text
    assert rw["db"].vendor_rmas.count_documents({}) == 0
    assert statuses(rw) == Counter({"AVAILABLE": 1})


def test_a_ticked_line_may_name_its_order_instead(rw):
    rw["db"].orders.insert_one({"order_id": "ORD-A", "order_number": "BV-1", "store_id": STORE})
    for ref in ("ORD-A", "BV-1"):
        made = raise_lines(rw, own_line(order_id=ref))
        assert made.status_code == 201, made.text
    hq = raise_lines(rw, own_line(order_id="ORD-A"), user=ADMIN)
    assert hq.status_code == 201, hq.text


def test_a_customers_piece_cannot_ride_a_return_of_the_same_product(rw):
    rw["db"].customers.insert_one({"customer_id": CUSTOMER, "store_id": STORE})
    rid = shipped_return(rw, "U1")
    mixed = raise_lines(rw, {"product_id": FRAME, "product_name": "Acme Aviator 5420", "quantity": 1,
                             "reason": "DEFECTIVE"}, own_line(customer_id=CUSTOMER), return_id=rid)
    assert mixed.status_code == 422, mixed.text
    assert "own RMA" in mixed.json()["detail"]


def test_an_old_unflagged_line_with_nothing_held_keeps_todays_outcome(rw):
    eng, rma_id = old_rma(rw, product=OFF_INVENTORY, name="Customer's own frame", qty=1)
    res = rw["as_"](MANAGER).post(f"/vendor-rma/{rma_id}/dispatch", json={"carrier": "D", "awb": "A"})
    assert res.status_code == 200, res.text
    saved = rw["db"].vendor_rmas.find_one({"rma_id": rma_id})["lines"][0]
    assert "customer_owned" not in saved  # historical lines are not rewritten


# --------------------------------------------------------------------------
# Codex round 3, P1: a credit that names no piece stops a later physical return
# --------------------------------------------------------------------------

def _dispatched_old_rma(w, **extra):
    eng, rma_id = old_rma(w, **extra)
    eng.dispatch(rma_id, carrier="DTDC", awb="AWB1", dispatch_date=None, actor="old")
    return eng, rma_id


def rtv_note_open(w, rid, amount, number):
    """The note as Accounts would post it, whatever the preview says now."""
    source = resolve_return_provenance(w["db"], returned(w, rid))[0]
    body = {"credit_note_number": number, "credit_note_date": "2026-10-18", "idempotency_key": str(uuid4()),
            "received_amount": amount, "source_selections": [],
            "allocations": [{"source_key": source_key(rid, source), "taxable": amount,
                             "cgst": 0, "sgst": 0, "igst": 0}]}
    return body, w["as_"](ACCOUNTANT).post(f"/vendor-returns/{rid}/credit-confirmations", json=body)


def unplaced_credit(w, monkeypatch, amount=1000, grn="G-OWN", **extra):
    """An unlinked RMA's credit from before the rule: the real writer, only the
    door that now refuses it passed by, as it was then. Its pieces stay unnamed."""
    _, rma_id = _dispatched_old_rma(w, grn_id=grn, **extra)
    with monkeypatch.context() as patched:
        patched.setattr(rma_router, "_physical_return_problem", lambda *a, **k: None)
        body, res = rma_note(w, rma_id, amount, "CN-OLD")
    assert res.status_code == 200, res.text
    before_the_rule(w)
    return rma_id, body


def test_an_unplaced_credit_stops_a_later_return_spending_the_same_goods(rw, monkeypatch):
    """Codex's reproduction with SPARE receipt quantity (six bought, four in
    play): the bill and receipt caps pass, only the ownership rule can refuse."""
    bought(rw, "U1", "U2", "U3", "U4", "U5", "U6", qty=6)
    rma_id, old_body = unplaced_credit(rw, monkeypatch)
    stored = rw["db"].supplier_credit_confirmations.find_one({"_id": old_body["idempotency_key"]})
    rid = received_return(rw, "U1", "U2")
    rw["as_"](ACCOUNTANT).put(f"/vendor-returns/{rid}/prices", json={"unit_prices": [500]})
    body, res = rtv_note_open(rw, rid, 1000, "CN-NEW")
    assert res.status_code == 200, res.text
    assert res.json()["confirmation"]["posting_status"] == "PENDING_EVIDENCE"
    reason = res.json()["confirmation"]["pending_reasons"][0]
    assert reason["code"] == "EARLIER_CREDIT_UNPLACED" and rma_id in reason["message"]
    # The supplier's note is kept, nothing posts, and history is untouched.
    assert posted(rw) == (1, 100000) and credited(rw) == 100000
    kept = rw["db"].supplier_credit_confirmations.find_one({"_id": body["idempotency_key"]})
    assert kept["identity_claimed"] is True and not kept.get("token")
    assert rw["db"].supplier_credit_confirmations.find_one({"_id": old_body["idempotency_key"]}) == stored
    assert statuses(rw) == Counter({"RTV": 2, "AVAILABLE": 4})
    assert returned(rw, rid)["status"] == "received_by_vendor"
    # Duplicate and lost-reply retries hold; a new form UUID cannot re-enter it.
    http = rw["as_"](ACCOUNTANT)
    again = http.post(f"/vendor-returns/{rid}/credit-confirmations", json=body)
    assert again.status_code == 200 and again.json()["confirmation"]["posting_status"] == "PENDING_EVIDENCE"
    retyped = http.post(f"/vendor-returns/{rid}/credit-confirmations", json={**body, "idempotency_key": str(uuid4())})
    assert retyped.status_code == 409 and "already been recorded" in retyped.text
    shown = http.post(f"/vendor-returns/{rid}/credit-preview", json={}).json()
    assert shown["source_status"] == "PENDING_EVIDENCE"
    assert shown["pending_reasons"][0]["code"] == "EARLIER_CREDIT_UNPLACED"
    assert posted(rw) == (1, 100000) and credited(rw) == 100000
    # The old RMA's own replay still answers, once.
    replay = http.post(f"/vendor-rma/{rma_id}/credit-note", json=old_body)
    assert replay.status_code == 200 and replay.json()["idempotent"] is True


def test_a_linked_rma_door_is_stopped_by_the_same_unplaced_credit(rw, monkeypatch):
    bought(rw, "U1", "U2", "U3", "U4", "U5", "U6", qty=6)
    unplaced_credit(rw, monkeypatch)
    rid = received_return(rw, "U1", "U2")
    rma_id = linked_rma(rw, rid)
    source = rma_credit.preview(rw["db"], rw["db"].vendor_rmas.find_one({"rma_id": rma_id}))
    res = rw["as_"](ACCOUNTANT).post(f"/vendor-rma/{rma_id}/credit-note", json={
        "credit_note_number": "CN-NEW", "credit_note_date": "2026-10-18", "idempotency_key": str(uuid4()),
        "received_amount": 1000, "source_selections": source["source_selections"],
        "allocations": [{"source_key": s["source_key"], "taxable": 1000, "cgst": 0, "sgst": 0, "igst": 0}
                        for s in source["sources"]]})
    assert res.status_code == 200, res.text
    assert res.json()["confirmation"]["pending_reasons"][0]["code"] == "EARLIER_CREDIT_UNPLACED"
    assert posted(rw) == (1, 100000) and credited(rw) == 100000


def test_a_customers_own_credit_is_placed_and_does_not_stop_a_shop_return(rw):
    """The tick says whose goods it was: a shop return on that receipt is free."""
    rw["db"].customers.insert_one({"customer_id": CUSTOMER, "store_id": STORE, "name": "A Customer"})
    bought(rw, "U1", "U2", "U3", "U4", qty=4)
    made = raise_lines(rw, own_line(customer_id=CUSTOMER, quantity=2), grn_id="G-OWN")
    rma_id = made.json()["rma_id"]
    http = rw["as_"]
    http(MANAGER).post(f"/vendor-rma/{rma_id}/authorize", json={"vendor_rma_number": "VR-1"})
    http(ACCOUNTANT).put(f"/vendor-rma/{rma_id}/prices", json={"unit_costs": [500]})
    http(MANAGER).post(f"/vendor-rma/{rma_id}/dispatch", json={"carrier": "D", "awb": "A"})
    assert rma_note(rw, rma_id, 1000, "CN-OWN")[1].status_code == 200
    rid = received_return(rw, "U1", "U2")
    http(ACCOUNTANT).put(f"/vendor-returns/{rid}/prices", json={"unit_prices": [500]})
    _, res = rtv_note(rw, rid, 1000, "CN-RTV")
    assert res.status_code == 200, res.text
    assert posted(rw) == (2, 200000) and credited(rw) == 200000


def test_an_unplaced_credit_on_another_receipt_blocks_nothing(rw, monkeypatch):
    bought(rw, "U1", "U2", qty=2)
    rw["db"].grns.insert_one({"grn_id": "G-EARLIER", "store_id": STORE, "vendor_id": DEALER, "status": "ACCEPTED",
                              "items": [{"product_id": FRAME, "accepted_qty": 2}]})
    bill = rw["db"].vendor_bills.find_one({"bill_id": "B-OWN"})
    rw["db"].vendor_bills.insert_one({**{k: v for k, v in bill.items() if k != "_id"},
                                      "bill_id": "B-EARLIER", "grn_id": "G-EARLIER", "bill_number": "INV-EARLIER"})
    unplaced_credit(rw, monkeypatch, grn="G-EARLIER")
    rid = received_return(rw, "U1", "U2")
    rw["as_"](ACCOUNTANT).put(f"/vendor-returns/{rid}/prices", json={"unit_prices": [500]})
    _, res = rtv_note(rw, rid, 1000, "CN-RTV")
    assert res.status_code == 200, res.text
    assert res.json()["confirmation"]["posting_status"] == "POSTED"


def test_two_lines_that_name_no_piece_stay_under_the_bill_cap_alone(rw):
    """Off-inventory goods have no physical return to double: unchanged."""
    for number in ("CN-1", "CN-2"):
        made = raise_rma(rw, product=OFF_INVENTORY)
        rma_id = made.json()["rma_id"]
        http = rw["as_"]
        http(MANAGER).post(f"/vendor-rma/{rma_id}/authorize", json={"vendor_rma_number": "VR-1"})
        http(ACCOUNTANT).put(f"/vendor-rma/{rma_id}/prices", json={"unit_costs": [500]})
        http(MANAGER).post(f"/vendor-rma/{rma_id}/dispatch", json={"carrier": "D", "awb": "A"})
        note = credit_payload(rw["db"], rma_id, amount=1000.0, number=number)
        assert http(ACCOUNTANT).post(f"/vendor-rma/{rma_id}/credit-note", json=note).status_code == 200
    assert posted(rw) == (2, 200000)
    assert rw["db"].stock_units.count_documents({"product_id": OFF_INVENTORY}) == 0


# --------------------------------------------------------------------------
# Codex round 3, P2: an old partial note can still receive its balance
# --------------------------------------------------------------------------

def legacy_partial(w, monkeypatch):
    """A linked RMA's Rs 600 note written by the adapter before the pieces rode
    along: its sources name no stock_ids, as stored notes of that time do."""
    bought(w, "U1", "U2", qty=2)
    rid = received_return(w, "U1", "U2")
    rma_id = linked_rma(w, rid)
    original = rma_credit.normalize
    with monkeypatch.context() as patched:
        patched.setattr(rma_credit, "normalize", lambda doc, db=None: original(doc))
        body, res = rma_note(w, rma_id, 600, "CN-OLD")
    assert res.status_code == 200, res.text
    before_the_rule(w)
    event = w["db"].supplier_credit_confirmations.find_one({"_id": body["idempotency_key"]})
    assert event["sources"][0]["stock_ids"] == []
    return rid, rma_id, body, event


def test_an_old_partial_note_receives_its_genuine_balance_once(rw, monkeypatch):
    rid, rma_id, body, event = legacy_partial(rw, monkeypatch)
    moves = {sid: events(rw, sid) for sid in ("U1", "U2")}
    http = rw["as_"](ACCOUNTANT)
    replay = http.post(f"/vendor-rma/{rma_id}/credit-note", json=body)
    assert replay.status_code == 200 and replay.json()["idempotent"] is True
    shown = http.post(f"/vendor-rma/{rma_id}/credit-preview", json={}).json()
    assert shown["source_status"] == "RESOLVED" and shown["remaining_amount"] == 400
    # Same identity as the note already posted: same cap, no second origin.
    assert shown["sources"][0]["source_key"] == event["sources"][0]["source_key"]
    rest_body, rest = rma_note(rw, rma_id, 400, "CN-REST")
    assert rest.status_code == 200, rest.text
    assert rest.json()["confirmation"]["posting_status"] == "POSTED"
    assert posted(rw) == (2, 100000) and credited(rw) == 100000
    # Retry and lost-reply replay change nothing; the first note is untouched.
    assert http.post(f"/vendor-rma/{rma_id}/credit-note", json=rest_body).json()["idempotent"] is True
    assert rw["db"].supplier_credit_confirmations.find_one({"_id": body["idempotency_key"]}) == event
    assert posted(rw) == (2, 100000) and credited(rw) == 100000
    settled = returned(rw, rid)
    assert settled["status"] == "credit_issued" and settled["confirmed_credit_amount"] == 1000
    assert rw["as_"](MANAGER).post(f"/vendor-rma/{rma_id}/close", json={}).status_code == 200
    assert {sid: events(rw, sid) for sid in ("U1", "U2")} == moves
    assert statuses(rw) == Counter({"RTV": 2})


def test_an_old_partial_note_cannot_be_overcredited_or_doubled(rw, monkeypatch):
    rid, rma_id, body, _ = legacy_partial(rw, monkeypatch)
    http = rw["as_"](ACCOUNTANT)
    changed = http.post(f"/vendor-rma/{rma_id}/credit-note", json={**body, "received_amount": 601})
    assert changed.status_code == 409
    shown = http.post(f"/vendor-rma/{rma_id}/credit-preview", json={}).json()
    over = {"credit_note_number": "CN-OVER", "credit_note_date": "2026-10-18", "idempotency_key": str(uuid4()),
            "received_amount": 401, "source_selections": shown["source_selections"],
            "allocations": [{"source_key": shown["sources"][0]["source_key"], "taxable": 401,
                             "cgst": 0, "sgst": 0, "igst": 0}]}
    assert http.post(f"/vendor-rma/{rma_id}/credit-note", json=over).status_code in (409, 422)
    # The return's own door stays shut: the RMA still owns these pieces.
    _, door = rtv_note_open(rw, rid, 400, "CN-RTV")
    assert door.status_code == 409 and rma_id in door.text
    assert posted(rw) == (1, 60000) and credited(rw) == 60000


def test_a_note_that_named_the_pieces_keeps_the_physical_identity(rw):
    """Only a note stored without pieces keeps the old identity."""
    bought(rw, "U1", "U2", "U3", "U4")
    rma_id = linked_rma(rw, received_return(rw, "U1", "U2"))
    body, first = rma_note(rw, rma_id, 600, "CN-1")
    assert first.status_code == 200, first.text
    saved = rw["db"].supplier_credit_confirmations.find_one({"_id": body["idempotency_key"]})
    assert saved["sources"][0]["stock_ids"] == ["U1", "U2"]
    shown = rw["as_"](ACCOUNTANT).post(f"/vendor-rma/{rma_id}/credit-preview", json={}).json()
    assert shown["sources"][0]["stock_ids"] == ["U1", "U2"]


# --------------------------------------------------------------------------
# Codex round 3, P2: a claimed supplier note is finished before the RMA ends
# --------------------------------------------------------------------------

def pending_note(w, monkeypatch, amount=1000, number="CN-WAIT", rma_id=None):
    """The supplier's note saved while its original bill cannot be confirmed."""
    if rma_id is None:
        bought(w, "U1", "U2", qty=2)
        rma_id = linked_rma(w, received_return(w, "U1", "U2"))
    body = {"credit_note_number": number, "credit_note_date": "2026-10-18", "idempotency_key": str(uuid4()),
            "received_amount": amount, "source_selections": [], "allocations": []}

    def unavailable(*_a, **_k):
        raise ReturnProvenanceError("EVIDENCE_DOWN", "Original evidence unavailable")

    with monkeypatch.context() as patched:
        patched.setattr(rma_credit.canonical, "resolve_credit_evidence", unavailable)
        res = w["as_"](ACCOUNTANT).post(f"/vendor-rma/{rma_id}/credit-note", json=body)
    assert res.status_code == 200 and res.json()["confirmation"]["posting_status"] == "PENDING_EVIDENCE", res.text
    return rma_id, body


def finish_pending(w, rma_id, body, amount):
    http = w["as_"](ACCOUNTANT)
    shown = http.post(f"/vendor-rma/{rma_id}/credit-preview", json={"confirmation_id": body["idempotency_key"]}).json()
    return http.post(f"/vendor-rma/{rma_id}/credit-note", json={
        **body, "source_selections": shown["source_selections"],
        "allocations": [{"source_key": shown["sources"][0]["source_key"], "taxable": amount,
                         "cgst": 0, "sgst": 0, "igst": 0}]})


def test_an_rma_holding_a_pending_note_is_not_rejected_and_the_note_finishes(rw, monkeypatch):
    rma_id, body = pending_note(rw, monkeypatch)
    # Accounts' own decision stays Accounts': a manager hears only that.
    assert rw["as_"](MANAGER).post(f"/vendor-rma/{rma_id}/reject", json={}).status_code == 403
    refused = rw["as_"](ACCOUNTANT).post(f"/vendor-rma/{rma_id}/reject", json={"reason": "supplier dispute"})
    assert refused.status_code == 409, refused.text
    assert "supplier credit note" in refused.text
    assert rw["db"].vendor_rmas.find_one({"rma_id": rma_id})["status"] == "DISPATCHED"
    # Evidence back, the same note on the same door finishes: nothing was lost.
    done = finish_pending(rw, rma_id, body, 1000)
    assert done.status_code == 200 and done.json()["confirmation"]["posting_status"] == "POSTED", done.text
    assert posted(rw) == (1, 100000) and credited(rw) == 100000
    assert rw["db"].supplier_credit_note_identities.count_documents({}) == 1


def test_a_note_refused_before_its_number_was_claimed_does_not_hold_the_rma(rw):
    bought(rw, "U1", "U2", qty=2)
    rma_id = linked_rma(rw, received_return(rw, "U1", "U2"))
    rw["db"].supplier_credit_confirmations.insert_one({
        "_id": str(uuid4()), "return_id": rma_id, "return_kind": "RMA", "identity_claimed": False,
        "posting_status": "PENDING_EVIDENCE"})
    assert rw["as_"](ACCOUNTANT).post(f"/vendor-rma/{rma_id}/reject", json={"reason": "x"}).status_code == 200


def test_a_lost_reply_on_a_posted_note_holds_the_rma_until_the_retry_repairs_it(rw, monkeypatch):
    bought(rw, "U1", "U2", qty=2)
    rma_id = linked_rma(rw, received_return(rw, "U1", "U2"))

    def lost(*_a, **_k):
        raise RuntimeError("lost reply")

    with monkeypatch.context() as patched:
        patched.setattr(rma_credit.canonical, "_finish_return", lost)
        body, res = rma_note(rw, rma_id, 1000, "CN-LOST")
    assert res.status_code == 503, res.text
    assert posted(rw) == (1, 100000)
    assert rw["as_"](ACCOUNTANT).post(f"/vendor-rma/{rma_id}/reject", json={"reason": "x"}).status_code == 409
    again = rw["as_"](ACCOUNTANT).post(f"/vendor-rma/{rma_id}/credit-note", json=body)
    assert again.status_code == 200, again.text
    assert rw["db"].vendor_rmas.find_one({"rma_id": rma_id})["received_credit_paise"] == 100000
    assert posted(rw) == (1, 100000) and credited(rw) == 100000


def test_a_write_off_close_cannot_strand_a_second_note(rw, monkeypatch):
    bought(rw, "U1", "U2", qty=2)
    rma_id = linked_rma(rw, received_return(rw, "U1", "U2"))
    assert rma_note(rw, rma_id, 600, "CN-1")[1].status_code == 200
    _, body = pending_note(rw, monkeypatch, amount=400, number="CN-2", rma_id=rma_id)
    refused = rw["as_"](ACCOUNTANT).post(f"/vendor-rma/{rma_id}/close", json={"write_off_variance": True})
    assert refused.status_code == 409 and "supplier credit note" in refused.text
    done = finish_pending(rw, rma_id, body, 400)
    assert done.status_code == 200, done.text
    assert rw["as_"](MANAGER).post(f"/vendor-rma/{rma_id}/close", json={}).status_code == 200
    assert posted(rw) == (2, 100000) and credited(rw) == 100000


# --------------------------------------------------------------------------
# Owner ruling 2026-11-06: every piece sold, an unticked line takes no credit
# --------------------------------------------------------------------------

def test_an_old_unticked_rma_cannot_dispatch_or_take_credit_once_every_piece_is_sold(rw):
    bought(rw, "U1", "U2", qty=2)
    sold_rest(rw, "U1", "U2")
    _, waiting = old_rma(rw, grn_id="G-OWN")
    res = rw["as_"](MANAGER).post(f"/vendor-rma/{waiting}/dispatch", json={"carrier": "D", "awb": "A"})
    assert res.status_code == 409 and "been sold" in res.text
    _, rma_id = _dispatched_old_rma(rw, grn_id="G-OWN")
    note = {"credit_note_number": "CN-1", "credit_note_date": "2026-10-18", "idempotency_key": str(uuid4()),
            "received_amount": 1000, "source_selections": [], "allocations": []}
    res = rw["as_"](ACCOUNTANT).post(f"/vendor-rma/{rma_id}/credit-note", json=note)
    assert res.status_code == 409 and "been sold" in res.text
    assert posted(rw) == (0, 0) and credited(rw) == 0
    assert rw["db"].supplier_credit_note_identities.count_documents({}) == 0


def test_a_credit_posted_before_the_ruling_stays_replays_and_closes(rw, monkeypatch):
    bought(rw, "U1", "U2", qty=2)
    _, rma_id = _dispatched_old_rma(rw, grn_id="G-OWN")
    with monkeypatch.context() as patched:
        patched.setattr(rma_router, "_physical_return_problem", lambda *a, **k: None)
        body, res = rma_note(rw, rma_id, 1000, "CN-OLD")
    assert res.status_code == 200, res.text
    before_the_rule(rw)
    sold_rest(rw, "U1", "U2")
    stored = rw["db"].supplier_credit_confirmations.find_one({"_id": body["idempotency_key"]})
    again = rw["as_"](ACCOUNTANT).post(f"/vendor-rma/{rma_id}/credit-note", json=body)
    assert again.status_code == 200 and again.json()["idempotent"] is True
    assert rw["as_"](MANAGER).post(f"/vendor-rma/{rma_id}/close", json={}).status_code == 200
    assert rw["db"].supplier_credit_confirmations.find_one({"_id": body["idempotency_key"]}) == stored
    assert posted(rw) == (1, 100000) and credited(rw) == 100000


def test_a_typed_return_line_takes_no_credit_on_sold_pieces_either(rw):
    bought(rw, "U1", "U2", qty=2)
    sold_rest(rw, "U1", "U2")
    refused = create(rw, line(qty=2), notes="Warranty frame, no barcode")
    assert refused.status_code == 422, refused.text
    assert "been sold" in refused.json()["detail"] and "customer-owned" in refused.json()["detail"]


def typed_return(w):
    """A typed return of a product no piece of is held yet, walked to the supplier."""
    made = create(w, line(qty=2), notes="Typed before the pieces were sold")
    assert made.status_code in (200, 201), made.text
    rid = made.json()["return_id"]
    for step in ("approved", "shipped", "received_by_vendor"):
        assert move(w, rid, step).status_code == 200, step
    w["as_"](ACCOUNTANT).put(f"/vendor-returns/{rid}/prices", json={"unit_prices": [500]})
    return rid


def test_a_typed_return_from_before_the_ruling_cannot_take_new_credit_on_sold_pieces(rw):
    bought(rw, qty=2)
    rid = typed_return(rw)
    unit(rw, "U1", status="SOLD", grn_id="G-OWN", source_type="GRN", source_id="G-OWN")
    unit(rw, "U2", status="SOLD", grn_id="G-OWN", source_type="GRN", source_id="G-OWN")
    _, res = rtv_note_open(rw, rid, 1000, "CN-TYPED")
    assert res.status_code == 409 and "been sold" in res.text
    # Refused before the door: the supplier's note number stays free.
    assert rw["db"].supplier_credit_note_identities.count_documents({}) == 0
    assert posted(rw) == (0, 0) and credited(rw) == 0


def test_a_typed_return_whose_credit_posted_first_still_replays(rw):
    bought(rw, qty=2)
    rid = typed_return(rw)
    body, res = rtv_note(rw, rid, 1000, "CN-TYPED")
    assert res.status_code == 200, res.text
    unit(rw, "U1", status="SOLD", grn_id="G-OWN", source_type="GRN", source_id="G-OWN")
    again = rw["as_"](ACCOUNTANT).post(f"/vendor-returns/{rid}/credit-confirmations", json=body)
    assert again.status_code == 200 and again.json()["idempotent"] is True
    assert posted(rw) == (1, 100000)


def test_a_product_never_tracked_piece_by_piece_keeps_its_credit(rw):
    """Off-inventory and made-to-order: no piece was ever minted, unticked works."""
    made = raise_rma(rw, product=OFF_INVENTORY)
    assert made.status_code == 201, made.text
    codes, _ = walk(rw, made.json()["rma_id"])
    assert codes == [200, 200, 200, 200, 200] and posted(rw) == (1, 100000)
    typed = create(rw, line(qty=1, product=OFF_INVENTORY, name="Customer's own frame"), notes="Made to order")
    assert typed.status_code in (200, 201), typed.text


def test_the_ticked_route_credits_a_sold_piece_and_touches_no_stock(rw):
    rw["db"].customers.insert_one({"customer_id": CUSTOMER, "store_id": STORE, "name": "A Customer"})
    bought(rw, "U1", "U2", qty=2)
    sold_rest(rw, "U1", "U2")
    made = raise_lines(rw, own_line(customer_id=CUSTOMER, quantity=2), grn_id="G-OWN")
    assert made.status_code == 201, made.text
    rma_id = made.json()["rma_id"]
    http = rw["as_"]
    assert http(MANAGER).post(f"/vendor-rma/{rma_id}/authorize", json={"vendor_rma_number": "VR-1"}).status_code == 200
    assert http(ACCOUNTANT).put(f"/vendor-rma/{rma_id}/prices", json={"unit_costs": [500]}).status_code == 200
    assert http(MANAGER).post(f"/vendor-rma/{rma_id}/dispatch", json={"carrier": "D", "awb": "A"}).status_code == 200
    _, res = rma_note(rw, rma_id, 1000, "CN-OWN")
    assert res.status_code == 200, res.text
    assert http(MANAGER).post(f"/vendor-rma/{rma_id}/close", json={}).status_code == 200
    assert posted(rw) == (1, 100000) and credited(rw) == 100000
    assert statuses(rw) == Counter({"SOLD": 2}) and events(rw, "U1") == []


def test_a_piece_left_that_is_not_sold_keeps_todays_outcome(rw):
    """Every piece means every piece: one released to RTV on the Quarantine
    screen has no other credit door, so the line still answers for it."""
    bought(rw, "U1", "U2", qty=2)
    sold_rest(rw, "U1")
    rw["db"].stock_units.update_one({"stock_id": "U2"}, {"$set": {"status": "RTV"}})
    assert raise_rma(rw, qty=1, grn_id="G-OWN").status_code == 201


def test_a_note_refused_outright_claims_nothing_and_holds_nothing(rw):
    """A note over the return's value is an error, not a saved note: its number
    stays free for the real amount and the RMA can still end."""
    bought(rw, "U1", "U2", qty=2)
    rma_id = linked_rma(rw, received_return(rw, "U1", "U2"))
    http = rw["as_"](ACCOUNTANT)
    shown = http.post(f"/vendor-rma/{rma_id}/credit-preview", json={}).json()
    over = {"credit_note_number": "CN-TYPO", "credit_note_date": "2026-10-18", "idempotency_key": str(uuid4()),
            "received_amount": 1001, "source_selections": shown["source_selections"],
            "allocations": [{"source_key": shown["sources"][0]["source_key"], "taxable": 1001,
                             "cgst": 0, "sgst": 0, "igst": 0}]}
    assert http.post(f"/vendor-rma/{rma_id}/credit-note", json=over).status_code == 422
    assert rw["db"].supplier_credit_note_identities.find_one({"claims.0": {"$exists": True}}) is None
    _, right = rma_note(rw, rma_id, 1000, "CN-TYPO")
    assert right.status_code == 200, right.text
    assert rw["as_"](MANAGER).post(f"/vendor-rma/{rma_id}/close", json={}).status_code == 200
    assert posted(rw) == (1, 100000) and credited(rw) == 100000


def test_an_old_refused_row_with_its_number_claimed_does_not_hold_the_rma(rw):
    bought(rw, "U1", "U2", qty=2)
    rma_id = linked_rma(rw, received_return(rw, "U1", "U2"))
    rw["db"].supplier_credit_confirmations.insert_one({
        "_id": str(uuid4()), "return_id": rma_id, "return_kind": "RMA", "identity_claimed": True,
        "posting_status": "PENDING_EVIDENCE", "pending_reasons": []})
    assert rw["as_"](ACCOUNTANT).post(f"/vendor-rma/{rma_id}/reject", json={"reason": "x"}).status_code == 200


def test_a_note_over_the_balance_can_never_finish_and_does_not_hold_the_rma(rw):
    bought(rw, "U1", "U2", qty=2)
    rma_id = linked_rma(rw, received_return(rw, "U1", "U2"))
    assert rma_note(rw, rma_id, 600, "CN-1")[1].status_code == 200
    http = rw["as_"](ACCOUNTANT)
    shown = http.post(f"/vendor-rma/{rma_id}/credit-preview", json={}).json()
    over = {"credit_note_number": "CN-OVER", "credit_note_date": "2026-10-18", "idempotency_key": str(uuid4()),
            "received_amount": 401, "source_selections": shown["source_selections"],
            "allocations": [{"source_key": shown["sources"][0]["source_key"], "taxable": 401,
                             "cgst": 0, "sgst": 0, "igst": 0}]}
    assert http.post(f"/vendor-rma/{rma_id}/credit-note", json=over).status_code == 422
    assert rma_note(rw, rma_id, 400, "CN-2")[1].status_code == 200
    assert rw["as_"](MANAGER).post(f"/vendor-rma/{rma_id}/close", json={}).status_code == 200
    assert posted(rw) == (2, 100000) and credited(rw) == 100000


# --------------------------------------------------------------------------
# Independent review of 39901c9c: the link must carry the goods, a no-piece
# credit spends them too, and a claimed note is finished before anything ends
# --------------------------------------------------------------------------

def old_linked_credit(w, monkeypatch, return_id, amount=1000):
    """A credit from before the rule on an RMA whose return_id was free text:
    the real writer, the door and the adapter as they were then, no pieces."""
    _, rma_id = _dispatched_old_rma(w, grn_id="G-OWN", return_id=return_id)
    original = rma_credit.normalize
    with monkeypatch.context() as patched:
        patched.setattr(rma_router, "_physical_return_problem", lambda *a, **k: None)
        patched.setattr(rma_credit, "normalize", lambda doc, db=None: original(doc))
        body, res = rma_note(w, rma_id, amount, "CN-OLD")
    assert res.status_code == 200, res.text
    before_the_rule(w)
    event = w["db"].supplier_credit_confirmations.find_one({"_id": body["idempotency_key"]})
    assert event["posting_status"] == "POSTED" and event["sources"][0]["stock_ids"] == []
    return rma_id


def link_cancelled(w):
    rid = create(w, line(qty=2, stock_ids=["U1", "U2"])).json()["return_id"]
    assert move(w, rid, "cancelled").status_code == 200
    assert state(w, "U1")["status"] == "AVAILABLE" and state(w, "U2")["status"] == "AVAILABLE"
    return rid


def link_typed(w):
    w["db"].vendor_returns.insert_one({"return_id": "VR-TYPED", "store_id": STORE, "vendor_id": DEALER,
                                       "status": "received_by_vendor", "stock_ids": [], "items": []})
    return "VR-TYPED"


def link_other_product(w):
    w["db"].vendor_returns.insert_one({"return_id": "VR-OTHER", "store_id": STORE, "vendor_id": DEALER,
                                       "status": "received_by_vendor", "stock_ids": ["X1", "X2"], "items": []})
    for sid in ("X1", "X2"):
        unit(w, sid, product="P-OTHER", status="RTV", rtv_vendor_id="VR-OTHER")
    return "VR-OTHER"


def link_another_supplier(w):
    w["db"].vendor_returns.insert_one({"return_id": "VR-VEND", "store_id": STORE, "vendor_id": "V-OTHER",
                                       "status": "received_by_vendor", "stock_ids": ["Z1", "Z2"], "items": []})
    for sid in ("Z1", "Z2"):
        unit(w, sid, status="RTV", rtv_vendor_id="VR-VEND")
    return "VR-VEND"


def link_cancelled_with_a_piece_stuck(w):
    """A cancel that could not put its pieces back keeps them linked (L2-RTV-F01)."""
    w["db"].vendor_returns.insert_one({"return_id": "VR-STUCK", "store_id": STORE, "vendor_id": DEALER,
                                       "status": "cancelled", "stock_ids": ["S1", "S2"], "items": []})
    for sid in ("S1", "S2"):
        unit(w, sid, status="QUARANTINED", rtv_vendor_id="VR-STUCK")
    return "VR-STUCK"


def link_one_piece_of_two(w):
    w["db"].vendor_returns.insert_one({"return_id": "VR-ONE", "store_id": STORE, "vendor_id": DEALER,
                                       "status": "received_by_vendor", "stock_ids": ["Y1"], "items": []})
    unit(w, "Y1", status="RTV", rtv_vendor_id="VR-ONE")
    return "VR-ONE"


@pytest.mark.parametrize("link", [link_cancelled, link_cancelled_with_a_piece_stuck, link_typed, link_other_product,
                                  link_another_supplier, link_one_piece_of_two, lambda w: "VR-NO-SUCH-RETURN"],
                         ids=["cancelled", "cancelled-stuck", "typed", "other-product", "other-supplier",
                              "short", "unknown"])
def test_a_link_that_did_not_carry_the_goods_does_not_place_the_credit(rw, monkeypatch, link):
    """Spare receipt quantity: the caps pass, only the ownership rule can stop it."""
    bought(rw, "U1", "U2", "U3", "U4", "U5", "U6", qty=8)
    old = old_linked_credit(rw, monkeypatch, link(rw))
    rid = received_return(rw, "U1", "U2")
    rw["as_"](ACCOUNTANT).put(f"/vendor-returns/{rid}/prices", json={"unit_prices": [500]})
    _, res = rtv_note_open(rw, rid, 1000, "CN-NEW")
    assert res.status_code == 200, res.text
    assert res.json()["confirmation"]["posting_status"] == "PENDING_EVIDENCE"
    reason = res.json()["confirmation"]["pending_reasons"][0]
    assert reason["code"] == "EARLIER_CREDIT_UNPLACED" and old in reason["message"]
    assert posted(rw) == (1, 100000) and credited(rw) == 100000


def test_a_link_that_carried_the_goods_places_the_credit_and_frees_other_pieces(rw, monkeypatch):
    bought(rw, "U1", "U2", "U3", "U4", qty=6)
    first = received_return(rw, "U1", "U2")
    old_linked_credit(rw, monkeypatch, first)
    rid = received_return(rw, "U3", "U4")
    rw["as_"](ACCOUNTANT).put(f"/vendor-returns/{rid}/prices", json={"unit_prices": [500]})
    _, res = rtv_note(rw, rid, 1000, "CN-NEW")
    assert res.status_code == 200 and res.json()["confirmation"]["posting_status"] == "POSTED", res.text
    assert posted(rw) == (2, 200000) and credited(rw) == 200000


@pytest.mark.parametrize("pieces,placed", [(2, False), (4, True)])
def test_two_lines_of_one_product_need_pieces_for_both(rw, pieces, placed):
    ids = [f"L{n}" for n in range(pieces)]
    rw["db"].vendor_returns.insert_one({"return_id": "VR-L", "store_id": STORE, "vendor_id": DEALER,
                                        "status": "received_by_vendor", "stock_ids": ids, "items": []})
    for sid in ids:
        unit(rw, sid, status="RTV", rtv_vendor_id="VR-L")
    rw["db"].vendor_rmas.insert_one({"rma_id": "RMA-L", "vendor_id": DEALER, "return_id": "VR-L", "lines": [{}, {}]})
    sources = [{"product_id": FRAME, "quantity": 2, "return_line_index": n, "stock_ids": []} for n in (0, 1)]
    event = {"return_kind": "RMA", "return_id": "RMA-L", "sources": sources}
    assert _goods_known(rw["db"], event, sources[0]) is placed


def no_piece_note(w, rma_id, amount, number):
    """The note as Accounts would post it on a line naming no piece."""
    doc = w["db"].vendor_rmas.find_one({"rma_id": rma_id})
    source = resolve_return_provenance(w["db"], rma_credit.normalize(doc, w["db"]))[0]
    return w["as_"](ACCOUNTANT).post(f"/vendor-rma/{rma_id}/credit-note", json={
        "credit_note_number": number, "credit_note_date": "2026-10-18", "idempotency_key": str(uuid4()),
        "received_amount": amount, "source_selections": [],
        "allocations": [{"source_key": source_key(f"RMA:{rma_id}", source), "taxable": amount,
                         "cgst": 0, "sgst": 0, "igst": 0}]})


def test_a_credit_naming_no_piece_cannot_spend_the_goods_of_an_unplaced_one(rw, monkeypatch):
    """Door (a): pieces released to RTV on Quarantine plus sold ones, so the line
    stands; it names none, yet a receipt tracked piece by piece is what it spends."""
    bought(rw, "U1", "U2", "U3", "U4", qty=6)
    old, _ = unplaced_credit(rw, monkeypatch)
    rw["db"].stock_units.update_many({"stock_id": {"$in": ["U1", "U2"]}}, {"$set": {"status": "RTV"}})
    sold_rest(rw, "U3", "U4")
    made = raise_rma(rw, qty=2, grn_id="G-OWN")
    assert made.status_code == 201, made.text
    rma_id = made.json()["rma_id"]
    http = rw["as_"]
    assert http(MANAGER).post(f"/vendor-rma/{rma_id}/authorize", json={"vendor_rma_number": "VR-1"}).status_code == 200
    assert http(ACCOUNTANT).put(f"/vendor-rma/{rma_id}/prices", json={"unit_costs": [500]}).status_code == 200
    assert http(MANAGER).post(f"/vendor-rma/{rma_id}/dispatch", json={"carrier": "D", "awb": "A"}).status_code == 200
    res = no_piece_note(rw, rma_id, 1000, "CN-NEW")
    assert res.status_code == 200, res.text
    assert res.json()["confirmation"]["posting_status"] == "PENDING_EVIDENCE"
    reason = res.json()["confirmation"]["pending_reasons"][0]
    assert reason["code"] == "EARLIER_CREDIT_UNPLACED" and old in reason["message"]
    assert posted(rw) == (1, 100000) and credited(rw) == 100000


def test_an_old_partial_notes_balance_cannot_spend_an_unplaced_credits_goods(rw, monkeypatch):
    """Door (b): the balance keeps its old no-piece identity; the rule still reads it."""
    bought(rw, "U1", "U2", "U3", "U4", qty=6)
    rma_id = linked_rma(rw, received_return(rw, "U1", "U2"))
    original = rma_credit.normalize
    with monkeypatch.context() as patched:
        patched.setattr(rma_credit, "normalize", lambda doc, db=None: original(doc))
        assert rma_note(rw, rma_id, 600, "CN-PART")[1].status_code == 200
    unplaced_credit(rw, monkeypatch)
    assert posted(rw) == (2, 160000)
    res = no_piece_note(rw, rma_id, 400, "CN-REST")
    assert res.status_code == 200, res.text
    before_the_rule(rw)
    assert res.json()["confirmation"]["pending_reasons"][0]["code"] == "EARLIER_CREDIT_UNPLACED"
    assert posted(rw) == (2, 160000) and credited(rw) == 160000


def test_a_customers_own_line_still_credits_beside_an_unplaced_credit(rw, monkeypatch):
    """The tick says the goods are not the receipt's pieces: today's outcome."""
    rw["db"].customers.insert_one({"customer_id": CUSTOMER, "store_id": STORE, "name": "A Customer"})
    bought(rw, "U1", "U2", "U3", "U4", qty=6)
    unplaced_credit(rw, monkeypatch)
    made = raise_lines(rw, own_line(customer_id=CUSTOMER, quantity=2), grn_id="G-OWN")
    rma_id = made.json()["rma_id"]
    http = rw["as_"]
    http(MANAGER).post(f"/vendor-rma/{rma_id}/authorize", json={"vendor_rma_number": "VR-1"})
    http(ACCOUNTANT).put(f"/vendor-rma/{rma_id}/prices", json={"unit_costs": [500]})
    http(MANAGER).post(f"/vendor-rma/{rma_id}/dispatch", json={"carrier": "D", "awb": "A"})
    _, res = rma_note(rw, rma_id, 1000, "CN-OWN")
    assert res.status_code == 200 and res.json()["confirmation"]["posting_status"] == "POSTED", res.text
    assert posted(rw) == (2, 200000)


def test_a_product_with_no_pieces_keeps_two_credits_on_one_receipt(rw):
    """Nothing tracked to spend twice: the bill cap alone, today's outcome."""
    bought(rw, qty=4)
    rw["db"].grns.update_one({"grn_id": "G-OWN"}, {"$set": {"items.0.product_id": OFF_INVENTORY}})
    rw["db"].vendor_bills.update_one({"bill_id": "B-OWN"}, {"$set": {"lines.0.product_id": OFF_INVENTORY}})
    http = rw["as_"]
    for number in ("CN-1", "CN-2"):
        rma_id = raise_rma(rw, product=OFF_INVENTORY, grn_id="G-OWN").json()["rma_id"]
        http(MANAGER).post(f"/vendor-rma/{rma_id}/authorize", json={"vendor_rma_number": "VR-1"})
        http(ACCOUNTANT).put(f"/vendor-rma/{rma_id}/prices", json={"unit_costs": [500]})
        http(MANAGER).post(f"/vendor-rma/{rma_id}/dispatch", json={"carrier": "D", "awb": "A"})
        _, res = rma_note(rw, rma_id, 1000, number)
        assert res.status_code == 200 and res.json()["confirmation"]["posting_status"] == "POSTED", res.text
    assert posted(rw) == (2, 200000)


def split_bill_rma(w):
    """A dispatched two-piece RMA whose receipt was billed on two lines of one."""
    bought(w, "U1", "U2", qty=2)
    w["db"].vendor_bills.update_one({"bill_id": "B-OWN"}, {"$set": {"lines": [
        {"product_id": FRAME, "qty": 1, "taxable": 500, "cgst": 0, "sgst": 0, "igst": 0,
         "gst_rate": 0, "line_total": 500} for _ in range(2)]}})
    return linked_rma(w, received_return(w, "U1", "U2"))


def wrong_selection_note(w, rma_id):
    body = {"credit_note_number": "CN-SPLIT", "credit_note_date": "2026-10-18", "idempotency_key": str(uuid4()),
            "received_amount": 1000, "allocations": [],
            "source_selections": [{"return_line_index": 0, "grn_id": "G-OWN", "quantity": 2,
                                   "bill_id": "B-OWN", "bill_line_index": 0}]}
    res = w["as_"](ACCOUNTANT).post(f"/vendor-rma/{rma_id}/credit-note", json=body)
    assert res.status_code == 200, res.text
    assert res.json()["confirmation"]["pending_reasons"][0]["code"] == "ORIGINAL_QUANTITY_EXCEEDED"
    saved = w["db"].supplier_credit_confirmations.find_one({"_id": body["idempotency_key"]})
    assert saved["identity_claimed"] is True
    return body


def test_a_wrong_selection_is_a_recoverable_note_and_holds_the_rma(rw):
    """ORIGINAL_QUANTITY_EXCEEDED from the evidence is no cap: the right split posts."""
    rma_id = split_bill_rma(rw)
    body = wrong_selection_note(rw, rma_id)
    http = rw["as_"](ACCOUNTANT)
    assert http.post(f"/vendor-rma/{rma_id}/reject", json={"reason": "x"}).status_code == 409
    assert rw["as_"](MANAGER).post(f"/vendor-rma/{rma_id}/close", json={}).status_code == 409
    assert rw["db"].vendor_rmas.find_one({"rma_id": rma_id})["status"] == "DISPATCHED"
    http = rw["as_"](ACCOUNTANT)
    split = [{"return_line_index": 0, "grn_id": "G-OWN", "quantity": 1, "bill_id": "B-OWN",
              "bill_line_index": n, "stock_ids": [sid]} for n, sid in enumerate(("U1", "U2"))]
    shown = http.post(f"/vendor-rma/{rma_id}/credit-preview", json={"source_selections": split}).json()
    assert shown["source_status"] == "RESOLVED", shown
    done = http.post(f"/vendor-rma/{rma_id}/credit-note", json={
        **body, "source_selections": shown["source_selections"],
        "allocations": [{"source_key": s["source_key"], "taxable": 500, "cgst": 0, "sgst": 0, "igst": 0}
                        for s in shown["sources"]]})
    assert done.status_code == 200 and done.json()["confirmation"]["posting_status"] == "POSTED", done.text
    assert posted(rw) == (1, 100000) and credited(rw) == 100000


def test_a_selection_over_one_receipt_is_recoverable_too(rw):
    """RECEIPT_QUANTITY_EXCEEDED from the evidence, on an unlinked line."""
    db = rw["db"]
    made = raise_rma(rw, product=OFF_INVENTORY)
    rma_id = made.json()["rma_id"]
    http = rw["as_"]
    http(MANAGER).post(f"/vendor-rma/{rma_id}/authorize", json={"vendor_rma_number": "VR-1"})
    http(ACCOUNTANT).put(f"/vendor-rma/{rma_id}/prices", json={"unit_costs": [500]})
    http(MANAGER).post(f"/vendor-rma/{rma_id}/dispatch", json={"carrier": "D", "awb": "A"})
    db.entities.insert_one({"entity_id": "E-OWN", "gstins": [{"gstin": "20AAAAA0000A1Z5"}]})
    for gid in ("G-A", "G-B"):
        db.grns.insert_one({"grn_id": gid, "store_id": STORE, "vendor_id": DEALER, "status": "ACCEPTED",
                            "items": [{"product_id": OFF_INVENTORY, "accepted_qty": 1}]})
        db.vendor_bills.insert_one({
            "bill_id": "B-" + gid, "grn_id": gid, "store_id": STORE, "vendor_id": DEALER, "status": "UNPAID",
            "received": True, "doc_type": "PURCHASE_INVOICE", "bill_number": "INV-" + gid,
            "bill_date": datetime(2026, 1, 1), "recipient_entity_id": "E-OWN",
            "recipient_gstin": "20AAAAA0000A1Z5", "vendor_gstin": "20BBBBB0000B1Z5",
            "lines": [{"product_id": OFF_INVENTORY, "qty": 2, "taxable": 1000, "cgst": 0, "sgst": 0, "igst": 0,
                       "gst_rate": 0, "line_total": 1000}],
            "taxable_amount": 1000, "tax_amount": 0, "cgst_total": 0, "sgst_total": 0, "igst_total": 0})
    body = {"credit_note_number": "CN-RCPT", "credit_note_date": "2026-10-18", "idempotency_key": str(uuid4()),
            "received_amount": 1000, "allocations": [],
            "source_selections": [{"return_line_index": 0, "grn_id": "G-A", "quantity": 2,
                                   "bill_id": "B-G-A", "bill_line_index": 0}]}
    res = http(ACCOUNTANT).post(f"/vendor-rma/{rma_id}/credit-note", json=body)
    assert res.status_code == 200, res.text
    assert res.json()["confirmation"]["pending_reasons"][0]["code"] == "RECEIPT_QUANTITY_EXCEEDED"
    assert http(ACCOUNTANT).post(f"/vendor-rma/{rma_id}/reject", json={"reason": "x"}).status_code == 409
    split = [{"return_line_index": 0, "grn_id": g, "quantity": 1, "bill_id": "B-" + g, "bill_line_index": 0}
             for g in ("G-A", "G-B")]
    shown = http(ACCOUNTANT).post(f"/vendor-rma/{rma_id}/credit-preview", json={"source_selections": split}).json()
    done = http(ACCOUNTANT).post(f"/vendor-rma/{rma_id}/credit-note", json={
        **body, "source_selections": shown["source_selections"],
        "allocations": [{"source_key": s["source_key"], "taxable": 500, "cgst": 0, "sgst": 0, "igst": 0}
                        for s in shown["sources"]]})
    assert done.status_code == 200 and done.json()["confirmation"]["posting_status"] == "POSTED", done.text
    assert posted(rw) == (1, 100000)


def test_a_note_a_real_posting_found_over_a_cap_still_lets_the_rma_end(rw, monkeypatch):
    """The saved row of a refused reservation can never fit: it holds nothing."""
    bought(rw, "U1", "U2", qty=2)
    rma_id = linked_rma(rw, received_return(rw, "U1", "U2"))

    def full(*_a, **_k):
        raise rma_credit.SupplierCreditError("RECEIPT_QUANTITY_EXCEEDED", "Already reserved")

    with monkeypatch.context() as patched:
        patched.setattr(rma_credit.canonical, "_reserve_physical", full)
        body, res = rma_note(rw, rma_id, 1000, "CN-FULL")
    assert res.status_code == 422, res.text
    saved = rw["db"].supplier_credit_confirmations.find_one({"_id": body["idempotency_key"]})
    assert saved["posting_status"] == "PENDING_EVIDENCE" and saved["fenced_token"]
    assert rw["as_"](ACCOUNTANT).post(f"/vendor-rma/{rma_id}/reject", json={"reason": "x"}).status_code == 200


def replace(w, rid, user=ACCOUNTANT):
    return w["as_"](user).patch(f"/vendor-returns/{rid}/status", json={"status": "replaced"})


def test_a_return_cannot_end_as_replaced_under_its_rmas_waiting_note(rw, monkeypatch):
    rma_id, body = pending_note(rw, monkeypatch)
    rid = rw["db"].vendor_rmas.find_one({"rma_id": rma_id})["return_id"]
    refused = replace(rw, rid)
    assert refused.status_code == 409 and "supplier credit note" in refused.text, refused.text
    assert returned(rw, rid)["status"] == "received_by_vendor"
    done = finish_pending(rw, rma_id, body, 1000)
    assert done.status_code == 200 and done.json()["confirmation"]["posting_status"] == "POSTED", done.text
    assert posted(rw) == (1, 100000) and credited(rw) == 100000


def test_a_return_cannot_end_as_replaced_under_its_own_waiting_note(rw, monkeypatch):
    bought(rw, "U1", "U2", qty=2)
    rid = received_return(rw, "U1", "U2")
    rw["as_"](ACCOUNTANT).put(f"/vendor-returns/{rid}/prices", json={"unit_prices": [500]})
    body = {"credit_note_number": "CN-OWN", "credit_note_date": "2026-10-18", "idempotency_key": str(uuid4()),
            "received_amount": 1000, "source_selections": [], "allocations": []}

    def unavailable(*_a, **_k):
        raise ReturnProvenanceError("EVIDENCE_DOWN", "Original evidence unavailable")

    http = rw["as_"](ACCOUNTANT)
    with monkeypatch.context() as patched:
        patched.setattr(rma_credit.canonical, "resolve_credit_evidence", unavailable)
        saved = http.post(f"/vendor-returns/{rid}/credit-confirmations", json=body)
    assert saved.status_code == 200 and saved.json()["confirmation"]["posting_status"] == "PENDING_EVIDENCE", saved.text
    assert replace(rw, rid).status_code == 409
    shown = http.post(f"/vendor-returns/{rid}/credit-preview", json={"confirmation_id": body["idempotency_key"]}).json()
    done = http.post(f"/vendor-returns/{rid}/credit-confirmations", json={
        **body, "source_selections": shown["source_selections"],
        "allocations": [{"source_key": shown["sources"][0]["source_key"], "taxable": 1000,
                         "cgst": 0, "sgst": 0, "igst": 0}]})
    assert done.status_code == 200 and done.json()["confirmation"]["posting_status"] == "POSTED", done.text
    assert posted(rw) == (1, 100000)


def test_a_credit_return_with_no_note_waiting_still_ends_as_replaced(rw):
    """Accounts forgoing the credit stays Accounts' to do, and only theirs."""
    bought(rw, "U1", "U2", qty=2)
    rid = received_return(rw, "U1", "U2")
    assert replace(rw, rid, user=MANAGER).status_code == 403
    assert replace(rw, rid).status_code == 200
    assert returned(rw, rid)["status"] == "replaced"


def test_a_rejected_rma_and_a_refused_note_do_not_hold_the_return(rw):
    bought(rw, "U1", "U2", qty=2)
    rid = received_return(rw, "U1", "U2")
    rma_id = linked_rma(rw, rid)
    rw["db"].supplier_credit_confirmations.insert_one({
        "_id": str(uuid4()), "return_id": rma_id, "return_kind": "RMA", "identity_claimed": False,
        "posting_status": "PENDING_EVIDENCE", "pending_reasons": []})
    assert rw["as_"](ACCOUNTANT).post(f"/vendor-rma/{rma_id}/reject", json={"reason": "x"}).status_code == 200
    assert replace(rw, rid).status_code == 200


def test_a_replacement_return_of_a_sold_out_product_still_creates(rw):
    """The owner's ruling is about a supplier credit: a replacement takes none."""
    bought(rw, "U1", "U2", qty=2)
    sold_rest(rw, "U1", "U2")
    made = create(rw, line(qty=1), notes="Warranty swap, no barcode", return_type="replacement")
    assert made.status_code in (200, 201), made.text
    rid = made.json()["return_id"]
    for step in ("approved", "shipped", "received_by_vendor", "replaced"):
        assert move(rw, rid, step).status_code == 200, step
    assert statuses(rw) == Counter({"SOLD": 2})
    # The same line as a credit return is still refused.
    assert create(rw, line(qty=1), notes="Warranty").status_code == 422


def test_a_replacement_line_still_cannot_claim_goods_accounted_for_elsewhere(rw):
    bought(rw, "U1", "U2", qty=2)
    landed_at_b(rw, "U1", "U2")
    refused = create(rw, line(qty=1), notes="Swap", return_type="replacement")
    assert refused.status_code == 422 and "are now held at" in refused.json()["detail"]


# --------------------------------------------------------------------------
# A claimed note, and a physical refusal that lands after it
# --------------------------------------------------------------------------
HELD = "head-office decision"


def off_shelf_rma(w, qty=2):
    """An unticked RMA that reached the supplier while this shop held no piece
    of its product: the physical rule passed it, and it was right to."""
    bought(w, qty=qty)
    made = raise_rma(w, qty=qty, grn_id="G-OWN")
    assert made.status_code == 201, made.text
    return to_the_supplier(w, made.json()["rma_id"])


def piece_since(w, status):
    """A later delivery's piece of the same product: not what went back."""
    w["db"].grns.insert_one({"grn_id": "G-NEXT", "store_id": STORE, "vendor_id": DEALER, "status": "ACCEPTED",
                             "items": [{"product_id": FRAME, "accepted_qty": 1}]})
    unit(w, "U9", status=status, grn_id="G-NEXT", source_type="GRN", source_id="G-NEXT")


def before_the_rule(w):
    """The note as a row saved before claims recorded the physical rule."""
    w["db"].supplier_credit_confirmations.update_many({}, {"$unset": {"physical_rule_passed": ""}})


def waiting(w):
    return w["db"].supplier_credit_confirmations.find_one({"posting_status": "PENDING_EVIDENCE"})


@pytest.mark.parametrize("status", ["AVAILABLE", "SOLD"])
def test_a_piece_arriving_after_a_note_was_claimed_does_not_strand_it(rw, monkeypatch, status):
    rma_id, body = pending_note(rw, monkeypatch, rma_id=off_shelf_rma(rw))
    piece_since(rw, status)
    http = rw["as_"](ACCOUNTANT)
    told = http.post(f"/vendor-rma/{rma_id}/reject", json={"reason": "x"})
    assert told.status_code == 409 and "retry that same note" in told.text, told.text
    done = finish_pending(rw, rma_id, body, 1000)
    assert done.status_code == 200 and done.json()["confirmation"]["posting_status"] == "POSTED", done.text
    assert posted(rw) == (1, 100000) and credited(rw) == 100000
    assert rw["as_"](MANAGER).post(f"/vendor-rma/{rma_id}/close", json={}).status_code == 200
    # The late piece was never the RMA's: it stays as it is, and a retry moves nothing.
    assert state(rw, "U9")["status"] == status and not events(rw, "U9")
    assert finish_pending(rw, rma_id, body, 1000).json()["idempotent"] is True
    assert posted(rw) == (1, 100000) and credited(rw) == 100000


def test_a_later_partial_note_on_that_rma_is_not_refused_by_the_late_piece(rw, monkeypatch):
    rma_id, body = pending_note(rw, monkeypatch, amount=600, rma_id=off_shelf_rma(rw))
    piece_since(rw, "AVAILABLE")
    assert finish_pending(rw, rma_id, body, 600).status_code == 200
    _, balance = rma_note(rw, rma_id, 400, "CN-BALANCE")
    assert balance.status_code == 200, balance.text
    assert rw["as_"](MANAGER).post(f"/vendor-rma/{rma_id}/close", json={}).status_code == 200
    assert posted(rw) == (2, 100000) and credited(rw) == 100000
    assert state(rw, "U9")["status"] == "AVAILABLE"


@pytest.mark.parametrize("status, why", [("AVAILABLE", "holds pieces"), ("SOLD", "been sold")])
def test_a_note_from_before_the_rule_is_told_truthfully_it_cannot_finish(rw, monkeypatch, status, why):
    """OWNER DECISION: nothing says this note passed the rule, so it cannot post
    over it, and no route frees it. Every door says so; none says retry."""
    rma_id, body = pending_note(rw, monkeypatch, rma_id=off_shelf_rma(rw))
    before_the_rule(rw)
    piece_since(rw, status)
    http = rw["as_"](ACCOUNTANT)
    for res in (http.post(f"/vendor-rma/{rma_id}/credit-note", json=body),
                http.post(f"/vendor-rma/{rma_id}/reject", json={"reason": "x"}),
                rw["as_"](MANAGER).post(f"/vendor-rma/{rma_id}/close", json={})):
        assert res.status_code == 409 and HELD in res.text and why in res.text, res.text
        assert "retry that same note" not in res.text
    assert rw["db"].vendor_rmas.find_one({"rma_id": rma_id})["status"] == "DISPATCHED"
    assert waiting(rw)["identity_claimed"] is True and posted(rw) == (0, 0) and credited(rw) == 0


def test_a_note_that_claimed_nothing_is_not_cleared_by_having_been_tried(rw):
    """Refused outright, it claimed no number: a corrected retry after the piece
    arrived meets the rule with no hold, and the RMA can still end."""
    rma_id = off_shelf_rma(rw)
    http = rw["as_"](ACCOUNTANT)
    shown = http.post(f"/vendor-rma/{rma_id}/credit-preview", json={}).json()
    body = {"credit_note_number": "CN-TYPO", "credit_note_date": "2026-10-18", "idempotency_key": str(uuid4()),
            "received_amount": 1001, "source_selections": shown["source_selections"],
            "allocations": [{"source_key": shown["sources"][0]["source_key"], "taxable": 1001,
                             "cgst": 0, "sgst": 0, "igst": 0}]}
    assert http.post(f"/vendor-rma/{rma_id}/credit-note", json=body).status_code == 422
    piece_since(rw, "AVAILABLE")
    res = http.post(f"/vendor-rma/{rma_id}/credit-note", json=body)
    assert res.status_code == 409 and "holds pieces" in res.text and HELD not in res.text, res.text
    assert http.post(f"/vendor-rma/{rma_id}/reject", json={"reason": "x"}).status_code == 200
    assert posted(rw) == (0, 0)


def test_a_linked_notes_own_pieces_are_still_checked_on_its_retry(rw, monkeypatch):
    """The answer a claim fixes is 'no piece of this product was held', never
    'this return's pieces went back': one that came back stops the note."""
    rma_id, body = pending_note(rw, monkeypatch)
    rw["db"].stock_units.update_one({"stock_id": "U1"}, {"$set": {"status": "AVAILABLE"}})
    http = rw["as_"](ACCOUNTANT)
    for res in (finish_pending(rw, rma_id, body, 1000), http.post(f"/vendor-rma/{rma_id}/reject", json={"reason": "x"})):
        assert res.status_code == 409 and HELD in res.text and "no longer held" in res.text, res.text
    rid = rw["db"].vendor_rmas.find_one({"rma_id": rma_id})["return_id"]
    refused = replace(rw, rid)
    assert refused.status_code == 409 and rma_id in refused.text, refused.text
    assert "retry that same note" not in refused.text
    assert posted(rw) == (0, 0) and credited(rw) == 0


def pending_on_return(w, monkeypatch, rid):
    body = {"credit_note_number": "CN-TYPED", "credit_note_date": "2026-10-18", "idempotency_key": str(uuid4()),
            "received_amount": 1000, "source_selections": [], "allocations": []}

    def unavailable(*_a, **_k):
        raise ReturnProvenanceError("EVIDENCE_DOWN", "Original evidence unavailable")

    with monkeypatch.context() as patched:
        patched.setattr(rma_credit.canonical, "resolve_credit_evidence", unavailable)
        saved = w["as_"](ACCOUNTANT).post(f"/vendor-returns/{rid}/credit-confirmations", json=body)
    assert saved.status_code == 200 and saved.json()["confirmation"]["posting_status"] == "PENDING_EVIDENCE", saved.text
    return body


def sold_since(w):
    unit(w, "U1", status="SOLD", grn_id="G-OWN", source_type="GRN", source_id="G-OWN")
    unit(w, "U2", status="SOLD", grn_id="G-OWN", source_type="GRN", source_id="G-OWN")


def test_a_typed_returns_note_claimed_before_its_pieces_sold_still_finishes(rw, monkeypatch):
    bought(rw, qty=2)
    rid = typed_return(rw)
    body = pending_on_return(rw, monkeypatch, rid)
    sold_since(rw)
    told = replace(rw, rid)
    assert told.status_code == 409 and "retry that same note" in told.text, told.text
    http = rw["as_"](ACCOUNTANT)
    shown = http.post(f"/vendor-returns/{rid}/credit-preview", json={"confirmation_id": body["idempotency_key"]}).json()
    full = {**body, "source_selections": shown["source_selections"],
            "allocations": [{"source_key": shown["sources"][0]["source_key"], "taxable": 1000,
                             "cgst": 0, "sgst": 0, "igst": 0}]}
    done = http.post(f"/vendor-returns/{rid}/credit-confirmations", json=full)
    assert done.status_code == 200 and done.json()["confirmation"]["posting_status"] == "POSTED", done.text
    assert posted(rw) == (1, 100000) and credited(rw) == 100000
    assert http.post(f"/vendor-returns/{rid}/credit-confirmations", json=full).json()["idempotent"] is True
    assert posted(rw) == (1, 100000)


def test_a_typed_returns_note_from_before_the_rule_is_told_truthfully_too(rw, monkeypatch):
    bought(rw, qty=2)
    rid = typed_return(rw)
    body = pending_on_return(rw, monkeypatch, rid)
    before_the_rule(rw)
    sold_since(rw)
    for res in (rw["as_"](ACCOUNTANT).post(f"/vendor-returns/{rid}/credit-confirmations", json=body), replace(rw, rid)):
        assert res.status_code == 409 and HELD in res.text and "been sold" in res.text, res.text
        assert "retry that same note" not in res.text
    assert returned(rw, rid)["status"] == "received_by_vendor"
    assert waiting(rw)["identity_claimed"] is True and posted(rw) == (0, 0) and credited(rw) == 0
