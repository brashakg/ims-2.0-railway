"""Owner rulings 2026-09-28 -- a purchase order can be corrected and withdrawn.

Procurement audit findings closed here (server side):
  F3  no edit anywhere: PUT on a PO answered 405. A DRAFT is now editable
      (quantity, unit cost, add / remove lines) and ONLY a draft.
  F4  no cancel on Draft/Sent from the screens, blank reasons accepted, and a
      single line could not be cancelled (POST .../items/0/cancel -> 404).
      Cancel now needs a real reason; DRAFT and SENT cancel whole; a part-
      received order cancels only what is still due (stock already received is
      never touched); one line can be cancelled for its undelivered quantity.
      Every change lands on the order timeline and the audit log with the
      person, the time and the reason.
  F61 the catalogue manager may raise a DRAFT (the Buy Desk door) -- sending,
      editing and cancelling stay with the managers.

Handlers are driven directly with in-memory repos (no Mongo, no HTTP), like
test_po_store_boundary / test_po_timeline.
"""

from __future__ import annotations

import asyncio
import copy
import os
import sys
from datetime import datetime

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from api.routers import vendors as v  # noqa: E402
from database.repositories.vendor_repository import PurchaseOrderRepository  # noqa: E402
from strict_fakes import StrictCollection, matches  # noqa: E402


# --------------------------------------------------------------------------- #
# In-memory repos
# --------------------------------------------------------------------------- #


class _PORepo:
    def __init__(self, po):
        self.pos = {po["po_id"]: copy.deepcopy(po)} if po else {}
        self.updates = []

    def find_by_id(self, pid):
        doc = self.pos.get(pid)
        return copy.deepcopy(doc) if doc else None

    def update(self, pid, patch):
        self.updates.append((pid, copy.deepcopy(patch)))
        self.pos[pid].update(copy.deepcopy(patch))
        return True

    def update_if(self, pid, expected, patch):
        doc = self.pos.get(pid)
        if doc is None or not matches(doc, expected):
            return False
        return self.update(pid, patch)

    def find_many(self, flt, skip=0, limit=50):
        out = []
        for doc in self.pos.values():
            st = flt.get("status")
            if isinstance(st, dict) and doc.get("status") not in st.get("$in", []):
                continue
            if isinstance(st, str) and doc.get("status") != st:
                continue
            if "vendor_id" in flt and doc.get("vendor_id") != flt["vendor_id"]:
                continue
            out.append(copy.deepcopy(doc))
        return out


class _GRNRepo:
    def __init__(self, grns=()):
        self.grns = list(grns)

    def find_many(self, flt, limit=200):
        # Honours the status filter: the receipt sum reads ACCEPTED ones only.
        return [copy.deepcopy(g) for g in self.grns if matches(g, flt)]


class _AuditRepo:
    def __init__(self):
        self.rows = []

    def create(self, doc):
        self.rows.append(doc)
        return doc


class _VendorRepo:
    def find_by_id(self, vid):
        return {"vendor_id": vid, "trade_name": "Jharkhand Optical Traders"}


def _user(roles=("STORE_MANAGER",), active="S1", uid="mgr_dhn2"):
    return {
        "user_id": uid,
        "username": uid,
        "roles": list(roles),
        "active_store_id": active,
        "store_ids": [active],
    }


def _line(pid, name, qty, price, rate=5, received=0):
    return {
        "product_id": pid,
        "product_name": name,
        "sku": pid,
        "quantity": qty,
        "ordered_qty": qty,
        "unit_price": price,
        "tax_rate": rate,
        "received_qty": received,
        "line_status": "OPEN" if not received else "PARTIAL",
    }


def _po(status="DRAFT", items=None, store="S1", **extra):
    items = items or [
        _line("P1", "Carrera CA8895", 2, 1000),
        _line("P2", "Ray-Ban RB2140", 3, 2000),
    ]
    doc = {
        "po_id": "PO1",
        "po_number": "PO/BV-DHN-02/26-27/0005",
        "vendor_id": "V1",
        "vendor_name": "Jharkhand Optical Traders",
        "delivery_store_id": store,
        "status": status,
        "items": items,
        "subtotal": 8000.0,
        "tax_amount": 400.0,
        "total_amount": 8400.0,
        "created_by": "cat_mgr",
        "created_at": "2026-09-20T10:00:00",
    }
    doc.update(extra)
    return doc


def _wire(monkeypatch, po, grns=()):
    repo = _PORepo(po)
    audit = _AuditRepo()
    monkeypatch.setattr(v, "get_purchase_order_repository", lambda: repo)
    monkeypatch.setattr(v, "get_grn_repository", lambda: _GRNRepo(grns))
    monkeypatch.setattr(v, "get_audit_repository", lambda: audit)
    monkeypatch.setattr(v, "get_vendor_repository", lambda: _VendorRepo())
    monkeypatch.setattr(v, "get_product_repository", lambda: None)
    monkeypatch.setattr(v, "get_store_repository", lambda: None)
    monkeypatch.setattr(v, "get_stock_repository", lambda: None)
    return repo, audit


def _run(coro):
    return asyncio.run(coro)


def _edit_body(items, **kw):
    return v.POUpdate(items=[v.POItemCreate(**it) for it in items], **kw)


# =========================================================================== #
# F3 -- edit a DRAFT
# =========================================================================== #


def test_edit_route_exists_for_put():
    """The audit's PUT answered 405: there was no edit route at all."""
    methods = set()
    for r in v.router.routes:
        if getattr(r, "path", "") == "/purchase-orders/{po_id}":
            methods |= set(getattr(r, "methods", set()) or set())
    assert "PUT" in methods


def test_edit_draft_changes_qty_cost_and_lines(monkeypatch):
    repo, audit = _wire(monkeypatch, _po())
    body = _edit_body(
        [
            # P1: qty 2 -> 3, cost 1000 -> 950
            {"product_id": "P1", "product_name": "Carrera CA8895", "sku": "P1",
             "quantity": 3, "unit_price": 950, "gst_rate": 5},
            # P2 removed; P3 added
            {"product_id": "P3", "product_name": "Oakley OX8046", "sku": "P3",
             "quantity": 1, "unit_price": 4000, "gst_rate": 5},
        ]
    )
    out = _run(v.update_po("PO1", body, _user()))
    doc = repo.pos["PO1"]
    assert [i["product_id"] for i in doc["items"]] == ["P1", "P3"]
    assert doc["items"][0]["quantity"] == 3
    assert doc["items"][0]["ordered_qty"] == 3
    assert doc["items"][0]["unit_price"] == 950
    # 3*950 + 1*4000 = 6850 ; 5% = 342.5
    assert doc["subtotal"] == 6850
    assert doc["tax_amount"] == 342.5
    assert doc["total_amount"] == 7192.5
    assert doc["status"] == "DRAFT"
    ev = doc["history"][-1]
    assert ev["kind"] == "edited"
    assert ev["actor"] == "mgr_dhn2"
    assert ev["at"]
    assert "Carrera CA8895" in ev["detail"] and "2 -> 3" in ev["detail"]
    assert "Ray-Ban RB2140" in ev["detail"]  # removed line is named
    assert "Oakley OX8046" in ev["detail"]  # added line is named
    assert audit.rows and audit.rows[-1]["action"] == "purchase_order.edit"
    assert audit.rows[-1]["user_id"] == "mgr_dhn2"
    assert out["po_id"] == "PO1" and out["subtotal"] == 6850


