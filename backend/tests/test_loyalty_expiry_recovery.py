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


def test_lost_reply_of_a_committed_mark_finishes_and_is_counted_in_the_same_run(
    client, auth_headers, env, monkeypatch
):
    env.seed("c5b", _earn("L5b", 100, "c5b"))
    _fail(monkeypatch, env.txns, "update_one", _is_mark, after_commit=True)

    first = client.post(URL, headers=auth_headers)
    assert first.status_code == 200 and first.json() == {"expired_txns": 1, "points_expired": 100}
    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 0, "points_expired": 0}
    _assert_reconciled(env, "c5b", {"L5b": 100})


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
    The sweep must not debit again (balance -100), and must not mark the lot
    finished either: the balance does not hold the lot, so the lot waits. Once
    the row lands, the ledger says the lot holds nothing and it finishes."""
    env.seed("c8", _earn("L8", 100, "c8"), balance=0)

    first = client.post(URL, headers=auth_headers)
    assert first.status_code == 503 and first.json()["detail"]["incomplete_lots"] == ["L8"]
    assert env.balance("c8") == 0 and env.expire_rows("c8") == []
    assert env.marked("L8") is False

    env.txns.insert_one({"_id": "R8", "txn_id": "R8", "type": "REDEEM", "points": 100,
                         "customer_id": "c8", "created_at": datetime(2026, 1, 20)})
    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 0, "points_expired": 0}
    assert env.balance("c8") == 0 and env.expire_rows("c8") == []
    assert env.marked("L8") is True


def _redeem(env, cid, txn_id, points, when):
    """The /loyalty/redeem route's two writes: guarded debit, then the row."""
    assert env.a_repo.try_debit(cid, points) is not None
    env.txns.insert_one({"_id": txn_id, "txn_id": txn_id, "type": "REDEEM", "points": points,
                         "customer_id": cid, "created_at": when})


def test_redeem_landing_after_the_sweep_read_the_ledger_never_takes_a_valid_lots_points(
    client, auth_headers, env, monkeypatch
):
    """Due lot A (100) and valid lot B (60); balance 160. The customer redeems
    60 after the sweep has read the ledger. FIFO charges the redeem to A, so A
    sheds 40 and B keeps 60. Before: the sweep debited its stale 100 and the
    customer was left with 0 -- B's 60 valid points expired."""
    env.seed("c14", _earn("A14", 100, "c14"),
             _earn("B14", 60, "c14", created=T0 + timedelta(days=5), expires=datetime(2099, 1, 1)))
    real = env.t_repo.ledger_for_expiry

    def ledger_then_redeem(customer_id):
        rows = real(customer_id)
        _redeem(env, "c14", "R14", 60, T0 + timedelta(days=10))
        return rows

    monkeypatch.setattr(env.t_repo, "ledger_for_expiry", ledger_then_redeem)
    first = client.post(URL, headers=auth_headers)
    monkeypatch.setattr(env.t_repo, "ledger_for_expiry", real)

    # The account changed after the ledger read, so the lot waits (nothing
    # moved) and the next sweep, reading the REDEEM row, sheds A's real 40.
    assert first.status_code == 503 and first.json()["detail"]["incomplete_lots"] == ["A14"]
    assert env.balance("c14") == 100 and env.expire_rows("c14") == []
    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 1, "points_expired": 40}
    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 0, "points_expired": 0}
    assert env.balance("c14") == 60
    assert [(r["source_earn_txn_id"], r["points"]) for r in env.expire_rows("c14")] == [("A14", 40)]
    assert env.marked("A14") is True and env.marked("B14") is False
    from api.services.loyalty_engine import expirable_points_by_lot
    assert expirable_points_by_lot(list(env.txns.docs), datetime(2100, 1, 1)) == {"B14": 60}


