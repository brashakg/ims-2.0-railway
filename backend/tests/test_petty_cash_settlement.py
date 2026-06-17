"""
IMS 2.0 - F17 petty-cash END-OF-DAY SETTLEMENT (intent-level tests)
===================================================================
Exercise the REAL petty_cash_settlement_service over a faithful in-memory fake
Mongo, plus the REAL expenses router for the position/settle/list endpoints (RBAC
+ store-scope + idempotency). A hollow shell that skips the variance math, the
ledger-derived expected closing, the one-per-store-day idempotency, or the RBAC
gate FAILS here.

The position math is fed off the SAME float ledger petty_cash_service stamps:
each row carries type CREDIT|DEBIT, a positive ``delta``, a ``balance_after``,
and an IST-naive ``created_at`` whose first 10 chars are the IST day.

No emoji (Windows cp1252).
"""

from __future__ import annotations

import copy
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import expenses  # noqa: E402
from api.routers.auth import get_current_user  # noqa: E402
from api.services import petty_cash_settlement_service as pcss  # noqa: E402


# ============================================================================
# Faithful in-memory fake Mongo (subset used by the settlement service)
# ============================================================================


def _matches(doc, filt):
    for k, v in filt.items():
        cur = doc.get(k)
        if isinstance(v, dict):
            if "$gte" in v and (cur is None or cur < v["$gte"]):
                return False
            if "$lte" in v and (cur is None or cur > v["$lte"]):
                return False
        elif cur != v:
            return False
    return True


class _DuplicateKeyError(Exception):
    pass


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def sort(self, key, direction):
        self._rows.sort(key=lambda r: r.get(key) or "", reverse=(direction < 0))
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def __iter__(self):
        return iter(self._rows)


class FakeCollection:
    def __init__(self, name):
        self.name = name
        self.docs = []
        self._unique = []  # list of tuple-key field lists

    def create_index(self, keys, unique=False, **kw):
        if unique:
            if isinstance(keys, str):
                self._unique.append((keys,))
            elif isinstance(keys, (list, tuple)) and keys and isinstance(keys[0], tuple):
                self._unique.append(tuple(k[0] for k in keys))
        return "idx"

    def _violates_unique(self, doc):
        for fields in self._unique:
            for d in self.docs:
                if all(d.get(f) == doc.get(f) for f in fields):
                    return True
        return False

    def insert_one(self, doc):
        doc = dict(doc)
        if self._violates_unique(doc):
            raise _DuplicateKeyError("E11000 duplicate key")
        doc.setdefault("_id", f"oid-{self.name}-{len(self.docs)}")
        self.docs.append(doc)
        return type("R", (), {"inserted_id": doc["_id"]})()

    def find_one(self, filt, projection=None):
        for d in self.docs:
            if _matches(d, filt):
                out = copy.deepcopy(d)
                if projection and projection.get("_id") == 0:
                    out.pop("_id", None)
                return out
        return None

    def find(self, filt, projection=None):
        rows = []
        for d in self.docs:
            if _matches(d, filt):
                out = copy.deepcopy(d)
                if projection and projection.get("_id") == 0:
                    out.pop("_id", None)
                rows.append(out)
        return _Cursor(rows)

    def update_one(self, filt, update, upsert=False):
        for d in self.docs:
            if _matches(d, filt):
                for f, val in (update.get("$set") or {}).items():
                    d[f] = val
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()


