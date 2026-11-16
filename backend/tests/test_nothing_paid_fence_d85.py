"""
IMS 2.0 - the counter press and "Nothing was paid" can never both land (#1397, D85).
====================================================================================
D85(1), from Claude's review of head 58ceb90f: the counter's credit write
(_issue_store_credit -> post_ledger_credit '<return id>:credit') ran after its
last fence as a separate step, while Returns > Needs completing "Nothing was
paid" took the hold over by its age alone. A press stalled between its fence
and its ledger write, taken over meanwhile, still posted its credit: native
Mongo (claude_d85_takeover.py, BEFORE) booked a NOTHING_PAID record AND a
Rs 900 credit, gave the unit claim back, wrote the used credit-note number
off, and the identical retry then issued a second Rs 900 (Rs 1,800). A cash
return's money-out fence landing between the takeover's record and its hold
release paid Rs 900 cash beside a NOTHING_PAID record.

Now the takeover first takes the press's own keys (returns._take_from_the_press):
a FENCED row at the credit line's id (the posting door refuses it) and a
compare-and-set mark on the hold that every later fence of the press refuses.
Whichever lands first wins, in every order; a lost acknowledgement is read back.

D85(2): a door's kept hold ('credit-note:<id>', 'advance:<id>') whose release
failed counts as finished once the door's own record is complete -- no bill
waits on a developer for it.

Real code over the strict fakes of test_pos_money_doors (till). No emoji (cp1252).
"""

from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("MONGODB_URI", "")

from fastapi import HTTPException  # noqa: E402

from api.routers import customers as cust_router  # noqa: E402
from api.services import store_credit_ledger as scl  # noqa: E402
from tests.test_bill_money_doors_r7 import _admin_note, _invoiced  # noqa: E402
from tests.test_pos_money_doors import _advance_client, _hdr, _ledger, _order, _stored, returns_router, till  # noqa: E402,F401
from tests.test_pos_money_doors_r8 import _two_unit_bill  # noqa: E402
from tests.test_pos_money_doors_r9 import _balance, _customer  # noqa: E402
from tests.test_pos_money_doors_r12 import tasks  # noqa: E402,F401
from tests.test_pos_money_doors_r13 import _bill_with_hold, _complete  # noqa: E402
from tests.test_pos_money_doors_r18 import REAL_CLAIM, REAL_RELEASE, TokenLines  # noqa: E402

pytestmark = pytest.mark.usefixtures("gst_numbers")


def _aged_past_the_timeout(till):
    """Age ONLY the press's own hold past the Needs-completing timeout (it is
    kept "writing" from its first fence, so Nothing was paid may act on it)."""
    old = (datetime.now(timezone.utc) - timedelta(seconds=cust_router.PENDING_TIMEOUT_SECONDS + 5)).isoformat()
    orders = till["db"].get_collection("orders")
    holds = orders.find_one({"order_id": "ORD-1"})["refund_holds"]
    rid = [k for k in holds if k.startswith("RET-")][0]
    orders.update_one({"order_id": "ORD-1"}, {"$set": {f"refund_holds.{rid}.at": old}})
    return rid


def _takeover_now(till, out):
    """Nothing was paid, pressed by a person while the press is stalled (its
    own thread: the endpoint runs its own event loop)."""
    rid = _aged_past_the_timeout(till)
    out["rid"] = rid

    def run():
        try:
            out["takeover"] = _complete(rid, nothing_paid=True)
        except HTTPException as exc:
            out["takeover"] = exc.status_code
    t = threading.Thread(target=run)
    t.start()
    t.join(60)


def _counted(monkeypatch):
    released, written_off = [], []
    monkeypatch.setattr(returns_router, "_release_returnable_qty", lambda *a, **k: released.append(a) or True)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note",
                        lambda n, *a, **k: written_off.append(n) or True)
    return released, written_off


def _credit_note_return(till, monkeypatch):
    payload = _two_unit_bill(till, monkeypatch)
    payload["return_type"] = "CREDIT_NOTE"
    _customer(till)
    return payload


def _docs(till):
    return sorted(d.get("status") for d in till["db"].get_collection("returns").docs)


def _issued(till):
    return [r for r in _ledger(till).docs if r.get("type") in (scl.ISSUED, "ISSUING")]


# --- D85(1): the four orderings ---------------------------------------------------


def test_nothing_paid_during_the_press_wins_and_the_press_issues_nothing(till, monkeypatch, tasks):
    """The reviewer's must-fix: the press passed its credit fence and stalled
    before its ledger write; Nothing was paid answered meanwhile. Before D85
    the press then posted its credit beside the NOTHING_PAID record."""
    payload = _credit_note_return(till, monkeypatch)
    released, written_off = _counted(monkeypatch)
    real, out = returns_router._issue_store_credit, {}

    def stalled(*a, **k):
        if "takeover" not in out:
            _takeover_now(till, out)
        return real(*a, **k)

    monkeypatch.setattr(returns_router, "_issue_store_credit", stalled)
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert isinstance(out["takeover"], dict) and out["takeover"]["status"] == "NOTHING_PAID", out
    assert r.status_code == 409 and "NOTHING was issued" in r.text, r.text
    assert _issued(till) == [] and _balance(till) == 0.0
    assert _docs(till) == ["NOTHING_PAID"]
    assert len(released) == 1 and written_off == ["TST01/C2627/0001"]  # each give-back once
    assert _ledger(till).find_one({"entry_id": f"{out['rid']}:credit"})["type"] == scl.FENCED
    assert not (_stored(till).get("refund_holds") or {})
    # Pressed again: nothing changes.
    again = _complete(out["rid"], nothing_paid=True)
    assert again.get("replayed") is True and len(released) == 1 and written_off == ["TST01/C2627/0001"]


