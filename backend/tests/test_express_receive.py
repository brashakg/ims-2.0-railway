"""
IMS 2.0 - Express receive (procurement Phase 2, one-shot clean-delivery chain)
==============================================================================
POST /vendors/grn/express runs create -> accept -> invoice-draft preview ->
3-way-match preview -> accountant task server-side for a CLEAN delivery
(every line fully accepted, nothing rejected). Stock + money critical, so the
tests assert every existing receiving control is PRESERVED, not re-implemented:

  * CLEAN-ONLY: any rejected unit / short-accept / zero receipt is a 400 with
    the stable code EXPRESS_NOT_CLEAN (the FE falls back to two-step receive)
    and NOTHING is persisted.
  * STANDARD-only: a DELIVERY_CHALLAN body is 400 EXPRESS_STANDARD_ONLY.
  * F-S3 attachment gate unchanged: missing doc -> 400 ATTACHMENT_REQUIRED;
    forged/stale file id -> 400 ATTACHMENT_INVALID (BUG-010). No GRN persisted.
  * F2 store boundary: a store-scoped caller receiving another store's PO
    reads 404 with NO mutation; the GRN books to the PO's delivery store.
  * Duplicate-accept idempotence: re-accepting the express-accepted GRN is a
    400 and mints nothing extra.
  * F3: the invoice draft only computes AFTER the GRN is ACCEPTED (the draft
    path re-asserts it via _load_standard_grn) -- express stops at a DRAFT
    (no vendor_bills write, no AP booking).
  * Failure atomicity: an accept failure AFTER the GRN exists surfaces as
    500 EXPRESS_PARTIAL carrying the grn_id (the pending panel recovers it).
  * RBAC: gated to the SAME receiving roles as create/accept GRN, with a
    matching rbac_policy row.

Style: monkeypatched-repo unit tests calling the router function directly
(same approach as test_grn_void.py / test_po_store_boundary.py).

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_express_receive.py -q
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from api.routers import vendors as vendors_mod  # noqa: E402
from api.routers import purchase_invoices as pi_mod  # noqa: E402


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


class _FakeGRNRepo:
    def __init__(self):
        self.docs = {}

    def create(self, doc):
        self.docs[doc["grn_id"]] = dict(doc)
        return dict(doc)

    def find_by_id(self, grn_id):
        d = self.docs.get(grn_id)
        return dict(d) if d else None

    def find_one(self, flt):
        for d in self.docs.values():
            if all(d.get(k) == v for k, v in flt.items()):
                return dict(d)
        return None

    def find_many(self, flt, limit=1000):
        out = []
        for d in self.docs.values():
            if all(d.get(k) == v for k, v in (flt or {}).items()):
                out.append(dict(d))
        return out[:limit]

    def update(self, grn_id, fields):
        if grn_id in self.docs:
            self.docs[grn_id].update(fields)
            return True
        return False


class _FakePORepo:
    def __init__(self, po):
        self.po = po
        self.mutations = []

    def find_by_id(self, pid):
        return dict(self.po) if self.po and pid == self.po["po_id"] else None

    def update(self, pid, patch):
        self.mutations.append((pid, patch))
        if self.po and pid == self.po["po_id"]:
            self.po.update(patch)
        return True


class _FakeStockRepo:
    def __init__(self):
        self.units = []

    def count(self, flt):
        n = 0
        for u in self.units:
            if all(u.get(k) == v for k, v in (flt or {}).items()):
                n += 1
        return n

    def create(self, doc):
        doc = dict(doc)
        doc["stock_id"] = f"STK-{len(self.units) + 1}"
        self.units.append(doc)
        return doc


class _FakeTaskRepo:
    def __init__(self):
        self.created = []

    def find_many(self, flt):
        return []

    def create(self, doc):
        self.created.append(dict(doc))
        return dict(doc)


class _FileStore:
    """Live store whose get() finds the attachment (gate passes)."""

    def get(self, _fid):
        return (b"x", "inv.pdf", "application/pdf")


class _EmptyFileStore:
    """Live store whose get() finds NOTHING (forged/stale id -> BUG-010)."""

    def get(self, _fid):
        return None


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _user(roles=("STORE_MANAGER",), active="STORE-A", stores=None):
    return {
        "user_id": "u1",
        "username": "receiver",
        "roles": list(roles),
        "active_store_id": active,
        "store_ids": stores if stores is not None else ([active] if active else []),
    }


def _po(store_id="STORE-A", status="SENT"):
    return {
        "po_id": "PO-1",
        "po_number": "PO-2601-1",
        "vendor_id": "V1",
        "vendor_name": "Acme Optical",
        "delivery_store_id": store_id,
        "status": status,
        "items": [
            {
                "product_id": "P1",
                "product_name": "Frame X",
                "sku": "S1",
                "quantity": 5,
                "unit_price": 100.0,
                "gst_rate": 12,
                "hsn": "9003",
            }
        ],
    }


def _body(**overrides):
    base = {
        "po_id": "PO-1",
        "vendor_invoice_no": "INV-42",
        "vendor_invoice_date": "2026-07-01",
        "items": [
            {
                "product_id": "P1",
                "received_qty": 5,
                "accepted_qty": 5,
                "rejected_qty": 0,
            }
        ],
        "attachment_file_id": "FILE-1",
        "attachment_filename": "inv.pdf",
        "attachment_mime": "application/pdf",
    }
    base.update(overrides)
    return vendors_mod.ExpressGRNCreate(**base)


def _wire(monkeypatch, *, po=None, file_store=None, product_repo=None):
    """Patch BOTH modules the chain crosses (vendors + purchase_invoices) with
    one consistent set of fakes; returns them for assertions."""
    grn_repo = _FakeGRNRepo()
    po_repo = _FakePORepo(po if po is not None else _po())
    stock_repo = _FakeStockRepo()
    task_repo = _FakeTaskRepo()

    monkeypatch.setattr(vendors_mod, "get_grn_repository", lambda: grn_repo)
    monkeypatch.setattr(vendors_mod, "get_purchase_order_repository", lambda: po_repo)
    monkeypatch.setattr(vendors_mod, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr(vendors_mod, "get_product_repository", lambda: product_repo)
    monkeypatch.setattr(vendors_mod, "get_audit_repository", lambda: None)
    monkeypatch.setattr(
        vendors_mod,
        "get_file_store",
        lambda: (file_store if file_store is not None else _FileStore()),
    )
    monkeypatch.setattr(vendors_mod, "generate_grn_number", lambda s: f"GRN-{s}")
    monkeypatch.setattr(vendors_mod, "_grn_barcode", lambda s, p: "BC-TEST")
    monkeypatch.setattr(vendors_mod, "_get_db", lambda: None)
    # The accountant task is created via a call-time import of
    # api.dependencies.get_task_repository -- patch it at the source module.
    import api.dependencies as deps

    monkeypatch.setattr(deps, "get_task_repository", lambda: task_repo)

    # purchase_invoices side (invoice draft + match preview).
    monkeypatch.setattr(pi_mod, "get_grn_repository", lambda: grn_repo)
    monkeypatch.setattr(pi_mod, "get_purchase_order_repository", lambda: po_repo)
    monkeypatch.setattr(pi_mod, "get_vendor_repository", lambda: None)
    monkeypatch.setattr(pi_mod, "_get_db", lambda: None)

    return grn_repo, po_repo, stock_repo, task_repo


def _run(body, user):
    return asyncio.run(vendors_mod.express_receive_grn(body, current_user=user))


# --------------------------------------------------------------------------- #
# Happy path -- the whole chain
# --------------------------------------------------------------------------- #


def test_express_happy_path_full_chain(monkeypatch):
    grn_repo, po_repo, stock_repo, task_repo = _wire(monkeypatch)
    res = _run(_body(), _user())

    # Receive completed: GRN accepted, stock minted once per unit.
    assert res["grn_id"]
    assert res["grn_number"] == "GRN-STORE-A"
    assert res["accepted_units"] == 5
    assert len(stock_repo.units) == 5
    assert all(u["status"] == "AVAILABLE" for u in stock_repo.units)
    assert all(u["store_id"] == "STORE-A" for u in stock_repo.units)
    grn = grn_repo.find_by_id(res["grn_id"])
    assert grn["status"] == "ACCEPTED"
    assert grn["store_id"] == "STORE-A"

    # PO advanced to fully received (5 of 5).
    assert res["po_status"] == "RECEIVED"
    assert po_repo.po["status"] == "RECEIVED"

    # Invoice DRAFT computed (F3-gated path) but NOTHING booked.
    d = res["invoice_draft"]
    assert d is not None
    assert d["vendor_id"] == "V1"
    assert d["invoice_number"] == "INV-42"
    assert d["lines_count"] == 1
    assert d["totals"]["taxable_total"] == 500.0
    assert d["totals"]["total"] > 0

    # 3-way match preview: clean receipt at PO price -> MATCHED, 0 exceptions.
    assert res["match_preview"] == {"match_status": "MATCHED", "exception_count": 0}

    # Accountant task created with the deep link + verdict payload.
    assert res["accountant_task_id"]
    assert len(task_repo.created) == 1
    task = task_repo.created[0]
    assert task["task_id"] == res["accountant_task_id"]
    assert task["assigned_to"] == "ACCOUNTANT"
    assert task["category"] == "Purchase"
    assert task["store_id"] == "STORE-A"
    assert task["source_ref"] == f"express_invoice:{res['grn_id']}"
    assert task["link"] == f"/purchase/invoices/book?grn_id={res['grn_id']}"
    assert task["payload"]["grn_id"] == res["grn_id"]
    assert task["payload"]["match_status"] == "MATCHED"
    assert "GRN-STORE-A" in task["title"]
    assert "Acme" in task["title"]


def test_express_duplicate_accept_is_idempotent(monkeypatch):
    """Re-accepting the express-accepted GRN is refused and mints nothing --
    the existing PENDING-status guard is preserved through the shared impl."""
    grn_repo, _po_repo, stock_repo, _tasks = _wire(monkeypatch)
    res = _run(_body(), _user())
    assert len(stock_repo.units) == 5
    with pytest.raises(HTTPException) as e:
        asyncio.run(vendors_mod.accept_grn(res["grn_id"], current_user=_user()))
    assert e.value.status_code == 400
    assert len(stock_repo.units) == 5, "no double-mint on re-accept"


# --------------------------------------------------------------------------- #
# CLEAN-ONLY guard
# --------------------------------------------------------------------------- #


def _assert_nothing_persisted(grn_repo, stock_repo):
    assert grn_repo.docs == {}, "no GRN row may exist after a pre-create reject"
    assert stock_repo.units == [], "no stock may be minted after a reject"


def test_express_rejected_qty_is_not_clean(monkeypatch):
    grn_repo, _po, stock_repo, _t = _wire(monkeypatch)
    body = _body(
        items=[
            {"product_id": "P1", "received_qty": 5, "accepted_qty": 4, "rejected_qty": 1}
        ]
    )
    with pytest.raises(HTTPException) as e:
        _run(body, _user())
    assert e.value.status_code == 400
    assert e.value.detail["code"] == "EXPRESS_NOT_CLEAN"
    _assert_nothing_persisted(grn_repo, stock_repo)


def test_express_short_accept_is_not_clean(monkeypatch):
    grn_repo, _po, stock_repo, _t = _wire(monkeypatch)
    body = _body(
        items=[
            {"product_id": "P1", "received_qty": 5, "accepted_qty": 3, "rejected_qty": 0}
        ]
    )
    with pytest.raises(HTTPException) as e:
        _run(body, _user())
    assert e.value.status_code == 400
    assert e.value.detail["code"] == "EXPRESS_NOT_CLEAN"
    _assert_nothing_persisted(grn_repo, stock_repo)


def test_express_zero_received_is_not_clean(monkeypatch):
    grn_repo, _po, stock_repo, _t = _wire(monkeypatch)
    body = _body(
        items=[
            {"product_id": "P1", "received_qty": 0, "accepted_qty": 0, "rejected_qty": 0}
        ]
    )
    with pytest.raises(HTTPException) as e:
        _run(body, _user())
    assert e.value.status_code == 400
    assert e.value.detail["code"] == "EXPRESS_NOT_CLEAN"
    _assert_nothing_persisted(grn_repo, stock_repo)


def test_express_rejects_delivery_challan(monkeypatch):
    grn_repo, _po, stock_repo, _t = _wire(monkeypatch)
    body = _body(grn_subtype="DELIVERY_CHALLAN")
    with pytest.raises(HTTPException) as e:
        _run(body, _user())
    assert e.value.status_code == 400
    assert e.value.detail["code"] == "EXPRESS_STANDARD_ONLY"
    _assert_nothing_persisted(grn_repo, stock_repo)


# --------------------------------------------------------------------------- #
# F-S3 attachment gate (no paper, no stock) -- unchanged through express
# --------------------------------------------------------------------------- #


def test_express_missing_attachment_400(monkeypatch):
    grn_repo, _po, stock_repo, _t = _wire(monkeypatch)
    body = _body(attachment_file_id=None, attachment_filename=None, attachment_mime=None)
    with pytest.raises(HTTPException) as e:
        _run(body, _user())
    assert e.value.status_code == 400
    assert e.value.detail["code"] == "ATTACHMENT_REQUIRED"
    _assert_nothing_persisted(grn_repo, stock_repo)


def test_express_forged_attachment_400(monkeypatch):
    """BUG-010: a non-empty but unknown file id must fail BEFORE persisting."""
    grn_repo, _po, stock_repo, _t = _wire(monkeypatch, file_store=_EmptyFileStore())
    with pytest.raises(HTTPException) as e:
        _run(_body(attachment_file_id="FORGED-ID"), _user())
    assert e.value.status_code == 400
    assert e.value.detail["code"] == "ATTACHMENT_INVALID"
    _assert_nothing_persisted(grn_repo, stock_repo)


# --------------------------------------------------------------------------- #
# F2 store boundary
# --------------------------------------------------------------------------- #


def test_express_cross_store_404_no_mutation(monkeypatch):
    """A STORE_MANAGER of STORE-A expressing STORE-B's PO reads 404; nothing
    is created, minted, or advanced."""
    grn_repo, po_repo, stock_repo, task_repo = _wire(monkeypatch, po=_po("STORE-B"))
    with pytest.raises(HTTPException) as e:
        _run(_body(), _user(roles=("STORE_MANAGER",), active="STORE-A"))
    assert e.value.status_code == 404
    _assert_nothing_persisted(grn_repo, stock_repo)
    assert po_repo.mutations == []
    assert task_repo.created == []


def test_express_books_to_po_store_not_active(monkeypatch):
    """Cross-store ADMIN: the GRN + stock book to the PO's delivery store."""
    grn_repo, _po_repo, stock_repo, _t = _wire(monkeypatch, po=_po("STORE-B"))
    res = _run(_body(), _user(roles=("ADMIN",), active="STORE-A"))
    assert res["grn_number"] == "GRN-STORE-B"
    grn = grn_repo.find_by_id(res["grn_id"])
    assert grn["store_id"] == "STORE-B"
    assert all(u["store_id"] == "STORE-B" for u in stock_repo.units)


