"""
L2-INTEGRATION-F01 - a transfer never claims a courier booking it did not make
==============================================================================
Audit reproduction: an APPROVED transfer with nothing packed or shipped, courier
dispatch off and no courier credentials. POST /{id}/create-shiprocket-shipment
answered 200 "Shiprocket shipment created" with a made-up SHP_/AWB pair, a
second press made up ANOTHER pair and overwrote the first, and GET
/{id}/tracking then reported PICKUP_SCHEDULED for a box that never left. The
same invention sat behind /ship?create_shiprocket=true (the box shipped with a
tracking number nobody typed and no courier issued) and behind
create_shiprocket_shipment on create (a made-up SR_ order id).

IMS has no courier booking for a transfer box: the Shiprocket adapter
(services/shiprocket.py) books customer orders only. So every one of those doors
now REFUSES, writes nothing, and the box ships on the courier and tracking no.
staff type at Ship. Tracking reports only IMS's own record of the box.

Same harness as test_idor_transfers.py (its fakes are imported): the endpoint
functions run directly over an in-test collection. No Mongo, no network.
"""

from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.routers import transfers  # noqa: E402
from test_idor_transfers import (  # noqa: E402,F401  (coll is a fixture)
    MGR_B,
    MGR_C,
    _seed_box,
    _seed_transfer,
    coll,
)

_COURIER_FIELDS = (
    "tracking_number",
    "tracking_url",
    "courier_name",
    "shiprocket_shipment_id",
    "shiprocket_order_id",
)


def _courier_state(doc):
    return {k: doc.get(k) for k in _COURIER_FIELDS}


def _refused(coro):
    """The door answered 409 TRANSFER_COURIER_NOT_CONNECTED - and said so in
    words staff can act on."""
    with pytest.raises(HTTPException) as exc:
        asyncio.run(coro)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "TRANSFER_COURIER_NOT_CONNECTED"
    assert "tracking no" in exc.value.detail["message"]
    return exc.value


def test_courier_door_books_nothing_and_a_retry_changes_nothing(coll):
    """The audit's exact press, twice: approved, nothing packed, no courier."""
    _seed_transfer("t1", "B", "C", "approved")
    before = _courier_state(coll.docs["t1"])
    assert before == dict.fromkeys(_COURIER_FIELDS)  # nothing on it to begin with

    _refused(transfers.create_shiprocket_shipment_for_transfer("t1", current_user=MGR_B))
    assert _courier_state(coll.docs["t1"]) == before  # no AWB, no shipment id
    _refused(
        transfers.create_shiprocket_shipment_for_transfer(
            "t1", courier_code="Bluedart", current_user=MGR_B
        )
    )
    assert _courier_state(coll.docs["t1"]) == before
    assert coll.docs["t1"]["status"] == "approved"


def test_courier_door_never_replaces_a_tracking_number_staff_typed(coll):
    """The replay half of the finding: a real, hand-typed tracking no. was
    overwritten by a made-up one."""
    _seed_transfer(
        "t2", "B", "C", "packed", tracking_number="D123456789", courier_name="DTDC"
    )
    _refused(transfers.create_shiprocket_shipment_for_transfer("t2", current_user=MGR_B))
    assert coll.docs["t2"]["tracking_number"] == "D123456789"
    assert coll.docs["t2"]["courier_name"] == "DTDC"
    assert coll.docs["t2"].get("shiprocket_shipment_id") is None


@pytest.mark.parametrize(
    "typed", [{}, {"tracking_number": "D123456789", "courier_name": "DTDC"}]
)
def test_ship_never_invents_a_tracking_number(coll, typed):
    """/ship?create_shiprocket=true shipped the box on an invented AWB. It is
    refused BEFORE anything moves - also when a courier was typed, because the
    caller asked for a booking that will not happen."""
    sids = _seed_box(coll, "t3", "B", "C", "approved")
    _refused(
        transfers.ship_transfer("t3", create_shiprocket=True, current_user=MGR_B, **typed)
    )
    doc = coll.docs["t3"]
    assert doc["status"] == "approved" and doc.get("shipped_at") is None
    assert _courier_state(doc) == dict.fromkeys(_COURIER_FIELDS)
    units = list(coll.mdb["stock_units"].find({"stock_id": {"$in": sids}}))
    assert [u["status"] for u in units] == ["AVAILABLE", "AVAILABLE"]  # nothing left the shop


def test_ship_with_a_typed_courier_still_ships(coll):
    """The door staff actually use is untouched."""
    _seed_box(coll, "t4", "B", "C", "approved")
    res = asyncio.run(
        transfers.ship_transfer(
            "t4", tracking_number="D123456789", courier_name="DTDC", current_user=MGR_B
        )
    )
    assert res["transfer"]["status"] == transfers.TransferStatus.IN_TRANSIT
    assert coll.docs["t4"]["tracking_number"] == "D123456789"
    assert coll.docs["t4"].get("shiprocket_shipment_id") is None


def test_create_never_invents_a_courier_order(coll):
    """create_shiprocket_shipment on create stamped a made-up SR_ order id.
    Refused before a transfer is made."""
    body = transfers.TransferInput(
        transfer_type="store_to_store",
        from_location_id="B",
        from_location_name="Store B",
        to_location_id="C",
        to_location_name="Store C",
        items=[],
        create_shiprocket_shipment=True,
    )
    _refused(transfers.create_transfer(body, current_user=MGR_B))
    assert coll.docs == {}


def test_tracking_reports_only_what_ims_recorded(coll):
    """No courier feeds a transfer, so tracking carries no courier scans: an
    unshipped box showed PICKUP_SCHEDULED, a shipped one a 'Distribution Hub'
    that exists nowhere."""
    _seed_transfer(
        "t5", "B", "C", "approved", tracking_number="D123456789", courier_name="DTDC",
        status_history=[
            {"status": "approved", "timestamp": "2026-06-10T09:05:00", "user_id": "u1",
             "user_name": "mgr", "notes": "Approved"},
        ],
    )
    for user in (MGR_B, MGR_C):  # both shops may look
        out = asyncio.run(transfers.get_transfer_tracking("t5", current_user=user))
        assert out["tracking_number"] == "D123456789" and out["courier"] == "DTDC"
        assert out["current_status"] == "approved"
        assert out["source"] == "internal"
        assert out["provider_events"] == []
        assert out["tracking_history"] == [
            {"status": "approved", "timestamp": "2026-06-10T09:05:00",
             "notes": "Approved", "source": "internal"},
        ]


def test_tracking_of_a_shipped_box_is_its_own_history(coll):
    _seed_box(coll, "t6", "B", "C", "approved")
    asyncio.run(
        transfers.ship_transfer(
            "t6", tracking_number="D123456789", courier_name="DTDC", current_user=MGR_B
        )
    )
    out = asyncio.run(transfers.get_transfer_tracking("t6", current_user=MGR_C))
    assert out["current_status"] == transfers.TransferStatus.IN_TRANSIT
    assert out["provider_events"] == []
    statuses = [e["status"] for e in out["tracking_history"]]
    assert statuses == [transfers.TransferStatus.IN_TRANSIT]
    assert {e["source"] for e in out["tracking_history"]} == {"internal"}
    assert "Distribution Hub" not in str(out) and "PICKUP_SCHEDULED" not in str(out)
