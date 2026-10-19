"""A12-2 (owner 2026-10-18): a supplier payment picks its bill, an advance can
be applied to a bill later, and no money lands on a bill beyond what is left.

The Cash Flow payment form never sent bill_id, so since #1219 (aging counts
only explicit allocations) every screen payment left its bill OUTSTANDING and
skipped the rejected-goods hold. Synthetic mongomock rows only.
"""
from types import SimpleNamespace

import pytest

from api.routers import vendors as vendors_pkg
from test_finance_ap_scope_round2 import ADMIN, ACCT_PUNE, DHN, PUN
from test_supplier_advances_a12 import bill, payment, report_world  # noqa: F401


@pytest.fixture(autouse=True)
def _isolate_db():
    yield


@pytest.fixture
def ap(report_world, monkeypatch):  # noqa: F811
    memory, world = report_world
    monkeypatch.setattr(vendors_pkg, "get_vendor_repository", lambda: SimpleNamespace(
        find_by_id=lambda identifier: {"vendor_id": identifier, "legal_name": "Supplier"},
    ))
    memory.vendor_bills.insert_one(bill(store_id=PUN, bill_number="INV-1"))
    return memory, world


def _pay(world, body, user=ACCT_PUNE):
    return world.post("/vendors/V1/payments", user, {"payment_date": "2026-10-15", **body})


def _advance_left(world, payment_id, user):
    """What the form shows is left of the advance: its row in the payments
    list (cash + TDS); 0 when this login cannot see it."""
    rows = world.get("/vendors/V1/payments", user).json().get("payments", [])
    row = next((r for r in rows if r.get("payment_id") == payment_id), {})
    return round((row.get("amount") or 0) + (row.get("tds_amount") or 0), 2)


def _apply(world, payment_id, bill_id, amount, user=ACCT_PUNE, left=None):
    """The Apply advance form: it sends what it showed was left (`left`
    overrides it: a request made earlier, before another one landed)."""
    seen = _advance_left(world, payment_id, user) if left is None else left
    return world.post(f"/vendors/V1/payments/{payment_id}/apply", user,
                      {"bill_id": bill_id, "amount": amount, "advance_left": seen})


def _owed_and_advances(world):
    totals = world.ok("/vendors/ap-aging", ACCT_PUNE)["totals"]
    return totals["owed"], totals["advances"]


def _status(world):
    """The bill's status as the payment form's bill list reads it."""
    return world.ok("/vendors/V1/bills", ACCT_PUNE)["bills"][0]["status"]


def test_a_payment_that_names_its_bill_clears_it_in_aging(ap):
    memory, world = ap
    assert _pay(world, {"amount": 1000, "bill_id": "B1"}).status_code == 201
    assert _owed_and_advances(world) == (0, 0)
    assert _status(world) == "PAID"


def test_an_advance_stays_apart_from_the_bill(ap):
    memory, world = ap
    resp = _pay(world, {"amount": 400})
    assert resp.status_code == 201 and resp.json()["bill_id"] is None
    assert _owed_and_advances(world) == (1000, 400)
    assert _status(world) == "OUTSTANDING"


def test_apply_part_then_the_rest_of_an_advance(ap):
    memory, world = ap
    adv = _pay(world, {"amount": 400}).json()["payment_id"]

    part = _apply(world, adv, "B1", 300)
    assert part.status_code == 200, part.text
    assert part.json()["advance_left"] == 100
    assert _owed_and_advances(world) == (700, 100)
    assert _status(world) == "PARTIAL"
    left = memory.vendor_payments.find_one({"payment_id": adv})
    applied = memory.vendor_payments.find_one({"applied_from": adv})
    assert (left["amount"], left["bill_id"]) == (100, None)
    assert (applied["amount"], applied["bill_id"], applied["store_id"]) == (300, "B1", PUN)
    assert applied["payment_date"] == left["payment_date"]  # same money, same day

    rest = _apply(world, adv, "B1", 100)
    assert rest.status_code == 200, rest.text
    assert _owed_and_advances(world) == (600, 0)
    assert memory.vendor_payments.find_one({"payment_id": adv})["bill_id"] == "B1"
    # The cash paid never changed: two ordinary payments of 400 in all.
    assert sum(p["amount"] for p in memory.vendor_payments.find({})) == 400
    assert _apply(world, adv, "B1", 1).status_code == 409  # nothing left to apply


