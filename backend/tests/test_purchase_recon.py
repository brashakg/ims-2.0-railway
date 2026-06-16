"""Purchase P1 / S6 -- Accountant Reconciliation Console.

Tests drive the endpoint functions directly with monkeypatched DB mocks.
No real Mongo required.  Pattern mirrors test_po_gst_and_residuals.py.

Coverage:
  - recon tick persists the 4 flags + audit stamps (idempotent)
  - partial update leaves untouched flags unchanged
  - GET recon returns empty block with defaults when no recon yet
  - GET recon 404 on missing bill
  - POST recon 404 on missing bill
  - worklists endpoint: all 4 lists populated from mocked collections
  - worklists endpoint: fail-soft empty when collections absent (db=None)
"""
from __future__ import annotations

import asyncio
import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")
os.environ.setdefault("MONGODB_URI", "")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import HTTPException

from api.routers.purchase_recon import (
    ReconUpdate,
    _build_recon_block,
    _stock_yet_to_receive,
    _pending_vendor_returns_open,
    _pending_return_credit_notes,
    _pending_scheme_cns,
    get_recon,
    get_recon_worklists,
    upsert_recon,
)
import api.routers.purchase_recon as recon_mod


# ---------------------------------------------------------------------------
# Fake DB helpers
# ---------------------------------------------------------------------------


class _FakeCollection:
    """Minimal pymongo collection stand-in."""

    def __init__(self, docs: list):
        self._docs = docs
        self.updated = []

    def find_one(self, q, proj=None):
        for doc in self._docs:
            if self._matches(doc, q):
                return dict(doc)
        return None

    def find(self, q=None, proj=None):
        q = q or {}
        return _FakeCursor([d for d in self._docs if self._matches(d, q)])

    def update_one(self, q, update, upsert=False):
        for doc in self._docs:
            if self._matches(doc, q):
                if "$set" in update:
                    doc.update(update["$set"])
                self.updated.append(dict(doc))
                return
        if upsert:
            new_doc = {**q, **(update.get("$set", {}))}
            self._docs.append(new_doc)
            self.updated.append(new_doc)

    def count_documents(self, q):
        return len([d for d in self._docs if self._matches(d, q)])

    @staticmethod
    def _matches(doc, q):
        """Very simple Mongo query emulator: handles equality, $in, $exists."""
        for k, v in q.items():
            if isinstance(v, dict):
                # $in
                if "$in" in v:
                    if doc.get(k) not in v["$in"]:
                        return False
                # $exists
                elif "$exists" in v:
                    has = k in doc
                    if v["$exists"] and not has:
                        return False
                    if not v["$exists"] and has:
                        return False
                else:
                    return False
            else:
                if doc.get(k) != v:
                    return False
        return True


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *a):
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def __iter__(self):
        return iter(self._docs)


class _FakeDB:
    def __init__(self, collections: dict):
        self._colls = collections

    def get_collection(self, name: str):
        if name in self._colls:
            return self._colls[name]
        # Return empty collection for unknown names (fail-soft)
        return _FakeCollection([])


def _user():
    return {"user_id": "acc-1", "roles": ["ACCOUNTANT"], "active_store_id": "BV-01"}


# ---------------------------------------------------------------------------
# Unit tests for _build_recon_block (pure logic, no I/O)
# ---------------------------------------------------------------------------


def test_build_recon_block_sets_flags():
    """Ticking flags should set them + audit stamps."""
    body = ReconUpdate(reconciled=True, entered_tally=True)
    result = _build_recon_block({}, body, "acc-1", "2026-01-01T00:00:00+00:00")

    assert result["reconciled"] is True
    assert result["entered_tally"] is True
    assert result["reconciled_by"] == "acc-1"
    assert result["entered_tally_by"] == "acc-1"
    # Untouched flags should not appear
    assert "filed_gst" not in result
    assert "payment_settled" not in result


