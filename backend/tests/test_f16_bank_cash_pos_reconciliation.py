"""
IMS 2.0 - Feature #16 Bank / Cash / POS reconciliation -- intent tests.

Exercises the REAL BankReconciliationEngine + the route role/store gates against
a faithful in-memory fake Mongo (no network, no mongod). Covers:
  - integer-paise helpers (to_paise round-half-up, within_tolerance),
  - CASH trail from #23 till close vs bank deposit (match + paisa-exact variance),
  - POS digital trail vs settlement NET OF MDR (fee recorded, not flagged variance),
  - unmatched-in-books + unmatched-in-bank surfaced,
  - atomic soft-lock (two concurrent locks -> exactly one wins),
  - sign-off atomic + store-pinned (already-signed -> 409),
  - route RBAC: a cashier cannot run / sign off (403),
  - route store-scope: a cross-store actor is blocked (validate_store_access).

No whole-JSON substring asserts; every DB touch is a seeded fake. No emoji.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import bank_reconciliation as svc  # noqa: E402


# --------------------------------------------------------------------------- #
# Faithful in-memory fake Mongo (only the operators the engine uses).
# --------------------------------------------------------------------------- #
def _match(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in query.items():
        actual = doc.get(k)
        if isinstance(v, dict):
            if "$gte" in v and not (actual is not None and actual >= v["$gte"]):
                return False
            if "$lte" in v and not (actual is not None and actual <= v["$lte"]):
                return False
            if "$in" in v and actual not in v["$in"]:
                return False
        else:
            if actual != v:
                return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def limit(self, n):
        self._docs = self._docs[: int(n)]
        return self

    def __iter__(self):
        return iter(self._docs)


class _Coll:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def insert_one(self, doc):
        self.docs.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    def find(self, query=None, projection=None):
        return _Cursor([dict(d) for d in self.docs if _match(d, query or {})])

    def find_one(self, query=None, projection=None):
        for d in self.docs:
            if _match(d, query or {}):
                return dict(d)
        return None

    def find_one_and_update(self, query, update, return_document=None, **_kw):
        for d in self.docs:
            if _match(d, query or {}):
                # apply $set in place (so a second guarded call no longer matches)
                for kk, vv in (update.get("$set") or {}).items():
                    d[kk] = vv
                return dict(d)
        return None

    def create_index(self, *_a, **_k):
        return "idx"


class _FakeDB:
    def __init__(self):
        self._cols: Dict[str, _Coll] = {}

    def get_collection(self, name):
        return self._cols.setdefault(name, _Coll())


@pytest.fixture
def db():
    return _FakeDB()


@pytest.fixture
def engine(db):
    return svc.BankReconciliationEngine(db)


_MGR = {"user_id": "u-mgr", "roles": ["STORE_MANAGER"], "active_store_id": "BV-01"}
STORE = "BV-01"


# --------------------------------------------------------------------------- #
# Integer-paise helpers
# --------------------------------------------------------------------------- #
def test_to_paise_rounds_half_up():
    assert svc.to_paise("1234.56") == 123456
    assert svc.to_paise(0) == 0
    assert svc.to_paise(None) == 0
    assert svc.to_paise("garbage") == 0
    assert svc.to_paise("0.005") == 1  # half-up


def test_within_tolerance():
    assert svc.within_tolerance(100, 103, 5) is True
    assert svc.within_tolerance(100, 106, 5) is False
    assert svc.within_tolerance(100, 100, 0) is True


# --------------------------------------------------------------------------- #
# CASH trail (from #23 till close) + matching + paisa-exact variance
# --------------------------------------------------------------------------- #
def test_build_cash_expected_from_till_close(engine, db):
    db.get_collection("till_sessions").insert_one(
        {
            "session_id": "T1",
            "store_id": STORE,
            "session_date": "2026-06-10",
            "status": "LOCKED",
            "blind_count_paisa": 5000_00,
            "expected_cash_paisa": 4990_00,
        }
    )
    # a different store's close must NOT leak in
    db.get_collection("till_sessions").insert_one(
        {"session_id": "T2", "store_id": "BV-99", "session_date": "2026-06-10",
         "status": "LOCKED", "blind_count_paisa": 9999_00}
    )
    out = engine.build_cash_expected(STORE, "2026-06-10", "2026-06-10")
    assert len(out) == 1
    assert out[0]["expected_paise"] == 5000_00  # counted (deposit-bound), not system-expected
    assert out[0]["kind"] == svc.KIND_CASH


def test_cash_match_and_paisa_exact_variance(engine):
    expected = [{"kind": svc.KIND_CASH, "tender": "CASH", "ref_date": "2026-06-10",
                 "expected_paise": 5000_00}]
    bank = [{"line_id": "B1", "kind": svc.KIND_CASH, "amount_paise": 4999_50}]
    res = engine.match_trail(expected, bank, tolerance_paise=100, mdr_bps=0)
    assert len(res["matched"]) == 1
    assert res["matched"][0]["variance_paise"] == -50  # 4999_50 - 5000_00, integer-exact
    assert res["unmatched_in_books"] == []
    assert res["unmatched_in_bank"] == []
    assert res["totals"]["variance_paise"] == -50


def test_cash_unmatched_both_ways(engine):
    expected = [{"kind": svc.KIND_CASH, "tender": "CASH", "expected_paise": 5000_00}]
    bank = [{"line_id": "B1", "kind": svc.KIND_CASH, "amount_paise": 9000_00}]  # too far off
    res = engine.match_trail(expected, bank, tolerance_paise=100, mdr_bps=0)
    assert len(res["unmatched_in_books"]) == 1
    assert len(res["unmatched_in_bank"]) == 1
    assert res["reconciled"] is False


# --------------------------------------------------------------------------- #
# POS digital trail vs settlement NET OF MDR
# --------------------------------------------------------------------------- #
def test_digital_matches_net_of_mdr_fee(engine):
    # gross 10000.00 = 1,000,000 paise; MDR 2% (200 bps) -> fee 20000 -> net 980000.
    expected = [{"kind": svc.KIND_DIGITAL, "tender": "CARD", "expected_paise": 1000000}]
    bank = [{"line_id": "B1", "kind": svc.KIND_DIGITAL, "tender": "CARD", "amount_paise": 980000}]
    res = engine.match_trail(expected, bank, tolerance_paise=100, mdr_bps=200)
    assert len(res["matched"]) == 1
    m = res["matched"][0]
    assert m["mdr_fee_paise"] == 20000          # fee recorded explicitly
    assert m["expected_net_paise"] == 980000
    assert m["variance_paise"] == 0             # net matches -> NOT a variance
    assert res["totals"]["mdr_fee_paise"] == 20000


def test_build_pos_digital_excludes_cash(engine, monkeypatch):
    from api.services import tender_reconciliation as tr

    monkeypatch.setattr(
        tr, "reconcile_window",
        lambda *a, **k: {"by_mode": {
            "CASH": {"net": 5000.0, "count": 3},
            "CARD": {"net": 10000.0, "count": 2},
            "UPI": {"net": 0.0, "count": 0},
        }},
    )
    out = engine.build_pos_digital_expected(STORE, "2026-06-10", "2026-06-10")
    tenders = {o["tender"] for o in out}
    assert "CASH" not in tenders          # cash is the #23 trail, never a settlement
    assert "CARD" in tenders
    assert "UPI" not in tenders           # zero net is dropped
    card = next(o for o in out if o["tender"] == "CARD")
    assert card["expected_paise"] == 1000000


# --------------------------------------------------------------------------- #
# Atomic soft-lock + sign-off
# --------------------------------------------------------------------------- #
def _seed_run(db, run_id="BR-1", status=svc.STATUS_OPEN):
    db.get_collection(svc.RECON_COLLECTION).insert_one(
        {"_id": run_id, "run_id": run_id, "store_id": STORE, "status": status}
    )
    return run_id


def test_lock_is_atomic_one_winner(engine, db):
    rid = _seed_run(db)
    first = engine.acquire_lock(rid, _MGR)
    assert first is not None and first["status"] == svc.STATUS_LOCKED
    # second concurrent lock: the guarded filter (status:OPEN) no longer matches.
    second = engine.acquire_lock(rid, _MGR)
    assert second is None


def test_sign_off_atomic_and_store_pinned(engine, db):
    rid = _seed_run(db)
    res = engine.sign_off(rid, STORE, _MGR)
    assert res["ok"] is True and res["run"]["status"] == svc.STATUS_SIGNED_OFF
    # already signed off -> 409
    again = engine.sign_off(rid, STORE, _MGR)
    assert again["ok"] is False and again["http"] == 409
    # wrong store -> not found (cannot sign another store's run)
    rid2 = _seed_run(db, run_id="BR-2")
    wrong = engine.sign_off(rid2, "BV-99", _MGR)
    assert wrong["ok"] is False and wrong["http"] == 404


def test_run_reconciliation_persists_and_audits(engine, db, monkeypatch):
    from api.services import tender_reconciliation as tr

    monkeypatch.setattr(tr, "reconcile_window", lambda *a, **k: {"by_mode": {}})
    db.get_collection("till_sessions").insert_one(
        {"session_id": "T1", "store_id": STORE, "session_date": "2026-06-10",
         "status": "LOCKED", "blind_count_paisa": 5000_00}
    )
    db.get_collection(svc.BANK_LINES_COLLECTION).insert_one(
        {"line_id": "B1", "store_id": STORE, "value_date": "2026-06-10",
         "kind": svc.KIND_CASH, "amount_paise": 5000_00}
    )
    res = engine.run_reconciliation(STORE, "2026-06-10", "2026-06-10", 100, 0, _MGR)
    assert res["ok"] is True
    run = res["run"]
    assert run["status"] == svc.STATUS_OPEN
    assert run["cash"]["reconciled"] is True   # 5000 vs 5000, exact
    # persisted
    saved = db.get_collection(svc.RECON_COLLECTION).find_one({"_id": run["run_id"]})
    assert saved is not None


# --------------------------------------------------------------------------- #
# Route-level RBAC + store-scope
# --------------------------------------------------------------------------- #
def test_route_cashier_cannot_run(monkeypatch):
    from api.routers import bank_reconciliation as r
    from fastapi import HTTPException

    monkeypatch.setattr(r, "_get_db", lambda: _FakeDB())
    cashier = {"user_id": "u-c", "roles": ["SALES_CASHIER"], "active_store_id": STORE}
    body = r.RunBody(store_id=STORE, window_start="2026-06-10", window_end="2026-06-10")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(r.run_reconciliation(body, cashier))
    assert ei.value.status_code == 403


def test_route_cashier_cannot_sign_off(monkeypatch):
    from api.routers import bank_reconciliation as r
    from fastapi import HTTPException

    monkeypatch.setattr(r, "_get_db", lambda: _FakeDB())
    cashier = {"user_id": "u-c", "roles": ["CASHIER"], "active_store_id": STORE}
    with pytest.raises(HTTPException) as ei:
        asyncio.run(r.sign_off_reconciliation("BR-1", cashier))
    assert ei.value.status_code == 403


def test_route_cross_store_blocked(monkeypatch):
    from api.routers import bank_reconciliation as r
    from fastapi import HTTPException

    fake = _FakeDB()
    monkeypatch.setattr(r, "_get_db", lambda: fake)

    def _deny(store_id, user):
        raise HTTPException(status_code=403, detail="cross-store")

    monkeypatch.setattr(r, "validate_store_access", _deny)
    body = r.RunBody(store_id="BV-99", window_start="2026-06-10", window_end="2026-06-10")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(r.run_reconciliation(body, _MGR))
    assert ei.value.status_code == 403
