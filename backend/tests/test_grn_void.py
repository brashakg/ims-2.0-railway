"""
IMS 2.0 - GRN void endpoint (duplicate/mistake cleanup)
=======================================================
The Goods-Receipt Cockpit used to create GRNs (PENDING, no stock) and reset --
the PO stayed receivable, operators re-received, and duplicate PENDING GRNs
piled up with no way to clear them (live-hit by the owner 2026-07-04, three
identical GRNs). POST /vendors/grn/{id}/void closes that hole:

  * PENDING-only -- an ACCEPTED/PARTIALLY_ACCEPTED GRN has minted stock and
    must be corrected via a vendor return, so voiding it is 400.
  * Store-scoped like accept: a cross-store caller reads 404 (existence not
    disclosed).
  * The row is KEPT with status VOID (audit/numbering continuity); the accept
    endpoint refuses non-PENDING rows, so a voided GRN can never mint stock.

Run: JWT_SECRET_KEY=test python -m pytest backend/tests/test_grn_void.py -q
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from api.routers import vendors as v  # noqa: E402
from api.routers.vendors import void_grn  # noqa: E402


class _FakeGRNRepo:
    def __init__(self, preset=None):
        self._preset = preset
        self.updated = None

    def find_by_id(self, grn_id):
        return self._preset

    def update(self, grn_id, fields):
        self.updated = (grn_id, fields)
        return True


def _grn(status="PENDING", store="BV-TEST-01"):
    return {
        "grn_id": "G1",
        "grn_number": "RCPT/TEST/26-27/0006",
        "status": status,
        "store_id": store,
        "po_id": "PO1",
        "items": [{"product_id": "P1", "received_qty": 2, "accepted_qty": 2}],
    }


def _user(store="BV-TEST-01", roles=("ADMIN",)):
    return {"user_id": "u1", "roles": list(roles), "active_store_id": store}


def _patch(mp, repo):
    mp.setattr(v, "get_grn_repository", lambda: repo)
    mp.setattr(v, "get_audit_repository", lambda: None)


def test_void_pending_grn_flips_status(monkeypatch):
    repo = _FakeGRNRepo(_grn())
    _patch(monkeypatch, repo)
    res = asyncio.run(void_grn("G1", current_user=_user()))
    assert res["grn_status"] == "VOID"
    grn_id, fields = repo.updated
    assert grn_id == "G1"
    assert fields["status"] == "VOID"
    assert fields["voided_by"] == "u1"
    assert "voided_at" in fields


def test_void_accepted_grn_is_400(monkeypatch):
    repo = _FakeGRNRepo(_grn(status="ACCEPTED"))
    _patch(monkeypatch, repo)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(void_grn("G1", current_user=_user()))
    assert exc.value.status_code == 400
    assert "vendor return" in str(exc.value.detail)
    assert repo.updated is None


def test_void_partially_accepted_is_400_too(monkeypatch):
    # PARTIALLY_ACCEPTED has already minted SOME stock -- not voidable.
    repo = _FakeGRNRepo(_grn(status="PARTIALLY_ACCEPTED"))
    _patch(monkeypatch, repo)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(void_grn("G1", current_user=_user()))
    assert exc.value.status_code == 400
    assert repo.updated is None


def test_missing_grn_is_404(monkeypatch):
    repo = _FakeGRNRepo(None)
    _patch(monkeypatch, repo)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(void_grn("NOPE", current_user=_user()))
    assert exc.value.status_code == 404


def test_cross_store_reads_as_404(monkeypatch):
    # A store-level caller must not learn the GRN exists in another store.
    repo = _FakeGRNRepo(_grn(store="OTHER-STORE"))
    _patch(monkeypatch, repo)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            void_grn(
                "G1",
                current_user={
                    "user_id": "u2",
                    "roles": ["STORE_MANAGER"],
                    "active_store_id": "BV-TEST-01",
                    "store_ids": ["BV-TEST-01"],
                },
            )
        )
    assert exc.value.status_code == 404
    assert repo.updated is None


def test_superadmin_can_void_any_store(monkeypatch):
    repo = _FakeGRNRepo(_grn(store="OTHER-STORE"))
    _patch(monkeypatch, repo)
    res = asyncio.run(
        void_grn("G1", current_user=_user(roles=("SUPERADMIN",)))
    )
    assert res["grn_status"] == "VOID"


def test_rbac_row_catalogued():
    from api.services import rbac_policy as rbac

    rows = [
        p for p in rbac.POLICY
        if p.get("path") == "/api/v1/vendors/grn/{grn_id}/void"
    ]
    assert len(rows) == 1
    assert rows[0]["method"] == "POST"
