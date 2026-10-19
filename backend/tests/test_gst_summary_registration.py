"""A04: cash set-off stays within a verified registration, or is refused.

Since the GST review of 2026-10-19 (M3) the summary cards ARE the GSTR-3B
returns of the caller's registrations (finance.gst_crosscheck.
gst_by_registration): a bill is credit on the return of the GSTIN it was
received on, by the bill's stored tax heads and written date. Credit no
return can place -- a bill with no company, a GSTIN that is no shop's, an
unproven legacy allocation, a date no month holds -- refuses the figures
(409) rather than leaving that credit out unseen."""
import asyncio
from datetime import datetime

import mongomock
import pytest
from fastapi import HTTPException

from api.routers.finance import gst

JH = "20ZZZZZ9999Z1Z9"
MH = "27ZZZZZ9999Z1Z9"
ADMIN = {"roles": ["ADMIN"]}


@pytest.fixture(autouse=True)
def _isolate_db():
    yield


@pytest.fixture
def world(monkeypatch):
    db = mongomock.MongoClient().db
    db.entities.insert_many([
        {"entity_id": "E1", "gstins": [{"gstin": JH}, {"gstin": MH}]},
        {"entity_id": "E2", "gstins": [{"gstin": "07ABCDE1234F1Z5"}]},
    ])
    db.stores.insert_many([
        {"store_id": "J1", "entity_id": "E1", "gstin": JH, "state": "Jharkhand"},
        {"store_id": "J2", "entity_id": "E1", "gstin": JH, "state": "Jharkhand"},
        {"store_id": "M1", "entity_id": "E1", "gstin": MH, "state": "Maharashtra"},
    ])
    monkeypatch.setattr(gst, "_get_db", lambda: db)
    return db


def sale(db, shop="M1", tax=500, **extra):
    db.orders.insert_one({"store_id": shop, "status": "DELIVERED",
                          "created_at": datetime(2026, 5, 12, 6), "tax_amount": tax,
                          "interstate": True, **extra})


def bill(db, shop="J1", tax=500, **extra):
    # As the bill doors store it: its tax heads (inter-state IGST here).
    db.vendor_bills.insert_one({"bill_id": "B1", "store_id": shop,
                               "recipient_entity_id": "E1", "recipient_gstin": JH,
                               "bill_date": "2026-05-12", "tax_amount": tax,
                               "igst_total": tax, "cgst_total": 0, "sgst_total": 0,
                               "status": "OUTSTANDING", **extra})


def summary(user=None):
    return asyncio.run(gst.get_gst_summary(month=5, year=2026, current_user=user or ADMIN))


@pytest.mark.parametrize("different_entity", [False, True])
def test_different_registrations_do_not_spend_each_others_credit(world, different_entity):
    if different_entity:
        world.entities.update_one({"entity_id": "E2"}, {"$set": {"gstins": [{"gstin": MH}]}})
        world.stores.update_one({"store_id": "M1"}, {"$set": {"entity_id": "E2"}})
    sale(world)
    bill(world)
    result = summary()
    assert (result["gst_collected"], result["gst_input_credit"]) == (500, 500)
    assert (result["net_gst_payable"], result["gst_input_credit_carried_forward"]) == (500, 500)


def test_two_shops_of_the_same_registration_can_set_off(world):
    sale(world, "J2")
    bill(world)
    assert summary()["net_gst_payable"] == 0
    assert summary()["gst_input_credit_carried_forward"] == 0


def test_reverse_charge_is_cash_even_with_credit_on_its_registration(world):
    sale(world, tax=500)
    bill(world, reverse_charge=True)
    result = summary()
    assert (result["reverse_charge_tax"], result["net_gst_payable"]) == (500, 1000)
    assert result["gst_input_credit_carried_forward"] == 500


def test_stamped_bill_registration_wins_over_current_shop_number(world):
    sale(world, "M1")
    bill(world, recipient_gstin=MH)
    assert summary()["net_gst_payable"] == 0


@pytest.mark.parametrize("evidence", ["shop", "receipt", "sole-registration"])
def test_unstamped_legacy_credit_uses_verified_evidence(world, evidence):
    sale(world, "J2")
    extra = {"recipient_gstin": None}
    if evidence == "receipt":
        extra.update(store_id=None, grn_id="G1")
        world.grns.insert_one({"grn_id": "G1", "store_id": "J1"})
    if evidence == "sole-registration":
        extra["store_id"] = None
        world.entities.update_one({"entity_id": "E1"}, {"$set": {"gstins": [{"gstin": JH}]}})
    bill(world, **extra)
    assert summary()["net_gst_payable"] == 0


@pytest.mark.parametrize("extra", [
    {"recipient_gstin": None, "store_id": None},
    {"recipient_gstin": "invalid"},
    {"recipient_gstin": "07ABCDE1234F1Z5"},
    {"recipient_gstin": None, "grn_id": "missing"},
    {"recipient_entity_id": "missing"},
])
def test_ambiguous_bill_refuses_a_calculated_summary(world, extra):
    sale(world)
    bill(world, **extra)
    with pytest.raises(HTTPException) as exc:
        summary()
    assert exc.value.status_code == 409
    assert "Accounts" in str(exc.value.detail)