@pytest.mark.parametrize(
    # ACKNOWLEDGED: the server counts it as gone to the vendor (on order).
    "status", ["SENT", "ACKNOWLEDGED", "PARTIALLY_RECEIVED", "RECEIVED", "CANCELLED"]
)
def test_edit_refused_once_not_a_draft(monkeypatch, status):
    repo, audit = _wire(monkeypatch, _po(status=status))
    body = _edit_body(
        [{"product_id": "P1", "product_name": "x", "quantity": 9, "unit_price": 1}]
    )
    with pytest.raises(HTTPException) as e:
        _run(v.update_po("PO1", body, _user()))
    assert e.value.status_code == 400
    assert repo.updates == [] and audit.rows == []


def test_edit_cross_store_404_no_mutation(monkeypatch):
    repo, _ = _wire(monkeypatch, _po(store="S2"))
    body = _edit_body(
        [{"product_id": "P1", "product_name": "x", "quantity": 9, "unit_price": 1}]
    )
    with pytest.raises(HTTPException) as e:
        _run(v.update_po("PO1", body, _user(active="S1")))
    assert e.value.status_code == 404
    assert repo.updates == []


# =========================================================================== #
# F4 -- cancel with a reason; part-received cancels only what is due
# =========================================================================== #


@pytest.mark.parametrize("status", ["DRAFT", "SENT"])
def test_cancel_draft_or_sent_records_reason_person_and_time(monkeypatch, status):
    repo, audit = _wire(monkeypatch, _po(status=status))
    _run(v.cancel_po("PO1", "qty typo", _user()))
    doc = repo.pos["PO1"]
    assert doc["status"] == "CANCELLED"
    assert doc["cancellation_reason"] == "qty typo"
    assert doc["cancelled_by"] == "mgr_dhn2"
    ev = doc["history"][-1]
    assert ev["kind"] == "cancelled" and ev["actor"] == "mgr_dhn2" and ev["at"]
    assert "qty typo" in ev["detail"]
    assert audit.rows[-1]["action"] == "purchase_order.cancel"
    assert audit.rows[-1]["after"]["reason"] == "qty typo"


@pytest.mark.parametrize("status", ["DRAFT", "SENT"])
@pytest.mark.parametrize("reason", ["", "   ", "x"])
def test_cancel_needs_a_real_reason(monkeypatch, status, reason):
    """Owner ruling: a Draft OR a Sent order is cancelled WITH A REASON."""
    repo, audit = _wire(monkeypatch, _po(status=status))
    with pytest.raises(HTTPException) as e:
        _run(v.cancel_po("PO1", reason, _user()))
    assert e.value.status_code == 400
    assert repo.updates == [] and audit.rows == []


def test_cancel_part_received_cancels_only_what_is_still_due(monkeypatch):
    po = _po(
        status="PARTIALLY_RECEIVED",
        items=[
            _line("P1", "Carrera CA8895", 2, 1000, received=2),
            _line("P2", "Ray-Ban RB2140", 3, 2000, received=1),
        ],
        received_qty_by_product={"P1": 2, "P2": 1},
    )
    repo, audit = _wire(monkeypatch, po)
    _run(v.cancel_po("PO1", "vendor out of stock", _user()))
    doc = repo.pos["PO1"]
    p1, p2 = doc["items"]
    # Nothing received is ever un-received: the received counts are untouched.
    assert p1["received_qty"] == 2 and p2["received_qty"] == 1
    assert doc["received_qty_by_product"] == {"P1": 2, "P2": 1}
    # The 2 undelivered Ray-Bans are cancelled; the order now asks for what came.
    assert p2["quantity"] == 1 and p2["ordered_qty"] == 1 and p2["cancelled_qty"] == 2
    assert p1["quantity"] == 2 and p1.get("cancelled_qty", 0) == 0
    # Everything still asked for has arrived -> the order is complete.
    assert doc["status"] == "RECEIVED"
    # Value drops by the cancelled 2 x 2000.
    assert doc["subtotal"] == 4000
    ev = doc["history"][-1]
    assert ev["kind"] == "cancelled" and "vendor out of stock" in ev["detail"]
    assert "2" in ev["detail"]
    assert audit.rows[-1]["action"] == "purchase_order.cancel"
    # The order is no longer due anywhere: the receipt rule says complete.
    assert v.compute_po_receipt_state(doc["items"], doc["received_qty_by_product"]) == "RECEIVED"


def test_cancel_refused_while_a_box_waits_to_be_accepted(monkeypatch):
    """A logged-but-unaccepted receipt would flip a cancelled order back to
    received the moment it is accepted -- so cancel waits for it."""
    repo, audit = _wire(
        monkeypatch,
        _po(status="SENT"),
        grns=[{"po_id": "PO1", "grn_number": "RCPT/0007", "status": "PENDING"}],
    )
    with pytest.raises(HTTPException) as e:
        _run(v.cancel_po("PO1", "changed mind", _user()))
    assert e.value.status_code == 409
    assert "RCPT/0007" in str(e.value.detail)
    assert "void" in str(e.value.detail)  # a PENDING receipt CAN be voided
    assert repo.updates == []


def test_cancel_refused_when_fully_received(monkeypatch):
    repo, _ = _wire(monkeypatch, _po(status="RECEIVED"))
    with pytest.raises(HTTPException) as e:
        _run(v.cancel_po("PO1", "too late", _user()))
    assert e.value.status_code == 400
    assert repo.updates == []


# =========================================================================== #
# F4 -- cancel one line
# =========================================================================== #


def _line_body(reason="vendor discontinued", product_id=None):
    return v.POLineCancel(reason=reason, product_id=product_id)


def test_line_cancel_route_exists():
    paths = {getattr(r, "path", "") for r in v.router.routes}
    assert "/purchase-orders/{po_id}/items/{line_index}/cancel" in paths


def test_line_cancel_on_sent_order(monkeypatch):
    repo, audit = _wire(monkeypatch, _po(status="SENT"))
    _run(v.cancel_po_line("PO1", 1, _line_body(product_id="P2"), _user()))
    doc = repo.pos["PO1"]
    p1, p2 = doc["items"]
    assert p2["quantity"] == 0 and p2["ordered_qty"] == 0
    assert p2["cancelled_qty"] == 3 and p2["line_status"] == "CANCELLED"
    assert p1["quantity"] == 2
    assert doc["status"] == "SENT"
    assert doc["subtotal"] == 2000  # only the Carreras remain
    ev = doc["history"][-1]
    assert ev["kind"] == "line_cancelled" and ev["actor"] == "mgr_dhn2"
    assert "Ray-Ban RB2140" in ev["detail"] and "vendor discontinued" in ev["detail"]
    # The audit row says who and why, like the whole-order cancel's.
    row = audit.rows[-1]
    assert row["action"] == "purchase_order.cancel_line"
    assert row["user_id"] == "mgr_dhn2"
    assert row["after"]["reason"] == "vendor discontinued"


def test_cancelled_line_is_no_longer_due_at_receiving(monkeypatch):
    repo, _ = _wire(monkeypatch, _po(status="SENT"))
    _run(v.cancel_po_line("PO1", 1, _line_body(), _user()))
    cockpit = _run(v.goods_receipt_cockpit("V1", None, _user()))
    lines = [ln for p in cockpit["open_pos"] for ln in p["lines"]]
    assert [ln["product_id"] for ln in lines] == ["P1"]
    assert all(r["product_id"] != "P2" for r in cockpit["pending_not_received"])