def test_an_advance_never_settles_a_bill_on_its_own(ap):
    """Paying the bill's whole amount as an advance leaves the bill owed."""
    memory, world = ap
    _pay(world, {"amount": 1000})
    assert _owed_and_advances(world) == (1000, 1000)
    assert _status(world) == "OUTSTANDING"


@pytest.mark.parametrize("amount,code", [(1001, 422), (0, 422)])
def test_apply_refuses_more_than_the_advance(ap, amount, code):
    memory, world = ap
    memory.vendor_bills.update_one({}, {"$set": {"total_amount": 5000}})
    adv = _pay(world, {"amount": 1000}).json()["payment_id"]
    assert _apply(world, adv, "B1", amount).status_code == code
    assert memory.vendor_payments.count_documents({}) == 1


def test_neither_door_puts_more_on_a_bill_than_is_left(ap):
    memory, world = ap
    over = _pay(world, {"amount": 1200, "bill_id": "B1"})
    assert over.status_code == 422 and over.json()["detail"]["left"] == 1000
    adv = _pay(world, {"amount": 1200}).json()["payment_id"]
    assert _apply(world, adv, "B1", 1200).status_code == 422
    assert _apply(world, adv, "B1", 1000).status_code == 200  # up to what is left
    assert _owed_and_advances(world) == (0, 200)


def test_the_rejected_goods_hold_now_stops_a_screen_payment_and_an_apply(ap, monkeypatch):
    memory, world = ap
    adv = _pay(world, {"amount": 400}).json()["payment_id"]
    monkeypatch.setattr("api.services.receipt_payment_hold.rejected_receipt_hold",
                        lambda db, bill_id: "Rejected goods on this bill need the supplier's credit.")
    held = _pay(world, {"amount": 400, "bill_id": "B1"})
    assert held.status_code == 409
    assert held.json()["detail"]["code"] == "REJECTED_GOODS_NO_DEBIT_NOTE"
    assert _apply(world, adv, "B1", 400).status_code == 409
    assert _owed_and_advances(world) == (1000, 400)
    assert memory.vendor_payments.count_documents({}) == 1


def test_apply_stays_in_the_shop_and_in_reach(ap):
    memory, world = ap
    memory.vendor_bills.insert_one(bill("B2", store_id=DHN))
    pune_adv = _pay(world, {"amount": 400}).json()["payment_id"]
    assert _apply(world, pune_adv, "B2", 100, ADMIN).status_code == 422  # another shop's bill
    memory.vendor_payments.insert_one(payment(300, store_id=DHN, payment_id="DHN-ADV"))
    assert _apply(world, "DHN-ADV", "B1", 100).status_code == 404  # not this login's
    assert _apply(world, pune_adv, "nope", 100).status_code == 404
    paid = _pay(world, {"amount": 100, "bill_id": "B1"}).json()["payment_id"]
    assert _apply(world, paid, "B1", 100).status_code == 409  # already on a bill


def test_an_advance_with_tds_applies_only_whole(ap):
    memory, world = ap
    adv = _pay(world, {"amount": 390, "tds_amount": 10, "tds_section": "194C_OTHER"}).json()["payment_id"]
    assert _apply(world, adv, "B1", 200).status_code == 422
    assert _apply(world, adv, "B1", 400).status_code == 200
    assert _owed_and_advances(world) == (600, 0)


class _StaleAdvance:
    """vendor_payments whose read of one advance returns an older copy."""

    def __init__(self, real, stale):
        self._real, self._stale = real, stale

    def __getattr__(self, name):
        return getattr(self._real, name)

    def find_one(self, flt, *a, **k):
        if flt.get("payment_id") == self._stale["payment_id"]:
            return dict(self._stale)
        return self._real.find_one(flt, *a, **k)