def test_redeem_landing_between_the_balance_read_and_the_debit_makes_the_lot_wait(
    client, auth_headers, env, monkeypatch
):
    """Same customer shape; the redeem lands inside debit_for_expiry, after it
    read the balance and before its guarded write. The guard (balance must
    still hold the valid lots' 60 after the debit) refuses, the lot waits, and
    the next sweep sheds the right 40."""
    env.seed("c15", _earn("A15", 100, "c15"),
             _earn("B15", 60, "c15", created=T0 + timedelta(days=5), expires=datetime(2099, 1, 1)))
    real = env.accounts.find_one_and_update
    fired = []

    def redeem_first(flt, upd, *a, **k):
        if not fired and "money_ledger.idempotency_key" in flt:
            fired.append(1)
            monkeypatch.setattr(env.accounts, "find_one_and_update", real)
            _redeem(env, "c15", "R15", 60, T0 + timedelta(days=10))
        return real(flt, upd, *a, **k)

    monkeypatch.setattr(env.accounts, "find_one_and_update", redeem_first)
    first = client.post(URL, headers=auth_headers)
    assert fired and first.status_code == 503, first.text
    assert first.json()["detail"]["incomplete_lots"] == ["A15"]
    assert env.balance("c15") == 100 and env.expire_rows("c15") == []
    assert env.marked("A15") is False

    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 1, "points_expired": 40}
    assert env.balance("c15") == 60
    assert [(r["source_earn_txn_id"], r["points"]) for r in env.expire_rows("c15")] == [("A15", 40)]


def test_redeem_and_earn_landing_after_the_ledger_read_never_take_the_new_lots_points(
    client, auth_headers, env, monkeypatch
):
    """Due lot A (100), balance 100. After the sweep read the ledger the
    customer redeems 60 (FIFO: from A, which now holds 40) and earns a fresh
    lot N (100). The balance is 140 and the valid-lots floor the sweep read
    is 0, so the floor alone let it take its stale 100 -- 60 of them N's.
    The account changed after the ledger read, so the debit must refuse; the
    lot waits and the next sweep sheds A's real 40."""
    env.seed("c16", _earn("A16", 100, "c16"))
    real = env.t_repo.ledger_for_expiry

    def ledger_then_redeem_and_earn(customer_id):
        rows = real(customer_id)
        _redeem(env, "c16", "R16", 60, T0 + timedelta(days=10))
        env.txns.insert_one(_earn("N16", 100, "c16", created=T0 + timedelta(days=20),
                                  expires=datetime(2099, 1, 1)))
        env.a_repo.adjust_balance("c16", 100, delta_lifetime_earned=100)
        return rows

    monkeypatch.setattr(env.t_repo, "ledger_for_expiry", ledger_then_redeem_and_earn)
    first = client.post(URL, headers=auth_headers)
    monkeypatch.setattr(env.t_repo, "ledger_for_expiry", real)

    assert env.balance("c16") >= 100, "the fresh lot's points were expired"
    if first.status_code == 503:
        assert first.json()["detail"]["incomplete_lots"] == ["A16"]
        assert env.marked("A16") is False and env.expire_rows("c16") == []
        assert client.post(URL, headers=auth_headers).json() == {
            "expired_txns": 1, "points_expired": 40}
    assert env.balance("c16") == 100
    assert [(r["source_earn_txn_id"], r["points"]) for r in env.expire_rows("c16")] == [("A16", 40)]
    assert env.marked("A16") is True and env.marked("N16") is False


def test_two_due_lots_of_one_customer_both_finish_in_one_healthy_sweep(client, auth_headers, env):
    """The sweep's own first debit changes the account; that must not make the
    customer's second due lot wait."""
    env.seed("c17", _earn("A17", 100, "c17"),
             _earn("B17", 50, "c17", created=T0 + timedelta(days=1)))
    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 2, "points_expired": 150}
    _assert_reconciled(env, "c17", {"A17": 100, "B17": 50})


# ---------------------------------------------------------------------------
# A write whose ledger row and balance move have not both landed (2026-11-22
# review, MUST-FIX 1 and 2, builder limit 2). The lot must never finish
# having shed less than it holds.
# ---------------------------------------------------------------------------


def _with_older_no_expiry_lot(env, cid):
    """ADJUST +50 (no expiry), due L1 100, valid L2 100; balance 250."""
    env.seed(cid,
             {"_id": cid + "-ADJ", "txn_id": cid + "-ADJ", "type": "ADJUST", "points": 50,
              "customer_id": cid, "created_at": T0 - timedelta(days=30)},
             _earn(cid + "-L1", 100, cid),
             _earn(cid + "-L2", 100, cid, created=T0 + timedelta(days=5),
                   expires=datetime(2099, 1, 1)))


def _assert_l1_shed_50(env, client, auth_headers, cid):
    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 1, "points_expired": 50}
    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 0, "points_expired": 0}
    # FIFO: the redeem of 100 ate ADJ 50 + L1 50, so L1 shed 50 and L2 keeps 100.
    assert env.balance(cid) == 100
    assert [(r["source_earn_txn_id"], r["points"]) for r in env.expire_rows(cid)] == [(cid + "-L1", 50)]
    assert env.marked(cid + "-L1") is True and env.marked(cid + "-L2") is False


