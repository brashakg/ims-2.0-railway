"""
IMS 2.0 - FIN-3: Inter-GSTIN transfer mirror purchase
======================================================
Tests for _book_mirror_purchase and helpers in transfers.py.

All tests are pure (no live DB).  The DB-backed helpers (_get_db,
collection access) are patched with lightweight in-memory stubs so
the test suite stays green without a real mongod.

Coverage:
  * _tax_split: intra-state (CGST+SGST) and inter-state (IGST).
  * _book_mirror_purchase skips intra-entity transfers (no write).
  * _book_mirror_purchase writes a bill for inter-entity transfers.
  * Idempotency: second call with existing bill -> no duplicate insert.
  * Fail-soft: DB error on insert -> no exception propagates.
  * GST classification: from_state == to_state -> intra; else inter.
  * Zero-value transfer: bill written with taxable=0, tax=0.
"""

from __future__ import annotations

import os
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers.transfers import (  # noqa: E402
    _tax_split,
    _book_mirror_purchase,
    _store_state_code,
    _store_entity,
    _entity_gstin_for_state,
)


# ============================================================================
# Pure helper tests
# ============================================================================


def test_tax_split_intrastate():
    cgst, sgst, igst = _tax_split(100.0, interstate=False)
    assert igst == 0.0
    assert cgst + sgst == 100.0
    assert cgst == 50.0
    assert sgst == 50.0


def test_tax_split_intrastate_odd_paise():
    # tax=9.01: half=4.51, sgst=4.50, sum=9.01 exact
    cgst, sgst, igst = _tax_split(9.01, interstate=False)
    assert igst == 0.0
    assert round(cgst + sgst, 2) == 9.01


def test_tax_split_interstate():
    cgst, sgst, igst = _tax_split(180.0, interstate=True)
    assert cgst == 0.0
    assert sgst == 0.0
    assert igst == 180.0


def test_tax_split_zero():
    cgst, sgst, igst = _tax_split(0, interstate=False)
    assert cgst == sgst == igst == 0.0


# ============================================================================
# Stubs for DB-backed helpers (_store_state_code, _store_entity, etc.)
# ============================================================================


def _make_db(stores=None, entities=None, bills=None):
    """Build a minimal fake DB object for the transfer helpers."""
    stores = stores or {}
    entities = entities or {}
    bills_store = list(bills or [])

    def _find_one(coll_name, query, projection=None):
        if coll_name == "stores":
            sid = query.get("store_id")
            return stores.get(sid)
        if coll_name == "entities":
            eid = query.get("entity_id")
            return entities.get(eid)
        if coll_name == "vendor_bills":
            tid = (query or {}).get("source_transfer_id")
            for b in bills_store:
                if b.get("source_transfer_id") == tid:
                    return b
            return None
        return None

    class FakeColl:
        def __init__(self, name):
            self._name = name

        def find_one(self, q, proj=None):
            return _find_one(self._name, q, proj)

        def insert_one(self, doc):
            bills_store.append(dict(doc))

    class FakeDB:
        def get_collection(self, name):
            return FakeColl(name)

    return FakeDB(), bills_store


# ============================================================================
# _book_mirror_purchase: skip intra-entity
# ============================================================================


def test_mirror_skips_same_entity():
    """Same entity on both sides -> no bill written."""
    db, bills = _make_db(
        stores={
            "store_a": {"store_id": "store_a", "entity_id": "ent_1", "state_code": "20"},
            "store_b": {"store_id": "store_b", "entity_id": "ent_1", "state_code": "20"},
        }
    )
    transfer = {
        "id": "trf_001",
        "transfer_number": "TRF-202606-001",
        "from_location_id": "store_a",
        "to_location_id": "store_b",
        "from_location_name": "Store A",
        "to_location_name": "Store B",
        "total_value": 1000.0,
        "items": [],
        "completed_at": "2026-06-05T12:00:00",
    }
    with patch("api.routers.transfers._get_db", return_value=db):
        _book_mirror_purchase(transfer)

    assert bills == [], "No bill should be written for same-entity transfer"


