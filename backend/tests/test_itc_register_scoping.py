"""
IMS 2.0 -- /finance/itc-register scoping + eligibility (transfer deemed-supply
alignment, #50 follow-up to #899)
==============================================================================
The register previously summed EVERY vendor_bills doc: it ignored its own
`entity_id` param for the bill set AND applied no eligibility filter, so
cancelled / 17(5)-blocked / not-yet-received bills showed as claimable ITC and
a mirror TRANSFER bill inflated every entity's register instead of only the
receiving entity's (which is how GSTR-3B Table 4 scopes it after #899).

These tests drive the endpoint with a fake db and assert:
  - ITC-ineligible bills (cancelled / blocked / not-received) are excluded;
  - entity_id scopes the bill set to that entity via recipient_entity_id, so a
    transfer bill lands only with the RECEIVING entity, never the sender's.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-itc-register")


def _matches(doc, query):
    for k, v in query.items():
        if doc.get(k) != v:
            return False
    return True


class _Coll:
    def __init__(self, docs):
        self._docs = docs

    def find(self, query=None, projection=None):
        query = query or {}
        return [dict(d) for d in self._docs if _matches(d, query)]

    def find_one(self, query=None, projection=None):
        for d in self._docs:
            if _matches(d, query or {}):
                return dict(d)
        return None


class _Db:
    def __init__(self, collections):
        self._c = collections

    def get_collection(self, name):
        return _Coll(self._c.get(name, []))


SUPER = {"user_id": "u1", "roles": ["SUPERADMIN"]}


def _run(db, entity_id=None, period=None):
    """Call the endpoint with a fake db, RESTORING finance._get_db afterwards.

    A bare `finance._get_db = ...` assignment leaks the fake into every later
    test in the same process (broke test_period_lock's expense-create in CI:
    the fake _Coll has no insert_one). Save/restore in a finally so this file
    can never pollute another."""
    import api.routers.finance as finance

    original = finance._get_db
    finance._get_db = lambda: db  # type: ignore[attr-defined]
    try:
        return asyncio.run(
            finance.itc_register(period=period, entity_id=entity_id, current_user=SUPER)
        )
    finally:
        finance._get_db = original  # type: ignore[attr-defined]


def test_ineligible_bills_excluded():
    bills = [
        {"bill_date": "2026-07-01", "taxable_amount": 1000, "tax_amount": 50,
         "place_of_supply": "20", "recipient_entity_id": "E1", "status": "BOOKED"},
        {"bill_date": "2026-07-02", "taxable_amount": 9999, "tax_amount": 500,
         "place_of_supply": "20", "recipient_entity_id": "E1", "status": "CANCELLED"},
        {"bill_date": "2026-07-03", "taxable_amount": 9999, "tax_amount": 500,
         "place_of_supply": "20", "recipient_entity_id": "E1", "itc_blocked": True},
        {"bill_date": "2026-07-04", "taxable_amount": 9999, "tax_amount": 500,
         "place_of_supply": "20", "recipient_entity_id": "E1", "received": False},
    ]
    db = _Db({"vendor_bills": bills, "entities": [{"entity_id": "E1", "state_code": "20"}]})
    out = _run(db)
    # only the single eligible bill (50) counts -- not the 3 x 500 ineligible.
    assert round(out["total_itc"], 2) == 50.0
    assert round(out["total_taxable"], 2) == 1000.0


def test_entity_scopes_bill_set_transfer_lands_with_receiver():
    # A mirror transfer bill records recipient_entity_id = the RECEIVING entity.
    bills = [
        {"bill_date": "2026-07-01", "taxable_amount": 1000, "tax_amount": 50,
         "place_of_supply": "20", "recipient_entity_id": "E1", "status": "BOOKED"},
        {"bill_date": "2026-07-05", "taxable_amount": 4000, "tax_amount": 460,
         "place_of_supply": "27", "recipient_entity_id": "E2",
         "source_transfer_id": "TR-1", "from_store_id": "S-E1", "to_store_id": "S-E2",
         "status": "BOOKED"},
    ]
    db = _Db({
        "vendor_bills": bills,
        "entities": [{"entity_id": "E1", "state_code": "20"},
                     {"entity_id": "E2", "state_code": "27"}],
    })
    # Sender entity E1: sees ONLY its own purchase bill, NOT the transfer ITC.
    out_e1 = _run(db, entity_id="E1")
    assert round(out_e1["total_itc"], 2) == 50.0
    # Receiver entity E2: gets exactly the transfer deemed-supply ITC (460).
    out_e2 = _run(db, entity_id="E2")
    assert round(out_e2["total_itc"], 2) == 460.0


def test_org_wide_counts_each_bill_once():
    bills = [
        {"bill_date": "2026-07-01", "taxable_amount": 1000, "tax_amount": 50,
         "place_of_supply": "20", "recipient_entity_id": "E1", "status": "BOOKED"},
        {"bill_date": "2026-07-05", "taxable_amount": 4000, "tax_amount": 460,
         "place_of_supply": "27", "recipient_entity_id": "E2",
         "source_transfer_id": "TR-1", "status": "BOOKED"},
    ]
    db = _Db({"vendor_bills": bills, "entities": [{"entity_id": "E1", "state_code": "20"}]})
    out = _run(db)  # no entity_id -> org-wide anchor
    # both eligible bills counted exactly once (50 + 460); the transfer bill is
    # one physical doc so there is no double-count.
    assert round(out["total_itc"], 2) == 510.0
