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
    """3 of P2 live (1 cancelled up front). R0 (P2 x1 + P3 x1, P3 not
    catalogue-complete) stocks its P2 and holds P3, then is escalated (an
    ACCEPTED receipt cannot be: owner ruling R3), so it is in no accepted
    sum; its unit is still on the shelf. A (P2 x1) then B (P2 x2): 1 + 1 + 2
    > 3, so B is refused."""
    from api.services import product_master as pm

    monkeypatch.setattr(
        pm, "compute_catalog_status",
        lambda prod: ("DRAFT", ["hsn_code"]) if prod.get("product_id") == "P3" else ("ACTIVE", []),
    )
    po = _po(_line("P2", "Ray-Ban", 3),
             _line("P2", "Ray-Ban", 0, ordered_qty=0, cancelled_qty=1, line_status="CANCELLED"))
    po["items"].append(_line("P3", "Vogue", 1))
    grn_repo, _po_repo, stock, _t = _wire(monkeypatch, po=po, product_repo=_Products("P2", "P3"))
    _atomic_claims(grn_repo)
    r0 = _create("normal", _items("P2", 1) + _items("P3", 1), _user(), inv="INV-0")["grn_id"]
    assert _accept(r0)["grn_status"] == "PARTIALLY_ACCEPTED"
    asyncio.run(vendors_mod.escalate_grn(r0, note="price dispute", current_user=_user()))
    assert grn_repo.docs[r0]["status"] == "ESCALATED"
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


def _mongo_filters(grn_repo):
    """find_many that reads a filter as Mongo does ($in and all), so the
    cancel's "a receipt is still waiting" look sees the fake's receipts."""
    import types
    from strict_fakes import matches

    def find_many(self, flt, limit=1000):
        return [dict(d) for d in self.docs.values() if matches(d, flt or {})][:limit]

    grn_repo.find_many = types.MethodType(find_many, grn_repo)


def test_an_accept_keeps_what_a_cancel_closed_over_shelved_units(monkeypatch):
    """P1 3, P2 5, P3 1. R0 = P2 x2 + P3 x1 holds P3 (not catalogued), so its 2
    P2 are on the shelf; R0 is escalated. Cancelling the P2 line leaves it at
    2 received, 3 cancelled. Accepting R1 (P1 x3) then re-derives the order
    from the same count the cancel wrote -- the P2 line stays received."""
    from api.services import product_master as pm

    monkeypatch.setattr(
        pm, "compute_catalog_status",
        lambda prod: ("DRAFT", ["hsn_code"]) if prod.get("product_id") == "P3" else ("ACTIVE", []),
    )
    po = _po(_line("P1", "Frame X", 3), _line("P2", "Ray-Ban", 5))
    po["items"].append(_line("P3", "Vogue", 1))
    po.update(vendor_gstin="", store_gstin="")
    grn_repo, po_repo, stock, _t = _wire(monkeypatch, po=po,
                                         product_repo=_Products("P1", "P2", "P3"))
    _atomic_claims(grn_repo)
    _mongo_filters(grn_repo)
    r0 = _create("normal", _items("P2", 2) + _items("P3", 1), _user(), inv="INV-0")["grn_id"]
    assert _accept(r0)["grn_status"] == "PARTIALLY_ACCEPTED"
    asyncio.run(vendors_mod.escalate_grn(r0, note="held line", current_user=_user()))
    asyncio.run(vendors_mod.cancel_po_line(
        "PO-1", 1, vendors_mod.POLineCancel(reason="vendor short", product_id="P2"), _user()))
    p2 = po_repo.po["items"][1]
    assert (p2["quantity"], p2["received_qty"], p2["cancelled_qty"], p2["line_status"]) == (
        2, 2, 3, "RECEIVED")
    r1 = _create("normal", _items("P1", 3), _user(), inv="INV-1")["grn_id"]
    assert _accept(r1)["grn_status"] == "ACCEPTED"
    p2 = po_repo.po["items"][1]
    assert (p2["received_qty"], p2["line_status"]) == (2, "RECEIVED")
    assert po_repo.po["received_qty_by_product"]["P2"] == 2
    assert po_repo.po["items"][0]["received_qty"] == 3