def test_mirror_skips_missing_entity():
    """Store with no entity_id set -> skip."""
    db, bills = _make_db(
        stores={
            "store_a": {"store_id": "store_a", "state_code": "20"},  # no entity_id
            "store_b": {"store_id": "store_b", "entity_id": "ent_2", "state_code": "27"},
        }
    )
    transfer = {
        "id": "trf_002",
        "from_location_id": "store_a",
        "to_location_id": "store_b",
        "total_value": 500.0,
        "items": [],
    }
    with patch("api.routers.transfers._get_db", return_value=db):
        _book_mirror_purchase(transfer)
    assert bills == []


# ============================================================================
# _book_mirror_purchase: writes bill for inter-entity transfer
# ============================================================================


def test_mirror_writes_bill_inter_entity_intrastate():
    """Inter-entity, same state -> CGST+SGST split; bill inserted once."""
    db, bills = _make_db(
        stores={
            "store_a": {"store_id": "store_a", "entity_id": "ent_1", "state_code": "20"},
            "store_b": {"store_id": "store_b", "entity_id": "ent_2", "state_code": "20"},
        },
        entities={
            "ent_1": {"entity_id": "ent_1", "gstins": [{"state_code": "20", "gstin": "20AAPFU0939F1ZV"}]},
            "ent_2": {"entity_id": "ent_2", "gstins": [{"state_code": "20", "gstin": "20BBGAA1234J1ZV"}]},
        },
    )
    transfer = {
        "id": "trf_003",
        "transfer_number": "TRF-202606-003",
        "from_location_id": "store_a",
        "to_location_id": "store_b",
        "from_location_name": "Ranchi Store",
        "to_location_name": "Dhanbad Store",
        "total_value": 10000.0,
        "items": [],
        "completed_at": "2026-06-05T18:00:00",
    }
    with patch("api.routers.transfers._get_db", return_value=db):
        _book_mirror_purchase(transfer)

    assert len(bills) == 1
    b = bills[0]
    assert b["source_transfer_id"] == "trf_003"
    assert b["taxable_amount"] == 10000.0
    # NEW-GST-TRANSFER-RATES: no per-item cost data -> aggregate fallback taxed
    # at the app-wide resolve_gst_rate default (5% optical-dominant), replacing
    # the old arbitrary flat 18%.
    assert b["tax_amount"] == round(10000.0 * 0.05, 2)
    # Intra-state: CGST + SGST == tax; IGST == 0
    assert b["igst_total"] == 0.0
    assert round(b["cgst_total"] + b["sgst_total"], 2) == b["tax_amount"]
    assert b["interstate"] is False
    assert b["itc_eligible"] is True
    assert b["auto_generated"] is True
    assert b["status"] == "OUTSTANDING"
    assert b["vendor_gstin"] == "20AAPFU0939F1ZV"  # from_entity's GSTIN
    assert b["vendor_id"] == "ent_1"
    assert b["entity_id"] == "ent_2"
    # NEW-GST-TRANSFER-OUTWARD: fields the sender-side GSTR-1/3B and the
    # receiver-side ITC scoping key on.
    assert b["recipient_entity_id"] == "ent_2"
    assert b["from_store_id"] == "store_a"
    assert b["to_store_id"] == "store_b"


def test_mirror_writes_bill_inter_entity_interstate():
    """Inter-entity, different states -> IGST; CGST + SGST == 0."""
    db, bills = _make_db(
        stores={
            "jh_store": {"store_id": "jh_store", "entity_id": "ent_jh", "state_code": "20"},
            "mh_store": {"store_id": "mh_store", "entity_id": "ent_mh", "state_code": "27"},
        },
        entities={
            "ent_jh": {"entity_id": "ent_jh", "gstins": [{"state_code": "20", "gstin": "20ZZZZZ1234A1Z5"}]},
            "ent_mh": {"entity_id": "ent_mh", "gstins": [{"state_code": "27", "gstin": "27AAAAA5678B1Z3"}]},
        },
    )
    transfer = {
        "id": "trf_004",
        "transfer_number": "TRF-202606-004",
        "from_location_id": "jh_store",
        "to_location_id": "mh_store",
        "from_location_name": "Ranchi (JH)",
        "to_location_name": "Pune (MH)",
        "total_value": 5000.0,
        "items": [],
        "completed_at": "2026-06-05T20:00:00",
    }
    with patch("api.routers.transfers._get_db", return_value=db):
        _book_mirror_purchase(transfer)

    assert len(bills) == 1
    b = bills[0]
    assert b["interstate"] is True
    # Aggregate fallback (no per-item cost data) -> 5% app default, all IGST.
    assert b["igst_total"] == round(5000.0 * 0.05, 2)
    assert b["cgst_total"] == 0.0
    assert b["sgst_total"] == 0.0
    # place_of_supply = sending (JH) store's state code
    assert b["place_of_supply"] == "20"
    # supply_place_recipient = receiving (MH) store's state code (the sender's
    # outward place of supply reported on its GSTR-1 B2B row).
    assert b["supply_place_recipient"] == "27"


