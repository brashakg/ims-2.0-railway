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


def _two_pending_of_two(monkeypatch, qa=2, qb=2):
    po = _po(_line("P1", "Frame X", 5),
             _line("P2", "Ray-Ban", 3, cancelled_qty=1, received_qty=1))
    po["status"] = "PARTIALLY_RECEIVED"
    po["received_qty_by_product"] = {"P2": 1}
    grn_repo, po_repo, stock, _t = _wire(monkeypatch, po=po)
    _atomic_claims(grn_repo)
    a = _create("normal", _items("P2", qa), _user(), inv="INV-A")["grn_id"]
    b = _create("normal", _items("P2", qb), _user(), inv="INV-B")["grn_id"]
    return grn_repo, stock, a, b


# (2, 2): both want the whole open 2. (1, 2): A's 1 still to come is ADDED to
# the 1 the order already holds, not compared with it -- 1 + 1 + 2 > 3.
@pytest.mark.parametrize("qa,qb", [(2, 2), (1, 2)])
def test_two_accepts_in_flight_on_one_open_quantity_mint_once(monkeypatch, qa, qb):
    """2 open, two pending receipts: while A is still minting, B's accept is
    refused -- only A's units, never more than the order has room for."""
    import threading

    grn_repo, stock, a, b = _two_pending_of_two(monkeypatch, qa, qb)
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
    assert len([u for u in stock.units if u["product_id"] == "P2"]) == qa


def test_two_accepts_one_after_the_other_mint_once(monkeypatch):
    grn_repo, stock, a, b = _two_pending_of_two(monkeypatch)
    asyncio.run(vendors_mod.accept_grn(a, current_user=_user()))
    with pytest.raises(HTTPException) as e:
        asyncio.run(vendors_mod.accept_grn(b, current_user=_user()))
    assert e.value.status_code == 409
    assert len([u for u in stock.units if u["product_id"] == "P2"]) == 2


def _p2_units(stock):
    return len([u for u in stock.units if u["product_id"] == "P2"])


def _accept(gid):
    try:
        return asyncio.run(vendors_mod.accept_grn(gid, current_user=_user()))
    except HTTPException as exc:
        return exc


class _Products:
    def __init__(self, *pids):
        self.docs = {p: {"product_id": p, "cost_price": 100, "cost_source": "MANUAL"} for p in pids}

    def find_by_id(self, pid):
        d = self.docs.get(pid)
        return dict(d) if d else None

    def update(self, pid, patch):
        return True


def test_units_a_part_accepted_receipt_put_in_stock_count_at_the_next_accept(monkeypatch):
    """2 of P2 live (2 more cancelled outright); P3 not catalogue-complete.
    R1 = P2 x2 + P3 x1 and R2 = P2 x2, both logged before any accept. R1's
    accept stocks its 2 P2 and holds P3, so it is PARTIALLY_ACCEPTED with no
    claim and in no accepted sum -- its 2 units are still on the shelf, so R2
    is refused. R1 can still be accepted again for P3."""
    from api.services import product_master as pm

    monkeypatch.setattr(
        pm, "compute_catalog_status",
        lambda prod: ("DRAFT", ["hsn_code"]) if prod.get("product_id") == "P3" else ("ACTIVE", []),
    )
    po = _po(_line("P2", "Ray-Ban", 2),
             _line("P2", "Ray-Ban", 0, ordered_qty=0, cancelled_qty=2, line_status="CANCELLED"))
    po["items"].append(_line("P3", "Vogue", 1))
    grn_repo, _po_repo, stock, _t = _wire(monkeypatch, po=po, product_repo=_Products("P2", "P3"))
    _atomic_claims(grn_repo)
    r1 = _create("normal", _items("P2", 2) + _items("P3", 1), _user(), inv="INV-A")["grn_id"]
    r2 = _create("normal", _items("P2", 2), _user(), inv="INV-B")["grn_id"]
    assert _accept(r1)["grn_status"] == "PARTIALLY_ACCEPTED"
    out = _accept(r2)
    assert isinstance(out, HTTPException) and out.status_code == 409, out
    assert _p2_units(stock) == 2
    # Its own units are not counted against R1 itself: once P3 is catalogued,
    # accepting R1 again finishes it.
    monkeypatch.setattr(pm, "compute_catalog_status", lambda prod: ("ACTIVE", []))
    assert _accept(r1)["grn_status"] == "ACCEPTED"
    assert _p2_units(stock) == 2


