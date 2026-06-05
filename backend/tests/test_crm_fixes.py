"""
IMS 2.0 -- CRM fixes: CRM-1 through CRM-5
==========================================
CRM-1  referral reward routes loyalty points through the loyalty ledger
       (not the phantom loyalty_points field on the customers doc).
CRM-2  contact-lens auto-refill status endpoint.
CRM-3  churn-risk list uses real recency-based bands for medium/low
       (was a stub returning only high via a phantom field).
CRM-4  NPS-detractor follow-up writes correct field names
       (scheduled_date, notes, customer_phone -- not due_date/reason).
CRM-5  return-risk endpoint aggregates return count / rate as an advisory signal.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import crm as crm_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

class _Cursor(list):
    def sort(self, *_a, **_kw):
        return self
    def limit(self, _n):
        return self
    def skip(self, _n):
        return self


class _FakeColl:
    def __init__(self, docs=None):
        self._docs = list(docs or [])

    def find(self, flt=None, projection=None):
        return _Cursor(self._filter(flt))

    def find_one(self, flt=None, projection=None):
        matches = self._filter(flt)
        return dict(matches[0]) if matches else None

    def count_documents(self, flt=None):
        return len(self._filter(flt))

    def aggregate(self, pipeline):
        # Minimal: only supports a $match + $group pipeline over customer_id.
        # Enough to test _identify_churn_risk_customers.
        docs = list(self._docs)
        # Apply $match if present
        for stage in pipeline:
            if "$match" in stage:
                flt = stage["$match"]
                docs = self._apply_filter(docs, flt)
            elif "$group" in stage:
                group = stage["$group"]
                gid_expr = group.get("_id")
                by_key: dict = {}
                for d in docs:
                    # Only supports a simple field reference like "$customer_id"
                    if isinstance(gid_expr, str) and gid_expr.startswith("$"):
                        key = d.get(gid_expr[1:])
                    else:
                        key = None
                    bucket = by_key.setdefault(key, {
                        "_id": key, "last": None, "count": 0
                    })
                    bucket["count"] += 1
                    val = d.get("created_at")
                    if val is not None:
                        if bucket["last"] is None or val > bucket["last"]:
                            bucket["last"] = val
                docs = list(by_key.values())
        return iter(docs)

    def _filter(self, flt):
        return self._apply_filter(self._docs, flt)

    def _apply_filter(self, docs, flt):
        if not flt:
            return list(docs)
        result = []
        for d in docs:
            if self._matches(d, flt):
                result.append(dict(d))
        return result

    def _matches(self, doc, flt):
        for k, v in (flt or {}).items():
            if k == "$and":
                if not all(self._matches(doc, sub) for sub in v):
                    return False
            elif k == "$or":
                if not any(self._matches(doc, sub) for sub in v):
                    return False
            elif isinstance(v, dict):
                dv = doc.get(k)
                if "$nin" in v and dv in v["$nin"]:
                    return False
                if "$in" in v and dv not in v["$in"]:
                    return False
                if "$ne" in v and dv == v["$ne"]:
                    return False
            else:
                if doc.get(k) != v:
                    return False
        return True


class _FakeDB:
    is_connected = True

    def __init__(self, colls=None):
        self._colls = colls or {}

    def get_collection(self, name):
        return self._colls.get(name, _FakeColl())


# ---------------------------------------------------------------------------
# CRM-3: churn-risk bands use real recency
# ---------------------------------------------------------------------------

class _TestDBForChurn:
    """Wraps orders in a get_collection-compatible object."""

    def __init__(self, orders):
        self._orders = orders

    def get_collection(self, name):
        return _FakeColl(self._orders if name == "orders" else [])


def test_churn_high_band(monkeypatch):
    """Customer with last purchase >180 days ago => HIGH."""
    now = datetime.utcnow()
    orders = [
        {"customer_id": "c1", "grand_total": 5000,
         "created_at": now - timedelta(days=200),
         "status": "COMPLETED"},
    ]
    customers = [{"customer_id": "c1", "name": "Alice"}]

    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: _TestDBForChurn(orders))
    result = crm_mod._identify_churn_risk_customers(customers, "high")
    assert len(result) == 1
    assert result[0]["customer_id"] == "c1"
    assert result[0]["churn_risk_level"] == "high"
    assert result[0]["days_since_last_purchase"] >= 180


def test_churn_medium_band(monkeypatch):
    """Customer with last purchase 91-179 days ago => MEDIUM."""
    now = datetime.utcnow()
    orders = [
        {"customer_id": "c2", "grand_total": 3000,
         "created_at": now - timedelta(days=120),
         "status": "COMPLETED"},
    ]
    customers = [{"customer_id": "c2"}]

    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: _TestDBForChurn(orders))
    result = crm_mod._identify_churn_risk_customers(customers, "medium")
    assert len(result) == 1
    assert result[0]["churn_risk_level"] == "medium"
    days = result[0]["days_since_last_purchase"]
    assert 91 <= days <= 179


def test_churn_low_band(monkeypatch):
    """Customer with last purchase 31-90 days ago => LOW."""
    now = datetime.utcnow()
    orders = [
        {"customer_id": "c3", "grand_total": 2000,
         "created_at": now - timedelta(days=60),
         "status": "COMPLETED"},
    ]
    customers = [{"customer_id": "c3"}]

    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: _TestDBForChurn(orders))
    result = crm_mod._identify_churn_risk_customers(customers, "low")
    assert len(result) == 1
    assert result[0]["churn_risk_level"] == "low"


def test_churn_no_db_returns_empty(monkeypatch):
    """Fail-soft: no DB -> empty list."""
    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: None)
    result = crm_mod._identify_churn_risk_customers(
        [{"customer_id": "c1"}], "high"
    )
    assert result == []


def test_churn_active_recent_customer_not_in_high(monkeypatch):
    """Customer with purchase 10 days ago should NOT appear in high-risk band."""
    now = datetime.utcnow()
    orders = [
        {"customer_id": "c4", "grand_total": 1000,
         "created_at": now - timedelta(days=10),
         "status": "COMPLETED"},
    ]
    customers = [{"customer_id": "c4"}]
    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: _TestDBForChurn(orders))
    result = crm_mod._identify_churn_risk_customers(customers, "high")
    assert result == []


def test_churn_no_orders_excluded(monkeypatch):
    """Customers with no orders are not churn (they are prospects)."""
    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: _TestDBForChurn([]))
    result = crm_mod._identify_churn_risk_customers(
        [{"customer_id": "c5"}], "high"
    )
    assert result == []


# ---------------------------------------------------------------------------
# CRM-2: CL refill status
# ---------------------------------------------------------------------------

def test_cl_refill_daily_disposable(monkeypatch):
    """Daily disposable order: supply = pack_size * qty / 2 days."""
    now = datetime.utcnow()
    order_date = (now - timedelta(days=10)).isoformat()
    orders = [
        {
            "order_id": "ORD-001",
            "customer_id": "cust-1",
            "created_at": order_date,
            "items": [
                {
                    "sku": "ACU-DAILY-M3",
                    "category": "CONTACT_LENS",
                    "modality": "DAILY",
                    "pack_size": 30,
                    "quantity": 2,
                }
            ],
        }
    ]

    class _CL_DB:
        is_connected = True
        def get_collection(self, name):
            return _FakeColl(orders if name == "orders" else [])

    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: _CL_DB())
    import asyncio
    result = asyncio.run(
        crm_mod.get_cl_refill_status(
            customer_id="cust-1",
            current_user={"user_id": "u1", "roles": ["STORE_MANAGER"]},
        )
    )
    assert result["has_cl_history"] is True
    assert result["sku"] == "ACU-DAILY-M3"
    # 2 packs x 30 lenses = 60 lenses / 2 eyes = 30 days supply; started 10 days ago
    # => refill_due = order_date + 30 days = now + 20 days => days_remaining ~ 20
    assert result["refill_due_date"] is not None
    assert result["days_remaining"] is not None


def test_cl_refill_no_history(monkeypatch):
    """No CL orders => fail-soft empty result."""
    class _EmptyDB:
        is_connected = True
        def get_collection(self, name):
            return _FakeColl([])

    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: _EmptyDB())
    import asyncio
    result = asyncio.run(
        crm_mod.get_cl_refill_status(
            customer_id="cust-99",
            current_user={"user_id": "u1", "roles": ["STORE_MANAGER"]},
        )
    )
    assert result["has_cl_history"] is False
    assert result["refill_due_date"] is None


def test_cl_refill_no_db(monkeypatch):
    """No DB => fail-soft empty result."""
    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: None)
    import asyncio
    result = asyncio.run(
        crm_mod.get_cl_refill_status(
            customer_id="cust-99",
            current_user={"user_id": "u1"},
        )
    )
    assert result["has_cl_history"] is False


# ---------------------------------------------------------------------------
# CRM-5: return-risk endpoint
# ---------------------------------------------------------------------------

def test_return_risk_high(monkeypatch):
    """Customer with 3 returns on 5 orders (60%) => HIGH risk."""
    orders_docs = [{"customer_id": "c1", "status": "COMPLETED"}] * 5
    returns_docs = [
        {"customer_id": "c1", "returned_value": 500.0, "return_type": "RETURN"},
        {"customer_id": "c1", "returned_value": 300.0, "return_type": "RETURN"},
        {"customer_id": "c1", "returned_value": 200.0, "return_type": "RETURN"},
    ]

    class _DB:
        is_connected = True
        def get_collection(self, name):
            if name == "orders":
                return _FakeColl(orders_docs)
            if name == "returns":
                return _FakeColl(returns_docs)
            return _FakeColl()

    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: _DB())
    import asyncio
    result = asyncio.run(
        crm_mod.get_customer_return_risk(
            customer_id="c1",
            current_user={"user_id": "u1"},
        )
    )
    assert result["risk_level"] == "HIGH"
    assert result["return_count"] == 3
    assert result["order_count"] == 5
    assert result["return_rate_pct"] == 60.0


def test_return_risk_medium(monkeypatch):
    """Customer with 1 return on 5 orders (20%) => MEDIUM risk."""
    orders_docs = [{"customer_id": "c2", "status": "COMPLETED"}] * 5
    returns_docs = [
        {"customer_id": "c2", "returned_value": 500.0, "return_type": "RETURN"},
    ]

    class _DB:
        is_connected = True
        def get_collection(self, name):
            if name == "orders":
                return _FakeColl(orders_docs)
            if name == "returns":
                return _FakeColl(returns_docs)
            return _FakeColl()

    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: _DB())
    import asyncio
    result = asyncio.run(
        crm_mod.get_customer_return_risk(
            customer_id="c2",
            current_user={"user_id": "u1"},
        )
    )
    assert result["risk_level"] == "MEDIUM"
    assert result["return_count"] == 1


def test_return_risk_none(monkeypatch):
    """Customer with no returns => NONE risk."""
    orders_docs = [{"customer_id": "c3", "status": "COMPLETED"}] * 3

    class _DB:
        is_connected = True
        def get_collection(self, name):
            if name == "orders":
                return _FakeColl(orders_docs)
            return _FakeColl()

    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: _DB())
    import asyncio
    result = asyncio.run(
        crm_mod.get_customer_return_risk(
            customer_id="c3",
            current_user={"user_id": "u1"},
        )
    )
    assert result["risk_level"] == "NONE"
    assert result["return_count"] == 0


def test_return_risk_no_db(monkeypatch):
    """No DB => fail-soft NONE risk."""
    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: None)
    import asyncio
    result = asyncio.run(
        crm_mod.get_customer_return_risk(
            customer_id="c99",
            current_user={"user_id": "u1"},
        )
    )
    assert result["risk_level"] == "NONE"


# ---------------------------------------------------------------------------
# CRM-4: NPS detractor follow-up uses correct field names
# ---------------------------------------------------------------------------

def test_nps_detractor_followup_uses_correct_schema(monkeypatch):
    """NPS detractor response must insert scheduled_date/notes/customer_phone,
    not due_date/reason (which were the wrong fields -- CRM-4 fix)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import jwt as pyjwt
    from datetime import datetime as dt, timedelta as td

    from api.routers import marketing as mkt
    from api.routers import auth as auth_mod

    inserted = []

    class _InnerColl:
        def __init__(self, docs=None):
            self._docs = list(docs or [])
        def find_one(self, flt=None, _proj=None):
            for d in self._docs:
                if all(d.get(k) == v for k, v in (flt or {}).items()):
                    return dict(d)
            return None
        def update_one(self, *_a, **_kw):
            pass
        def insert_one(self, doc):
            inserted.append(doc)

    nps_doc = {
        "nps_id": "NPS-TEST-001",
        "store_id": "BV-01",
        "customer_id": "CUST-1",
        "customer_name": "Test User",
        "score": None,
        "status": "SENT",
    }
    customer_doc = {
        "customer_id": "CUST-1",
        "mobile": "9876543210",
    }

    class _FakeDB2:
        is_connected = True
        def get_collection(self, name):
            if name == "nps_responses":
                return _InnerColl([nps_doc])
            if name == "customers":
                return _InnerColl([customer_doc])
            if name == "follow_ups":
                return _InnerColl()
            return _InnerColl()

    monkeypatch.setattr(mkt, "_get_db", lambda: _FakeDB2())

    # Intercept insert_one on follow_ups via the fake DB
    # The follow_ups coll is accessed via db.get_collection("follow_ups").insert_one
    # which goes to _InnerColl.insert_one -> appends to `inserted`.
    real_get_coll = _FakeDB2().get_collection

    def _patched_db():
        class _DB2:
            is_connected = True
            def get_collection(self, name):
                if name == "follow_ups":
                    class _FuColl:
                        def insert_one(self_inner, doc):
                            inserted.append(doc)
                    return _FuColl()
                return real_get_coll(name)
        return _DB2()

    monkeypatch.setattr(mkt, "_get_db", _patched_db)

    tok = pyjwt.encode(
        {
            "sub": "u1",
            "user_id": "u1",
            "roles": ["STORE_MANAGER"],
            "exp": dt.utcnow() + td(hours=1),
        },
        auth_mod.SECRET_KEY,
        algorithm=auth_mod.ALGORITHM,
    )

    app = FastAPI()
    app.include_router(mkt.router, prefix="/api/v1/marketing")
    client = TestClient(app)

    r = client.post(
        "/api/v1/marketing/nps-response",
        json={"nps_id": "NPS-TEST-001", "score": 3, "feedback": "Bad experience"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text

    # A follow-up must have been inserted.
    assert len(inserted) == 1, "Expected one follow-up to be inserted"
    fu = inserted[0]

    # CRM-4: correct field names
    assert "scheduled_date" in fu, "Must use 'scheduled_date', not 'due_date'"
    assert "due_date" not in fu, "Must NOT write legacy 'due_date'"
    assert "notes" in fu, "Must use 'notes', not 'reason'"
    assert "reason" not in fu, "Must NOT write legacy 'reason'"
    # scheduled_date must be a date string (YYYY-MM-DD), not a full ISO datetime
    assert "T" not in fu["scheduled_date"], "scheduled_date should be a date (not datetime)"
    assert fu["status"] == "pending"
