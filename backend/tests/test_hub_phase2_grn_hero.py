"""
Hub Phase 2 -- the GRN hero flow (cost-at-receiving + ghost-stock gate).

Locks the Phase-2 contract (docs/roadmap/PRODUCT_HUB_RECOMMENDATION.md sec 4):
  * create_po refuses a line whose product is not on the `products` spine (no
    fabricated/placeholder ids -> 422 UNKNOWN_PRODUCT).
  * send_po refuses to SEND while any line is not catalog-complete -- EXCEPT a
    missing cost_price, which legitimately arrives at GRN (400 PO_LINES_INCOMPLETE).
  * accept_grn: a line whose product is not catalogued is HELD (no ghost stock) ->
    GRN PARTIALLY_ACCEPTED + unresolved_lines ("Catalog now"); a DRAFT product
    whose only gap is cost auto-promotes to ACTIVE using the PO cost, then mints.

Endpoints are driven directly with monkeypatched repos (mirrors test_e3w_wiring).

Run: JWT_SECRET_KEY=test python -m pytest \
        backend/tests/test_hub_phase2_grn_hero.py -q
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402

from fastapi import HTTPException  # noqa: E402
from api.routers import vendors as vd  # noqa: E402
from api.routers.vendors import POCreate, POItemCreate  # noqa: E402

_ADMIN = {"user_id": "u-admin", "username": "admin", "roles": ["ADMIN"]}


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _ProductRepo:
    def __init__(self, products):
        self.products = {p["product_id"]: dict(p) for p in products}

    def find_by_id(self, pid):
        p = self.products.get(pid)
        return dict(p) if p else None

    def update(self, pid, fields):
        if pid in self.products:
            self.products[pid].update(fields)
            return True
        return False


class _PORepo:
    def __init__(self):
        self.pos = {}

    def create(self, doc):
        self.pos[doc["po_id"]] = dict(doc)
        return dict(doc)

    def find_by_id(self, pid):
        p = self.pos.get(pid)
        return dict(p) if p else None

    def update(self, pid, fields):
        if pid in self.pos:
            self.pos[pid].update(fields)
            return True
        return False


class _VendorRepo:
    def find_by_id(self, vid):
        return {"vendor_id": vid, "trade_name": "Acme"}


class _StockRepo:
    def __init__(self):
        self.rows = []

    def create(self, doc):
        d = dict(doc)
        d.setdefault("stock_id", "ST-%d" % (len(self.rows) + 1))
        self.rows.append(d)
        return d

    def count(self, flt):
        return sum(1 for r in self.rows if all(r.get(k) == v for k, v in flt.items()))

    def find_many(self, flt, *a, **k):
        return [r for r in self.rows if all(r.get(k2) == v for k2, v in flt.items())]


class _GRNRepo:
    def __init__(self, grn):
        self._grn = grn

    def find_by_id(self, gid):
        return dict(self._grn) if gid == self._grn["grn_id"] else None

    def update(self, gid, patch):
        self._grn.update(patch)
        return True

    def find_many(self, *a, **k):
        return [self._grn]

    def find(self, *a, **k):
        return [self._grn]

    def claim_for_accept(self, gid, from_statuses):
        if gid != self._grn.get("grn_id") or self._grn.get("status") not in from_statuses:
            return None
        pre = dict(self._grn)
        self._grn["status"] = "ACCEPTING"
        return pre


def _complete_frame(
    pid, *, cost=None, colour="BLK", catalog_status="ACTIVE", gaps=None
):
    doc = {
        "product_id": pid,
        "category": "FRAME",
        "attributes": {
            "brand_name": "RB",
            "model_no": "M-" + pid,
            "colour_code": colour,
        },
        "mrp": 5000.0,
        "offer_price": 4500.0,
        "hsn_code": "9003",
        "gst_rate": 5.0,
        "catalog_status": catalog_status,
    }
    if cost is not None:
        doc["cost_price"] = cost
    if colour is None:
        doc["attributes"].pop("colour_code")
    if gaps is not None:
        doc["done_gaps"] = gaps
    return doc


@pytest.fixture(autouse=True)
def _mirror_off(monkeypatch):
    monkeypatch.setenv("PM_MIRROR_ENABLED", "")
    yield


# ===========================================================================
# 1. create_po: PO lines must reference real spine products
# ===========================================================================


def test_create_po_rejects_unknown_product(monkeypatch):
    monkeypatch.setattr(vd, "_po_catalog_gate_on", lambda: True)  # gate ON
    monkeypatch.setattr(vd, "get_vendor_repository", lambda: _VendorRepo())
    monkeypatch.setattr(vd, "get_purchase_order_repository", lambda: _PORepo())
    monkeypatch.setattr(
        vd, "get_product_repository", lambda: _ProductRepo([_complete_frame("P1")])
    )
    body = POCreate(
        vendor_id="V1",
        delivery_store_id="S1",
        items=[
            POItemCreate(
                product_id="new-12345",
                product_name="x",
                sku="SKU-X",
                quantity=1,
                unit_price=10.0,
            )
        ],
    )
    with pytest.raises(HTTPException) as ei:
        _run(vd.create_po(body, _ADMIN))
    assert ei.value.status_code == 422
    assert ei.value.detail["code"] == "UNKNOWN_PRODUCT"
    assert "new-12345" in ei.value.detail["product_ids"]


def test_create_po_accepts_known_product(monkeypatch):
    monkeypatch.setattr(vd, "get_vendor_repository", lambda: _VendorRepo())
    monkeypatch.setattr(vd, "get_purchase_order_repository", lambda: _PORepo())
    monkeypatch.setattr(
        vd, "get_product_repository", lambda: _ProductRepo([_complete_frame("P1")])
    )
    body = POCreate(
        vendor_id="V1",
        delivery_store_id="S1",
        items=[
            POItemCreate(
                product_id="P1",
                product_name="x",
                sku="SKU-1",
                quantity=2,
                unit_price=100.0,
            )
        ],
    )
    out = _run(vd.create_po(body, _ADMIN))
    assert out["po_id"]


# ===========================================================================
# 2. send_po: SENT gate (complete-except-cost is allowed)
# ===========================================================================


def _po_repo_with_line(pid):
    repo = _PORepo()
    repo.pos["PO-1"] = {
        "po_id": "PO-1",
        "status": "DRAFT",
        "items": [{"product_id": pid, "quantity": 1, "unit_price": 100.0}],
    }
    return repo


def test_send_po_blocks_incomplete_line(monkeypatch):
    # product missing colour_code -> real catalogue gap -> cannot send.
    monkeypatch.setattr(vd, "_po_catalog_gate_on", lambda: True)  # gate ON
    prod = _complete_frame(
        "P1", colour=None, catalog_status="DRAFT", gaps=["colour_code", "cost_price"]
    )
    monkeypatch.setattr(
        vd, "get_purchase_order_repository", lambda: _po_repo_with_line("P1")
    )
    monkeypatch.setattr(vd, "get_product_repository", lambda: _ProductRepo([prod]))
    with pytest.raises(HTTPException) as ei:
        _run(vd.send_po("PO-1", _ADMIN))
    assert ei.value.status_code == 400
    assert ei.value.detail["code"] == "PO_LINES_INCOMPLETE"
    assert "colour_code" in ei.value.detail["lines"][0]["missing"]


def test_send_po_allows_complete_except_cost(monkeypatch):
    # product is complete EXCEPT cost_price (which arrives at GRN) -> sendable.
    prod = _complete_frame("P1", cost=None, catalog_status="DRAFT", gaps=["cost_price"])
    po_repo = _po_repo_with_line("P1")
    monkeypatch.setattr(vd, "get_purchase_order_repository", lambda: po_repo)
    monkeypatch.setattr(vd, "get_product_repository", lambda: _ProductRepo([prod]))
    out = _run(vd.send_po("PO-1", _ADMIN))
    assert out["po_id"] == "PO-1"
    assert po_repo.pos["PO-1"]["status"] == "SENT"


def test_send_po_skips_gate_for_auto_generated_source(monkeypatch):
    # Regression (PR #675): cl_po lens-replenishment + demand-forecast POs are
    # written directly via po_repo.create() with a `source`, bypassing the
    # create-side gate. Their lines carry lens_catalog / forecast ids that are NOT
    # on the products spine. With pm.po_catalog_gate ON (now the default), the
    # SEND gate must SKIP any source-bearing PO -- else the entire auto-
    # replenishment send path 400s PO_LINES_INCOMPLETE on a product the gate can
    # never find. Only manually-picked POs (no source) are gated.
    monkeypatch.setattr(vd, "_po_catalog_gate_on", lambda: True)  # gate ON
    repo = _PORepo()
    repo.pos["PO-CL"] = {
        "po_id": "PO-CL",
        "status": "DRAFT",
        "source": "cl_po_generator",
        "items": [{"product_id": "LENS-LINE-1", "quantity": 1, "unit_price": 50.0}],
    }
    monkeypatch.setattr(vd, "get_purchase_order_repository", lambda: repo)
    # product repo is PRESENT but has no matching product -> would 400 if gated.
    monkeypatch.setattr(vd, "get_product_repository", lambda: _ProductRepo([]))
    out = _run(vd.send_po("PO-CL", _ADMIN))
    assert out["po_id"] == "PO-CL"
    assert repo.pos["PO-CL"]["status"] == "SENT"


# ===========================================================================
# 3. accept_grn: ghost-stock gate + cost-at-receiving promote
# ===========================================================================


def _grn(items):
    return {
        "grn_id": "GRN-1",
        "grn_number": "GRN-001",
        "store_id": "S1",
        "po_id": None,
        "status": "PENDING",
        "items": items,
    }


def _wire_grn(monkeypatch, *, grn, product_repo, stock_repo):
    monkeypatch.setattr(vd, "get_grn_repository", lambda: _GRNRepo(grn))
    monkeypatch.setattr(vd, "get_stock_repository", lambda: stock_repo)
    monkeypatch.setattr(vd, "get_purchase_order_repository", lambda: None)
    monkeypatch.setattr(vd, "get_product_repository", lambda: product_repo)
    monkeypatch.setattr(vd, "_get_db", lambda: None)
    monkeypatch.setattr(
        vd, "_cumulative_received_by_product", lambda repo, po_id: {}, raising=False
    )


def test_accept_grn_holds_uncatalogued_line_no_ghost_stock(monkeypatch):
    grn = _grn([{"product_id": "GHOST", "accepted_qty": 3, "unit_price": 50.0}])
    stock = _StockRepo()
    # product repo has NO "GHOST" product on the spine.
    _wire_grn(
        monkeypatch,
        grn=grn,
        product_repo=_ProductRepo([_complete_frame("P1", cost=10)]),
        stock_repo=stock,
    )
    out = _run(vd.accept_grn("GRN-1", _ADMIN))
    assert out["needs_cataloguing"] is True
    assert out["grn_status"] == "PARTIALLY_ACCEPTED"
    assert out["units_added"] == 0
    assert len(stock.rows) == 0  # NO ghost stock minted
    assert out["unresolved_lines"][0]["product_id"] == "GHOST"


def test_accept_grn_backfills_cost_and_promotes_draft(monkeypatch):
    # A DRAFT product whose only gap is cost. Receiving fills cost from the PO
    # line -> auto-ACTIVE -> mints stock.
    prod = _complete_frame("P1", cost=None, catalog_status="DRAFT", gaps=["cost_price"])
    product_repo = _ProductRepo([prod])
    grn = _grn([{"product_id": "P1", "accepted_qty": 2, "unit_price": 1800.0}])
    stock = _StockRepo()
    _wire_grn(monkeypatch, grn=grn, product_repo=product_repo, stock_repo=stock)
    out = _run(vd.accept_grn("GRN-1", _ADMIN))
    assert out["grn_status"] == "ACCEPTED"
    assert out["units_added"] == 2
    # the product was promoted + costed in place.
    promoted = product_repo.products["P1"]
    assert promoted["cost_price"] == 1800.0
    assert promoted["catalog_status"] == "ACTIVE"
    # minted units carry the receipt cost.
    assert all(r.get("cost_price") == 1800.0 for r in stock.rows)


def test_accept_grn_reaccept_after_cataloguing_mints_resolved(monkeypatch):
    # First accept holds the line (uncatalogued); after the product is catalogued
    # a re-accept mints it (PARTIALLY_ACCEPTED is re-acceptable).
    product_repo = _ProductRepo([])  # empty: line is uncatalogued at first
    grn = _grn([{"product_id": "P9", "accepted_qty": 1, "unit_price": 200.0}])
    stock = _StockRepo()
    _wire_grn(monkeypatch, grn=grn, product_repo=product_repo, stock_repo=stock)
    first = _run(vd.accept_grn("GRN-1", _ADMIN))
    assert first["grn_status"] == "PARTIALLY_ACCEPTED" and first["units_added"] == 0
    # catalogue the product, then re-accept the (now PARTIALLY_ACCEPTED) GRN.
    product_repo.products["P9"] = _complete_frame("P9", cost=200.0)
    second = _run(vd.accept_grn("GRN-1", _ADMIN))
    assert second["grn_status"] == "ACCEPTED"
    assert second["units_added"] == 1
    assert len(stock.rows) == 1


def test_accept_grn_two_lines_same_product_both_mint(monkeypatch):
    # Adversarial P1: a GRN with TWO lines for the SAME product must mint BOTH
    # lines' qty. A product-keyed idempotency count dropped the second line; the
    # grn_line_index key fixes it.
    product_repo = _ProductRepo([_complete_frame("P1", cost=10.0)])
    grn = _grn(
        [
            {"product_id": "P1", "accepted_qty": 3, "unit_price": 10.0},
            {"product_id": "P1", "accepted_qty": 2, "unit_price": 10.0},
        ]
    )
    stock = _StockRepo()
    _wire_grn(monkeypatch, grn=grn, product_repo=product_repo, stock_repo=stock)
    out = _run(vd.accept_grn("GRN-1", _ADMIN))
    assert out["grn_status"] == "ACCEPTED"
    assert out["units_added"] == 5  # 3 + 2, not 3 (line-keyed idempotency)
    assert len(stock.rows) == 5
    # each line minted its own qty under its own grn_line_index
    assert sum(1 for r in stock.rows if r.get("grn_line_index") == 0) == 3
    assert sum(1 for r in stock.rows if r.get("grn_line_index") == 1) == 2


def test_accept_grn_holds_incomplete_draft_beyond_cost(monkeypatch):
    # Adversarial P2: a DRAFT whose gaps go BEYOND cost (missing colour_code) must
    # NOT mint sellable stock even though its product exists -- it is HELD like an
    # uncatalogued line (no sellable stock for a non-purchasable DRAFT).
    prod = _complete_frame(
        "P1",
        colour=None,
        cost=None,
        catalog_status="DRAFT",
        gaps=["colour_code", "cost_price"],
    )
    product_repo = _ProductRepo([prod])
    grn = _grn([{"product_id": "P1", "accepted_qty": 2, "unit_price": 100.0}])
    stock = _StockRepo()
    _wire_grn(monkeypatch, grn=grn, product_repo=product_repo, stock_repo=stock)
    out = _run(vd.accept_grn("GRN-1", _ADMIN))
    assert out["grn_status"] == "PARTIALLY_ACCEPTED"
    assert out["units_added"] == 0
    assert len(stock.rows) == 0
    assert out["unresolved_lines"][0]["reason"] == "incomplete_catalog"


def test_create_po_rejects_multiple_unknown_products(monkeypatch):
    # Adversarial test-debt: the gate accumulates ALL unknown ids (not raise-on-first).
    monkeypatch.setattr(vd, "_po_catalog_gate_on", lambda: True)  # gate ON
    monkeypatch.setattr(vd, "get_vendor_repository", lambda: _VendorRepo())
    monkeypatch.setattr(vd, "get_purchase_order_repository", lambda: _PORepo())
    monkeypatch.setattr(
        vd, "get_product_repository", lambda: _ProductRepo([_complete_frame("P1")])
    )
    body = POCreate(
        vendor_id="V1",
        delivery_store_id="S1",
        items=[
            POItemCreate(
                product_id="P1",
                product_name="ok",
                sku="S1",
                quantity=1,
                unit_price=10.0,
            ),
            POItemCreate(
                product_id="new-A",
                product_name="x",
                sku="SX",
                quantity=1,
                unit_price=10.0,
            ),
            POItemCreate(
                product_id="new-B",
                product_name="y",
                sku="SY",
                quantity=1,
                unit_price=10.0,
            ),
        ],
    )
    with pytest.raises(HTTPException) as ei:
        _run(vd.create_po(body, _ADMIN))
    assert ei.value.status_code == 422
    assert set(ei.value.detail["product_ids"]) == {"new-A", "new-B"}


def test_create_po_failsoft_when_no_product_repo(monkeypatch):
    # Adversarial test-debt: gate skipped (fail-soft) when product_repo is None.
    monkeypatch.setattr(vd, "get_vendor_repository", lambda: _VendorRepo())
    monkeypatch.setattr(vd, "get_purchase_order_repository", lambda: _PORepo())
    monkeypatch.setattr(vd, "get_product_repository", lambda: None)
    body = POCreate(
        vendor_id="V1",
        delivery_store_id="S1",
        items=[
            POItemCreate(
                product_id="new-anything",
                product_name="x",
                sku="SX",
                quantity=1,
                unit_price=10.0,
            )
        ],
    )
    out = _run(vd.create_po(body, _ADMIN))
    assert out["po_id"]  # no 422


def test_send_po_failsoft_when_no_product_repo(monkeypatch):
    po_repo = _po_repo_with_line("P-whatever")
    monkeypatch.setattr(vd, "get_purchase_order_repository", lambda: po_repo)
    monkeypatch.setattr(vd, "get_product_repository", lambda: None)
    out = _run(vd.send_po("PO-1", _ADMIN))
    assert out["po_id"] == "PO-1"
    assert po_repo.pos["PO-1"]["status"] == "SENT"


def test_create_po_gate_dark_by_default_allows_unknown(monkeypatch):
    # pm.po_catalog_gate is DARK by default -> the manual free-text Create-PO
    # flow (fabricated new-<ts> ids) keeps working until the Buy Desk picker ships.
    monkeypatch.setattr(vd, "_po_catalog_gate_on", lambda: False)  # explicit dark
    monkeypatch.setattr(vd, "get_vendor_repository", lambda: _VendorRepo())
    monkeypatch.setattr(vd, "get_purchase_order_repository", lambda: _PORepo())
    monkeypatch.setattr(
        vd, "get_product_repository", lambda: _ProductRepo([_complete_frame("P1")])
    )
    body = POCreate(
        vendor_id="V1",
        delivery_store_id="S1",
        items=[
            POItemCreate(
                product_id="new-99999",
                product_name="freetext",
                sku="N/A",
                quantity=1,
                unit_price=10.0,
            )
        ],
    )
    out = _run(vd.create_po(body, _ADMIN))
    assert out["po_id"]  # gate dark -> no 422


def test_send_po_gate_dark_by_default_allows_uncatalogued(monkeypatch):
    monkeypatch.setattr(vd, "_po_catalog_gate_on", lambda: False)
    po_repo = _po_repo_with_line("new-77777")
    monkeypatch.setattr(vd, "get_purchase_order_repository", lambda: po_repo)
    monkeypatch.setattr(vd, "get_product_repository", lambda: _ProductRepo([]))
    out = _run(vd.send_po("PO-1", _ADMIN))
    assert po_repo.pos["PO-1"]["status"] == "SENT"  # gate dark -> sends