def test_cancelling_the_last_open_line_cancels_the_order(monkeypatch):
    repo, _ = _wire(
        monkeypatch, _po(status="SENT", items=[_line("P1", "Carrera CA8895", 2, 1000)])
    )
    _run(v.cancel_po_line("PO1", 0, _line_body(), _user()))
    doc = repo.pos["PO1"]
    assert doc["status"] == "CANCELLED"
    assert doc["cancelled_by"] == "mgr_dhn2"


def test_line_cancel_on_part_received_line_cancels_only_the_rest(monkeypatch):
    po = _po(
        status="PARTIALLY_RECEIVED",
        items=[
            _line("P1", "Carrera CA8895", 2, 1000, received=0),
            _line("P2", "Ray-Ban RB2140", 3, 2000, received=1),
        ],
        received_qty_by_product={"P2": 1},
    )
    repo, _ = _wire(monkeypatch, po)
    _run(v.cancel_po_line("PO1", 1, _line_body(), _user()))
    doc = repo.pos["PO1"]
    p2 = doc["items"][1]
    assert p2["received_qty"] == 1 and p2["quantity"] == 1 and p2["cancelled_qty"] == 2
    # The Carreras are still due -> still part received.
    assert doc["status"] == "PARTIALLY_RECEIVED"


def test_line_cancel_on_draft_removes_the_line(monkeypatch):
    repo, _ = _wire(monkeypatch, _po(status="DRAFT"))
    _run(v.cancel_po_line("PO1", 0, _line_body(), _user()))
    doc = repo.pos["PO1"]
    assert [i["product_id"] for i in doc["items"]] == ["P2"]
    assert doc["subtotal"] == 6000
    assert doc["history"][-1]["kind"] == "line_cancelled"


def test_line_cancel_refuses_the_only_line_of_a_draft(monkeypatch):
    repo, _ = _wire(
        monkeypatch, _po(status="DRAFT", items=[_line("P1", "Carrera CA8895", 2, 1000)])
    )
    with pytest.raises(HTTPException) as e:
        _run(v.cancel_po_line("PO1", 0, _line_body(), _user()))
    assert e.value.status_code == 400
    assert repo.updates == []


def test_line_cancel_refuses_a_line_with_nothing_due(monkeypatch):
    po = _po(
        status="PARTIALLY_RECEIVED",
        items=[
            _line("P1", "Carrera CA8895", 2, 1000, received=2),
            _line("P2", "Ray-Ban RB2140", 3, 2000, received=0),
        ],
        received_qty_by_product={"P1": 2},
    )
    repo, _ = _wire(monkeypatch, po)
    with pytest.raises(HTTPException) as e:
        _run(v.cancel_po_line("PO1", 0, _line_body(), _user()))
    assert e.value.status_code == 400
    assert repo.updates == []


def test_line_cancel_stale_screen_is_refused(monkeypatch):
    """The line index must still point at the product the person saw."""
    repo, _ = _wire(monkeypatch, _po(status="SENT"))
    with pytest.raises(HTTPException) as e:
        _run(v.cancel_po_line("PO1", 0, _line_body(product_id="P2"), _user()))
    assert e.value.status_code == 409
    assert repo.updates == []


def test_line_cancel_bad_index_404(monkeypatch):
    repo, _ = _wire(monkeypatch, _po(status="SENT"))
    with pytest.raises(HTTPException) as e:
        _run(v.cancel_po_line("PO1", 7, _line_body(), _user()))
    assert e.value.status_code == 404


def test_line_cancel_needs_a_reason():
    with pytest.raises(ValidationError):
        v.POLineCancel(reason="  ")


def test_line_cancel_cross_store_404(monkeypatch):
    repo, _ = _wire(monkeypatch, _po(status="SENT", store="S2"))
    with pytest.raises(HTTPException) as e:
        _run(v.cancel_po_line("PO1", 0, _line_body(), _user(active="S1")))
    assert e.value.status_code == 404
    assert repo.updates == []


# =========================================================================== #
# A later receipt must not revive a cancelled line
# =========================================================================== #


def test_receipt_state_keeps_a_cancelled_line_cancelled():
    assert v.po_line_status({"quantity": 0, "line_status": "CANCELLED"}, 0) == "CANCELLED"
    assert v.po_line_status({"quantity": 2, "line_status": "OPEN"}, 0) == "OPEN"
    assert v.po_line_status({"quantity": 2, "line_status": "OPEN"}, 1) == "PARTIAL"
    assert v.po_line_status({"quantity": 2, "line_status": "PARTIAL"}, 2) == "RECEIVED"


# =========================================================================== #
# The timeline shows every change with who and why
# =========================================================================== #


class _NoBillsDB:
    class _C:
        def find(self, *_a, **_k):
            return []

    def get_collection(self, _name):
        return self._C()


def test_timeline_shows_edit_and_cancel_with_person_and_reason(monkeypatch):
    repo, _ = _wire(monkeypatch, _po(status="SENT"))
    monkeypatch.setattr(v, "_get_db", lambda: _NoBillsDB())
    _run(v.cancel_po_line("PO1", 1, _line_body(reason="qty typo"), _user()))
    _run(v.cancel_po("PO1", "vendor closed", _user()))
    tl = _run(v.get_po_timeline("PO1", _user()))
    kinds = [e["kind"] for e in tl["events"]]
    assert kinds.count("cancelled") == 1  # not doubled by the legacy stamp
    assert "line_cancelled" in kinds
    lc = next(e for e in tl["events"] if e["kind"] == "line_cancelled")
    assert "qty typo" in lc["detail"] and lc["by"] == "mgr_dhn2"
    c = next(e for e in tl["events"] if e["kind"] == "cancelled")
    assert "vendor closed" in c["detail"] and c["by"] == "mgr_dhn2"


def test_timeline_still_shows_a_legacy_cancel(monkeypatch):
    po = _po(
        status="CANCELLED",
        cancelled_at="2026-09-21T10:00:00",
        cancelled_by="u9",
        cancellation_reason="Rejected by approver",
    )
    _wire(monkeypatch, po)
    monkeypatch.setattr(v, "_get_db", lambda: _NoBillsDB())
    tl = _run(v.get_po_timeline("PO1", _user()))
    c = [e for e in tl["events"] if e["kind"] == "cancelled"]
    assert len(c) == 1 and c[0]["detail"] == "Rejected by approver"


# =========================================================================== #
# Verifier round 2 -- the guards hold for real, not only in the happy path
# =========================================================================== #


def test_line_cancel_refused_while_a_box_waits_to_be_accepted(monkeypatch):
    """SENT order, P2 x3, a PENDING receipt holding 3 x P2. Had the line been
    cancelled first, accepting that receipt would put 3 units of a cancelled
    line into stock and mark the order received."""
    repo, audit = _wire(
        monkeypatch,
        _po(status="SENT"),
        grns=[{"po_id": "PO1", "grn_number": "RCPT/0009", "status": "PENDING",
               "items": [{"product_id": "P2", "received_qty": 3}]}],
    )
    with pytest.raises(HTTPException) as e:
        _run(v.cancel_po_line("PO1", 1, _line_body(product_id="P2"), _user()))
    assert e.value.status_code == 409
    assert "RCPT/0009" in str(e.value.detail)
    assert repo.updates == [] and audit.rows == []