@pytest.mark.parametrize("second", [300, 400])
def test_two_racing_applications_of_one_advance_apply_once(ap, monkeypatch, second):
    """The second request read the advance before the first changed it."""
    memory, world = ap
    adv = _pay(world, {"amount": 400}).json()["payment_id"]
    stale = memory.vendor_payments.find_one({"payment_id": adv}, {"_id": 0})
    assert _apply(world, adv, "B1", 300).status_code == 200
    proxy = SimpleNamespace(get_collection=lambda name: (
        _StaleAdvance(memory[name], stale) if name == "vendor_payments" else memory[name]))
    monkeypatch.setattr(vendors_pkg, "_get_db", lambda: proxy)
    # Sent with what it saw (400): only the compare-and-set can stop it.
    assert _apply(world, adv, "B1", second, left=400).status_code == 409
    assert sum(p["amount"] for p in memory.vendor_payments.find({})) == 400
    assert memory.vendor_payments.count_documents({"applied_from": adv}) == 1
    assert memory.vendor_payments.find_one({"payment_id": adv})["amount"] == 100


# --- review round 2 (B2 + the cheap items) ---------------------------------


def test_an_advance_with_no_shop_applies_only_to_the_shop_the_ledger_counts_it_under(ap):
    """A legacy advance with no shop is counted under the shop of the
    supplier's latest bill on or before it (B1, Pune); a Dhanbad bill would
    move it between shops' books."""
    memory, world = ap
    memory.vendor_bills.insert_one(bill("B2", store_id=DHN, bill_date="2026-10-01"))
    memory.vendor_payments.insert_one(payment(400, payment_id="LEGACY", payment_date="2026-09-15"))
    wrong = _apply(world, "LEGACY", "B2", 100, ADMIN)
    assert wrong.status_code == 422 and "no shop on record" in wrong.json()["detail"]
    assert _apply(world, "LEGACY", "B1", 100, ADMIN).status_code == 200


_CLOSED = "That month is closed - ask an admin to reopen it."


# An apply changes figures only from the LATER of the advance's day and the
# bill's day onward; a part-apply also adds a line on the advance's own day.
@pytest.mark.parametrize("advance_day,bill_day,locked,amount,code", [
    ("2026-09-15", "2026-10-01", [9], 400, 200),    # Sept advance, Oct bill, Sept closed: whole apply is fine
    ("2026-09-15", "2026-10-01", [9], 100, 423),    # ...but a part-apply splits the advance's Sept row
    ("2026-10-02", "2026-09-01", [9], 100, 200),    # Oct advance, Sept bill: nothing from Sept changes
    ("2026-09-15", "2026-10-01", [10], 400, 423),   # the month the change starts is closed
    ("2026-06-10", "2026-08-01", [10], 400, 423),   # a later closed month, not next to the change
    ("2026-06-10", "2026-08-01", [7], 400, 200),    # a closed month before the change starts
    ("2026-09-15", "2026-10-01", [11], 400, 200),   # a lock on a month not yet started is no lock
    ("2026-09-15", "2026-10-01", [13], 100, 200),   # nor is a mistyped month
    ("2026-09-15", "2026-10-01", [8], 100, 200),    # an earlier closed month
])
def test_a_closed_month_stays_closed(ap, advance_day, bill_day, locked, amount, code):
    memory, world = ap
    memory.vendor_bills.update_one({}, {"$set": {"bill_date": bill_day}})
    memory.vendor_payments.insert_one(payment(400, store_id=PUN, payment_id="ADV", payment_date=advance_day))
    memory.period_locks.insert_many([{"month": m, "year": 2026} for m in locked])
    resp = _apply(world, "ADV", "B1", amount)
    assert resp.status_code == code, resp.text
    if code == 423:
        assert resp.json()["detail"] == _CLOSED
        assert memory.vendor_payments.count_documents({}) == 1
        assert memory.vendor_payments.find_one({})["amount"] == 400