def test_express_unreceivable_po_400(monkeypatch):
    grn_repo, _po_repo, stock_repo, _t = _wire(monkeypatch, po=_po(status="RECEIVED"))
    with pytest.raises(HTTPException) as e:
        _run(_body(), _user())
    assert e.value.status_code == 400
    assert "not in receivable status" in str(e.value.detail)
    _assert_nothing_persisted(grn_repo, stock_repo)


# --------------------------------------------------------------------------- #
# Failure atomicity -- EXPRESS_PARTIAL
# --------------------------------------------------------------------------- #


def test_express_accept_failure_surfaces_partial_with_grn_id(monkeypatch):
    grn_repo, _po_repo, _stock, _t = _wire(monkeypatch)

    async def _boom(grn_id, current_user):
        raise RuntimeError("accept exploded")

    monkeypatch.setattr(vendors_mod, "_accept_grn_impl", _boom)
    with pytest.raises(HTTPException) as e:
        _run(_body(), _user())
    assert e.value.status_code == 500
    detail = e.value.detail
    assert detail["code"] == "EXPRESS_PARTIAL"
    # The stranded GRN is addressable (the pending panel recovers it).
    assert detail["grn_id"] in grn_repo.docs
    assert grn_repo.docs[detail["grn_id"]]["status"] == "PENDING"
    assert "accept or void" in detail["message"]