def test_redeem_after_the_ledger_read_that_leaves_nothing_above_the_floor_makes_the_lot_wait(
    client, auth_headers, env, monkeypatch
):
    """MUST-FIX 2. The stale ledger prices L1 at 100 with a floor of 150; the
    redeem leaves 150, so the take came out at 0 and the old code returned 0
    BEFORE the version guard and marked L1 finished with no EXPIRE row: 50
    expired points spendable for ever."""
    _with_older_no_expiry_lot(env, "c18")
    real = env.t_repo.ledger_for_expiry

    def ledger_then_redeem(customer_id):
        rows = real(customer_id)
        _redeem(env, "c18", "R18", 100, T0 + timedelta(days=10))
        return rows

    monkeypatch.setattr(env.t_repo, "ledger_for_expiry", ledger_then_redeem)
    first = client.post(URL, headers=auth_headers)
    monkeypatch.setattr(env.t_repo, "ledger_for_expiry", real)

    assert first.status_code == 503 and first.json()["detail"]["incomplete_lots"] == ["c18-L1"]
    assert env.marked("c18-L1") is False and env.expire_rows("c18") == []
    assert env.balance("c18") == 150
    _assert_l1_shed_50(env, client, auth_headers, "c18")


def test_redeem_debited_before_the_sweep_but_written_after_its_ledger_read_makes_the_lot_wait(
    client, auth_headers, env, monkeypatch
):
    """MUST-FIX 2, alternate route the version guard cannot see: the redeem's
    debit lands BEFORE the sweep reads the account stamp, its REDEEM row after
    the sweep read the ledger."""
    _with_older_no_expiry_lot(env, "c19")
    real_stamp, real_ledger = env.a_repo.expiry_account_version, env.t_repo.ledger_for_expiry

    def debit_then_stamp(customer_id):
        assert env.a_repo.try_debit("c19", 100) is not None
        return real_stamp(customer_id)

    def ledger_then_row(customer_id):
        rows = real_ledger(customer_id)
        env.txns.insert_one({"_id": "R19", "txn_id": "R19", "type": "REDEEM", "points": 100,
                             "customer_id": "c19", "created_at": T0 + timedelta(days=10)})
        return rows

    monkeypatch.setattr(env.a_repo, "expiry_account_version", debit_then_stamp)
    monkeypatch.setattr(env.t_repo, "ledger_for_expiry", ledger_then_row)
    first = client.post(URL, headers=auth_headers)
    monkeypatch.setattr(env.a_repo, "expiry_account_version", real_stamp)
    monkeypatch.setattr(env.t_repo, "ledger_for_expiry", real_ledger)

    assert first.status_code == 503 and first.json()["detail"]["incomplete_lots"] == ["c19-L1"]
    assert env.marked("c19-L1") is False and env.expire_rows("c19") == []
    _assert_l1_shed_50(env, client, auth_headers, "c19")


def test_earn_written_but_not_yet_on_the_balance_makes_the_lot_wait(
    client, auth_headers, env, monkeypatch
):
    """Builder limit 2. The earn route writes the EARN row, then bumps the
    balance. Row in the sweep's ledger, bump after the debit: the floor
    counted the new lot and the old code shed 0 and finished the due lot, so
    the customer kept 100 expired points once the bump landed."""
    env.seed("c20", _earn("c20-L1", 100, "c20"))
    real_stamp = env.a_repo.expiry_account_version

    def stamp_then_earn_row(customer_id):
        out = real_stamp(customer_id)
        env.txns.insert_one(_earn("c20-N", 100, "c20", created=T0 + timedelta(days=20),
                                  expires=datetime(2099, 1, 1)))
        return out

    monkeypatch.setattr(env.a_repo, "expiry_account_version", stamp_then_earn_row)
    first = client.post(URL, headers=auth_headers)
    monkeypatch.setattr(env.a_repo, "expiry_account_version", real_stamp)
    env.a_repo.adjust_balance("c20", 100, delta_lifetime_earned=100)   # the bump lands

    assert first.status_code == 503 and first.json()["detail"]["incomplete_lots"] == ["c20-L1"]
    assert env.marked("c20-L1") is False and env.expire_rows("c20") == []
    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 1, "points_expired": 100}
    assert env.balance("c20") == 100
    assert [(r["source_earn_txn_id"], r["points"]) for r in env.expire_rows("c20")] == [("c20-L1", 100)]
    assert env.marked("c20-N") is False


