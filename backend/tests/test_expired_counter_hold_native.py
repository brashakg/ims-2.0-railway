"""
IMS 2.0 - Codex round 7 P1 (#1397, D79) on NATIVE Mongo: a counter press that
stalls past its bill hold never issues a second credit beside an online refund.
==============================================================================
Codex, PR-1397-round7.md (independent_expired_counter_hold.py, real routes,
native Mongo): an online bill of Rs 1,000 (Rs 900 cash + Rs 100 points), one
unit SOLD, an online refund queued as no_restock. A counter CREDIT_NOTE press
passed its checks and stalled right before its unit claim; its 120-second hold
aged out; Accounts confirmed the online refund (Rs 900 credit, the Rs 100 of
points restored); the counter woke, claimed the line and issued a SECOND
Rs 900 credit: Rs 1,800 of store credit for Rs 900 paid, the same Rs 47.62 of
tax reversed twice. Codex asked for this ordering, with the no_restock refund,
as a native regression that reconciles ledger, tax, points and unit rows
through identical retries and recovery.

The two orders the stall can fall in, real routes over a REAL MongoDB (pymongo,
no database doubles), reconciled from the stored rows:

* at Codex's own pause point (right before _claim_returnable_qty): the press
  has by now fenced its hold (KEPT, "writing"), so ageing it changes nothing
  and the online Confirm is refused until the counter's money is out. ONE
  credit: the counter's.
* one step earlier (its hold aged out BEFORE the fence): the online refund
  takes the bill over and credits; the counter's fence finds its hold dead:
  409, nothing issued. ONE credit: the online refund's.

Where it runs: as test_d95_one_record_native (CI's mongo service; on a PC
MONGO_HOST / MONGO_PORT / MONGO_DATABASE of a loopback Mongo). No emoji (cp1252).
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import HTTPException  # noqa: E402

from api.routers import loyalty as L  # noqa: E402
from api.routers import online_store_refund_reviews as RR  # noqa: E402
from api.services import shopify_refund as S  # noqa: E402
from database.repositories.product_repository import StockRepository  # noqa: E402
from database.repositories.task_repository import TaskRepository  # noqa: E402
from tests.test_d95_one_record_native import _in_thread, _open_native, _slot_indexes  # noqa: E402
from tests.test_pos_money_doors import STORE, _advance_client, _hdr, _user, _wire, returns_router  # noqa: E402
from tests.test_refund_review_unpaid_credit import _order as _online_order  # noqa: E402
from tests.test_refund_review_unpaid_credit import _payload as _refund_payload  # noqa: E402

pytestmark = pytest.mark.usefixtures("gst_numbers")

REFUND_ID = "710001"
KEY = "K-STALE-COUNTER"
BODY = {"order_id": "ord-1", "store_id": STORE, "customer_id": "CUST-1", "return_type": "CREDIT_NOTE",
        "items": [{"order_item_id": "it-1", "product_id": "IMS-P-1", "sku": "RB-1", "return_qty": 1,
                   "unit_price": 952.38, "condition": "GOOD", "restock": True, "reason": "DEFECTIVE"}]}


@pytest.fixture()
def online(monkeypatch):
    """Codex's fixture on a fresh native database: the Rs 1,000 online bill
    (Rs 900 CASH + Rs 100 LOYALTY), its unit SOLD, points 50 / redeemed 100 /
    earned 50, and the online refund queued as no_restock. No provider is
    contacted (the refund payload is handed to the service)."""
    monkeypatch.delenv("SHOPIFY_REFUND_AUTO", raising=False)
    client, name = _open_native()
    db = client[name]
    assert db.name == name and name not in client.list_database_names(), name  # fresh, this test's own
    env = _wire(monkeypatch, db)
    monkeypatch.setattr(returns_router, "get_stock_repository", lambda: StockRepository(db["stock_units"]))
    _slot_indexes(db)
    monkeypatch.setattr(returns_router, "get_product_repository", lambda: None)
    monkeypatch.setattr("api.dependencies.get_task_repository", lambda: TaskRepository(db["tasks"]), raising=False)
    db["customers"].insert_one({"customer_id": "CUST-1", "name": "Ravi", "store_credit": 0.0})
    _online_order(db, store_id=STORE, fulfillment_stores=[STORE], balance_due=0.0, status="DELIVERED",
                  payments=[{"payment_id": "p1", "method": "CASH", "amount": 900.0, "status": "SETTLED"},
                            {"payment_id": "p2", "method": "LOYALTY", "amount": 100.0, "status": "SETTLED"}])
    db["stock_units"].insert_one({"_id": "U1", "stock_id": "U1", "product_id": "IMS-P-1", "store_id": STORE,
                                  "status": "SOLD", "order_id": "ord-1", "quantity": 1, "barcode": "BC1"})
    env["accounts"].find_or_create("CUST-1")
    env["accounts"].adjust_balance("CUST-1", delta_points=50, delta_lifetime_earned=50, delta_lifetime_redeemed=100)
    db["loyalty_transactions"].insert_many([
        {"txn_id": "T-REDEEM", "customer_id": "CUST-1", "type": "REDEEM", "points": 100, "rupee_value": 100.0,
         "order_id": "ord-1", "created_at": datetime.now()},
        {"txn_id": "T-EARN", "customer_id": "CUST-1", "type": "EARN", "points": 50, "order_id": "ord-1",
         "created_at": datetime.now()}])
    payload = _refund_payload(refund_id=REFUND_ID, txns=[], restock_type="no_restock")
    assert S.handle_shopify_refund(db, payload, topic="refunds/create")["status"] == "queued"
    env.update(payload=payload,
               review_id=db["shopify_refund_review"].find_one({"shopify_refund_id": REFUND_ID})["review_id"])
    yield env
    client.drop_database(name)


def _press():
    return _advance_client().post("/api/v1/returns", json=BODY,
                                  headers={**_hdr("SALES_STAFF"), "Idempotency-Key": KEY})


def _confirm(env):
    """Accounts press Confirm on the online refund: (status code, answer)."""
    try:
        return 200, asyncio.run(RR.confirm_refund_review(env["review_id"], current_user=_user("ACCOUNTANT")))
    except HTTPException as exc:
        return exc.status_code, exc.detail


def _books(db):
    """The stored rows Codex reconciled."""
    acct = db["loyalty_accounts"].find_one({"customer_id": "CUST-1"})
    ledger = list(db["credit_note_ledger"].find({"customer_id": "CUST-1"}))
    order = db["orders"].find_one({"order_id": "ord-1"})
    return {
        "payments": sorted((p["method"], p["amount"]) for p in order["payments"]),
        "credit_rows": sorted((r.get("type"), r.get("delta")) for r in ledger),
        "credit_tax": sorted(round(float(r.get("tax") or 0), 2) for r in ledger),
        "store_credit": db["customers"].find_one({"customer_id": "CUST-1"}).get("store_credit"),
        "returns": sorted(("online" if r.get("shopify_refund_id") else "counter", r.get("status"),
                           r.get("net_refund"), r.get("points_paid_share"), (r.get("gst_breakup") or {}).get("tax"))
                          for r in db["returns"].find({"order_id": "ord-1"})),
        "points": (int(acct["balance_points"]), int(acct.get("lifetime_redeemed") or 0),
                   int(acct.get("lifetime_earned") or 0)),
        "units": sorted((u["_id"], u["status"], u.get("order_id")) for u in db["stock_units"].find({})),
        "returned_qty": order["items"][0].get("returned_qty") or 0,
        "holds": sorted((order.get("refund_holds") or {})),
    }


def _retries_and_recovery(env):
    """Codex's step 6: the identical counter press, Confirm again, the refund
    delivered again, two loyalty sweeps, two unfinished-refund passes."""
    db = env["db"]
    out = {"counter": _press().status_code, "confirm": _confirm(env)[0],
           "webhook": S.handle_shopify_refund(db, env["payload"], topic="refunds/create")["status"]}
    for _ in range(2):
        L.reconcile_loyalty_reversals()
        RR.finish_unfinished_refunds()
    return out


def _age_counter_hold(db):
    """Age ONLY the counter press's own hold past its time limit."""
    holds = db["orders"].find_one({"order_id": "ord-1"}).get("refund_holds") or {}
    mine = [k for k in holds if k.startswith("RET-")]
    assert len(mine) == 1 and len(holds) == 1, holds
    old = (datetime.now(timezone.utc) - timedelta(seconds=returns_router._refund_hold_ttl_seconds() + 5)).isoformat()
    db["orders"].update_one({"order_id": "ord-1"}, {"$set": {f"refund_holds.{mine[0]}.at": old}})
    return holds[mine[0]]


