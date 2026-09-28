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

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from api.routers import vendors as v  # noqa: E402


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
        return [g for g in self.grns if g.get("po_id") == flt.get("po_id")]


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


@pytest.mark.parametrize("status", ["SENT", "PARTIALLY_RECEIVED", "RECEIVED", "CANCELLED"])
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


@pytest.mark.parametrize("reason", ["", "   ", "x"])
def test_cancel_needs_a_real_reason(monkeypatch, reason):
    repo, audit = _wire(monkeypatch, _po(status="SENT"))
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
    assert audit.rows[-1]["action"] == "purchase_order.cancel_line"


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
