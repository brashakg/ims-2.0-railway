"""A10: the return header and portal file identify the GSTIN receiving ITC.

The actual report/export readers run against an in-memory database. A shop's
raw GSTIN is not necessarily the registration held by its legal entity.
"""

import asyncio
import importlib

import mongomock
import pytest


gstr3b = importlib.import_module("api.routers.reports.gstr3b")
BUY_JH = "20ZZZZZ9999Z1Z9"
BUY_MH = "27ZZZZZ9999Z1Z9"
OTHER_MH = "27ABCDE1234F1Z5"
MONTH = "2026-05"
ADMIN = {"user_id": "audit-test", "roles": ["ADMIN"]}


@pytest.fixture(autouse=True)
def _isolate_db():
    """Use each test's mongomock DB, without the shared app-startup fixture."""
    yield


@pytest.fixture
def db(monkeypatch):
    client = mongomock.MongoClient()
    database = client["gstr3b_registration_test"]
    database["entities"].insert_one(
        {
            "entity_id": "E1",
            "gstins": [
                {"gstin": BUY_JH, "state_code": "20", "is_primary": True},
                {"gstin": BUY_MH, "state_code": "27"},
            ],
        }
    )
    # Different amounts expose either cross-registration leakage or a header
    # that identifies the primary registration while the ITC belongs to Pune.
    for gstin, tax in ((BUY_JH, 40.0), (BUY_MH, 90.0)):
        database["vendor_bills"].insert_one(
            {
                "bill_id": "bill-" + gstin,
                "bill_number": "bill-" + gstin,
                "recipient_entity_id": "E1",
                "recipient_gstin": gstin,
                "vendor_gstin": OTHER_MH,
                "bill_date": "2026-05-12",
                "status": "OUTSTANDING",
                "itc_eligible": True,
                "taxable_amount": 1000.0,
                "igst_total": tax,
                "cgst_total": 0.0,
                "sgst_total": 0.0,
            }
        )
    monkeypatch.setattr(gstr3b, "_get_raw_db", lambda: database)
    yield database
    client.close()


def _report_and_export(store_id):
    report = asyncio.run(
        gstr3b.gstr3b_report(month=MONTH, store_id=store_id, current_user=ADMIN)
    )
    portal = asyncio.run(
        gstr3b.gstr3b_gstn_json(
            month=MONTH,
            year=None,
            store_id=store_id,
            entity_id=None,
            current_user=ADMIN,
        )
    )
    return report, portal


@pytest.mark.parametrize("raw_gstin", [None, "", "not-a-gstin", OTHER_MH])
def test_entity_state_fallback_labels_the_credit_on_report_and_export(db, raw_gstin):
    db["stores"].insert_one(
        {"store_id": "PUNE", "entity_id": "E1", "state_code": "27", "gstin": raw_gstin}
    )

    report, portal = _report_and_export("PUNE")

    assert report["gstin"] == portal["gstin"] == BUY_MH
    assert report["itcAvailable"]["integratedTax"] == 90.0
    assert portal["itc_elg"]["itc_net"]["iamt"] == 90.0
    assert portal["ret_period"] == "052026"


def test_held_own_gstin_wins_over_stale_shop_state_and_primary(db):
    db["stores"].insert_one(
        {"store_id": "PUNE", "entity_id": "E1", "state_code": "20", "gstin": BUY_MH}
    )

    report, portal = _report_and_export("PUNE")

    assert report["gstin"] == portal["gstin"] == BUY_MH
    assert report["itcAvailable"]["integratedTax"] == 90.0
    assert portal["itc_elg"]["itc_net"]["iamt"] == 90.0


def test_two_registrations_keep_their_own_header_and_credit(db):
    db["stores"].insert_many(
        [
            {"store_id": "JH", "entity_id": "E1", "state_code": "20", "gstin": BUY_JH},
            {"store_id": "PUNE", "entity_id": "E1", "state": "Maharashtra", "gstin": ""},
        ]
    )

    for store_id, gstin, tax in (("JH", BUY_JH, 40.0), ("PUNE", BUY_MH, 90.0)):
        report, portal = _report_and_export(store_id)
        assert report["gstin"] == portal["gstin"] == gstin
        assert report["itcAvailable"]["integratedTax"] == tax
        assert portal["itc_elg"]["itc_net"]["iamt"] == tax


def test_unresolved_registration_keeps_empty_header_without_exposing_raw_number(db):
    db["stores"].insert_one(
        {"store_id": "BLR", "entity_id": "E1", "state_code": "29", "gstin": OTHER_MH}
    )

    report, portal = _report_and_export("BLR")

    assert report["gstin"] == portal["gstin"] == ""
    assert sum(report["itcAvailable"].values()) == 0.0
    assert sum(portal["itc_elg"]["itc_net"].values()) == 0.0


def test_registration_lookup_failure_does_not_fall_back_to_raw_number(db, monkeypatch):
    db["stores"].insert_one(
        {"store_id": "PUNE", "entity_id": "E1", "state_code": "27", "gstin": OTHER_MH}
    )
    gst_itc = importlib.import_module("api.routers.reports.gst_itc")

    def unavailable(_database):
        raise RuntimeError("registration lookup unavailable")

    monkeypatch.setattr(gst_itc, "shop_gstins", unavailable)
    report, portal = _report_and_export("PUNE")

    assert report["gstin"] == portal["gstin"] == ""


def test_no_database_preserves_empty_report_and_export(monkeypatch):
    monkeypatch.setattr(gstr3b, "_get_raw_db", lambda: None)

    report, portal = _report_and_export("PUNE")

    assert report["gstin"] == portal["gstin"] == ""
    assert sum(report["itcAvailable"].values()) == 0.0
    assert sum(portal["itc_elg"]["itc_net"].values()) == 0.0