def test_a_pending_reversal_restore_does_not_shield_expired_points(
    client, auth_headers, env, monkeypatch
):
    """MUST-FIX 1. Redeem 30 on order O, cancel O: the reversal marker ADJUST +30
    is written first and its balance write fails (marker pending, healed by a
    retried cancel). The sweep counted the +30 as held by the balance, shed 40
    of L1's 70 and finished L1; the heal then left the customer 60 instead of
    30. Expected: L1 sheds its 70 now, the heal restores the 30."""
    env.seed("c21", _earn("c21-L1", 100, "c21"))
    assert env.a_repo.try_debit("c21", 30) is not None       # the redeem route's two writes
    env.txns.insert_one({"_id": "R21", "txn_id": "R21", "type": "REDEEM", "points": 30,
                         "order_id": "O21", "customer_id": "c21", "created_at": T0 + timedelta(days=3)})
    heal = _fail(monkeypatch, env.accounts, "find_one_and_update",
                 lambda flt, *a, **k: "applied_reversals" in flt)
    out = env.mod.reverse_for_cancel("O21", "c21")
    heal()
    assert out.get("ok") is False and env.balance("c21") == 70
    pending = [t for t in env.txns.docs if t.get("type") == "ADJUST" and t.get("customer_id") == "c21"]
    assert len(pending) == 1 and pending[0]["points"] == 30

    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 1, "points_expired": 70}
    assert env.balance("c21") == 0 and env.marked("c21-L1") is True

    healed = env.mod.reverse_for_cancel("O21", "c21")
    assert healed.get("ok") is True and healed.get("completed_now") is True
    assert env.balance("c21") == 30
    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 0, "points_expired": 0}
    assert [(r["source_earn_txn_id"], r["points"]) for r in env.expire_rows("c21")] == [("c21-L1", 70)]


def test_an_applied_reversal_restore_is_held_like_any_valid_lot(
    client, auth_headers, env
):
    """Control for the test above: once the restore is ON the balance (its id
    in applied_reversals), it is a valid no-expiry lot and the floor keeps it."""
    env.seed("c22", _earn("c22-L1", 100, "c22"))
    assert env.a_repo.try_debit("c22", 30) is not None       # the redeem route's two writes
    env.txns.insert_one({"_id": "R22", "txn_id": "R22", "type": "REDEEM", "points": 30,
                         "order_id": "O22", "customer_id": "c22", "created_at": T0 + timedelta(days=3)})
    assert env.mod.reverse_for_cancel("O22", "c22").get("ok") is True
    assert env.balance("c22") == 100

    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 1, "points_expired": 70}
    assert env.balance("c22") == 30


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

    def a_is_overtaken(customer_id, lot_id, points, **k):
        env.a_repo.debit_for_expiry = real_debit
        counted["B"] = expire_lot(env.t_repo, env.a_repo, "c9", "L9", 100, "u", "run-B")
        return real_debit(customer_id, lot_id, points, **k)

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


def test_unreadable_account_is_not_read_as_not_debited_yet(client, auth_headers, env, monkeypatch):
    """The debit landed earlier (EXPIRE row missing). If the account cannot be
    read, the lot must wait. Read as "not debited yet" it would be re-computed
    from the ledger -- which by now says the lot has nothing left (a later
    redeem was charged to it) -- and be marked finished with its EXPIRE row
    missing for good."""
    env.seed("c13", _earn("L13", 100, "c13"))
    heal_insert = _fail(monkeypatch, env.txns, "insert_one", _is_expire_insert)
    assert client.post(URL, headers=auth_headers).status_code == 503
    heal_insert()
    assert env.balance("c13") == 0

    # The customer earns a fresh lot and spends it before the retry.
    env.txns.insert_one(_earn("VALID", 100, "c13", created=T0 + timedelta(days=40),
                              expires=datetime(2099, 1, 1)))
    env.a_repo.adjust_balance("c13", 100)
    assert env.a_repo.try_debit("c13", 100) is not None
    env.txns.insert_one({"_id": "R13", "txn_id": "R13", "type": "REDEEM", "points": 100,
                         "customer_id": "c13", "created_at": T0 + timedelta(days=41)})

    heal_read = _fail(monkeypatch, env.accounts, "find_one", lambda *a, **k: True)
    blind = client.post(URL, headers=auth_headers)
    assert blind.status_code == 503 and blind.json()["detail"]["incomplete_lots"] == ["L13"]
    heal_read()
    assert env.marked("L13") is False and env.expire_rows("c13") == []

    assert client.post(URL, headers=auth_headers).json() == {"expired_txns": 1, "points_expired": 100}
    _assert_reconciled(env, "c13", {"L13": 100})
    # Ledger and balance agree: nothing is left for the fresh lot to shed later.
    from api.services.loyalty_engine import expirable_points_by_lot
    assert expirable_points_by_lot(list(env.txns.docs), datetime(2100, 1, 1)) == {}


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