def test_codex_pause_point_one_credit_whoever_stalls(online, monkeypatch):
    """Codex's exact schedule: the counter stalls right before its real unit
    claim, its hold's clock is moved past the limit, Accounts confirm the
    online no_restock refund, the counter resumes its real claim and posting.
    Before D79: 201 and a SECOND Rs 900 credit (Rs 1,800 for Rs 900 paid, tax
    reversed twice). Now ONE credit, through every retry and recovery pass."""
    db, seen = online["db"], {}
    real_claim = returns_router._claim_returnable_qty

    def stalls_before_its_claim(order_id, *a, **k):
        if "confirm" not in seen:
            seen["hold"] = _age_counter_hold(db)
            seen["confirm"] = _in_thread(lambda: _confirm(online)).get("value")
            seen["books"] = _books(db)
        return real_claim(order_id, *a, **k)

    monkeypatch.setattr(returns_router, "_claim_returnable_qty", stalls_before_its_claim)
    r = _press()
    assert seen.get("confirm"), "the press never reached its unit claim"
    first = _books(db)
    # THE invariant Codex's probe failed on: one credit for the Rs 900 paid in cash.
    assert first["credit_rows"] == [("ISSUED", 900.0)] and first["store_credit"] == 900.0, (r.status_code, first)
    assert first["credit_tax"] == [47.62], first
    # How: the press had fenced its hold before this point, so no door prices
    # the bill under it -- the online Confirm waits (nothing booked, its row
    # stays open) and the counter's own credit is the one.
    assert seen["hold"].get("kept") is True and seen["hold"].get("writing") is True, seen["hold"]
    assert seen["confirm"][0] == 422 and "stays open for retry" in seen["confirm"][1], seen["confirm"]
    assert seen["books"]["credit_rows"] == [] and seen["books"]["store_credit"] == 0.0
    assert r.status_code == 201, r.text
    again = _retries_and_recovery(online)
    # The replay of the counter press; the online row now says the goods were returned at the counter.
    assert again == {"counter": 201, "confirm": 409, "webhook": "duplicate"}, again
    final = _books(db)
    assert final == {**first, "holds": final["holds"]}, (first, final)
    assert final["payments"] == [("CASH", 900.0), ("LOYALTY", 100.0)]
    assert final["credit_rows"] == [("ISSUED", 900.0)] and final["store_credit"] == 900.0  # never Rs 1,800
    assert final["credit_tax"] == [47.62]  # reversed once, not Rs 95.24
    assert final["returns"] == [("counter", "COMPLETED", 1000.0, 100.0, 47.62)]
    assert final["points"] == (100, 0, 0)  # the Rs 100 redeemed restored once, the 50 earned clawed once
    assert final["units"] == [("U1", "AVAILABLE", None)] and final["returned_qty"] == 1
    assert db["stock_units"].count_documents({}) == 1  # no unit minted
    review = db["shopify_refund_review"].find_one({"shopify_refund_id": REFUND_ID})
    assert review["resolved"] is not True  # the online row stays for Accounts (nothing was booked for it)


