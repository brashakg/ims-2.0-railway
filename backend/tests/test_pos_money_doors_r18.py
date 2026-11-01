"""
IMS 2.0 - POS money doors, round-18 money review of PR #1264 (head e5ed9db9)
============================================================================
A VOID redemption is answered against what THIS press asks for; a claim by
an explicit token is idempotent and every token path (the counter's
give-back, the in-request rollback, the re-assert) is pinned against a
token-aware line; the "credit taken but not on the bill" task counts only a
STORE_CREDIT payment of that amount, never tells staff to debit again, and
closes itself; the loyalty backfill stops on a conflict with a balance due.
No emoji (cp1252).
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import HTTPException  # noqa: E402

from api.routers import customers as cust_router  # noqa: E402
from tests.test_pos_money_doors import (  # noqa: E402,F401
    _advance_client,
    _hdr,
    _ledger,
    _order,
    _stored,
    returns_router,
    till,
)
from tests.test_pos_money_doors_r8 import _two_unit_bill  # noqa: E402
from tests.test_pos_money_doors_r9 import _ago  # noqa: E402
from tests.test_pos_money_doors_r12 import _repo, tasks  # noqa: E402,F401
from tests.test_pos_money_doors_r15 import _boom_at_money_out, _refs  # noqa: E402
from tests.test_pos_money_doors_r17 import KEY, _balance, _redeem, redeem_env  # noqa: E402,F401

REAL_CLAIM = returns_router._claim_returnable_qty
REAL_RELEASE = returns_router._release_returnable_qty


class TokenLines:
    """The orders collection's positional semantics for bill lines: the
    element filter (item, cap, returned_qty floor, claim token in / not in)
    and $inc / $addToSet / $pull on the matched element -- as Mongo does."""

    def __init__(self, lines, real=None):
        self.lines = lines
        self.real = real  # the bill's own document (its refund holds) lives here

    def __getattr__(self, name):
        return getattr(self.real, name)

    @staticmethod
    def _match(line, elem):
        for key, cond in elem.items():
            val = line.get(key)
            if key == "$or":
                if not any(TokenLines._match(line, sub) for sub in cond):
                    return False
            elif isinstance(cond, dict):
                for op, arg in cond.items():
                    if op == "$lte" and not (val is not None and val <= arg):
                        return False
                    if op == "$gte" and not (val is not None and val >= arg):
                        return False
                    if op == "$lt" and not (val is not None and val < arg):
                        return False
                    if op == "$exists" and (key in line) != bool(arg):
                        return False
                    if op == "$ne" and (arg in (val or []) if isinstance(val, list) else val == arg):
                        return False
            elif isinstance(val, list):
                if cond not in val:
                    return False
            elif val != cond:
                return False
        return True

    def _hit(self, match):
        elem = (match.get("items") or {}).get("$elemMatch") or {}
        return next((ln for ln in self.lines if self._match(ln, elem)), None)

    def find_one_and_update(self, match, update, **kw):
        line = self._hit(match)
        if line is None:
            return None
        for field, delta in (update.get("$inc") or {}).items():
            leaf = field.split(".")[-1]
            line[leaf] = (line.get(leaf) or 0) + delta
        for field, tok in (update.get("$addToSet") or {}).items():
            bucket = line.setdefault(field.split(".")[-1], [])
            if tok not in bucket:
                bucket.append(tok)
        for field, tok in (update.get("$pull") or {}).items():
            leaf = field.split(".")[-1]
            line[leaf] = [t for t in line.get(leaf) or [] if t != tok]
        return {"order_id": "ORD-1", "items": self.lines}

    def find_one(self, match, *a, **k):
        if "items" not in match:
            return self.real.find_one(match, *a, **k)
        return {"order_id": "ORD-1", "items": self.lines} if self._hit(match) is not None else None


# =========================================================================== #
# 1: a VOID redemption is answered against what THIS press asks for
# =========================================================================== #
def test_a_voided_line_whose_debit_landed_is_not_done_for_another_amount(till, redeem_env, monkeypatch):
    repo = redeem_env
    assert repo.try_debit_store_credit("CUST-1", 100.0, guard_ref=KEY)  # the debit landed
    _ledger(till).insert_one({"_id": KEY, "entry_id": KEY, "customer_id": "CUST-1", "type": "VOID",
                              "final_type": "REDEEMED", "amount": 100.0, "delta": 0.0, "void_delta": -100.0,
                              "void_settled": False, "pending_at": _ago(0)})
    with pytest.raises(HTTPException) as e:
        _redeem(150.0)
    assert e.value.status_code == 409 and "already took Rs 100.00" in e.value.detail
    row = _ledger(till).find_one({"entry_id": KEY})
    assert row["type"] == "REDEEMED" and row["delta"] == -100.0 and _balance(till) == 400.0  # healed, once
    assert _redeem(100.0)["entry"]["type"] == "REDEEMED" and _balance(till) == 400.0


# =========================================================================== #
# 2: a token claim is idempotent; every token path is pinned
# =========================================================================== #
def test_a_claim_by_its_token_never_counts_the_units_twice(monkeypatch):
    lines = TokenLines([{"item_id": "li1", "product_id": "P1", "quantity": 2, "returned_qty": 1.0,
                         "claim_tokens": ["RET-H:0"]}])
    monkeypatch.setattr(returns_router, "_orders_coll", lambda: lines)
    line = {"item_id": "li1", "product_id": "P1", "quantity": 2}
    assert REAL_CLAIM("ORD-1", line, 1.0, token="RET-H:0") is True  # already held: once
    assert lines.lines[0]["returned_qty"] == 1.0


def test_a_re_assert_after_a_lost_release_never_takes_the_unit_twice(monkeypatch):
    lines = TokenLines([{"item_id": "li1", "product_id": "P1", "quantity": 2, "returned_qty": 1.0,
                         "claim_tokens": ["RET-H:0"]}])  # the release never landed
    monkeypatch.setattr(returns_router, "_orders_coll", lambda: lines)
    monkeypatch.setattr(returns_router, "_keep_order_refund_hold", lambda *a, **k: None)
    spec = {"order_id": "ORD-1", "return_id": "RET-H", "net_amount": 1800.0,
            "lines": [{"item_id": "li1", "product_id": "P1", "qty": 1.0, "purchased": 2.0, "token": "RET-H:0"}]}
    returns_router._reassert_return_claims({"entry_id": "RET-H:credit", "undo_on_void": spec})
    assert lines.lines[0]["returned_qty"] == 1.0 and lines.lines[0]["claim_tokens"] == ["RET-H:0"]


def test_a_re_assert_after_a_landed_release_puts_the_same_token_back(monkeypatch):
    lines = TokenLines([{"item_id": "li1", "product_id": "P1", "quantity": 2, "returned_qty": 0.0,
                         "claim_tokens": []}])
    monkeypatch.setattr(returns_router, "_orders_coll", lambda: lines)
    monkeypatch.setattr(returns_router, "_keep_order_refund_hold", lambda *a, **k: None)
    spec = {"order_id": "ORD-1", "return_id": "RET-H", "net_amount": 1800.0,
            "lines": [{"item_id": "li1", "product_id": "P1", "qty": 1.0, "purchased": 2.0, "token": "RET-H:0"}]}
    returns_router._reassert_return_claims({"entry_id": "RET-H:credit", "undo_on_void": spec})
    assert lines.lines[0]["returned_qty"] == 1.0 and lines.lines[0]["claim_tokens"] == ["RET-H:0"]


@pytest.mark.usefixtures("gst_numbers")
def test_the_counter_gives_back_by_its_token_never_another_returns_unit(till, monkeypatch, tasks):
    payload = _two_unit_bill(till, monkeypatch)
    payload["refund_tenders"] = [{"method": "CASH", "amount": 1800.0}]
    monkeypatch.setattr(returns_router, "_claim_returnable_qty", REAL_CLAIM)
    monkeypatch.setattr(returns_router, "_release_returnable_qty", REAL_RELEASE)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: True)
    lines = TokenLines([{"item_id": "li1", "product_id": "P1", "quantity": 2, "returned_qty": 0.0,
                         "claim_tokens": []}], till["db"].get_collection("orders"))
    monkeypatch.setattr(returns_router, "_orders_coll", lambda: lines)
    monkeypatch.setattr(returns_router, "generate_return_id", lambda: "RET-C")
    _boom_at_money_out(monkeypatch)
    with pytest.raises(RuntimeError):
        _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    # Meanwhile another return holds the line's other unit; the counter's
    # give-back is then re-run (its done stamp lost): it takes nothing more.
    assert lines.lines[0]["returned_qty"] == 0.0 and lines.lines[0]["claim_tokens"] == []
    lines.lines[0].update(returned_qty=1.0, claim_tokens=["RET-OTHER:0"])
    assert REAL_RELEASE("ORD-1", {"item_id": "li1", "product_id": "P1"}, 1.0, token="RET-C:0") is True
    assert lines.lines[0]["returned_qty"] == 1.0 and lines.lines[0]["claim_tokens"] == ["RET-OTHER:0"]


@pytest.mark.usefixtures("gst_numbers")
def test_the_in_request_rollback_releases_by_token(till, monkeypatch):
    payload = _two_unit_bill(till, monkeypatch)
    monkeypatch.setattr(returns_router, "_claim_returnable_qty", REAL_CLAIM)
    monkeypatch.setattr(returns_router, "_release_returnable_qty", REAL_RELEASE)
    li2 = {"item_id": "li2", "product_id": "P2", "product_name": "Case", "sku": "C2", "quantity": 1,
           "unit_price": 84.75, "gst_rate": 18.0, "taxable_value": 84.75, "tax_amount": 15.25}
    till["db"].get_collection("orders").update_one(
        {"order_id": "ORD-1"}, {"$set": {"items": [_stored(till)["items"][0], li2], "grand_total": 3700.0,
                                         "amount_paid": 3700.0}})
    payload["items"].append({"order_item_id": "li2", "product_id": "P2", "product_name": "Case", "sku": "C2",
                             "return_qty": 1, "unit_price": 84.75, "reason": "DEFECTIVE", "condition": "GOOD"})
    payload["refund_tenders"] = [{"method": "CASH", "amount": 1900.0}]
    # li1 has room; li2's last unit went to another return since the read.
    lines = TokenLines([{"item_id": "li1", "product_id": "P1", "quantity": 2, "returned_qty": 1.0,
                         "claim_tokens": ["RET-OTHER:0"]},
                        {"item_id": "li2", "product_id": "P2", "quantity": 1, "returned_qty": 1.0,
                         "claim_tokens": ["RET-OTHER:1"]}], till["db"].get_collection("orders"))
    monkeypatch.setattr(returns_router, "_orders_coll", lambda: lines)
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert r.status_code == 409, r.text
    assert lines.lines[0]["returned_qty"] == 1.0 and lines.lines[0]["claim_tokens"] == ["RET-OTHER:0"]


# =========================================================================== #
# 3: the "taken but not on the bill" task
# =========================================================================== #
@pytest.fixture()
def bill(till, monkeypatch):
    def _make(payments):
        till["db"].get_collection("orders").insert_one(_order(order_id="O1", payments=payments))
        monkeypatch.setattr("api.dependencies.get_order_repository", lambda: till["orders"], raising=False)
    return _make


def _age_and_reconcile(till, repo, n=1):
    _ledger(till).update_one({"entry_id": KEY}, {"$set": {"pending_at": _ago(30)}})
    for _ in range(n):
        cust_router.reconcile_pending_credits(repo, ledger=_ledger(till))


def test_another_tender_reusing_the_legs_key_never_counts_as_billed(till, redeem_env, bill, tasks):
    bill([{"payment_id": "p1", "method": "CARD", "amount": 100.0, "idempotency_key": "k:leg:0"}])
    _redeem()
    _age_and_reconcile(till, redeem_env)
    assert _refs(tasks) == [f"store_credit_unbilled:{KEY}"]


def test_the_task_never_tells_staff_to_debit_again(till, redeem_env, bill, tasks):
    bill([])
    _redeem()
    _age_and_reconcile(till, redeem_env)
    text = tasks[0]["description"]
    # Round 19: ONE remedy -- press again; if the customer left, the real door
    # (Issue on the card, quoting the line) -- never a second store-credit payment.
    assert "Press Complete Sale again on that open bill" in text
    assert f"Store credit > Issue, Rs 100.00, with Reason {KEY}" in text
    assert "record the store-credit payment" not in text and "never Issue" not in text


def test_the_task_closes_itself_once_the_leg_is_billed(till, redeem_env, bill, tasks, monkeypatch):
    closed = []
    monkeypatch.setattr(returns_router, "_close_task", lambda ref, *a, **k: closed.append(ref))
    bill([])
    _redeem()
    _age_and_reconcile(till, redeem_env, n=2)
    assert _refs(tasks) == [f"store_credit_unbilled:{KEY}"] and closed == []  # once, still open
    till["db"].get_collection("orders").update_one({"order_id": "O1"}, {"$set": {"payments": [
        {"payment_id": "p9", "method": "STORE_CREDIT", "amount": 100.0, "idempotency_key": "k:leg:0"}]}})
    cust_router.reconcile_pending_credits(redeem_env, ledger=_ledger(till))
    assert closed == [f"store_credit_unbilled:{KEY}"]
    assert _ledger(till).find_one({"entry_id": KEY})["bill_checked"] is True


def test_the_bill_scan_runs_only_in_the_scheduled_pass(till, redeem_env, bill, tasks):
    bill([])
    _redeem()
    _ledger(till).update_one({"entry_id": KEY}, {"$set": {"pending_at": _ago(30)}})
    cust_router.reconcile_pending_credits(redeem_env, customer_id="CUST-1", ledger=_ledger(till))  # a press
    assert tasks == []


# =========================================================================== #
# 8: the loyalty backfill stops on a conflict on a bill with a balance due
# =========================================================================== #
def test_a_conflict_on_a_bill_with_a_balance_due_stops_the_backfill(monkeypatch, capsys):
    from scripts import backfill_loyalty_tender_claims as bl
    from tests.strict_fakes import StrictDB

    db = StrictDB()
    db["orders"].insert_one({"order_id": "O-1", "balance_due": 400.0, "payments": [
        {"payment_id": "P-OLD", "method": "LOYALTY", "amount": 300.0, "reference": "300 points"}]})
    db["loyalty_transactions"].insert_one({"txn_id": "T-OLD", "type": "REDEEM", "order_id": "O-1",
                                           "tender_payment_id": None})
    out = bl.plan(db)
    assert out["stop"] == [("T-OLD", "O-1", 400.0)]

    class _Client:
        def __init__(self, *a, **k):
            self.admin = type("A", (), {"command": lambda *a, **k: None})()

        def __getitem__(self, name):
            return db

    monkeypatch.setenv("MONGO_URL", "x")
    monkeypatch.setattr("pymongo.MongoClient", _Client)
    monkeypatch.setattr(sys, "argv", ["backfill", "--apply"])
    assert bl.main() == 2
    text = capsys.readouterr().out
    assert "[STOP]" in text and "[APPLIED]" not in text
    assert db["loyalty_transactions"].find_one({"txn_id": "T-OLD"})["tender_payment_id"] is None
