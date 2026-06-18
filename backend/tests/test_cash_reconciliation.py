"""
Cash-register vs blind-EOD reconciliation console tests (#7).

The endpoint GET /finance/cash-reconciliation-summary is a manager-facing,
READ-ONLY view that normalises two day-close flows into one row shape:
  - CASH_REGISTER : cash_register_sessions, status CLOSED  (rupees)
  - BLIND_EOD     : till_sessions,         status LOCKED   (paisa -> rupees)

What is asserted here:
  1. _recon_status() classifies BALANCED / OVERAGE / SHORTAGE against the band.
  2. A balanced + a shortage CLOSED cash-register session normalise to the right
     expected / counted / variance / variance_status, with correct totals.
  3. A blind-EOD (paisa) LOCKED session converts to rupees and trusts its stored
     status; variance flows through.
  4. Store-scoping: a STORE_MANAGER never sees another store's sessions; an
     explicit cross-store store_id is 403'd; an ADMIN sees all stores.
  5. The sign-off marker (cash_recon_signoffs) surfaces per row.

Uses an in-memory fake DB + FastAPI TestClient, mirroring test_cash_register.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import finance  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _restore_finance_get_db():
    """`_client()` rebinds finance._get_db to a fake; restore the real accessor
    after every test so it can't leak into later test modules (same contract as
    test_cash_register's fixture)."""
    original = finance._get_db
    try:
        yield
    finally:
        finance._get_db = original


# ===========================================================================
# In-memory fake DB (matches the shape used in test_cash_register)
# ===========================================================================


class _FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def find_one(self, flt, projection=None):
        for d in self.docs:
            if self._match(d, flt):
                return dict(d)
        return None

    def update_one(self, flt, upd, upsert=False):
        for d in self.docs:
            if self._match(d, flt):
                if "$set" in upd:
                    d.update(upd["$set"])

                class _R:
                    matched_count = 1
                    modified_count = 1
                    upserted_id = None

                return _R()
        if upsert:
            doc = dict(flt)
            if "$set" in upd:
                doc.update(upd["$set"])
            self.docs.append(doc)

            class _RU:
                matched_count = 0
                modified_count = 0
                upserted_id = "new"

            return _RU()

        class _R0:
            matched_count = 0
            modified_count = 0
            upserted_id = None

        return _R0()

    def find(self, flt, projection=None):
        return _Cursor([dict(d) for d in self.docs if self._match(d, flt)])

    @staticmethod
    def _match(d, f):
        for k, v in f.items():
            if k == "$or":
                if not any(_FakeCollection._match(d, sub) for sub in v):
                    return False
                continue
            dv = d.get(k)
            if isinstance(v, dict):
                for op, ov in v.items():
                    if op == "$in" and dv not in ov:
                        return False
                    if op == "$gte" and not (dv is not None and dv >= ov):
                        return False
                    if op == "$lte" and not (dv is not None and dv <= ov):
                        return False
                    if op == "$ne" and dv == ov:
                        return False
            elif dv != v:
                return False
        return True


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def __iter__(self):
        return iter(self.rows)


class _FakeDB:
    def __init__(self):
        self.collections = {}

    def seed(self, name, docs):
        self.collections[name] = _FakeCollection(docs)
        return self

    def get_collection(self, name):
        return self.collections.setdefault(name, _FakeCollection())


def _client(db, roles=None, active_store="store-001", store_ids=None):
    app = FastAPI()
    app.include_router(finance.router, prefix="/finance")

    async def _fake_user():
        return {
            "user_id": "u1",
            "name": "Sonia K.",
            "active_store_id": active_store,
            "store_ids": store_ids if store_ids is not None else [active_store],
            "roles": roles or ["STORE_MANAGER"],
        }

    app.dependency_overrides[get_current_user] = _fake_user
    finance._get_db = lambda: db
    return TestClient(app)


# ---------------------------------------------------------------------------
# Sample sessions
# ---------------------------------------------------------------------------


def _cr_session(session_id, store_id, day, *, opening, sales, expected, counted, tol=0.0):
    """A CLOSED close-by-denomination session (rupees)."""
    return {
        "session_id": session_id,
        "store_id": store_id,
        "status": "CLOSED",
        "shift": "PM",
        "opening_float": opening,
        "cash_sales": sales,
        "cash_refunds": 0.0,
        "cash_expenses": 0.0,
        "bank_deposit": 0.0,
        "expected": expected,
        "counted": counted,
        "tolerance": tol,
        "by_mode_breakdown": {"CASH": {"net": sales, "count": 4}},
        "closed_by": "cashier-7",
        "closed_by_name": "Ramesh",
        "closed_at": f"{day}T18:30:00",
        "opened_at": f"{day}T09:00:00",
    }