def test_a_hold_aged_out_before_the_fence_issues_nothing_at_the_counter(online, monkeypatch):
    """The stall one step earlier: the hold aged out BEFORE the press's first
    write. The online refund takes the bill over and credits Rs 900; the
    counter's fence finds its hold dead: 409, nothing claimed, issued or put
    back -- through every retry and recovery pass."""
    db, seen = online["db"], {}
    real_keep = returns_router._keep_order_refund_hold

    def stalls_before_its_fence(order_id, *a, fence=False, **k):
        if fence and "confirm" not in seen:
            seen["hold"] = _age_counter_hold(db)
            seen["confirm"] = _in_thread(lambda: _confirm(online)).get("value")
            seen["books"] = _books(db)
        return real_keep(order_id, *a, fence=fence, **k)

    monkeypatch.setattr(returns_router, "_keep_order_refund_hold", stalls_before_its_fence)
    r = _press()
    assert seen.get("confirm"), "the press never reached its fence"
    assert not seen["hold"].get("kept"), seen["hold"]
    assert seen["confirm"][0] == 200 and seen["confirm"][1]["status"] == "POSTED", seen["confirm"]
    assert r.status_code == 409 and "NOTHING was issued" in r.text, r.text
    first = _books(db)
    assert first == seen["books"], (seen["books"], first)  # the counter changed nothing
    again = _retries_and_recovery(online)
    assert again == {"counter": 400, "confirm": 409, "webhook": "duplicate"}, again
    final = _books(db)
    assert final == first, (first, final)
    assert final["payments"] == [("CASH", 900.0), ("LOYALTY", 100.0)]
    assert final["credit_rows"] == [("ISSUED", 900.0)] and final["store_credit"] == 900.0  # never Rs 1,800
    assert final["credit_tax"] == [47.62]
    assert final["returns"] == [("online", "COMPLETED", 1000.0, 100.0, 47.62)]
    assert final["points"] == (150, 0, 50)  # the Rs 100 redeemed restored once (D27: earned points stay)
    # no_restock: the unit stays out, still this bill's; the counter claimed nothing and holds nothing.
    assert final["units"] == [("U1", "SOLD", "ord-1")] and final["returned_qty"] == 0 and final["holds"] == []