def test_a_held_receipt_points_to_cataloguing_not_to_void(monkeypatch):
    """PARTIALLY_ACCEPTED = some lines held until their product is catalogued.
    Void refuses anything not PENDING, so the refusal must not send the person
    to a void button that fails; it says what actually clears it."""
    po = _po(
        status="PARTIALLY_RECEIVED",
        items=[
            _line("P1", "Carrera CA8895", 2, 1000, received=2),
            _line("P2", "Ray-Ban RB2140", 3, 2000),
        ],
        received_qty_by_product={"P1": 2},
    )
    repo, _ = _wire(
        monkeypatch,
        po,
        grns=[{"po_id": "PO1", "grn_number": "RCPT/0010", "status": "PARTIALLY_ACCEPTED"}],
    )
    for call in (
        lambda: v.cancel_po("PO1", "vendor out of stock", _user()),
        lambda: v.cancel_po_line("PO1", 1, _line_body(), _user()),
    ):
        with pytest.raises(HTTPException) as e:
            _run(call())
        detail = str(e.value.detail).lower()
        assert e.value.status_code == 409 and "rcpt/0010" in detail
        assert "catalogue" in detail and "void" not in detail
    assert repo.updates == []


def _stale_part_received():
    """PARTIALLY_RECEIVED, but the order's own copy of the received counts was
    never written (grn_accept's fallback writes only the status): no header,
    every line says 0. The receipts say 1 x P2 is on the shelf."""
    po = _po(status="PARTIALLY_RECEIVED")
    grns = [{"po_id": "PO1", "grn_number": "RCPT/0011", "status": "ACCEPTED",
             "items": [{"product_id": "P2", "accepted_qty": 1}]},
            # A receipt that never reached the shelf counts for nothing.
            {"po_id": "PO1", "grn_number": "RCPT/0012", "status": "VOID",
             "items": [{"product_id": "P1", "accepted_qty": 2}]}]
    return po, grns


def test_cancel_counts_what_the_receipts_accepted_not_a_stale_copy(monkeypatch):
    po, grns = _stale_part_received()
    repo, _ = _wire(monkeypatch, po, grns=grns)
    _run(v.cancel_po("PO1", "vendor out of stock", _user()))
    doc = repo.pos["PO1"]
    p1, p2 = doc["items"]
    # The Ray-Ban on the shelf stays ordered and received; 2 are withdrawn.
    assert p2["quantity"] == 1 and p2["cancelled_qty"] == 2
    assert p2["received_qty"] == 1 and p2["line_status"] == "RECEIVED"
    assert p1["quantity"] == 0 and p1["line_status"] == "CANCELLED"
    assert doc["received_qty_by_product"] == {"P1": 0, "P2": 1}
    # Stock arrived, so it is a received order, never a cancelled one.
    assert doc["status"] == "RECEIVED"
    assert v.compute_po_receipt_state(doc["items"], doc["received_qty_by_product"]) == "RECEIVED"


def test_line_cancel_counts_what_the_receipts_accepted(monkeypatch):
    po, grns = _stale_part_received()
    repo, _ = _wire(monkeypatch, po, grns=grns)
    _run(v.cancel_po_line("PO1", 1, _line_body(), _user()))
    p2 = repo.pos["PO1"]["items"][1]
    assert p2["quantity"] == 1 and p2["cancelled_qty"] == 2 and p2["received_qty"] == 1
    assert repo.pos["PO1"]["status"] == "PARTIALLY_RECEIVED"  # Carreras still due


class _RacingRepo(PurchaseOrderRepository):
    """The REAL repository over a strict fake collection. ``race`` runs once,
    right after the handler's ``at``-th read: someone else's write landing
    between the check and the write."""

    def __init__(self, po, race, at=1):
        super().__init__(StrictCollection("purchase_orders", [copy.deepcopy(po)]))
        self.race, self.reads_left = race, at

    def find_by_id(self, pid):
        doc = super().find_by_id(pid)
        self.reads_left -= 1
        if self.reads_left == 0 and self.race:
            race, self.race = self.race, None
            race(self)
        return doc


def _wire_racing(monkeypatch, po, race, at=1):
    _wire(monkeypatch, None)
    repo = _RacingRepo(po, race, at)
    monkeypatch.setattr(v, "get_purchase_order_repository", lambda: repo)
    return repo


def test_edit_refused_when_the_order_was_sent_meanwhile(monkeypatch):
    def send(repo):  # what send_po writes
        repo.update("PO1", {"status": "SENT", "sent_by": "mgr_other"})

    repo = _wire_racing(monkeypatch, _po(), send)
    body = _edit_body(
        [{"product_id": "P1", "product_name": "Carrera CA8895", "sku": "P1",
          "quantity": 9, "unit_price": 1000, "gst_rate": 5}]
    )
    with pytest.raises(HTTPException) as e:
        _run(v.update_po("PO1", body, _user()))
    assert e.value.status_code == 409
    doc = repo.collection.docs[0]
    # The order the vendor already has is exactly what was sent.
    assert doc["status"] == "SENT"
    assert [(i["product_id"], i["quantity"]) for i in doc["items"]] == [("P1", 2), ("P2", 3)]
    assert not doc.get("history")


class _ProductRepo:
    def __init__(self, prods):
        self.prods = copy.deepcopy(prods)

    def find_by_id(self, pid):
        doc = self.prods.get(pid)
        return copy.deepcopy(doc) if doc else None

    def update(self, pid, fields):
        self.prods[pid].update(copy.deepcopy(fields))
        return True


def _rate_777_body():
    return _edit_body(
        [{"product_id": "P1", "product_name": "Carrera CA8895", "sku": "P1",
          "quantity": 2, "unit_price": 777, "gst_rate": 5}]
    )


def _cost_rows(audit):
    return [r for r in audit.rows if r["action"] == "purchase.cost_from_po_rate"]


def test_a_refused_edit_changes_no_product_cost(monkeypatch):
    """Verifier probe: a PUT set rate 777 on an uncosted product while a
    colleague sent the draft. The edit got 409 and the order went out
    unchanged -- yet P1 came out costed 777 (PO_RATE) with no audit row: a cost
    that feeds margin and valuation, changed by an edit that never happened."""
    def send(repo):  # what send_po writes
        repo.update("PO1", {"status": "SENT", "sent_by": "mgr_other"})

    repo = _wire_racing(monkeypatch, _po(), send)
    products = _ProductRepo({"P1": {"product_id": "P1"}})
    monkeypatch.setattr(v, "get_product_repository", lambda: products)
    audit = v.get_audit_repository()

    with pytest.raises(HTTPException) as e:
        _run(v.update_po("PO1", _rate_777_body(), _user()))
    assert e.value.status_code == 409
    assert repo.race is None, "the send never landed in the window"
    assert products.prods["P1"] == {"product_id": "P1"}
    assert audit.rows == []


def test_an_edit_that_fills_a_cost_audits_it(monkeypatch):
    """The saved edit does fill the missing cost -- and says so, once."""
    repo, audit = _wire(monkeypatch, _po())
    products = _ProductRepo({"P1": {"product_id": "P1"}})
    monkeypatch.setattr(v, "get_product_repository", lambda: products)

    _run(v.update_po("PO1", _rate_777_body(), _user()))

    assert products.prods["P1"]["cost_price"] == 777
    assert products.prods["P1"]["cost_source"] == "PO_RATE"
    rows = _cost_rows(audit)
    assert len(rows) == 1
    assert rows[0]["user_id"] == "mgr_dhn2"
    assert rows[0]["detail"]["products"] == [{"product_id": "P1", "cost_price": 777.0}]