def test_units_of_an_escalated_receipt_count_at_the_next_accept(monkeypatch):
    """3 of P2 live (1 cancelled up front). R0 (P2 x1) is accepted, then
    escalated, so it drops out of the accepted sum; its unit is still on the
    shelf. A (P2 x1) then B (P2 x2): 1 + 1 + 2 > 3, so B is refused."""
    po = _po(_line("P2", "Ray-Ban", 3),
             _line("P2", "Ray-Ban", 0, ordered_qty=0, cancelled_qty=1, line_status="CANCELLED"))
    grn_repo, _po_repo, stock, _t = _wire(monkeypatch, po=po)
    _atomic_claims(grn_repo)
    r0 = _create("normal", _items("P2", 1), _user(), inv="INV-0")["grn_id"]
    _accept(r0)
    asyncio.run(vendors_mod.escalate_grn(r0, note="price dispute", current_user=_user()))
    a = _create("normal", _items("P2", 1), _user(), inv="INV-A")["grn_id"]
    b = _create("normal", _items("P2", 2), _user(), inv="INV-B")["grn_id"]
    assert _accept(a)["grn_status"] == "ACCEPTED"
    out = _accept(b)
    assert isinstance(out, HTTPException) and out.status_code == 409, out
    assert _p2_units(stock) == 2


@pytest.mark.parametrize("k", range(1, 10))
def test_an_accept_that_finishes_while_another_reads_is_seen_by_it(monkeypatch, k):
    """2 open. A claims and passes its check, then B claims and reads. At the
    k-th read of B's check A finishes -- its 2 units in stock, its receipt
    ACCEPTED, its claim released, the ORDER NOT YET WRITTEN. Wherever that
    lands, B is refused: 2 units, never 4."""
    from api.routers.vendors import grn_accept as ga

    grn_repo, stock, a, b = _two_pending_of_two(monkeypatch)
    po_repo = vendors_mod.get_purchase_order_repository()
    token = ga._claim_grn_for_accept(grn_repo, a, "mgr_a")
    ga._hold_order_open_for_receipt(po_repo, grn_repo.find_by_id(a))
    done = []

    def finish_a():
        cas = po_repo.update_if
        po_repo.update_if = lambda *_a, **_k: False  # A's order write not landed
        try:
            ga._accept_grn_claimed(a, grn_repo.find_by_id(a), _user(), grn_repo,
                                   stock, po_repo, token)
        finally:
            po_repo.update_if = cas
        done.append(True)

    reads = {"n": 0}

    def counted(fn):
        def read(*args, **kw):
            reads["n"] += 1
            if reads["n"] == k:
                finish_a()
            return fn(*args, **kw)
        return read

    grn_repo.find_many = counted(grn_repo.find_many)
    stock.count = counted(stock.count)
    out = _accept(b)
    assert isinstance(out, HTTPException) and out.status_code == 409, out
    assert not [u for u in stock.units if u["source_id"] == b]
    if not done:
        finish_a()
    assert _p2_units(stock) == 2


def test_units_of_a_receipt_whose_status_did_not_flip_count_at_the_next_accept(monkeypatch):
    """2 of P2 live (2 cancelled outright). R1's accept mints its 2 but its
    status write fails: R1 stays PENDING with no claim, in no accepted sum.
    R2 (P2 x2) is refused; R1 accepted again finishes without a new unit."""
    from api.routers.vendors import grn_accept as ga

    po = _po(_line("P2", "Ray-Ban", 2),
             _line("P2", "Ray-Ban", 0, ordered_qty=0, cancelled_qty=2, line_status="CANCELLED"))
    grn_repo, _po_repo, stock, _t = _wire(monkeypatch, po=po)
    _atomic_claims(grn_repo)
    r1 = _create("normal", _items("P2", 2), _user(), inv="INV-A")["grn_id"]
    r2 = _create("normal", _items("P2", 2), _user(), inv="INV-B")["grn_id"]
    with monkeypatch.context() as m:
        m.setattr(ga, "_advance_grn_terminal_status", lambda *_a: False)
        assert _accept(r1)["status_flip_failed"] is True
    assert grn_repo.docs[r1]["status"] == "PENDING" and _p2_units(stock) == 2
    out = _accept(r2)
    assert isinstance(out, HTTPException) and out.status_code == 409, out
    assert _accept(r1)["grn_status"] == "ACCEPTED"
    assert _p2_units(stock) == 2


