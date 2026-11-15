"""
IMS 2.0 - a press whose bill hold aged out or was taken over writes nothing (#1397, D79).
=========================================================================================
Codex round 7 (PR-1397-round7.md, P1), native Mongo: a counter CREDIT_NOTE
return reserved the bill and passed its checks, then stalled past its
120-second hold. An online no_restock refund of the same unit took the bill
over and credited Rs 900. The counter woke, claimed the line and turned its
OLD allowance into a new kept hold: a second Rs 900 credit and the same tax
reversed twice.

D79: every writer is fenced against the shared reservation at the moment it
writes (returns.fence_bill_hold): its own hold must still be there and live,
and becomes KEPT in the same compare-and-set. A door that takes the bill over
drops the dead hold (take_bill_hold), so the stale holder finds it gone even
if its own clock disagrees. Fenced: the counter's credit and money-out marks,
the online refund post, the admin invoice-change credit note and the advance
refund.

Real code over the fakes of test_returns_endpoint_tender_capture (ctx), the
online refund fakes of test_refund_review_unpaid_credit (env) and the till
fakes of test_pos_money_doors (till). No emoji (cp1252).
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import HTTPException  # noqa: E402

from api.routers import returns as returns_router  # noqa: E402
from tests.test_bill_money_doors_r7 import _admin_note, _credit_rows, _invoiced  # noqa: E402
from tests.test_pos_money_doors import _ADV_BODY, _advance_client, _advance_order, _hdr, _stored, till  # noqa: E402,F401
from tests.test_refund_review_unpaid_credit import _balance, _payload, env  # noqa: E402,F401
from tests.test_refund_review_unpaid_credit import _order as _online_order  # noqa: E402
from tests.test_return_points_paid_once import _post  # noqa: E402
from tests.test_return_points_paid_race import _with_compare_and_set  # noqa: E402
from tests.test_returns_endpoint_tender_capture import _BILLED_GROSS, ctx  # noqa: E402,F401

pytestmark = pytest.mark.usefixtures("gst_numbers")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _aged(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds + 5)).isoformat()


# --- the shared fence ----------------------------------------------------------


class _Orders:
    """One order document with the compare-and-set update_one takes."""

    def __init__(self, **order):
        self.doc = {"order_id": "O1", **order}

    def find_one(self, q):
        return dict(self.doc) if q.get("order_id") == self.doc["order_id"] else None

    def update_one(self, q, u):
        class _R:
            matched_count = 0

        if all(self.doc.get(k) == v for k, v in q.items()):
            self.doc.update(u["$set"])
            _R.matched_count = 1
        return _R()


def test_a_live_hold_is_fenced_and_kept():
    o = _Orders(refund_holds={"RET-1": {"amount": 900.0, "at": _now()}}, refund_holds_v=1)
    assert returns_router.fence_bill_hold(o, "O1", "RET-1") is True
    assert o.doc["refund_holds"]["RET-1"]["kept"] is True and o.doc["refund_holds_v"] == 2
    # Kept: it never ages out, and the fence passes again.
    o.doc["refund_holds"]["RET-1"]["at"] = _aged(3600)
    assert returns_router.fence_bill_hold(o, "O1", "RET-1") is True


def test_an_aged_out_hold_is_refused_and_not_kept():
    o = _Orders(refund_holds={"RET-1": {"amount": 900.0, "at": _aged(returns_router._refund_hold_ttl_seconds())}},
                refund_holds_v=1)
    assert returns_router.fence_bill_hold(o, "O1", "RET-1") is False
    assert not o.doc["refund_holds"]["RET-1"].get("kept") and o.doc["refund_holds_v"] == 1


def test_a_hold_taken_over_is_refused_whatever_its_holders_clock_says():
    """The online door took the bill over (the counter's hold was dead by ITS
    clock) and dropped the hold: the counter's own clock may still call it
    young -- the fence refuses on the missing hold alone."""
    o = _Orders(refund_holds={"RET-1": {"amount": 900.0, "at": _aged(returns_router._refund_hold_ttl_seconds())}},
                refund_holds_v=1)
    assert returns_router.take_bill_hold(o, "O1", "shopify:R1", 900.0, door="online", ttl=900) is True
    assert list(o.doc["refund_holds"]) == ["shopify:R1"]  # the dead counter hold is gone
    assert returns_router.fence_bill_hold(o, "O1", "RET-1") is False


def test_a_door_fenced_mid_write_still_refuses_another_door():
    o = _Orders(refund_holds={"credit-note:x": {"amount": 100.0, "at": _now(), "door": "credit_note",
                                                "ttl": 900, "kept": True}}, refund_holds_v=1)
    assert returns_router.take_bill_hold(o, "O1", "shopify:R1", 900.0, door="online", ttl=900) is False
    assert list(o.doc["refund_holds"]) == ["credit-note:x"]


def test_a_kept_counter_hold_is_still_no_refusal_and_is_never_dropped():
    o = _Orders(refund_holds={"RET-1": {"amount": 900.0, "at": _aged(3600), "kept": True}}, refund_holds_v=1)
    assert returns_router.take_bill_hold(o, "O1", "shopify:R1", 100.0, door="online", ttl=900) is True
    assert sorted(o.doc["refund_holds"]) == ["RET-1", "shopify:R1"]


# --- the counter (Codex round 7 P1) ---------------------------------------------


def _stall_past_the_hold(c, monkeypatch, take_over=False):
    """The press stalls right before its unit claim (after its checks): its
    hold ages out (or another door takes the bill over and drops it)."""
    real, seen = returns_router._claim_returnable_qty, []

    def _claim(order_id, *a, **k):
        if not seen:
            holds = c["order"].get("refund_holds") or {}
            assert len(holds) == 1 and not any(h.get("kept") for h in holds.values()), holds
            seen.append(list(holds))
            if take_over:
                c["order"]["refund_holds"] = {}
                c["order"]["refund_holds_v"] = int(c["order"].get("refund_holds_v") or 0) + 1
            else:
                for h in holds.values():
                    h["at"] = _aged(returns_router._refund_hold_ttl_seconds())
        return real(order_id, *a, **k)

    monkeypatch.setattr(returns_router, "_claim_returnable_qty", _claim)
    return seen


def _nothing_issued(c):
    assert c["returns"].docs == []
    assert [d for d in c["ledger"].docs if d.get("type") == "ISSUED"] == []
    assert c["customers"].customers["CUST-1"]["store_credit"] == 0.0
    # No hold of the press says money went out. (On native Mongo the press
    # gives its unit claim back and drops its hold -- claude_d79_stale_writers.py:
    # returned_qty 0.0, no hold. This fake orders collection has no update_one,
    # so the give-back marks fail and the hold stays as the Needs-completing
    # trace, as for any give-back that cannot be written.)
    assert not [k for k, h in (c["order"].get("refund_holds") or {}).items() if h.get("money_out")]


@pytest.mark.parametrize("take_over", [False, True])
def test_a_stale_counter_credit_note_issues_nothing(ctx, monkeypatch, take_over):
    c = ctx([{"method": "CASH", "amount": _BILLED_GROSS}])
    _with_compare_and_set(c)
    seen = _stall_past_the_hold(c, monkeypatch, take_over=take_over)
    r = _post(c, None, return_type="CREDIT_NOTE")
    assert seen, "the press never reached its claim"
    assert r.status_code == 409 and "NOTHING was issued" in r.text, r.text
    _nothing_issued(c)


def test_a_stale_counter_cash_return_pays_nothing(ctx, monkeypatch):
    """No credit leg: the money-out mark is the fence."""
    c = ctx([{"method": "CASH", "amount": _BILLED_GROSS}])
    _with_compare_and_set(c)
    seen = _stall_past_the_hold(c, monkeypatch)
    r = _post(c, [{"method": "CASH", "amount": _BILLED_GROSS}])
    assert seen and r.status_code == 409, r.text
    _nothing_issued(c)


def test_a_counter_press_that_did_not_stall_is_unchanged(ctx):
    """Control: the same CREDIT_NOTE return, no stall: issued once, hold kept."""
    c = ctx([{"method": "CASH", "amount": _BILLED_GROSS}])
    _with_compare_and_set(c)
    r = _post(c, None, return_type="CREDIT_NOTE")
    assert r.status_code == 201, r.text
    assert len([d for d in c["ledger"].docs if d.get("type") == "ISSUED"]) == 1
    assert all(h.get("kept") for h in c["order"]["refund_holds"].values())


# --- the online refund post -------------------------------------------------------


def test_a_stale_online_post_books_nothing(env, monkeypatch):
    """The online post read its allowance and line note, then stalled until
    its own hold aged out: it books nothing (credit_failed, the row stays
    open for retry) -- a counter return may have priced the bill meanwhile."""
    from api.services import shopify_refund as S

    _online_order(env, payments=[{"method": "CASH", "amount": 1000.0}])
    assert S.handle_shopify_refund(env, _payload(refund_id="710001", txns=[]), topic="refunds/create")["status"] \
        == "queued"
    real = S._store_credit_allowance

    def _stall(db, order_id, *a, **k):
        out = real(db, order_id, *a, **k)
        orders = env.get_collection("orders")
        holds = orders.find_one({"order_id": order_id})["refund_holds"]
        orders.update_one({"order_id": order_id}, {"$set": {"refund_holds.shopify:710001.at": _aged(900)}})
        assert "shopify:710001" in holds
        return out

    monkeypatch.setattr(S, "_store_credit_allowance", _stall)
    row = env.get_collection("shopify_refund_review").find_one({"shopify_refund_id": "710001"})
    res = S.post_from_review(env, row)
    assert res["status"] == "credit_failed" and res.get("reason") == "order_busy", res
    assert env.get_collection("credit_note_ledger").count_documents({}) == 0
    assert env.get_collection("returns").count_documents({"shopify_refund_id": "710001"}) == 0
    assert _balance(env) == (0.0, 0.0)
    assert not (env.get_collection("orders").find_one({"order_id": "ord-1"}).get("refund_holds") or {})
    # Retried later with a live hold: posted once.
    monkeypatch.setattr(S, "_store_credit_allowance", real)
    assert S.post_from_review(env, row)["status"] == "credited"
    assert env.get_collection("returns").count_documents({"shopify_refund_id": "710001"}) == 1


# --- the admin credit note and the advance refund ---------------------------------


@pytest.mark.asyncio
async def test_a_stale_admin_credit_note_writes_nothing(till, monkeypatch):
    _invoiced(till)
    real = returns_router.bill_room_left

    def _stall(db, order):
        out = real(db, order)
        orders = till["db"].get_collection("orders")
        holds = orders.find_one({"order_id": "ORD-1"})["refund_holds"]
        key = [k for k in holds if k.startswith("credit-note:")][0]
        orders.update_one({"order_id": "ORD-1"}, {"$set": {f"refund_holds.{key}.at": _aged(900)}})
        return out

    monkeypatch.setattr(returns_router, "bill_room_left", _stall)
    with pytest.raises(HTTPException) as exc:
        await _admin_note(till, monkeypatch)
    assert exc.value.status_code == 409 and "NOTHING was issued" in exc.value.detail
    assert _credit_rows(till) == []
    assert _stored(till).get("post_invoice_credit_note_at") is None
    assert _stored(till).get("refund_holds") == {}


def test_a_stale_advance_refund_writes_nothing(till, monkeypatch):
    _advance_order(till)
    real = returns_router._advance_refund_plan

    def _stall(order):
        out = real(order)
        orders = till["db"].get_collection("orders")
        holds = orders.find_one({"order_id": "ORD-1"})["refund_holds"]
        key = [k for k in holds if k.startswith("advance:")][0]
        orders.update_one({"order_id": "ORD-1"}, {"$set": {f"refund_holds.{key}.at": _aged(900)}})
        return out

    monkeypatch.setattr(returns_router, "_advance_refund_plan", _stall)
    r = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert r.status_code == 409 and "NOTHING was issued" in r.text, r.text
    assert till["db"].get_collection("returns").count_documents({}) == 0
    assert _stored(till).get("advance_refund_id") is None and _stored(till)["status"] == "CONFIRMED"
    assert _stored(till).get("refund_holds") == {}


# --- round 8 (internal review of D79) ----------------------------------------------


def _fence_read_fails_once(monkeypatch):
    """One transient database error on the fence's OWN read of the bill (a
    network blip, a primary step-down): BaseRepository.find_by_id swallows it
    into None, read_or_raise raises -- as the production repository does."""
    from pymongo.errors import AutoReconnect

    repo = returns_router.get_order_repository()
    real, hit = repo.find_by_id, []

    def _in_fence():
        f = sys._getframe(1)
        for _ in range(4):
            if f is None:
                return False
            if f.f_code.co_name == "fence_bill_hold":
                return True
            f = f.f_back
        return False

    def read_or_raise(oid):
        if not hit and _in_fence():
            hit.append(oid)
            raise AutoReconnect("primary stepped down")
        return real(oid)

    def find_by_id(oid):
        try:
            return read_or_raise(oid)
        except Exception:  # noqa: BLE001 -- BaseRepository.find_by_id
            return None

    monkeypatch.setattr(repo, "read_or_raise", read_or_raise, raising=False)
    monkeypatch.setattr(repo, "find_by_id", find_by_id)
    return hit


@pytest.mark.parametrize("take_over", [False, True])
def test_a_counter_fence_that_cannot_read_the_bill_issues_nothing(ctx, monkeypatch, take_over):
    """Internal review of D79: the counter's fence read the bill through
    find_by_id, which turns a database error into None -- "no such bill",
    passed. With the online door's take-over (Codex round 7 P1) that issued a
    second Rs 900 credit. An unreadable bill is not an absent one: refused,
    nothing issued."""
    c = ctx([{"method": "CASH", "amount": _BILLED_GROSS}])
    _with_compare_and_set(c)
    hit = _fence_read_fails_once(monkeypatch)
    seen = _stall_past_the_hold(c, monkeypatch, take_over=True) if take_over else [1]
    r = _post(c, None, return_type="CREDIT_NOTE")
    assert seen and hit, "the fence never read the bill"
    assert r.status_code == 503 and "nothing was issued" in r.text.lower(), r.text
    _nothing_issued(c)


def test_the_counter_fence_refuses_a_bill_that_is_gone():
    """The counter reserved its hold ON the bill: a bill the fence cannot find
    is no licence to write (the doors' coll path keeps "no such bill: True")."""

    class _Repo:
        def read_or_raise(self, oid):
            return None

        def update_if(self, *a):
            raise AssertionError("nothing to write")

    assert returns_router.fence_bill_hold(None, "O1", "RET-1", repo=_Repo()) is False


def _two_paid_units(env):
    _online_order(env, grand_total=2000.0, amount_paid=2000.0,
                  items=[{"item_id": "it-1", "product_id": "7001", "ims_product_id": "IMS-P-1",
                          "shopify_line_item_id": 9001, "sku": "RB-1", "product_name": "RB",
                          "quantity": 2, "gst_rate": 5.0, "taxable_value": 1904.76, "tax_amount": 95.24}])


def test_a_door_hold_whose_release_failed_is_never_counted_twice(env, monkeypatch):
    """Internal review of D79: online refund A's release failed after its
    claim doc landed, so its KEPT 'shopify:A' hold stayed. Refund B of the
    other unit then counted A twice (its doc and its hold) and got no store
    credit for Rs 1,000 owed. A door's hold is never a record of money given
    back (its own doc / ledger row is): not counted; and the next door that
    takes the bill drops it once its record counts."""
    from api.services import shopify_refund as S

    _two_paid_units(env)
    real_drop = returns_router.drop_bill_hold
    monkeypatch.setattr(returns_router, "drop_bill_hold", lambda *a, **k: None)  # the release is lost
    assert S.handle_shopify_refund(env, _payload(refund_id="710101", txns=[]), topic="refunds/create")["status"] \
        == "queued"
    row = env.get_collection("shopify_refund_review").find_one({"shopify_refund_id": "710101"})
    assert S.post_from_review(env, row)["status"] == "credited"
    order = env.get_collection("orders").find_one({"order_id": "ord-1"})
    assert order["refund_holds"]["shopify:710101"]["kept"] is True  # left behind
    # The shared reader alone: A counts once (its doc), never again through its hold.
    assert S._given_back_elsewhere(env, order, "710102")["given"] == 1000.0

    monkeypatch.setattr(returns_router, "drop_bill_hold", real_drop)
    assert S.handle_shopify_refund(env, _payload(refund_id="710102", txns=[]), topic="refunds/create")["status"] \
        == "queued"
    row = env.get_collection("shopify_refund_review").find_one({"shopify_refund_id": "710102"})
    assert S.post_from_review(env, row)["status"] == "credited"
    assert sorted(r["delta"] for r in env.get_collection("credit_note_ledger").find({})) == [1000.0, 1000.0]
    assert _balance(env) == (2000.0, 2000.0)
    assert not (env.get_collection("orders").find_one({"order_id": "ord-1"}).get("refund_holds") or {})


def test_a_door_hold_stuck_after_its_fence_raises_one_p1_task(till, monkeypatch):
    """A door (here an advance refund) died after its fence: its KEPT hold
    refuses every refund of the bill and no record of it counts. Never
    released by itself (the press may still be writing) -- but a person is
    told, once. A young door hold, or one whose record counts, is no task."""
    from tests.test_pos_money_doors_r9 import _ago

    out = []
    monkeypatch.setattr("api.services.task_triggers.create_system_task", lambda repo, **k: out.append(k) or k)
    monkeypatch.setattr("api.dependencies.get_task_repository", lambda: object(), raising=False)
    orders = till["db"].get_collection("orders")
    orders.insert_one({"order_id": "ORD-S", "order_number": "BV-S", "store_id": "BV-TEST-01",
                       "refund_holds_v": 1, "refund_holds": {
                           "advance:RET-X": {"amount": 500.0, "at": _ago(30), "kept": True, "door": "advance",
                                             "ttl": 900},
                           "credit-note:young": {"amount": 100.0, "at": _ago(2), "kept": True,
                                                 "door": "credit_note", "ttl": 900}}})
    assert returns_router.sweep_stuck_return_holds() == 1
    assert returns_router.sweep_stuck_return_holds() == 1  # the task itself is deduped
    assert [t["dedupe_ref"] for t in out] == ["door_hold_stuck:ORD-S:advance:RET-X"] * 2
    assert out[0]["priority"] == "P1"
    assert orders.find_one({"order_id": "ORD-S"})["refund_holds"]["advance:RET-X"]["kept"] is True  # never released