def test_a_refused_order_creates_no_typed_in_product(monkeypatch):
    """The catalogue gate refuses an unknown product id BEFORE a typed-in line
    is turned into a product: a refused edit leaves no provisional product."""
    _wire(monkeypatch, _po())
    monkeypatch.setattr(v, "get_product_repository", lambda: _ProductRepo({}))
    monkeypatch.setattr(v, "_po_catalog_gate_on", lambda: True)
    made = []
    monkeypatch.setattr(
        v._pm, "create_via_door", lambda payload, **kw: made.append(payload) or {}
    )
    body = _edit_body(
        [
            {"new_product": {"brand": "Vogue", "model": "VO5286", "mrp": 5000},
             "quantity": 1, "unit_price": 2000},
            {"product_id": "GHOST", "product_name": "x", "quantity": 1, "unit_price": 1},
        ]
    )
    with pytest.raises(HTTPException) as e:
        _run(v.update_po("PO1", body, _user()))
    assert e.value.status_code == 422
    assert made == []


def test_a_door_refusal_on_a_later_typed_in_line_creates_no_product(monkeypatch):
    """Panel MEDIUM: the typed-in lines went through the product door one at a
    time, so the door refusing line 2 arrived after line 1's product was
    already written -- a 422 edit, an unchanged order, and an orphan
    provisional Vogue on the spine with its product.created audit row. Every
    typed-in line is now validated before the first is written."""
    from test_purchase_lifecycle import _ProductRepo as _SpineRepo

    repo, audit = _wire(monkeypatch, _po())
    spine = _SpineRepo()
    monkeypatch.setattr(v, "get_product_repository", lambda: spine)
    monkeypatch.setattr(v, "_get_db", lambda: None)
    vogue = {"category": "FRAME", "brand": "Vogue", "model": "VO5286",
             "colour": "W44", "size": "52", "mrp": 5000}
    body = _edit_body(
        [
            {"new_product": vogue, "quantity": 1, "unit_price": 2000},
            {"new_product": {**vogue, "category": "NOT_A_CATEGORY",
                             "brand": "Oakley", "model": "OX8046"},
             "quantity": 1, "unit_price": 2500},
        ]
    )
    with pytest.raises(HTTPException) as e:
        _run(v.update_po("PO1", body, _user()))
    assert e.value.status_code == 422
    assert e.value.detail["code"] == "NEW_PRODUCT_INVALID"
    assert spine.rows == [], "a refused edit leaves no provisional product"
    assert audit.rows == []
    assert [(i["product_id"], i["quantity"]) for i in repo.pos["PO1"]["items"]] == [
        ("P1", 2), ("P2", 3)
    ]

    # The same lines with a real category: both products made, order saved.
    body = _edit_body(
        [
            {"new_product": vogue, "quantity": 1, "unit_price": 2000},
            {"new_product": {**vogue, "brand": "Oakley", "model": "OX8046"},
             "quantity": 1, "unit_price": 2500},
        ]
    )
    _run(v.update_po("PO1", body, _user()))
    assert len(spine.rows) == 2
    assert [i["product_id"] for i in repo.pos["PO1"]["items"]] == [
        r["product_id"] for r in spine.rows
    ]


_VOGUE = {"category": "FRAME", "brand": "Vogue", "model": "VO5286",
          "colour": "W44", "size": "52", "mrp": 5000}


def _real_spine(monkeypatch):
    """The REAL ProductRepository over a strict fake collection: the product
    door's own duplicate checks and writes run, nothing is stubbed."""
    from database.repositories.product_repository import ProductRepository

    spine = ProductRepository(StrictCollection("products", []))
    monkeypatch.setattr(v, "get_product_repository", lambda: spine)
    monkeypatch.setattr(v, "_get_db", lambda: None)
    return spine


def _typed_in_vogue_body():
    return _edit_body([{"new_product": dict(_VOGUE), "quantity": 1, "unit_price": 2000}])


@pytest.mark.parametrize("status", ["SENT", "ACKNOWLEDGED", "RECEIVED", "CANCELLED"])
def test_an_edit_refused_for_status_creates_no_typed_in_product(monkeypatch, status):
    """Verifier round 6: the DRAFT check must stop an edit before a typed-in
    line reaches the product door. Moving it after the pricing left a
    provisional Vogue (and its product.created row) behind a 400."""
    repo, audit = _wire(monkeypatch, _po(status=status))
    spine = _real_spine(monkeypatch)
    with pytest.raises(HTTPException) as e:
        _run(v.update_po("PO1", _typed_in_vogue_body(), _user()))
    assert e.value.status_code == 400
    assert spine.collection.docs == [], "a refused edit left a provisional product"
    assert audit.rows == [] and repo.updates == []


def test_an_edit_that_lost_the_race_creates_no_typed_in_product(monkeypatch):
    """Round 6 open item 2: the typed-in product used to be written before the
    compare-and-set. A colleague who sent the draft while the edit ran left a
    409, an unchanged order and an orphan provisional Vogue with its audit row.
    The product is now written only after the order write succeeds."""
    def send(repo):  # what send_po writes
        repo.update("PO1", {"status": "SENT", "sent_by": "mgr_other"})

    repo = _wire_racing(monkeypatch, _po(), send)
    spine = _real_spine(monkeypatch)
    audit = v.get_audit_repository()
    body = _edit_body([
        {"new_product": dict(_VOGUE), "quantity": 1, "unit_price": 2000},
        {"new_product": {**_VOGUE, "brand": "Oakley", "model": "OX8046"},
         "quantity": 1, "unit_price": 2500},
    ])
    with pytest.raises(HTTPException) as e:
        _run(v.update_po("PO1", body, _user()))
    assert e.value.status_code == 409
    assert repo.race is None, "the send never landed in the window"
    assert spine.collection.docs == [], "a lost edit left a provisional product"
    assert audit.rows == []
    doc = repo.collection.docs[0]
    assert [(i["product_id"], i["quantity"]) for i in doc["items"]] == [("P1", 2), ("P2", 3)]


def test_a_saved_edit_writes_the_typed_in_product_under_the_lines_id(monkeypatch):
    repo, audit = _wire(monkeypatch, _po())
    spine = _real_spine(monkeypatch)
    body = _edit_body([
        {"new_product": dict(_VOGUE), "quantity": 1, "unit_price": 2000},
        # The same frame typed twice in one edit: one product, not two.
        {"new_product": dict(_VOGUE), "quantity": 2, "unit_price": 2000},
    ])
    _run(v.update_po("PO1", body, _user()))
    assert len(spine.collection.docs) == 1
    prod = spine.collection.docs[0]
    assert prod["provisional"] is True and prod["is_active"] is False
    lines = repo.pos["PO1"]["items"]
    assert [i["product_id"] for i in lines] == [prod["product_id"]] * 2
    assert [i["sku"] for i in lines] == [prod["sku"]] * 2
    assert len(repo.updates) == 1, "one write: the edit, nothing to repair after"
    actions = [r["action"] for r in audit.rows]
    assert actions.index("purchase_order.edit") < actions.index("product.created")