# --------------------------------------------------------------------------- #
# Review round 10.
# --------------------------------------------------------------------------- #


def test_an_unreadable_receipt_list_refuses_the_accept(monkeypatch):
    """A holds its claim; B's read of the order's receipts errors. Through the
    real repository (whose find_many turns an error into []) that read must
    still fail CLOSED -- 503, nothing minted -- not read 'nobody in flight'."""
    from database.repositories.vendor_repository import GRNRepository
    from strict_fakes import StrictCollection
    from api.routers.vendors import grn_accept as ga

    fake, stock, a, b = _two_pending_of_two(monkeypatch)
    real = GRNRepository(StrictCollection("grns", [copy.deepcopy(d) for d in fake.docs.values()]))
    monkeypatch.setattr(vendors_mod, "get_grn_repository", lambda: real)
    assert ga._claim_grn_for_accept(real, a, "mgr_a")
    real_find = real.collection.find

    def find(flt=None, *args, **kw):
        if flt == {"po_id": "PO-1"}:
            raise RuntimeError("socket timeout")
        return real_find(flt, *args, **kw)

    real.collection.find = find
    out = _accept(b)
    assert isinstance(out, HTTPException) and out.status_code == 503, out
    assert _p2_units(stock) == 0


def _parked(monkeypatch, fn_owner, name, park_on):
    """Wrap `fn_owner.name` so the first call for which park_on(*args) is true
    waits (on its own thread) until released. Returns (inside, release)."""
    import threading

    inside, release = threading.Event(), threading.Event()
    real = getattr(fn_owner, name)

    def wrapper(*args, **kw):
        if not inside.is_set() and park_on(*args):
            inside.set()
            assert release.wait(10)
        return real(*args, **kw)

    monkeypatch.setattr(fn_owner, name, wrapper)
    return inside, release


def _in_background(gid):
    import threading

    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", _accept(gid)))
    t.start()
    return t, out


def test_a_receipt_in_flight_counts_whole_when_stock_rows_are_gone(monkeypatch):
    """P2: 5 live, 1 cancelled. R0 is ACCEPTED for 3 but its stock rows are
    gone (the order's own count still says 3), so 2 are open. B (2) is parked
    after stocking its first unit; C (1) is accepted meanwhile and refused --
    B's stocked unit is in no other count, so B counts whole."""
    po = _po(_line("P1", "Frame X", 5),
             _line("P2", "Ray-Ban", 5, ordered_qty=5, cancelled_qty=1, received_qty=3,
                   line_status="PARTIAL"))
    po["status"] = "PARTIALLY_RECEIVED"
    po["received_qty_by_product"] = {"P2": 3}
    grn_repo, _po_repo, stock, _t = _wire(monkeypatch, po=po)
    _atomic_claims(grn_repo)
    grn_repo.docs["R0"] = {"grn_id": "R0", "po_id": "PO-1", "status": "ACCEPTED",
                           "store_id": "STORE-A", "items": _items("P2", 3)}
    b = _create("normal", _items("P2", 2), _user(), inv="INV-B")["grn_id"]
    c = _create("normal", _items("P2", 1), _user(), inv="INV-C")["grn_id"]
    calls = {"n": 0}

    def second_create(doc):
        calls["n"] += 1
        return calls["n"] == 2

    inside, release = _parked(monkeypatch, stock, "create", second_create)
    t, out_b = _in_background(b)
    assert inside.wait(10), "B never reached its second unit"
    try:
        out = _accept(c)
        assert isinstance(out, HTTPException) and out.status_code == 409, out
    finally:
        release.set()
        t.join(15)
    assert out_b["r"]["grn_status"] == "ACCEPTED", out_b
    assert _p2_units(stock) == 2