def test_the_claim_comes_before_the_read_a_parked_before_its_claim(monkeypatch):
    """2 open. A's accept stops just before it claims; B runs to the end and
    mints 2. A then claims, reads B's receipt and is refused -- had A read the
    order BEFORE claiming, both would mint (4 against 2 open)."""
    from api.routers.vendors import grn_accept as ga

    grn_repo, stock, a, b = _two_pending_of_two(monkeypatch)
    inside, release = _parked(monkeypatch, ga, "_claim_grn_for_accept",
                              lambda _repo, gid, *_: gid == a)
    t, out_a = _in_background(a)
    assert inside.wait(10), "A never reached its claim"
    try:
        assert _accept(b)["grn_status"] == "ACCEPTED"
    finally:
        release.set()
        t.join(15)
    assert isinstance(out_a["r"], HTTPException) and out_a["r"].status_code == 409, out_a
    assert _p2_units(stock) == 2


def test_a_catalog_now_re_accept_in_flight_is_seen(monkeypatch):
    """2 of P2 live (2 cancelled). R1 (P2 x2) is held -- P2 not catalogued
    yet -- so it is PARTIALLY_ACCEPTED with nothing in stock. P2 is
    catalogued and R1 accepted again; while it mints, R2 (P2 x2) is refused:
    a held receipt being accepted again is in flight like a pending one."""
    from api.services import product_master as pm

    po = _po(_line("P2", "Ray-Ban", 2),
             _line("P2", "Ray-Ban", 0, ordered_qty=0, cancelled_qty=2, line_status="CANCELLED"))
    grn_repo, _po_repo, stock, _t = _wire(monkeypatch, po=po, product_repo=_Products("P2"))
    _atomic_claims(grn_repo)
    r1 = _create("normal", _items("P2", 2), _user(), inv="INV-A")["grn_id"]
    r2 = _create("normal", _items("P2", 2), _user(), inv="INV-B")["grn_id"]
    monkeypatch.setattr(pm, "compute_catalog_status", lambda prod: ("DRAFT", ["hsn_code"]))
    assert _accept(r1)["grn_status"] == "PARTIALLY_ACCEPTED" and _p2_units(stock) == 0
    monkeypatch.setattr(pm, "compute_catalog_status", lambda prod: ("ACTIVE", []))
    inside, release = _parked(monkeypatch, stock, "create", lambda doc: True)
    t, out_r1 = _in_background(r1)
    assert inside.wait(10), "R1 never reached its first stock create"
    try:
        out = _accept(r2)
        assert isinstance(out, HTTPException) and out.status_code == 409, out
    finally:
        release.set()
        t.join(15)
    assert out_r1["r"]["grn_status"] == "ACCEPTED", out_r1
    assert _p2_units(stock) == 2


def _held_then_full(monkeypatch, with_p1):
    """2 of P2 live (2 cancelled), P1 3 open. R1 (P2 x2, plus P1 x3 when
    `with_p1`) is accepted while P2 is not catalogued: P2 is held. P2 is
    catalogued and R2 (P2 x2) takes the room. R1 can no longer be accepted."""
    from api.services import product_master as pm

    po = _po(_line("P1", "Frame X", 3), _line("P2", "Ray-Ban", 2))
    po["items"].append(_line("P2", "Ray-Ban", 0, ordered_qty=0, cancelled_qty=2,
                             line_status="CANCELLED"))
    grn_repo, _po_repo, stock, _t = _wire(monkeypatch, po=po, product_repo=_Products("P1", "P2"))
    _atomic_claims(grn_repo)
    _mongo_filters(grn_repo)
    lines = _items("P2", 2) + (_items("P1", 3) if with_p1 else [])
    r1 = _create("normal", lines, _user(), inv="INV-A")["grn_id"]
    r2 = _create("normal", _items("P2", 2), _user(), inv="INV-B")["grn_id"]
    monkeypatch.setattr(
        pm, "compute_catalog_status",
        lambda prod: ("DRAFT", ["hsn_code"]) if prod.get("product_id") == "P2" else ("ACTIVE", []),
    )
    assert _accept(r1)["grn_status"] == "PARTIALLY_ACCEPTED"
    monkeypatch.setattr(pm, "compute_catalog_status", lambda prod: ("ACTIVE", []))
    assert _accept(r2)["grn_status"] == "ACCEPTED" and _p2_units(stock) == 2
    out = _accept(r1)
    assert isinstance(out, HTTPException) and out.status_code == 409, out
    return grn_repo, stock, r1, out.detail