def test_a_product_made_meanwhile_is_reused_not_left_dangling(monkeypatch):
    """Someone creates the identical frame between the check and the write:
    the door answers 409 and the stored line is pointed at that product, so
    the order never names a product the spine does not hold."""
    from database.repositories.product_repository import ProductRepository

    repo, _ = _wire(monkeypatch, _po())
    spine = _real_spine(monkeypatch)
    real_find = ProductRepository.find_by_identity_key
    calls = {"n": 0}

    def find(self, key):
        calls["n"] += 1
        if calls["n"] == 2:  # the door's own pre-check, after the order write
            self.collection.insert_one(
                {"_id": "RIVAL", "product_id": "RIVAL", "sku": "RIVAL-SKU",
                 "identity_key": key}
            )
        return real_find(self, key)

    monkeypatch.setattr(ProductRepository, "find_by_identity_key", find)
    _run(v.update_po("PO1", _typed_in_vogue_body(), _user()))
    assert [d["product_id"] for d in spine.collection.docs] == ["RIVAL"]
    line = repo.pos["PO1"]["items"][0]
    assert (line["product_id"], line["sku"]) == ("RIVAL", "RIVAL-SKU")


def test_two_line_cancels_at_once_never_lose_one(monkeypatch):
    def colleague_cancels_p1(repo):  # a finished line cancel, by someone else
        items = copy.deepcopy(repo.collection.docs[0]["items"])
        items[0].update(quantity=0, ordered_qty=0, cancelled_qty=2, line_status="CANCELLED")
        repo.update(
            "PO1",
            {"items": items,
             "history": [{"kind": "line_cancelled", "actor": "mgr_other",
                          "detail": "Carrera CA8895: 2 units cancelled"}]},
        )

    repo = _wire_racing(monkeypatch, _po(status="SENT"), colleague_cancels_p1)
    with pytest.raises(HTTPException) as e:
        _run(v.cancel_po_line("PO1", 1, _line_body(product_id="P2"), _user()))
    assert e.value.status_code == 409
    doc = repo.collection.docs[0]
    assert doc["items"][0]["line_status"] == "CANCELLED"
    assert doc["items"][1]["quantity"] == 3  # untouched by the refused write
    assert [h["actor"] for h in doc["history"]] == ["mgr_other"]

    # Reloaded and tried again: both changes and both timeline rows survive.
    _run(v.cancel_po_line("PO1", 1, _line_body(product_id="P2"), _user()))
    doc = repo.collection.docs[0]
    assert [i["line_status"] for i in doc["items"]] == ["CANCELLED", "CANCELLED"]
    assert doc["status"] == "CANCELLED"
    assert [h["actor"] for h in doc["history"]] == ["mgr_other", "mgr_dhn2", "mgr_dhn2"]


def test_change_times_are_saved_with_their_zone(monkeypatch):
    """Owner ruling (Wave 6 A7): save every time WITH its zone. A naive stamp on
    the UTC server showed a 14:03 IST cancel as 8:33 am in the drawer."""
    repo, _ = _wire(monkeypatch, _po(status="SENT"))
    _run(v.cancel_po_line("PO1", 1, _line_body(), _user()))
    _run(v.cancel_po("PO1", "vendor closed", _user()))
    doc = repo.pos["PO1"]
    for stamp in [h["at"] for h in doc["history"]] + [doc["cancelled_at"]]:
        assert datetime.fromisoformat(stamp).utcoffset() is not None, stamp


def test_send_refused_when_the_draft_was_edited_meanwhile(monkeypatch):
    """The send checks the lines, then writes: an edit landing in between must
    not go to the vendor unchecked."""
    def edit(repo):  # a finished draft edit, by someone else
        items = copy.deepcopy(repo.collection.docs[0]["items"])
        items[0]["quantity"] = 9
        repo.update("PO1", {"items": items})

    repo = _wire_racing(monkeypatch, _po(), edit)
    with pytest.raises(HTTPException) as e:
        _run(v.send_po("PO1", _user()))
    assert e.value.status_code == 409
    assert repo.collection.docs[0]["status"] == "DRAFT"
    assert "sent_at" not in repo.collection.docs[0]


class _AcceptedGrnRepo:
    """One receipt, for accept_grn (its claim needs ``.collection``) and for
    every receipt read (the filter is honoured, like Mongo)."""

    def __init__(self, doc):
        from test_grn_accept_atomic_claim import _FakeGrnColl

        self.collection = _FakeGrnColl(doc)

    def find_by_id(self, gid):
        doc = self.collection.doc
        return dict(doc) if gid == doc["grn_id"] else None

    def update(self, gid, patch):
        self.collection.doc.update(patch)
        return True

    def find_many(self, flt=None, *a, **k):
        doc = self.collection.doc
        return [dict(doc)] if matches(doc, flt or {}) else []


def test_a_receipt_accepted_during_a_cancel_never_brings_the_cancelled_units_back(
    monkeypatch,
):
    """Verifier probe (MEDIUM): the accept reads the order, the manager cancels
    what is still due, then the accept wrote back the lines IT had read -- the
    3 cancelled Ray-Bans were due again (PARTIALLY_RECEIVED, P2 x3 OPEN) under a
    timeline that says 'Rest cancelled'. The receipt math now re-reads and
    re-derives when the order changed under it."""
    from test_grn_accept_atomic_claim import _StockRepo, _grn
    from test_grn_accept_atomic_claim import _run as run_sync

    def manager_cancels_the_rest(_repo):
        _run(v.cancel_po("PO1", "vendor out of stock", _user(roles=("ADMIN",))))

    # Read 1 is the accept's hold on the order, read 2 prices the units,
    # read 3 is the one the receipt math writes back.
    repo = _wire_racing(monkeypatch, _po(status="SENT"), manager_cancels_the_rest, at=3)
    grn = _grn(qty=2, po_id="PO1", store_id="S1")
    grn_repo = _AcceptedGrnRepo(grn)
    monkeypatch.setattr(v, "get_grn_repository", lambda: grn_repo)
    monkeypatch.setattr(v, "get_stock_repository", lambda: _StockRepo())
    monkeypatch.setattr(v, "_get_db", lambda: None)

    out = run_sync(v.accept_grn("GRN-1", _user(roles=("ADMIN",), uid="u-admin")))

    assert repo.race is None, "the cancel never landed in the window"
    doc = repo.collection.docs[0]
    p1, p2 = doc["items"]
    assert (p2["quantity"], p2["line_status"]) == (0, "CANCELLED")
    assert (p1["received_qty"], p1["line_status"]) == (2, "RECEIVED")
    assert doc["status"] == out["po_status"] == "RECEIVED"
    assert [h["label"] for h in doc["history"]] == ["Rest cancelled"]


def test_a_rejected_delivery_accepted_during_a_cancel_leaves_it_cancelled(monkeypatch):
    """Same window, nothing kept (every unit rejected): the cancel withdraws the
    whole order, and the accept must not re-derive it back to part-received --
    that would put all 5 units back on the due lists."""
    from test_grn_accept_atomic_claim import _StockRepo, _grn
    from test_grn_accept_atomic_claim import _run as run_sync

    def manager_cancels(_repo):
        _run(v.cancel_po("PO1", "vendor sent the wrong model", _user(roles=("ADMIN",))))

    repo = _wire_racing(monkeypatch, _po(status="SENT"), manager_cancels, at=3)
    grn = _grn(qty=0, po_id="PO1", store_id="S1")
    grn_repo = _AcceptedGrnRepo(grn)
    monkeypatch.setattr(v, "get_grn_repository", lambda: grn_repo)
    monkeypatch.setattr(v, "get_stock_repository", lambda: _StockRepo())
    monkeypatch.setattr(v, "_get_db", lambda: None)

    run_sync(v.accept_grn("GRN-1", _user(roles=("ADMIN",), uid="u-admin")))

    assert repo.race is None, "the cancel never landed in the window"
    assert repo.collection.docs[0]["status"] == "CANCELLED"