def test_build_recon_block_idempotent():
    """Re-ticking the same flags updates audit stamps but stays consistent."""
    existing = {"reconciled": True, "reconciled_by": "old-user", "reconciled_at": "old-ts"}
    body = ReconUpdate(reconciled=True)
    result = _build_recon_block(existing, body, "new-user", "2026-02-01T00:00:00+00:00")

    assert result["reconciled"] is True
    assert result["reconciled_by"] == "new-user"
    assert result["reconciled_at"] == "2026-02-01T00:00:00+00:00"


def test_build_recon_block_partial_update_leaves_others():
    """Only supplied flags should change; existing ones must be preserved."""
    existing = {
        "reconciled": True,
        "reconciled_by": "u1",
        "entered_tally": False,
    }
    body = ReconUpdate(entered_tally=True)  # only tick entered_tally
    result = _build_recon_block(existing, body, "u2", "2026-03-01T00:00:00+00:00")

    assert result["reconciled"] is True  # unchanged
    assert result["reconciled_by"] == "u1"  # audit stamp preserved
    assert result["entered_tally"] is True
    assert result["entered_tally_by"] == "u2"


def test_build_recon_block_untick_clears_audit():
    """Setting a flag to False should remove the *_by / *_at stamps."""
    existing = {
        "reconciled": True,
        "reconciled_by": "u1",
        "reconciled_at": "ts1",
    }
    body = ReconUpdate(reconciled=False)
    result = _build_recon_block(existing, body, "u2", "ts2")

    assert result["reconciled"] is False
    assert "reconciled_by" not in result
    assert "reconciled_at" not in result


def test_build_recon_block_note():
    """Setting a note should persist it with audit."""
    body = ReconUpdate(note="Will file GST by Friday")
    result = _build_recon_block({}, body, "u1", "ts1")
    assert result["note"] == "Will file GST by Friday"
    assert result["note_by"] == "u1"


# ---------------------------------------------------------------------------
# Endpoint tests: upsert_recon / get_recon (with DB mock)
# ---------------------------------------------------------------------------


def test_upsert_recon_persists_and_returns(monkeypatch):
    """POST /recon should write flags to vendor_bills and return recon block."""
    bill = {"bill_id": "INV-001", "vendor_id": "V1"}
    coll = _FakeCollection([bill])
    db = _FakeDB({"vendor_bills": coll})

    monkeypatch.setattr(recon_mod, "_get_db", lambda: db)

    body = ReconUpdate(reconciled=True, entered_tally=True)
    result = asyncio.run(upsert_recon("INV-001", body, current_user=_user()))

    assert result["invoice_id"] == "INV-001"
    assert result["recon"]["reconciled"] is True
    assert result["recon"]["entered_tally"] is True
    assert "reconciled_by" in result["recon"]
    # Verify the doc was actually updated in the fake collection
    assert coll.updated, "update_one was never called"
    assert coll.updated[0]["recon"]["reconciled"] is True


def test_upsert_recon_is_idempotent(monkeypatch):
    """A second POST with the same flags should not corrupt the recon block."""
    bill = {
        "bill_id": "INV-002",
        "recon": {
            "reconciled": True,
            "reconciled_by": "acc-1",
            "reconciled_at": "2026-01-01T00:00:00+00:00",
            "last_updated_by": "acc-1",
            "last_updated_at": "2026-01-01T00:00:00+00:00",
        },
    }
    coll = _FakeCollection([bill])
    db = _FakeDB({"vendor_bills": coll})
    monkeypatch.setattr(recon_mod, "_get_db", lambda: db)

    body = ReconUpdate(reconciled=True, filed_gst=True)
    result = asyncio.run(upsert_recon("INV-002", body, current_user=_user()))

    assert result["recon"]["reconciled"] is True
    assert result["recon"]["filed_gst"] is True