def test_a_held_receipt_the_order_has_no_room_for_can_be_voided(monkeypatch):
    """Nothing of R1 is in stock: the refusal says void, the void works, and
    the order's cancels are no longer held up by it."""
    grn_repo, stock, r1, detail = _held_then_full(monkeypatch, with_p1=False)
    assert "Void this receipt" in detail
    with pytest.raises(HTTPException) as e:
        vendors_mod._refuse_if_box_waiting("PO-1")
    assert e.value.status_code == 409
    out = asyncio.run(vendors_mod.void_grn(r1, current_user=_user()))
    assert out["grn_status"] == "VOID" and grn_repo.docs[r1]["status"] == "VOID"
    vendors_mod._refuse_if_box_waiting("PO-1")  # no longer waiting
    assert _p2_units(stock) == 2


def test_a_held_receipt_with_stock_points_to_escalation_not_void(monkeypatch):
    """R1 put its 3 P1 in stock, so it cannot be voided: the refusal says to
    escalate it, and the void refuses."""
    grn_repo, stock, r1, detail = _held_then_full(monkeypatch, with_p1=True)
    assert "escalate" in detail and "Void this receipt" not in detail
    with pytest.raises(HTTPException) as e:
        asyncio.run(vendors_mod.void_grn(r1, current_user=_user()))
    assert e.value.status_code == 409
    assert grn_repo.docs[r1]["status"] == "PARTIALLY_ACCEPTED"
    asyncio.run(vendors_mod.escalate_grn(r1, note="no room left", current_user=_user()))
    vendors_mod._refuse_if_box_waiting("PO-1")  # escalated: no longer waiting


# --------------------------------------------------------------------------- #
# Review round 12 + owner rulings 2026-10-08.
# --------------------------------------------------------------------------- #


def _rehome(stock, product_id, to_store="STORE-B"):
    """A completed stock transfer, as transfers._rehome writes it: the source
    fields are rewritten, po_id and everything else kept."""
    for u in stock.units:
        if u["product_id"] == product_id and u.get("source_type") == "GRN":
            u.update(source_type="TRANSFER", source_id="TRF-1", store_id=to_store,
                     transfer_number="TRF/0001", from_store_id="STORE-A")


def _incomplete(monkeypatch, *pids):
    from api.services import product_master as pm

    monkeypatch.setattr(
        pm, "compute_catalog_status",
        lambda prod: ("DRAFT", ["hsn_code"]) if prod.get("product_id") in pids else ("ACTIVE", []),
    )


def test_transferred_units_still_count_so_no_cancelled_unit_is_received(monkeypatch):
    """Panel item 1. P2 ordered 6, 2 cancelled (4 live). R0 (P2 x2) is
    accepted and its units moved to another shop; R1 (P2 x2 + P3 x1, P3 not
    catalogue-complete) is accepted, P3 held, and its P2 moved too. Both
    receipts' units count wherever they are: the order reads 4 arrived, R2
    (P2 x2) is refused, and R1 accepted again after P3 is catalogued mints
    P3 only -- never its moved P2 a second time."""
    po = _po(_line("P2", "Ray-Ban", 4, ordered_qty=4, cancelled_qty=2), _line("P3", "Vogue", 1))
    grn_repo, po_repo, stock, _t = _wire(monkeypatch, po=po, product_repo=_Products("P2", "P3"))
    _atomic_claims(grn_repo)
    _incomplete(monkeypatch, "P3")
    r0 = _create("normal", _items("P2", 2), _user(), inv="INV-0")["grn_id"]
    assert _accept(r0)["grn_status"] == "ACCEPTED"
    _rehome(stock, "P2")
    r1 = _create("normal", _items("P2", 2) + _items("P3", 1), _user(), inv="INV-1")["grn_id"]
    r2 = _create("normal", _items("P2", 2), _user(), inv="INV-2")["grn_id"]  # 2 + 2 <= 4 then
    assert _accept(r1)["grn_status"] == "PARTIALLY_ACCEPTED"
    _rehome(stock, "P2")
    assert po_repo.po["received_qty_by_product"]["P2"] == 4
    out = _accept(r2)
    assert isinstance(out, HTTPException) and out.status_code == 409, out
    with pytest.raises(HTTPException) as e:  # logging it again is refused too
        _create("normal", _items("P2", 2), _user(), inv="INV-3")
    assert e.value.status_code == 400
    _incomplete(monkeypatch)
    assert _accept(r1)["grn_status"] == "ACCEPTED"
    assert _p2_units(stock) == 4
    assert len([u for u in stock.units if u["product_id"] == "P3"]) == 1