class FakeDB:
    def __init__(self):
        self._collections = {}

    def get_collection(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection(name)
        return self._collections[name]

    def __getitem__(self, name):
        return self.get_collection(name)


# ============================================================================
# Fixtures + seed helpers
# ============================================================================

DAY = "2026-06-17"
PREV = "2026-06-16"


@pytest.fixture
def db():
    d = FakeDB()
    pcss.ensure_indexes(d)
    return d


def _seed_float(db, *, store_id="S1", balance=5000.0, ledger=None, status="ACTIVE"):
    db.get_collection(pcss.FLOAT_COLLECTION).insert_one({
        "store_id": store_id,
        "balance": balance,
        "float_limit": 10000.0,
        "status": status,
        "ledger": ledger or [],
    })


def _row(ltype, delta, balance_after, day, txn="t"):
    return {
        "txn_id": txn, "type": ltype, "delta": delta,
        "balance_after": balance_after, "reason": "x",
        "created_at": f"{day}T10:00:00",
    }


# ============================================================================
# PURE position math (compute_position)
# ============================================================================


def test_position_no_movement_today():
    """No ledger rows on the settle day -> opening == expected == balance."""
    pos = pcss.compute_position([], DAY, current_balance=5000.0)
    assert pos["opening_float"] == 5000.0
    assert pos["credits_today"] == 0.0
    assert pos["debits_today"] == 0.0
    assert pos["expected_closing"] == 5000.0


def test_position_payouts_and_topup_today():
    """Open 5000, two payouts (-200, -300) then a topup (+500). Expected close is
    the last balance_after; opening backs out the day's net = 5000."""
    ledger = [
        _row("CREDIT", 5000.0, 5000.0, DAY, "open"),   # float opened today
        _row("DEBIT", 200.0, 4800.0, DAY, "p1"),
        _row("DEBIT", 300.0, 4500.0, DAY, "p2"),
        _row("CREDIT", 500.0, 5000.0, DAY, "top"),
    ]
    pos = pcss.compute_position(ledger, DAY, current_balance=5000.0)
    assert pos["debits_today"] == 500.0
    assert pos["credits_today"] == 5500.0   # 5000 open + 500 topup
    assert pos["payouts_count"] == 2
    assert pos["expected_closing"] == 5000.0
    # opening = closing - credits + debits = 5000 - 5500 + 500 = 0 (box was empty
    # before today's open). This is the correct opening for a same-day open.
    assert pos["opening_float"] == 0.0


def test_position_settling_a_past_day_ignores_later_rows():
    """A float opened PREV with one payout; settling PREV uses only PREV rows
    even though the float kept moving on DAY."""
    ledger = [
        _row("CREDIT", 5000.0, 5000.0, PREV, "open"),
        _row("DEBIT", 1000.0, 4000.0, PREV, "p1"),
        # next day movement -- must NOT affect the PREV position
        _row("DEBIT", 500.0, 3500.0, DAY, "p2"),
    ]
    pos = pcss.compute_position(ledger, PREV, current_balance=3500.0)
    assert pos["debits_today"] == 1000.0
    assert pos["credits_today"] == 5000.0
    assert pos["expected_closing"] == 4000.0   # PREV last balance_after
    assert pos["opening_float"] == 0.0


# ============================================================================
# Service-level settle (variance math + idempotency)
# ============================================================================


def test_settle_balanced(db):
    _seed_float(db, balance=4500.0, ledger=[
        _row("CREDIT", 5000.0, 5000.0, DAY, "open"),
        _row("DEBIT", 500.0, 4500.0, DAY, "p1"),
    ])
    res = pcss.settle_day(db, store_id="S1", counted_closing=4500.0, actor="mgr1",
                          settle_date=DAY)
    assert res["ok"] is True
    assert res["expected_closing"] == 4500.0
    assert res["counted_closing"] == 4500.0
    assert res["variance"] == 0.0
    assert res["variance_status"] == "BALANCED"
    # An audit row was written (INFO since balanced).
    audits = [a for a in db.get_collection("audit_logs").docs
              if a.get("action") == "petty_cash.settle_day"]
    assert len(audits) == 1
    assert audits[0]["severity"] == "INFO"


def test_settle_short_variance_negative(db):
    """Counted less than expected -> SHORT (negative variance)."""
    _seed_float(db, balance=4500.0, ledger=[
        _row("CREDIT", 5000.0, 5000.0, DAY, "open"),
        _row("DEBIT", 500.0, 4500.0, DAY, "p1"),
    ])
    res = pcss.settle_day(db, store_id="S1", counted_closing=4300.0, actor="mgr1",
                          settle_date=DAY)
    assert res["ok"] is True
    assert res["variance"] == -200.0
    assert res["variance_status"] == "SHORT"
    audits = [a for a in db.get_collection("audit_logs").docs
              if a.get("action") == "petty_cash.settle_day"]
    assert audits and audits[0]["severity"] == "WARNING"


def test_settle_over_variance_positive(db):
    """Counted more than expected -> OVER (positive variance)."""
    _seed_float(db, balance=4500.0, ledger=[
        _row("CREDIT", 5000.0, 5000.0, DAY, "open"),
        _row("DEBIT", 500.0, 4500.0, DAY, "p1"),
    ])
    res = pcss.settle_day(db, store_id="S1", counted_closing=4650.0, actor="mgr1",
                          settle_date=DAY)
    assert res["ok"] is True
    assert res["variance"] == 150.0
    assert res["variance_status"] == "OVER"


def test_settle_is_idempotent_per_store_day(db):
    """A second settle for the same store-day returns the EXISTING record (409
    already_settled) and never re-writes the variance."""
    _seed_float(db, balance=4500.0, ledger=[
        _row("DEBIT", 500.0, 4500.0, DAY, "p1"),
    ])
    first = pcss.settle_day(db, store_id="S1", counted_closing=4400.0, actor="mgr1",
                            settle_date=DAY)
    assert first["ok"] is True
    sid = first["settlement_id"]

    second = pcss.settle_day(db, store_id="S1", counted_closing=9999.0, actor="other",
                             settle_date=DAY)
    assert second["ok"] is False
    assert second["error"] == "already_settled"
    assert second["settlement"]["settlement_id"] == sid
    assert second["settlement"]["counted_closing"] == 4400.0  # NOT overwritten
    # Exactly one settlement doc persisted.
    assert len(db.get_collection(pcss.COLLECTION).docs) == 1


def test_settle_different_days_both_allowed(db):
    """One settlement per (store, day): two DIFFERENT days both settle."""
    _seed_float(db, balance=4000.0, ledger=[
        _row("DEBIT", 1000.0, 4000.0, PREV, "p0"),
        _row("DEBIT", 0.0, 4000.0, DAY, "noop"),
    ])
    r1 = pcss.settle_day(db, store_id="S1", counted_closing=4000.0, actor="m", settle_date=PREV)
    r2 = pcss.settle_day(db, store_id="S1", counted_closing=4000.0, actor="m", settle_date=DAY)
    assert r1["ok"] is True and r2["ok"] is True
    assert len(db.get_collection(pcss.COLLECTION).docs) == 2


def test_settle_no_float_404(db):
    res = pcss.settle_day(db, store_id="NOSTORE", counted_closing=100.0, actor="m",
                          settle_date=DAY)
    assert res["ok"] is False and res["http"] == 404 and res["error"] == "float_not_open"


def test_settle_negative_counted_rejected(db):
    _seed_float(db)
    res = pcss.settle_day(db, store_id="S1", counted_closing=-5.0, actor="m", settle_date=DAY)
    assert res["ok"] is False and res["http"] == 422 and res["error"] == "invalid_counted"


def test_settle_no_db_503():
    res = pcss.settle_day(None, store_id="S1", counted_closing=100.0, actor="m")
    assert res["ok"] is False and res["http"] == 503


def test_get_day_position_reflects_settlement(db):
    _seed_float(db, balance=4500.0, ledger=[
        _row("DEBIT", 500.0, 4500.0, DAY, "p1"),
    ])
    before = pcss.get_day_position(db, "S1", DAY)
    assert before["exists"] is True
    assert before["settled"] is False
    assert before["expected_closing"] == 4500.0
    assert before["debits_today"] == 500.0

    pcss.settle_day(db, store_id="S1", counted_closing=4500.0, actor="m", settle_date=DAY)
    after = pcss.get_day_position(db, "S1", DAY)
    assert after["settled"] is True
    assert after["settlement"]["counted_closing"] == 4500.0


def test_get_day_position_no_float_failsoft(db):
    pos = pcss.get_day_position(db, "GHOST", DAY)
    assert pos["ok"] is True and pos["exists"] is False and pos["settled"] is False


def test_list_settlements_newest_first(db):
    _seed_float(db, balance=4000.0, ledger=[
        _row("DEBIT", 1000.0, 4000.0, PREV, "p0"),
        _row("DEBIT", 0.0, 4000.0, DAY, "noop"),
    ])
    pcss.settle_day(db, store_id="S1", counted_closing=4000.0, actor="m", settle_date=PREV)
    pcss.settle_day(db, store_id="S1", counted_closing=4000.0, actor="m", settle_date=DAY)
    rows = pcss.list_settlements(db, "S1")
    assert [r["settle_date"] for r in rows] == [DAY, PREV]


# ============================================================================
# Router-level: RBAC + store-scope + idempotency through the HTTP surface
# ============================================================================


def _client(db, roles, *, user_id="u1", store_ids=("S1",), active="S1"):
    app = FastAPI()
    app.include_router(expenses.router, prefix="/api/v1/expenses")

    async def _fake_user():
        return {
            "user_id": user_id, "full_name": "Tester",
            "active_store_id": active, "store_ids": list(store_ids),
            "roles": list(roles),
        }

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


@pytest.fixture
def wired(db, monkeypatch):
    monkeypatch.setattr(expenses, "get_db", lambda: db)
    return db


def test_router_position_then_settle_manager(wired):
    db = wired
    _seed_float(db, balance=4500.0, ledger=[_row("DEBIT", 500.0, 4500.0, DAY, "p1")])
    client = _client(db, ["STORE_MANAGER"])

    pos = client.get("/api/v1/expenses/petty-cash/settlement/position",
                     params={"store_id": "S1", "settle_date": DAY})
    assert pos.status_code == 200, pos.text
    assert pos.json()["expected_closing"] == 4500.0

    res = client.post("/api/v1/expenses/petty-cash/settlement",
                      json={"store_id": "S1", "counted_closing": 4450.0, "settle_date": DAY})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["variance"] == -50.0 and body["variance_status"] == "SHORT"


def test_router_settle_idempotent_409(wired):
    db = wired
    _seed_float(db, balance=4500.0, ledger=[_row("DEBIT", 500.0, 4500.0, DAY, "p1")])
    client = _client(db, ["STORE_MANAGER"])
    first = client.post("/api/v1/expenses/petty-cash/settlement",
                        json={"store_id": "S1", "counted_closing": 4500.0, "settle_date": DAY})
    assert first.status_code == 201, first.text
    second = client.post("/api/v1/expenses/petty-cash/settlement",
                         json={"store_id": "S1", "counted_closing": 1.0, "settle_date": DAY})
    assert second.status_code == 409
    assert second.json()["detail"]["error"] == "already_settled"


def test_router_settle_rbac_optometrist_denied(wired):
    db = wired
    _seed_float(db)
    client = _client(db, ["OPTOMETRIST"])
    res = client.post("/api/v1/expenses/petty-cash/settlement",
                      json={"store_id": "S1", "counted_closing": 100.0, "settle_date": DAY})
    assert res.status_code == 403


def test_router_accountant_can_view_and_settle(wired):
    db = wired
    _seed_float(db, balance=4500.0, ledger=[_row("DEBIT", 500.0, 4500.0, DAY, "p1")])
    # ACCOUNTANT scoped to S1 (store-level role honours active_store_id).
    client = _client(db, ["ACCOUNTANT"], user_id="acc", store_ids=("S1",), active="S1")
    pos = client.get("/api/v1/expenses/petty-cash/settlement/position",
                     params={"store_id": "S1", "settle_date": DAY})
    assert pos.status_code == 200, pos.text
    res = client.post("/api/v1/expenses/petty-cash/settlement",
                      json={"store_id": "S1", "counted_closing": 4500.0, "settle_date": DAY})
    assert res.status_code == 201, res.text


def test_router_cross_store_settle_blocked(wired):
    """A manager scoped to store A cannot settle store B's day (cross-store IDOR)."""
    db = wired
    _seed_float(db, store_id="B", balance=1000.0, ledger=[_row("DEBIT", 100.0, 1000.0, DAY, "p")])
    client_a = _client(db, ["STORE_MANAGER"], user_id="mgrA", store_ids=("A",), active="A")
    res = client_a.post("/api/v1/expenses/petty-cash/settlement",
                        json={"store_id": "B", "counted_closing": 1000.0, "settle_date": DAY})
    assert res.status_code == 403
    # Nothing was settled for B.
    assert db.get_collection(pcss.COLLECTION).docs == []


def test_router_list_settlements(wired):
    db = wired
    _seed_float(db, balance=4000.0, ledger=[_row("DEBIT", 0.0, 4000.0, DAY, "noop")])
    client = _client(db, ["ADMIN"], user_id="adm", store_ids=(), active=None)
    client.post("/api/v1/expenses/petty-cash/settlement",
                json={"store_id": "S1", "counted_closing": 4000.0, "settle_date": DAY})
    lst = client.get("/api/v1/expenses/petty-cash/settlement", params={"store_id": "S1"})
    assert lst.status_code == 200, lst.text
    assert lst.json()["total"] == 1
