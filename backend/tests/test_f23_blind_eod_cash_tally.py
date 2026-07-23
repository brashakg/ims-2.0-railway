"""
IMS 2.0 - F23 Blind EOD cash tally & Z-Read tests (intent-level)
================================================================
Exercises the REAL eod_tally service + the till router against a faithful
in-memory fake Mongo (no network, no live mongod). A hollow shell that reveals
the expected figure to a cashier, mis-computes the Z-Read, skips the atomic
soft-lock, lets a reason-less reopen through, or leaks one store's day to another
FAILS here.

CI-robustness: EVERY DB accessor the code touches is satisfied by the fake
(till_sessions / orders / counters / audit) and EVERY guard reads SEEDED docs --
there is NO fail-soft divergence between local (no Mongo) and CI. The policy
engine (variance tolerance, reopen roles) + the store->entity resolver + the
audit repo accessor are monkeypatched at their real call sites. No whole-JSON
substring assertions: we assert on structured fields.

Maps to the packet's acceptance intents:
  * blind entry      -- the cashier's response NEVER carries expected/variance
                        before a manager locks (data-layer redaction)
  * Z-read math       -- opening + cash_sales - payouts = expected;
                        counted - expected = variance (paisa-exact integers)
  * soft-lock atomic  -- two concurrent locks -> exactly one wins; ONE audit row
  * reopen            -- requires a non-empty reason + an authorized role; audits;
                        a LOCKED day can be reopened (transparent soft-lock)
  * store-scope 403   -- a BV-1 cashier/manager cannot touch a BV-2 session
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

from api.services import eod_tally as till  # noqa: E402


# ============================================================================
# Faithful in-memory fake Mongo (only the operators F23 uses)
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


def _set_path(doc: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = doc
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _apply_update(doc: Dict[str, Any], update: Dict[str, Any]) -> None:
    for op, fields in update.items():
        if op in ("$set", "$setOnInsert"):
            for kk, vv in fields.items():
                if "." in kk:
                    _set_path(doc, kk, vv)
                else:
                    doc[kk] = vv
        elif op == "$inc":
            for kk, vv in fields.items():
                doc[kk] = (doc.get(kk) or 0) + vv
        elif op == "$push":
            for kk, vv in fields.items():
                doc.setdefault(kk, [])
                doc[kk].append(vv)


def _project(doc: Dict[str, Any], projection: Optional[Dict[str, int]]) -> Dict[str, Any]:
    out = dict(doc)
    if projection and projection.get("_id") == 0:
        out.pop("_id", None)
    return out


class FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    def sort(self, field, direction=-1):
        self._docs = sorted(
            self._docs,
            key=lambda d: (d.get(field) is None, d.get(field)),
            reverse=(direction == -1),
        )
        return self

    def limit(self, n):
        self._docs = self._docs[: int(n)]
        return self

    def __iter__(self):
        return iter(self._docs)


class FakeCollection:
    def __init__(self, database=None):
        self.docs: List[Dict[str, Any]] = []
        self._n = 0
        self.database = database

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
                return _project(d, projection)
        return None

    def find(self, query=None, projection=None):
        matched = [_project(d, projection) for d in self.docs if _matches(d, query or {})]
        return FakeCursor(matched)

    def update_one(self, query, update, upsert=False):
        for d in self.docs:
            if _matches(d, query):
                _apply_update(d, {k: v for k, v in update.items() if k != "$setOnInsert"})
                return type("R", (), {"modified_count": 1, "matched_count": 1})()
        if upsert:
            self._upsert(query, update)
            return type("R", (), {"modified_count": 0, "matched_count": 0, "upserted_id": 1})()
        return type("R", (), {"modified_count": 0, "matched_count": 0})()

    def find_one_and_update(self, query, update, return_document=None, upsert=False, **_kw):
        for d in self.docs:
            if _matches(d, query):
                _apply_update(d, {k: v for k, v in update.items() if k != "$setOnInsert"})
                return _project(d, None)
        if upsert:
            d = self._upsert(query, update)
            return _project(d, None)
        return None

    def _upsert(self, query, update):
        base: Dict[str, Any] = {}
        for k, v in query.items():
            if not isinstance(v, dict):
                base[k] = v
        base.update(update.get("$setOnInsert", {}))
        _apply_update(base, {k: v for k, v in update.items() if k != "$setOnInsert"})
        self.insert_one(base)
        return base

    def create_index(self, *a, **k):
        return "idx"


class FakeDB:
    def __init__(self):
        self._collections: Dict[str, FakeCollection] = {}
        self.is_connected = True

    def get_collection(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection(database=self)
        return self._collections[name]

    def __getitem__(self, name: str) -> FakeCollection:
        return self.get_collection(name)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture()
def db() -> FakeDB:
    return FakeDB()


@pytest.fixture(autouse=True)
def _isolate_entity_resolver(monkeypatch):
    """tender_reconciliation's scope chain memoizes a store->entity lookup. Pin
    it so reconcile_window never reaches the real (absent) DB."""
    monkeypatch.setattr(
        "api.services.policy_engine._resolve_entity_id",
        lambda store_id: None,
    )


@pytest.fixture(autouse=True)
def _pin_policy(monkeypatch):
    """Pin the E2 policy reads the till engine makes (variance tolerance + reopen
    roles) to deterministic defaults at the REAL call site (policy_engine.
    get_policy). Tests that need a different tolerance/role-set override per-call."""
    defaults = {
        "till.variance_tolerance_paisa": 0,
        "till.reopen_roles": ["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
    }

    def _fake_get_policy(key, scope=None, *, default=None):
        return defaults.get(key, default)

    monkeypatch.setattr("api.services.policy_engine.get_policy", _fake_get_policy)
    return defaults


@pytest.fixture(autouse=True)
def _capture_audit(monkeypatch):
    """Capture AuditRepository.create calls via the SAME accessor the service
    uses (api.dependencies.get_audit_repository)."""
    rows: List[Dict[str, Any]] = []

    class _Repo:
        def create(self, data):
            rows.append(dict(data))
            return {"log_id": f"AUD-{len(rows)}"}

    monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: _Repo())
    return rows


def _pay(method, amount, **extra):
    """A faithful order.payments[] row (the capture shape the POS writes)."""
    row = {
        "payment_id": extra.pop("payment_id", "PAY-x"),
        "method": method,
        "amount": amount,
        "reference": extra.pop("reference", None),
        "received_by": extra.pop("received_by", "U1"),
        "received_at": extra.pop("received_at", "2026-06-09T10:00:00"),
        "idempotency_key": extra.pop("idempotency_key", "IK-x"),
    }
    row.update(extra)
    return row


def _seed_order(db, *, order_id, store_id, payments, created_at="2026-06-09T10:00:00"):
    db.get_collection("orders").insert_one(
        {"order_id": order_id, "store_id": store_id, "created_at": created_at, "payments": payments}
    )


def _cashier(uid="C1", store="BV-1"):
    return {"user_id": uid, "full_name": "Cashier One", "roles": ["SALES_CASHIER"],
            "store_ids": [store], "active_store_id": store}


def _manager(uid="M1", store="BV-1"):
    return {"user_id": uid, "full_name": "Manager One", "roles": ["STORE_MANAGER"],
            "store_ids": [store], "active_store_id": store}


WS = "2026-06-09T00:00:00"
WE = "2026-06-10T00:00:00"


# ============================================================================
# Pure money math (paisa-exact denomination + variance status)
# ============================================================================


def test_denomination_total_is_paisa_exact():
    rows = [{"face": 500, "kind": "note", "pieces": 3}, {"face": 50, "kind": "note", "pieces": 2}]
    # 3*500 + 2*50 = 1600 rupees = 160000 paisa.
    assert till.total_paisa_from_denominations(rows) == 160000


def test_denomination_normalize_clamps_junk():
    rows = [{"face": "x", "pieces": 1}, {"face": 100, "pieces": -5}, {"face": 20, "kind": "weird", "pieces": "2"}]
    norm = till.normalize_denominations(rows)
    # bad face dropped; negative clamped to 0; junk kind -> note; pieces coerced.
    assert len(norm) == 2
    assert norm[0]["pieces"] == 0
    assert norm[1]["kind"] == "note" and norm[1]["pieces"] == 2
    assert norm[1]["line_total_paisa"] == 20 * 100 * 2


def test_variance_status_band():
    assert till.variance_status(0, 0) == "BALANCED"
    assert till.variance_status(50, 100) == "BALANCED"  # within tolerance
    assert till.variance_status(150, 100) == "OVERAGE"
    assert till.variance_status(-150, 100) == "SHORTAGE"


# ============================================================================
# Z-Read math (opening + cash_sales - payouts = expected; paisa-exact)
# ============================================================================


def test_compute_expected_matches_zread_identity(db):
    # Opening 1000.00 (100000 paisa). CASH 700.00 + 333.33 + a 200.00 refund.
    _seed_order(db, order_id="O1", store_id="BV-1",
                payments=[_pay("CASH", 700.0), _pay("CASH", 333.33), _pay("CASH", -200.0)])
    # net CASH = 700 + 333.33 - 200 = 833.33 -> 83333 paisa.
    exp = till.compute_expected(db, "BV-1", WS, WE, 100000, cash_payouts_paisa=5000)
    assert exp["cash_sales_paisa"] == 83333
    # expected = opening + cash_sales - payouts = 100000 + 83333 - 5000 = 178333
    assert exp["expected_cash_paisa"] == 178333


def test_non_cash_tenders_excluded_from_drawer_expected(db):
    # CARD/UPI must NOT inflate the drawer-expected figure (only CASH does).
    _seed_order(db, order_id="O2", store_id="BV-1",
                payments=[_pay("CASH", 500.0), _pay("CARD", 1000.0), _pay("UPI", 250.0)])
    exp = till.compute_expected(db, "BV-1", WS, WE, 0, 0)
    assert exp["cash_sales_paisa"] == 50000  # only the 500 CASH
    assert exp["expected_cash_paisa"] == 50000
    # but the by-mode breakdown still SHOWS the other tenders for the Z-Read.
    assert "CARD" in exp["by_mode"] and "UPI" in exp["by_mode"]


def test_full_close_variance_is_paisa_exact(db):
    _seed_order(db, order_id="O3", store_id="BV-1", payments=[_pay("CASH", 1000.0)])
    # opening 200.00, cash sales 1000.00, payouts 50.00 -> expected 1150.00 (115000 paisa).
    opened = till.open_session(db, store_id="BV-1", session_date="2026-06-09",
                               opening_float_paisa=20000, actor=_cashier())
    sid = opened["session"]["session_id"]
    # cashier counts 1145.00 (114500 paisa) -> variance = 114500 - 115000 = -500 (SHORT 5.00).
    res = till.blind_submit(db, sid, blind_count_paisa=114500,
                            blind_denominations=[{"face": 500, "pieces": 2}, {"face": 100, "pieces": 1},
                                                 {"face": 20, "pieces": 2}, {"face": 5, "pieces": 1}],
                            cash_payouts_paisa=5000, actor=_cashier())
    assert res["ok"] is True
    s = res["session"]
    assert s["expected_cash_paisa"] == 115000
    assert s["variance_paisa"] == -500
    assert s["variance_status"] == "SHORTAGE"


# ============================================================================
# BLIND ENTRY -- expected/variance hidden from the cashier until lock
# ============================================================================


def test_open_does_not_compute_expected(db):
    _seed_order(db, order_id="O4", store_id="BV-1", payments=[_pay("CASH", 999.0)])
    opened = till.open_session(db, store_id="BV-1", session_date="2026-06-09",
                               opening_float_paisa=10000, actor=_cashier())
    s = opened["session"]
    # At OPEN time the expected figure is NOT computed (blind).
    assert s["expected_cash_paisa"] is None
    assert s["variance_paisa"] is None


def test_redact_for_cashier_strips_expected_fields():
    session = {
        "session_id": "TILL-x", "status": "BLIND_SUBMITTED",
        "blind_count_paisa": 50000, "opening_float_paisa": 10000,
        "expected_cash_paisa": 60000, "variance_paisa": -10000,
        "variance_status": "SHORTAGE", "cash_sales_paisa": 50000,
        "by_mode": {"CASH": {"net": 500.0}}, "tolerance_paisa": 0,
    }
    red = till.redact_for_cashier(session)
    # The system figures are GONE; the cashier's own count + float remain.
    assert "expected_cash_paisa" not in red
    assert "variance_paisa" not in red
    assert "variance_status" not in red
    assert "by_mode" not in red
    assert red["blind_count_paisa"] == 50000
    assert red["expected_hidden"] is True
    # The stored doc is untouched (a copy was returned).
    assert session["expected_cash_paisa"] == 60000


def test_blind_submit_stores_expected_but_router_redacts_for_cashier(db, monkeypatch):
    """End-to-end via the ROUTER: a SALES_CASHIER's blind-submit RESPONSE must
    NOT carry the expected figure even though it is stored server-side."""
    import asyncio
    from api.routers import till as tillroute

    monkeypatch.setattr(tillroute, "_get_db", lambda: db)
    monkeypatch.setattr(tillroute, "validate_store_access", lambda sid, u: sid or u.get("active_store_id"))

    _seed_order(db, order_id="O5", store_id="BV-1", payments=[_pay("CASH", 800.0)])
    opened = till.open_session(db, store_id="BV-1", session_date="2026-06-09",
                               opening_float_paisa=10000, actor=_cashier())
    sid = opened["session"]["session_id"]
    body = tillroute.BlindSubmit(blind_denominations=[{"face": 500, "pieces": 1}, {"face": 100, "pieces": 4}],
                                 blind_count_paisa=90000)
    out = asyncio.run(tillroute.submit_blind_count(sid, body, current_user=_cashier()))
    # The cashier's response is redacted.
    assert out["session"].get("expected_cash_paisa") is None
    assert "variance_paisa" not in out["session"] or out["session"].get("variance_paisa") is None
    assert out["session"]["expected_hidden"] is True
    # But the stored doc DID compute + persist the truth (90000 - 90000 = 0).
    stored = db.get_collection("till_sessions").find_one({"_id": sid})
    assert stored["expected_cash_paisa"] == 90000
    assert stored["variance_paisa"] == 0


def test_manager_blind_submit_response_is_not_redacted(db, monkeypatch):
    """A manager submitting (e.g. covering a till) DOES see the figures -- the
    redaction is keyed on cashier-ONLY roles."""
    import asyncio
    from api.routers import till as tillroute

    monkeypatch.setattr(tillroute, "_get_db", lambda: db)
    monkeypatch.setattr(tillroute, "validate_store_access", lambda sid, u: sid or u.get("active_store_id"))

    _seed_order(db, order_id="O6", store_id="BV-1", payments=[_pay("CASH", 500.0)])
    opened = till.open_session(db, store_id="BV-1", session_date="2026-06-09",
                               opening_float_paisa=0, actor=_manager())
    sid = opened["session"]["session_id"]
    body = tillroute.BlindSubmit(blind_denominations=[{"face": 500, "pieces": 1}], blind_count_paisa=50000)
    out = asyncio.run(tillroute.submit_blind_count(sid, body, current_user=_manager()))
    assert out["session"]["expected_cash_paisa"] == 50000
    assert out["session"]["variance_paisa"] == 0


# ============================================================================
# Denomination integrity + idempotency + lifecycle guards
# ============================================================================


def test_denomination_mismatch_rejected(db):
    opened = till.open_session(db, store_id="BV-1", session_date="2026-06-09",
                               opening_float_paisa=0, actor=_cashier())
    sid = opened["session"]["session_id"]
    # Grid sums to 50000 but the submitted total claims 99999 -> reject.
    res = till.blind_submit(db, sid, blind_denominations=[{"face": 500, "pieces": 1}],
                            blind_count_paisa=99999, actor=_cashier())
    assert res["ok"] is False
    assert res["error"] == "denomination_mismatch"
    assert res["http"] == 400


def test_second_open_same_store_day_returns_same_shared_drawer(db):
    """ONE SHARED DRAWER PER STORE: a second open for the SAME (store, date) --
    even by a DIFFERENT cashier -- joins the EXISTING shared drawer (no phantom
    second session). Otherwise the store-wide cash math would falsely short every
    extra drawer on a multi-cashier day."""
    a = till.open_session(db, store_id="BV-1", session_date="2026-06-09",
                          opening_float_paisa=5000, actor=_cashier("C1"))
    assert a["ok"] is True
    sid_a = a["session"]["session_id"]
    # A DIFFERENT cashier opens the same store/day -> the SAME shared session.
    b = till.open_session(db, store_id="BV-1", session_date="2026-06-09",
                          opening_float_paisa=999999, actor=_cashier("C2"))
    assert b["ok"] is True
    assert b.get("already_open") is True
    assert b["session"]["session_id"] == sid_a
    # The opening float of the FIRST open stands (the join does not overwrite).
    assert b["session"]["opening_float_paisa"] == 5000
    # Exactly ONE session exists for the store/day (no phantom second drawer).
    rows = db.get_collection("till_sessions").find({"store_id": "BV-1", "session_date": "2026-06-09"})
    assert len([r for r in rows]) == 1


def test_open_race_duplicate_insert_returns_existing_not_500(db, monkeypatch):
    """Open-race: the find_one precheck passes (window between two concurrent
    opens) but the unique (store, date) index makes the second INSERT collide.
    The service must catch DuplicateKeyError and return the existing shared drawer
    (or a clean 409) -- NEVER a 500 with a leaked E11000."""
    coll = db.get_collection("till_sessions")
    # First open lands a real session.
    a = till.open_session(db, store_id="BV-1", session_date="2026-06-09",
                          opening_float_paisa=5000, actor=_cashier("C1"))
    assert a["ok"] is True
    sid_a = a["session"]["session_id"]

    # Simulate the race: force the precheck find_one to miss (return None) so the
    # code reaches insert_one, and force insert_one to raise DuplicateKeyError
    # (as the real unique index would).
    from pymongo.errors import DuplicateKeyError

    real_find_one = coll.find_one
    calls = {"n": 0}

    def racing_find_one(query, projection=None):
        # The PRECHECK (status $in OPEN/BLIND_SUBMITTED) misses once; the post-
        # collision recovery find_one then sees the real existing session.
        if query.get("status", {}).get("$in") and calls["n"] == 0:
            calls["n"] += 1
            return None
        return real_find_one(query, projection)

    def racing_insert(doc):
        raise DuplicateKeyError("E11000 duplicate key uniq_active_till_per_store_day")

    monkeypatch.setattr(coll, "find_one", racing_find_one)
    monkeypatch.setattr(coll, "insert_one", racing_insert)

    b = till.open_session(db, store_id="BV-1", session_date="2026-06-09",
                          opening_float_paisa=0, actor=_cashier("C2"))
    # No 500, no leaked E11000 -- recovered to the existing shared drawer.
    assert b["ok"] is True and b.get("already_open") is True
    assert b["session"]["session_id"] == sid_a


def test_blind_submit_idempotent_retry_returns_existing(db):
    _seed_order(db, order_id="O7", store_id="BV-1", payments=[_pay("CASH", 300.0)])
    opened = till.open_session(db, store_id="BV-1", session_date="2026-06-09",
                               opening_float_paisa=0, actor=_cashier())
    sid = opened["session"]["session_id"]
    r1 = till.blind_submit(db, sid, blind_count_paisa=30000,
                           blind_denominations=[{"face": 100, "pieces": 3}],
                           idempotency_key="K1", actor=_cashier())
    assert r1["ok"] is True
    # A retry with the SAME key returns the existing state (no double-apply).
    r2 = till.blind_submit(db, sid, blind_count_paisa=99999,
                           blind_denominations=[{"face": 500, "pieces": 200}],
                           idempotency_key="K1", actor=_cashier())
    assert r2["ok"] is True and r2.get("idempotent") is True
    assert r2["session"]["blind_count_paisa"] == 30000  # the FIRST count stands


def test_cannot_lock_before_blind_submit(db):
    opened = till.open_session(db, store_id="BV-1", session_date="2026-06-09",
                               opening_float_paisa=0, actor=_cashier())
    sid = opened["session"]["session_id"]
    res = till.lock_session(db, sid, actor=_manager())
    assert res["ok"] is False and res["error"] == "not_submitted" and res["http"] == 409


# ============================================================================
# SOFT-LOCK is atomic (two concurrent locks -> exactly one wins; one audit row)
# ============================================================================


def test_two_concurrent_locks_exactly_one_wins(db, _capture_audit):
    _seed_order(db, order_id="O8", store_id="BV-1", payments=[_pay("CASH", 400.0)])
    opened = till.open_session(db, store_id="BV-1", session_date="2026-06-09",
                               opening_float_paisa=0, actor=_cashier())
    sid = opened["session"]["session_id"]
    till.blind_submit(db, sid, blind_count_paisa=40000,
                      blind_denominations=[{"face": 100, "pieces": 4}], actor=_cashier())
    r1 = till.lock_session(db, sid, actor=_manager("M1"))
    r2 = till.lock_session(db, sid, actor=_manager("M2"))
    oks = [r1["ok"], r2["ok"]]
    assert oks.count(True) == 1, "exactly one lock must succeed"
    loser = r1 if not r1["ok"] else r2
    assert loser["error"] == "already_locked" and loser["http"] == 409
    # Exactly ONE till.lock audit row.
    lock_rows = [r for r in _capture_audit if r["action"] == "till.lock"]
    assert len(lock_rows) == 1
    # The winner got a Z-Read number.
    winner = r1 if r1["ok"] else r2
    assert winner["session"]["zread_number"]


def test_lock_reveals_expected_and_variance(db):
    _seed_order(db, order_id="O9", store_id="BV-1", payments=[_pay("CASH", 600.0)])
    opened = till.open_session(db, store_id="BV-1", session_date="2026-06-09",
                               opening_float_paisa=10000, actor=_cashier())
    sid = opened["session"]["session_id"]
    till.blind_submit(db, sid, blind_count_paisa=70500,  # over by 5.00
                      blind_denominations=[{"face": 500, "pieces": 1}, {"face": 200, "pieces": 1},
                                           {"face": 5, "pieces": 1}], actor=_cashier())
    res = till.lock_session(db, sid, actor=_manager())
    assert res["ok"] is True
    s = res["session"]
    assert s["expected_cash_paisa"] == 70000
    assert s["variance_paisa"] == 500
    assert s["variance_status"] == "OVERAGE"


def test_zread_carries_the_identity_fields(db):
    _seed_order(db, order_id="O10", store_id="BV-1",
                payments=[_pay("CASH", 500.0), _pay("UPI", 250.0)])
    opened = till.open_session(db, store_id="BV-1", session_date="2026-06-09",
                               opening_float_paisa=10000, actor=_cashier())
    sid = opened["session"]["session_id"]
    till.blind_submit(db, sid, blind_count_paisa=60000,
                      blind_denominations=[{"face": 500, "pieces": 1}, {"face": 100, "pieces": 1}],
                      actor=_cashier())
    till.lock_session(db, sid, actor=_manager())
    z = till.build_zread(db, sid)
    assert z["ok"] is True
    # opening + cash_sales - payouts == expected.
    assert z["opening_float_paisa"] + z["cash_sales_paisa"] - z["cash_payouts_paisa"] == z["expected_cash_paisa"]
    assert z["counted_cash_paisa"] == 60000
    assert z["variance_paisa"] == z["counted_cash_paisa"] - z["expected_cash_paisa"]
    # The Z-Read surfaces the by-tender breakdown (UPI shown, CASH feeds drawer).
    assert "UPI" in z["by_mode"]
    assert z["zread_number"]


# ============================================================================
# REOPEN -- requires reason + role; audits; transparent soft-lock release
# ============================================================================


def _locked(db, *, opening=0, counted=40000):
    _seed_order(db, order_id=f"OR-{counted}", store_id="BV-1", payments=[_pay("CASH", 400.0)])
    opened = till.open_session(db, store_id="BV-1", session_date="2026-06-09",
                               opening_float_paisa=opening, actor=_cashier())
    sid = opened["session"]["session_id"]
    till.blind_submit(db, sid, blind_count_paisa=counted,
                      blind_denominations=[{"face": 100, "pieces": counted // 10000}], actor=_cashier())
    till.lock_session(db, sid, actor=_manager())
    return sid


def test_reopen_requires_nonempty_reason(db):
    sid = _locked(db)
    res = till.reopen_session(db, sid, reason="   ", actor=_manager())
    assert res["ok"] is False and res["error"] == "reason_required" and res["http"] == 400
    # The day is STILL locked (the empty-reason reopen was rejected).
    assert till.get_session(db, sid)["status"] == "LOCKED"


def test_reopen_requires_authorized_role(db):
    sid = _locked(db)
    # A SALES_CASHIER is NOT in the reopen-role set -> 403.
    res = till.reopen_session(db, sid, reason="miscount", actor=_cashier())
    assert res["ok"] is False and res["error"] == "not_permitted_to_reopen" and res["http"] == 403
    assert till.get_session(db, sid)["status"] == "LOCKED"


def test_reopen_succeeds_audits_and_is_soft(db, _capture_audit):
    sid = _locked(db)
    res = till.reopen_session(db, sid, reason="recount after bank deposit error", actor=_manager())
    assert res["ok"] is True
    s = res["session"]
    # Transparent soft-lock: back to BLIND_SUBMITTED, reopen recorded + counted.
    assert s["status"] == "BLIND_SUBMITTED"
    assert s["reopen_count"] == 1
    assert any(h.get("action") == "reopen" and h.get("reason") for h in (s.get("history") or []))
    # The reopen is audited.
    reopen_rows = [r for r in _capture_audit if r["action"] == "till.reopen"]
    assert len(reopen_rows) == 1
    assert reopen_rows[0]["after_state"]["reason"].startswith("recount")


def test_reopen_then_relock_keeps_same_zread_number(db):
    sid = _locked(db)
    z1 = till.get_session(db, sid)["zread_number"]
    till.reopen_session(db, sid, reason="fix", actor=_manager())
    relock = till.lock_session(db, sid, actor=_manager())
    assert relock["ok"] is True
    assert relock["session"]["zread_number"] == z1  # same business day-close


def test_reopen_role_set_is_e2_configurable(db, monkeypatch):
    """The reopen role set is read from E2 policy -- narrowing it to ADMIN-only
    must 403 a STORE_MANAGER who would otherwise be allowed."""
    monkeypatch.setattr(
        "api.services.policy_engine.get_policy",
        lambda key, scope=None, *, default=None: (["ADMIN"] if key == "till.reopen_roles" else (0 if key == "till.variance_tolerance_paisa" else default)),
    )
    sid = _locked(db)
    res = till.reopen_session(db, sid, reason="x", actor=_manager())  # STORE_MANAGER
    assert res["ok"] is False and res["http"] == 403


# ============================================================================
# STORE-SCOPE -- a BV-1 actor cannot touch a BV-2 session (router IDOR guard)
# ============================================================================


def test_lock_route_403_for_cross_store_manager(db, monkeypatch):
    """A BV-1 manager must NOT lock a BV-2 session. validate_store_access
    store-scopes STORE_MANAGER (only SUPERADMIN/ADMIN bypass)."""
    import asyncio
    from fastapi import HTTPException
    from api.routers import till as tillroute

    monkeypatch.setattr(tillroute, "_get_db", lambda: db)
    # Use the REAL validate_store_access (the IDOR guard under test).
    _seed_order(db, order_id="OX", store_id="BV-2", payments=[_pay("CASH", 100.0)])
    opened = till.open_session(db, store_id="BV-2", session_date="2026-06-09",
                               opening_float_paisa=0, actor=_cashier("C2", "BV-2"))
    sid = opened["session"]["session_id"]
    till.blind_submit(db, sid, blind_count_paisa=10000,
                      blind_denominations=[{"face": 100, "pieces": 1}], actor=_cashier("C2", "BV-2"))
    bv1_manager = _manager("M9", "BV-1")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(tillroute.lock_till_session(sid, current_user=bv1_manager))
    assert exc.value.status_code == 403
    # The foreign session stays BLIND_SUBMITTED -- the cross-store lock was blocked.
    assert till.get_session(db, sid)["status"] == "BLIND_SUBMITTED"


def test_blind_submit_route_403_for_cross_store_cashier(db, monkeypatch):
    import asyncio
    from fastapi import HTTPException
    from api.routers import till as tillroute

    monkeypatch.setattr(tillroute, "_get_db", lambda: db)
    opened = till.open_session(db, store_id="BV-2", session_date="2026-06-09",
                               opening_float_paisa=0, actor=_cashier("C2", "BV-2"))
    sid = opened["session"]["session_id"]
    body = tillroute.BlindSubmit(blind_denominations=[{"face": 100, "pieces": 1}], blind_count_paisa=10000)
    bv1_cashier = _cashier("C1", "BV-1")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(tillroute.submit_blind_count(sid, body, current_user=bv1_cashier))
    assert exc.value.status_code == 403
    # Untouched.
    assert till.get_session(db, sid)["status"] == "OPEN"


def test_zread_route_403_for_cashier_role(db, monkeypatch):
    """A SALES_CASHIER must NOT reach the Z-Read (it reveals the expected
    figure). The route gate rejects the role outright."""
    import asyncio
    from fastapi import HTTPException
    from api.routers import till as tillroute

    monkeypatch.setattr(tillroute, "_get_db", lambda: db)
    monkeypatch.setattr(tillroute, "validate_store_access", lambda sid, u: sid or u.get("active_store_id"))
    opened = till.open_session(db, store_id="BV-1", session_date="2026-06-09",
                               opening_float_paisa=0, actor=_cashier())
    sid = opened["session"]["session_id"]
    with pytest.raises(HTTPException) as exc:
        asyncio.run(tillroute.get_zread(sid, current_user=_cashier()))
    assert exc.value.status_code == 403


# ============================================================================
# SESSION-DATE VALIDATION -- the window is ALWAYS bounded to an IST day; a
# malformed/out-of-range session_date is rejected (never an open-ended window).
# ============================================================================


def test_session_day_window_is_always_bounded_for_valid_date():
    """A valid session_date yields a (start, end) span of EXACTLY one IST day --
    both bounds present (no open-ended $gte-only window)."""
    from datetime import timedelta
    from api.utils.ist import ist_day_start_utc
    import datetime as _dt

    session = {"session_date": "2026-06-09", "opened_at": _dt.datetime(2026, 6, 9, 12, 0, 0)}
    start, end = till._session_day_window(session)
    assert start is not None and end is not None
    assert start == ist_day_start_utc(_dt.date(2026, 6, 9))
    assert end - start == timedelta(days=1)


def test_session_day_window_never_open_ended_on_junk_date():
    """A missing/garbage session_date must NOT degrade to an open-ended window
    (which would reconcile EVERY order from opened_at forward and over-state the
    expected figure). It falls back to the IST day of opened_at -- still BOUNDED."""
    from datetime import timedelta
    import datetime as _dt

    # opened_at is a naive-UTC instant on 2026-06-09 (07:00 UTC == 12:30 IST).
    session = {"session_date": "not-a-date", "opened_at": _dt.datetime(2026, 6, 9, 7, 0, 0)}
    start, end = till._session_day_window(session)
    assert start is not None and end is not None, "window must be bounded, never (start, None)"
    assert end - start == timedelta(days=1)
    # Also a totally absent session_date is bounded.
    s2 = {"opened_at": _dt.datetime(2026, 6, 9, 7, 0, 0)}
    start2, end2 = till._session_day_window(s2)
    assert start2 is not None and end2 is not None
    assert end2 - start2 == timedelta(days=1)


def test_open_route_rejects_malformed_session_date(db, monkeypatch):
    """The OPEN route validates session_date with date.fromisoformat -- a junk
    value is a 400, never a silent open-ended reconciliation window."""
    import asyncio
    from fastapi import HTTPException
    from api.routers import till as tillroute

    monkeypatch.setattr(tillroute, "_get_db", lambda: db)
    monkeypatch.setattr(tillroute, "validate_store_access", lambda sid, u: sid or u.get("active_store_id"))
    body = tillroute.OpenSession(store_id="BV-1", session_date="13/06/2026",
                                 opening_denominations=[], opening_float_paisa=0)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(tillroute.open_till_session(body, current_user=_cashier()))
    assert exc.value.status_code == 400


def test_open_route_rejects_out_of_range_session_date(db, monkeypatch):
    """A well-formed but far-off session_date (years away) is out of the IST
    today +/- 1 day band -> 400 (a store closes today, not 2099)."""
    import asyncio
    from fastapi import HTTPException
    from api.routers import till as tillroute

    monkeypatch.setattr(tillroute, "_get_db", lambda: db)
    monkeypatch.setattr(tillroute, "validate_store_access", lambda sid, u: sid or u.get("active_store_id"))
    body = tillroute.OpenSession(store_id="BV-1", session_date="2099-01-01",
                                 opening_denominations=[], opening_float_paisa=0)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(tillroute.open_till_session(body, current_user=_cashier()))
    assert exc.value.status_code == 400


def test_open_route_accepts_today_and_bounds_the_window(db, monkeypatch):
    """A valid (today) session_date opens fine; the resulting session's window is
    bounded to that IST day so the blind-submit expected figure is correct."""
    import asyncio
    from datetime import timedelta
    from api.routers import till as tillroute
    from api.utils.ist import ist_today

    monkeypatch.setattr(tillroute, "_get_db", lambda: db)
    monkeypatch.setattr(tillroute, "validate_store_access", lambda sid, u: sid or u.get("active_store_id"))
    today = ist_today().isoformat()
    body = tillroute.OpenSession(store_id="BV-1", session_date=today,
                                 opening_denominations=[], opening_float_paisa=10000)
    out = asyncio.run(tillroute.open_till_session(body, current_user=_cashier()))
    assert out["ok"] is True
    sid = out["session"]["session_id"]
    session = db.get_collection("till_sessions").find_one({"_id": sid})
    start, end = till._session_day_window(session)
    assert start is not None and end is not None
    assert end - start == timedelta(days=1)


def test_blind_submit_bounded_window_expected_is_correct(db, monkeypatch):
    """End-to-end: an order INSIDE the session's IST day feeds the expected cash;
    an order on a DIFFERENT day does NOT (the window is bounded, not open-ended)."""
    from api.utils.ist import ist_today, ist_day_start_utc
    from datetime import timedelta
    import datetime as _dt

    today = ist_today()
    # An order created today (inside the IST day window).
    in_day = ist_day_start_utc(today) + timedelta(hours=3)
    # An order created on a LATER day (must be EXCLUDED by the bounded window).
    next_day = ist_day_start_utc(today + timedelta(days=1)) + timedelta(hours=3)
    _seed_order(db, order_id="OIN", store_id="BV-1", payments=[_pay("CASH", 500.0)], created_at=in_day)
    _seed_order(db, order_id="OOUT", store_id="BV-1", payments=[_pay("CASH", 999.0)], created_at=next_day)

    opened = till.open_session(db, store_id="BV-1", session_date=today.isoformat(),
                               opening_float_paisa=0, actor=_cashier())
    sid = opened["session"]["session_id"]
    res = till.blind_submit(db, sid, blind_count_paisa=50000,
                            blind_denominations=[{"face": 500, "pieces": 1}], actor=_cashier())
    assert res["ok"] is True
    # Only today's 500.00 CASH counts (the 999.00 next-day order is excluded).
    assert res["session"]["expected_cash_paisa"] == 50000
    assert res["session"]["variance_paisa"] == 0


# ============================================================================
# DB-absent + index fail-soft (no crash divergence)
# ============================================================================


def test_db_absent_is_failsoft_not_crash():
    assert till.open_session(None, store_id="BV-1", session_date="2026-06-09", actor=_cashier())["http"] == 503
    assert till.blind_submit(None, "x", actor=_cashier())["http"] == 503
    assert till.lock_session(None, "x", actor=_manager())["http"] == 503
    assert till.reopen_session(None, "x", reason="r", actor=_manager())["http"] == 503
    assert till.get_session(None, "x") is None
    assert till.list_sessions(None, store_id="BV-1") == []


def test_ensure_indexes_idempotent_and_failsoft(db):
    till.ensure_till_indexes(db)
    till.ensure_till_indexes(db)
    till.ensure_till_indexes(None)  # DB absent -> no raise


# ---------------------------------------------------------------------------
# W1.4 / OS-030 -- ONLINE-store guard: no blind-EOD till for an online store
# ---------------------------------------------------------------------------


def test_open_route_rejects_online_store(db, monkeypatch):
    """An ONLINE store (BV-ONLINE-01) has no till -- opening a blind-EOD session
    for it is a 400 and no session doc is written."""
    import asyncio
    from fastapi import HTTPException
    from api.routers import till as tillroute

    monkeypatch.setattr(tillroute, "_get_db", lambda: db)
    monkeypatch.setattr(
        tillroute,
        "validate_store_access",
        lambda sid, u: sid or u.get("active_store_id"),
    )

    body = tillroute.OpenSession(
        store_id="BV-ONLINE-01", opening_denominations=[], opening_float_paisa=0
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            tillroute.open_till_session(
                body, current_user=_cashier(store="BV-ONLINE-01")
            )
        )
    assert exc.value.status_code == 400
    assert "online store" in str(exc.value.detail).lower()
    assert db.get_collection("till_sessions").find_one({"store_id": "BV-ONLINE-01"}) is None
