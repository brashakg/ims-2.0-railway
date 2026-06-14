"""
IMS 2.0 - F29 Skills-based rostering + optometrist coverage tests (intent-level)
================================================================================
Exercises the REAL roster_engine service + router against a faithful in-memory
fake Mongo (no network). Owner decision baked in: ALL stores are clinical and
the optometrist licence never expires -- so there is NO licence-expiry
machinery; only WHO is an optometrist + whether each shift has enough of them.

Maps to the F29 acceptance intents:
  * coverage math -- OK when optoms_rostered >= required, BREACH when below
  * staff-skills registry -- one upsert doc per employee marks optometrists
  * roster CRUD -- create/replace per store-week, edit, publish
  * coverage endpoint -- surfaces BREACH rows for an uncovered shift
  * required-optoms is an E2 setting (override respected)
  * store-scope (IDOR) -- a BV-1 actor cannot touch a BV-2 roster
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import roster_engine as svc  # noqa: E402

# ============================================================================
# Faithful in-memory fake Mongo
# ============================================================================


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in query.items():
        actual = doc.get(k)
        if isinstance(v, dict) and any(str(kk).startswith("$") for kk in v):
            for op, expected in v.items():
                if op == "$in" and actual not in expected:
                    return False
                if op == "$ne" and actual == expected:
                    return False
            continue
        if actual != v:
            return False
    return True


def _apply_update(doc: Dict[str, Any], update: Dict[str, Any], inserted: bool) -> None:
    for op, fields in update.items():
        if op == "$set":
            for kk, vv in fields.items():
                doc[kk] = vv
        elif op == "$setOnInsert" and inserted:
            for kk, vv in fields.items():
                doc.setdefault(kk, vv)
        elif op == "$push":
            for kk, vv in fields.items():
                doc.setdefault(kk, []).append(vv)


class FakeCollection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []
        self._n = 0

    def insert_one(self, doc):
        if doc.get("_id") and any(d.get("_id") == doc["_id"] for d in self.docs):
            from pymongo.errors import DuplicateKeyError

            raise DuplicateKeyError(f"dup _id {doc['_id']}")
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find_one(self, query, projection=None):
        for d in self.docs:
            if _matches(d, query):
                return dict(d)
        return None

    def find(self, query=None, projection=None):
        return [dict(d) for d in self.docs if _matches(d, query or {})]

    def find_one_and_update(
        self, query, update, return_document=None, upsert=False, **_kw
    ):
        for d in self.docs:
            if _matches(d, query):
                _apply_update(d, update, inserted=False)
                return dict(d)
        if upsert:
            doc: Dict[str, Any] = {}
            for k, v in query.items():
                if not (
                    isinstance(v, dict) and any(str(kk).startswith("$") for kk in v)
                ):
                    doc[k] = v
            doc.setdefault("_id", f"oid-{self._n}")
            self._n += 1
            _apply_update(doc, update, inserted=True)
            self.docs.append(dict(doc))
            return dict(doc)
        return None

    def create_index(self, *a, **k):
        return "idx"


class FakeDB:
    def __init__(self):
        self._c: Dict[str, FakeCollection] = {}
        self.is_connected = True

    def get_collection(self, name):
        return self._c.setdefault(name, FakeCollection())

    def __getitem__(self, name):
        return self.get_collection(name)


@pytest.fixture()
def db() -> FakeDB:
    return FakeDB()


def _mgr(uid="M1", store="BV-1"):
    return {
        "user_id": uid,
        "roles": ["STORE_MANAGER"],
        "store_ids": [store],
        "active_store_id": store,
    }


def _staff(uid="S1", store="BV-1"):
    return {
        "user_id": uid,
        "roles": ["SALES_STAFF"],
        "store_ids": [store],
        "active_store_id": store,
    }


# ============================================================================
# Pure coverage math
# ============================================================================


def _shift(emp, store="BV-1", date="2026-06-15", shift="MORNING"):
    return {"employee_id": emp, "store_id": store, "date": date, "shift": shift}


def test_coverage_ok_when_optom_present():
    shifts = [_shift("E1"), _shift("E2")]
    skills = {"E1": {"is_optometrist": True}, "E2": {"is_optometrist": False}}
    cov = svc.compute_coverage(shifts, skills, 1)
    assert len(cov) == 1
    assert cov[0]["optoms_rostered"] == 1 and cov[0]["rostered_total"] == 2
    assert cov[0]["status"] == svc.COVERAGE_OK


def test_coverage_breach_when_no_optom():
    shifts = [_shift("E2"), _shift("E3")]
    skills = {"E2": {"is_optometrist": False}, "E3": {"is_optometrist": False}}
    cov = svc.compute_coverage(shifts, skills, 1)
    assert cov[0]["status"] == svc.COVERAGE_BREACH and cov[0]["optoms_rostered"] == 0


def test_coverage_breach_when_below_required_two():
    shifts = [_shift("E1"), _shift("E2")]
    skills = {"E1": {"is_optometrist": True}, "E2": {"is_optometrist": False}}
    cov = svc.compute_coverage(shifts, skills, 2)  # need 2 optoms, only 1
    assert cov[0]["status"] == svc.COVERAGE_BREACH


def test_coverage_multi_store_multi_shift_independent():
    shifts = [
        _shift("E1", store="BV-1", shift="MORNING"),  # optom -> OK
        _shift("E2", store="BV-1", shift="EVENING"),  # no optom -> BREACH
        _shift("E1", store="BV-2", shift="MORNING"),  # optom -> OK
    ]
    skills = {"E1": {"is_optometrist": True}, "E2": {"is_optometrist": False}}
    cov = svc.compute_coverage(shifts, skills, 1)
    by = {(c["store_id"], c["shift"]): c["status"] for c in cov}
    assert by[("BV-1", "MORNING")] == "OK"
    assert by[("BV-1", "EVENING")] == "BREACH"
    assert by[("BV-2", "MORNING")] == "OK"


def test_coverage_breaches_filter():
    shifts = [_shift("E1", shift="MORNING"), _shift("E2", shift="EVENING")]
    skills = {"E1": {"is_optometrist": True}, "E2": {"is_optometrist": False}}
    breaches = svc.coverage_breaches(svc.compute_coverage(shifts, skills, 1))
    assert len(breaches) == 1 and breaches[0]["shift"] == "EVENING"


def test_validate_roster_payload_rules():
    with pytest.raises(svc.RosterError) as e:
        svc.validate_roster_payload({"week_start": "2026-06-15"})  # no store
    assert e.value.status == 422
    with pytest.raises(svc.RosterError):
        svc.validate_roster_payload({"store_id": "BV-1"})  # no week_start
    with pytest.raises(svc.RosterError):
        svc.validate_roster_payload(
            {
                "store_id": "BV-1",
                "week_start": "x",
                "entries": [{"employee_id": "E1", "date": "d", "shift": "NOON"}],
            }
        )
    ok = svc.validate_roster_payload(
        {
            "store_id": "BV-1",
            "week_start": "2026-06-15",
            "entries": [{"employee_id": "E1", "date": "d", "shift": "morning"}],
        }
    )
    assert ok["entries"][0]["shift"] == "MORNING" and ok["status"] == "DRAFT"


# ============================================================================
# Staff-skills registry
# ============================================================================


def test_staff_skill_upsert_and_list(db):
    svc.upsert_staff_skill(
        db,
        "E1",
        {"is_optometrist": True, "skills": ["fitting"], "store_id": "BV-1"},
        actor=_mgr(),
    )
    rec = svc.get_staff_skill(db, "E1")
    assert rec["is_optometrist"] is True and rec["skills"] == ["fitting"]
    # re-upsert flips the flag (one doc per employee)
    svc.upsert_staff_skill(
        db, "E1", {"is_optometrist": False, "store_id": "BV-1"}, actor=_mgr()
    )
    assert svc.get_staff_skill(db, "E1")["is_optometrist"] is False
    assert len(db.get_collection("staff_skills").docs) == 1
    rows = svc.list_staff_skills(db, "BV-1")
    assert len(rows) == 1


# ============================================================================
# Roster CRUD + coverage
# ============================================================================


def _roster(db, store="BV-1", week="2026-06-15", entries=None, status="DRAFT"):
    return svc.create_or_replace_roster(
        db,
        {
            "store_id": store,
            "week_start": week,
            "status": status,
            "entries": (
                entries
                if entries is not None
                else [{"employee_id": "E1", "date": "2026-06-15", "shift": "MORNING"}]
            ),
        },
        actor=_mgr(store=store),
    )


def test_roster_create_then_replace_same_store_week(db):
    r1 = _roster(db)
    assert r1["roster_id"].startswith("RST-") and r1["status"] == "DRAFT"
    r2 = _roster(
        db,
        entries=[{"employee_id": "E9", "date": "2026-06-16", "shift": "EVENING"}],
        status="PUBLISHED",
    )
    # same store-week -> replaced, not a 2nd doc
    assert r2["roster_id"] == r1["roster_id"]
    assert r2["status"] == "PUBLISHED" and r2["entries"][0]["employee_id"] == "E9"
    assert len(db.get_collection("rosters").docs) == 1


def test_roster_update_publish(db):
    r = _roster(db)
    upd = svc.update_roster(db, r["roster_id"], {"status": "PUBLISHED"}, actor=_mgr())
    assert upd["status"] == "PUBLISHED"


def test_update_unknown_roster_404(db):
    with pytest.raises(svc.RosterError) as e:
        svc.update_roster(db, "RST-GHOST", {"status": "PUBLISHED"}, actor=_mgr())
    assert e.value.status == 404


def test_coverage_for_roster_flags_breach(db):
    svc.upsert_staff_skill(db, "E1", {"is_optometrist": True}, actor=_mgr())
    svc.upsert_staff_skill(db, "E2", {"is_optometrist": False}, actor=_mgr())
    r = _roster(
        db,
        entries=[
            {"employee_id": "E1", "date": "2026-06-15", "shift": "MORNING"},  # covered
            {
                "employee_id": "E2",
                "date": "2026-06-15",
                "shift": "EVENING",
            },  # uncovered
        ],
    )
    cov = svc.coverage_for_roster(db, r, 1)
    by = {c["shift"]: c["status"] for c in cov}
    assert by["MORNING"] == "OK" and by["EVENING"] == "BREACH"


def test_engine_db_absent_failsoft():
    with pytest.raises(svc.RosterError) as e:
        svc.upsert_staff_skill(None, "E1", {}, actor=_mgr())
    assert e.value.status == 503
    assert svc.list_staff_skills(None) == []
    assert svc.get_roster(None, roster_id="x") is None
    svc.ensure_indexes(None)  # no raise


# ============================================================================
# ROUTER -- role gates, store-scope (IDOR), E2 required-optoms
# ============================================================================


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_router_create_roster_403_for_sales_staff(db, monkeypatch):
    from fastapi import HTTPException
    from api.routers import roster as r

    monkeypatch.setattr(r, "_get_db", lambda: db)
    monkeypatch.setattr(r, "validate_store_access", lambda sid, u: True)
    body = r.RosterBody(store_id="BV-1", week_start="2026-06-15", entries=[])
    with pytest.raises(HTTPException) as exc:
        _run(r.create_roster(body, current_user=_staff()))
    assert exc.value.status_code == 403


def test_router_coverage_403_cross_store(db, monkeypatch):
    from fastapi import HTTPException
    from api.routers import roster as r

    monkeypatch.setattr(r, "_get_db", lambda: db)
    rec = _roster(db, store="BV-2")
    with pytest.raises(HTTPException) as exc:
        _run(r.roster_coverage(rec["roster_id"], current_user=_mgr("M9", "BV-1")))
    assert exc.value.status_code == 403


def test_router_coverage_uses_e2_required_optoms(db, monkeypatch):
    from api.routers import roster as r

    monkeypatch.setattr(r, "_get_db", lambda: db)
    monkeypatch.setattr(r, "validate_store_access", lambda sid, u: True)
    # one optometrist rostered
    svc.upsert_staff_skill(db, "E1", {"is_optometrist": True}, actor=_mgr())
    rec = _roster(
        db, entries=[{"employee_id": "E1", "date": "2026-06-15", "shift": "MORNING"}]
    )
    # E2 says require 2 optoms -> the single optom is now a BREACH
    monkeypatch.setattr(r, "_required_optoms", lambda store_id: 2)
    out = _run(r.roster_coverage(rec["roster_id"], current_user=_mgr()))
    assert out["required_optometrists"] == 2
    assert out["breaches"] and out["breaches"][0]["status"] == "BREACH"


def test_router_put_staff_skill_manager_only(db, monkeypatch):
    from fastapi import HTTPException
    from api.routers import roster as r

    monkeypatch.setattr(r, "_get_db", lambda: db)
    monkeypatch.setattr(r, "validate_store_access", lambda sid, u: True)
    body = r.StaffSkillBody(is_optometrist=True)
    with pytest.raises(HTTPException) as exc:
        _run(r.put_staff_skill("E1", body, current_user=_staff()))
    assert exc.value.status_code == 403
    # manager can
    out = _run(r.put_staff_skill("E1", body, current_user=_mgr()))
    assert out["is_optometrist"] is True