def _till_session(session_id, store_id, day, *, opening_p, sales_p, expected_p, counted_p, status):
    """A LOCKED blind-EOD session (paisa)."""
    return {
        "session_id": session_id,
        "store_id": store_id,
        "status": "LOCKED",
        "session_date": day,
        "shift": "FULL",
        "opening_float_paisa": opening_p,
        "cash_sales_paisa": sales_p,
        "cash_payouts_paisa": 0,
        "expected_cash_paisa": expected_p,
        "blind_count_paisa": counted_p,
        "tolerance_paisa": 0,
        "variance_status": status,
        "by_mode": {"CASH": {"net": round(sales_p / 100.0, 2), "count": 3}},
        "locked_by": "mgr-2",
        "locked_by_name": "Priya",
        "locked_at": f"{day}T20:00:00",
        "zread_number": "Z-0007",
    }


# ===========================================================================
# 1. _recon_status pure classification
# ===========================================================================


class TestReconStatus:
    def test_balanced_within_epsilon(self):
        assert finance._recon_status(0.0) == "BALANCED"
        assert finance._recon_status(0.004) == "BALANCED"  # < epsilon

    def test_overage_and_shortage(self):
        assert finance._recon_status(120.0) == "OVERAGE"
        assert finance._recon_status(-80.0) == "SHORTAGE"

    def test_tolerance_band_widens_balanced(self):
        # Within an explicit tolerance band -> balanced even though non-zero.
        assert finance._recon_status(150.0, tolerance=200.0) == "BALANCED"
        assert finance._recon_status(250.0, tolerance=200.0) == "OVERAGE"

    def test_garbage_is_balanced(self):
        assert finance._recon_status(None) == "BALANCED"


# ===========================================================================
# 2. Cash-register CLOSED sessions: balanced + shortage
# ===========================================================================


class TestCashRegisterSummary:
    def _db(self):
        db = _FakeDB()
        db.seed(
            "cash_register_sessions",
            [
                # Balanced: counted == expected.
                _cr_session(
                    "CR-1", "store-001", "2026-06-10",
                    opening=2000, sales=8000, expected=10000, counted=10000,
                ),
                # Shortage: counted 200 short.
                _cr_session(
                    "CR-2", "store-001", "2026-06-12",
                    opening=2000, sales=5000, expected=7000, counted=6800,
                ),
            ],
        )
        db.seed("stores", [{"store_id": "store-001", "store_name": "BV Bokaro"}])
        return db

    def test_balanced_and_shortage_rows(self):
        db = self._db()
        c = _client(db, roles=["ADMIN"], store_ids=[])
        r = c.get("/finance/cash-reconciliation-summary",
                  params={"from": "2026-06-01", "to": "2026-06-30"})
        assert r.status_code == 200, r.text
        body = r.json()
        rows = {row["session_id"]: row for row in body["rows"]}
        assert set(rows) == {"CR-1", "CR-2"}

        bal = rows["CR-1"]
        assert bal["expected_cash"] == 10000
        assert bal["counted_cash"] == 10000
        assert bal["variance"] == 0
        assert bal["variance_status"] == "BALANCED"
        assert bal["store_name"] == "BV Bokaro"  # name, not raw id
        assert bal["closed_by_name"] == "Ramesh"
        assert bal["source"] == "CASH_REGISTER"
        assert bal["blind"] is False

        short = rows["CR-2"]
        assert short["expected_cash"] == 7000
        assert short["counted_cash"] == 6800
        assert short["variance"] == -200
        assert short["variance_status"] == "SHORTAGE"

    def test_totals_aggregate(self):
        db = self._db()
        c = _client(db, roles=["ADMIN"], store_ids=[])
        body = c.get(
            "/finance/cash-reconciliation-summary",
            params={"from": "2026-06-01", "to": "2026-06-30"},
        ).json()
        t = body["totals"]
        assert t["sessions"] == 2
        assert t["balanced"] == 1
        assert t["shortage"] == 1
        assert t["overage"] == 0
        assert t["expected_cash"] == 17000
        assert t["counted_cash"] == 16800
        assert t["variance"] == -200
        assert t["shortage_amount"] == 200

    def test_date_range_excludes_out_of_window(self):
        db = self._db()
        c = _client(db, roles=["ADMIN"], store_ids=[])
        body = c.get(
            "/finance/cash-reconciliation-summary",
            params={"from": "2026-06-11", "to": "2026-06-30"},
        ).json()
        ids = {row["session_id"] for row in body["rows"]}
        assert ids == {"CR-2"}  # CR-1 on 06-10 is before the window


# ===========================================================================
# 3. Blind-EOD (paisa) LOCKED sessions
# ===========================================================================