@pytest.mark.parametrize("pay_day,bill_day,locked,taken", [
    ("2026-10-10", "2026-09-01", [9], True),
    ("2026-08-10", "2026-08-01", [10], False),
    ("2026-10-10", "2026-10-01", [10], False),
    ("2026-08-10", "2026-07-01", [6, 10], False),
    ("2026-10-10", "2026-08-01", [9], True),
    ("2026-10-10", "2026-10-01", [11], True),
])
def test_paying_a_bill_and_applying_an_advance_to_it_agree_on_closed_months(ap, pay_day, bill_day, locked, taken):
    """The same money put on the same bill: a payment naming it, or an advance
    of that day part-applied to it (both add a line on the money's day)."""
    memory, world = ap
    memory.vendor_bills.update_one({}, {"$set": {"bill_date": bill_day}})
    memory.vendor_payments.insert_one(payment(400, store_id=PUN, payment_id="ADV", payment_date=pay_day))
    memory.period_locks.insert_many([{"month": m, "year": 2026} for m in locked])
    paid = world.post("/vendors/V1/payments", ACCT_PUNE, {"payment_date": pay_day, "amount": 100, "bill_id": "B1"})
    applied = _apply(world, "ADV", "B1", 100)
    assert (paid.status_code == 201, applied.status_code == 200) == (taken, taken), (paid.text, applied.text)
    if paid.status_code != 201:  # (the payment's own month keeps its older words)
        assert paid.status_code == applied.status_code == 423


def test_an_amount_that_rounds_to_nothing_is_refused(ap):
    memory, world = ap
    adv = _pay(world, {"amount": 400}).json()["payment_id"]
    assert _apply(world, adv, "B1", 0.004).status_code == 422
    assert memory.vendor_payments.count_documents({}) == 1


def test_a_bill_takes_up_to_re_1_over_what_is_left(ap):
    memory, world = ap
    assert _pay(world, {"amount": 1001.5, "bill_id": "B1"}).status_code == 422
    assert _pay(world, {"amount": 1000.5, "bill_id": "B1"}).status_code == 201


def test_the_form_is_offered_exactly_what_the_refusal_allows(ap):
    """A post-dated cheque on the bill: today it still reads 1,000 outstanding,
    but only 600 is left to pay once the cheque clears -- the list says 600
    and the server takes 600, never more."""
    memory, world = ap
    memory.vendor_payments.insert_one(payment(400, "B1", store_id=PUN, payment_date="2026-10-20"))
    listed = world.ok("/vendors/V1/bills", ACCT_PUNE)["bills"][0]
    assert (listed["outstanding"], listed["left_to_pay"]) == (1000, 600)
    assert _pay(world, {"amount": 700, "bill_id": "B1"}).json()["detail"]["left"] == 600
    assert _pay(world, {"amount": 600, "bill_id": "B1"}).status_code == 201


class _InsertFailsAfterRival:
    """vendor_payments whose next insert fails -- after a rival application
    of the same advance has taken its own share in between."""

    def __init__(self, real, adv):
        self._real, self._adv = real, adv

    def __getattr__(self, name):
        return getattr(self._real, name)

    def insert_one(self, doc, *a, **k):
        left = self._real.find_one({"payment_id": self._adv})["amount"]
        self._real.update_one({"payment_id": self._adv}, {"$set": {"amount": left - 50}})
        self._real.insert_one({**doc, "payment_id": "RIVAL", "amount": 50})
        raise OSError("insert failed")


def test_a_failed_apply_gives_back_only_what_it_took(ap, monkeypatch):
    memory, world = ap
    adv = _pay(world, {"amount": 400}).json()["payment_id"]
    proxy = SimpleNamespace(get_collection=lambda name: (
        _InsertFailsAfterRival(memory[name], adv) if name == "vendor_payments" else memory[name]))
    monkeypatch.setattr(vendors_pkg, "_get_db", lambda: proxy)
    assert _apply(world, adv, "B1", 300, left=400).status_code == 500
    # The rival's 50 stands; the failed 300 went back: 350 + 50 = 400 paid.
    assert memory.vendor_payments.find_one({"payment_id": adv})["amount"] == 350
    assert sum(p["amount"] for p in memory.vendor_payments.find({})) == 400