def test_a_press_whose_credit_landed_first_keeps_it_and_nothing_paid_is_refused(till, monkeypatch, tasks):
    payload = _credit_note_return(till, monkeypatch)
    real, out = returns_router._issue_store_credit, {}

    def landed_then_stalled(*a, **k):
        res = real(*a, **k)
        if "takeover" not in out:
            _takeover_now(till, out)
        return res

    monkeypatch.setattr(returns_router, "_issue_store_credit", landed_then_stalled)
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert out["takeover"] == 409, out
    assert r.status_code == 201, r.text
    assert [x["type"] for x in _issued(till)] == [scl.ISSUED] and _docs(till) == ["COMPLETED"]
    hold = _stored(till)["refund_holds"][out["rid"]]
    assert not hold.get(returns_router.NOTHING_PAID_MARK)


def test_nothing_paid_whose_fence_acknowledgement_is_lost_still_wins(till, monkeypatch, tasks):
    from pymongo.errors import AutoReconnect

    payload = _credit_note_return(till, monkeypatch)
    _counted(monkeypatch)
    ledger = _ledger(till)
    real_insert, lost = ledger.insert_one, []

    def insert(doc):
        if doc.get("type") == scl.FENCED and not lost:
            real_insert(doc)
            lost.append(doc["_id"])
            raise AutoReconnect("landed, acknowledgement lost")
        return real_insert(doc)

    monkeypatch.setattr(ledger, "insert_one", insert)
    real, out = returns_router._issue_store_credit, {}

    def stalled(*a, **k):
        if "takeover" not in out:
            _takeover_now(till, out)
        return real(*a, **k)

    monkeypatch.setattr(returns_router, "_issue_store_credit", stalled)
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert lost and isinstance(out["takeover"], dict) and out["takeover"]["status"] == "NOTHING_PAID", out
    assert r.status_code == 409 and _issued(till) == [] and _balance(till) == 0.0
    assert _docs(till) == ["NOTHING_PAID"]


def test_a_cash_press_fenced_between_the_takeover_record_and_its_release_pays_nothing(till, monkeypatch, tasks):
    """No credit leg: the press's money-out fence is the only gate. It ran
    after Nothing was paid wrote its record but before it released the hold:
    before D85 the hold was still there and unmarked -- the fence passed and
    Rs cash went out beside the NOTHING_PAID record."""
    payload = _two_unit_bill(till, monkeypatch)
    payload.update(refund_method="CASH", refund_tenders=[{"method": "CASH", "amount": 1800.0}])
    released, written_off = _counted(monkeypatch)
    real_keep, real_release = returns_router._keep_order_refund_hold, returns_router._release_order_refund
    state = {"go": threading.Event(), "at_release": threading.Event()}

    def release(order_id, rid, *a, **k):
        if threading.current_thread() is state.get("thread"):
            state["at_release"].set()
            assert state["go"].wait(60)
        return real_release(order_id, rid, *a, **k)

    def keep(order_id, rid, *a, fence=False, extra=None, **k):
        if fence and "money_out" in (extra or {}) and "thread" not in state:
            rid0 = _aged_past_the_timeout(till)

            def run():
                try:
                    state["takeover"] = _complete(rid0, nothing_paid=True)
                except HTTPException as exc:
                    state["takeover"] = exc.status_code
            state["thread"] = threading.Thread(target=run)
            state["thread"].start()
            assert state["at_release"].wait(60), "the takeover never reached its release"
        return real_keep(order_id, rid, *a, fence=fence, extra=extra, **k)

    monkeypatch.setattr(returns_router, "_keep_order_refund_hold", keep)
    monkeypatch.setattr(returns_router, "_release_order_refund", release)
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    state["go"].set()
    state["thread"].join(60)
    assert r.status_code == 409 and "NOTHING was issued" in r.text, r.text
    assert isinstance(state["takeover"], dict) and state["takeover"]["status"] == "NOTHING_PAID", state
    assert _docs(till) == ["NOTHING_PAID"]
    assert len(released) == 1 and written_off == ["TST01/C2627/0001"]
    assert not (_stored(till).get("refund_holds") or {})


def test_nothing_paid_before_the_press_numbers_its_note_leaves_the_press_nothing(till, monkeypatch, tasks):
    payload = _credit_note_return(till, monkeypatch)
    released, written_off = _counted(monkeypatch)
    out = {}

    def number(*a, **k):
        if "takeover" not in out:
            _takeover_now(till, out)
        return "TST01/C2627/0002"

    monkeypatch.setattr(returns_router, "next_document_number", number)
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert isinstance(out["takeover"], dict) and out["takeover"]["status"] == "NOTHING_PAID", out
    assert r.status_code == 409 and _issued(till) == [] and _docs(till) == ["NOTHING_PAID"]
    # The press's own number is written off once (by its own refusal).
    assert len(released) == 1 and written_off == ["TST01/C2627/0002"]


def _all_points(monkeypatch):
    """The whole return was paid in points (pays 0): its credit line moves no
    balance -- the credit note reverses the tax, the points go back."""
    monkeypatch.setattr(returns_router, "_points_paid_share", lambda order, oid, net, **k: round(float(net), 2))