def test_express_held_lines_surface_partial_not_false_success(monkeypatch):
    """An uncatalogued product HOLDs its line -> PARTIALLY_ACCEPTED. Express
    must not report a clean success (F3 blocks the draft anyway)."""

    class _NoProductRepo:
        def find_by_id(self, pid):
            return None  # product not on the spine

    grn_repo, _po_repo, stock_repo, _t = _wire(
        monkeypatch, product_repo=_NoProductRepo()
    )
    with pytest.raises(HTTPException) as e:
        _run(_body(), _user())
    assert e.value.status_code == 500
    detail = e.value.detail
    assert detail["code"] == "EXPRESS_PARTIAL"
    assert detail["grn_status"] == "PARTIALLY_ACCEPTED"
    assert grn_repo.docs[detail["grn_id"]]["status"] == "PARTIALLY_ACCEPTED"
    assert stock_repo.units == []  # the held line minted nothing


# --------------------------------------------------------------------------- #
# Fail-soft periphery: draft/task problems never fail a completed receive
# --------------------------------------------------------------------------- #


def test_express_draft_failure_is_fail_soft(monkeypatch):
    grn_repo, _po_repo, stock_repo, task_repo = _wire(monkeypatch)

    async def _draft_boom(grn_id, current_user):
        raise RuntimeError("draft exploded")

    monkeypatch.setattr(pi_mod, "draft_invoice_from_grn", _draft_boom)
    res = _run(_body(), _user())
    assert res["accepted_units"] == 5
    assert len(stock_repo.units) == 5
    assert res["invoice_draft"] is None
    assert res["match_preview"] is None
    # The accountant task still fires (booking is still owed).
    assert res["accountant_task_id"]
    assert task_repo.created[0]["payload"]["match_status"] is None


