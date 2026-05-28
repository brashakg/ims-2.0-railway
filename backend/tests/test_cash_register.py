"""
Cash register / EOD reconciliation tests.

Three layers:
  1. Pure money math (services/cash_register.py): denomination sum, expected
     cash, variance, tolerance status, close summary. No DB, no async.
  2. Endpoint behaviour against an in-memory fake collection: open/close,
     expected-cash math from POS CASH tenders + cash expenses, variance,
     double-open guard, store-scope (validate_store_access 403).
  3. A real-Mongo round trip (open -> close -> sessions) that skips fail-soft
     when Mongo is unreachable on a laptop and runs in CI's mongo:7.0 service.

Expected cash contract:
    expected = opening_float + cash_sales - cash_refunds
             - cash_expenses - bank_deposit
    variance = counted - expected
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.services import cash_register as cr  # noqa: E402
from api.routers import finance  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402

# CI's asyncio_mode=auto runs coroutine tests; the endpoint tests here use the
# synchronous TestClient, but we mark the module so the convention is explicit.
pytestmark = pytest.mark.asyncio


# ===========================================================================
# 1. Pure money math
# ===========================================================================


class TestDenominationMath:
    def test_ladder_has_notes_and_coins_no_2000(self):
        rows = cr.denomination_ladder()
        faces_notes = [r["face"] for r in rows if r["kind"] == "note"]
        faces_coins = [r["face"] for r in rows if r["kind"] == "coin"]
        assert faces_notes == [500, 200, 100, 50, 20, 10]
        assert faces_coins == [10, 5, 2, 1]
        assert 2000 not in faces_notes  # RBI withdrew it
        assert all(r["pieces"] == 0 for r in rows)

    def test_total_from_denominations(self):
        rows = [
            {"face": 500, "pieces": 5, "kind": "note"},  # 2500
            {"face": 100, "pieces": 12, "kind": "note"},  # 1200
            {"face": 10, "pieces": 4, "kind": "coin"},  # 40
            {"face": 1, "pieces": 10, "kind": "coin"},  # 10
        ]
        assert cr.total_from_denominations(rows) == 3750.0

    def test_normalize_drops_bad_rows_and_clamps_pieces(self):
        rows = [
            {"face": 500, "pieces": 2},  # ok -> note default
            {"face": "x", "pieces": 5},  # bad face -> dropped
            {"face": 50, "pieces": -3},  # negative -> 0
            {"face": 5, "pieces": 4, "kind": "coin"},
            "junk",  # not a dict -> skipped
        ]
        out = cr.normalize_denominations(rows)
        assert len(out) == 3
        assert out[0] == {"face": 500, "kind": "note", "pieces": 2, "line_total": 1000}
        assert out[1]["pieces"] == 0 and out[1]["line_total"] == 0
        assert out[2]["kind"] == "coin"

    def test_normalize_handles_none(self):
        assert cr.normalize_denominations(None) == []
        assert cr.total_from_denominations(None) == 0.0


class TestExpectedCashAndVariance:
    def test_expected_cash_full_formula(self):
        # opening 2000 + sales 6420 - refunds 0 - expenses 500 - deposit 5000
        exp = cr.compute_expected_cash(2000, 6420, 0, 500, 5000)
        assert exp == 2920.0

    def test_expected_cash_coerces_junk_to_zero(self):
        assert cr.compute_expected_cash(1000, None, "x", None, None) == 1000.0

    def test_variance_over_short_balanced(self):
        assert cr.compute_variance(3000, 2920) == 80.0  # over
        assert cr.compute_variance(2900, 2920) == -20.0  # short
        assert cr.compute_variance(2920, 2920) == 0.0

    def test_variance_status_tolerance_band(self):
        # within +/-200 tolerance -> balanced
        assert cr.variance_status(120, tolerance=200) == "BALANCED"
        assert cr.variance_status(-200, tolerance=200) == "BALANCED"
        assert cr.variance_status(250, tolerance=200) == "OVER"
        assert cr.variance_status(-250, tolerance=200) == "SHORT"
        assert cr.variance_status(0, tolerance=0) == "BALANCED"

    def test_build_close_summary(self):
        denoms = [
            {"face": 500, "pieces": 5, "kind": "note"},  # 2500
            {"face": 100, "pieces": 4, "kind": "note"},  # 400
            {"face": 10, "pieces": 2, "kind": "coin"},  # 20
        ]  # counted = 2920
        s = cr.build_close_summary(
            opening_float=2000,
            cash_sales=6420,
            cash_refunds=0,
            cash_expenses=500,
            bank_deposit=5000,
            denominations=denoms,
            tolerance=50,
        )
        assert s["counted"] == 2920.0
        assert s["expected"] == 2920.0
        assert s["variance"] == 0.0
        assert s["variance_status"] == "BALANCED"


# ===========================================================================
# In-memory fake Mongo collection (just enough surface for the router)
# ===========================================================================


class _FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def find_one(self, flt, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in flt.items()):
                return dict(d)
        return None

    def insert_one(self, doc):
        self.docs.append(dict(doc))

        class _R:
            inserted_id = "x"

        return _R()

    def update_one(self, flt, upd):
        for d in self.docs:
            if all(d.get(k) == v for k, v in flt.items()):
                if "$set" in upd:
                    d.update(upd["$set"])

                class _R:
                    matched_count = 1
                    modified_count = 1

                return _R()

        class _R0:
            matched_count = 0
            modified_count = 0

        return _R0()

    # --- query surface for orders / expenses (find with sort/limit chain) ---
    def find(self, flt, projection=None):
        def _match(d):
            for k, v in flt.items():
                dv = d.get(k)
                if isinstance(v, dict):
                    for op, ov in v.items():
                        if op == "$gte" and not (dv is not None and dv >= ov):
                            return False
                        if op == "$lte" and not (dv is not None and dv <= ov):
                            return False
                        if op == "$in" and dv not in ov:
                            return False
                elif dv != v:
                    return False
            return True

        return _Cursor([dict(d) for d in self.docs if _match(d)])


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

    def get_collection(self, name):
        return self.collections.setdefault(name, _FakeCollection())


def _client(db, roles=None, active_store="store-001", store_ids=None):
    """Fresh app with just the finance router; override user + DB."""
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
    finance._get_db = lambda: db  # monkeypatch module-level DB accessor
    return TestClient(app)


# ===========================================================================
# 2. Endpoint behaviour
# ===========================================================================


class TestCashRegisterEndpoints:
    def test_open_then_close_with_zero_variance(self):
        db = _FakeDB()
        c = _client(db)

        # Open with Rs 2000 float.
        r = c.post(
            "/finance/cash-register/open",
            json={
                "store_id": "store-001",
                "shift": "PM",
                "denominations": [{"face": 500, "pieces": 4, "kind": "note"}],
            },
        )
        assert r.status_code == 200, r.text
        sess = r.json()
        assert sess["opening_float"] == 2000.0
        assert sess["status"] == "OPEN"
        sid = sess["session_id"]

        # Seed activity INSIDE the window: anchor the order's created_at to the
        # session's opened_at so it falls between open and close regardless of
        # wall-clock skew. Expense dated on the open day.
        opened_at = sess["opened_at"]
        db.get_collection("orders").docs.append(
            {
                "order_id": "o1",
                "store_id": "store-001",
                "created_at": opened_at,
                "payments": [{"method": "CASH", "amount": 6420}],
            }
        )
        db.get_collection("expenses").docs.append(
            {
                "expense_id": "e1",
                "store_id": "store-001",
                "amount": 500,
                "payment_mode": "CASH",
                "status": "APPROVED",
                "expense_date": opened_at[:10],
            }
        )

        # Close: counted = opening 2000 + cash_sales 6420 - expenses 500
        #        - deposit 5000 = expected 2920. Count exactly 2920.
        r2 = c.post(
            "/finance/cash-register/close",
            json={
                "session_id": sid,
                "bank_deposit": 5000,
                "denominations": [
                    {"face": 500, "pieces": 5, "kind": "note"},  # 2500
                    {"face": 100, "pieces": 4, "kind": "note"},  # 400
                    {"face": 10, "pieces": 2, "kind": "coin"},  # 20
                ],
                "tolerance": 100,
            },
        )
        assert r2.status_code == 200, r2.text
        closed = r2.json()
        assert closed["status"] == "CLOSED"
        assert closed["cash_sales"] == 6420.0
        assert closed["cash_expenses"] == 500.0
        assert closed["expected"] == 2920.0
        assert closed["counted"] == 2920.0
        assert closed["variance"] == 0.0
        assert closed["variance_status"] == "BALANCED"

    def test_close_detects_short_and_over(self):
        db = _FakeDB()
        c = _client(db)
        # No sales/expenses -> expected == opening float == 1000.
        sid = c.post(
            "/finance/cash-register/open",
            json={"store_id": "store-001", "opening_float": 1000, "denominations": []},
        ).json()["session_id"]

        r = c.post(
            "/finance/cash-register/close",
            json={
                "session_id": sid,
                "counted_override": 850,  # 150 short
                "denominations": [],
                "tolerance": 100,
            },
        )
        body = r.json()
        assert body["expected"] == 1000.0
        assert body["variance"] == -150.0
        assert body["variance_status"] == "SHORT"

    def test_double_open_blocked(self):
        db = _FakeDB()
        c = _client(db)
        c.post(
            "/finance/cash-register/open",
            json={"store_id": "store-001", "opening_float": 500, "denominations": []},
        )
        r = c.post(
            "/finance/cash-register/open",
            json={"store_id": "store-001", "opening_float": 500, "denominations": []},
        )
        assert r.status_code == 409

    def test_store_scope_denied_for_other_store(self):
        # A store manager whose store_ids is ['store-001'] cannot open a
        # session for 'store-999'.
        db = _FakeDB()
        c = _client(db, roles=["STORE_MANAGER"], store_ids=["store-001"])
        r = c.post(
            "/finance/cash-register/open",
            json={"store_id": "store-999", "opening_float": 500, "denominations": []},
        )
        assert r.status_code == 403

    def test_admin_can_open_any_store(self):
        db = _FakeDB()
        c = _client(db, roles=["ADMIN"], store_ids=[])
        r = c.post(
            "/finance/cash-register/open",
            json={"store_id": "store-999", "opening_float": 500, "denominations": []},
        )
        assert r.status_code == 200, r.text
        assert r.json()["store_id"] == "store-999"

    def test_sessions_history_scoped_and_preview(self):
        db = _FakeDB()
        c = _client(db)
        opened = c.post(
            "/finance/cash-register/open",
            json={"store_id": "store-001", "opening_float": 1000, "denominations": []},
        ).json()
        db.get_collection("orders").docs.append(
            {
                "order_id": "o1",
                "store_id": "store-001",
                "created_at": opened["opened_at"],
                "payments": [{"method": "CASH", "amount": 300}],
            }
        )
        r = c.get("/finance/cash-register/sessions", params={"store_id": "store-001"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert len(data["sessions"]) == 1
        assert data["open_session"] is not None
        # running expected = 1000 + 300 = 1300
        assert data["expected_preview"]["expected"] == 1300.0

    def test_close_missing_session_404(self):
        db = _FakeDB()
        c = _client(db)
        r = c.post(
            "/finance/cash-register/close",
            json={"session_id": "nope", "denominations": []},
        )
        assert r.status_code == 404


# ===========================================================================
# 3. Real-Mongo round trip (skips fail-soft when Mongo is unreachable)
# ===========================================================================


@pytest.fixture(scope="module")
def mongo_db():
    try:
        from pymongo import MongoClient
        from pymongo.errors import ServerSelectionTimeoutError
    except ImportError:
        pytest.skip("pymongo unavailable")
        return None

    uri = (
        os.getenv("MONGODB_URL")
        or os.getenv("MONGODB_URI")
        or "mongodb://localhost:27017"
    )
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.server_info()
    except (ServerSelectionTimeoutError, Exception):  # noqa: BLE001
        pytest.skip(f"Mongo unavailable at {uri}; skipping integration test")
        return None

    db_name = f"ims_test_cashreg_{uuid.uuid4().hex[:8]}"
    db = client[db_name]
    try:
        yield db
    finally:
        try:
            client.drop_database(db_name)
        except Exception:  # noqa: BLE001
            pass
        client.close()


class _DBShim:
    """Adapts a raw pymongo Database to the .get_collection() the router calls."""

    def __init__(self, db):
        self._db = db

    def get_collection(self, name):
        return self._db[name]


@pytest.mark.integration
def test_cash_register_roundtrip_real_mongo(mongo_db):
    """open -> close writes/reads through real Mongo; expected cash reflects a
    real CASH order persisted to the orders collection."""
    db = _DBShim(mongo_db)
    now = datetime.utcnow()
    mongo_db["orders"].insert_one(
        {
            "order_id": "ord-cr-1",
            "store_id": "store-rt",
            "created_at": (now + timedelta(minutes=1)).isoformat(),
            "payments": [{"method": "CASH", "amount": 1500}],
        }
    )
    c = _client(db, roles=["ADMIN"], active_store="store-rt", store_ids=[])

    opened = c.post(
        "/finance/cash-register/open",
        json={"store_id": "store-rt", "opening_float": 1000, "denominations": []},
    ).json()
    sid = opened["session_id"]

    # expected = 1000 + 1500 = 2500. Count 2500 -> balanced.
    closed = c.post(
        "/finance/cash-register/close",
        json={
            "session_id": sid,
            "counted_override": 2500,
            "denominations": [{"face": 500, "pieces": 5, "kind": "note"}],
        },
    ).json()
    assert closed["expected"] == 2500.0
    assert closed["variance"] == 0.0
    assert closed["status"] == "CLOSED"

    # Persisted + readable via the history endpoint.
    hist = c.get(
        "/finance/cash-register/sessions", params={"store_id": "store-rt"}
    ).json()
    ids = [s["session_id"] for s in hist["sessions"]]
    assert sid in ids
