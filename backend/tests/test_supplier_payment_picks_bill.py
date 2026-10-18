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


def _apply(world, payment_id, bill_id, amount, user=ACCT_PUNE):
    return world.post(f"/vendors/V1/payments/{payment_id}/apply", user,
                      {"bill_id": bill_id, "amount": amount})


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
    assert _apply(world, adv, "B1", second).status_code == 409
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


@pytest.mark.parametrize("advance_day,bill_day", [("2026-09-15", "2026-10-01"), ("2026-10-02", "2026-09-01")])
def test_a_closed_month_stays_closed(ap, advance_day, bill_day):
    memory, world = ap
    memory.vendor_bills.update_one({}, {"$set": {"bill_date": bill_day}})
    memory.vendor_payments.insert_one(payment(400, store_id=PUN, payment_id="ADV", payment_date=advance_day))
    memory.period_locks.insert_one({"month": 9, "year": 2026})
    closed = _apply(world, "ADV", "B1", 100)
    assert closed.status_code == 423
    assert closed.json()["detail"] == "That month is closed - ask an admin to reopen it."
    assert memory.vendor_payments.count_documents({}) == 1
    memory.period_locks.delete_many({})
    assert _apply(world, "ADV", "B1", 100).status_code == 200


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
    assert _apply(world, adv, "B1", 300).status_code == 500
    # The rival's 50 stands; the failed 300 went back: 350 + 50 = 400 paid.
    assert memory.vendor_payments.find_one({"payment_id": adv})["amount"] == 350
    assert sum(p["amount"] for p in memory.vendor_payments.find({})) == 400