# ---------------------------------------------------------------------------
# Manual deductions (POST /loyalty/adjust with points < 0). The route stores
# the row as points=abs(points) with the sign only in "delta"; the FIFO read
# "points" alone, so a -50 counted as a +50 lot that never expires. The floor
# then outweighed the balance for good: every sweep 503, expired points
# spendable. Driven through the real adjust route, so the row is the one the
# route writes.
# ---------------------------------------------------------------------------

ADJUST_URL = "/api/v1/loyalty/adjust"


def test_a_manual_deduction_then_expiry_finishes_the_lot(client, auth_headers, env):
    env.seed("c30", _earn("A30", 100, "c30"))
    r = client.post(ADJUST_URL, headers=auth_headers,
                    json={"customer_id": "c30", "points": -50, "reason": "manual fix"})
    assert r.status_code == 200, r.text
    assert env.balance("c30") == 50

    first = client.post(URL, headers=auth_headers)
    assert first.status_code == 200, first.text
    assert first.json() == {"expired_txns": 1, "points_expired": 50}
    _assert_reconciled(env, "c30", {"A30": 50})
    again = client.post(URL, headers=auth_headers)
    assert again.json() == {"expired_txns": 0, "points_expired": 0}
    _assert_reconciled(env, "c30", {"A30": 50})


def test_a_manual_deduction_is_charged_to_the_due_lot_and_the_valid_lot_is_kept(
    client, auth_headers, env
):
    """Due A 100, valid B 50, balance 150; a manual -30. FIFO charges the 30
    to A, so A sheds 70 and the customer keeps B's 50."""
    later = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=300)
    env.seed("c31", _earn("A31", 100, "c31"),
             _earn("B31", 50, "c31", created=T0 + timedelta(days=10), expires=later))
    r = client.post(ADJUST_URL, headers=auth_headers,
                    json={"customer_id": "c31", "points": -30, "reason": "manual fix"})
    assert r.status_code == 200, r.text

    res = client.post(URL, headers=auth_headers)
    assert res.status_code == 200, res.text
    assert res.json() == {"expired_txns": 1, "points_expired": 70}
    assert env.balance("c31") == 50
    assert [(x["source_earn_txn_id"], x["points"]) for x in env.expire_rows("c31")] == [("A31", 70)]
    assert env.marked("A31") is True and env.marked("B31") is False


def test_the_shared_lot_arithmetic_reads_the_signed_delta_of_a_manual_adjust():
    """The engine rule every caller routes through: a manual-adjust row's sign
    lives in "delta" (points holds the size). Rows without delta (reversal
    markers, edit re-gate claws) keep their signed points."""
    from api.services.loyalty_engine import (
        expirable_points_by_lot,
        points_held_by_unexpired_lots,
    )

    now = datetime(2026, 7, 1)
    ledger = [
        {"txn_id": "A", "type": "EARN", "points": 100, "created_at": T0, "expires_at": DUE},
        {"txn_id": "B", "type": "EARN", "points": 50, "created_at": T0 + timedelta(days=1),
         "expires_at": datetime(2027, 1, 1)},
        {"txn_id": "M1", "type": "ADJUST", "points": 30, "delta": -30,
         "created_at": T0 + timedelta(days=2)},
        {"txn_id": "M2", "type": "ADJUST", "points": 20, "delta": 20,
         "created_at": T0 + timedelta(days=3)},
        {"txn_id": "C", "type": "ADJUST", "points": -10, "created_at": T0 + timedelta(days=4)},
    ]
    # -30 and -10 come off A (oldest first); M2 is a +20 lot with no expiry.
    assert expirable_points_by_lot(ledger, now) == {"A": 60}
    assert points_held_by_unexpired_lots(ledger, now) == 50 + 20