def test_express_task_failure_never_rolls_back_receive(monkeypatch):
    grn_repo, _po_repo, stock_repo, _t = _wire(monkeypatch)
    import api.dependencies as deps

    def _no_repo():
        raise RuntimeError("tasks down")

    monkeypatch.setattr(deps, "get_task_repository", _no_repo)
    res = _run(_body(), _user())
    assert res["accepted_units"] == 5
    assert len(stock_repo.units) == 5
    assert res["accountant_task_id"] is None
    assert grn_repo.find_by_id(res["grn_id"])["status"] == "ACCEPTED"


# --------------------------------------------------------------------------- #
# RBAC
# --------------------------------------------------------------------------- #


def test_express_gated_by_vendor_roles():
    """The endpoint's Depends closure must gate on exactly _VENDOR_ROLES (the
    same receiving roles as create/accept GRN -- owner decision)."""
    import inspect

    sig = inspect.signature(vendors_mod.express_receive_grn)
    dep_fn = sig.parameters["current_user"].default.dependency
    closures = [c.cell_contents for c in (dep_fn.__closure__ or [])]
    allowed = next(c for c in closures if isinstance(c, set))
    assert allowed == set(vendors_mod._VENDOR_ROLES)


def test_rbac_row_catalogued():
    from api.services import rbac_policy as rbac

    rows = [
        p
        for p in rbac.POLICY
        if p.get("path") == "/api/v1/vendors/grn/express"
    ]
    assert len(rows) == 1
    assert rows[0]["method"] == "POST"
    assert sorted(rows[0]["allowed"]) == sorted(vendors_mod._VENDOR_ROLES)
