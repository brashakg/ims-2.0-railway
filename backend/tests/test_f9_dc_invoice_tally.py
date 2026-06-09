"""
IMS 2.0 - F9: Lens Delivery-Challan -> invoice tally (hardlock) tests
=====================================================================
Feature #9. A lens lab sends external-lab lenses (lens_status=ORDERED) to a
store with a Delivery Challan (DC) -- a goods-receipt doc with NO tax invoice;
the invoice comes later, consolidated. F9 makes the DC the mandatory checkpoint:

  1. DC logging -- a GRN with grn_subtype=DELIVERY_CHALLAN + dc_number + dc_date;
     vendor_invoice_no optional (it arrives later); po_id optional.
  2. WORKSHOP HARDLOCK -- a job for an ORDERED lens with no accepted DC covering
     that SKU at that store is HARD-BLOCKED (422 DC_HARDLOCK). In-house lenses
     are exempt. ADMIN+ may override with a reason (audited). A flag +
     cutover-date gate the lock (grace period; no retroactive block).
  3. DC->INVOICE TALLY -- the accountant consolidates N open DCs into ONE bulk
     invoice; dc_bulk_match tallies DC-received vs billed qty per product.
     A mismatch -> ON_HOLD_EXCEPTION (auto-hold; AP liability still recorded).
     Each linked DC is stamped dc_matched=true so it can't be double-billed.

CI-ROBUSTNESS: every test monkeypatches EVERY repo/db accessor on the routers
under test and SEEDS the docs each handler reads -- there is NO local-vs-CI
fail-soft divergence (a hollow shell that ignores grn_subtype / the hardlock /
the tally cannot pass).

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_f9_dc_invoice_tally.py -q
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
from api.routers import vendors as vendors_router  # noqa: E402
from api.routers import purchase_invoices as pi_router  # noqa: E402
from api.routers import workshop as workshop_router  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402


# ===========================================================================
# In-memory fakes (a real Mongo-shaped DB + repos)
# ===========================================================================


class _FakeCollection:
    """Minimal Mongo collection over a shared list of dicts. Supports the
    operators the F9 handlers use: equality, $ne, $gte/$lte ranges, and a
    dotted 'items.product_id' membership match."""

    def __init__(self, store):
        self._store = store  # shared list of dicts

    @staticmethod
    def _match(doc, flt):
        for k, cond in (flt or {}).items():
            if k == "items.product_id":
                items = doc.get("items") or []
                if not any(
                    isinstance(it, dict) and it.get("product_id") == cond
                    for it in items
                ):
                    return False
                continue
            val = doc.get(k)
            if isinstance(cond, dict):
                for op, target in cond.items():
                    if op == "$ne" and val == target:
                        return False
                    if op == "$gte" and not (val is not None and val >= target):
                        return False
                    if op == "$lte" and not (val is not None and val <= target):
                        return False
                    if op == "$in" and val not in target:
                        return False
            else:
                if val != cond:
                    return False
        return True

    def find_one(self, flt, projection=None):
        for d in self._store:
            if self._match(d, flt):
                return {k: v for k, v in d.items() if k != "_id"}
        return None

    def find(self, flt=None, projection=None):
        rows = [
            {k: v for k, v in d.items() if k != "_id"}
            for d in self._store
            if self._match(d, flt or {})
        ]
        return _FakeCursor(rows)

    def insert_one(self, doc):
        self._store.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def update_one(self, flt, update, upsert=False):
        for d in self._store:
            if self._match(d, flt):
                d.update(update.get("$set", {}))
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        if upsert:
            new = dict(flt)
            new.update(update.get("$set", {}))
            self._store.append(new)
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    def find_one_and_update(self, flt, update, **kw):
        for d in self._store:
            if self._match(d, flt):
                d.update(update.get("$set", {}))
                return {k: v for k, v in d.items() if k != "_id"}
        return None

    def count_documents(self, flt):
        return sum(1 for d in self._store if self._match(d, flt or {}))


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
            "grns": [],
            "vendor_bills": [],
            "vendors": [
                {
                    "vendor_id": "V1",
                    "trade_name": "Zeiss Lab",
                    "gstin": "27ABCDE1234F1Z5",
                    "credit_days": 30,
                }
            ],
            "entities": [
                {
                    "entity_id": "E1",
                    "name": "Better Vision",
                    "gstins": [
                        {
                            "gstin": "20ZZZZZ9999Z1Z9",
                            "state_code": "20",
                            "is_primary": True,
                        }
                    ],
                }
            ],
            "stores": [{"store_id": "S1", "entity_id": "E1"}],
            "purchase_settings": [],
            "period_locks": [],
            "stock_units": [],
        }

    def get_collection(self, name):
        return _FakeCollection(self.collections.setdefault(name, []))

    @property
    def is_connected(self):
        return True


class _GRNRepo:
    """GRN repository over the SAME _FakeDB 'grns' list so the vendors router
    and the purchase-invoices router see one shared store."""

    def __init__(self, db):
        self._coll = _FakeCollection(db.collections["grns"])

    def create(self, doc):
        self._coll.insert_one(doc)
        return doc

    def find_by_id(self, grn_id):
        return self._coll.find_one({"grn_id": grn_id})

    def find_many(self, flt=None, sort=None, skip=0, limit=100):
        rows = list(self._coll.find(flt or {}))
        return rows[skip : skip + limit] if limit else rows

    def update(self, grn_id, data):
        return self._coll.update_one({"grn_id": grn_id}, {"$set": data}).modified_count > 0

    def count(self, flt):
        return self._coll.count_documents(flt)


class _PORepo:
    def __init__(self, po):
        self._po = po

    def find_by_id(self, _id):
        return dict(self._po) if (self._po and self._po.get("po_id") == _id) else None


class _WorkshopRepo:
    """In-memory workshop_jobs repo."""

    def __init__(self):
        self._jobs = []

    def create(self, doc):
        doc = dict(doc)
        doc.setdefault("job_id", f"JOB-{len(self._jobs) + 1}")
        self._jobs.append(doc)
        return doc

    def find_by_id(self, job_id):
        for j in self._jobs:
            if j.get("job_id") == job_id:
                return dict(j)
        return None


class _AuditRepo:
    def __init__(self):
        self.rows = []

    def create(self, data):
        self.rows.append(dict(data))
        return dict(data)


def _user(roles=("STORE_MANAGER",), uid="u1"):
    return {
        "user_id": uid,
        "full_name": "T",
        "username": "t",
        "roles": list(roles),
        "store_ids": ["S1"],
        "active_store_id": "S1",
        "discount_cap": None,
    }


# ===========================================================================
# Fixtures: standalone apps with every accessor monkeypatched
# ===========================================================================


@pytest.fixture(autouse=True)
def _restore_globals():
    """Snapshot + restore every router global we monkeypatch so no fakes leak
    between tests."""
    saved_v = (
        vendors_router._get_db,
        vendors_router.get_grn_repository,
        vendors_router.get_purchase_order_repository,
        vendors_router.get_audit_repository,
        vendors_router.validate_store_access,
    )
    saved_pi = (
        pi_router._get_db,
        pi_router.get_vendor_repository,
        pi_router.get_purchase_order_repository,
        pi_router.get_grn_repository,
        pi_router.get_audit_repository,
    )
    saved_w = (
        workshop_router.get_db,
        workshop_router.get_workshop_repository,
        workshop_router.get_order_repository,
        workshop_router.get_audit_repository,
    )
    yield
    (
        vendors_router._get_db,
        vendors_router.get_grn_repository,
        vendors_router.get_purchase_order_repository,
        vendors_router.get_audit_repository,
        vendors_router.validate_store_access,
    ) = saved_v
    (
        pi_router._get_db,
        pi_router.get_vendor_repository,
        pi_router.get_purchase_order_repository,
        pi_router.get_grn_repository,
        pi_router.get_audit_repository,
    ) = saved_pi
    (
        workshop_router.get_db,
        workshop_router.get_workshop_repository,
        workshop_router.get_order_repository,
        workshop_router.get_audit_repository,
    ) = saved_w


def _vendors_client(db, grn_repo, po=None, audit=None, roles=("STORE_MANAGER",)):
    app = FastAPI()
    app.include_router(vendors_router.router, prefix="/api/v1/vendors")

    async def _u():
        return _user(roles)

    app.dependency_overrides[get_current_user] = _u
    vendors_router._get_db = lambda: db
    vendors_router.get_grn_repository = lambda: grn_repo
    vendors_router.get_purchase_order_repository = lambda: _PORepo(po) if po else None
    vendors_router.get_audit_repository = lambda: audit
    vendors_router.validate_store_access = lambda sid, u: sid or u.get("active_store_id")
    return TestClient(app)


def _pi_client(db, grn_repo, audit=None, roles=("ACCOUNTANT",)):
    app = FastAPI()
    app.include_router(
        pi_router.router, prefix="/api/v1/vendors/purchase-invoices"
    )

    async def _u():
        return _user(roles)

    app.dependency_overrides[get_current_user] = _u
    pi_router._get_db = lambda: db
    pi_router.get_vendor_repository = lambda: _PORepo(None)  # not used by F9 paths
    pi_router.get_purchase_order_repository = lambda: None
    pi_router.get_grn_repository = lambda: grn_repo
    pi_router.get_audit_repository = lambda: audit
    return TestClient(app)


# The vendor repo for purchase-invoices must return the seeded vendor.
class _VendorRepo:
    def __init__(self, db):
        self._db = db

    def find_by_id(self, vid):
        for v in self._db.collections["vendors"]:
            if v.get("vendor_id") == vid:
                return dict(v)
        return None


def _pi_client_full(db, grn_repo, audit=None, roles=("ACCOUNTANT",)):
    c = _pi_client(db, grn_repo, audit, roles)
    pi_router.get_vendor_repository = lambda: _VendorRepo(db)
    return c


def _workshop_client(db, wrepo, audit=None, order_exists=True, roles=("STORE_MANAGER",)):
    app = FastAPI()
    app.include_router(workshop_router.router, prefix="/api/v1/workshop")

    async def _u():
        return _user(roles)

    app.dependency_overrides[get_current_user] = _u
    workshop_router.get_db = lambda: db
    workshop_router.get_workshop_repository = lambda: wrepo

    class _OrderRepo:
        def find_by_id(self, _id):
            return {"order_id": _id} if order_exists else None

        def update(self, *a, **k):
            return True

    workshop_router.get_order_repository = lambda: _OrderRepo()
    workshop_router.get_audit_repository = lambda: audit
    return TestClient(app)


def _grn_item(product_id, qty):
    """A fully-accepted GRN/DC line (received == accepted, none rejected)."""
    return {
        "product_id": product_id,
        "product_name": f"Lens {product_id}",
        "received_qty": qty,
        "accepted_qty": qty,
        "rejected_qty": 0,
    }


def _log_dc(client, dc_number, items, vendor_id="V1", dc_date="2026-05-10", **over):
    body = {
        "grn_subtype": "DELIVERY_CHALLAN",
        "dc_number": dc_number,
        "dc_date": dc_date,
        "vendor_id": vendor_id,
        "items": items,
    }
    body.update(over)
    return client.post("/api/v1/vendors/grn", json=body)


# ===========================================================================
# 1. PURE ENGINE: dc_bulk_match  (MATCHED vs ON_HOLD_EXCEPTION at tolerance)
# ===========================================================================


class TestDcBulkMatchEngine:
    def test_exact_match_is_matched(self):
        dcs = [{"items": [_grn_item("P1", 50)]}, {"items": [_grn_item("P1", 30)]}]
        lines = [{"product_id": "P1", "qty": 80}]
        res = pmatch.dc_bulk_match(dcs, lines, tolerance_pct=5.0)
        assert res["match_status"] == "MATCHED"
        assert res["dc_qty_by_product"]["P1"] == 80

    def test_within_tolerance_is_matched(self):
        # 80 received, 83 billed -> 3.75% < 5% tolerance -> MATCHED.
        dcs = [{"items": [_grn_item("P1", 80)]}]
        lines = [{"product_id": "P1", "qty": 83}]
        assert pmatch.dc_bulk_match(dcs, lines, 5.0)["match_status"] == "MATCHED"

    def test_over_tolerance_is_on_hold(self):
        # 80 received, 100 billed -> 25% > 5% -> ON_HOLD_EXCEPTION.
        dcs = [{"items": [_grn_item("P1", 80)]}]
        lines = [{"product_id": "P1", "qty": 100}]
        res = pmatch.dc_bulk_match(dcs, lines, 5.0)
        assert res["match_status"] == "ON_HOLD_EXCEPTION"
        assert res["detail"][0]["verdict"] == "EXCEPTION"
        assert res["detail"][0]["dc_qty_total"] == 80
        assert res["detail"][0]["invoice_qty"] == 100

    def test_billed_product_with_no_dc_is_exception(self):
        dcs = [{"items": [_grn_item("P1", 10)]}]
        lines = [{"product_id": "P1", "qty": 10}, {"product_id": "P9", "qty": 5}]
        res = pmatch.dc_bulk_match(dcs, lines, 5.0)
        assert res["match_status"] == "ON_HOLD_EXCEPTION"

    def test_dc_received_but_not_billed_is_exception(self):
        dcs = [{"items": [_grn_item("P1", 10)]}, {"items": [_grn_item("P2", 4)]}]
        lines = [{"product_id": "P1", "qty": 10}]  # P2 not billed
        res = pmatch.dc_bulk_match(dcs, lines, 5.0)
        assert res["match_status"] == "ON_HOLD_EXCEPTION"

    def test_aggregation_across_dcs(self):
        dcs = [
            {"items": [_grn_item("P1", 50)]},
            {"items": [_grn_item("P1", 30), _grn_item("P2", 20)]},
        ]
        agg = pmatch._dc_accepted_by_product(dcs)
        assert agg == {"P1": 80, "P2": 20}


# ===========================================================================
# 2. DC LOGGING  (acceptance tests 1, 2, 12)
# ===========================================================================


class TestDcLogging:
    def test_dc_without_invoice_no_succeeds_but_standard_requires_it(self):
        db = _FakeDB()
        grn_repo = _GRNRepo(db)
        audit = _AuditRepo()
        c = _vendors_client(db, grn_repo, audit=audit)

        # DC with NO vendor_invoice_no -> 201 (it arrives later).
        r = _log_dc(c, "DC-001", [_grn_item("L1", 5)])
        assert r.status_code == 201, r.text
        assert r.json()["grn_subtype"] == "DELIVERY_CHALLAN"

        # A STANDARD GRN with no vendor_invoice_no -> 422 (still mandatory).
        r2 = c.post(
            "/api/v1/vendors/grn",
            json={"grn_subtype": "STANDARD", "items": [_grn_item("L1", 5)]},
        )
        assert r2.status_code == 422

    def test_dc_requires_dc_number_and_date(self):
        db = _FakeDB()
        c = _vendors_client(db, _GRNRepo(db))
        # Missing dc_number.
        r = c.post(
            "/api/v1/vendors/grn",
            json={
                "grn_subtype": "DELIVERY_CHALLAN",
                "dc_date": "2026-05-10",
                "items": [_grn_item("L1", 5)],
            },
        )
        assert r.status_code == 422

    def test_dc_number_uniqueness_per_store(self):
        db = _FakeDB()
        grn_repo = _GRNRepo(db)
        c = _vendors_client(db, grn_repo)
        assert _log_dc(c, "DC-DUP", [_grn_item("L1", 5)]).status_code == 201
        # Same (vendor, dc_number, store) -> 409.
        r = _log_dc(c, "DC-DUP", [_grn_item("L1", 5)])
        assert r.status_code == 409
        # A different dc_number -> 201.
        assert _log_dc(c, "DC-OTHER", [_grn_item("L1", 5)]).status_code == 201

    def test_legacy_grn_without_subtype_reads_as_standard(self):
        # Acceptance #12: an existing GRN with no grn_subtype + a PO + invoice
        # books as a STANDARD GRN (backward-compatible).
        db = _FakeDB()
        po = {
            "po_id": "PO1",
            "po_number": "PO-1",
            "vendor_id": "V1",
            "vendor_name": "Zeiss Lab",
            "status": "SENT",
            "items": [{"product_id": "L1", "quantity": 5, "unit_price": 100}],
        }
        c = _vendors_client(db, _GRNRepo(db), po=po)
        r = c.post(
            "/api/v1/vendors/grn",
            json={
                "po_id": "PO1",
                "vendor_invoice_no": "INV-1",
                "vendor_invoice_date": "2026-05-01",
                "items": [_grn_item("L1", 5)],
            },
        )
        assert r.status_code == 201, r.text
        assert r.json()["grn_subtype"] == "STANDARD"

    def test_dc_log_writes_audit_row(self):
        db = _FakeDB()
        audit = _AuditRepo()
        c = _vendors_client(db, _GRNRepo(db), audit=audit)
        _log_dc(c, "DC-AUD", [_grn_item("L1", 5)])
        assert any(r["action"] == "vendor.dc_log" for r in audit.rows)

    def test_dc_period_locked_blocks_log(self):
        db = _FakeDB()
        # Lock May 2026.
        db.collections["period_locks"].append({"month": 5, "year": 2026})
        c = _vendors_client(db, _GRNRepo(db))
        r = _log_dc(c, "DC-LOCK", [_grn_item("L1", 5)], dc_date="2026-05-10")
        assert r.status_code == 423


# ===========================================================================
# 3. WORKSHOP HARDLOCK  (acceptance tests 3, 4, 5, 11)
# ===========================================================================


def _job_body(lens_status, product_id="L1", **over):
    body = {
        "order_id": "O1",
        "frame_details": {},
        "lens_details": {"lens_status": lens_status, "product_id": product_id},
        "prescription_id": "RX1",
        "expected_date": "2026-06-01",
    }
    body.update(over)
    return body


class TestWorkshopHardlock:
    def test_external_lens_no_dc_is_blocked_inhouse_passes(self):
        db = _FakeDB()
        wrepo = _WorkshopRepo()
        c = _workshop_client(db, wrepo)

        # ORDERED lens, no DC -> 422 DC_HARDLOCK.
        r = c.post("/api/v1/workshop/jobs", json=_job_body("ORDERED"))
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "DC_HARDLOCK"

        # In-house lens (NOT ORDERED) -> 201 (exempt).
        r2 = c.post("/api/v1/workshop/jobs", json=_job_body("RECEIVED"))
        assert r2.status_code == 201, r2.text

    def test_external_lens_with_dc_passes(self):
        db = _FakeDB()
        # Seed an accepted DC covering L1 at store S1.
        db.collections["grns"].append(
            {
                "grn_id": "DC1",
                "grn_subtype": "DELIVERY_CHALLAN",
                "status": "ACCEPTED",
                "store_id": "S1",
                "items": [_grn_item("L1", 3)],
            }
        )
        c = _workshop_client(db, _WorkshopRepo())
        r = c.post("/api/v1/workshop/jobs", json=_job_body("ORDERED"))
        assert r.status_code == 201, r.text

    def test_hardlock_respects_cutover_date(self):
        db = _FakeDB()
        # Cutover in the future -> a job created today is BEFORE it -> exempt.
        db.collections["purchase_settings"].append(
            {"_id": "default", "dc_hardlock_from_date": "2099-01-01"}
        )
        c = _workshop_client(db, _WorkshopRepo())
        r = c.post("/api/v1/workshop/jobs", json=_job_body("ORDERED"))
        assert r.status_code == 201, r.text

    def test_admin_override_with_reason_audited_storemanager_forbidden(self):
        db = _FakeDB()
        audit = _AuditRepo()
        # STORE_MANAGER supplying an override reason -> 403 (not authorised).
        c_sm = _workshop_client(db, _WorkshopRepo(), audit=audit, roles=("STORE_MANAGER",))
        r_sm = c_sm.post(
            "/api/v1/workshop/jobs",
            json=_job_body("ORDERED", override_reason="DC in transit"),
        )
        assert r_sm.status_code == 403

        # ADMIN overriding with a reason -> 201 + audit row.
        c_adm = _workshop_client(db, _WorkshopRepo(), audit=audit, roles=("ADMIN",))
        r_adm = c_adm.post(
            "/api/v1/workshop/jobs",
            json=_job_body("ORDERED", override_reason="Emergency - DC in transit"),
        )
        assert r_adm.status_code == 201, r_adm.text
        assert r_adm.json()["dc_hardlock_override"] is True
        assert any(r["action"] == "dc_hardlock_override" for r in audit.rows)

    def test_require_flag_false_bypasses_hardlock(self):
        db = _FakeDB()
        db.collections["purchase_settings"].append(
            {"_id": "default", "require_dc_for_workshop": False}
        )
        c = _workshop_client(db, _WorkshopRepo())
        # Flag off -> ORDERED lens with no DC succeeds.
        r = c.post("/api/v1/workshop/jobs", json=_job_body("ORDERED"))
        assert r.status_code == 201, r.text

        # Restore flag to true -> blocked again.
        db.collections["purchase_settings"][0]["require_dc_for_workshop"] = True
        r2 = c.post("/api/v1/workshop/jobs", json=_job_body("ORDERED"))
        assert r2.status_code == 422


# ===========================================================================
# 4. DC -> BULK-INVOICE TALLY  (acceptance tests 7, 8, 9, 10)
# ===========================================================================


def _seed_accepted_dc(db, grn_id, items, dc_date="2026-05-10", vendor_id="V1"):
    db.collections["grns"].append(
        {
            "grn_id": grn_id,
            "grn_number": grn_id,
            "grn_subtype": "DELIVERY_CHALLAN",
            "status": "ACCEPTED",
            "store_id": "S1",
            "vendor_id": vendor_id,
            "dc_number": grn_id,
            "dc_date": dc_date,
            "dc_matched": False,
            "linked_bulk_invoice_id": None,
            "items": items,
        }
    )


def _invoice_body(lines, **over):
    body = {
        "vendor_id": "V1",
        "invoice_number": over.pop("invoice_number", "BULK-001"),
        "invoice_date": over.pop("invoice_date", "2026-06-01"),
        "recipient_entity_id": "E1",
        "lines": lines,
    }
    body.update(over)
    return body


class TestDcInvoiceTally:
    def test_from_dcs_aggregates_lines(self):
        # Acceptance #7: D1 (50 P1), D2 (30 P1, 20 P2) -> P1=80, P2=20.
        db = _FakeDB()
        _seed_accepted_dc(db, "D1", [_grn_item("P1", 50)])
        _seed_accepted_dc(db, "D2", [_grn_item("P1", 30), _grn_item("P2", 20)])
        c = _pi_client_full(db, _GRNRepo(db))
        r = c.get(
            "/api/v1/vendors/purchase-invoices/from-dcs",
            params={"dc_ids": "D1,D2", "vendor_id": "V1"},
        )
        assert r.status_code == 200, r.text
        by_pid = {ln["product_id"]: ln["qty"] for ln in r.json()["lines"]}
        assert by_pid.get("P1") == 80
        assert by_pid.get("P2") == 20
        assert set(r.json()["linked_dc_ids"]) == {"D1", "D2"}

    def test_matched_booking_stamps_dcs_and_blocks_relink(self):
        # Acceptance #8: exact-qty bulk invoice -> MATCHED; DCs stamped; a second
        # link of D1 -> 409.
        db = _FakeDB()
        audit = _AuditRepo()
        _seed_accepted_dc(db, "D1", [_grn_item("P1", 50)])
        _seed_accepted_dc(db, "D2", [_grn_item("P1", 30)])
        c = _pi_client_full(db, _GRNRepo(db), audit=audit)
        body = _invoice_body(
            [{"product_id": "P1", "qty": 80, "unit_price": 100, "gst_rate": 5}],
            linked_dc_ids=["D1", "D2"],
        )
        r = c.post("/api/v1/vendors/purchase-invoices", json=body)
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["dc_match_status"] == "MATCHED"
        # Both DCs flipped dc_matched=true.
        for dc_id in ("D1", "D2"):
            dc = _FakeCollection(db.collections["grns"]).find_one({"grn_id": dc_id})
            assert dc["dc_matched"] is True
            assert dc["linked_bulk_invoice_id"] == out["bill_id"]

        # Re-linking D1 to a new invoice -> 409 (already matched).
        body2 = _invoice_body(
            [{"product_id": "P1", "qty": 50, "unit_price": 100, "gst_rate": 5}],
            invoice_number="BULK-002",
            linked_dc_ids=["D1"],
        )
        r2 = c.post("/api/v1/vendors/purchase-invoices", json=body2)
        assert r2.status_code == 409

    def test_exception_booking_holds_but_records_liability(self):
        # Acceptance #9: billed 100 vs received 80 (25% > 5%) ->
        # ON_HOLD_EXCEPTION, but the bill IS persisted (AP liability recorded).
        db = _FakeDB()
        _seed_accepted_dc(db, "D1", [_grn_item("P1", 80)])
        c = _pi_client_full(db, _GRNRepo(db))
        body = _invoice_body(
            [{"product_id": "P1", "qty": 100, "unit_price": 100, "gst_rate": 5}],
            linked_dc_ids=["D1"],
        )
        r = c.post("/api/v1/vendors/purchase-invoices", json=body)
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["dc_match_status"] == "ON_HOLD_EXCEPTION"
        # The payable was still recorded.
        bill = _FakeCollection(db.collections["vendor_bills"]).find_one(
            {"bill_id": out["bill_id"]}
        )
        assert bill is not None
        assert bill["outstanding"] == out["total"]
        # The DC is still flipped matched (it's reconciled, just flagged).
        dc = _FakeCollection(db.collections["grns"]).find_one({"grn_id": "D1"})
        assert dc["dc_matched"] is True

    def test_bulk_invoice_period_lock_uses_earliest_dc_date(self):
        # Acceptance #10: DCs in a LOCKED month -> 423 even though invoice_date
        # is in the open month.
        db = _FakeDB()
        db.collections["period_locks"].append({"month": 4, "year": 2026})  # Apr lock
        _seed_accepted_dc(db, "D1", [_grn_item("P1", 10)], dc_date="2026-04-15")
        c = _pi_client_full(db, _GRNRepo(db))
        body = _invoice_body(
            [{"product_id": "P1", "qty": 10, "unit_price": 100, "gst_rate": 5}],
            invoice_date="2026-06-01",  # open month
            linked_dc_ids=["D1"],
        )
        r = c.post("/api/v1/vendors/purchase-invoices", json=body)
        assert r.status_code == 423

    def test_dc_match_detail_endpoint(self):
        db = _FakeDB()
        _seed_accepted_dc(db, "D1", [_grn_item("P1", 80)])
        c = _pi_client_full(db, _GRNRepo(db))
        body = _invoice_body(
            [{"product_id": "P1", "qty": 80, "unit_price": 100, "gst_rate": 5}],
            linked_dc_ids=["D1"],
        )
        out = c.post("/api/v1/vendors/purchase-invoices", json=body).json()
        r = c.get(
            f"/api/v1/vendors/purchase-invoices/{out['bill_id']}/dc-match"
        )
        assert r.status_code == 200, r.text
        assert r.json()["dc_match_status"] == "MATCHED"
        assert r.json()["linked_dc_ids"] == ["D1"]

    def test_non_dc_invoice_has_na_dc_match_status(self):
        # Acceptance #12: an invoice with no linked_dc_ids -> dc_match_status N_A.
        db = _FakeDB()
        c = _pi_client_full(db, _GRNRepo(db))
        body = _invoice_body(
            [{"product_id": "P1", "qty": 10, "unit_price": 100, "gst_rate": 5}]
        )
        r = c.post("/api/v1/vendors/purchase-invoices", json=body)
        assert r.status_code == 201, r.text
        assert r.json()["dc_match_status"] == "N_A"

    def test_link_unaccepted_dc_rejected(self):
        # A pending (not ACCEPTED) DC cannot be billed.
        db = _FakeDB()
        db.collections["grns"].append(
            {
                "grn_id": "DP",
                "grn_subtype": "DELIVERY_CHALLAN",
                "status": "PENDING",
                "store_id": "S1",
                "vendor_id": "V1",
                "dc_date": "2026-05-10",
                "dc_matched": False,
                "items": [_grn_item("P1", 10)],
            }
        )
        c = _pi_client_full(db, _GRNRepo(db))
        body = _invoice_body(
            [{"product_id": "P1", "qty": 10, "unit_price": 100, "gst_rate": 5}],
            linked_dc_ids=["DP"],
        )
        r = c.post("/api/v1/vendors/purchase-invoices", json=body)
        assert r.status_code == 400


# ===========================================================================
# 5. GRN LIST FILTERS (open-DC panel query)
# ===========================================================================


class TestGrnListFilters:
    def test_open_dc_filter(self):
        db = _FakeDB()
        _seed_accepted_dc(db, "D1", [_grn_item("P1", 10)])  # open
        # A matched DC should be excluded by dc_matched=false.
        db.collections["grns"].append(
            {
                "grn_id": "D2",
                "grn_subtype": "DELIVERY_CHALLAN",
                "status": "ACCEPTED",
                "store_id": "S1",
                "vendor_id": "V1",
                "dc_date": "2026-05-11",
                "dc_matched": True,
                "items": [_grn_item("P1", 5)],
            }
        )
        c = _vendors_client(db, _GRNRepo(db), roles=("ACCOUNTANT",))
        r = c.get(
            "/api/v1/vendors/grn",
            params={
                "grn_subtype": "DELIVERY_CHALLAN",
                "dc_matched": "false",
                "vendor_id": "V1",
                "status": "ACCEPTED",
            },
        )
        assert r.status_code == 200, r.text
        ids = [g["grn_id"] for g in r.json()["grns"]]
        assert "D1" in ids and "D2" not in ids
