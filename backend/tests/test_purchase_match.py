"""
IMS 2.0 - Purchase 3-Way Match + Inventory Valuation tests (Phase 2)
====================================================================
Covers the procure-to-pay control layered on top of the Phase-1 purchase
invoice:

  ENGINE (pure, no DB -- services/purchase_match.py):
    * three_way_match: MATCHED when ordered/received/invoiced qty + price all
      within tolerance; ON_HOLD_EXCEPTION on a qty short/over, a price gap, a
      product not on the PO, or invoiced-but-not-received -- each with a reason.
    * config: resolve_config defaults (MOVING_AVERAGE + 5%) + normalisers
      (valuation method / tolerance clamp).
    * moving_average_cost + valuation_trueup_for_invoice (blend on receipt;
      FIFO records the latest layer).

  ROUTER (standalone FastAPI + fake DB, no Mongo):
    * POST / with po_id+grn_id stores match_status + match_detail; a clean match
      -> MATCHED, an out-of-tolerance price -> ON_HOLD_EXCEPTION (still booked).
    * booking trues up the product moving-average cost_price.
    * GET /{id}/match returns the detail (and lazily recomputes for an older
      invoice without a stored detail).
    * POST /{id}/approve-exception flips ON_HOLD -> MATCHED_OVERRIDE + audits;
      400 on a non-held invoice; role-gated to ACCOUNTANT/ADMIN.
    * GET/PUT /config: default surfaced; override persisted + normalised.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_purchase_match.py -q
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.services import purchase_match as pmatch  # noqa: E402
from api.routers import purchase_invoices as pi_router  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


SUP_MH = "27ABCDE1234F1Z5"
BUY_MH = "27ZZZZZ9999Z1Z9"


# ===========================================================================
# ENGINE - three_way_match
# ===========================================================================


def _po(items):
    return {"po_id": "PO1", "items": items}


def _grn(items):
    return {"grn_id": "G1", "po_id": "PO1", "items": items}


def _inv_line(pid, qty, unit_price):
    # Mirror the computed-invoice line shape (engine carries taxable+unit_price).
    return {
        "product_id": pid,
        "qty": qty,
        "unit_price": unit_price,
        "taxable": round(qty * unit_price, 2),
    }


class TestThreeWayMatchHappyPath:
    def test_all_within_tolerance_is_matched(self):
        po = _po([{"product_id": "P1", "quantity": 10, "unit_price": 100.0}])
        grn = _grn([{"product_id": "P1", "accepted_qty": 10}])
        inv = [_inv_line("P1", 10, 100.0)]
        res = pmatch.three_way_match(po, grn, inv, tolerance_pct=5)
        assert res["match_status"] == pmatch.MATCH_MATCHED
        assert res["summary"]["exception_lines"] == 0
        assert res["lines"][0]["status"] == "MATCHED"
        assert res["exceptions"] == []

    def test_small_qty_delta_within_tolerance_matched(self):
        # ordered 100, received/invoiced 98 -> 2% < 5% tolerance -> MATCHED.
        po = _po([{"product_id": "P1", "quantity": 100, "unit_price": 50.0}])
        grn = _grn([{"product_id": "P1", "accepted_qty": 98}])
        inv = [_inv_line("P1", 98, 50.0)]
        res = pmatch.three_way_match(po, grn, inv, tolerance_pct=5)
        assert res["match_status"] == pmatch.MATCH_MATCHED

    def test_small_price_delta_within_tolerance_matched(self):
        # PO 100, invoice 104 -> 4% < 5% -> MATCHED.
        po = _po([{"product_id": "P1", "quantity": 10, "unit_price": 100.0}])
        grn = _grn([{"product_id": "P1", "accepted_qty": 10}])
        inv = [_inv_line("P1", 10, 104.0)]
        res = pmatch.three_way_match(po, grn, inv, tolerance_pct=5)
        assert res["match_status"] == pmatch.MATCH_MATCHED


class TestThreeWayMatchExceptions:
    def test_qty_over_tolerance_holds(self):
        # ordered 10, invoiced 13 -> 30% > 5% -> ON_HOLD.
        po = _po([{"product_id": "P1", "quantity": 10, "unit_price": 100.0}])
        grn = _grn([{"product_id": "P1", "accepted_qty": 13}])
        inv = [_inv_line("P1", 13, 100.0)]
        res = pmatch.three_way_match(po, grn, inv, tolerance_pct=5)
        assert res["match_status"] == pmatch.MATCH_ON_HOLD
        assert res["lines"][0]["status"] == "EXCEPTION"
        assert any("qty" in r.lower() for r in res["lines"][0]["reasons"])

    def test_price_over_tolerance_holds(self):
        # PO 100, invoice 130 -> 30% > 5% -> ON_HOLD with a price reason.
        po = _po([{"product_id": "P1", "quantity": 10, "unit_price": 100.0}])
        grn = _grn([{"product_id": "P1", "accepted_qty": 10}])
        inv = [_inv_line("P1", 10, 130.0)]
        res = pmatch.three_way_match(po, grn, inv, tolerance_pct=5)
        assert res["match_status"] == pmatch.MATCH_ON_HOLD
        assert any("price" in r.lower() for r in res["lines"][0]["reasons"])

    def test_product_not_on_po_holds(self):
        po = _po([{"product_id": "P1", "quantity": 10, "unit_price": 100.0}])
        grn = _grn([{"product_id": "P1", "accepted_qty": 10}])
        # Invoice bills an extra product P9 that was never ordered.
        inv = [_inv_line("P1", 10, 100.0), _inv_line("P9", 1, 999.0)]
        res = pmatch.three_way_match(po, grn, inv, tolerance_pct=5)
        assert res["match_status"] == pmatch.MATCH_ON_HOLD
        p9 = [l for l in res["lines"] if l["product_id"] == "P9"][0]
        assert any("not on purchase order" in r.lower() for r in p9["reasons"])

    def test_invoiced_but_not_received_holds(self):
        # Ordered + invoiced 10 but GRN accepted 0 -> billed for goods that
        # didn't arrive.
        po = _po([{"product_id": "P1", "quantity": 10, "unit_price": 100.0}])
        grn = _grn([{"product_id": "P1", "accepted_qty": 0}])
        inv = [_inv_line("P1", 10, 100.0)]
        res = pmatch.three_way_match(po, grn, inv, tolerance_pct=5)
        assert res["match_status"] == pmatch.MATCH_ON_HOLD
        assert any(
            "no goods received" in r.lower() for r in res["lines"][0]["reasons"]
        )

    def test_received_short_holds_even_if_invoice_matches_po(self):
        # Invoice agrees with PO (10 @100) but only 7 were received -> short.
        po = _po([{"product_id": "P1", "quantity": 10, "unit_price": 100.0}])
        grn = _grn([{"product_id": "P1", "accepted_qty": 7}])
        inv = [_inv_line("P1", 10, 100.0)]
        res = pmatch.three_way_match(po, grn, inv, tolerance_pct=5)
        assert res["match_status"] == pmatch.MATCH_ON_HOLD

    def test_zero_tolerance_flags_any_delta(self):
        po = _po([{"product_id": "P1", "quantity": 10, "unit_price": 100.0}])
        grn = _grn([{"product_id": "P1", "accepted_qty": 10}])
        inv = [_inv_line("P1", 10, 101.0)]  # 1% gap
        res = pmatch.three_way_match(po, grn, inv, tolerance_pct=0)
        assert res["match_status"] == pmatch.MATCH_ON_HOLD

    def test_no_comparable_lines_holds(self):
        res = pmatch.three_way_match(None, None, [], tolerance_pct=5)
        assert res["match_status"] == pmatch.MATCH_ON_HOLD


# ===========================================================================
# ENGINE - config defaults + normalisers
# ===========================================================================


class TestConfig:
    def test_resolve_config_defaults_when_none(self):
        cfg = pmatch.resolve_config(None)
        assert cfg["valuation_method"] == pmatch.VALUATION_MOVING_AVERAGE
        assert cfg["match_tolerance_pct"] == 5.0

    def test_resolve_config_override(self):
        cfg = pmatch.resolve_config(
            {"valuation_method": "fifo", "match_tolerance_pct": 2.5}
        )
        assert cfg["valuation_method"] == pmatch.VALUATION_FIFO
        assert cfg["match_tolerance_pct"] == 2.5

    def test_normalize_method_falls_back(self):
        assert pmatch.normalize_valuation_method("garbage") == (
            pmatch.VALUATION_MOVING_AVERAGE
        )
        assert pmatch.normalize_valuation_method("moving-average") == (
            pmatch.VALUATION_MOVING_AVERAGE
        )
        assert pmatch.normalize_valuation_method("FIFO") == pmatch.VALUATION_FIFO

    def test_normalize_tolerance_clamps(self):
        assert pmatch.normalize_tolerance_pct(-3) == 5.0  # negative -> default
        assert pmatch.normalize_tolerance_pct(250) == 100.0  # clamp high
        assert pmatch.normalize_tolerance_pct(0) == 0.0  # 0 allowed
        assert pmatch.normalize_tolerance_pct("x") == 5.0  # junk -> default


# ===========================================================================
# ENGINE - moving-average valuation
# ===========================================================================


class TestMovingAverage:
    def test_blends_old_and_new(self):
        # 10 @100 + 10 @120 -> 20 @110.
        assert pmatch.moving_average_cost(10, 100, 10, 120) == 110.0

    def test_first_receipt_takes_receipt_cost(self):
        # No prior stock -> cost becomes the receipt cost.
        assert pmatch.moving_average_cost(0, 0, 5, 250) == 250.0

    def test_zero_receipt_qty_keeps_old_cost(self):
        assert pmatch.moving_average_cost(10, 100, 0, 999) == 100.0

    def test_trueup_moving_average(self):
        lines = [_inv_line("P1", 10, 120.0)]
        state = {"P1": {"on_hand_qty": 10, "cost_price": 100.0}}
        updates = pmatch.valuation_trueup_for_invoice(
            lines, state, pmatch.VALUATION_MOVING_AVERAGE
        )
        assert len(updates) == 1
        assert updates[0]["new_cost"] == 110.0
        assert updates[0]["old_cost"] == 100.0

    def test_trueup_fifo_records_latest_layer(self):
        lines = [_inv_line("P1", 10, 120.0)]
        state = {"P1": {"on_hand_qty": 10, "cost_price": 100.0}}
        updates = pmatch.valuation_trueup_for_invoice(
            lines, state, pmatch.VALUATION_FIFO
        )
        # FIFO does not blend the product-level cost: latest layer = 120.
        assert updates[0]["new_cost"] == 120.0


# ===========================================================================
# ROUTER - fake DB with update_one + count_documents
# ===========================================================================


class _FakeCollection:
    def __init__(self, store):
        self._store = store  # list of dicts

    def find_one(self, flt, projection=None):
        for d in self._store:
            if all(d.get(k) == v for k, v in flt.items()):
                return dict(d)
        return None

    def find(self, flt=None, projection=None):
        flt = flt or {}

        def _match(d):
            for k, v in flt.items():
                if isinstance(v, dict) and "$in" in v:
                    if d.get(k) not in v["$in"]:
                        return False
                elif d.get(k) != v:
                    return False
            return True

        return _FakeCursor([dict(d) for d in self._store if _match(d)])

    def insert_one(self, doc):
        self._store.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def update_one(self, flt, update, upsert=False):
        for d in self._store:
            if all(d.get(k) == v for k, v in flt.items()):
                d.update(update.get("$set", {}))
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        if upsert:
            newdoc = dict(flt)
            newdoc.update(update.get("$set", {}))
            self._store.append(newdoc)
            return type("R", (), {"modified_count": 0, "matched_count": 0,
                                  "upserted_id": flt.get("_id")})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    def count_documents(self, flt):
        return len(list(self.find(flt)))


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def limit(self, n):
        return _FakeCursor(self._rows[:n])

    def __iter__(self):
        return iter(self._rows)


class _FakeDB:
    def __init__(self):
        self.collections = {
            "vendor_bills": [],
            "vendors": [
                {"vendor_id": "V1", "trade_name": "Acme Optics",
                 "gstin": SUP_MH, "credit_days": 30},
            ],
            "entities": [
                {"entity_id": "E1", "name": "BV",
                 "gstins": [{"gstin": BUY_MH, "state_code": "27",
                             "is_primary": True}]},
            ],
            "stores": [{"store_id": "S1", "entity_id": "E1"}],
            "products": [
                {"product_id": "P1", "cost_price": 100.0},
            ],
            "stock_units": [
                # 10 AVAILABLE units of P1 at store S1 (on-hand for the blend).
                *[
                    {"stock_id": f"U{i}", "product_id": "P1",
                     "store_id": "S1", "status": "AVAILABLE"}
                    for i in range(10)
                ]
            ],
            "grns": [],
            "purchase_settings": [],
        }

    def get_collection(self, name):
        return _FakeCollection(self.collections.setdefault(name, []))


class _Repo:
    """Minimal find_by_id repo over a single doc."""

    def __init__(self, doc):
        self._doc = doc

    def find_by_id(self, _id):
        return dict(self._doc) if self._doc else None


def _app(db, roles=("ACCOUNTANT",), uid="u1", po=None, grn=None):
    app = FastAPI()
    app.include_router(
        pi_router.router, prefix="/api/v1/vendors/purchase-invoices"
    )

    async def _u():
        return {
            "user_id": uid,
            "full_name": "T",
            "username": "t",
            "roles": list(roles),
            "store_ids": ["S1"],
            "active_store_id": "S1",
            "discount_cap": None,
        }

    app.dependency_overrides[get_current_user] = _u
    pi_router._get_db = lambda: db
    pi_router.get_vendor_repository = lambda: _Repo(db.collections["vendors"][0])
    pi_router.get_purchase_order_repository = lambda: _Repo(po) if po else None
    pi_router.get_grn_repository = lambda: _Repo(grn) if grn else None
    pi_router.get_audit_repository = lambda: None
    return TestClient(app)


@pytest.fixture(autouse=True)
def _restore_router():
    saved = (
        pi_router._get_db,
        pi_router.get_vendor_repository,
        pi_router.get_purchase_order_repository,
        pi_router.get_grn_repository,
        pi_router.get_audit_repository,
    )
    yield
    (
        pi_router._get_db,
        pi_router.get_vendor_repository,
        pi_router.get_purchase_order_repository,
        pi_router.get_grn_repository,
        pi_router.get_audit_repository,
    ) = saved


_PO_DOC = {
    "po_id": "PO1", "vendor_id": "V1",
    "items": [{"product_id": "P1", "product_name": "Frame X", "sku": "SKU1",
               "quantity": 10, "unit_price": 100.0, "hsn": "9003"}],
}
_GRN_DOC = {
    "grn_id": "G1", "po_id": "PO1", "vendor_id": "V1", "store_id": "S1",
    # A standard GRN can only be billed once ACCEPTED (F3 server-side guard).
    "status": "ACCEPTED",
    "items": [{"product_id": "P1", "accepted_qty": 10}],
}


def _body(**over):
    body = {
        "vendor_id": "V1",
        "invoice_number": "INV-MATCH-1",
        "invoice_date": "2026-05-01",
        "recipient_entity_id": "E1",
        "po_id": "PO1",
        "grn_id": "G1",
        "lines": [
            {"product_id": "P1", "description": "Frame X", "hsn": "9003",
             "qty": 10, "unit_price": 100, "gst_rate": 5},
        ],
    }
    body.update(over)
    return body


class TestCreateRunsMatch:
    def test_clean_match_is_matched(self):
        db = _FakeDB()
        cli = _app(db, po=_PO_DOC, grn=_GRN_DOC)
        r = cli.post("/api/v1/vendors/purchase-invoices", json=_body())
        assert r.status_code == 201, r.text
        doc = r.json()
        assert doc["match_status"] == pmatch.MATCH_MATCHED
        assert doc["match_detail"]["summary"]["exception_lines"] == 0

    def test_price_mismatch_holds_but_still_books(self):
        db = _FakeDB()
        cli = _app(db, po=_PO_DOC, grn=_GRN_DOC)
        # Invoice price 200 vs PO 100 -> 100% gap -> ON_HOLD. Still 201 booked.
        body = _body(
            invoice_number="INV-HOLD-1",
            lines=[{"product_id": "P1", "qty": 10, "unit_price": 200,
                    "gst_rate": 5}],
            total=2100,  # 10*200 + 5% = 2100, reconciles
        )
        r = cli.post("/api/v1/vendors/purchase-invoices", json=body)
        assert r.status_code == 201, r.text
        doc = r.json()
        assert doc["match_status"] == pmatch.MATCH_ON_HOLD
        assert doc["outstanding"] == 2100.0  # payable recorded despite hold
        assert len(db.collections["vendor_bills"]) == 1

    def test_no_po_grn_link_has_no_match(self):
        db = _FakeDB()
        cli = _app(db)  # no PO/GRN repos
        body = _body(po_id=None, grn_id=None, invoice_number="INV-NOLINK")
        r = cli.post("/api/v1/vendors/purchase-invoices", json=body)
        assert r.status_code == 201, r.text
        assert r.json()["match_status"] is None


class TestValuationTrueUp:
    def test_booking_updates_moving_average_cost(self):
        db = _FakeDB()
        # P1: 10 units on-hand @100. Invoice 10 @120 -> blended 110.
        cli = _app(db, po=_PO_DOC, grn=_GRN_DOC)
        body = _body(
            invoice_number="INV-VAL-1",
            lines=[{"product_id": "P1", "qty": 10, "unit_price": 120,
                    "gst_rate": 5}],
            total=1260,  # 1200 + 5%
        )
        r = cli.post("/api/v1/vendors/purchase-invoices", json=body)
        assert r.status_code == 201, r.text
        prod = [p for p in db.collections["products"]
                if p["product_id"] == "P1"][0]
        assert prod["cost_price"] == 110.0
        assert prod["moving_avg_cost"] == 110.0
        assert prod["cost_source"] == "PURCHASE_INVOICE"


class TestMatchEndpoint:
    def test_get_match_returns_detail(self):
        db = _FakeDB()
        cli = _app(db, po=_PO_DOC, grn=_GRN_DOC)
        created = cli.post(
            "/api/v1/vendors/purchase-invoices", json=_body()
        ).json()
        inv_id = created["invoice_id"]
        r = cli.get(f"/api/v1/vendors/purchase-invoices/{inv_id}/match")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["match_status"] == pmatch.MATCH_MATCHED
        assert data["match_detail"]["summary"]["total_lines"] == 1


class TestApproveException:
    def _book_held(self, db, cli):
        body = _body(
            invoice_number="INV-OVR-1",
            lines=[{"product_id": "P1", "qty": 10, "unit_price": 200,
                    "gst_rate": 5}],
            total=2100,
        )
        return cli.post("/api/v1/vendors/purchase-invoices", json=body).json()

    def test_approve_flips_to_override(self):
        db = _FakeDB()
        cli = _app(db, po=_PO_DOC, grn=_GRN_DOC)
        created = self._book_held(db, cli)
        assert created["match_status"] == pmatch.MATCH_ON_HOLD
        inv_id = created["invoice_id"]
        r = cli.post(
            f"/api/v1/vendors/purchase-invoices/{inv_id}/approve-exception",
            json={"reason": "Negotiated price increase approved by owner"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["match_status"] == pmatch.MATCH_OVERRIDE
        # Persisted on the doc.
        stored = [d for d in db.collections["vendor_bills"]
                  if d["bill_id"] == inv_id][0]
        assert stored["match_status"] == pmatch.MATCH_OVERRIDE
        assert stored["exception_override"]["approved_by"] == "u1"
        assert "Negotiated" in stored["exception_override"]["reason"]

    def test_approve_clean_match_400(self):
        db = _FakeDB()
        cli = _app(db, po=_PO_DOC, grn=_GRN_DOC)
        created = cli.post(
            "/api/v1/vendors/purchase-invoices", json=_body()
        ).json()
        assert created["match_status"] == pmatch.MATCH_MATCHED
        inv_id = created["invoice_id"]
        r = cli.post(
            f"/api/v1/vendors/purchase-invoices/{inv_id}/approve-exception",
            json={"reason": "x"},
        )
        assert r.status_code == 400, r.text

    def test_approve_role_gated(self):
        db = _FakeDB()
        cli = _app(db, roles=("SALES_STAFF",), po=_PO_DOC, grn=_GRN_DOC)
        r = cli.post(
            "/api/v1/vendors/purchase-invoices/whatever/approve-exception",
            json={"reason": "x"},
        )
        assert r.status_code == 403


class TestConfigEndpoint:
    def test_get_config_defaults(self):
        db = _FakeDB()
        cli = _app(db)
        r = cli.get("/api/v1/vendors/purchase-invoices/config")
        assert r.status_code == 200, r.text
        cfg = r.json()["config"]
        assert cfg["valuation_method"] == pmatch.VALUATION_MOVING_AVERAGE
        assert cfg["match_tolerance_pct"] == 5.0

    def test_put_config_override_persists_and_normalises(self):
        db = _FakeDB()
        cli = _app(db, roles=("ADMIN",))
        r = cli.put(
            "/api/v1/vendors/purchase-invoices/config",
            json={"valuation_method": "fifo", "match_tolerance_pct": 2.5},
        )
        assert r.status_code == 200, r.text
        out = r.json()["config"]
        assert out["valuation_method"] == pmatch.VALUATION_FIFO  # normalised
        assert out["match_tolerance_pct"] == 2.5
        # And a subsequent GET reflects the stored override.
        cfg = cli.get("/api/v1/vendors/purchase-invoices/config").json()["config"]
        assert cfg["valuation_method"] == pmatch.VALUATION_FIFO

    def test_put_config_rejects_out_of_range_tolerance(self):
        # The schema bound (0..100) rejects an absurd tolerance LOUDLY (422)
        # before it can ever be stored -- defense at the edge.
        db = _FakeDB()
        cli = _app(db, roles=("ADMIN",))
        r = cli.put(
            "/api/v1/vendors/purchase-invoices/config",
            json={"match_tolerance_pct": 250},
        )
        assert r.status_code == 422

    def test_put_config_role_gated(self):
        db = _FakeDB()
        cli = _app(db, roles=("SALES_STAFF",))
        r = cli.put(
            "/api/v1/vendors/purchase-invoices/config",
            json={"match_tolerance_pct": 3},
        )
        assert r.status_code == 403

    def test_tolerance_drives_match_verdict(self):
        # With a wide tolerance an otherwise-held invoice matches.
        db = _FakeDB()
        db.collections["purchase_settings"].append(
            {"_id": "default", "valuation_method": "MOVING_AVERAGE",
             "match_tolerance_pct": 60}
        )
        cli = _app(db, po=_PO_DOC, grn=_GRN_DOC)
        body = _body(
            invoice_number="INV-TOL-1",
            lines=[{"product_id": "P1", "qty": 10, "unit_price": 150,
                    "gst_rate": 5}],  # 50% over PO, within 60% tol
            total=1575,
        )
        r = cli.post("/api/v1/vendors/purchase-invoices", json=body)
        assert r.status_code == 201, r.text
        assert r.json()["match_status"] == pmatch.MATCH_MATCHED
