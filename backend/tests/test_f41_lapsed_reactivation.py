"""
IMS 2.0 - F41 Lapsed-patient reactivation (#41) -- INTENT tests
===============================================================
The packet's intent: an in-app, per-store REACTIVATION WORK-LIST of clinically
lapsed patients (NO confirmed order AND no prescription exam in the lapse window,
default 24 months), worked through in-app (Reached / Skip). It is NOT an outbound
message channel and it mints NO voucher -- logging an outcome creates an in-app
reactivation_call follow_up record, NEVER a provider send (WhatsApp ban; F41 is
dark, mirroring the #39 NBA call list).

A hollow shell that returns canned data FAILS these tests: they assert the dual-gap
lapsed-cohort math (exclude recently-ordered AND recently-examined), the ranking
(VIP-first), the cap, the in-app outcome persistence, RBAC + cross-store scoping,
and that NO live send / no voucher mint ever fires.

CI-ROBUSTNESS: every repo/db accessor the handlers read is monkeypatched and the
fake DB is SEEDED with every doc the handler reads (no local-vs-CI fail-soft
divergence). Absence-of-a-value is asserted on the parsed object, never via a
whole-JSON substring check.

No emoji. Run:
  JWT_SECRET_KEY=test python -m pytest backend/tests/test_f41_lapsed_reactivation.py -q
"""

from __future__ import annotations

import copy
import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DISPATCH_MODE", "off")

import jwt  # noqa: E402
import pytest  # noqa: E402
from datetime import timezone  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import crm as crm_mod  # noqa: E402
from api.routers import auth as auth_mod  # noqa: E402
from api.services import lapsed_reactivation as react  # noqa: E402
from api.services import campaign_segments as seg  # noqa: E402

STORE = "BV-PUN-01"
OTHER_STORE = "BV-BOK-01"
TODAY = react._today_ist()
NOW = datetime.fromisoformat(TODAY + "T10:00:00")


# ---------------------------------------------------------------------------
# Fake Mongo -- supports the operators the F41 handlers + resolver use:
#   find($or / $gte / $lte / $nin / dotted-key entries.customer_id), find_one,
#   find_one_and_update ($set incl. positional entries.$.dismissed), insert_one,
#   count_documents, update_one. Modelled on the F39 fake (kept self-contained).
# ---------------------------------------------------------------------------


def _matches(doc, query):
    for key, val in (query or {}).items():
        if key == "$or":
            if not any(_matches(doc, sub) for sub in val):
                return False
            continue
        if "." in key:
            head, _, tail = key.partition(".")
            container = doc.get(head)
            if isinstance(container, list):
                if not any(isinstance(c, dict) and c.get(tail) == val for c in container):
                    return False
                continue
            actual = (doc.get(head) or {}).get(tail) if isinstance(doc.get(head), dict) else None
        else:
            actual = doc.get(key)
        if isinstance(val, dict):
            if "$in" in val and actual not in val["$in"]:
                return False
            if "$nin" in val and actual in val["$nin"]:
                return False
            if "$gte" in val and (actual is None or actual < val["$gte"]):
                return False
            if "$lte" in val and (actual is None or actual > val["$lte"]):
                return False
            if "$ne" in val and actual == val["$ne"]:
                return False
        else:
            if actual != val:
                return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __iter__(self):
        return iter(self._docs)


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = [copy.deepcopy(d) for d in (docs or [])]
        self.inserts = 0

    def find_one(self, query=None, projection=None, sort=None):
        for d in self.docs:
            if _matches(d, query or {}):
                return copy.deepcopy(d)
        return None

    def find(self, query=None, projection=None):
        return _Cursor([copy.deepcopy(d) for d in self.docs if _matches(d, query or {})])

    def count_documents(self, query=None):
        return sum(1 for d in self.docs if _matches(d, query or {}))

    def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))
        self.inserts += 1
        return type("R", (), {"inserted_id": "oid"})()

    def _apply_update(self, doc, update, query):
        if "$set" in update:
            for k, v in update["$set"].items():
                if k.startswith("entries.$."):
                    field = k[len("entries.$."):]
                    target_cid = query.get("entries.customer_id")
                    for e in doc.get("entries", []):
                        if e.get("customer_id") == target_cid:
                            e[field] = v
                elif "." in k:
                    head, _, tail = k.partition(".")
                    doc.setdefault(head, {})[tail] = v
                else:
                    doc[k] = v

    def update_one(self, query, update, **kw):
        for d in self.docs:
            if _matches(d, query or {}):
                self._apply_update(d, update, query)
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        if kw.get("upsert"):
            self.docs.append({})
            self._apply_update(self.docs[-1], update, query)
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    def find_one_and_update(self, query, update, return_document=False, upsert=False, **kw):
        for d in self.docs:
            if _matches(d, query or {}):
                before = copy.deepcopy(d)
                self._apply_update(d, update, query)
                return copy.deepcopy(d if return_document else before)
        if upsert:
            new = {}
            self._apply_update(new, update, query)
            self.docs.append(new)
            return copy.deepcopy(new) if return_document else None
        return None