def test_a_held_receipt_whose_units_were_transferred_cannot_be_voided(monkeypatch):
    """Panel item 2. R1 (P1 x2 + P3 x1) put its 2 P1 in stock and held P3;
    the 2 P1 then moved shops. The void still sees them and refuses, so the
    same goods are never logged and received twice."""
    po = _po(_line("P1", "Frame X", 2), _line("P3", "Vogue", 1))
    grn_repo, _po_repo, stock, _t = _wire(monkeypatch, po=po, product_repo=_Products("P1", "P3"))
    _atomic_claims(grn_repo)
    _incomplete(monkeypatch, "P3")
    r1 = _create("normal", _items("P1", 2) + _items("P3", 1), _user(), inv="INV-1")["grn_id"]
    assert _accept(r1)["grn_status"] == "PARTIALLY_ACCEPTED"
    _rehome(stock, "P1")
    with pytest.raises(HTTPException) as e:
        asyncio.run(vendors_mod.void_grn(r1, current_user=_user()))
    assert e.value.status_code == 409
    assert grn_repo.docs[r1]["status"] == "PARTIALLY_ACCEPTED"


def test_the_refusal_advises_escalation_for_a_held_receipt_whose_units_moved(monkeypatch):
    """Panel item 2, second probe: R1's 3 P1 are in stock (then moved) and its
    P2 line is held with no room left. The re-accept refusal must not advise
    the void -- the void refuses it."""
    grn_repo, stock, r1, _detail = _held_then_full(monkeypatch, with_p1=True)
    _rehome(stock, "P1")
    out = _accept(r1)
    assert isinstance(out, HTTPException) and out.status_code == 409, out
    assert "escalate" in out.detail and "Void this receipt" not in out.detail


def test_a_cancelled_item_delivered_and_rejected_can_be_logged(monkeypatch):
    """Owner ruling 2026-10-08 (R2): a delivery that includes cancelled items
    is logged when every cancelled item on it is rejected; nothing cancelled
    enters stock. Accepting one cancelled unit is still refused."""
    grn_repo, _po_repo, stock, _t = _wire(monkeypatch, po=_fully_cancelled_p2())
    one_in = [{"product_id": "P2", "received_qty": 3, "accepted_qty": 1, "rejected_qty": 2}]
    with pytest.raises(HTTPException) as e:
        _create("normal", _items("P1", 5) + one_in, _user(), inv="INV-10")
    assert e.value.status_code == 400 and "rejected" in e.value.detail
    rejected = [{"product_id": "P2", "received_qty": 3, "accepted_qty": 0, "rejected_qty": 3}]
    gid = _create("normal", _items("P1", 5) + rejected, _user())["grn_id"]
    assert _accept(gid)["grn_status"] == "ACCEPTED"
    assert len([u for u in stock.units if u["product_id"] == "P1"]) == 5
    assert _p2_units(stock) == 0


def test_a_part_cancelled_line_takes_what_is_open_and_rejects_the_rest(monkeypatch):
    """P2 ordered 3, 1 in, 1 cancelled: 1 still open. A box of 2 is logged
    with 1 accepted and 1 rejected; 2 accepted is refused."""
    po = _part_cancelled_p2()
    po["items"][1].update(quantity=2, ordered_qty=2, cancelled_qty=1, line_status="PARTIAL")
    grn_repo, _po_repo, stock, _t = _wire(monkeypatch, po=po)
    split = [{"product_id": "P2", "received_qty": 2, "accepted_qty": 1, "rejected_qty": 1}]
    gid = _create("normal", split, _user())["grn_id"]
    assert _accept(gid)["grn_status"] == "ACCEPTED" and _p2_units(stock) == 1
    with pytest.raises(HTTPException) as e:
        _create("normal", _items("P2", 2), _user(), inv="INV-10")
    assert e.value.status_code == 400