def test_conflicting_receipt_and_shop_evidence_is_not_guessed(world):
    sale(world)
    world.grns.insert_one({"grn_id": "G1", "store_id": "M1"})
    bill(world, recipient_gstin=None, grn_id="G1")
    with pytest.raises(HTTPException) as exc:
        summary()
    assert exc.value.status_code == 409


def test_unresolved_output_registration_is_refused(world):
    sale(world, "missing")
    with pytest.raises(HTTPException) as exc:
        summary()
    assert exc.value.status_code == 409


def test_existing_entity_state_fallback_is_used(world):
    world.stores.update_one({"store_id": "M1"}, {"$set": {"gstin": ""}})
    sale(world)
    bill(world)
    assert summary()["net_gst_payable"] == 500


def test_a_shop_with_sales_and_no_registration_is_refused(world):
    world.entities.update_one({"entity_id": "E1"}, {"$set": {"gstins": [{"gstin": MH}]}})
    world.stores.update_one({"store_id": "J1"}, {"$set": {"gstin": ""}})
    sale(world, "J1")
    with pytest.raises(HTTPException) as exc:
        summary()
    assert exc.value.status_code == 409


def test_accountant_visibility_is_still_own_shop(world):
    """J2's GSTIN files one return with J1, which the accountant cannot see:
    his own shop's sales tax, and NO input-side figure (the whole GSTIN's,
    M4) -- never J1's credit, nor a payable short of it."""
    sale(world, "J2")
    bill(world)
    accountant = {"roles": ["ACCOUNTANT"], "active_store_id": "J2", "store_ids": ["J2"]}
    result = summary(accountant)
    assert result["gst_collected"] == 500
    assert (result["gst_input_credit"], result["net_gst_payable"]) == (None, None)
    assert summary({"roles": ["ACCOUNTANT"], "store_ids": []})["gst_collected"] == 0


def test_ineligible_and_out_of_period_bills_do_not_require_allocation(world):
    """A blocked bill claims no credit, so it needs no GSTIN to be placed on;
    another month's bill is that month's. Neither refuses this month."""
    bill(world, recipient_gstin=None, store_id=None, itc_blocked=True)
    bill(world, recipient_gstin=None, store_id=None, bill_date="2026-04-12")
    result = summary()
    assert (result["gst_input_credit"], result["gst_input_credit_excluded"]) == (0, 0)


def test_a_blocked_bill_on_a_return_is_held_back(world):
    # A registered supplier's bill (an unregistered one's "tax" is no GST).
    bill(world, itc_blocked=True, vendor_gstin="20AAAAA0000A1Z5")
    result = summary()
    assert (result["gst_input_credit"], result["gst_input_credit_excluded"]) == (0, 500)


def test_registration_read_failure_refuses_figures(world, monkeypatch):
    sale(world)
    original = world.get_collection

    def unavailable(name, *args, **kwargs):
        if name == "entities":
            raise RuntimeError("registration unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(world, "get_collection", unavailable)
    with pytest.raises(HTTPException) as exc:
        summary()
    assert exc.value.status_code == 503


def test_no_database_refuses_figures(monkeypatch):
    monkeypatch.setattr(gst, "_get_db", lambda: None)
    with pytest.raises(HTTPException) as exc:
        summary()
    assert exc.value.status_code == 503


def test_response_keys_and_empty_month_remain_unchanged(world):
    assert set(summary()) == {
        "month", "year", "gst_collected", "cgst", "sgst", "igst", "gst_input_credit",
        "gst_input_credit_excluded", "reverse_charge_tax", "gst_input_credit_carried_forward",
        "net_gst_payable", "gstr1_due_date", "gstr3b_due_date", "gstr1_filed", "gstr3b_filed",
    }


@pytest.mark.parametrize("extra", [
    # No company: GSTR-3B places it on no return (the Cross-Check lists it).
    {"recipient_entity_id": None, "recipient_gstin": None},
    # Tax with no stored heads: Table 4 sums the heads.
    "no heads",
])
def test_credit_no_return_places_refuses_the_figures(world, extra):
    sale(world, "J2")
    bill(world, **({} if extra == "no heads" else extra))
    if extra == "no heads":
        world.vendor_bills.update_one({}, {"$unset": {"igst_total": "", "cgst_total": "", "sgst_total": ""}})
    with pytest.raises(HTTPException) as exc:
        summary()
    assert exc.value.status_code == 409


@pytest.mark.parametrize("collection", ["orders", "vendor_bills", "stores", "grns", "customers"])
def test_source_read_failure_never_becomes_a_zero_summary(world, monkeypatch, collection):
    sale(world)
    world.grns.insert_one({"grn_id": "G1", "store_id": "J1"})
    bill(world, recipient_gstin=None, grn_id="G1")
    original = world.get_collection

    def unavailable(name, *args, **kwargs):
        if name == collection:
            raise RuntimeError("source unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(world, "get_collection", unavailable)
    with pytest.raises(HTTPException) as exc:
        summary()
    assert exc.value.status_code == 503