class _FakeDB:
    is_connected = True

    def __init__(self, collections=None):
        self._cols = collections or {}

    def get_collection(self, name):
        if name not in self._cols:
            self._cols[name] = _FakeColl()
        return self._cols[name]


# ---------------------------------------------------------------------------
# Seed builders
# ---------------------------------------------------------------------------


def _months_ago(months):
    return NOW - timedelta(days=int(months * 30))


def _seed(customers=None, orders=None, prescriptions=None, cohorts=None, follow_ups=None):
    return _FakeDB({
        "customers": _FakeColl(customers or []),
        "orders": _FakeColl(orders or []),
        "prescriptions": _FakeColl(prescriptions or []),
        "reactivation_cohorts": _FakeColl(cohorts or []),
        "follow_ups": _FakeColl(follow_ups or []),
        "notification_logs": _FakeColl([]),
        "vouchers": _FakeColl([]),
        "stores": _FakeColl([{"store_id": STORE, "status": "ACTIVE"}]),
        "audit_logs": _FakeColl([]),
    })


# Population for the cohort-math tests:
#   LAPSED   -- last order 25mo ago, last exam 26mo ago (both > 24mo) -> lapsed.
#   ACTIVE_O -- last order 10mo ago -> active (recent sale).
#   ACTIVE_X -- last order 30mo ago BUT exam 5mo ago -> active (recent exam).
#   NEVER    -- no order, no exam at all -> infinitely lapsed.
#   VIPLAP   -- lapsed AND carries a HIGH vip_churn_risk -> lapsed + VIP-first.
def _seed_population():
    customers = [
        {"customer_id": "LAPSED", "name": "Lap Sed", "mobile": "9000000001", "store_id": STORE},
        {"customer_id": "ACTIVE_O", "name": "Act Order", "mobile": "9000000002", "store_id": STORE},
        {"customer_id": "ACTIVE_X", "name": "Act Exam", "mobile": "9000000003", "store_id": STORE},
        {"customer_id": "NEVER", "name": "Nev Er", "mobile": "9000000004", "store_id": STORE},
        {"customer_id": "VIPLAP", "name": "Vip Lapsed", "mobile": "9000000005", "store_id": STORE,
         "total_lifetime_value": 200000,
         "vip_churn_risk": {"risk_label": "HIGH", "overdue_by_days": 400}},
    ]
    orders = [
        {"order_id": "O1", "customer_id": "LAPSED", "store_id": STORE,
         "created_at": _months_ago(25), "status": "DELIVERED"},
        {"order_id": "O2", "customer_id": "ACTIVE_O", "store_id": STORE,
         "created_at": _months_ago(10), "status": "DELIVERED"},
        {"order_id": "O3", "customer_id": "ACTIVE_X", "store_id": STORE,
         "created_at": _months_ago(30), "status": "DELIVERED"},
        {"order_id": "O4", "customer_id": "VIPLAP", "store_id": STORE,
         "created_at": _months_ago(28), "status": "DELIVERED"},
    ]
    prescriptions = [
        {"prescription_id": "RX1", "customer_id": "LAPSED", "store_id": STORE,
         "created_at": _months_ago(26).isoformat()},
        {"prescription_id": "RX2", "customer_id": "ACTIVE_X", "store_id": STORE,
         "created_at": _months_ago(5).isoformat()},
        {"prescription_id": "RX3", "customer_id": "VIPLAP", "store_id": STORE,
         "created_at": _months_ago(27).isoformat()},
    ]
    return _seed(customers=customers, orders=orders, prescriptions=prescriptions)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def _tok(roles, uid="u1", store_id=STORE):
    return jwt.encode(
        {
            "sub": uid, "user_id": uid, "username": "tester", "full_name": "Tester",
            "roles": list(roles), "active_store_id": store_id, "store_ids": [store_id],
            "exp": datetime.utcnow() + timedelta(hours=1),
        },
        auth_mod.SECRET_KEY,
        algorithm=auth_mod.ALGORITHM,
    )


