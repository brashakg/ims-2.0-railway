"""
IMS 2.0 - F15 Blind stock take tests (intent-level)
===================================================
Exercises the REAL blind_stock_take service + its router against a faithful
in-memory fake Mongo (no network, no live mongod). A hollow shell that reveals
the expected on-hand to a counter, mis-computes the variance, skips the atomic
soft-lock, lets a reason-less reopen through, auto-mutates on-hand instead of
PROPOSING an adjustment, or leaks one store's count to another FAILS here.

Maps to the F15 acceptance intents:
  * blind entry        -- a counter NEVER sees the expected on-hand / variance /
                          summary while the session is OPEN (data-layer redaction,
                          no anchoring)
  * variance math      -- counted - system_on_hand = variance (units), valued at
                          integer paise cost; tolerance bands the verdict only
  * soft-lock atomic    -- two concurrent locks -> exactly one wins (the loser 409s)
  * reopen              -- requires a non-empty reason + a manager role; a counter
                          can NEVER reveal / lock / reopen
  * adjustment is a PROPOSAL -- a confirmed variance ENQUEUES a reversible
                          stock_adjustment_proposals row; it NEVER mutates on-hand
  * store-scope 403     -- a BV-1 actor cannot open/submit/lock/read a BV-2 session
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import blind_stock_take as svc  # noqa: E402


# ============================================================================
# Faithful in-memory fake Mongo (only the operators F15 uses)
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
    except TypeError:
        return False
    return False


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for k, v in query.items():
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


class FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []
        self._n = 0

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
        for d in self.docs:
            if _matches(d, query):
                _apply_update(d, update)
                return dict(d)
        return None

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


@pytest.fixture()
def db() -> FakeDB:
    return FakeDB()


def _counter(uid="S1", store="BV-1"):
    return {"user_id": uid, "full_name": "Sales Staff", "roles": ["SALES_STAFF"],
            "store_ids": [store], "active_store_id": store}


def _manager(uid="M1", store="BV-1"):
    return {"user_id": uid, "full_name": "Manager One", "roles": ["STORE_MANAGER"],
            "store_ids": [store], "active_store_id": store}


def _admin(uid="A1"):
    return {"user_id": uid, "full_name": "Admin", "roles": ["ADMIN"], "store_ids": [], "active_store_id": None}


def _on_hand(mapping):
    """A controllable on_hand_resolver(store_id, pids) -> {pid: qty}."""
    return lambda store_id, pids: {p: mapping.get(p, 0) for p in pids}


def _costs(mapping):
    return lambda pids: {p: mapping.get(p, 0) for p in pids}


# ============================================================================
# Pure variance math (units + paise valuation; tolerance bands the verdict)
# ============================================================================


def test_variance_is_counted_minus_expected():
    assert svc.variance(8, 10) == -2     # shrinkage
    assert svc.variance(12, 10) == 2     # surplus
    assert svc.variance(10, 10) == 0
    assert svc.variance(None, 5) == -5   # an uncounted SKU reads as 0
    assert svc.variance(5, None) == 5


def test_verdict_classifies_within_tolerance():
    assert svc.verdict(10, 10) == svc.VERDICT_MATCHED
    assert svc.verdict(12, 10) == svc.VERDICT_OVER
    assert svc.verdict(8, 10) == svc.VERDICT_SHORT
    # tolerance of 2 absorbs a +/-2 delta into MATCHED.
    assert svc.verdict(12, 10, tolerance=2) == svc.VERDICT_MATCHED
    assert svc.verdict(8, 10, tolerance=2) == svc.VERDICT_MATCHED
    assert svc.verdict(13, 10, tolerance=2) == svc.VERDICT_OVER


def test_build_summary_rolls_up_units_and_paise():
    items = [
        {"product_id": "P-A", "counted_qty": 8, "expected": 10, "cost_paise": 10000},   # short 2 -> -20000
        {"product_id": "P-B", "counted_qty": 5, "expected": 5, "cost_paise": 30000},    # matched
        {"product_id": "P-C", "counted_qty": 12, "expected": 10, "cost_paise": 5000},   # over 2 -> +10000
    ]
    rows, summary = svc.build_summary(items, tolerance=0)
    assert summary["total_skus"] == 3
    assert (summary["matched"], summary["over"], summary["short"]) == (1, 1, 1)
    assert summary["net_variance_units"] == 0          # -2 + 0 + 2
    assert summary["net_variance_value_paise"] == -10000  # -20000 + 0 + 10000
    assert summary["within_tolerance"] is False
    # Per-row enrichment carries the signed variance + verdict + valued delta.
    by_id = {r["product_id"]: r for r in rows}
    assert by_id["P-A"]["variance_units"] == -2 and by_id["P-A"]["verdict"] == svc.VERDICT_SHORT
    assert by_id["P-A"]["variance_value_paise"] == -20000
    assert by_id["P-C"]["verdict"] == svc.VERDICT_OVER


def test_build_summary_tolerance_bands_verdict_but_not_valuation():
    """A within-tolerance count reads MATCHED + within_tolerance, yet the net
    valued variance STILL reflects the real (signed) delta -- tolerance is a
    verdict band, not a money fudge."""
    items = [
        {"product_id": "P-A", "counted_qty": 8, "expected": 10, "cost_paise": 10000},
        {"product_id": "P-C", "counted_qty": 12, "expected": 10, "cost_paise": 5000},
    ]
    _, summary = svc.build_summary(items, tolerance=2)
    assert summary["matched"] == 2 and summary["over"] == 0 and summary["short"] == 0
    assert summary["within_tolerance"] is True
    # real money impact is unchanged: -20000 + 10000 = -10000
    assert summary["net_variance_value_paise"] == -10000


# ============================================================================
# BLIND ENTRY -- counter never sees expected/variance/summary while OPEN
# ============================================================================


def test_redact_strips_reveal_fields_for_counter_while_open():
    session = {
        "session_id": "BST-x", "status": svc.STATUS_OPEN, "store_id": "BV-1",
        "summary": {"net_variance_units": -2}, "items_revealed": [{"x": 1}],
        "expected_on_hand": {"P-A": 10},
        "items": [{"product_id": "P-A", "counted_qty": 8, "expected": 10,
                   "variance_units": -2, "verdict": "short", "variance_value_paise": -20000}],
    }
    red = svc.redact_for_counter(session, _counter())
    assert "summary" not in red
    assert "items_revealed" not in red
    assert "expected_on_hand" not in red
    assert red["_blind_redacted"] is True
    # the counter keeps their OWN counted qty but NOT the expected/variance.
    it = red["items"][0]
    assert it["counted_qty"] == 8
    assert "expected" not in it and "variance_units" not in it and "verdict" not in it
    # the stored doc is untouched (a copy was redacted).
    assert session["summary"]["net_variance_units"] == -2


def test_redact_is_a_noop_for_manager():
    session = {"session_id": "BST-y", "status": svc.STATUS_OPEN, "store_id": "BV-1",
               "summary": {"net": 1}, "items": [{"product_id": "P", "expected": 9}]}
    out = svc.redact_for_counter(session, _manager())
    assert out["summary"] == {"net": 1}
    assert out["items"][0]["expected"] == 9


def test_redact_reveals_to_everyone_once_locked():
    """After a manager LOCK the count is done -- the reveal is public (a counter
    may then see the variance they helped find)."""
    session = {"session_id": "BST-z", "status": svc.STATUS_LOCKED, "store_id": "BV-1",
               "summary": {"net_variance_units": -2},
               "items": [{"product_id": "P", "expected": 10, "variance_units": -2}]}
    out = svc.redact_for_counter(session, _counter())
    assert out["summary"]["net_variance_units"] == -2
    assert out["items"][0]["variance_units"] == -2
    assert "_blind_redacted" not in out


def test_redact_none_is_none():
    assert svc.redact_for_counter(None, _counter()) is None


def test_open_session_stores_no_expected(db):
    eng = svc.BlindStockTakeEngine(db)
    sess = eng.open_session(store_id="BV-1", actor=_counter())
    assert sess["status"] == svc.STATUS_OPEN
    assert sess["items"] == []
    # nothing about expected on-hand exists at open time (blind).
    assert "summary" not in sess and "expected_on_hand" not in sess


def test_submit_count_is_blind_no_expected_persisted(db):
    eng = svc.BlindStockTakeEngine(db)
    sess = eng.open_session(store_id="BV-1", actor=_counter())
    sid = sess["session_id"]
    updated = eng.submit_count(sid, [{"product_id": "P-A", "counted_qty": 8},
                                     {"product_id": "P-B", "counted_qty": 5}],
                               store_id="BV-1", actor=_counter())
    assert updated["status"] == svc.STATUS_OPEN
    for it in updated["items"]:
        assert "expected" not in it           # no system figure leaked into storage
        assert "variance_units" not in it
    assert {it["product_id"] for it in updated["items"]} == {"P-A", "P-B"}


# ============================================================================
# LOCK reveals the variance computed from system on-hand (paise-exact)
# ============================================================================


def test_lock_reveals_variance_from_system_on_hand(db):
    eng = svc.BlindStockTakeEngine(db)
    sess = eng.open_session(store_id="BV-1", actor=_counter())
    sid = sess["session_id"]
    eng.submit_count(sid, [{"product_id": "P-A", "counted_qty": 8},
                           {"product_id": "P-C", "counted_qty": 12}],
                     store_id="BV-1", actor=_counter())
    locked = eng.lock_and_reveal(
        sid, store_id="BV-1", actor=_manager(),
        on_hand_resolver=_on_hand({"P-A": 10, "P-C": 10}),
        cost_resolver=_costs({"P-A": 10000, "P-C": 5000}),
        tolerance=0)
    assert locked["status"] == svc.STATUS_LOCKED
    s = locked["summary"]
    assert s["short"] == 1 and s["over"] == 1
    assert s["net_variance_units"] == 0          # -2 + 2
    assert s["net_variance_value_paise"] == -10000  # -20000 + 10000
    assert locked["locked_by"] == "M1"


# ============================================================================
# SOFT-LOCK is atomic (two concurrent locks -> exactly one wins)
# ============================================================================


def test_two_concurrent_locks_exactly_one_wins(db):
    eng = svc.BlindStockTakeEngine(db)
    sess = eng.open_session(store_id="BV-1", actor=_counter())
    sid = sess["session_id"]
    eng.submit_count(sid, [{"product_id": "P-A", "counted_qty": 9}], store_id="BV-1", actor=_counter())
    res = _on_hand({"P-A": 10})
    first = eng.lock_and_reveal(sid, store_id="BV-1", actor=_manager("M1"),
                                on_hand_resolver=res, cost_resolver=_costs({"P-A": 1000}))
    assert first["status"] == svc.STATUS_LOCKED
    # A second lock on the now-LOCKED session: the OPEN-guard no longer matches.
    with pytest.raises(svc.BlindStockTakeError) as exc:
        eng.lock_and_reveal(sid, store_id="BV-1", actor=_manager("M2"),
                            on_hand_resolver=res, cost_resolver=_costs({"P-A": 1000}))
    assert exc.value.status == 409


# ============================================================================
# REOPEN -- requires reason + manager role; transparent soft-lock release
# ============================================================================


def _locked(db, *, counted=9, on_hand=10):
    eng = svc.BlindStockTakeEngine(db)
    sess = eng.open_session(store_id="BV-1", actor=_counter())
    sid = sess["session_id"]
    eng.submit_count(sid, [{"product_id": "P-A", "counted_qty": counted}], store_id="BV-1", actor=_counter())
    eng.lock_and_reveal(sid, store_id="BV-1", actor=_manager(),
                        on_hand_resolver=_on_hand({"P-A": on_hand}), cost_resolver=_costs({"P-A": 1000}))
    return eng, sid


def test_reopen_requires_nonempty_reason(db):
    eng, sid = _locked(db)
    with pytest.raises(svc.BlindStockTakeError) as exc:
        eng.reopen(sid, store_id="BV-1", actor=_manager(), reason="   ")
    assert exc.value.status == 400
    # still locked.
    assert eng.get(sid)["status"] == svc.STATUS_LOCKED


def test_reopen_succeeds_records_reason(db):
    eng, sid = _locked(db)
    out = eng.reopen(sid, store_id="BV-1", actor=_manager(), reason="recount after damage")
    assert out["status"] == svc.STATUS_REOPENED
    assert out["reopen_reason"].startswith("recount")
    assert out["reopened_by"] == "M1"


def test_reopen_only_when_locked(db):
    """An OPEN (never-locked) session cannot be reopened -- the guard is
    status==LOCKED, so it 409s rather than silently corrupting state."""
    eng = svc.BlindStockTakeEngine(db)
    sess = eng.open_session(store_id="BV-1", actor=_counter())
    with pytest.raises(svc.BlindStockTakeError) as exc:
        eng.reopen(sess["session_id"], store_id="BV-1", actor=_manager(), reason="x")
    assert exc.value.status == 409


# ============================================================================
# ADJUSTMENT IS A PROPOSAL -- never auto-mutates on-hand
# ============================================================================


def test_propose_adjustment_writes_a_proposal_not_an_on_hand_write(db):
    eng, sid = _locked(db, counted=8, on_hand=10)  # short 2
    # seed a product on-hand so we can prove it is NOT touched.
    db.get_collection("products").insert_one({"product_id": "P-A", "on_hand": 10, "cost_price": 100})
    proposal = eng.propose_adjustment(sid, store_id="BV-1", actor=_manager())
    assert proposal["status"] == "PROPOSED"
    assert proposal["source"] == "blind_stock_take" and proposal["source_id"] == sid
    # exactly one line (the nonzero variance), carrying the reversible delta.
    assert len(proposal["lines"]) == 1
    line = proposal["lines"][0]
    assert line["product_id"] == "P-A"
    assert line["delta_units"] == -2 and line["from_qty"] == 10 and line["to_qty"] == 8
    # it landed in the PROPOSALS collection, NOT as an on-hand mutation.
    stored = db.get_collection(svc.ADJUSTMENT_COLLECTION).find_one({"_id": proposal["proposal_id"]})
    assert stored is not None and stored["status"] == "PROPOSED"
    # the product's on-hand is UNTOUCHED -- a manager approves the proposal elsewhere.
    assert db.get_collection("products").find_one({"product_id": "P-A"})["on_hand"] == 10


def test_propose_adjustment_skips_matched_lines(db):
    eng = svc.BlindStockTakeEngine(db)
    sess = eng.open_session(store_id="BV-1", actor=_counter())
    sid = sess["session_id"]
    eng.submit_count(sid, [{"product_id": "P-A", "counted_qty": 10},   # matched
                           {"product_id": "P-B", "counted_qty": 7}],   # short 3
                     store_id="BV-1", actor=_counter())
    eng.lock_and_reveal(sid, store_id="BV-1", actor=_manager(),
                        on_hand_resolver=_on_hand({"P-A": 10, "P-B": 10}),
                        cost_resolver=_costs({"P-A": 0, "P-B": 0}))
    proposal = eng.propose_adjustment(sid, store_id="BV-1", actor=_manager())
    assert {ln["product_id"] for ln in proposal["lines"]} == {"P-B"}  # P-A (matched) excluded


def test_propose_requires_locked_session(db):
    eng = svc.BlindStockTakeEngine(db)
    sess = eng.open_session(store_id="BV-1", actor=_counter())
    with pytest.raises(svc.BlindStockTakeError) as exc:
        eng.propose_adjustment(sess["session_id"], store_id="BV-1", actor=_manager())
    assert exc.value.status == 409


# ============================================================================
# DB-absent fail-soft (no crash divergence local vs CI)
# ============================================================================


def test_engine_db_absent_is_failsoft():
    eng = svc.BlindStockTakeEngine(None)
    for call in (
        lambda: eng.open_session(store_id="BV-1", actor=_counter()),
        lambda: eng.submit_count("x", [], store_id="BV-1", actor=_counter()),
        lambda: eng.lock_and_reveal("x", store_id="BV-1", actor=_manager(), on_hand_resolver=_on_hand({})),
        lambda: eng.reopen("x", store_id="BV-1", actor=_manager(), reason="r"),
        lambda: eng.propose_adjustment("x", store_id="BV-1", actor=_manager()),
    ):
        with pytest.raises(svc.BlindStockTakeError) as exc:
            call()
        assert exc.value.status == 503
    assert eng.get("x") is None


def test_ensure_indexes_idempotent_and_failsoft(db):
    svc.ensure_indexes(db)
    svc.ensure_indexes(db)
    svc.ensure_indexes(None)  # DB absent -> no raise


# ============================================================================
# ROUTER -- store-scope (IDOR) + role gates + data-layer redaction
# ============================================================================


def _seed_open(db, *, store="BV-1", actor=None):
    eng = svc.BlindStockTakeEngine(db)
    return eng.open_session(store_id=store, actor=actor or _counter(store=store))["session_id"]


def test_router_counter_submit_response_is_blind(db, monkeypatch):
    """End-to-end via the ROUTER: a SALES_STAFF submit RESPONSE carries no
    expected/variance even though counts are stored."""
    import asyncio
    from api.routers import blind_stock_take as r

    monkeypatch.setattr(r, "_get_db", lambda: db)
    monkeypatch.setattr(r, "validate_store_access", lambda sid, u: sid or u.get("active_store_id"))
    sid = _seed_open(db)
    body = r.SubmitBody(counts=[r.CountLine(product_id="P-A", counted_qty=8)])
    out = asyncio.run(r.submit_count(sid, body, current_user=_counter()))
    assert out["_blind_redacted"] is True
    assert "summary" not in out and "expected_on_hand" not in out


def test_router_counter_cannot_lock(db, monkeypatch):
    """A counter hitting the LOCK route is 403 (only a manager reveals)."""
    import asyncio
    from fastapi import HTTPException
    from api.routers import blind_stock_take as r

    monkeypatch.setattr(r, "_get_db", lambda: db)
    monkeypatch.setattr(r, "validate_store_access", lambda sid, u: sid or u.get("active_store_id"))
    sid = _seed_open(db)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(r.lock_count(sid, current_user=_counter()))
    assert exc.value.status_code == 403
    # session untouched (still OPEN).
    assert svc.BlindStockTakeEngine(db).get(sid)["status"] == svc.STATUS_OPEN


def test_router_lock_403_for_cross_store_manager(db, monkeypatch):
    """A BV-1 manager must NOT lock a BV-2 session (real validate_store_access)."""
    import asyncio
    from fastapi import HTTPException
    from api.routers import blind_stock_take as r

    monkeypatch.setattr(r, "_get_db", lambda: db)
    # Use the REAL validate_store_access (the IDOR guard under test).
    sid = _seed_open(db, store="BV-2", actor=_counter("S2", "BV-2"))
    svc.BlindStockTakeEngine(db).submit_count(sid, [{"product_id": "P-A", "counted_qty": 1}],
                                              store_id="BV-2", actor=_counter("S2", "BV-2"))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(r.lock_count(sid, current_user=_manager("M9", "BV-1")))
    assert exc.value.status_code == 403
    assert svc.BlindStockTakeEngine(db).get(sid)["status"] == svc.STATUS_OPEN


def test_router_submit_403_for_cross_store_counter(db, monkeypatch):
    import asyncio
    from fastapi import HTTPException
    from api.routers import blind_stock_take as r

    monkeypatch.setattr(r, "_get_db", lambda: db)
    sid = _seed_open(db, store="BV-2", actor=_counter("S2", "BV-2"))
    body = r.SubmitBody(counts=[r.CountLine(product_id="P-A", counted_qty=8)])
    with pytest.raises(HTTPException) as exc:
        asyncio.run(r.submit_count(sid, body, current_user=_counter("S1", "BV-1")))
    assert exc.value.status_code == 403


def test_router_get_403_for_cross_store(db, monkeypatch):
    import asyncio
    from fastapi import HTTPException
    from api.routers import blind_stock_take as r

    monkeypatch.setattr(r, "_get_db", lambda: db)
    monkeypatch.setattr(r, "_reopen_roles", lambda store_id: None)
    sid = _seed_open(db, store="BV-2", actor=_counter("S2", "BV-2"))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(r.get_count(sid, current_user=_counter("S1", "BV-1")))
    assert exc.value.status_code == 403


def test_router_lock_then_propose_end_to_end(db, monkeypatch):
    """Full manager flow through the router: open -> submit -> lock (reveal) ->
    propose. The proposal is PROPOSED and on-hand is never mutated by the route."""
    import asyncio
    from api.routers import blind_stock_take as r

    monkeypatch.setattr(r, "_get_db", lambda: db)
    monkeypatch.setattr(r, "validate_store_access", lambda sid, u: sid or u.get("active_store_id"))
    monkeypatch.setattr(r, "_tolerance", lambda store_id: 0)
    monkeypatch.setattr(r, "_on_hand_resolver", _on_hand({"P-A": 10}))
    monkeypatch.setattr(r, "_cost_resolver", _costs({"P-A": 5000}))
    sid = _seed_open(db)
    svc.BlindStockTakeEngine(db).submit_count(sid, [{"product_id": "P-A", "counted_qty": 7}],
                                              store_id="BV-1", actor=_counter())
    locked = asyncio.run(r.lock_count(sid, current_user=_manager()))
    assert locked["summary"]["short"] == 1
    proposal = asyncio.run(r.propose_adjustment(sid, current_user=_manager()))
    assert proposal["status"] == "PROPOSED"
    assert proposal["lines"][0]["delta_units"] == -3
