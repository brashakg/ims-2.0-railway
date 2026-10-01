"""No receipt can be accepted against a cancelled quantity.

The order is the authority: a receipt logged AFTER a line cancel is refused at
create (normal and express), and a receipt logged BEFORE the cancel is refused
at accept. One rule (vendors.po_detail.beyond_open_quantity) serves all of it.
"""

import asyncio
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import test_express_receive as ex  # noqa: E402
from test_express_receive import vendors_mod, _user, _wire  # noqa: E402


def _line(pid, name, qty, **extra):
    row = {"product_id": pid, "product_name": name, "sku": pid, "quantity": qty,
           "ordered_qty": qty, "unit_price": 100.0, "gst_rate": 12, "hsn": "9003",
           "received_qty": 0, "line_status": "OPEN"}
    row.update(extra)
    return row


def _po(p1, p2, status="SENT"):
    po = ex._po(status=status)
    po["items"] = [p1, p2]
    return po


def _fully_cancelled_p2():
    """P2 (3 ordered) withdrawn outright: quantity 0, CANCELLED."""
    return _po(_line("P1", "Frame X", 5),
               _line("P2", "Ray-Ban", 0, ordered_qty=0, cancelled_qty=3,
                     line_status="CANCELLED"))


def _part_cancelled_p2():
    """P2 ordered 3, 1 received, the other 2 withdrawn: quantity 1."""
    po = _po(_line("P1", "Frame X", 5),
             _line("P2", "Ray-Ban", 1, ordered_qty=1, cancelled_qty=2,
                   received_qty=1, line_status="RECEIVED"))
    po["status"] = "PARTIALLY_RECEIVED"
    po["received_qty_by_product"] = {"P2": 1}
    return po


def _items(pid, qty):
    return [{"product_id": pid, "received_qty": qty, "accepted_qty": qty,
             "rejected_qty": 0}]


def _normal(items, inv="INV-9"):
    return vendors_mod.GRNCreate(
        po_id="PO-1", vendor_invoice_no=inv, vendor_invoice_date="2026-07-01",
        items=[{**i, "tallied": True} for i in items],
        attachment_file_id="FILE-1", attachment_filename="inv.pdf",
        attachment_mime="application/pdf",
    )


def _express(items):
    return ex._body(items=items, vendor_invoice_no="INV-9")


def _create(kind, items, user, inv="INV-9"):
    if kind == "normal":
        return asyncio.run(vendors_mod.create_grn(_normal(items, inv), current_user=user))
    return asyncio.run(vendors_mod.express_receive_grn(_express(items), current_user=user))


CASES = [("normal", "full"), ("normal", "part"), ("express", "full"), ("express", "part")]


@pytest.mark.parametrize("kind,cut", CASES)
def test_a_receipt_logged_after_the_cancel_is_refused_at_create(monkeypatch, kind, cut):
    po = _fully_cancelled_p2() if cut == "full" else _part_cancelled_p2()
    grn_repo, _po_repo, stock, _t = _wire(monkeypatch, po=po)
    with pytest.raises(HTTPException) as e:
        _create(kind, _items("P2", 3 if cut == "full" else 2), _user())
    assert e.value.status_code == 400
    assert "Ray-Ban" in e.value.detail
    assert grn_repo.docs == {} and stock.units == [], "nothing logged, nothing minted"


@pytest.mark.parametrize("kind", ["normal", "express"])
def test_the_uncancelled_product_still_receives(monkeypatch, kind):
    grn_repo, _po_repo, stock, _t = _wire(monkeypatch, po=_part_cancelled_p2())
    _create(kind, _items("P1", 5), _user())
    assert len(grn_repo.docs) == 1


def test_the_one_unit_still_open_on_a_part_cancelled_line_receives(monkeypatch):
    po = _part_cancelled_p2()
    # One of four withdrawn: 3 live, 1 already in.
    po["items"][1].update(quantity=3, ordered_qty=3, received_qty=1,
                          cancelled_qty=1, line_status="PARTIAL")
    grn_repo, *_ = _wire(monkeypatch, po=po)
    _create("normal", _items("P2", 2), _user())
    assert len(grn_repo.docs) == 1
    with pytest.raises(HTTPException) as e:  # 1 received + 3 > 3 live
        _create("normal", _items("P2", 3), _user(), inv="INV-10")
    assert e.value.status_code == 400