def _over_tasks(task_repo):
    return [t for t in task_repo.created if t["title"].startswith("Over-delivery")]


def test_over_delivery_on_an_untouched_line_is_accepted_and_flagged(monkeypatch):
    """Owner ruling 2026-10-08 (R1): P1 ordered 2, nothing cancelled. Two
    receipts of 2 arrive: both are logged and accepted (4 in stock) and the
    second raises one task for the shop's store manager naming the order,
    the product and the 2 extra units. The first, which fits, raises none."""
    po = _po(_line("P1", "Frame X", 2), _line("P3", "Vogue", 1))
    grn_repo, po_repo, stock, tasks = _wire(monkeypatch, po=po)
    a = _create("normal", _items("P1", 2), _user(), inv="INV-A")["grn_id"]
    b = _create("normal", _items("P1", 2), _user(), inv="INV-B")["grn_id"]
    assert _accept(a)["grn_status"] == "ACCEPTED"
    assert _over_tasks(tasks) == []
    assert _accept(b)["grn_status"] == "ACCEPTED"
    assert len([u for u in stock.units if u["product_id"] == "P1"]) == 4
    assert po_repo.po["received_qty_by_product"]["P1"] == 4
    [task] = _over_tasks(tasks)
    assert "PO-2601-1" in task["title"]
    assert "Frame X: 2 more than ordered (4 received, 2 ordered)" in task["description"]
    assert task["assigned_to"] == "store_manager@STORE-A"
    assert task["store_id"] == "STORE-A" and task["source_ref"] == f"grn_over:{b}"


def test_two_accepts_in_flight_on_an_untouched_line_both_mint(monkeypatch):
    """R1: the in-flight guard is only for a product with cancelled units.
    P1 ordered 2, untouched: A is parked in its mint while B is accepted --
    B is not refused, both mint, and the over-delivery is flagged."""
    po = _po(_line("P1", "Frame X", 2), _line("P3", "Vogue", 1))
    grn_repo, _po_repo, stock, tasks = _wire(monkeypatch, po=po)
    _atomic_claims(grn_repo)
    a = _create("normal", _items("P1", 2), _user(), inv="INV-A")["grn_id"]
    b = _create("normal", _items("P1", 2), _user(), inv="INV-B")["grn_id"]
    inside, release = _parked(monkeypatch, stock, "create", lambda doc: True)
    t, out_a = _in_background(a)
    assert inside.wait(10), "A never reached its first stock create"
    try:
        assert _accept(b)["grn_status"] == "ACCEPTED"
    finally:
        release.set()
        t.join(15)
    assert out_a["r"]["grn_status"] == "ACCEPTED", out_a
    assert len([u for u in stock.units if u["product_id"] == "P1"]) == 4
    assert len(_over_tasks(tasks)) == 1


def _p2_live_four(monkeypatch, qa, qb):
    """P2 ordered 5, 1 cancelled: 4 live. Receipts A and B logged."""
    po = _po(_line("P1", "Frame X", 5), _line("P2", "Ray-Ban", 4, ordered_qty=4, cancelled_qty=1))
    grn_repo, _po_repo, stock, _t = _wire(monkeypatch, po=po)
    _atomic_claims(grn_repo)
    a = _create("normal", _items("P2", qa), _user(), inv="INV-A")["grn_id"]
    b = _create("normal", _items("P2", qb), _user(), inv="INV-B")["grn_id"]
    return grn_repo, stock, a, b