def test_upsert_recon_404_on_missing_bill(monkeypatch):
    """POST /recon should 404 when the invoice does not exist."""
    db = _FakeDB({"vendor_bills": _FakeCollection([])})
    monkeypatch.setattr(recon_mod, "_get_db", lambda: db)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(upsert_recon("NONEXISTENT", ReconUpdate(), current_user=_user()))

    assert exc_info.value.status_code == 404


def test_get_recon_returns_defaults_when_no_recon(monkeypatch):
    """GET /recon should return all 4 flags as False when no recon yet."""
    bill = {"bill_id": "INV-003", "vendor_id": "V1"}
    db = _FakeDB({"vendor_bills": _FakeCollection([bill])})
    monkeypatch.setattr(recon_mod, "_get_db", lambda: db)

    result = asyncio.run(get_recon("INV-003", current_user=_user()))

    assert result["invoice_id"] == "INV-003"
    for flag in ("reconciled", "entered_tally", "filed_gst", "payment_settled"):
        assert result["recon"][flag] is False, f"{flag} should default to False"


def test_get_recon_returns_existing_flags(monkeypatch):
    """GET /recon should return persisted flags."""
    bill = {
        "bill_id": "INV-004",
        "recon": {
            "reconciled": True,
            "reconciled_by": "u1",
            "entered_tally": False,
        },
    }
    db = _FakeDB({"vendor_bills": _FakeCollection([bill])})
    monkeypatch.setattr(recon_mod, "_get_db", lambda: db)

    result = asyncio.run(get_recon("INV-004", current_user=_user()))
    assert result["recon"]["reconciled"] is True
    assert result["recon"]["reconciled_by"] == "u1"
    assert result["recon"]["entered_tally"] is False


def test_get_recon_404_on_missing_bill(monkeypatch):
    """GET /recon should 404 when the invoice does not exist."""
    db = _FakeDB({"vendor_bills": _FakeCollection([])})
    monkeypatch.setattr(recon_mod, "_get_db", lambda: db)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(get_recon("GHOST", current_user=_user()))

    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Endpoint tests: get_recon_worklists
# ---------------------------------------------------------------------------


def test_worklists_returns_all_four_lists(monkeypatch):
    """Worklists endpoint should return all 4 list keys."""
    db = _FakeDB({
        "purchase_orders": _FakeCollection([
            {
                "po_id": "PO-1",
                "po_number": "PO-2026-001",
                "vendor_id": "V1",
                "status": "APPROVED",
                "delivery_store_id": "BV-01",
                "items": [
                    {
                        "product_id": "P1",
                        "product_name": "Frame A",
                        "sku": "F001",
                        "ordered_qty": 10,
                        "received_qty": 3,
                    }
                ],
            }
        ]),
        "vendor_returns": _FakeCollection([
            {
                "return_id": "RTV-1",
                "vendor_id": "V1",
                "vendor_name": "Acme",
                "store_id": "BV-01",
                "return_type": "credit_note",
                "status": "created",
                "total_value": 500.0,
                "credit_note_number": None,
                "created_at": "2026-06-01T00:00:00+00:00",
            }
        ]),
        "vendor_debit_notes": _FakeCollection([
            {
                "credit_note_number": "RCN-001",
                "vendor_id": "V1",
                "amount": 1000.0,
                "amount_paise": 100000,
                "source": "VOLUME_REBATE",
                "rebate_id": "VRB-001",
                "created_at": "2026-06-01T00:00:00+00:00",
                # cn_received_at NOT present -> pending
            }
        ]),
    })
    monkeypatch.setattr(recon_mod, "_get_db", lambda: db)

    # Pass store_id=None and vendor_id=None explicitly -- when calling FastAPI
    # endpoint functions directly (not via HTTP), Query() defaults must be
    # supplied by the caller; they are NOT auto-resolved outside HTTP context.
    result = asyncio.run(
        get_recon_worklists(store_id=None, vendor_id=None, current_user=_user())
    )

    assert "stock_yet_to_receive" in result
    assert "vendor_returns" in result
    assert "pending_credit_notes_scheme" in result
    assert "pending_credit_notes_return" in result

    # stock: PO-1 has 7 pending (10-3)
    assert len(result["stock_yet_to_receive"]) == 1
    assert result["stock_yet_to_receive"][0]["po_id"] == "PO-1"
    assert result["stock_yet_to_receive"][0]["total_pending_qty"] == 7

    # vendor returns: 1 open return
    assert len(result["vendor_returns"]) == 1
    assert result["vendor_returns"][0]["return_id"] == "RTV-1"

    # scheme CN: 1 pending (no cn_received_at)
    assert len(result["pending_credit_notes_scheme"]) == 1
    assert result["pending_credit_notes_scheme"][0]["credit_note_number"] == "RCN-001"

    # return CN: 1 open (same return RTV-1 is credit_note type + created)
    assert len(result["pending_credit_notes_return"]) == 1


