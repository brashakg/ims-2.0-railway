"""accept_grn is store-scoped (salvaged hardening 2026-07-03).

A store-level VENDOR-role user must not be able to accept (mint stock against /
advance the PO of) a GRN belonging to ANOTHER store. The object-level guard
(can_access_store_scoped) mirrors the sibling download_grn_doc endpoint and hides
existence with a 404. This test asserts the reject fires BEFORE any mutation; the
same-store / admin happy path stays covered by the existing GRN accept tests
(test_grn_cl_batch_expiry, test_hub_phase2_grn_hero).
"""
from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from api.routers import vendors as vendors_mod  # noqa: E402


class _GRNRepo:
    """Records every mutation so the test can assert none happened on reject."""

    def __init__(self, grn):
        self.grn = grn
        self.mutations = []

    def find_by_id(self, gid):
        return dict(self.grn) if gid == self.grn["grn_id"] else None

    def update(self, gid, patch):
        self.mutations.append(("update", gid, patch))
        return True

    def update_one(self, *a, **k):
        self.mutations.append(("update_one", a, k))
        return type("R", (), {"matched_count": 1, "modified_count": 1})()

    def find_one_and_update(self, *a, **k):
        self.mutations.append(("find_one_and_update", a, k))
        return dict(self.grn)


def _grn(store_id):
    return {
        "grn_id": "GRN-1",
        "grn_number": "GRN-2601-001",
        "store_id": store_id,
        "po_id": "PO-1",
        "status": "PENDING",
        "items": [{"product_id": "P1", "accepted_qty": 5, "location_code": "DEFAULT"}],
    }


def _user(roles, active, stores=None):
    return {
        "user_id": "u1",
        "username": "t",
        "roles": roles,
        "active_store_id": active,
        "store_ids": stores if stores is not None else ([active] if active else []),
    }


def test_cross_store_accept_404_no_mutation(monkeypatch):
    """A STORE_MANAGER of STORE-A accepting a STORE-B GRN gets 404, no writes."""
    repo = _GRNRepo(_grn("STORE-B"))
    monkeypatch.setattr(vendors_mod, "get_grn_repository", lambda: repo)
    monkeypatch.setattr(vendors_mod, "get_stock_repository", lambda: None)
    monkeypatch.setattr(vendors_mod, "get_purchase_order_repository", lambda: None)

    with pytest.raises(HTTPException) as e:
        asyncio.run(vendors_mod.accept_grn("GRN-1", _user(["STORE_MANAGER"], "STORE-A")))
    assert e.value.status_code == 404
    assert repo.mutations == [], "guard must fire before any stock/PO mutation"