def test_a_receipt_logged_during_a_cancel_cannot_be_accepted(monkeypatch):
    """Verifier LOW: the receipt passed its 'can receive' check on a SENT
    order; the cancel found no waiting box and wrote CANCELLED; the receipt was
    then logged PENDING. Accepting it minted the stock while the order stayed
    Cancelled with nothing received. The accept now holds the order open with
    a compare-and-set before minting -- and a cancelled order refuses it."""
    from test_grn_accept_atomic_claim import _StockRepo, _grn
    from test_grn_accept_atomic_claim import _run as run_sync

    repo, _ = _wire(monkeypatch, _po(status="SENT"))
    _run(v.cancel_po("PO1", "vendor out of stock", _user()))
    assert repo.pos["PO1"]["status"] == "CANCELLED"

    grn_repo = _AcceptedGrnRepo(_grn(qty=2, po_id="PO1", store_id="S1"))
    stock = _StockRepo()
    monkeypatch.setattr(v, "get_grn_repository", lambda: grn_repo)
    monkeypatch.setattr(v, "get_stock_repository", lambda: stock)
    monkeypatch.setattr(v, "_get_db", lambda: None)

    with pytest.raises(HTTPException) as e:
        run_sync(v.accept_grn("GRN-1", _user(roles=("ADMIN",), uid="u-admin")))
    assert e.value.status_code == 409
    assert "Void this receipt" in e.value.detail
    assert stock.rows == [], "no stock on the shelf of a cancelled order"
    assert grn_repo.collection.doc["status"] == "PENDING"  # still voidable
    assert repo.pos["PO1"]["status"] == "CANCELLED"


def test_a_cancel_that_read_the_order_before_an_accept_is_refused(monkeypatch):
    """The other order of events: the accept holds the order first, so a
    cancel working from the order it read before that is refused (reload, and
    the receipt shows) instead of cancelling under the delivery."""
    def accept_holds_the_order(repo):
        v._hold_order_open_for_receipt(repo, {"po_id": "PO1"})

    repo = _wire_racing(monkeypatch, _po(status="SENT"), accept_holds_the_order)
    with pytest.raises(HTTPException) as e:
        _run(v.cancel_po("PO1", "vendor out of stock", _user()))
    assert e.value.status_code == 409
    assert repo.race is None, "the accept never landed in the window"
    assert repo.collection.docs[0]["status"] == "SENT"


def _receipt(pid, qty, ordered):
    """A PENDING receipt as grn_create logs it: the order's quantity for the
    product, read at logging time, stamped on the line."""
    from test_grn_accept_atomic_claim import _grn

    doc = _grn(po_id="PO1", store_id="S1")
    doc["items"] = [{"product_id": pid, "accepted_qty": qty, "location_code": "A1",
                     "ordered_qty": ordered}]
    return doc


def test_a_receipt_logged_during_a_line_cancel_cannot_be_accepted(monkeypatch):
    """Panel LOW-MEDIUM: the hold covered only a whole CANCELLED order. A
    receipt for the Ray-Bans logged (ordered 3 stamped) between the line
    cancel's box check and its write was accepted after it: 3 units on the
    shelf against a line the timeline says was withdrawn, the receipt saying
    3 of 3 exactly. The accept now refuses a receipt whose product the order
    has since cut; the uncut Carreras still receive."""
    from test_grn_accept_atomic_claim import _StockRepo
    from test_grn_accept_atomic_claim import _run as run_sync

    repo, _ = _wire(monkeypatch, _po(status="SENT"))
    _run(v.cancel_po_line("PO1", 1, _line_body(product_id="P2"), _user()))
    stock = _StockRepo()
    monkeypatch.setattr(v, "get_stock_repository", lambda: stock)
    monkeypatch.setattr(v, "_get_db", lambda: None)
    admin = _user(roles=("ADMIN",), uid="u-admin")

    grn_repo = _AcceptedGrnRepo(_receipt("P2", 3, ordered=3))
    monkeypatch.setattr(v, "get_grn_repository", lambda: grn_repo)
    with pytest.raises(HTTPException) as e:
        run_sync(v.accept_grn("GRN-1", admin))
    assert e.value.status_code == 409
    assert "Void this receipt" in e.value.detail
    assert stock.rows == [], "no stock against a withdrawn line"
    assert grn_repo.collection.doc["status"] == "PENDING"  # still voidable
    p2 = repo.pos["PO1"]["items"][1]
    assert (p2["quantity"], p2["received_qty"], p2["line_status"]) == (0, 0, "CANCELLED")

    grn_repo = _AcceptedGrnRepo(_receipt("P1", 2, ordered=2))
    monkeypatch.setattr(v, "get_grn_repository", lambda: grn_repo)
    run_sync(v.accept_grn("GRN-1", admin))
    assert {r["product_id"] for r in stock.rows} == {"P1"} and len(stock.rows) == 2


def test_a_line_cancel_landing_inside_the_accepts_hold_still_refuses_it(monkeypatch):
    """The hold's own window: the line cancel's write lands after the accept
    read the order (still 3 Ray-Bans) and before its compare-and-set. The
    stale read must not pass -- the hold re-reads, sees the cut, refuses."""
    from test_grn_accept_atomic_claim import _StockRepo
    from test_grn_accept_atomic_claim import _run as run_sync

    def line_cancel_write_lands(repo):
        items = copy.deepcopy(repo.collection.docs[0]["items"])
        items[1].update(quantity=0, ordered_qty=0, cancelled_qty=3, line_status="CANCELLED")
        repo.update("PO1", {"items": items})

    repo = _wire_racing(monkeypatch, _po(status="SENT"), line_cancel_write_lands)
    stock = _StockRepo()
    monkeypatch.setattr(v, "get_grn_repository", lambda: _AcceptedGrnRepo(_receipt("P2", 3, 3)))
    monkeypatch.setattr(v, "get_stock_repository", lambda: stock)
    monkeypatch.setattr(v, "_get_db", lambda: None)
    with pytest.raises(HTTPException) as e:
        run_sync(v.accept_grn("GRN-1", _user(roles=("ADMIN",), uid="u-admin")))
    assert e.value.status_code == 409
    assert repo.race is None, "the cancel never landed in the window"
    assert stock.rows == []


def test_a_line_cancel_that_read_the_order_before_an_accept_is_refused(monkeypatch):
    """The other order of events for a line: the accept holds the order first,
    so the line cancel working from the order it read before is refused."""
    def accept_holds_the_order(repo):
        v._hold_order_open_for_receipt(repo, _receipt("P2", 3, ordered=3))

    repo = _wire_racing(monkeypatch, _po(status="SENT"), accept_holds_the_order)
    with pytest.raises(HTTPException) as e:
        _run(v.cancel_po_line("PO1", 1, _line_body(product_id="P2"), _user()))
    assert e.value.status_code == 409
    assert repo.race is None, "the accept never landed in the window"
    assert repo.collection.docs[0]["items"][1]["quantity"] == 3


