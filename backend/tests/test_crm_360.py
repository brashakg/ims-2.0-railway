"""
IMS 2.0 -- CRM 360 / lifecycle / prescriptions date-parsing regression
======================================================================
P1 LIVE BUG: the CRM "360-degree view" 500'd for ALL customers because the
date helpers called value.replace("Z","+00:00") -- a str method -- on values
that Mongo actually stores as native `datetime` objects (created_at /
order_date / issue_date) or that are None on legacy rows. The generic except
masked the resulting TypeError/AttributeError as a 500.

These tests seed the EXACT shapes that broke prod:
  - a customer whose created_at is a datetime object (not an ISO string)
  - an order whose order_date is a datetime object
  - a prescription with issue_date=None AND id=None (legacy Rx)
and assert GET 360 / lifecycle / prescriptions all succeed (validate against
their response models == what FastAPI returns to the client, i.e. NOT a 500)
with sane bodies. Plus the historically-OK no-orders path still works.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")

import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.routers import crm as crm_mod  # noqa: E402
from api.routers.crm import (  # noqa: E402
    Customer360Response,
    LifecyclePhase,
    PrescriptionWithStatusResponse,
)


_USER = {"user_id": "u1", "roles": ["ADMIN"], "active_role": "ADMIN"}


class _FakeCRMDB:
    """Stand-in for crm_mod.db (the _CRMDataAdapter). Returns the seeded
    customer / orders / prescriptions / interactions verbatim so we control
    the exact field SHAPES the helpers receive."""

    def __init__(self, customer=None, orders=None, prescriptions=None,
                 interactions=None):
        self._customer = customer
        self._orders = orders or []
        self._prescriptions = prescriptions or []
        self._interactions = interactions or []

    def query_customer(self, customer_id):
        return self._customer

    def query_customer_orders(self, customer_id):
        return list(self._orders)

    def query_customer_prescriptions(self, customer_id):
        return list(self._prescriptions)

    def query_customer_interactions(self, customer_id, limit=100):
        return list(self._interactions)[:limit]


def _patch_db(monkeypatch, **kwargs):
    fake = _FakeCRMDB(**kwargs)
    monkeypatch.setattr(crm_mod, "db", fake)
    # filter_docs_by_store is applied in the /prescriptions endpoint; make it
    # a pass-through so store-scoping does not drop our seeded rows.
    monkeypatch.setattr(crm_mod, "filter_docs_by_store", lambda docs, user: docs)
    return fake


# Customer whose created_at is a real datetime object (the prod shape).
_NOW = datetime.now(timezone.utc)
_CUSTOMER_DT = {
    "customer_id": "CUST-DT-1",
    "name": "Datetime Dave",
    "phone": "9876500001",
    "email": "dave@example.com",
    "created_at": _NOW - timedelta(days=400),  # datetime, NOT a string
}

# Order whose order_date is a datetime object.
_ORDER_DT = {
    "order_id": "ORD-DT-1",
    "customer_id": "CUST-DT-1",
    "order_date": _NOW - timedelta(days=200),  # datetime, NOT a string
    "total_amount": 4500.0,
    "status": "DELIVERED",
}

# Legacy prescription: id=None AND issue_date=None (would 500 pre-fix).
_RX_LEGACY = {
    "id": None,
    "prescription_id": "RX-LEGACY-1",
    "customer_id": "CUST-DT-1",
    "issue_date": None,
    "expiry_date": None,
    "sph_od": -1.25,
}


# ---------------------------------------------------------------------------
# GET /crm/customers/360/{id}
# ---------------------------------------------------------------------------

def test_customer_360_datetime_objects_no_500(monkeypatch):
    """The exact prod break: created_at datetime + order_date datetime +
    legacy null Rx -> must return a body that validates as Customer360Response
    (i.e. a 200, not a 500)."""
    _patch_db(
        monkeypatch,
        customer=_CUSTOMER_DT,
        orders=[_ORDER_DT],
        prescriptions=[_RX_LEGACY],
        interactions=[{"id": "i1"}],
    )

    body = asyncio.run(
        crm_mod.get_customer_360(customer_id="CUST-DT-1", current_user=_USER)
    )
    # Validating against the response_model is exactly what FastAPI does before
    # serialising -- a 500/422 in prod = a ValidationError here.
    model = Customer360Response(**body)

    assert model.id == "CUST-DT-1"
    assert model.name == "Datetime Dave"
    # created_at coerced from datetime -> ISO string.
    assert isinstance(model.created_at, str) and model.created_at
    assert model.stats.total_orders == 1
    assert model.stats.total_lifetime_value == 4500.0
    # legacy Rx surfaced with "unknown" status, id resolved from prescription_id.
    assert len(model.prescriptions) == 1
    rx = model.prescriptions[0]
    assert rx.renewal_status == "unknown"
    assert rx.id == "RX-LEGACY-1"
    assert rx.issue_date is None
    assert model.interactions_count == 1


def test_customer_360_no_orders_still_ok(monkeypatch):
    """The historically-working path (no orders) must still 200."""
    _patch_db(
        monkeypatch,
        customer=_CUSTOMER_DT,
        orders=[],
        prescriptions=[],
        interactions=[],
    )

    body = asyncio.run(
        crm_mod.get_customer_360(customer_id="CUST-DT-1", current_user=_USER)
    )
    model = Customer360Response(**body)
    assert model.stats.total_orders == 0
    assert model.stats.total_lifetime_value == 0
    assert model.prescriptions == []


def test_customer_360_string_created_at_still_ok(monkeypatch):
    """Backward-compat: an ISO-string created_at (older docs) still parses."""
    customer = dict(_CUSTOMER_DT)
    customer["created_at"] = (_NOW - timedelta(days=400)).isoformat() + "Z"
    _patch_db(monkeypatch, customer=customer, orders=[_ORDER_DT])

    body = asyncio.run(
        crm_mod.get_customer_360(customer_id="CUST-DT-1", current_user=_USER)
    )
    model = Customer360Response(**body)
    assert isinstance(model.created_at, str) and model.created_at


# ---------------------------------------------------------------------------
# GET /crm/customers/{id}/lifecycle
# ---------------------------------------------------------------------------

def test_lifecycle_with_orders_datetime_no_500(monkeypatch):
    """created_at + order_date datetimes -> lifecycle must NOT 500."""
    _patch_db(monkeypatch, customer=_CUSTOMER_DT, orders=[_ORDER_DT])

    body = asyncio.run(
        crm_mod.get_customer_lifecycle_phase(
            customer_id="CUST-DT-1", current_user=_USER
        )
    )
    model = LifecyclePhase(**body)
    # signup 400d + last order 200d ago, LTV 4500 -> at_risk (180<d<=365).
    assert model.phase == "at_risk"
    assert model.reason
    assert model.recommended_action


def test_lifecycle_no_orders_prospect(monkeypatch):
    """No orders -> prospect (the already-working path)."""
    _patch_db(monkeypatch, customer=_CUSTOMER_DT, orders=[])

    body = asyncio.run(
        crm_mod.get_customer_lifecycle_phase(
            customer_id="CUST-DT-1", current_user=_USER
        )
    )
    model = LifecyclePhase(**body)
    assert model.phase == "prospect"


def test_lifecycle_order_missing_date_no_500(monkeypatch):
    """An order with NO order_date must not crash the comparison chain."""
    order = {"customer_id": "CUST-DT-1", "total_amount": 1000.0}  # no order_date
    _patch_db(monkeypatch, customer=_CUSTOMER_DT, orders=[order])

    body = asyncio.run(
        crm_mod.get_customer_lifecycle_phase(
            customer_id="CUST-DT-1", current_user=_USER
        )
    )
    model = LifecyclePhase(**body)
    # Unknown recency -> falls through to "active" without crashing.
    assert model.phase in {"active", "vip"}


# ---------------------------------------------------------------------------
# GET /crm/customers/{id}/prescriptions
# ---------------------------------------------------------------------------

def test_prescriptions_legacy_null_no_500(monkeypatch):
    """Legacy Rx (issue_date=None, id=None) -> list returns with status
    'unknown', not a 500."""
    _patch_db(monkeypatch, customer=_CUSTOMER_DT, prescriptions=[_RX_LEGACY])

    body = asyncio.run(
        crm_mod.get_customer_prescriptions(
            customer_id="CUST-DT-1", current_user=_USER
        )
    )
    models = [PrescriptionWithStatusResponse(**rx) for rx in body]
    assert len(models) == 1
    assert models[0].renewal_status == "unknown"
    assert models[0].id == "RX-LEGACY-1"
    assert models[0].issue_date is None


def test_prescriptions_datetime_issue_and_expiry(monkeypatch):
    """issue_date/expiry_date as datetime objects parse + classify correctly."""
    rx = {
        "id": "RX-DT-1",
        "customer_id": "CUST-DT-1",
        "issue_date": _NOW - timedelta(days=30),       # datetime
        "expiry_date": _NOW + timedelta(days=15),       # datetime, upcoming
    }
    _patch_db(monkeypatch, customer=_CUSTOMER_DT, prescriptions=[rx])

    body = asyncio.run(
        crm_mod.get_customer_prescriptions(
            customer_id="CUST-DT-1", current_user=_USER
        )
    )
    models = [PrescriptionWithStatusResponse(**r) for r in body]
    assert len(models) == 1
    # expiry 15 days out -> "upcoming"
    assert models[0].renewal_status == "upcoming"
    assert isinstance(models[0].issue_date, str)
    assert models[0].days_until_renewal is not None


def test_prescriptions_expired(monkeypatch):
    """A past expiry_date classifies as expired."""
    rx = {
        "id": "RX-EXP-1",
        "customer_id": "CUST-DT-1",
        "issue_date": _NOW - timedelta(days=400),
        "expiry_date": _NOW - timedelta(days=10),  # already expired
    }
    _patch_db(monkeypatch, customer=_CUSTOMER_DT, prescriptions=[rx])

    body = asyncio.run(
        crm_mod.get_customer_prescriptions(
            customer_id="CUST-DT-1", current_user=_USER
        )
    )
    models = [PrescriptionWithStatusResponse(**r) for r in body]
    assert models[0].renewal_status == "expired"


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------

def test_to_dt_handles_all_shapes():
    assert crm_mod._to_dt(None) is None
    assert crm_mod._to_dt("") is None
    assert crm_mod._to_dt("not-a-date") is None
    # naive datetime -> gets UTC attached
    naive = datetime(2024, 1, 1, 12, 0, 0)
    out = crm_mod._to_dt(naive)
    assert out is not None and out.tzinfo is not None
    # aware datetime -> returned as-is
    aware = datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert crm_mod._to_dt(aware) == aware
    # ISO string with Z
    assert crm_mod._to_dt("2024-01-01T00:00:00Z") is not None


def test_to_iso_handles_all_shapes():
    assert crm_mod._to_iso(None) is None
    assert crm_mod._to_iso("2024-01-01") == "2024-01-01"
    dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert crm_mod._to_iso(dt) == dt.isoformat()