def test_a_points_paid_credit_note_taken_over_during_the_press_issues_nothing(till, monkeypatch, tasks):
    """Review of b1341e22: a line that moves no balance was a plain insert (a
    fresh ObjectId), so the FENCED row on '<rid>:credit' never met it -- the
    press filed a credit note (tax reversed) beside the NOTHING_PAID record."""
    payload = _credit_note_return(till, monkeypatch)
    _all_points(monkeypatch)
    released, written_off = _counted(monkeypatch)
    real, out = returns_router._issue_store_credit, {}

    def stalled(*a, **k):
        if "takeover" not in out:
            _takeover_now(till, out)
        return real(*a, **k)

    monkeypatch.setattr(returns_router, "_issue_store_credit", stalled)
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert isinstance(out["takeover"], dict) and out["takeover"]["status"] == "NOTHING_PAID", out
    assert r.status_code == 409 and "NOTHING was issued" in r.text, r.text
    assert [x.get("type") for x in _ledger(till).docs] == [scl.FENCED], _ledger(till).docs
    assert _docs(till) == ["NOTHING_PAID"] and _balance(till) == 0.0
    assert len(released) == 1 and written_off == ["TST01/C2627/0001"]


def test_a_points_paid_credit_note_whose_acknowledgement_was_lost_keeps_its_claims(till, monkeypatch, tasks):
    """The line landed, its acknowledgement was lost: before, the press read
    'not written', gave its claims back and wrote its number off beside an
    ISSUED credit note. Now it is unconfirmed; Nothing was paid is refused."""
    from pymongo.errors import AutoReconnect

    payload = _credit_note_return(till, monkeypatch)
    _all_points(monkeypatch)
    released, written_off = _counted(monkeypatch)
    ledger = _ledger(till)
    real_insert, lost = ledger.insert_one, []

    def insert(doc):
        real_insert(doc)
        if doc.get("type") == scl.ISSUED and not lost:
            lost.append(doc["_id"])
            raise AutoReconnect("landed, acknowledgement lost")

    monkeypatch.setattr(ledger, "insert_one", insert)
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert lost and r.status_code == 503, r.text
    assert released == [] and written_off == []
    assert [x.get("type") for x in ledger.docs] == [scl.ISSUED]
    rid = _aged_past_the_timeout(till)
    with pytest.raises(HTTPException) as exc:
        _complete(rid, nothing_paid=True)
    assert exc.value.status_code == 409
    assert [x.get("type") for x in ledger.docs] == [scl.ISSUED] and _docs(till) == []


def test_a_zero_balance_line_is_written_on_its_own_id(till, monkeypatch):
    """Both orders on the one key: the line first -> the fence's insert meets it;
    the fence first -> the line is refused (nothing issued, nothing written)."""
    _customer(till)
    ledger = _ledger(till)
    kw = dict(reason="r", ref="RET-Z", current_user={"user_id": "u1"}, store_id="TST01", order_id="ORD-1",
              note_number="TST01/C2627/0009", balance_delta=0.0, entry_id="RET-Z:credit", tax=47.62)
    entry = returns_router._issue_store_credit("CUST-1", 1000.0, **kw)
    assert entry and entry["delta"] == 0.0
    assert ledger.find_one({"_id": "RET-Z:credit"})["type"] == scl.ISSUED
    again = returns_router._issue_store_credit("CUST-1", 1000.0, **kw)  # a lost acknowledgement replays it
    assert again["note_number"] == "TST01/C2627/0009" and len(ledger.docs) == 1

    ledger.insert_one({"_id": "RET-F:credit", "entry_id": "RET-F:credit", "type": scl.FENCED})
    outcome = {}
    kw.update(ref="RET-F", entry_id="RET-F:credit")
    assert returns_router._issue_store_credit("CUST-1", 1000.0, outcome=outcome, **kw) is None
    assert outcome.get("fenced") is True and outcome.get("not_written") is True
    assert [x["type"] for x in ledger.docs if x["entry_id"] == "RET-F:credit"] == [scl.FENCED]

    from pymongo.errors import AutoReconnect
    real_insert = ledger.insert_one

    def lands_ack_lost(doc):
        real_insert(doc)
        raise AutoReconnect("landed, acknowledgement lost")

    monkeypatch.setattr(ledger, "insert_one", lands_ack_lost)
    outcome = {}
    kw.update(ref="RET-L", entry_id="RET-L:credit")
    with pytest.raises(returns_router.CreditNotConfirmed):  # landed: never "not issued"
        returns_router._issue_store_credit("CUST-1", 1000.0, outcome=outcome, **kw)
    assert outcome.get("not_written") is False
    assert [x["type"] for x in ledger.docs if x["entry_id"] == "RET-L:credit"] == [scl.ISSUED]


# --- D85(1): the takeover's own writes ----------------------------------------------


def test_the_posting_door_refuses_a_fenced_line():
    class Ledger:
        def __init__(self):
            self.row = {"_id": "RET-F:credit", "entry_id": "RET-F:credit", "type": scl.FENCED, "applied": False}

        def find_one(self, q, *a, **k):
            return dict(self.row) if q.get("entry_id") == "RET-F:credit" else None

        def find(self, *a, **k):
            return []

    entry = {"entry_id": "RET-F:credit", "customer_id": "CUST-1", "type": scl.ISSUED, "delta": 900.0}
    assert cust_router._post_ledger_credit(None, entry, ledger=Ledger()) == cust_router.REFUSED
    assert entry.get(cust_router.NOT_WRITTEN) is True