# --- review round 4 -----------------------------------------------------------


def test_a_retried_payment_request_records_one_payment(ap):
    """The client retries a 5xx; a 504 can come after the save. The same
    request id answers the payment already recorded."""
    memory, world = ap
    body = {"amount": 300, "bill_id": "B1", "request_id": "req-0001-abcd"}
    first, again = _pay(world, body), _pay(world, body)
    assert first.status_code == again.status_code == 201
    assert first.json()["payment_id"] == again.json()["payment_id"]
    assert memory.vendor_payments.count_documents({}) == 1
    assert _owed_and_advances(world) == (700, 0)
    other = _pay(world, {**body, "request_id": "req-0002-abcd"})
    assert other.status_code == 201 and memory.vendor_payments.count_documents({}) == 2


def test_the_same_payment_request_racing_itself_records_one(ap, monkeypatch):
    """Both copies passed the 'already recorded?' read before either saved:
    the unique request id lets one through and the other answers with it."""
    from api.routers.vendors import ap_payments

    memory, world = ap
    real, seen = ap_payments._replayed_payment, []

    def missed_once(db, request_id, sent, user):
        if not seen:
            seen.append(1)
            memory.vendor_payments.insert_one(  # the twin saved in between
                payment(300, "B1", store_id=PUN, payment_id="TWIN", request_id=request_id,
                        payment_date="2026-10-15", tds_amount=0.0))
            return None
        return real(db, request_id, sent, user)

    monkeypatch.setattr(vendors_pkg, "_replayed_payment", missed_once)
    resp = _pay(world, {"amount": 300, "bill_id": "B1", "request_id": "req-race-0001"})
    assert resp.status_code == 201 and resp.json()["payment_id"] == "TWIN"
    assert memory.vendor_payments.count_documents({}) == 1


def test_a_repeated_part_apply_is_refused_not_applied_twice(ap):
    memory, world = ap
    memory.vendor_bills.update_one({}, {"$set": {"total_amount": 5000}})
    adv = _pay(world, {"amount": 1000}).json()["payment_id"]
    assert _apply(world, adv, "B1", 300, left=1000).status_code == 200
    again = _apply(world, adv, "B1", 300, left=1000)  # the retried copy
    assert again.status_code == 409 and "already applied" in again.json()["detail"]
    assert memory.vendor_payments.find_one({"payment_id": adv})["amount"] == 700
    assert memory.vendor_payments.count_documents({"applied_from": adv}) == 1


@pytest.mark.parametrize("month,year,code", [(13, 2026, 422), (0, 2026, 422), (5, 2062, 422),
                                             (11, 2026, 422), (9, 2026, 200), (10, 2026, 200)])
def test_only_a_month_that_has_started_can_be_closed(ap, monkeypatch, month, year, code):
    """A mistyped lock (month 13, 05/2062) or one on a month not yet started
    is refused at the door -- nothing could reopen it."""
    import asyncio
    from datetime import datetime
    from fastapi import HTTPException
    from api.routers.finance import period_lock
    from test_finance_ap_scope_round2 import _DBProxy

    memory, _ = ap
    monkeypatch.setattr(period_lock, "_get_db", lambda: _DBProxy(memory))
    monkeypatch.setattr("api.utils.ist.now_ist_naive", lambda: datetime(2026, 10, 15, 12))
    try:
        asyncio.run(period_lock.lock_period(month, year, ADMIN))
        status = 200
    except HTTPException as exc:
        status = exc.status_code
    assert status == code
    assert memory.period_locks.count_documents({}) == (1 if code == 200 else 0)


def test_a_payment_dated_by_timestamp_falls_in_its_ist_month(ap):
    """20:00 UTC on 30 Sept is 01:30 IST on 1 Oct: October's money. With
    September closed it is taken, and stored as the IST day."""
    memory, world = ap
    memory.period_locks.insert_one({"month": 9, "year": 2026})
    resp = _pay(world, {"amount": 100, "bill_id": "B1", "payment_date": "2026-09-30T20:00:00+00:00"})
    assert resp.status_code == 201, resp.text
    assert memory.vendor_payments.find_one({})["payment_date"] == "2026-10-01"