def test_a_credit_move_that_stalls_past_the_reconciler_never_lands_beside_an_online_refund(online, monkeypatch):
    """D79, the same double credit through another stale writer (Claude's
    reviewers on head d7d0a963): the counter press stalls INSIDE its balance
    move, after every fence. Its ISSUING line passes the reconciler's limit;
    the reconciler voids it and gives the unit claim and the bill's hold
    back; Accounts confirm the online no_restock refund (Rs 900); the press's
    move resumes. Before: the move landed, the VOID line was healed to ISSUED
    and the hold re-kept -- Rs 1,800 of store credit for Rs 900 paid, 201.
    Now the reconciler fences the move on the customer before it gives
    anything back: the late move can never land."""
    from api.routers import customers as C
    from database.repositories.audit_repository import AuditRepository
    from database.repositories.customer_repository import CustomerRepository

    db, seen = online["db"], {}
    # The real audit trail: the reconciler's write-off of the credit-note
    # number is recorded there, and only then is the bill's hold given back.
    monkeypatch.setattr("api.dependencies.get_audit_repository", lambda: AuditRepository(db["audit_logs"]),
                        raising=False)
    real_move = C.apply_store_credit_delta

    def stalls_inside_its_move(repo, customer_id, delta, **k):
        ref = k.get("guard_ref") or ""
        if ref.endswith(":credit") and "confirm" not in seen:
            old = (datetime.now(timezone.utc) - timedelta(seconds=C.PENDING_TIMEOUT_SECONDS + 5)).isoformat()
            assert db["credit_note_ledger"].update_one(
                {"entry_id": ref, "type": "ISSUING"}, {"$set": {"pending_at": old}}).modified_count == 1
            seen["reconciled"] = C.reconcile_pending_credits(CustomerRepository(db["customers"]))
            seen["line"] = db["credit_note_ledger"].find_one({"entry_id": ref})
            seen["holds"] = sorted(db["orders"].find_one({"order_id": "ord-1"}).get("refund_holds") or {})
            seen["confirm"] = _in_thread(lambda: _confirm(online)).get("value")
        return real_move(repo, customer_id, delta, **k)

    monkeypatch.setattr(C, "apply_store_credit_delta", stalls_inside_its_move)
    r = _press()
    assert seen.get("confirm"), "the press never reached its balance move"
    # The reconciler took the stalled line over and gave the bill back ...
    assert seen["reconciled"]["voided"] == 1 and seen["line"]["type"] == "VOID" and seen["holds"] == [], seen
    # ... so the online refund was booked ...
    assert seen["confirm"][0] == 200 and seen["confirm"][1]["status"] == "POSTED", seen["confirm"]
    first = _books(db)
    live = [row for row in first["credit_rows"] if row[0] != "VOID"]
    # ... and the press's late move never landed beside it.
    assert live == [("ISSUED", 900.0)] and first["store_credit"] == 900.0, (r.status_code, r.text, first)
    assert r.status_code != 201, r.text
    assert first["returns"] == [("online", "COMPLETED", 1000.0, 100.0, 47.62)]
    assert first["holds"] == [] and first["returned_qty"] == 0 and first["units"] == [("U1", "SOLD", "ord-1")]
    cust = db["customers"].find_one({"customer_id": "CUST-1"})
    assert seen["line"]["entry_id"] in cust[C.STORE_CREDIT_FENCED] and seen["line"]["entry_id"] not in cust[C.STORE_CREDIT_REFS]
    again = _retries_and_recovery(online)
    assert again == {"counter": 400, "confirm": 409, "webhook": "duplicate"}, again
    C.reconcile_pending_credits(CustomerRepository(db["customers"]))
    final = _books(db)
    assert final == first, (first, final)
    assert final["points"] == (150, 0, 50) and db["stock_units"].count_documents({}) == 1