def test_a_takeover_that_cannot_confirm_its_fence_changes_nothing(till, monkeypatch, tasks):
    _bill_with_hold(till, "RET-U", at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    ledger = _ledger(till)

    def insert(doc):
        raise RuntimeError("no answer")

    monkeypatch.setattr(ledger, "insert_one", insert)
    with pytest.raises(HTTPException) as exc:
        _complete("RET-U", nothing_paid=True)
    assert exc.value.status_code == 503
    assert not _stored(till)["refund_holds"]["RET-U"].get(returns_router.NOTHING_PAID_MARK)
    assert _docs(till) == []


def test_a_takeover_that_meets_a_settling_credit_line_changes_nothing(till, monkeypatch, tasks):
    """The press's line landed between Needs completing's read and the fence:
    the insert meets it -- 409, the hold unmarked, no record."""
    _bill_with_hold(till, "RET-S", at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    real_unfinished = returns_router._unfinished_return

    def read_then_line_lands(rid):
        state = real_unfinished(rid)
        _ledger(till).insert_one({"_id": f"{rid}:credit", "entry_id": f"{rid}:credit", "type": "ISSUING",
                                  "customer_id": "CUST-1", "delta": 900.0, "applied": False})
        return state

    monkeypatch.setattr(returns_router, "_unfinished_return", read_then_line_lands)
    with pytest.raises(HTTPException) as exc:
        _complete("RET-S", nothing_paid=True)
    assert exc.value.status_code == 409 and "being confirmed" in exc.value.detail
    assert not _stored(till)["refund_holds"]["RET-S"].get(returns_router.NOTHING_PAID_MARK)
    assert _docs(till) == []


def test_a_cash_press_whose_money_out_landed_first_keeps_the_return(till, monkeypatch, tasks):
    """The press's money-out mark landed after Needs completing read the hold:
    the takeover's mark loses, its fence row is taken back, nothing recorded."""
    _bill_with_hold(till, "RET-M", at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    real_unfinished = returns_router._unfinished_return

    def read_then_money_out(rid):
        state = real_unfinished(rid)
        till["db"].get_collection("orders").update_one(
            {"order_id": "ORD-1"}, {"$set": {f"refund_holds.{rid}.money_out": True}, "$inc": {"refund_holds_v": 1}})
        return state

    monkeypatch.setattr(returns_router, "_unfinished_return", read_then_money_out)
    with pytest.raises(HTTPException) as exc:
        _complete("RET-M", nothing_paid=True)
    assert exc.value.status_code == 409 and "Money moved" in exc.value.detail
    assert _ledger(till).find_one({"entry_id": "RET-M:credit"}) is None
    assert _docs(till) == []


def test_a_press_that_gave_everything_back_first_is_not_given_back_twice(till, monkeypatch, tasks):
    """The press refused on its own and released its hold (after its
    give-backs) between Needs completing's read and the takeover: the record
    is written, nothing is released or written off a second time."""
    released, written_off = _counted(monkeypatch)
    _bill_with_hold(till, "RET-G", at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    real_unfinished = returns_router._unfinished_return

    def read_then_released(rid):
        state = real_unfinished(rid)
        till["db"].get_collection("orders").update_one(
            {"order_id": "ORD-1"}, {"$unset": {f"refund_holds.{rid}": ""}, "$inc": {"refund_holds_v": 1}})
        return state

    monkeypatch.setattr(returns_router, "_unfinished_return", read_then_released)
    out = _complete("RET-G", nothing_paid=True)
    assert out["status"] == "NOTHING_PAID" and released == [] and written_off == []
    again = _complete("RET-G", nothing_paid=True)
    assert again.get("replayed") is True and released == [] and written_off == []


def test_the_fence_refuses_a_hold_nothing_was_paid_took():
    class Orders:
        doc = {"order_id": "O1", "refund_holds_v": 3, "refund_holds": {
            "RET-1": {"amount": 900.0, "at": "2026-11-16T00:00:00+00:00", "kept": True, "writing": True,
                      returns_router.NOTHING_PAID_MARK: True}}}

        def find_one(self, q):
            return dict(self.doc)

        def update_one(self, *a, **k):
            raise AssertionError("a refused fence writes nothing")

    assert returns_router.fence_bill_hold(Orders(), "O1", "RET-1") is False


# --- D85(2): a finished door's hold whose release failed --------------------------------


def _door_bill(till, key, **hold):
    h = {"amount": 100.0, "at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(), "kept": True,
         "door": "credit_note" if key.startswith("credit-note:") else "advance", "ttl": 900}
    h.update(hold)
    till["db"].get_collection("orders").insert_one(_order(status="DELIVERED", refund_holds={key: h},
                                                          refund_holds_v=1))


@pytest.mark.parametrize("row, live", [
    ({"type": scl.ISSUED}, False),
    ({"type": scl.ADJUSTED}, False),
    ({"type": "VOID", "void_settled": True}, False),
    ({"type": "VOID"}, False),
    ({"type": "VOID", "void_settled": False}, True),
    ({"type": "ISSUING"}, True),
    (None, True),
])
def test_a_credit_note_doors_hold_counts_as_finished_by_its_ledger_line(till, row, live):
    key = "credit-note:ORD-1:sacn:10000:90000"
    _door_bill(till, key)
    if row is not None:
        _ledger(till).insert_one({"_id": key.split(":", 1)[1], "entry_id": key.split(":", 1)[1], **row})
    got, _ = returns_router._live_refund_holds("ORD-1", _stored(till)["refund_holds"])
    assert (key in got) is live


@pytest.mark.parametrize("status, claim, live", [
    ("COMPLETED", "RET-ADV", False), ("VOIDED", None, False), ("PENDING", "RET-ADV", True),
    # D85(2), round 8: no record yet. Its claim still on the bill: the door may
    # be writing (a re-press frees a stale claim). No claim, or another's: it
    # stopped before its claim -- it can write nothing more, so it is finished.
    (None, "RET-ADV", True), (None, None, False), (None, "RET-OTHER", False)])
def test_an_advance_doors_hold_counts_as_finished_by_its_record(till, status, claim, live):
    _door_bill(till, "advance:RET-ADV", amount=1000.0)
    till["db"].get_collection("orders").update_one({"order_id": "ORD-1"}, {"$set": {"advance_refund_id": claim}})
    if status:
        till["db"].get_collection("returns").insert_one({"return_id": "RET-ADV", "order_id": "ORD-1",
                                                          "advance_refund": True, "status": status,
                                                          "net_refund": 1000.0})
    got, _ = returns_router._live_refund_holds("ORD-1", _stored(till)["refund_holds"])
    assert ("advance:RET-ADV" in got) is live


def test_an_unreadable_credit_note_line_fails_closed(till, monkeypatch):
    _door_bill(till, "credit-note:X1")

    def boom(*a, **k):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(_ledger(till), "find", boom)
    with pytest.raises(HTTPException) as exc:
        returns_router._live_refund_holds("ORD-1", _stored(till)["refund_holds"])
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_an_admin_note_whose_release_failed_never_locks_the_bill(till, monkeypatch, tasks):
    """End to end: the note is ISSUED, its hold release fails (kept). Before
    D85 every counter return and online refund of the bill was refused for
    good and the sweep told a person to ask the IMS team."""
    from api.services import shopify_refund as S

    _invoiced(till)
    monkeypatch.setattr(returns_router, "drop_bill_hold", lambda *a, **k: None)
    out = await _admin_note(till, monkeypatch)
    assert out["note_type"] == "CREDIT_NOTE"
    assert [k for k in _stored(till)["refund_holds"] if k.startswith("credit-note:")]  # left kept
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    key = [k for k in _stored(till)["refund_holds"]][0]
    till["db"].get_collection("orders").update_one({"order_id": "ORD-1"},
                                                   {"$set": {f"refund_holds.{key}.at": old}})
    assert returns_router.sweep_stuck_return_holds() == 0 and tasks == []
    prior, reserved, _ = returns_router._reserve_order_refund("ORD-1", 100.0, "RET-C", _stored(till))
    assert reserved and key not in _stored(till)["refund_holds"]  # the finished door's hold is dropped
    assert returns_router._release_order_refund("ORD-1", "RET-C")
    assert S._hold_order_for_online(till["db"], "ORD-1", "R1", 100.0) is True


def test_a_credit_note_door_that_stopped_before_its_record_tells_a_superadmin_to_press_again(till, tasks):
    _door_bill(till, "credit-note:ORD-1:sacn:10000:90000")
    assert returns_router.sweep_stuck_return_holds() == 1
    assert "SAME correction again" in tasks[0]["description"] and "IMS team" not in tasks[0]["description"]


# --- Needs completing "Cash paid" on a press that may have restocked -------------------


def test_a_cash_paid_record_never_tells_a_person_to_add_a_unit_already_in_stock(till, tasks):
    """The press's money-out mark landed, then it stalled; its restock may
    already have run. The recovered record used to say 'Put its units back in
    stock by hand' -- a person following it doubled the stock."""
    _bill_with_hold(till, "RET-CP", money_out=True, net_amount=1800.0, return_type="RETURN",
                    gst_view={"taxable": 1525.42, "tax": 274.58, "gross": 1800.0, "gst_rate": 18.0},
                    tenders=[{"method": "CASH", "amount": 1800.0}])
    doc = _complete("RET-CP", cash_leg_paid=True)
    assert doc["status"] == "COMPLETED"
    assert "already shows in stock" in doc["message"] and "do not add it again" in doc["message"]


# --- D85(1), round 8: Nothing was paid between the press's claim fence and its unit claim ---


def _real_claims(till, monkeypatch):
    """A credit-note return whose unit claims and releases are the REAL ones,
    on the bill line with Mongo's positional semantics (TokenLines); the
    bill's refund holds stay on the stored bill (the takeover's mark and the
    press's claim meet on ONE document, as in Mongo)."""
    payload = _credit_note_return(till, monkeypatch)
    lines = TokenLines([{"item_id": "li1", "product_id": "P1", "quantity": 2, "claim_tokens": []}],
                       till["db"].get_collection("orders"))
    monkeypatch.setattr(returns_router, "_orders_coll", lambda: lines)
    monkeypatch.setattr(returns_router, "_claim_returnable_qty", REAL_CLAIM)
    monkeypatch.setattr(returns_router, "_release_returnable_qty", REAL_RELEASE)
    monkeypatch.setattr(returns_router, "_record_unused_credit_note", lambda *a, **k: True)
    return payload, lines


def test_nothing_paid_before_the_press_claims_its_units_leaves_no_claim_on_the_bill(till, monkeypatch, tasks):
    """Reviewers' must-fix (head 48638aba): the press passed its claim fence
    and stalled before _claim_returnable_qty; Nothing was paid took it over and
    released its lines by token (none there yet: 'done'); the press then woke
    and claimed. Its next fence refused, and its give-back trusted the 'done'
    mark: returned_qty 1 and its token stayed on the line with no return doc
    -- the unit locked out of every later return and refund."""
    payload, lines = _real_claims(till, monkeypatch)
    real_claim = REAL_CLAIM
    out = {}

    def stalled(*a, **k):
        if "takeover" not in out:
            _takeover_now(till, out)
        return real_claim(*a, **k)

    monkeypatch.setattr(returns_router, "_claim_returnable_qty", stalled)
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert isinstance(out["takeover"], dict) and out["takeover"]["status"] == "NOTHING_PAID", out
    assert r.status_code == 409 and "NOTHING was issued" in r.text, r.text
    assert _issued(till) == [] and _balance(till) == 0.0 and _docs(till) == ["NOTHING_PAID"]
    line = lines.lines[0]
    assert float(line.get("returned_qty") or 0) == 0.0, line
    assert not line.get("claim_tokens"), line
    assert not (_stored(till).get("refund_holds") or {})
    # The unit is returnable again: a fresh return of it goes through.
    monkeypatch.setattr(returns_router, "_claim_returnable_qty", real_claim)
    monkeypatch.setattr(returns_router, "next_document_number", lambda *a, **k: "TST01/C2627/0003")
    again = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert again.status_code == 201, again.text
    assert float(lines.lines[0].get("returned_qty") or 0) == 1.0


def test_a_claim_that_landed_before_nothing_paid_is_released_by_the_takeover(till, monkeypatch, tasks):
    """The other order: the press's claim lands, THEN Nothing was paid takes
    it over -- the takeover's release finds the token and gives the unit back
    once; the press issues nothing."""
    payload, lines = _real_claims(till, monkeypatch)
    real_claim = REAL_CLAIM
    out = {}

    def landed_then_stalled(*a, **k):
        res = real_claim(*a, **k)
        if "takeover" not in out:
            _takeover_now(till, out)
        return res

    monkeypatch.setattr(returns_router, "_claim_returnable_qty", landed_then_stalled)
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert isinstance(out["takeover"], dict) and out["takeover"]["status"] == "NOTHING_PAID", out
    assert r.status_code == 409 and _issued(till) == [] and _docs(till) == ["NOTHING_PAID"]
    line = lines.lines[0]
    assert float(line.get("returned_qty") or 0) == 0.0 and not line.get("claim_tokens"), line


def test_the_counter_claim_needs_its_own_live_hold_on_the_bill(till, monkeypatch):
    """The shared rule (_claim_returnable_qty hold_key): the claim and the
    hold are on ONE bill document, so the claim lands only while the press's
    hold is there and not taken by Nothing was paid -- in the same write."""
    _, lines = _real_claims(till, monkeypatch)
    orders = till["db"].get_collection("orders")
    orig = {"item_id": "li1", "product_id": "P1", "quantity": 2}
    mark = returns_router.NOTHING_PAID_MARK
    assert returns_router._claim_returnable_qty("ORD-1", orig, 1.0, token="T:0", hold_key="RET-X") is False
    orders.update_one({"order_id": "ORD-1"}, {"$set": {"refund_holds": {"RET-X": {"kept": True, mark: True}}}})
    assert returns_router._claim_returnable_qty("ORD-1", orig, 1.0, token="T:0", hold_key="RET-X") is False
    orders.update_one({"order_id": "ORD-1"}, {"$set": {"refund_holds": {"RET-X": {"kept": True}}}})
    assert returns_router._claim_returnable_qty("ORD-1", orig, 1.0, token="T:0", hold_key="RET-X") is True
    assert float(lines.lines[0].get("returned_qty") or 0) == 1.0


# --- D85(1), round 8: "Cash paid" and a press still running after its money-out mark ---


def _cash_press(till, monkeypatch):
    """A cash RETURN whose returns collection refuses a second record of one
    return id (prod: uniq_returns_return_id), and whose restock is counted."""
    from pymongo.errors import DuplicateKeyError

    payload = _two_unit_bill(till, monkeypatch)
    payload.update(refund_method="CASH", refund_tenders=[{"method": "CASH", "amount": 1800.0}])
    rcoll = till["db"].get_collection("returns")
    real_insert = rcoll.insert_one

    def unique_return_id(doc, *a, **k):
        if doc.get("return_id") and rcoll.find_one({"return_id": doc["return_id"]}):
            raise DuplicateKeyError(f"E11000 duplicate key return_id {doc['return_id']!r}")
        return real_insert(doc, *a, **k)

    monkeypatch.setattr(rcoll, "insert_one", unique_return_id)
    restocks = []

    def restock(items, store_id, return_id, **k):
        restocks.append(return_id)
        return {"restocked": [{"product_id": "P1", "reactivated": 1}], "restock_stock_ids": ["S1"],
                "applied": True, "skipped": [], "restock_store_id": store_id, "restock_store_ids": [store_id],
                "restock_store_redirected_from": None, "restock_store_reason": None}

    monkeypatch.setattr(returns_router, "_restock_good_items", restock)
    return payload, rcoll, restocks


def _cash_paid_now(till, out):
    rid = _aged_past_the_timeout(till)
    out["rid"] = rid

    def run():
        try:
            out["record"] = _complete(rid, cash_leg_paid=True)
        except HTTPException as exc:
            out["record"] = exc.status_code
    t = threading.Thread(target=run)
    t.start()
    t.join(60)


def _retry_restock(rid):
    import asyncio

    from tests.test_pos_money_doors_r13 import _user
    return asyncio.run(returns_router.retry_restock(rid, current_user=_user("STORE_MANAGER")))


def test_cash_paid_before_the_press_restocks_leaves_the_units_to_the_record(till, monkeypatch, tasks):
    """Reviewers' flag (D85(1): every completing writer is fenced): Cash paid
    wrote the record (restock_applied False) while the press stalled after its
    money-out mark; the press then woke and restocked, and the record's
    Restock put the units back a SECOND time (a phantom unit)."""
    payload, rcoll, restocks = _cash_press(till, monkeypatch)
    real_keep, out = returns_router._keep_order_refund_hold, {}

    def money_out_then_stalled(order_id, rid, *a, extra=None, **k):
        res = real_keep(order_id, rid, *a, extra=extra, **k)
        if "money_out" in (extra or {}) and "record" not in out:
            _cash_paid_now(till, out)
        return res

    monkeypatch.setattr(returns_router, "_keep_order_refund_hold", money_out_then_stalled)
    _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert isinstance(out["record"], dict) and out["record"]["status"] == "COMPLETED", out
    assert restocks == []  # the press put nothing back
    rec = rcoll.find_one({"_id": f"recovered:{out['rid']}"})
    assert rec["restock_applied"] is False and not rec.get("restock_left_to_counter")
    assert len(list(rcoll.find({"return_id": out["rid"]}))) == 1
    # The record's Restock puts the units back -- once.
    first = _retry_restock(out["rid"])
    second = _retry_restock(out["rid"])
    assert restocks == [out["rid"]] and first["restock_applied"] is True, first
    assert second["message"] == "Already restocked" and restocks == [out["rid"]]


def test_cash_paid_after_the_press_began_restocking_leaves_the_units_to_the_press(till, monkeypatch, tasks):
    """The other order: the press passed its restock fence, then Cash paid
    answered. The units are the press's: the record says so and its Restock
    adds nothing."""
    payload, rcoll, restocks = _cash_press(till, monkeypatch)
    real_restock, out = returns_router._restock_good_items, {}

    def stalled(*a, **k):
        if "record" not in out:
            _cash_paid_now(till, out)
        return real_restock(*a, **k)

    monkeypatch.setattr(returns_router, "_restock_good_items", stalled)
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert isinstance(out["record"], dict) and out["record"]["status"] == "COMPLETED", out
    # D92(4): Cash paid recorded the cash -- the counter never says "Refund recorded".
    assert r.status_code == 409 and r.json()["detail"] == returns_router.RECORD_TAKEN_MESSAGE, r.text
    assert restocks == [out["rid"]]  # the press's own restock, once
    rec = rcoll.find_one({"_id": f"recovered:{out['rid']}"})
    assert rec["restock_left_to_counter"] is True
    again = _retry_restock(out["rid"])
    assert again["restock_applied"] is False and "adds nothing" in again["message"], again
    assert restocks == [out["rid"]]


def test_the_press_puts_its_units_back_when_nobody_took_its_record(till, monkeypatch, tasks):
    payload, rcoll, restocks = _cash_press(till, monkeypatch)
    r = _advance_client().post("/api/v1/returns", json=payload, headers=_hdr("STORE_MANAGER"))
    assert r.status_code == 201 and r.json()["restock_applied"] is True and len(restocks) == 1, r.text
    hold = next(iter(_stored(till)["refund_holds"].values()))
    assert hold.get("restocking") is True and not hold.get(returns_router.RECORD_TAKEN_MARK)


# --- D85(2), round 8: an advance door that stopped after its fence, before its record ---


def _old(till, key, hours=2):
    """Age ONLY that door hold past its ttl."""
    old = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    till["db"].get_collection("orders").update_one({"order_id": "ORD-1"}, {"$set": {f"refund_holds.{key}.at": old}})


def _adv_key(till):
    keys = [k for k in (_stored(till).get("refund_holds") or {}) if k.startswith("advance:")]
    assert len(keys) == 1, _stored(till).get("refund_holds")
    return keys[0]


def test_an_advance_door_that_died_after_its_fence_never_locks_the_bill(till, monkeypatch):
    """Reviewers' flag: the door's worker died after its fence (its hold KEPT,
    never released), before its claim. A re-press could not clear it (a new
    return id each press): every refund of the bill was refused and the task
    said 'ask the IMS team'. Now pressing Refund advance again goes through."""
    from tests.test_pos_money_doors import _ADV_BODY, _advance_order

    _advance_order(till)
    real_fence, real_drop = returns_router.fence_bill_hold, returns_router.drop_bill_hold
    monkeypatch.setattr(returns_router, "drop_bill_hold", lambda *a, **k: None)  # the worker is gone

    def dies(coll, order_id, key, **k):
        real_fence(coll, order_id, key, **k)
        raise RuntimeError("worker died after its fence")

    monkeypatch.setattr(returns_router, "fence_bill_hold", dies)
    with pytest.raises(RuntimeError):
        _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    key = _adv_key(till)
    assert _stored(till)["refund_holds"][key]["kept"] and _stored(till).get("advance_refund_id") is None
    monkeypatch.setattr(returns_router, "fence_bill_hold", real_fence)
    monkeypatch.setattr(returns_router, "drop_bill_hold", real_drop)
    _old(till, key)
    again = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY, headers=_hdr("ACCOUNTANT"))
    assert again.status_code == 201, again.text
    assert _stored(till)["refund_holds"] == {} and _stored(till)["status"] == "CANCELLED"
    docs = list(till["db"].get_collection("returns").find({"advance_refund": True}))
    assert [d["status"] for d in docs] == ["COMPLETED"]


def test_an_advance_door_cleared_while_it_stalled_claims_nothing_when_it_wakes(till, monkeypatch):
    """The other half: the stalled door is not dead. Its hold aged past its
    ttl and another door took the bill (dropping it); the door wakes and its
    claim -- which now needs its own hold on the bill -- lands nowhere."""
    from tests.test_pos_money_doors import _ADV_BODY, _advance_order

    _advance_order(till)
    real_fence, out = returns_router.fence_bill_hold, {}
    go, at_fence = threading.Event(), threading.Event()

    def stalls(coll, order_id, key, **k):
        ok = real_fence(coll, order_id, key, **k)
        at_fence.set()
        assert go.wait(60)
        return ok

    monkeypatch.setattr(returns_router, "fence_bill_hold", stalls)

    def press():
        out["r"] = _advance_client().post("/api/v1/returns/advance-refund", json=_ADV_BODY,
                                          headers=_hdr("ACCOUNTANT"))
    t = threading.Thread(target=press)
    t.start()
    assert at_fence.wait(60)
    key = _adv_key(till)
    _old(till, key)
    orders = till["db"].get_collection("orders")
    assert returns_router.take_bill_hold(orders, "ORD-1", "credit-note:other", 100.0, door="credit_note")
    returns_router.drop_bill_hold(orders, "ORD-1", "credit-note:other")
    go.set()
    t.join(60)
    assert out["r"].status_code == 409, out["r"].text
    assert _stored(till).get("advance_refund_id") is None and _stored(till)["status"] == "CONFIRMED"
    assert till["db"].get_collection("returns").count_documents({}) == 0


# --- D85(1), round 8: a VOID credit line's late balance move vs Nothing was paid ---


def _void_line_bill(till, rid="RET-V"):
    eid = f"{rid}:credit"
    _bill_with_hold(till, rid, writing=True)
    _customer(till)
    _ledger(till).insert_one({
        "_id": eid, "entry_id": eid, "customer_id": "CUST-1", "type": "VOID", "final_type": scl.ISSUED,
        "delta": 0.0, "void_delta": 900.0, "amount": 900.0, "applied": False, "note_number": "TST01/C2627/0031",
        "pending_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "undo_on_void": {"order_id": "ORD-1", "return_id": rid,
                         "lines": [{"item_id": "li1", "product_id": "P1", "qty": 1.0}]}})
    return eid


def _late_move(eid):
    return cust_router.apply_store_credit_delta(returns_router.get_customer_repository(), "CUST-1", 900.0,
                                                guard_ref=eid, entry_id=eid)


def test_a_void_lines_late_move_never_lands_after_nothing_paid(till, monkeypatch, tasks):
    """Reviewers' flag: the press stalled on its balance move past the
    reconciler's timeout (its line VOID), Nothing was paid answered, then the
    move landed: the line healed to ISSUED beside the NOTHING_PAID record --
    Rs 900 credit, its number both filed and written off, the bill cap blind
    to it. Now the takeover fences the move's guard first."""
    _counted(monkeypatch)
    eid = _void_line_bill(till)
    doc = _complete("RET-V", nothing_paid=True)
    assert doc["status"] == "NOTHING_PAID"
    assert _late_move(eid) is False and _balance(till) == 0.0
    assert _ledger(till).find_one({"entry_id": eid})["type"] == "VOID"
    assert not cust_router._guard_landed(returns_router.get_customer_repository(), "CUST-1", eid)


def test_a_void_lines_move_that_landed_first_refuses_nothing_paid(till, monkeypatch, tasks):
    _counted(monkeypatch)
    eid = _void_line_bill(till)
    assert _late_move(eid) is True and _balance(till) == 900.0  # the money moved
    with pytest.raises(HTTPException) as exc:
        _complete("RET-V", nothing_paid=True)
    assert exc.value.status_code == 409 and "Money moved" in exc.value.detail
    assert _docs(till) == [] and _balance(till) == 900.0


def test_a_record_completed_from_a_hold_restocks_the_ims_product(till, tasks):
    """Native probe J (online bill): the hold kept the bill line's Shopify
    product id (None), so the record's Restock reported 'applied' and put
    nothing back. The record carries the IMS product the press returned."""
    _bill_with_hold(till, "RET-IP", money_out=True, net_amount=900.0, return_type="RETURN",
                    gst_view={"taxable": 857.14, "tax": 42.86, "gross": 900.0, "gst_rate": 5.0},
                    tenders=[{"method": "CASH", "amount": 900.0}],
                    lines=[{"item_id": "li1", "product_id": None, "ims_product_id": "IMS-P", "qty": 1.0,
                            "condition": "GOOD", "restock": True}])
    doc = _complete("RET-IP", cash_leg_paid=True)
    assert doc["items"][0]["product_id"] == "IMS-P" and doc["items"][0]["restock"] is True