@pytest.mark.parametrize("bad", ["", "garbage", "30/09/2026", "2062-01-01", "2016-01-01"])
def test_a_payment_date_that_is_no_real_day_is_refused(ap, bad):
    memory, world = ap
    assert _pay(world, {"amount": 100, "payment_date": bad}).status_code == 422
    assert memory.vendor_payments.count_documents({}) == 0


def test_a_legacy_advance_dated_by_timestamp_is_read_in_its_ist_month(ap):
    """A stored advance from before the date rule: its month is the IST one."""
    memory, world = ap
    memory.vendor_payments.insert_one(
        payment(400, store_id=PUN, payment_id="OLD", payment_date="2026-09-30T20:00:00+00:00"))
    memory.period_locks.insert_one({"month": 9, "year": 2026})
    assert _apply(world, "OLD", "B1", 100).status_code == 200  # part-apply: the split row is October's


# --- review round 5 -----------------------------------------------------------


def test_a_form_made_advance_can_be_part_applied_and_a_retry_is_refused(ap):
    """An advance recorded through the form carries its request_id; the split
    row of a part-apply must not copy it (the unique index refused the copy:
    500)."""
    memory, world = ap
    adv = _pay(world, {"amount": 1000, "request_id": "form-adv-0001"}).json()["payment_id"]
    assert _apply(world, adv, "B1", 300, left=1000).status_code == 200
    split = memory.vendor_payments.find_one({"applied_from": adv})
    assert split["amount"] == 300 and "request_id" not in split
    assert memory.vendor_payments.find_one({"payment_id": adv})["request_id"] == "form-adv-0001"
    again = _apply(world, adv, "B1", 300, left=1000)
    assert again.status_code == 409
    assert memory.vendor_payments.count_documents({"applied_from": adv}) == 1
    assert _owed_and_advances(world) == (700, 700)


@pytest.mark.parametrize("change", [
    {"amount": 900}, {"bill_id": None}, {"payment_date": "2026-10-14"},
    {"tds_amount": 10, "tds_section": "194C_OTHER"}, {"reference": "UTR-OTHER"},
])
def test_the_same_request_id_with_other_details_is_refused(ap, change):
    """A stale form saved again with different details: never the earlier
    payment passed off as this one."""
    memory, world = ap
    body = {"amount": 1000, "bill_id": "B1", "request_id": "form-0001-same", "reference": "UTR-1"}
    assert _pay(world, body).status_code == 201
    other = _pay(world, {**body, **change})
    assert other.status_code == 409 and "different details" in other.json()["detail"]
    assert memory.vendor_payments.count_documents({}) == 1


def test_the_same_request_id_for_another_supplier_is_refused_not_a_crash(ap):
    memory, world = ap
    memory.vendor_bills.insert_one(bill("B2", vendor="V2", store_id=PUN))
    assert _pay(world, {"amount": 100, "request_id": "form-0001-twov"}).status_code == 201
    other = world.post("/vendors/V2/payments", ACCT_PUNE,
                       {"payment_date": "2026-10-15", "amount": 100, "request_id": "form-0001-twov"})
    assert other.status_code == 409
    assert memory.vendor_payments.count_documents({}) == 1


def test_a_replay_answers_only_a_caller_who_can_see_the_payment(ap):
    """Dhanbad's payment, replayed by a Pune login: the same 404 as any
    payment out of reach -- before anything else is compared."""
    from test_finance_ap_scope_round2 import _user

    memory, world = ap
    memory.vendor_bills.insert_one(bill("B2", store_id=DHN))
    acct_dhn = _user("ACCOUNTANT", DHN, (DHN,))
    body = {"payment_date": "2026-10-15", "amount": 100, "bill_id": "B2", "request_id": "form-0001-dhn"}
    assert world.post("/vendors/V1/payments", acct_dhn, body).status_code == 201
    assert world.post("/vendors/V1/payments", ACCT_PUNE, body).status_code == 404
    assert memory.vendor_payments.count_documents({}) == 1
