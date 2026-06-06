"""BUG-099: reverse_for_return claws back points EARNED on an order and restores
points REDEEMED on it when goods are returned -- idempotent on return_id, a single
atomic adjust_balance ($inc) for balance + both lifetime counters, never raises,
no silent negative-balance clamp."""
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import loyalty as L  # noqa: E402


class _FakeTxns:
    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]
        self.created = []

    def find_for_customer(self, cid, limit=20):
        return [dict(r) for r in self.rows if r.get("customer_id") == cid][:limit]

    def create(self, doc):
        self.created.append(dict(doc))
        self.rows.append(dict(doc))


class _FakeAccounts:
    def __init__(self, balance=0, le=0, lr=0):
        self.acct = {"balance_points": balance, "lifetime_earned": le, "lifetime_redeemed": lr}
        self.adjustments = []

    def find_or_create(self, cid):
        return dict(self.acct)

    def adjust_balance(self, cid, delta_points=0, delta_lifetime_earned=0,
                       delta_lifetime_redeemed=0, new_tier=None):
        self.adjustments.append(
            {"dp": delta_points, "dle": delta_lifetime_earned, "dlr": delta_lifetime_redeemed}
        )
        self.acct["balance_points"] += delta_points
        self.acct["lifetime_earned"] += delta_lifetime_earned
        self.acct["lifetime_redeemed"] += delta_lifetime_redeemed


def _wire(monkeypatch, txns, accounts):
    monkeypatch.setattr(L, "get_loyalty_transaction_repository", lambda: txns)
    monkeypatch.setattr(L, "get_loyalty_account_repository", lambda: accounts)


def _earn(cid, oid, pts):
    return {"customer_id": cid, "type": "EARN", "points": pts, "order_id": oid}


def _redeem(cid, oid, pts):
    return {"customer_id": cid, "type": "REDEEM", "points": pts, "order_id": oid}


def test_claws_back_earned(monkeypatch):
    txns = _FakeTxns([_earn("C1", "O1", 100)])
    acc = _FakeAccounts(balance=100, le=100)
    _wire(monkeypatch, txns, acc)
    r = L.reverse_for_return("R1", "O1", "C1")
    assert r["ok"] and r["earned_clawed"] == 100 and r["redeemed_restored"] == 0
    assert acc.adjustments == [{"dp": -100, "dle": -100, "dlr": 0}]
    assert acc.acct["balance_points"] == 0


def test_restores_redeemed(monkeypatch):
    txns = _FakeTxns([_earn("C1", "O1", 150), _redeem("C1", "O1", 50)])
    acc = _FakeAccounts(balance=100, le=150, lr=50)
    _wire(monkeypatch, txns, acc)
    r = L.reverse_for_return("R1", "O1", "C1")
    assert r["ok"] and r["earned_clawed"] == 150 and r["redeemed_restored"] == 50
    assert r["net_delta"] == -100
    assert acc.adjustments == [{"dp": -100, "dle": -150, "dlr": -50}]
    assert acc.acct["balance_points"] == 0


def test_idempotent_on_return_id(monkeypatch):
    txns = _FakeTxns([
        _earn("C1", "O1", 100),
        {"customer_id": "C1", "type": "ADJUST", "points": -100, "order_id": "O1", "return_id": "R1"},
    ])
    acc = _FakeAccounts(balance=0)
    _wire(monkeypatch, txns, acc)
    r = L.reverse_for_return("R1", "O1", "C1")
    assert r["ok"] and r.get("already_reversed") is True
    assert acc.adjustments == [] and txns.created == []


def test_no_earn_no_redeem_is_noop(monkeypatch):
    txns = _FakeTxns([_earn("C1", "OTHER", 100)])  # earn on a different order
    acc = _FakeAccounts(balance=100)
    _wire(monkeypatch, txns, acc)
    r = L.reverse_for_return("R1", "O1", "C1")
    assert r["ok"] and r["earned_clawed"] == 0
    assert acc.adjustments == [] and txns.created == []


def test_missing_ids(monkeypatch):
    _wire(monkeypatch, _FakeTxns([]), _FakeAccounts())
    assert L.reverse_for_return("", "O1", "C1")["ok"] is False
    assert L.reverse_for_return("R1", "", "C1")["ok"] is False
    assert L.reverse_for_return("R1", "O1", "")["ok"] is False


def test_balance_underflow_not_clamped(monkeypatch):
    # Earned 100 but only 50 left (50 already spent on a later order) -> clawback
    # would go negative; do NOT silently clamp -> ok False, no balance change.
    txns = _FakeTxns([_earn("C1", "O1", 100)])
    acc = _FakeAccounts(balance=50, le=100)
    _wire(monkeypatch, txns, acc)
    r = L.reverse_for_return("R1", "O1", "C1")
    assert r["ok"] is False and r["reason"] == "balance_underflow"
    assert acc.adjustments == []


def test_marker_tagged_with_return_id(monkeypatch):
    txns = _FakeTxns([_earn("C1", "O1", 100)])
    acc = _FakeAccounts(balance=100, le=100)
    _wire(monkeypatch, txns, acc)
    L.reverse_for_return("R1", "O1", "C1")
    assert len(txns.created) == 1
    assert txns.created[0]["type"] == "ADJUST"
    assert txns.created[0]["return_id"] == "R1"
    assert txns.created[0]["order_id"] == "O1"


def test_db_unavailable(monkeypatch):
    monkeypatch.setattr(L, "get_loyalty_transaction_repository", lambda: None)
    monkeypatch.setattr(L, "get_loyalty_account_repository", lambda: None)
    assert L.reverse_for_return("R1", "O1", "C1")["reason"] == "loyalty_db_unavailable"
