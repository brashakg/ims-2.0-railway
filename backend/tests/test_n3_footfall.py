"""
IMS 2.0 - N3 Footfall + conversion % (manual) acceptance tests
==============================================================
Intent-level acceptance for feature N3 (docs/roadmap/features/F3footfall.md).
A hollow shell -- an endpoint that always 200s with dummy data, or one that
silently stores walk_ins=0 / scores a missing footfall as 0 -- FAILS these.

Binding correction folded (docs/roadmap/CORRECTIONS.md HARDENING line 92):
  A missing walk-in footfall makes the conversion UNSCORED (null / blocked),
  NEVER a silent 0 -- a silent 0 corrupts payout rupees ("Fail Loudly").

CI-robustness (hard lesson): these tests monkeypatch EVERY repo/db accessor the
handlers read and SEED the docs they need, so behaviour is identical whether or
not a real Mongo is present (no reliance on the fail-soft None branch, which
diverges local vs CI).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

STORE = "BV-TEST-01"
OTHER_STORE = "BV-TEST-02"


# ===========================================================================
# Fake Mongo (single-doc find_one_and_update + $set/$push) -- mirrors
# test_sc_scorecard.FakeCollection, adds nothing the handlers don't use.
# ===========================================================================


def _doc_matches(doc, filt):
    if not filt:
        return True
    for k, expected in filt.items():
        actual = doc.get(k)
        if isinstance(expected, dict):
            for op, op_val in expected.items():
                if op == "$gte" and not (actual is not None and actual >= op_val):
                    return False
                if op == "$lte" and not (actual is not None and actual <= op_val):
                    return False
                if op == "$ne" and actual == op_val:
                    return False
                if op == "$in" and actual not in op_val:
                    return False
                if op == "$nin" and actual in op_val:
                    return False
        else:
            if actual != expected:
                return False
    return True


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)
        self._sort = None

    def sort(self, keys):
        self._sort = keys
        return self

    def __iter__(self):
        out = list(self._docs)
        if self._sort:
            for key, direction in reversed(self._sort):
                out.sort(
                    key=lambda d, k=key: (d.get(k) is None, d.get(k)),
                    reverse=(direction == -1),
                )
        return iter(out)


class FakeCollection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, filt=None, projection=None):
        if not filt:
            return self.docs[0] if self.docs else None
        for d in self.docs:
            if _doc_matches(d, filt):
                return d
        return None

    def find(self, filt=None, projection=None):
        return _FakeCursor(d for d in self.docs if _doc_matches(d, filt))

    def _apply(self, d, update):
        for k, v in ((update or {}).get("$set", {}) or {}).items():
            d[k] = v
        for k, v in ((update or {}).get("$push", {}) or {}).items():
            arr = d.get(k)
            if not isinstance(arr, list):
                arr = []
            arr.append(v)
            d[k] = arr

    def update_one(self, filt, update):
        modified = 0
        for d in self.docs:
            if _doc_matches(d, filt):
                self._apply(d, update)
                modified += 1
                break
        return type("R", (), {"modified_count": modified, "matched_count": modified})()

    def find_one_and_update(self, filt, update, return_document=None):
        for d in self.docs:
            if _doc_matches(d, filt):
                self._apply(d, update)
                return d
        return None


class FakeDB:
    is_connected = True

    def __init__(self):
        self._collections = {}

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]

    def __getattr__(self, name):
        return self.get_collection(name)


# ===========================================================================
# Fixtures
# ===========================================================================


# Active customer-facing roster the entry-status enum is measured against.
# S1 = sales staff, S2 = sales cashier; ACC (accountant) is NOT a footfall role
# so it must NOT count toward "expected staff" / "missing".
_ROSTER = {
    STORE: [
        {"user_id": "S1", "name": "Staff One", "roles": ["SALES_STAFF"],
         "store_ids": [STORE], "is_active": True},
        {"user_id": "S2", "name": "Staff Two", "roles": ["SALES_CASHIER"],
         "store_ids": [STORE], "is_active": True},
        {"user_id": "ACC", "name": "Account", "roles": ["ACCOUNTANT"],
         "store_ids": [STORE], "is_active": True},
    ],
    OTHER_STORE: [
        {"user_id": "S9", "name": "Staff Nine", "roles": ["SALES_STAFF"],
         "store_ids": [OTHER_STORE], "is_active": True},
    ],
}


def _fake_user_repo():
    class _R:
        def find_by_store(self, store_id, active_only=True):
            rows = _ROSTER.get(store_id, [])
            if active_only:
                rows = [r for r in rows if r.get("is_active")]
            return [dict(r) for r in rows]

        def find_by_id(self, uid):
            for rows in _ROSTER.values():
                for r in rows:
                    if r.get("user_id") == uid:
                        return dict(r)
            return {"user_id": uid, "name": uid}

        def find_one(self, filt):
            return self.find_by_id(filt.get("user_id", ""))

    return _R()


@pytest.fixture
def patched(monkeypatch):
    """Wire a fake DB + every repo accessor the walkouts/points handlers read.
    SEED nothing implicitly -- tests create the docs they assert on."""
    fake_db = FakeDB()

    from api.routers import walkouts as walkouts_module
    from api.routers import points as points_module

    from database.repositories.walkin_counter_repository import (
        WalkInCounterRepository,
    )
    from database.repositories.walkout_repository import WalkoutRepository
    from database.repositories.audit_repository import AuditRepository

    walkin_repo = WalkInCounterRepository(fake_db.get_collection("walk_in_counters"))
    walkout_repo = WalkoutRepository(fake_db.get_collection("walkouts"))
    audit_repo = AuditRepository(fake_db.get_collection("audit_logs"))

    # walkouts router accessors
    monkeypatch.setattr(walkouts_module, "get_db", lambda: fake_db)
    monkeypatch.setattr(
        walkouts_module, "get_walkin_counter_repository", lambda: walkin_repo
    )
    monkeypatch.setattr(walkouts_module, "get_user_repository", _fake_user_repo)
    monkeypatch.setattr(walkouts_module, "get_audit_repository", lambda: audit_repo)

    # points router accessors (for the D2 422-block acceptance)
    monkeypatch.setattr(points_module, "get_db", lambda: fake_db)
    monkeypatch.setattr(points_module, "get_audit_repository", lambda: audit_repo)
    monkeypatch.setattr(points_module, "get_user_repository", _fake_user_repo)
    monkeypatch.setattr(
        points_module, "get_walkout_repository", lambda: walkout_repo
    )
    monkeypatch.setattr(
        points_module, "get_walkin_counter_repository", lambda: walkin_repo
    )

    return {
        "db": fake_db,
        "walkin_repo": walkin_repo,
        "walkout_repo": walkout_repo,
        "audit_repo": audit_repo,
    }


def _hdr(user_id, roles, store_id=STORE):
    from api.routers.auth import create_access_token

    token = create_access_token(
        {
            "user_id": user_id,
            "username": user_id,
            "roles": roles,
            "store_ids": [store_id],
            "active_store_id": store_id,
        }
    )
    return {"Authorization": f"Bearer {token}"}


def _today():
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone(timedelta(hours=5, minutes=30))).date().isoformat()


def _tomorrow():
    from datetime import datetime, timedelta, timezone

    ist = timezone(timedelta(hours=5, minutes=30))
    return (datetime.now(ist).date() + timedelta(days=1)).isoformat()


def _yesterday():
    from datetime import datetime, timedelta, timezone

    ist = timezone(timedelta(hours=5, minutes=30))
    return (datetime.now(ist).date() - timedelta(days=1)).isoformat()


# ===========================================================================
# 1 -- Missing footfall BLOCKS the SC conversion score (the core correction)
# ===========================================================================


def test_missing_footfall_blocks_conversion_then_override_succeeds(
    client, patched
):
    """POST a daily scorecard with conversion=null for today when no walk-in
    footfall exists for the staff -> HTTP 422 mentioning footfall. Supplying an
    explicit numeric conversion (manager override) then saves."""
    hdr = _hdr("MGR", ["STORE_MANAGER"])
    scores_null = {
        "attendance": 9, "conversion": None, "task": 9, "visufit": 8,
        "punctuality": 10, "behaviour": 9, "kicker_1": 0, "kicker_2": 0,
        "reviews": 8,
    }
    payload = {"date": _today(), "staff_id": "S1", "scores": scores_null}
    resp = client.post(
        "/api/v1/incentive/points/daily", json=payload, headers=hdr
    )
    assert resp.status_code == 422, resp.text
    assert "footfall" in resp.json()["detail"].lower()

    # Manager override: an explicit conversion value bypasses the block.
    scores_override = dict(scores_null)
    scores_override["conversion"] = 15
    payload2 = {"date": _today(), "staff_id": "S1", "scores": scores_override}
    resp2 = client.post(
        "/api/v1/incentive/points/daily", json=payload2, headers=hdr
    )
    assert resp2.status_code == 201, resp2.text
    assert resp2.json()["conversion"] == 15


def test_footfall_present_autofills_and_saves(client, patched):
    """With footfall present, conversion=null auto-fills from the SC engine and
    the save succeeds (proves the block only fires when footfall is missing)."""
    patched["walkin_repo"].set_per_staff(
        store_id=STORE, staff_id="S1", walk_ins=10, updated_by="MGR",
        date_str=_today(),
    )
    hdr = _hdr("MGR", ["STORE_MANAGER"])
    scores = {
        "attendance": 9, "conversion": None, "task": 9, "visufit": 8,
        "punctuality": 10, "behaviour": 9, "kicker_1": 0, "kicker_2": 0,
        "reviews": 8,
    }
    resp = client.post(
        "/api/v1/incentive/points/daily",
        json={"date": _today(), "staff_id": "S1", "scores": scores},
        headers=hdr,
    )
    assert resp.status_code == 201, resp.text
    # walk_ins=10, walkouts=0, retro=0 -> 20
    assert resp.json()["conversion"] == 20


# ===========================================================================
# 2 -- Walk-in entry sets per_staff, updates log, leaves totals untouched
# ===========================================================================


def test_per_staff_entry_persists_and_logs(client, patched):
    hdr = _hdr("MGR", ["STORE_MANAGER"])
    repo = patched["walkin_repo"]

    r1 = client.patch(
        "/api/v1/walkouts/walkins/per-staff",
        json={"staff_id": "S1", "walk_ins": 8}, headers=hdr,
    )
    assert r1.status_code == 200, r1.text
    doc = repo.get_today(STORE, date_str=_today())
    assert doc["per_staff"]["S1"] == 8
    # pos auto-floor + manual-topup untouched (attribution is independent).
    assert doc.get("total", 0) == (
        doc.get("pos_auto_count", 0) + doc.get("manual_topup", 0)
    )
    assert len(doc["per_staff_log"]) == 1
    assert doc["per_staff_log"][0]["new_val"] == 8

    # Second update -> overwrite to 10, append a second log entry.
    r2 = client.patch(
        "/api/v1/walkouts/walkins/per-staff",
        json={"staff_id": "S1", "walk_ins": 10}, headers=hdr,
    )
    assert r2.status_code == 200, r2.text
    doc = repo.get_today(STORE, date_str=_today())
    assert doc["per_staff"]["S1"] == 10
    assert len(doc["per_staff_log"]) == 2
    assert doc["per_staff_log"][1]["old_val"] == 8
    assert doc["per_staff_log"][1]["new_val"] == 10

    # Third update -> 0 is VALID data (staff had no customers today).
    r3 = client.patch(
        "/api/v1/walkouts/walkins/per-staff",
        json={"staff_id": "S1", "walk_ins": 0}, headers=hdr,
    )
    assert r3.status_code == 200, r3.text
    doc = repo.get_today(STORE, date_str=_today())
    assert doc["per_staff"]["S1"] == 0
    assert len(doc["per_staff_log"]) == 3

    # Audit row written for the per-staff update.
    audit_docs = patched["db"].get_collection("audit_logs").docs
    assert any(a.get("action") == "walkin.per_staff_update" for a in audit_docs)


# ===========================================================================
# 3 -- Entry status transitions PENDING -> PARTIAL -> COMPLETE
# ===========================================================================


def test_entry_status_transitions(client, patched):
    hdr = _hdr("MGR", ["STORE_MANAGER"])

    s0 = client.get("/api/v1/walkouts/walkins/status", headers=hdr)
    assert s0.status_code == 200, s0.text
    body0 = s0.json()
    assert body0["status"] == "PENDING"
    # ACCOUNTANT is not a footfall role -> only S1, S2 are "expected".
    assert set(body0["staff_missing"]) == {"S1", "S2"}

    client.patch(
        "/api/v1/walkouts/walkins/per-staff",
        json={"staff_id": "S1", "walk_ins": 5}, headers=hdr,
    )
    s1 = client.get("/api/v1/walkouts/walkins/status", headers=hdr).json()
    assert s1["status"] == "PARTIAL"
    assert [r["staff_id"] for r in s1["staff_with_data"]] == ["S1"]
    assert s1["staff_missing"] == ["S2"]

    # walk_ins=0 still COUNTS as "has data" -> COMPLETE once both present.
    client.patch(
        "/api/v1/walkouts/walkins/per-staff",
        json={"staff_id": "S2", "walk_ins": 0}, headers=hdr,
    )
    s2 = client.get("/api/v1/walkouts/walkins/status", headers=hdr).json()
    assert s2["status"] == "COMPLETE"
    assert s2["staff_missing"] == []
    assert s2["total_walk_ins"] == 5


def test_walkins_today_carries_entry_status(client, patched):
    hdr = _hdr("MGR", ["STORE_MANAGER"])
    t0 = client.get("/api/v1/walkouts/walkins/today", headers=hdr).json()
    assert t0["entry_status"] == "PENDING"
    client.patch(
        "/api/v1/walkouts/walkins/per-staff",
        json={"staff_id": "S1", "walk_ins": 3}, headers=hdr,
    )
    t1 = client.get("/api/v1/walkouts/walkins/today", headers=hdr).json()
    assert t1["entry_status"] == "PARTIAL"


# ===========================================================================
# 4 -- conversion-feed emits null (not 0) for missing footfall
# ===========================================================================


def test_conversion_feed_null_on_missing_footfall(client, patched):
    """S1 has walkouts logged but NO walk-in entry -> conversion_score=null +
    footfall_missing=true. S2 has footfall -> a real numeric score."""
    repo = patched["walkout_repo"]
    today = _today()
    for _ in range(3):
        repo.create_walkout({
            "store_id": STORE, "date_str": today,
            "sales_person_id": "S1", "sales_person_name": "Staff One",
            "customer_name": "X", "mobile": "9000000001",
            "primary_walkout_reason": "BUDGET/PRICE",
        })
    # S2: 10 walk-ins, 2 walkouts -> (10-2)/10*20 = 16.
    patched["walkin_repo"].set_per_staff(
        store_id=STORE, staff_id="S2", walk_ins=10, updated_by="MGR",
        date_str=today,
    )
    for _ in range(2):
        repo.create_walkout({
            "store_id": STORE, "date_str": today,
            "sales_person_id": "S2", "sales_person_name": "Staff Two",
            "customer_name": "Y", "mobile": "9000000002",
            "primary_walkout_reason": "BRAND",
        })

    hdr = _hdr("MGR", ["STORE_MANAGER"])
    resp = client.get(
        f"/api/v1/walkouts/conversion-feed?date={today}", headers=hdr
    )
    assert resp.status_code == 200, resp.text
    items = {r["sales_person_id"]: r for r in resp.json()}
    assert items["S1"]["conversion_score"] is None
    assert items["S1"]["footfall_missing"] is True
    assert items["S2"]["conversion_score"] == 16.0
    assert items["S2"]["footfall_missing"] is False


# ===========================================================================
# 5 -- Conversion formula correctness (engine seam, NOT re-implemented)
# ===========================================================================


def test_conversion_formula_is_correct():
    from api.services import scorecard_engine as eng

    class _Walkouts:
        def __init__(self, n):
            self.n = n

        def list_walkouts(self, store_id, date_from, date_to, limit):
            if date_from == date_to:
                return [{"sales_person_id": "S1", "result": "PENDING"}
                        for _ in range(self.n)]
            return []

    class _Walkin:
        def __init__(self, n):
            self.n = n

        def get_today(self, store_id, date_str):
            return {"per_staff": {"S1": self.n}}

    # walk_ins=10, walkouts=3, retro=0 -> round((10-3)/10*20)=14
    assert eng.conversion_score(
        STORE, "2026-06-08", "S1",
        walkout_repo=_Walkouts(3), walkin_repo=_Walkin(10),
    ) == 14
    # walkouts > walk_ins -> clamp at 0 (not negative).
    assert eng.conversion_score(
        STORE, "2026-06-08", "S1",
        walkout_repo=_Walkouts(12), walkin_repo=_Walkin(10),
    ) == 0
    # full conversion: walkouts=0 -> 20.
    assert eng.conversion_score(
        STORE, "2026-06-08", "S1",
        walkout_repo=_Walkouts(0), walkin_repo=_Walkin(10),
    ) == 20
    # missing footfall -> None (NOT 0).
    assert eng.conversion_score(
        STORE, "2026-06-08", "S1",
        walkout_repo=_Walkouts(0), walkin_repo=_Walkin(0),
    ) is None


# ===========================================================================
# 6 -- RBAC: SALES_STAFF cannot set per-staff; cross-store manager blocked
# ===========================================================================


def test_rbac_sales_staff_cannot_set_per_staff(client, patched):
    staff_hdr = _hdr("S1", ["SALES_STAFF"])
    resp = client.patch(
        "/api/v1/walkouts/walkins/per-staff",
        json={"staff_id": "S2", "walk_ins": 99}, headers=staff_hdr,
    )
    assert resp.status_code == 403, resp.text


def test_rbac_store_manager_can_set_in_store_but_not_cross_store(client, patched):
    # In-store: OK.
    mgr = _hdr("MGR", ["STORE_MANAGER"], store_id=STORE)
    ok = client.patch(
        "/api/v1/walkouts/walkins/per-staff",
        json={"staff_id": "S1", "walk_ins": 4}, headers=mgr,
    )
    assert ok.status_code == 200, ok.text
    doc = patched["walkin_repo"].get_today(STORE, date_str=_today())
    assert doc["per_staff"]["S1"] == 4

    # Cross-store attempt: a STORE_MANAGER scoped to STORE cannot override the
    # store via the query param (only SUPERADMIN/ADMIN may), so the write lands
    # on THEIR store, never OTHER_STORE.
    cross = client.patch(
        "/api/v1/walkouts/walkins/per-staff?store_id=" + OTHER_STORE,
        json={"staff_id": "S9", "walk_ins": 50}, headers=mgr,
    )
    assert cross.status_code == 200, cross.text
    other_doc = patched["walkin_repo"].get_today(OTHER_STORE, date_str=_today())
    assert "S9" not in (other_doc.get("per_staff") or {})


# ===========================================================================
# 7 -- Future date blocked (IST) on both write + status
# ===========================================================================


def test_future_date_blocked(client, patched):
    mgr = _hdr("MGR", ["STORE_MANAGER"])
    w = client.patch(
        "/api/v1/walkouts/walkins/per-staff",
        json={"staff_id": "S1", "walk_ins": 3, "date_str": _tomorrow()},
        headers=mgr,
    )
    assert w.status_code == 422, w.text

    s = client.get(
        "/api/v1/walkouts/walkins/status?date=" + _tomorrow(), headers=mgr
    )
    assert s.status_code == 422, s.text

    # A past date (correction) IS allowed.
    p = client.patch(
        "/api/v1/walkouts/walkins/per-staff",
        json={"staff_id": "S1", "walk_ins": 2, "date_str": _yesterday()},
        headers=mgr,
    )
    assert p.status_code == 200, p.text


# ===========================================================================
# 8 -- POS auto-increment dedups; per-staff set does NOT touch pos_auto_count
# ===========================================================================


def test_pos_autoincrement_dedup_and_independence(patched):
    repo = patched["walkin_repo"]
    today = _today()
    repo.auto_increment(store_id=STORE, sales_person_id="S1",
                        mobile="9876543210", date_str=today)
    repo.auto_increment(store_id=STORE, sales_person_id="S1",
                        mobile="9876543210", date_str=today)
    doc = repo.get_today(STORE, date_str=today)
    # Same mobile twice in one day -> counted once.
    assert doc["per_staff"]["S1"] == 1
    assert doc["pos_auto_count"] == 1

    # Manager SETS the attribution to 5 -> overwrites per_staff[S1] but does NOT
    # reset pos_auto_count (the two paths are independent layers).
    repo.set_per_staff(store_id=STORE, staff_id="S1", walk_ins=5,
                       updated_by="MGR", date_str=today)
    doc = repo.get_today(STORE, date_str=today)
    assert doc["per_staff"]["S1"] == 5
    assert doc["pos_auto_count"] == 1
