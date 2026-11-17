"""
IMS 2.0 - loyalty expiry is a resumable, exactly-once operation (PR 1438, R2-A)
================================================================================
The sweep used to mark the EARN lot ``expired`` FIRST, then write the EXPIRE
ledger row and the balance debit without checking either. A failed write was
answered 200 "100 points expired", and because the lot was already marked no
later sweep ever looked at it again: the ledger and the account disagreed for
good.

Now the mark is the LAST step, each step is confirmed from the stored row, and
a later healthy sweep resumes at the first step that is missing:

    balance debit (keyed on the lot)  ->  EXPIRE row (fixed id)  ->  mark

Every test asserts the STORED account and ledger, through the real route.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-loyalty-expiry-recovery")

URL = "/api/v1/loyalty/expire"
T0 = datetime(2026, 1, 1)
DUE = datetime(2026, 1, 31)


class Boom(Exception):
    """A driver failure (pymongo AutoReconnect stands in for this natively)."""


def _earn(txn_id, points, customer_id, created=T0, expires=DUE):
    return {"_id": txn_id, "txn_id": txn_id, "type": "EARN", "points": points,
            "customer_id": customer_id, "created_at": created, "expires_at": expires}


@pytest.fixture
def env(monkeypatch):
    """Real loyalty repositories over strict in-memory collections."""
    from api.routers import loyalty as loyalty_mod
    from database.repositories.loyalty_repository import (
        LoyaltyAccountRepository,
        LoyaltyTransactionRepository,
    )
    from strict_fakes import StrictCollection

    txns = StrictCollection("loyalty_transactions")
    accounts = StrictCollection("loyalty_accounts")
    t_repo, a_repo = LoyaltyTransactionRepository(txns), LoyaltyAccountRepository(accounts)
    monkeypatch.setattr(loyalty_mod, "get_loyalty_transaction_repository", lambda: t_repo)
    monkeypatch.setattr(loyalty_mod, "get_loyalty_account_repository", lambda: a_repo)
    monkeypatch.setattr(loyalty_mod, "get_audit_repository", lambda: None)

    class Env:
        pass

    e = Env()
    e.txns, e.accounts, e.t_repo, e.a_repo, e.mod = txns, accounts, t_repo, a_repo, loyalty_mod

    def seed(customer_id, *lots, balance=None):
        for lot in lots:
            txns.insert_one(lot)
        accounts.insert_one({
            "_id": customer_id, "customer_id": customer_id,
            "balance_points": sum(l["points"] for l in lots) if balance is None else balance,
        })
    e.seed = seed
    e.balance = lambda cid: accounts.find_one({"customer_id": cid})["balance_points"]
    e.expire_rows = lambda cid: [t for t in txns.docs
                                 if t.get("customer_id") == cid and t.get("type") == "EXPIRE"]
    e.marked = lambda lot_id: bool(txns.find_one({"txn_id": lot_id}).get("expired"))
    return e


def _fail(monkeypatch, coll, method, when, *, after_commit=False):
    """Make coll.<method> raise for calls matching ``when``.

    after_commit=False: the write does not happen (a definite failure).
    after_commit=True:  the write lands and only the reply is lost.
    """
    real = getattr(coll, method)

    def wrapped(*a, **k):
        if when(*a, **k):
            if after_commit:
                real(*a, **k)
            raise Boom(f"{coll.name}.{method} failed")
        return real(*a, **k)

    monkeypatch.setattr(coll, method, wrapped)
    return lambda: monkeypatch.setattr(coll, method, real)


def _is_expire_insert(doc, *a, **k):
    return doc.get("type") == "EXPIRE"


def _is_debit(points):
    return lambda flt, upd, *a, **k: (upd.get("$inc") or {}).get("balance_points") == -points


def _is_mark(flt, upd, *a, **k):
    return (upd.get("$set") or {}).get("expired") is True


def _assert_reconciled(env, cid, lots):
    """Ledger and account agree, each lot expired exactly once."""
    rows = env.expire_rows(cid)
    assert sorted((r["source_earn_txn_id"], r["points"]) for r in rows) == sorted(lots.items()), rows
    assert env.balance(cid) == 0
    assert all(env.marked(lot) for lot in lots)


# ---------------------------------------------------------------------------
# Codex R2-A (1): the EXPIRE ledger insert fails
# ---------------------------------------------------------------------------


def test_failed_ledger_insert_is_not_reported_done_and_a_healthy_retry_finishes_it_once(
    client, auth_headers, env, monkeypatch
):
    env.seed("c1", _earn("L1", 100, "c1"))
    heal = _fail(monkeypatch, env.txns, "insert_one", _is_expire_insert)

    failed = client.post(URL, headers=auth_headers)
    assert failed.status_code == 503, failed.text
    detail = failed.json()["detail"]
    assert detail["expired_txns"] == 0 and detail["points_expired"] == 0
    assert detail["incomplete"] == 1 and detail["incomplete_lots"] == ["L1"]
    # The lot is still due: a later sweep will look at it again.
    assert env.marked("L1") is False
    assert env.expire_rows("c1") == []

    heal()
    retry = client.post(URL, headers=auth_headers)
    assert retry.status_code == 200 and retry.json() == {"expired_txns": 1, "points_expired": 100}
    again = client.post(URL, headers=auth_headers)
    assert again.json() == {"expired_txns": 0, "points_expired": 0}
    _assert_reconciled(env, "c1", {"L1": 100})


# ---------------------------------------------------------------------------
# Codex R2-A (2): the account debit fails before it commits
# ---------------------------------------------------------------------------


def test_failed_balance_debit_is_not_reported_done_and_a_healthy_retry_finishes_it_once(
    client, auth_headers, env, monkeypatch
):
    env.seed("c2", _earn("L2", 100, "c2"))
    heal = _fail(monkeypatch, env.accounts, "find_one_and_update", _is_debit(100))

    failed = client.post(URL, headers=auth_headers)
    assert failed.status_code == 503, failed.text
    assert failed.json()["detail"]["incomplete_lots"] == ["L2"]
    # Nothing moved and nothing claims it did.
    assert env.balance("c2") == 100
    assert env.expire_rows("c2") == []
    assert env.marked("L2") is False

    heal()
    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 1, "points_expired": 100}
    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 0, "points_expired": 0}
    _assert_reconciled(env, "c2", {"L2": 100})


# ---------------------------------------------------------------------------
# Lost acknowledgements: the write landed, only the reply was lost
# ---------------------------------------------------------------------------


def test_lost_reply_of_a_committed_ledger_insert_finishes_in_the_same_run(
    client, auth_headers, env, monkeypatch
):
    env.seed("c3", _earn("L3", 100, "c3"))
    _fail(monkeypatch, env.txns, "insert_one", _is_expire_insert, after_commit=True)

    first = client.post(URL, headers=auth_headers)
    assert first.status_code == 200 and first.json() == {"expired_txns": 1, "points_expired": 100}
    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 0, "points_expired": 0}
    _assert_reconciled(env, "c3", {"L3": 100})


def test_lost_reply_of_a_committed_debit_is_never_debited_twice(
    client, auth_headers, env, monkeypatch
):
    env.seed("c4", _earn("L4", 100, "c4"), _earn("VALID", 40, "c4", expires=datetime(2099, 1, 1)))
    heal = _fail(monkeypatch, env.accounts, "find_one_and_update", _is_debit(100), after_commit=True)

    first = client.post(URL, headers=auth_headers)
    assert first.status_code == 200 and first.json() == {"expired_txns": 1, "points_expired": 100}
    heal()
    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 0, "points_expired": 0}
    # 140 - 100, not 140 - 200: the valid lot's 40 points are intact.
    assert env.balance("c4") == 40
    assert [(r["source_earn_txn_id"], r["points"]) for r in env.expire_rows("c4")] == [("L4", 100)]


# ---------------------------------------------------------------------------
# The "finished" mark fails after both money writes landed
# ---------------------------------------------------------------------------


def test_failed_mark_leaves_the_lot_due_and_the_retry_moves_no_money_again(
    client, auth_headers, env, monkeypatch
):
    env.seed("c5", _earn("L5", 100, "c5"))
    heal = _fail(monkeypatch, env.txns, "update_one", _is_mark)

    failed = client.post(URL, headers=auth_headers)
    assert failed.status_code == 503
    assert env.marked("L5") is False
    assert env.balance("c5") == 0 and len(env.expire_rows("c5")) == 1

    heal()
    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 1, "points_expired": 100}
    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 0, "points_expired": 0}
    _assert_reconciled(env, "c5", {"L5": 100})


# ---------------------------------------------------------------------------
# Partial failure across two lots; a redeem between the failure and the retry
# ---------------------------------------------------------------------------


def test_older_lot_whose_debit_failed_still_sheds_its_own_points_on_the_retry(
    client, auth_headers, env, monkeypatch
):
    """The newer lot's expiry went through, the older lot's did not. The retry
    must expire the older lot's 50 -- with pure FIFO the newer lot's EXPIRE row
    was charged to the older lot, which then read as 'nothing left to shed'."""
    env.seed(
        "c6",
        _earn("OLD", 50, "c6", created=T0, expires=DUE),
        _earn("NEW", 100, "c6", created=T0 + timedelta(days=5), expires=DUE),
    )
    heal = _fail(monkeypatch, env.accounts, "find_one_and_update", _is_debit(50))

    failed = client.post(URL, headers=auth_headers)
    assert failed.status_code == 503
    detail = failed.json()["detail"]
    assert (detail["expired_txns"], detail["points_expired"]) == (1, 100)
    assert detail["incomplete_lots"] == ["OLD"]
    assert env.balance("c6") == 50

    heal()
    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 1, "points_expired": 50}
    _assert_reconciled(env, "c6", {"OLD": 50, "NEW": 100})


def test_retry_after_the_ledger_changed_writes_the_amount_that_was_actually_debited(
    client, auth_headers, env, monkeypatch
):
    """Debit landed (100), the EXPIRE row did not; the customer then redeems 60
    of a still-valid lot. The retry must write EXPIRE 100 (what was debited),
    not a recomputed figure, and must not debit again."""
    env.seed(
        "c7",
        _earn("L7", 100, "c7"),
        _earn("VALID", 60, "c7", created=T0 + timedelta(days=5), expires=datetime(2099, 1, 1)),
    )
    heal = _fail(monkeypatch, env.txns, "insert_one", _is_expire_insert)
    assert client.post(URL, headers=auth_headers).status_code == 503
    assert env.balance("c7") == 60
    heal()

    assert env.a_repo.try_debit("c7", 60) is not None
    env.txns.insert_one({"_id": "R7", "txn_id": "R7", "type": "REDEEM", "points": 60,
                         "customer_id": "c7", "created_at": datetime(2026, 6, 1)})

    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 1, "points_expired": 100}
    assert env.balance("c7") == 0
    assert [(r["source_earn_txn_id"], r["points"]) for r in env.expire_rows("c7")] == [("L7", 100)]


def test_points_being_redeemed_right_now_are_not_expired_underneath_the_customer(
    client, auth_headers, env
):
    """A redeem has taken the balance but its REDEEM row is not written yet.
    The sweep must not debit again (balance -100); once the row lands the lot
    simply has nothing left to shed."""
    env.seed("c8", _earn("L8", 100, "c8"), balance=0)

    mid = client.post(URL, headers=auth_headers)
    assert mid.status_code == 503 and mid.json()["detail"]["incomplete_lots"] == ["L8"]
    assert env.balance("c8") == 0 and env.expire_rows("c8") == []
    assert env.marked("L8") is False

    env.txns.insert_one({"_id": "R8", "txn_id": "R8", "type": "REDEEM", "points": 100,
                         "customer_id": "c8", "created_at": datetime(2026, 1, 20)})
    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 0, "points_expired": 0}
    assert env.balance("c8") == 0 and env.expire_rows("c8") == []
    assert env.marked("L8") is True


# ---------------------------------------------------------------------------
# Two sweeps at once
# ---------------------------------------------------------------------------


def test_two_sweeps_holding_the_same_lot_expire_it_once(env):
    """Run B starts and finishes while run A sits between reading the lot and
    debiting it. One debit, one EXPIRE row, counted by exactly one run."""
    env.seed("c9", _earn("L9", 100, "c9"))
    expire_lot = env.mod._expire_lot
    real_debit = env.a_repo.debit_for_expiry
    counted = {}

    def a_is_overtaken(customer_id, lot_id, points, actor=None):
        env.a_repo.debit_for_expiry = real_debit
        counted["B"] = expire_lot(env.t_repo, env.a_repo, "c9", "L9", 100, "u", "run-B")
        return real_debit(customer_id, lot_id, points, actor)

    env.a_repo.debit_for_expiry = a_is_overtaken
    counted["A"] = expire_lot(env.t_repo, env.a_repo, "c9", "L9", 100, "u", "run-A")

    assert sorted(counted.values()) == [0, 100], counted
    _assert_reconciled(env, "c9", {"L9": 100})


# ---------------------------------------------------------------------------
# Read failures and a missing store are not "nothing to expire"
# ---------------------------------------------------------------------------


def test_unreadable_ledger_does_not_mark_the_lot_finished(client, auth_headers, env, monkeypatch):
    """The ledger read used to fail soft to []: FIFO over an empty ledger said
    'nothing left to shed', and the lot was marked finished with its points
    still on the balance, for ever."""
    env.seed("c10", _earn("L10", 100, "c10"))
    heal = _fail(monkeypatch, env.txns, "find",
                 lambda flt=None, *a, **k: (flt or {}).get("customer_id") == "c10")

    failed = client.post(URL, headers=auth_headers)
    assert failed.status_code == 503 and failed.json()["detail"]["incomplete_lots"] == ["L10"]
    assert env.marked("L10") is False and env.balance("c10") == 100

    heal()
    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 1, "points_expired": 100}
    _assert_reconciled(env, "c10", {"L10": 100})


def test_unreadable_due_list_is_an_error_not_a_zero(client, auth_headers, env, monkeypatch):
    env.seed("c11", _earn("L11", 100, "c11"))
    _fail(monkeypatch, env.txns, "find", lambda flt=None, *a, **k: (flt or {}).get("type") == "EARN")

    res = client.post(URL, headers=auth_headers)
    assert res.status_code == 503, res.text
    assert env.marked("L11") is False and env.balance("c11") == 100


def test_missing_loyalty_store_is_an_error_not_a_zero(client, auth_headers, env, monkeypatch):
    monkeypatch.setattr(env.mod, "get_loyalty_account_repository", lambda: None)
    assert client.post(URL, headers=auth_headers).status_code == 503


# ---------------------------------------------------------------------------
# Shapes: timestamps, the account answer, the shared lot arithmetic
# ---------------------------------------------------------------------------


def test_expiry_stamps_are_the_utc_instant_and_the_account_answer_hides_the_markers(
    client, auth_headers, env, monkeypatch
):
    from api.utils import event_time as et

    instant = datetime(2026, 11, 17, 4, 32, 10, tzinfo=timezone.utc)   # 10:02 IST
    monkeypatch.setattr(et, "utc_now", lambda: instant)
    env.seed("c12", _earn("L12", 100, "c12"))

    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 1, "points_expired": 100}
    naive = instant.replace(tzinfo=None)
    row = env.expire_rows("c12")[0]
    assert row["txn_id"] == "EXPIRE-L12" and row["created_at"] == naive
    assert env.txns.find_one({"txn_id": "L12"})["expired_at"] == naive
    account = env.accounts.find_one({"customer_id": "c12"})
    assert account["updated_at"] == naive and account["last_activity_at"] == naive

    shown = client.get("/api/v1/loyalty/account/c12", headers=auth_headers)
    assert shown.status_code == 200, shown.text
    assert shown.json()["account"]["balance_points"] == 0
    assert "money_ledger" not in shown.json()["account"]


def test_an_expiry_sheds_its_own_lot_not_an_older_lot_that_is_still_valid():
    """Shared lot arithmetic. OLD (50) outlives NEW (100) because the expiry
    period was shortened in between. NEW expired first; when OLD later expires
    it must still shed its 50."""
    from api.services.loyalty_engine import expirable_points_by_lot

    ledger = [
        {"txn_id": "OLD", "type": "EARN", "points": 50, "created_at": T0,
         "expires_at": datetime(2026, 6, 1)},
        {"txn_id": "NEW", "type": "EARN", "points": 100, "created_at": T0 + timedelta(days=5),
         "expires_at": DUE, "expired": True},
        {"txn_id": "EXPIRE-NEW", "type": "EXPIRE", "points": 100, "source_earn_txn_id": "NEW",
         "created_at": DUE + timedelta(days=1)},
    ]
    assert expirable_points_by_lot(ledger, datetime(2026, 7, 1)) == {"OLD": 50}


def test_an_expiry_row_without_a_source_lot_still_consumes_oldest_first():
    from api.services.loyalty_engine import expirable_points_by_lot

    ledger = [
        {"txn_id": "A", "type": "EARN", "points": 100, "created_at": T0, "expires_at": DUE},
        {"txn_id": "X", "type": "EXPIRE", "points": 30, "created_at": T0 + timedelta(days=2)},
    ]
    assert expirable_points_by_lot(ledger, datetime(2026, 2, 1)) == {"A": 70}