def test_the_accept_fallback_never_reopens_an_order_a_cancel_closed(monkeypatch):
    """The accept's last resort (its receipt write failed: flag the order
    part-received) was a plain status write too. With the manager's cancel
    already in, it turned a RECEIVED order back to PARTIALLY_RECEIVED."""
    from test_grn_accept_atomic_claim import _StockRepo, _grn
    from test_grn_accept_atomic_claim import _run as run_sync

    def manager_cancels_then_the_db_blips(repo):
        _run(v.cancel_po("PO1", "vendor out of stock", _user(roles=("ADMIN",))))
        real = repo.update_if

        def blip_once(*a, **k):
            repo.update_if = real
            raise RuntimeError("not primary; election in progress")

        repo.update_if = blip_once

    repo = _wire_racing(
        monkeypatch, _po(status="SENT"), manager_cancels_then_the_db_blips, at=3
    )
    grn_repo = _AcceptedGrnRepo(_grn(qty=2, po_id="PO1", store_id="S1"))
    monkeypatch.setattr(v, "get_grn_repository", lambda: grn_repo)
    monkeypatch.setattr(v, "get_stock_repository", lambda: _StockRepo())
    monkeypatch.setattr(v, "_get_db", lambda: None)

    run_sync(v.accept_grn("GRN-1", _user(roles=("ADMIN",), uid="u-admin")))

    assert repo.race is None, "the cancel never landed in the window"
    doc = repo.collection.docs[0]
    assert doc["status"] == "RECEIVED"
    assert doc["items"][1]["line_status"] == "CANCELLED"


def _lagging_copy():
    """PARTIALLY_RECEIVED; the receipts put P1 x2 on the shelf but the order's
    own line copy still says 0 / OPEN (grn_accept's fallback writes only the
    status). P3 is still due."""
    items = [
        _line("P1", "Carrera CA8895", 2, 1000),
        _line("P2", "Ray-Ban RB2140", 3, 2000),
        _line("P3", "Oakley OX8046", 1, 3000),
    ]
    po = _po(status="PARTIALLY_RECEIVED", items=items)
    grns = [{"po_id": "PO1", "grn_number": "RCPT/0013", "status": "ACCEPTED",
             "items": [{"product_id": "P1", "accepted_qty": 2}]}]
    return po, grns


def test_line_cancel_brings_a_lagging_line_copy_up_to_date(monkeypatch):
    """Verifier LOW: the receive inbox reads a line's own received_qty before
    the header. Cancelling P2 wrote the right header but left P1 at 0 / OPEN,
    so P1 x2 showed as still pending on the order."""
    po, grns = _lagging_copy()
    repo, _ = _wire(monkeypatch, po, grns=grns)
    _run(v.cancel_po_line("PO1", 1, _line_body(product_id="P2"), _user()))
    p1, p2, p3 = repo.pos["PO1"]["items"]
    assert (p1["received_qty"], p1["line_status"]) == (2, "RECEIVED")
    assert p2["line_status"] == "CANCELLED"
    assert (p3["quantity"], p3["line_status"]) == (1, "OPEN")
    assert repo.pos["PO1"]["status"] == "PARTIALLY_RECEIVED"  # P3 still due


def test_cancel_brings_a_line_with_nothing_due_up_to_date(monkeypatch):
    """Same lag through 'cancel what is still due': P1 has nothing due, so the
    old early return skipped it and it kept 0 / OPEN on a RECEIVED order."""
    po, grns = _lagging_copy()
    repo, _ = _wire(monkeypatch, po, grns=grns)
    _run(v.cancel_po("PO1", "vendor out of stock", _user()))
    doc = repo.pos["PO1"]
    p1 = doc["items"][0]
    assert (p1["received_qty"], p1["line_status"]) == (2, "RECEIVED")
    assert doc["status"] == "RECEIVED"


# --------------------------------------------------------------------------- #
# Review round 7: a part-accepted receipt that was then ESCALATED is in no
# receipt sum, yet its units are on the shelf. The cancel read "nothing
# arrived" and cancelled the whole order (or the line) over 5 shelved frames.
# --------------------------------------------------------------------------- #


def _escalated_world(monkeypatch):
    from database.repositories.product_repository import StockRepository
    from database.repositories.vendor_repository import GRNRepository
    from test_hub_phase2_grn_hero import _ProductRepo as _HeroProducts, _complete_frame

    monkeypatch.setenv("PM_MIRROR_ENABLED", "")
    po_repo = PurchaseOrderRepository(StrictCollection("purchase_orders", []))
    grn_repo = GRNRepository(StrictCollection("grns", []))
    stock_repo = StockRepository(StrictCollection("stock_units", []))
    po_repo.create(_po(status="SENT", items=[
        _line("P1", "Frame A", 5, 1000),
        _line("GHOST", "Frame B (typed in)", 3, 800),  # not catalogued: held
    ], store="S1"))
    grn_repo.create({
        "grn_id": "G1", "grn_number": "RCPT/S1/26-27/0001", "po_id": "PO1",
        "store_id": "S1", "vendor_id": "V1", "status": "PENDING",
        "items": [
            {"product_id": "P1", "received_qty": 5, "accepted_qty": 5,
             "rejected_qty": 0, "ordered_qty": 5, "unit_price": 1000.0},
            {"product_id": "GHOST", "received_qty": 3, "accepted_qty": 3,
             "rejected_qty": 0, "ordered_qty": 3, "unit_price": 800.0},
        ],
    })
    audit = _AuditRepo()
    for name, repo in {
        "get_purchase_order_repository": po_repo,
        "get_grn_repository": grn_repo,
        "get_stock_repository": stock_repo,
        "get_product_repository": _HeroProducts([_complete_frame("P1", cost=1000.0)]),
        "get_audit_repository": audit,
        "get_store_repository": None,
        "get_vendor_repository": None,
    }.items():
        monkeypatch.setattr(v, name, lambda r=repo: r)
    monkeypatch.setattr(v, "_get_db", lambda: None)
    monkeypatch.setattr(v, "is_online_store", lambda *a, **k: False)
    admin = _user(roles=("ADMIN",), uid="u-mgr")
    out = _run(v.accept_grn("G1", admin))
    assert out["grn_status"] == "PARTIALLY_ACCEPTED" and out["units_added"] == 5
    _run(v.escalate_grn("G1", note="held line, vendor dispute", current_user=admin))
    assert grn_repo.find_by_id("G1")["status"] == "ESCALATED"
    return po_repo, stock_repo, admin


def test_a_cancel_counts_units_an_escalated_receipt_put_on_the_shelf(monkeypatch):
    po_repo, stock_repo, admin = _escalated_world(monkeypatch)
    _run(v.cancel_po("PO1", reason="vendor closed down", current_user=admin))
    po = po_repo.find_by_id("PO1")
    assert po["status"] != "CANCELLED", "cancelled whole over 5 frames on the shelf"
    p1, ghost = po["items"]
    assert (p1["quantity"], p1.get("cancelled_qty", 0)) == (5, 0)
    assert (ghost["quantity"], ghost["cancelled_qty"]) == (0, 3)
    assert len(stock_repo.collection.find({"po_id": "PO1", "product_id": "P1"})) == 5


def test_a_line_cancel_cannot_withdraw_units_an_escalated_receipt_shelved(monkeypatch):
    po_repo, _, admin = _escalated_world(monkeypatch)
    with pytest.raises(HTTPException) as e:
        _run(v.cancel_po_line(
            "PO1", 0, v.POLineCancel(reason="vendor short", product_id="P1"), admin
        ))
    assert e.value.status_code == 400
    assert po_repo.find_by_id("PO1")["items"][0].get("cancelled_qty", 0) == 0