def _hdr(roles=("STORE_MANAGER",), store_id=STORE):
    return {"Authorization": f"Bearer {_tok(roles, store_id=store_id)}"}


def _crm_client(db, monkeypatch):
    monkeypatch.setattr(crm_mod, "_crm_get_db", lambda: db)
    app = FastAPI()
    app.include_router(crm_mod.router, prefix="/api/v1/crm")
    return TestClient(app)


# ===========================================================================
# T1 -- dual-gap lapsed-cohort math (the PRIMARY code path).
# ===========================================================================


def test_t1_lapsed_dual_gap_resolver():
    db = _seed_population()
    rows = seg.resolve_segment(db, "lapsed_patient", store_id=STORE,
                               params={"lapse_threshold_months": 24, "now": NOW})
    ids = {r["customer_id"] for r in rows}
    # LAPSED + NEVER + VIPLAP are lapsed; ACTIVE_O (recent sale) + ACTIVE_X
    # (recent exam) are excluded by the dual-gap.
    assert ids == {"LAPSED", "NEVER", "VIPLAP"}
    assert "ACTIVE_O" not in ids  # recent order keeps a patient active
    assert "ACTIVE_X" not in ids  # recent exam keeps a patient active


def test_t1b_months_lapsed_reported():
    db = _seed_population()
    rows = seg.resolve_segment(db, "lapsed_patient", store_id=STORE,
                               params={"lapse_threshold_months": 24, "now": NOW})
    by_id = {r["customer_id"]: r for r in rows}
    # LAPSED's most-recent touch is the 25mo order -> >= 24 months reported.
    assert by_id["LAPSED"]["variables"]["months_lapsed"] >= 24
    # NEVER has no record at all -> months_lapsed is None (infinitely lapsed),
    # asserted on the parsed object (not a JSON substring).
    assert by_id["NEVER"]["variables"]["months_lapsed"] is None


def test_t1c_threshold_is_tunable():
    db = _seed_population()
    # A 6-month threshold makes ACTIVE_O (10mo) lapsed too; ACTIVE_X (5mo) stays.
    rows = seg.resolve_segment(db, "lapsed_patient", store_id=STORE,
                               params={"lapse_threshold_months": 6, "now": NOW})
    ids = {r["customer_id"] for r in rows}
    assert "ACTIVE_O" in ids
    assert "ACTIVE_X" not in ids


def test_t1d_draft_order_does_not_keep_active():
    # A patient whose ONLY recent order is a DRAFT/CANCELLED is still lapsed.
    customers = [{"customer_id": "DRAFTER", "name": "Dr Aft", "mobile": "9", "store_id": STORE}]
    orders = [
        {"order_id": "OD", "customer_id": "DRAFTER", "store_id": STORE,
         "created_at": _months_ago(1), "status": "DRAFT"},
        {"order_id": "OC", "customer_id": "DRAFTER", "store_id": STORE,
         "created_at": _months_ago(2), "status": "CANCELLED"},
    ]
    db = _seed(customers=customers, orders=orders)
    rows = seg.resolve_segment(db, "lapsed_patient", store_id=STORE,
                               params={"lapse_threshold_months": 24, "now": NOW})
    assert {r["customer_id"] for r in rows} == {"DRAFTER"}


# ===========================================================================
# T2 -- cohort build ranks VIP-first, then most-lapsed; respects the cap.
# ===========================================================================


def test_t2_cohort_ranks_vip_first():
    db = _seed_population()
    entries = react.build_cohort(db, STORE, now=NOW, lapse_months=24, cohort_size=50)
    cids = [e["customer_id"] for e in entries]
    assert set(cids) == {"LAPSED", "NEVER", "VIPLAP"}
    # VIPLAP carries a HIGH vip_churn_risk -> ranked first.
    assert entries[0]["customer_id"] == "VIPLAP"
    assert entries[0]["is_vip"] is True
    assert entries[0]["rank"] == 1