def test_worklists_fail_soft_when_db_none(monkeypatch):
    """Worklists should return 4 empty lists when db is None (DB down)."""
    monkeypatch.setattr(recon_mod, "_get_db", lambda: None)

    result = asyncio.run(
        get_recon_worklists(store_id=None, vendor_id=None, current_user=_user())
    )

    assert result["stock_yet_to_receive"] == []
    assert result["vendor_returns"] == []
    assert result["pending_credit_notes_scheme"] == []
    assert result["pending_credit_notes_return"] == []


def test_worklists_fully_received_po_not_shown(monkeypatch):
    """A PO where all items are fully received should not appear in stock_yet_to_receive."""
    db = _FakeDB({
        "purchase_orders": _FakeCollection([
            {
                "po_id": "PO-DONE",
                "po_number": "PO-2026-002",
                "vendor_id": "V1",
                "status": "APPROVED",
                "items": [
                    {
                        "product_id": "P2",
                        "product_name": "Lens B",
                        "sku": "L001",
                        "ordered_qty": 5,
                        "received_qty": 5,  # fully received
                    }
                ],
            }
        ]),
    })
    monkeypatch.setattr(recon_mod, "_get_db", lambda: db)

    result = asyncio.run(
        get_recon_worklists(store_id=None, vendor_id=None, current_user=_user())
    )
    assert result["stock_yet_to_receive"] == []


def test_worklists_scheme_cn_with_received_at_not_shown(monkeypatch):
    """A scheme CN that already has cn_received_at should NOT appear as pending."""
    db = _FakeDB({
        "vendor_debit_notes": _FakeCollection([
            {
                "credit_note_number": "RCN-DONE",
                "vendor_id": "V1",
                "amount": 500.0,
                "source": "VOLUME_REBATE",
                "rebate_id": "VRB-002",
                "created_at": "2026-06-01T00:00:00+00:00",
                "cn_received_at": "2026-06-10T00:00:00+00:00",  # already received
            }
        ]),
    })
    monkeypatch.setattr(recon_mod, "_get_db", lambda: db)

    result = asyncio.run(
        get_recon_worklists(store_id=None, vendor_id=None, current_user=_user())
    )
    assert result["pending_credit_notes_scheme"] == []


def test_worklists_503_on_db_upsert_error(monkeypatch):
    """POST recon should return 503 when DB raises an exception."""

    class _BrokenDB:
        def get_collection(self, name):
            return _BrokenColl()

    class _BrokenColl:
        def find_one(self, q, proj=None):
            # Return a doc so we get past the 404 check
            return {"bill_id": "INV-X"}

        def update_one(self, q, upd, upsert=False):
            raise RuntimeError("DB unavailable")

    monkeypatch.setattr(recon_mod, "_get_db", lambda: _BrokenDB())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            upsert_recon("INV-X", ReconUpdate(reconciled=True), current_user=_user())
        )
    assert exc_info.value.status_code == 503