class TestBlindEodSummary:
    def test_paisa_to_rupees_and_overage(self):
        db = _FakeDB()
        # expected 95000 paisa, counted 96500 paisa -> +15.00 overage.
        db.seed(
            "till_sessions",
            [
                _till_session(
                    "TILL-1", "store-001", "2026-06-15",
                    opening_p=200000, sales_p=750000,
                    expected_p=950000, counted_p=965000, status="OVERAGE",
                )
            ],
        )
        db.seed("stores", [{"store_id": "store-001", "store_name": "BV Bokaro"}])
        c = _client(db, roles=["ADMIN"], store_ids=[])
        body = c.get(
            "/finance/cash-reconciliation-summary",
            params={"from": "2026-06-01", "to": "2026-06-30"},
        ).json()
        assert len(body["rows"]) == 1
        row = body["rows"][0]
        assert row["source"] == "BLIND_EOD"
        assert row["blind"] is True
        assert row["opening_float"] == 2000.0
        assert row["expected_cash"] == 9500.0
        assert row["counted_cash"] == 9650.0
        assert row["variance"] == 150.0
        assert row["variance_status"] == "OVERAGE"
        assert row["zread_number"] == "Z-0007"
        assert row["closed_by_name"] == "Priya"


# ===========================================================================
# 4. Store-scoping
# ===========================================================================


class TestStoreScope:
    def _multi_store_db(self):
        db = _FakeDB()
        db.seed(
            "cash_register_sessions",
            [
                _cr_session("CR-A", "store-001", "2026-06-10",
                            opening=1000, sales=4000, expected=5000, counted=5000),
                _cr_session("CR-B", "store-002", "2026-06-10",
                            opening=1000, sales=4000, expected=5000, counted=4500),
            ],
        )
        db.seed("stores", [
            {"store_id": "store-001", "store_name": "BV Bokaro"},
            {"store_id": "store-002", "store_name": "BV Ranchi"},
        ])
        return db

    def test_store_manager_sees_only_own_store(self):
        db = self._multi_store_db()
        c = _client(db, roles=["STORE_MANAGER"], active_store="store-001",
                    store_ids=["store-001"])
        body = c.get(
            "/finance/cash-reconciliation-summary",
            params={"from": "2026-06-01", "to": "2026-06-30"},
        ).json()
        ids = {row["session_id"] for row in body["rows"]}
        assert ids == {"CR-A"}  # store-002's CR-B is invisible
        assert body["store_id"] == "store-001"

    def test_store_manager_cross_store_is_403(self):
        db = self._multi_store_db()
        c = _client(db, roles=["STORE_MANAGER"], active_store="store-001",
                    store_ids=["store-001"])
        r = c.get(
            "/finance/cash-reconciliation-summary",
            params={"store_id": "store-002"},
        )
        assert r.status_code == 403

    def test_admin_sees_all_stores(self):
        db = self._multi_store_db()
        c = _client(db, roles=["ADMIN"], store_ids=[])
        body = c.get(
            "/finance/cash-reconciliation-summary",
            params={"from": "2026-06-01", "to": "2026-06-30"},
        ).json()
        ids = {row["session_id"] for row in body["rows"]}
        assert ids == {"CR-A", "CR-B"}
        assert body["store_id"] is None

    def test_unauthorised_role_403(self):
        db = self._multi_store_db()
        c = _client(db, roles=["SALES_STAFF"], store_ids=["store-001"])
        r = c.get("/finance/cash-reconciliation-summary")
        assert r.status_code == 403


# ===========================================================================
# 5. Sign-off marker surfaces per row
# ===========================================================================


class TestSignoffSurfacing:
    def test_reviewed_marker_attached(self):
        db = _FakeDB()
        db.seed(
            "cash_register_sessions",
            [_cr_session("CR-S", "store-001", "2026-06-10",
                         opening=1000, sales=4000, expected=5000, counted=5000)],
        )
        db.seed("stores", [{"store_id": "store-001", "store_name": "BV Bokaro"}])
        db.seed("cash_recon_signoffs", [
            {
                "session_id": "CR-S",
                "reviewed": True,
                "reviewed_by": "u1",
                "reviewed_by_name": "Sonia K.",
                "reviewed_at": "2026-06-13T10:00:00",
                "note": "checked",
            }
        ])
        c = _client(db, roles=["ADMIN"], store_ids=[])
        body = c.get(
            "/finance/cash-reconciliation-summary",
            params={"from": "2026-06-01", "to": "2026-06-30"},
        ).json()
        row = body["rows"][0]
        assert row["signoff"]["reviewed"] is True
        assert row["signoff"]["reviewed_by_name"] == "Sonia K."

    def test_unreviewed_default_marker(self):
        db = _FakeDB()
        db.seed(
            "cash_register_sessions",
            [_cr_session("CR-N", "store-001", "2026-06-10",
                         opening=1000, sales=4000, expected=5000, counted=5000)],
        )
        db.seed("stores", [{"store_id": "store-001", "store_name": "BV Bokaro"}])
        c = _client(db, roles=["ADMIN"], store_ids=[])
        body = c.get(
            "/finance/cash-reconciliation-summary",
            params={"from": "2026-06-01", "to": "2026-06-30"},
        ).json()
        assert body["rows"][0]["signoff"] == {"reviewed": False}