def test_t2b_cohort_size_cap():
    # 5 lapsed customers, cohort_size=2 -> exactly 2 entries.
    customers = [
        {"customer_id": f"L{i}", "name": f"L {i}", "mobile": f"90000{i:03d}", "store_id": STORE}
        for i in range(5)
    ]
    db = _seed(customers=customers)  # no orders/exams at all -> all lapsed
    entries = react.build_cohort(db, STORE, now=NOW, lapse_months=24, cohort_size=2)
    assert len(entries) == 2


def test_t2c_empty_when_no_lapsed():
    db = _seed(customers=[{"customer_id": "A", "name": "A", "mobile": "9", "store_id": STORE}],
               orders=[{"order_id": "O", "customer_id": "A", "store_id": STORE,
                        "created_at": _months_ago(1), "status": "DELIVERED"}])
    entries = react.build_cohort(db, STORE, now=NOW, lapse_months=24)
    assert entries == []


# ===========================================================================
# T3 -- the work-list endpoint serves the cohort; store-scoped; read-only preview.
# ===========================================================================


def test_t3_worklist_fallback_builds(monkeypatch):
    db = _seed_population()  # reactivation_cohorts EMPTY -> synchronous fallback
    client = _crm_client(db, monkeypatch)
    r = client.get(f"/api/v1/crm/reactivation/{STORE}", headers=_hdr(("STORE_MANAGER",)))
    assert r.status_code == 200
    body = r.json()
    assert body["entries"], "fallback must compute a non-empty list"
    assert body["generated_at"]


def test_t3b_worklist_reads_persisted_doc(monkeypatch):
    db = _seed_population()
    # Pre-seed today's cohort doc (the MEGAPHONE-built path).
    entries = react.build_cohort(db, STORE, now=NOW, lapse_months=24)
    doc = react.build_cohort_doc(STORE, entries, date_str=TODAY, lapse_months=24)
    db.get_collection("reactivation_cohorts").docs.append(doc)
    client = _crm_client(db, monkeypatch)
    r = client.get(f"/api/v1/crm/reactivation/{STORE}", headers=_hdr(("SALES_STAFF",)))
    assert r.status_code == 200
    assert r.json()["lapse_months"] == 24


def test_t3c_preview_is_read_only(monkeypatch):
    db = _seed_population()
    client = _crm_client(db, monkeypatch)
    before = len(db.get_collection("reactivation_cohorts").docs)
    r = client.get(f"/api/v1/crm/reactivation/{STORE}?preview=true", headers=_hdr(("ADMIN",)))
    assert r.status_code == 200
    assert r.json()["entries"]
    # Preview NEVER persists a cohort doc and NEVER mints a voucher.
    assert len(db.get_collection("reactivation_cohorts").docs) == before
    assert db.get_collection("vouchers").inserts == 0


def test_t3d_cross_store_idor_blocked(monkeypatch):
    db = _seed_population()
    client = _crm_client(db, monkeypatch)
    # SALES_STAFF scoped to STORE asks for OTHER_STORE -> 403, not 200 with data.
    r = client.get(f"/api/v1/crm/reactivation/{OTHER_STORE}",
                   headers=_hdr(("SALES_STAFF",), store_id=STORE))
    assert r.status_code == 403


# ===========================================================================
# T4 -- logging an outcome writes an IN-APP follow_up record (NO send, NO voucher).
# ===========================================================================


def test_t4_log_outcome_writes_followup_record(monkeypatch):
    db = _seed_population()
    entries = react.build_cohort(db, STORE, now=NOW, lapse_months=24)
    # Pre-create the reactivation_call follow_up + stamp its id (MEGAPHONE path).
    fu_id = "FU-EXIST-VIPLAP"
    db.get_collection("follow_ups").docs.append({
        "follow_up_id": fu_id, "customer_id": "VIPLAP", "store_id": STORE,
        "type": "reactivation_call", "scheduled_date": TODAY, "status": "pending",
        "outcome": None,
    })
    for e in entries:
        if e["customer_id"] == "VIPLAP":
            e["follow_up_id"] = fu_id
    doc = react.build_cohort_doc(STORE, entries, date_str=TODAY, lapse_months=24)
    db.get_collection("reactivation_cohorts").docs.append(doc)
    monkeypatch.setattr(crm_mod, "get_audit_repository", lambda: None)
    client = _crm_client(db, monkeypatch)

    r = client.post(f"/api/v1/crm/reactivation/{STORE}/log",
                    json={"customer_id": "VIPLAP", "outcome": "reached",
                          "notes": "Spoke to the customer; booking an eye test."},
                    headers=_hdr(("SALES_STAFF",)))
    assert r.status_code == 200 and r.json()["ok"] is True

    # (1) the in-app follow_up is resolved to completed with the outcome.
    fu = db.get_collection("follow_ups").find_one({"follow_up_id": fu_id})
    assert fu["status"] == "completed" and fu["outcome"] == "reached"
    # (2) NO message row and NO voucher were ever written -- dark / in-app only.
    assert db.get_collection("notification_logs").inserts == 0
    assert db.get_collection("vouchers").inserts == 0
    # (3) the entry is removed from the live work-list (dismissed).
    g = client.get(f"/api/v1/crm/reactivation/{STORE}", headers=_hdr(("SALES_STAFF",)))
    assert "VIPLAP" not in {e["customer_id"] for e in g.json()["entries"]}


