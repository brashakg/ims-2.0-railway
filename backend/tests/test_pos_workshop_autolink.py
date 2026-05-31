"""
IMS 2.0 — POS operational-wins: workshop auto-link + delivery-date bound
=========================================================================
Owner-approved "operational wins" pass on the (mature) POS module:

  * Win 2 — a CONFIRMED fitting order is GUARANTEED a workshop/lab job.
    The POS client already creates one on the happy path (Phase 6.8); the
    backend `_ensure_workshop_job_for_order` is the idempotent, fail-soft
    SAFETY NET for (a) that client call failing and (b) non-POS confirm paths.
    It must NOT duplicate a job that already exists, and must stamp the reverse
    `workshop_job_id` pointer on the order (the link was previously one-way).
  * Win 3 — a delivery_date can't be scheduled absurdly far in the future.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-pos-opwins")


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeWorkshopRepo:
    def __init__(self):
        self.jobs = []

    def find_by_order(self, order_id):
        return [j for j in self.jobs if j.get("order_id") == order_id]

    def create(self, data):
        doc = dict(data)
        doc["job_id"] = f"JID-{len(self.jobs) + 1}"
        self.jobs.append(doc)
        return doc


class FakeOrderRepo:
    def __init__(self):
        self.updates = []

    def update(self, order_id, data):
        self.updates.append((order_id, data))
        return True


@pytest.fixture()
def patched_repos(monkeypatch):
    import api.dependencies as deps
    import api.routers.orders as orders

    wrepo = FakeWorkshopRepo()
    orepo = FakeOrderRepo()
    monkeypatch.setattr(deps, "get_workshop_repository", lambda: wrepo)
    monkeypatch.setattr(orders, "get_order_repository", lambda: orepo)
    return wrepo, orepo


# --------------------------------------------------------------------------- #
# Win 2 — workshop auto-link safety net
# --------------------------------------------------------------------------- #
def test_confirm_creates_workshop_job_for_lens_order(patched_repos):
    from api.routers.orders import _ensure_workshop_job_for_order

    wrepo, orepo = patched_repos
    order = {
        "order_id": "ORD-1",
        "store_id": "BV-BOK-01",
        "expected_delivery": "2026-06-10T00:00:00",
        "items": [
            {"item_type": "FRAME", "product_id": "F1", "product_name": "Ray-Ban", "sku": "RB1"},
            {"item_type": "LENS", "product_id": "L1", "prescription_id": "RX-9", "lens_details": {"index": "1.6"}},
        ],
    }
    jid = _ensure_workshop_job_for_order(order, "user-1")
    assert jid == "JID-1"
    assert len(wrepo.jobs) == 1
    job = wrepo.jobs[0]
    assert job["order_id"] == "ORD-1"
    assert job["prescription_id"] == "RX-9"
    assert job["status"] == "PENDING"
    assert job["auto_created"] is True
    assert job["frame_details"]["name"] == "Ray-Ban"
    assert job["lens_details"]["index"] == "1.6"
    # reverse pointer stamped on the order
    assert ("ORD-1", {"workshop_job_id": "JID-1", "workshop_job_number": job["job_number"]}) in orepo.updates


def test_idempotent_when_job_already_exists(patched_repos):
    from api.routers.orders import _ensure_workshop_job_for_order

    wrepo, orepo = patched_repos
    # POS client already created the job (no reverse pointer yet on the order).
    wrepo.jobs.append({"order_id": "ORD-2", "job_id": "JID-existing", "job_number": "WS-1"})
    order = {"order_id": "ORD-2", "items": [{"item_type": "LENS"}]}
    jid = _ensure_workshop_job_for_order(order, "user-1")
    assert jid == "JID-existing"
    assert len(wrepo.jobs) == 1  # NO duplicate created
    # backfilled the reverse pointer
    assert ("ORD-2", {"workshop_job_id": "JID-existing", "workshop_job_number": "WS-1"}) in orepo.updates


def test_no_backfill_when_order_already_linked(patched_repos):
    from api.routers.orders import _ensure_workshop_job_for_order

    wrepo, orepo = patched_repos
    wrepo.jobs.append({"order_id": "ORD-3", "job_id": "JID-x", "job_number": "WS-2"})
    order = {"order_id": "ORD-3", "workshop_job_id": "JID-x", "items": [{"item_type": "LENS"}]}
    jid = _ensure_workshop_job_for_order(order, "user-1")
    assert jid == "JID-x"
    assert orepo.updates == []  # already linked -> no redundant write


def test_no_job_for_accessory_only_order(patched_repos):
    from api.routers.orders import _ensure_workshop_job_for_order

    wrepo, _ = patched_repos
    order = {"order_id": "ORD-4", "items": [{"item_type": "ACCESSORY"}, {"item_type": "WATCH"}]}
    assert _ensure_workshop_job_for_order(order, "u") is None
    assert wrepo.jobs == []


def test_no_job_for_frame_without_prescription(patched_repos):
    from api.routers.orders import _ensure_workshop_job_for_order

    wrepo, _ = patched_repos
    order = {"order_id": "ORD-5", "items": [{"item_type": "FRAME"}]}  # frame, no Rx anywhere
    assert _ensure_workshop_job_for_order(order, "u") is None
    assert wrepo.jobs == []


def test_frame_with_prescription_does_need_fitting(patched_repos):
    from api.routers.orders import _ensure_workshop_job_for_order

    wrepo, _ = patched_repos
    order = {"order_id": "ORD-6", "items": [{"item_type": "FRAME", "prescription_id": "RX-1"}]}
    jid = _ensure_workshop_job_for_order(order, "u")
    assert jid == "JID-1"
    assert len(wrepo.jobs) == 1


def test_never_raises_on_repo_failure(monkeypatch):
    """A workshop hiccup must never block confirming a paid order."""
    import api.dependencies as deps
    from api.routers.orders import _ensure_workshop_job_for_order

    class Boom:
        def find_by_order(self, _):
            raise RuntimeError("mongo down")

    monkeypatch.setattr(deps, "get_workshop_repository", lambda: Boom())
    order = {"order_id": "ORD-7", "items": [{"item_type": "LENS"}]}
    assert _ensure_workshop_job_for_order(order, "u") is None  # swallowed


# --------------------------------------------------------------------------- #
# Win 3 — delivery-date upper bound
# --------------------------------------------------------------------------- #
def test_delivery_date_too_far_rejected():
    from pydantic import ValidationError

    from api.routers.orders import OrderCreate, OrderItemCreate

    far = date.today() + timedelta(days=400)
    with pytest.raises(ValidationError):
        OrderCreate(
            customer_id="C1",
            items=[OrderItemCreate(item_type="FRAME", product_id="F1", unit_price=100.0)],
            delivery_date=far,
        )


def test_delivery_date_within_year_ok():
    from api.routers.orders import OrderCreate, OrderItemCreate

    soon = date.today() + timedelta(days=30)
    o = OrderCreate(
        customer_id="C1",
        items=[OrderItemCreate(item_type="FRAME", product_id="F1", unit_price=100.0)],
        delivery_date=soon,
    )
    assert o.delivery_date == soon


def test_delivery_date_in_past_still_rejected():
    from pydantic import ValidationError

    from api.routers.orders import OrderCreate, OrderItemCreate

    with pytest.raises(ValidationError):
        OrderCreate(
            customer_id="C1",
            items=[OrderItemCreate(item_type="FRAME", product_id="F1", unit_price=100.0)],
            delivery_date=date.today() - timedelta(days=1),
        )
