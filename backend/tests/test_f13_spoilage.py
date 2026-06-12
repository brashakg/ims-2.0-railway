"""
IMS 2.0 - F13 remake justification + spoilage analytics tests (intent-level)
=============================================================================
Exercises the REAL spoilage_analytics service + workshop router against a
faithful in-memory fake Mongo (no network, no live mongod), with the REAL
WorkshopJobRepository wrapped around the fake collection. A hollow shell that
lets a reason-less rework through, invents a category, double-writes the job,
skips the spoilage cost, mis-computes the rollup, or lets a cashier edit the
owner's taxonomy FAILS here.

Maps to the F13 acceptance intents:
  * required justification -- POST /rework without remake_reason_code -> 422
                              ("remake_reason_code is required") and the job is
                              UNTOUCHED; unknown code -> 422 naming it
  * atomic append          -- the remake_reasons[] entry (with paise cost) lands
                              in the SAME single find_one_and_update that flips
                              QC_FAILED -> IN_PROGRESS (no second write)
  * category defaulting    -- from the code's taxonomy entry; optional
                              spoilage_category override; invalid override 422
  * spoilage audit         -- a SPOILAGE row in lens_stock_audit (fail-soft)
  * costing                -- WAC rupees (products.cost_price) -> integer paise;
                              missing product / DB -> 0, never a block
  * analytics              -- manager+ only; exact rate / totals / by_category /
                              by_reason / by_technician / top_reasons ordering
  * taxonomy               -- seed idempotent; GET any-auth; PUT admin-only with
                              strict validation, replaces the list
  * dashboard KPIs         -- spoilage_cost_mtd_paise + remake_rate_pct carried
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import workshop as workshop_module  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services import spoilage_analytics as svc  # noqa: E402
from database.repositories.workshop_repository import WorkshopJobRepository  # noqa: E402


# ============================================================================
# Faithful in-memory fake Mongo (operators F13 uses; mirrors test_f15's fake)
# ============================================================================


def _cmp_op(actual: Any, op: str, expected: Any) -> bool:
    if actual is None and op in ("$gt", "$gte", "$lt", "$lte"):
        return False
    try:
        if op == "$gt":
            return actual > expected
        if op == "$gte":
            return actual >= expected
        if op == "$lt":
            return actual < expected
        if op == "$lte":
            return actual <= expected
        if op == "$ne":
            return actual != expected
        if op == "$in":
            return actual in expected
        if op == "$nin":
            return actual not in expected
    except TypeError:
        return False
    return False


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in (query or {}).items():
        if k == "$or":
            if not any(_matches(doc, sub) for sub in v):
                return False
            continue
        actual = doc.get(k)
        if isinstance(v, dict) and any(str(kk).startswith("$") for kk in v):
            for op, expected in v.items():
                if not _cmp_op(actual, op, expected):
                    return False
            continue
        if actual != v:
            return False
    return True


def _apply_update(doc: Dict[str, Any], update: Dict[str, Any]) -> None:
    for op, fields in update.items():
        if op == "$set":
            for kk, vv in fields.items():
                doc[kk] = vv
        elif op == "$inc":
            for kk, vv in fields.items():
                doc[kk] = (doc.get(kk) or 0) + vv
        elif op == "$push":
            for kk, vv in fields.items():
                doc.setdefault(kk, [])
                doc[kk].append(vv)


class FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def sort(self, spec):
        if isinstance(spec, list) and spec:
            key, direction = spec[0]
            self._docs.sort(
                key=lambda d: (d.get(key) is None, str(d.get(key) or "")),
                reverse=(direction == -1),
            )
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        if n:
            self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []
        self._n = 0
        # Write-path instrumentation: proves the rework is a SINGLE write.
        self.n_find_one_and_update = 0
        self.n_update_one = 0

    def insert_one(self, doc):
        doc.setdefault("_id", f"oid-{self._n}")
        self._n += 1
        if any(d.get("_id") == doc["_id"] for d in self.docs):
            from pymongo.errors import DuplicateKeyError

            raise DuplicateKeyError(f"E11000 duplicate key error: _id {doc['_id']}")
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc["_id"]})()

    def find_one(self, query, projection=None):
        for d in self.docs:
            if _matches(d, query):
                return dict(d)
        return None

    def find(self, query=None, projection=None):
        return FakeCursor([dict(d) for d in self.docs if _matches(d, query or {})])

    def find_one_and_update(self, query, update, return_document=None, upsert=False, **_kw):
        self.n_find_one_and_update += 1
        for d in self.docs:
            if _matches(d, query):
                _apply_update(d, update)
                return dict(d)
        return None

    def update_one(self, query, update, upsert=False, **_kw):
        self.n_update_one += 1
        for d in self.docs:
            if _matches(d, query):
                _apply_update(d, update)
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        if upsert:
            doc = {k: v for k, v in (query or {}).items() if not str(k).startswith("$")}
            _apply_update(doc, update)
            doc.setdefault("_id", f"oid-{self._n}")
            self._n += 1
            self.docs.append(doc)
            return type(
                "R", (), {"matched_count": 0, "modified_count": 0, "upserted_id": doc["_id"]}
            )()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    def count_documents(self, query=None):
        return len([d for d in self.docs if _matches(d, query or {})])

    def aggregate(self, pipeline):
        return iter([])

    def create_index(self, *a, **k):
        return "idx"


class FakeDB:
    def __init__(self):
        self._collections: Dict[str, FakeCollection] = {}
        self.is_connected = True

    def get_collection(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]

    def __getitem__(self, name: str) -> FakeCollection:
        return self.get_collection(name)


# ============================================================================
# Fixtures + helpers
# ============================================================================

STORE = "BV-TEST-01"


def _mk_job(job_id="j1", status="QC_FAILED", store=STORE, **extra) -> Dict[str, Any]:
    doc: Dict[str, Any] = {
        "_id": job_id,
        "job_id": job_id,
        "job_number": f"WS-{job_id}",
        "status": status,
        "store_id": store,
        "created_at": datetime.now().isoformat(),
        "technician_id": "TECH-1",
        "lens_details": {"product_id": "LENS-1"},
        "fitting_details": {"confirmed_by_sales": True},
    }
    doc.update(extra)
    return doc


@pytest.fixture()
def db() -> FakeDB:
    d = FakeDB()
    # WAC on the product: cost_price 450.00 rupees -> 45000 paise.
    d.get_collection("products").insert_one({"product_id": "LENS-1", "cost_price": 450.0})
    return d


def _repo_with(db: FakeDB, jobs: List[Dict[str, Any]]) -> WorkshopJobRepository:
    coll = db.get_collection("workshop_jobs")
    for j in jobs:
        coll.insert_one(dict(j))
    return WorkshopJobRepository(coll)


def _client(monkeypatch, roles: List[str], repo, db, active_store: Optional[str] = STORE):
    app = FastAPI()
    app.include_router(workshop_module.router, prefix="/workshop")

    async def _fake_user():
        return {
            "user_id": "u1",
            "username": "tester",
            "full_name": "Test User",
            "active_store_id": active_store,
            "store_ids": [active_store] if active_store else [],
            "roles": roles,
        }

    app.dependency_overrides[get_current_user] = _fake_user
    monkeypatch.setattr(workshop_module, "get_workshop_repository", lambda: repo)
    monkeypatch.setattr(workshop_module, "get_db", lambda: db)
    return TestClient(app)


# ============================================================================
# Pure service: taxonomy + cost + summary
# ============================================================================


def test_default_taxonomy_is_well_formed():
    codes = {c["code"] for c in svc.DEFAULT_REASON_CODES}
    assert "OTHER" in codes and "AXIS_ERROR" in codes
    for c in svc.DEFAULT_REASON_CODES:
        assert c["code"] and c["label"]
        assert c["category"] in svc.VALID_CATEGORIES


def test_ensure_reason_codes_idempotent(db):
    assert svc.ensure_reason_codes(db) is True  # first call inserts
    assert svc.ensure_reason_codes(db) is False  # second is a no-op
    coll = db.get_collection(svc.REASON_CODES_DOC_ID)
    assert len(coll.docs) == 1, "two seeds must never produce two docs"
    assert coll.docs[0]["codes"] == svc.DEFAULT_REASON_CODES
    # DB absent -> fail-soft False, never a raise.
    assert svc.ensure_reason_codes(None) is False


def test_valid_codes_falls_back_without_db():
    lookup = svc.valid_codes(None)
    assert lookup["AXIS_ERROR"]["category"] == "LAB_FAULT"
    assert [c["code"] for c in svc.list_codes(None)] == [
        c["code"] for c in svc.DEFAULT_REASON_CODES
    ]


def test_put_payload_validation_rules():
    ok = [{"code": "x1", "label": "X", "category": "LAB_FAULT"}]
    assert svc.validate_codes_payload(ok) is None
    assert svc.validate_codes_payload([]) is not None
    assert svc.validate_codes_payload("nope") is not None
    assert "label" in svc.validate_codes_payload([{"code": "A", "category": "CUSTOMER"}])
    assert "category" in svc.validate_codes_payload(
        [{"code": "A", "label": "a", "category": "BOGUS"}]
    )
    assert "duplicate" in svc.validate_codes_payload(
        [
            {"code": "A", "label": "a", "category": "CUSTOMER"},
            {"code": "a", "label": "b", "category": "CUSTOMER"},
        ]
    )
    # normalize uppercases code/category for storage
    assert svc.normalize_codes_payload(ok)[0]["code"] == "X1"


def test_spoilage_cost_paise_is_none_safe_and_exact():
    assert svc.spoilage_cost_paise({}, None) == 0
    assert svc.spoilage_cost_paise({}, lambda j: None) == 0
    assert svc.spoilage_cost_paise({}, lambda j: "junk") == 0

    def _boom(_j):
        raise RuntimeError("resolver exploded")

    assert svc.spoilage_cost_paise({}, _boom) == 0
    assert svc.spoilage_cost_paise({}, lambda j: 12.345) == 1234  # half-up paise
    assert svc.spoilage_cost_paise({}, lambda j: -5) == 0  # never negative


def test_summary_math_exact():
    jobs = [
        {  # two remakes, tech T1
            "technician_id": "T1",
            "remake_reasons": [
                {"reason_code": "AXIS_ERROR", "category": "LAB_FAULT", "cost_paise": 10000},
                {"reason_code": "WRONG_LENS_PICKED", "category": "STORE_FAULT", "cost_paise": 1000},
            ],
        },
        {  # one remake, tech T2 (same reason as above -> count 2 for WRONG_LENS_PICKED)
            "technician_id": "T2",
            "remake_reasons": [
                {"reason_code": "WRONG_LENS_PICKED", "category": "STORE_FAULT", "cost_paise": 2000},
            ],
        },
        {"technician_id": "T1"},  # clean job
        {},  # clean job, never assigned
    ]
    s = svc.build_spoilage_summary(jobs, window_days=30)
    assert s["window_days"] == 30
    assert s["total_jobs"] == 4
    assert s["jobs_with_remake"] == 2
    assert s["total_remakes"] == 3
    assert s["remake_rate_pct"] == 50.0  # 2 of 4 jobs, 1dp
    assert s["spoilage_cost_total_paise"] == 13000
    assert s["by_category"] == {
        "LAB_FAULT": {"count": 1, "cost_paise": 10000},
        "STORE_FAULT": {"count": 2, "cost_paise": 3000},
    }
    assert s["by_reason"]["WRONG_LENS_PICKED"] == {"count": 2, "cost_paise": 3000}
    assert s["by_technician"] == {
        "T1": {"count": 2, "cost_paise": 11000},
        "T2": {"count": 1, "cost_paise": 2000},
    }
    # top_reasons: count desc first (WRONG=2 beats AXIS=1 despite lower cost)
    assert [r["reason_code"] for r in s["top_reasons"]] == [
        "WRONG_LENS_PICKED",
        "AXIS_ERROR",
    ]


def test_summary_empty_and_garbage_safe():
    s = svc.build_spoilage_summary([], window_days=90)
    assert s["total_jobs"] == 0 and s["remake_rate_pct"] == 0.0
    assert s["spoilage_cost_total_paise"] == 0 and s["top_reasons"] == []
    # non-dict jobs / entries are ignored, not crashed on
    s2 = svc.build_spoilage_summary(
        [None, "junk", {"remake_reasons": ["junk", {"cost_paise": "NaN"}]}],  # type: ignore[list-item]
        window_days=7,
    )
    assert s2["total_jobs"] == 1 and s2["total_remakes"] == 1
    assert s2["spoilage_cost_total_paise"] == 0


# ============================================================================
# POST /jobs/{id}/rework -- required justification, atomic append, audit
# ============================================================================


class TestReworkJustification:
    def test_422_when_reason_code_missing_and_job_untouched(self, monkeypatch, db):
        repo = _repo_with(db, [_mk_job()])
        c = _client(monkeypatch, ["WORKSHOP_STAFF"], repo, db)
        r = c.post("/workshop/jobs/j1/rework")
        assert r.status_code == 422, r.text
        assert r.json()["detail"] == "remake_reason_code is required"
        job = db.get_collection("workshop_jobs").find_one({"job_id": "j1"})
        assert job["status"] == "QC_FAILED", "a refused rework must not advance the job"
        assert "remake_reasons" not in job
        assert int(job.get("rework_count") or 0) == 0

    def test_422_unknown_code_names_it(self, monkeypatch, db):
        repo = _repo_with(db, [_mk_job()])
        c = _client(monkeypatch, ["WORKSHOP_STAFF"], repo, db)
        r = c.post("/workshop/jobs/j1/rework", params={"remake_reason_code": "BOGUS"})
        assert r.status_code == 422, r.text
        assert "BOGUS" in r.json()["detail"]
        job = db.get_collection("workshop_jobs").find_one({"job_id": "j1"})
        assert job["status"] == "QC_FAILED" and "remake_reasons" not in job

    def test_appends_reason_with_cost_in_single_atomic_update(self, monkeypatch, db):
        repo = _repo_with(db, [_mk_job()])
        coll = db.get_collection("workshop_jobs")
        c = _client(monkeypatch, ["WORKSHOP_STAFF"], repo, db)
        r = c.post(
            "/workshop/jobs/j1/rework",
            params={"remake_reason_code": "axis_error", "notes": "axis off by 5"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "IN_PROGRESS"
        assert body["rework_count"] == 1
        assert body["remake_reason_code"] == "AXIS_ERROR"  # case-normalised
        assert body["spoilage_cost_paise"] == 45000  # 450.00 rupees WAC -> paise

        job = coll.find_one({"job_id": "j1"})
        assert job["status"] == "IN_PROGRESS" and job["rework_count"] == 1
        entries = job["remake_reasons"]
        assert len(entries) == 1
        e = entries[0]
        assert e["reason_code"] == "AXIS_ERROR"
        assert e["category"] == "LAB_FAULT"  # defaulted from the taxonomy
        assert e["cost_paise"] == 45000
        assert e["by"] == "u1" and e["notes"] == "axis off by 5" and e["at"]

        # THE atomicity intent: ONE find_one_and_update did everything; no
        # second update_one write touched the job doc.
        assert coll.n_find_one_and_update == 1
        assert coll.n_update_one == 0

    def test_category_override_and_invalid_override_422(self, monkeypatch, db):
        repo = _repo_with(db, [_mk_job("j1"), _mk_job("j2")])
        c = _client(monkeypatch, ["WORKSHOP_STAFF"], repo, db)
        # CUSTOMER_CHANGED_RX defaults to CUSTOMER; override pins STORE_FAULT.
        r = c.post(
            "/workshop/jobs/j1/rework",
            params={
                "remake_reason_code": "CUSTOMER_CHANGED_RX",
                "spoilage_category": "STORE_FAULT",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["spoilage_category"] == "STORE_FAULT"
        # An invented category fails loudly and leaves j2 untouched.
        r2 = c.post(
            "/workshop/jobs/j2/rework",
            params={"remake_reason_code": "OTHER", "spoilage_category": "GREMLINS"},
        )
        assert r2.status_code == 422, r2.text
        job2 = db.get_collection("workshop_jobs").find_one({"job_id": "j2"})
        assert job2["status"] == "QC_FAILED"

    def test_writes_spoilage_audit_row(self, monkeypatch, db):
        repo = _repo_with(db, [_mk_job()])
        c = _client(monkeypatch, ["WORKSHOP_STAFF"], repo, db)
        r = c.post("/workshop/jobs/j1/rework", params={"remake_reason_code": "BREAKAGE_IN_LAB"})
        assert r.status_code == 200, r.text
        rows = db.get_collection("lens_stock_audit").docs
        assert len(rows) == 1
        row = rows[0]
        assert row["source_type"] == "SPOILAGE"
        assert row["job_id"] == "j1" and row["store_id"] == STORE
        assert row["reason_code"] == "BREAKAGE_IN_LAB"
        assert row["category"] == "LAB_FAULT"
        assert row["cost_paise"] == 45000
        assert row["by"] == "u1" and row["at"]

    def test_cost_fail_soft_zero_when_product_unknown(self, monkeypatch, db):
        job = _mk_job(lens_details={"product_id": "NO-SUCH-LENS"})
        repo = _repo_with(db, [job])
        c = _client(monkeypatch, ["WORKSHOP_STAFF"], repo, db)
        r = c.post("/workshop/jobs/j1/rework", params={"remake_reason_code": "OTHER"})
        assert r.status_code == 200, r.text
        assert r.json()["spoilage_cost_paise"] == 0  # costing gap never blocks

    def test_db_absent_still_records_with_default_taxonomy(self, monkeypatch):
        # No Mongo at all: taxonomy falls back to the seed, cost is 0, audit is
        # skipped -- but the justification still lands atomically on the job.
        bare = FakeDB()
        repo = _repo_with(bare, [_mk_job()])
        c = _client(monkeypatch, ["WORKSHOP_STAFF"], repo, db=None)
        r = c.post("/workshop/jobs/j1/rework", params={"remake_reason_code": "POWER_ERROR"})
        assert r.status_code == 200, r.text
        assert r.json()["spoilage_cost_paise"] == 0
        job = bare.get_collection("workshop_jobs").find_one({"job_id": "j1"})
        assert job["remake_reasons"][0]["reason_code"] == "POWER_ERROR"

    def test_wrong_status_400_and_race_409(self, monkeypatch, db):
        repo = _repo_with(db, [_mk_job("j1", status="PENDING"), _mk_job("j2")])
        c = _client(monkeypatch, ["WORKSHOP_STAFF"], repo, db)
        r = c.post("/workshop/jobs/j1/rework", params={"remake_reason_code": "OTHER"})
        assert r.status_code == 400, r.text
        # Simulate losing the guarded update (concurrent rework won): 409.
        monkeypatch.setattr(
            repo.collection, "find_one_and_update", lambda *a, **k: None
        )
        r2 = c.post("/workshop/jobs/j2/rework", params={"remake_reason_code": "OTHER"})
        assert r2.status_code == 409, r2.text


# ============================================================================
# GET /spoilage-analytics -- manager+ gate + windowed rollup
# ============================================================================


class TestSpoilageAnalytics:
    def _seed_jobs(self, db):
        now = datetime.now()
        fresh = _mk_job(
            "j1",
            status="IN_PROGRESS",
            remake_reasons=[
                {"reason_code": "AXIS_ERROR", "category": "LAB_FAULT", "cost_paise": 45000,
                 "at": now.isoformat()},
            ],
            rework_count=1,
        )
        clean = _mk_job("j2", status="PENDING")
        ancient = _mk_job(
            "j3",
            status="DELIVERED",
            created_at=(now - timedelta(days=400)).isoformat(),
            remake_reasons=[
                {"reason_code": "OTHER", "category": "LAB_FAULT", "cost_paise": 99999,
                 "at": (now - timedelta(days=400)).isoformat()},
            ],
        )
        return _repo_with(db, [fresh, clean, ancient])

    @pytest.mark.parametrize("role", ["WORKSHOP_STAFF", "SALES_STAFF", "CASHIER"])
    def test_non_manager_403(self, monkeypatch, db, role):
        repo = self._seed_jobs(db)
        c = _client(monkeypatch, [role], repo, db)
        assert c.get("/workshop/spoilage-analytics").status_code == 403

    def test_manager_gets_windowed_summary(self, monkeypatch, db):
        repo = self._seed_jobs(db)
        c = _client(monkeypatch, ["STORE_MANAGER"], repo, db)
        r = c.get("/workshop/spoilage-analytics", params={"days": 90})
        assert r.status_code == 200, r.text
        s = r.json()
        # j3 (created + remade 400 days ago) is OUTSIDE the 90-day window.
        assert s["window_days"] == 90
        assert s["total_jobs"] == 2
        assert s["jobs_with_remake"] == 1
        assert s["total_remakes"] == 1
        assert s["remake_rate_pct"] == 50.0
        assert s["spoilage_cost_total_paise"] == 45000
        assert s["by_technician"] == {"TECH-1": {"count": 1, "cost_paise": 45000}}
        assert s["top_reasons"][0]["reason_code"] == "AXIS_ERROR"
        assert s["store_id"] == STORE

    def test_old_job_with_fresh_remake_counts(self, monkeypatch, db):
        # A job CREATED long ago but remade yesterday IS current margin bleed.
        now = datetime.now()
        old_created = _mk_job(
            "j9",
            status="IN_PROGRESS",
            created_at=(now - timedelta(days=300)).isoformat(),
            remake_reasons=[
                {"reason_code": "COATING_DEFECT", "category": "VENDOR_FAULT",
                 "cost_paise": 7000, "at": (now - timedelta(days=1)).isoformat()},
            ],
        )
        repo = _repo_with(db, [old_created])
        c = _client(monkeypatch, ["AREA_MANAGER"], repo, db)
        s = c.get("/workshop/spoilage-analytics", params={"days": 90}).json()
        assert s["total_jobs"] == 1 and s["total_remakes"] == 1
        assert s["by_category"] == {"VENDOR_FAULT": {"count": 1, "cost_paise": 7000}}

    def test_repo_absent_fail_soft_empty(self, monkeypatch, db):
        c = _client(monkeypatch, ["ADMIN"], None, db)
        r = c.get("/workshop/spoilage-analytics")
        assert r.status_code == 200, r.text
        assert r.json()["total_jobs"] == 0


# ============================================================================
# Taxonomy endpoints -- GET any-auth, PUT admin-only + validated
# ============================================================================


class TestReasonCodeEndpoints:
    def test_get_returns_seeded_taxonomy_for_any_role(self, monkeypatch, db):
        repo = _repo_with(db, [])
        c = _client(monkeypatch, ["SALES_STAFF"], repo, db)
        r = c.get("/workshop/remake-reason-codes")
        assert r.status_code == 200, r.text
        codes = r.json()["codes"]
        assert [c_["code"] for c_ in codes] == [c_["code"] for c_ in svc.DEFAULT_REASON_CODES]

    def test_put_role_gated(self, monkeypatch, db):
        repo = _repo_with(db, [])
        body = {"codes": [{"code": "NEW", "label": "New", "category": "CUSTOMER"}]}
        for role in ("STORE_MANAGER", "AREA_MANAGER", "WORKSHOP_STAFF"):
            c = _client(monkeypatch, [role], repo, db)
            assert c.put("/workshop/remake-reason-codes", json=body).status_code == 403

    def test_put_replaces_and_get_reflects_it(self, monkeypatch, db):
        repo = _repo_with(db, [])
        c = _client(monkeypatch, ["ADMIN"], repo, db)
        body = {
            "codes": [
                {"code": "edge_chip", "label": "Edge chip", "category": "LAB_FAULT"},
                {"code": "TINT_MISMATCH", "label": "Tint mismatch", "category": "VENDOR_FAULT"},
            ]
        }
        r = c.put("/workshop/remake-reason-codes", json=body)
        assert r.status_code == 200, r.text
        assert r.json()["count"] == 2
        got = c.get("/workshop/remake-reason-codes").json()["codes"]
        assert [x["code"] for x in got] == ["EDGE_CHIP", "TINT_MISMATCH"]
        # ...and the rework validation now follows the OWNER'S taxonomy:
        repo2 = _repo_with(db, [_mk_job("jx")])
        c2 = _client(monkeypatch, ["WORKSHOP_STAFF"], repo2, db)
        assert (
            c2.post("/workshop/jobs/jx/rework", params={"remake_reason_code": "AXIS_ERROR"})
            .status_code
            == 422
        ), "a code the owner removed must no longer validate"
        assert (
            c2.post("/workshop/jobs/jx/rework", params={"remake_reason_code": "EDGE_CHIP"})
            .status_code
            == 200
        )

    def test_put_validation_422(self, monkeypatch, db):
        repo = _repo_with(db, [])
        c = _client(monkeypatch, ["SUPERADMIN"], repo, db)
        bad = {"codes": [{"code": "A", "label": "a", "category": "NOT_A_CATEGORY"}]}
        assert c.put("/workshop/remake-reason-codes", json=bad).status_code == 422
        assert c.put("/workshop/remake-reason-codes", json={"codes": []}).status_code == 422


# ============================================================================
# Dashboard KPIs carry the two new keys
# ============================================================================


class TestDashboardKpiKeys:
    def test_kpis_carry_spoilage_keys(self, monkeypatch, db):
        now = datetime.now()
        remade = _mk_job(
            "j1",
            status="IN_PROGRESS",
            remake_reasons=[
                {"reason_code": "AXIS_ERROR", "category": "LAB_FAULT",
                 "cost_paise": 45000, "at": now.isoformat()},
                # last month's spoilage must NOT land in MTD
                {"reason_code": "OTHER", "category": "LAB_FAULT",
                 "cost_paise": 11111, "at": (now - timedelta(days=62)).isoformat()},
            ],
        )
        clean = _mk_job("j2", status="PENDING")
        repo = _repo_with(db, [remade, clean])
        c = _client(monkeypatch, ["STORE_MANAGER"], repo, db)
        r = c.get("/workshop/dashboard-kpis")
        assert r.status_code == 200, r.text
        k = r.json()
        assert k["spoilage_cost_mtd_paise"] == 45000
        assert k["remake_rate_pct"] == 50.0  # 1 of 2 jobs

    def test_kpis_empty_path_still_carries_keys(self, monkeypatch):
        c = _client(monkeypatch, ["STORE_MANAGER"], None, None)
        k = c.get("/workshop/dashboard-kpis").json()
        assert k["spoilage_cost_mtd_paise"] == 0
        assert k["remake_rate_pct"] == 0.0