def test_t4b_log_skip_marks_followup_skipped(monkeypatch):
    db = _seed_population()
    entries = react.build_cohort(db, STORE, now=NOW, lapse_months=24)
    doc = react.build_cohort_doc(STORE, entries, date_str=TODAY, lapse_months=24)
    db.get_collection("reactivation_cohorts").docs.append(doc)
    monkeypatch.setattr(crm_mod, "get_audit_repository", lambda: None)
    client = _crm_client(db, monkeypatch)
    # No pre-created follow_up here -> the handler writes the in-app RECORD itself.
    r = client.post(f"/api/v1/crm/reactivation/{STORE}/log",
                    json={"customer_id": "LAPSED", "outcome": "no_answer", "notes": ""},
                    headers=_hdr(("STORE_MANAGER",)))
    assert r.status_code == 200
    fid = r.json()["follow_up_id"]
    assert fid
    fu = db.get_collection("follow_ups").find_one({"follow_up_id": fid})
    assert fu["status"] == "skipped" and fu["outcome"] == "no_answer"
    assert fu["type"] == "reactivation_call"
    assert db.get_collection("notification_logs").inserts == 0


def test_t4d_off_list_outcome_rejected_and_writes_nothing(monkeypatch):
    """Adversarial regression (audit F41-P3): logging an outcome for a customer
    NOT on today's persisted work-list must 404 (not_on_todays_list) and insert
    NO reactivation_call follow_up -- the old else-branch wrote an orphan record
    that polluted the analytics aggregation."""
    db = _seed_population()
    entries = react.build_cohort(db, STORE, now=NOW, lapse_months=24)
    doc = react.build_cohort_doc(STORE, entries, date_str=TODAY, lapse_months=24)
    db.get_collection("reactivation_cohorts").docs.append(doc)
    monkeypatch.setattr(crm_mod, "get_audit_repository", lambda: None)
    client = _crm_client(db, monkeypatch)

    # ACTIVE_O is a real customer but NOT lapsed -> not in today's cohort.
    r = client.post(f"/api/v1/crm/reactivation/{STORE}/log",
                    json={"customer_id": "ACTIVE_O", "outcome": "reached",
                          "notes": "Should never be recorded."},
                    headers=_hdr(("STORE_MANAGER",)))
    assert r.status_code == 404
    assert r.json()["detail"] == "not_on_todays_list"
    # NOTHING was written: no follow_up insert, no analytics pollution.
    assert db.get_collection("follow_ups").inserts == 0
    assert db.get_collection("follow_ups").docs == []

    # Even WITH a next-touch date supplied, an off-list log writes nothing.
    r2 = client.post(f"/api/v1/crm/reactivation/{STORE}/log",
                     json={"customer_id": "GHOST-404", "outcome": "no_answer",
                           "notes": "", "follow_up_scheduled_date": "2030-01-01"},
                     headers=_hdr(("STORE_MANAGER",)))
    assert r2.status_code == 404
    assert db.get_collection("follow_ups").inserts == 0


def test_t4e_log_without_todays_cohort_doc_is_404(monkeypatch):
    """No persisted cohort doc for today at all -> the log endpoint refuses
    (404 not_on_todays_list) rather than minting an orphan follow_up."""
    db = _seed_population()  # reactivation_cohorts EMPTY
    monkeypatch.setattr(crm_mod, "get_audit_repository", lambda: None)
    client = _crm_client(db, monkeypatch)
    r = client.post(f"/api/v1/crm/reactivation/{STORE}/log",
                    json={"customer_id": "LAPSED", "outcome": "reached",
                          "notes": "Cohort never persisted today."},
                    headers=_hdr(("STORE_MANAGER",)))
    assert r.status_code == 404
    assert r.json()["detail"] == "not_on_todays_list"
    assert db.get_collection("follow_ups").inserts == 0