@pytest.mark.parametrize("cut", ["full", "part"])
def test_a_receipt_logged_before_the_cancel_is_refused_at_accept(monkeypatch, cut):
    full = _po(_line("P1", "Frame X", 5), _line("P2", "Ray-Ban", 3))
    grn_repo, po_repo, stock, _t = _wire(monkeypatch, po=full)
    made = _create("normal", _items("P2", 3 if cut == "full" else 2), _user())
    gid = made["grn_id"]
    # The cancel lands afterwards.
    po_repo.po.clear()
    po_repo.po.update(copy.deepcopy(
        _fully_cancelled_p2() if cut == "full" else _part_cancelled_p2()))
    with pytest.raises(HTTPException) as e:
        asyncio.run(vendors_mod.accept_grn(gid, current_user=_user()))
    assert e.value.status_code == 409
    assert "Ray-Ban" in e.value.detail
    assert stock.units == [], "no stock against the cancelled units"
    assert grn_repo.docs[gid]["status"] == "PENDING"


def _atomic_claims(grn_repo):
    """Give the fake receipt repo the guarded-update primitive the real one has,
    so the accept CLAIM is recorded (a repo without it fails open)."""
    import types
    from strict_fakes import matches

    def find_one_and_update(self, flt, patch):
        for d in self.docs.values():
            if matches(d, flt):
                before = dict(d)
                d.update(patch.get("$set", {}))
                return before
        return None

    grn_repo.find_one_and_update = types.MethodType(find_one_and_update, grn_repo)


def _two_pending_of_two(monkeypatch):
    po = _po(_line("P1", "Frame X", 5),
             _line("P2", "Ray-Ban", 3, cancelled_qty=1, received_qty=1))
    po["status"] = "PARTIALLY_RECEIVED"
    po["received_qty_by_product"] = {"P2": 1}
    grn_repo, po_repo, stock, _t = _wire(monkeypatch, po=po)
    _atomic_claims(grn_repo)
    a = _create("normal", _items("P2", 2), _user(), inv="INV-A")["grn_id"]
    b = _create("normal", _items("P2", 2), _user(), inv="INV-B")["grn_id"]
    return grn_repo, stock, a, b


def test_two_accepts_in_flight_on_one_open_quantity_mint_once(monkeypatch):
    """2 open, two pending receipts of 2: while A is still minting, B's accept
    is refused -- exactly 2 units, never 4."""
    import threading

    grn_repo, stock, a, b = _two_pending_of_two(monkeypatch)
    inside, release = threading.Event(), threading.Event()
    real_create = stock.create

    def slow_create(doc):
        inside.set()
        assert release.wait(10)
        return real_create(doc)

    stock.create = slow_create
    outcome = {}

    def accept_a():
        try:
            outcome["a"] = asyncio.run(vendors_mod.accept_grn(a, current_user=_user()))
        except Exception as exc:  # noqa: BLE001
            outcome["a"] = exc

    t = threading.Thread(target=accept_a)
    t.start()
    assert inside.wait(10), "A never reached its first stock create"
    try:
        with pytest.raises(HTTPException) as e:
            asyncio.run(vendors_mod.accept_grn(b, current_user=_user()))
        assert e.value.status_code == 409
    finally:
        release.set()
        t.join(15)
    assert not isinstance(outcome.get("a"), Exception), outcome
    assert len([u for u in stock.units if u["product_id"] == "P2"]) == 2


def test_two_accepts_one_after_the_other_mint_once(monkeypatch):
    grn_repo, stock, a, b = _two_pending_of_two(monkeypatch)
    asyncio.run(vendors_mod.accept_grn(a, current_user=_user()))
    with pytest.raises(HTTPException) as e:
        asyncio.run(vendors_mod.accept_grn(b, current_user=_user()))
    assert e.value.status_code == 409
    assert len([u for u in stock.units if u["product_id"] == "P2"]) == 2