def test_a_receipt_that_fits_beside_one_in_flight_is_accepted(monkeypatch):
    """Panel item 8. A (2) and B (2) both fit the 4 live. A is parked after
    stocking its first unit; B is accepted -- A's stocked unit is not counted
    twice. 4 units, never more."""
    grn_repo, stock, a, b = _p2_live_four(monkeypatch, 2, 2)
    calls = {"n": 0}

    def second_create(doc):
        calls["n"] += 1
        return calls["n"] == 2

    inside, release = _parked(monkeypatch, stock, "create", second_create)
    t, out_a = _in_background(a)
    assert inside.wait(10), "A never reached its second unit"
    try:
        assert _accept(b)["grn_status"] == "ACCEPTED"
    finally:
        release.set()
        t.join(15)
    assert out_a["r"]["grn_status"] == "ACCEPTED", out_a
    assert _p2_units(stock) == 4


def test_a_refusal_caused_by_a_delivery_in_flight_says_try_again(monkeypatch):
    """Panel item 8. A (3) is parked in its mint; B (2) would not fit once A
    is in (3 + 2 > 4), but only because of A: B is told another delivery is
    being accepted -- never to void a receipt that may still fit."""
    grn_repo, stock, a, b = _p2_live_four(monkeypatch, 3, 2)
    inside, release = _parked(monkeypatch, stock, "create", lambda doc: True)
    t, out_a = _in_background(a)
    assert inside.wait(10), "A never reached its first stock create"
    try:
        out = _accept(b)
    finally:
        release.set()
        t.join(15)
    assert isinstance(out, HTTPException) and out.status_code == 409, out
    assert out.detail == (
        "Another delivery for this order is being accepted right now - try again in a moment."
    )
    assert grn_repo.docs[b]["status"] == "PENDING" and _p2_units(stock) == 3


@pytest.mark.parametrize("down", [True, False])
def test_an_order_that_cannot_be_read_refuses_the_accept(monkeypatch, down):
    """Panel item 7. P2 fully cancelled; R1 (P2 x3) was logged in the
    cancel's window. The repository's read swallows a driver error into
    None ('no such order'), which skipped the hold. Still down: 503; back on
    the raw re-read: the cancelled order refuses it. Nothing minted."""
    full = _po(_line("P1", "Frame X", 5), _line("P2", "Ray-Ban", 3))
    grn_repo, po_repo, stock, _t = _wire(monkeypatch, po=full)
    gid = _create("normal", _items("P2", 3), _user())["grn_id"]
    po_repo.po.clear()
    po_repo.po.update(copy.deepcopy(_fully_cancelled_p2()))
    stored = po_repo.find_by_id("PO-1")

    class _Coll:
        def find_one(self, flt):
            if down:
                raise RuntimeError("socket timeout")
            return copy.deepcopy(stored)

    po_repo.collection = _Coll()
    po_repo.find_by_id = lambda _pid: None  # what BaseRepository does on an error
    out = _accept(gid)
    assert isinstance(out, HTTPException) and out.status_code == (503 if down else 409), out
    assert stock.units == [] and grn_repo.docs[gid]["status"] == "PENDING"


def test_a_cancel_waits_on_an_unreadable_receipt_list(monkeypatch):
    """Panel items 3 and 6. A receipt is minting; the cancel's look for a
    waiting receipt errors. Through the real repository (whose find_many
    turns an error into []) the cancel must answer 503 and write nothing."""
    from database.repositories.vendor_repository import GRNRepository
    from strict_fakes import StrictCollection

    po = _po(_line("P1", "Frame X", 5), _line("P2", "Ray-Ban", 2))
    fake, po_repo, _stock, _t = _wire(monkeypatch, po=po)
    _create("normal", _items("P2", 2), _user())
    real = GRNRepository(StrictCollection("grns", [copy.deepcopy(d) for d in fake.docs.values()]))

    def find(*_a, **_k):
        raise RuntimeError("socket timeout")

    real.collection.find = find
    monkeypatch.setattr(vendors_mod, "get_grn_repository", lambda: real)
    before = copy.deepcopy(po_repo.po)
    body = vendors_mod.POLineCancel(reason="vendor short", product_id="P2")
    with pytest.raises(HTTPException) as e:
        asyncio.run(vendors_mod.cancel_po_line("PO-1", 1, body, _user()))
    assert e.value.status_code == 503
    with pytest.raises(HTTPException) as e:
        asyncio.run(vendors_mod.cancel_po("PO-1", reason="vendor closed", current_user=_user()))
    assert e.value.status_code == 503
    assert po_repo.po == before