def test_t4c_log_schedules_next_touch(monkeypatch):
    db = _seed_population()
    entries = react.build_cohort(db, STORE, now=NOW, lapse_months=24)
    doc = react.build_cohort_doc(STORE, entries, date_str=TODAY, lapse_months=24)
    db.get_collection("reactivation_cohorts").docs.append(doc)
    monkeypatch.setattr(crm_mod, "get_audit_repository", lambda: None)
    client = _crm_client(db, monkeypatch)
    nextd = (NOW + timedelta(days=14)).date().isoformat()
    r = client.post(f"/api/v1/crm/reactivation/{STORE}/log",
                    json={"customer_id": "NEVER", "outcome": "scheduled_visit",
                          "notes": "Will come Saturday.",
                          "follow_up_scheduled_date": nextd},
                    headers=_hdr(("SALES_STAFF",)))
    assert r.status_code == 200
    nfid = r.json()["next_follow_up_id"]
    assert nfid
    newfu = db.get_collection("follow_ups").find_one({"follow_up_id": nfid})
    assert newfu["type"] == "reactivation_call" and newfu["scheduled_date"] == nextd
    assert newfu["status"] == "pending"


# ===========================================================================
# T5 -- analytics aggregates the in-app outcome log (NOT a notification log).
# ===========================================================================


def test_t5_analytics_aggregation(monkeypatch):
    since_ok = (datetime.now() - timedelta(days=3)).isoformat()
    follow_ups = [
        {"follow_up_id": "F1", "store_id": STORE, "type": "reactivation_call",
         "outcome": "reached", "completed_at": since_ok},
        {"follow_up_id": "F2", "store_id": STORE, "type": "reactivation_call",
         "outcome": "reached", "completed_at": since_ok},
        {"follow_up_id": "F3", "store_id": STORE, "type": "reactivation_call",
         "outcome": "no_answer", "completed_at": since_ok},
        # A non-reactivation follow_up MUST NOT be counted.
        {"follow_up_id": "F4", "store_id": STORE, "type": "nba_call",
         "outcome": "completed", "completed_at": since_ok},
    ]
    db = _seed(follow_ups=follow_ups,
               customers=[{"customer_id": "L", "name": "L", "mobile": "9", "store_id": STORE}])
    client = _crm_client(db, monkeypatch)
    # STORE_MANAGER: a store-facing role on the F41 policy rows (ACCOUNTANT was
    # dropped to match the FE route gate -- see test_t6).
    r = client.get(f"/api/v1/crm/reactivation/{STORE}/analytics", headers=_hdr(("STORE_MANAGER",)))
    assert r.status_code == 200
    body = r.json()
    assert body["logged"] == 3  # F4 (nba_call) excluded
    assert body["reached"] == 2
    assert body["no_answer"] == 1
    assert body["currently_lapsed"] == 1  # the seeded lapsed customer L


# ===========================================================================
# T6 -- RBAC: the central policy registry gates the routes correctly.
# ===========================================================================


def test_t6_rbac_policy_rows():
    from api.services import rbac_policy as rbac

    wl = f"/api/v1/crm/reactivation/{STORE}"
    log = f"/api/v1/crm/reactivation/{STORE}/log"
    an = f"/api/v1/crm/reactivation/{STORE}/analytics"

    # All three endpoints catalogued (coverage-lock parity).
    assert rbac.policy_for("GET", wl) is not None
    assert rbac.policy_for("POST", log) is not None
    assert rbac.policy_for("GET", an) is not None

    # Store-facing roles reach the work-list; clinical/workshop/catalog do not.
    for role in ("SALES_STAFF", "SALES_CASHIER", "STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"):
        assert rbac.check_access("GET", wl, [role]) is True, role
    for role in ("OPTOMETRIST", "WORKSHOP_STAFF", "CATALOG_MANAGER"):
        assert rbac.check_access("GET", wl, [role]) is False, role

    # ACCOUNTANT is NOT on any F41 surface: the policy mirrors the (stricter)
    # FE route gate -- App.tsx customers/reactivation has no ACCOUNTANT -- so
    # the FE/BE role drift flagged by the adversarial audit (F41-P3) is closed.
    assert rbac.check_access("GET", an, ["ACCOUNTANT"]) is False
    assert rbac.check_access("GET", wl, ["ACCOUNTANT"]) is False
    assert rbac.check_access("POST", log, ["ACCOUNTANT"]) is False
    # The store-facing roles still see analytics.
    for role in ("SALES_STAFF", "SALES_CASHIER", "STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"):
        assert rbac.check_access("GET", an, [role]) is True, role

    # Store-scope is declared so the request-time enforcer applies validate_store_access.
    assert rbac.is_store_scoped("GET", wl) is True
    assert rbac.is_store_scoped("POST", log) is True