# ============================================================================
# Idempotency
# ============================================================================


def test_mirror_idempotent_no_duplicate():
    """If a bill for this transfer_id already exists, do not insert again."""
    existing_bill = {"source_transfer_id": "trf_005", "bill_id": "mbill_existing"}
    db, bills = _make_db(
        stores={
            "store_a": {"store_id": "store_a", "entity_id": "ent_1", "state_code": "20"},
            "store_b": {"store_id": "store_b", "entity_id": "ent_2", "state_code": "20"},
        },
        entities={
            "ent_1": {"entity_id": "ent_1", "gstins": [{"state_code": "20", "gstin": "20AAPFU0939F1ZV"}]},
            "ent_2": {"entity_id": "ent_2", "gstins": [{"state_code": "20", "gstin": "20BBGAA1234J1ZV"}]},
        },
        bills=[existing_bill],
    )
    transfer = {
        "id": "trf_005",
        "from_location_id": "store_a",
        "to_location_id": "store_b",
        "total_value": 1000.0,
        "items": [],
    }
    with patch("api.routers.transfers._get_db", return_value=db):
        _book_mirror_purchase(transfer)

    # Still only the original bill (no second insert).
    assert len(bills) == 1
    assert bills[0]["bill_id"] == "mbill_existing"


# ============================================================================
# Fail-soft: DB down
# ============================================================================


def test_mirror_fail_soft_db_none():
    """_get_db() returns None -> silently skip, no exception."""
    transfer = {
        "id": "trf_006",
        "from_location_id": "store_a",
        "to_location_id": "store_b",
        "total_value": 2000.0,
        "items": [],
    }
    with patch("api.routers.transfers._get_db", return_value=None):
        # Should not raise.
        _book_mirror_purchase(transfer)


def test_mirror_fail_soft_insert_error():
    """DB insert explodes -> exception swallowed; no propagation."""
    db, bills = _make_db(
        stores={
            "store_a": {"store_id": "store_a", "entity_id": "ent_1", "state_code": "20"},
            "store_b": {"store_id": "store_b", "entity_id": "ent_2", "state_code": "20"},
        },
        entities={
            "ent_1": {"entity_id": "ent_1", "gstins": [{"state_code": "20", "gstin": "20AAPFU0939F1ZV"}]},
            "ent_2": {"entity_id": "ent_2", "gstins": [{"state_code": "20", "gstin": "20BBGAA1234J1ZV"}]},
        },
    )

    class BombColl:
        def find_one(self, *a, **kw):
            return None  # no existing bill -> proceed

        def insert_one(self, doc):
            raise RuntimeError("DB exploded")

    class BombDB:
        def get_collection(self, name):
            return BombColl()

    transfer = {
        "id": "trf_007",
        "from_location_id": "store_a",
        "to_location_id": "store_b",
        "total_value": 1000.0,
        "items": [],
    }
    with patch("api.routers.transfers._get_db", return_value=BombDB()):
        # Should not raise -- fail-soft.
        _book_mirror_purchase(transfer)


# ============================================================================
# Zero-value transfer
# ============================================================================