# ===========================================================================
# T7 -- IST day-key + TTL anchor (audit F39/F41-P3 timezone class). The cohort
# day key is the IST calendar day and the TTL anchor is the NAIVE-UTC instant
# of the next IST midnight (Mongo reads naive BSON dates as UTC).
# ===========================================================================


def test_t7_ttl_anchor_is_utc_instant_of_ist_midnight():
    # IST midnight after 2026-06-10 = 2026-06-11T00:00+05:30 = 2026-06-10T18:30Z.
    # The old bare `.astimezone()` used the SERVER-LOCAL zone, so on a non-UTC
    # host (e.g. an IST dev box) this returned 2026-06-11T00:00 naive -> Mongo
    # read it as UTC and the work-list lingered 5h30m past the IST midnight.
    assert react._ist_midnight_utc("2026-06-10") == datetime(2026, 6, 10, 18, 30)
    doc = react.build_cohort_doc(STORE, [], date_str="2026-06-10", lapse_months=24)
    assert doc["ttl_expires_at"] == datetime(2026, 6, 10, 18, 30)
    assert doc["date"] == "2026-06-10"


def test_t7b_day_key_boundary_early_morning_ist():
    # 01:00 IST on 2026-06-10 == 19:30 UTC on 2026-06-09: the day key must be
    # the IST day (2026-06-10), never the prior UTC day.
    early = datetime(2026, 6, 9, 19, 30, tzinfo=timezone.utc)
    assert react._today_ist(early) == "2026-06-10"
    # 23:59 IST stays on the same IST day (18:29 UTC).
    late = datetime(2026, 6, 9, 18, 29, tzinfo=timezone.utc)
    assert react._today_ist(late) == "2026-06-09"


# ===========================================================================
# NO-LIVE-SEND (dark): the whole feature is in-app. The builder + the handlers
# do not import or reference any send function, and the MEGAPHONE cohort step
# queues NO messages and mints NO voucher.
# ===========================================================================


def test_no_live_send_service_has_no_sender():
    # Tripwires asserted on the module objects (not a JSON substring).
    assert not hasattr(react, "send_notification")
    assert not hasattr(react, "send_whatsapp")
    assert not hasattr(react, "send_sms")
    assert not hasattr(crm_mod, "send_notification")


def test_no_live_send_megaphone_step_queues_no_messages():
    import agents.implementations.megaphone as mp

    db = _seed_population()
    agent = mp.MegaphoneAgent(db=db)
    stats = agent._build_reactivation_cohorts()
    assert stats["stores_built"] == 1
    # The step writes reactivation_cohorts + reactivation_call follow_ups, NOT
    # messages, and mints NO voucher.
    assert db.get_collection("notification_logs").inserts == 0
    assert db.get_collection("vouchers").inserts == 0
    fu_types = {f.get("type") for f in db.get_collection("follow_ups").docs}
    assert "reactivation_call" in fu_types
    # The cohort doc was persisted (one document, one collection -- P0-1).
    assert db.get_collection("reactivation_cohorts").find_one(
        {"store_id": STORE, "date": TODAY}
    ) is not None


def test_megaphone_step_idempotent():
    import agents.implementations.megaphone as mp

    db = _seed_population()
    agent = mp.MegaphoneAgent(db=db)
    first = agent._build_reactivation_cohorts()
    assert first["stores_built"] == 1
    cohorts_after = len(db.get_collection("reactivation_cohorts").docs)
    fu_after = len(db.get_collection("follow_ups").docs)

    second = agent._build_reactivation_cohorts()
    assert second["stores_built"] == 0  # already built today -> skipped
    assert len(db.get_collection("reactivation_cohorts").docs) == cohorts_after
    assert len(db.get_collection("follow_ups").docs) == fu_after