def test_mirror_zero_value():
    """Transfer with total_value=0 and no cost-bearing items -> bill with taxable=0."""
    db, bills = _make_db(
        stores={
            "store_a": {"store_id": "store_a", "entity_id": "ent_1", "state_code": "20"},
            "store_b": {"store_id": "store_b", "entity_id": "ent_2", "state_code": "20"},
        },
        entities={
            "ent_1": {"entity_id": "ent_1", "gstins": []},
            "ent_2": {"entity_id": "ent_2", "gstins": []},
        },
    )
    transfer = {
        "id": "trf_008",
        "from_location_id": "store_a",
        "to_location_id": "store_b",
        "total_value": 0,
        "items": [{"quantity_requested": 2, "unit_cost": None}],
    }
    with patch("api.routers.transfers._get_db", return_value=db):
        _book_mirror_purchase(transfer)

    assert len(bills) == 1
    b = bills[0]
    assert b["taxable_amount"] == 0.0
    assert b["tax_amount"] == 0.0


# ============================================================================
# Value computed from items when total_value is zero
# ============================================================================


def test_mirror_value_from_items():
    """When total_value=0 but items have unit_cost, value is summed from items."""
    db, bills = _make_db(
        stores={
            "s1": {"store_id": "s1", "entity_id": "e1", "state_code": "20"},
            "s2": {"store_id": "s2", "entity_id": "e2", "state_code": "20"},
        },
        entities={
            "e1": {"entity_id": "e1", "gstins": [{"state_code": "20", "gstin": "20AAPFU0939F1ZV"}]},
            "e2": {"entity_id": "e2", "gstins": [{"state_code": "20", "gstin": "20BBGAA1234J1ZV"}]},
        },
    )
    transfer = {
        "id": "trf_009",
        "from_location_id": "s1",
        "to_location_id": "s2",
        "total_value": 0,
        "items": [
            {"quantity_received": 3, "unit_cost": 500.0},
            {"quantity_received": 2, "unit_cost": 250.0},
        ],
    }
    with patch("api.routers.transfers._get_db", return_value=db):
        _book_mirror_purchase(transfer)

    # 3*500 + 2*250 = 2000
    assert len(bills) == 1
    b = bills[0]
    assert b["taxable_amount"] == 2000.0
    # NEW-GST-TRANSFER-RATES: per-line path. Products are unknown to the fake
    # master -> each line resolves via the app-wide 5% optical-dominant default.
    assert b["tax_amount"] == round(2000.0 * 0.05, 2)
    # Per-line detail present, and header == sum(lines) to the paisa.
    assert len(b["lines"]) == 2
    assert round(sum(ln["taxable"] for ln in b["lines"]), 2) == b["taxable_amount"]
    assert (
        round(
            sum(ln["cgst"] + ln["sgst"] + ln["igst"] for ln in b["lines"]), 2
        )
        == b["tax_amount"]
    )


def test_mirror_writes_bill_same_entity_interstate():
    """NEW-GST-TRANSFER-IGST: SAME entity but DIFFERENT states must book an IGST
    mirror bill (deemed supply between distinct GSTINs of one PAN). Previously this
    returned early (entity-only gate) and booked NO IGST -> GST understated."""
    db, bills = _make_db(
        stores={
            "jh": {"store_id": "jh", "entity_id": "ent_1", "state_code": "20"},
            "mh": {"store_id": "mh", "entity_id": "ent_1", "state_code": "27"},
        },
        entities={
            "ent_1": {"entity_id": "ent_1", "gstins": [
                {"state_code": "20", "gstin": "20AAPFU0939F1ZV"},
                {"state_code": "27", "gstin": "27AAPFU0939F1ZX"},
            ]},
        },
    )
    transfer = {
        "id": "trf_se_is",
        "transfer_number": "TRF-202606-099",
        "from_location_id": "jh",
        "to_location_id": "mh",
        "from_location_name": "Ranchi",
        "to_location_name": "Pune",
        "total_value": 10000.0,
        "items": [],
        "completed_at": "2026-06-05T18:00:00",
    }
    with patch("api.routers.transfers._get_db", return_value=db):
        _book_mirror_purchase(transfer)

    assert len(bills) == 1, "same-entity interstate transfer must book an IGST mirror bill"
    b = bills[0]
    assert b["interstate"] is True
    # Aggregate fallback (no per-item cost data) -> 5% app default, all IGST.
    assert b["igst_total"] == round(10000.0 * 0.05, 2)
    assert round(b["cgst_total"] + b["sgst_total"], 2) == 0.0
    assert b["vendor_id"] == "ent_1" and b["entity_id"] == "ent_1"
