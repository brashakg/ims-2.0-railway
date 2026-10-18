"""A08 same-draft recovery and issue/cancel/reprice interleavings."""
import pytest

from tests.test_receipt_rejection_handoff_a08 import (
    world, app_world, line, receipt, post, MANAGER, ACCOUNTANT,
)
from api.routers import rtv_debit_notes as dn
from api.services.rtv_debit_note import DebitNoteEngine


def ready(w, *, priced=True):
    receipt(w, [line(0, 1)])
    assert post(w).status_code == 200
    rid = w["db"].vendor_returns.find_one({})["return_id"]
    assert status(w, rid, "created", return_type="credit_note").status_code == 200
    if priced:
        assert price(w, rid, 100).status_code == 200
    return rid


def status(w, rid, value, **extra):
    return w["as_"](MANAGER).patch(f"/vendor-returns/{rid}/status", json={"status": value, **extra})


def price(w, rid, value):
    return w["as_"](ACCOUNTANT).put(f"/vendor-returns/{rid}/prices", json={"unit_prices": [value]})


def issue(w, rid):
    return w["as_"](ACCOUNTANT).post("/rtv-debit-notes/issue", json={"rtv_id": rid})


@pytest.mark.parametrize("starting", ["draft", "created", "approved"])
def test_cancel_reopen_same_draft_preserves_custody_prices_and_requires_confirmation(app_world, starting):
    db = app_world["db"]
    rid = ready(app_world)
    if starting == "draft":
        db.vendor_returns.update_one({"return_id": rid}, {"$set": {"status": "draft"}})
    elif starting == "approved":
        assert status(app_world, rid, "approved").status_code == 200
    before = db.vendor_returns.find_one({"return_id": rid})
    assert status(app_world, rid, "cancelled").status_code == 200
    reopened = status(app_world, rid, "draft")
    assert reopened.status_code == 200, reopened.text
    saved = db.vendor_returns.find_one({"return_id": rid})
    for key in ("return_id", "stock_ids", "items", "total_value", "return_type", "grn_id", "grn_line_index"):
        assert saved[key] == before[key]
    assert saved["debit_note_draft_status"] == "DRAFT"
    assert [h["status"] for h in saved["status_history"][-2:]] == ["cancelled", "draft"]
    assert db.stock_units.find_one({})["status"] == "QUARANTINED"
    assert issue(app_world, rid).status_code == 409
    assert status(app_world, rid, "created", return_type="replacement").status_code == 200
    assert issue(app_world, rid).status_code == 201


def test_issue_claim_wins_then_cancel_and_reprice_cannot_change_snapshot(app_world, monkeypatch):
    rid = ready(app_world)
    real = DebitNoteEngine.issue

    def during_issue(self, doc, *args, **kwargs):
        assert status(app_world, rid, "cancelled").status_code == 409
        assert price(app_world, rid, 900).status_code == 409
        assert issue(app_world, rid).status_code == 409  # Exclusive live claim.
        return real(self, doc, *args, **kwargs)

    monkeypatch.setattr(DebitNoteEngine, "issue", during_issue)
    assert issue(app_world, rid).status_code == 201
    saved = app_world["db"].vendor_returns.find_one({"return_id": rid})
    assert saved["total_value"] == 100 and saved["debit_note_draft_status"] == "ISSUED"


def test_cancellation_wins_before_issue_claim_so_stale_snapshot_cannot_issue(app_world, monkeypatch):
    db = app_world["db"]
    rid = ready(app_world)
    real = db.vendor_returns.find_one_and_update
    fired = []

    def cancel_first(flt, patch, **kwargs):
        if patch.get("$set", {}).get("debit_note_draft_status") == "ISSUING" and not fired:
            fired.append(True)
            assert status(app_world, rid, "cancelled").status_code == 200
        return real(flt, patch, **kwargs)

    monkeypatch.setattr(db.vendor_returns, "find_one_and_update", cancel_first)
    assert issue(app_world, rid).status_code == 409
    assert db.debit_notes.count_documents({}) == 0
    assert db.vendor_returns.find_one({})["status"] == "cancelled"


def test_price_wins_before_issue_claim_and_issue_uses_fresh_document(app_world, monkeypatch):
    db = app_world["db"]
    rid = ready(app_world)
    real_claim = db.vendor_returns.find_one_and_update
    real_issue = DebitNoteEngine.issue
    fired = []

    def price_first(flt, patch, **kwargs):
        if not fired:
            fired.append(True)
            assert price(app_world, rid, 321).status_code == 200
        return real_claim(flt, patch, **kwargs)

    def assert_fresh(self, doc, *args, **kwargs):
        assert doc["items"][0]["unit_price"] == 321
        return real_issue(self, doc, *args, **kwargs)

    monkeypatch.setattr(db.vendor_returns, "find_one_and_update", price_first)
    monkeypatch.setattr(DebitNoteEngine, "issue", assert_fresh)
    assert issue(app_world, rid).status_code == 201


def test_known_validation_failure_releases_own_claim_for_accounts_correction(app_world):
    rid = ready(app_world, priced=False)
    assert issue(app_world, rid).status_code == 422
    saved = app_world["db"].vendor_returns.find_one({})
    assert saved["debit_note_draft_status"] == "DRAFT"
    assert not saved.get("debit_note_issue_token")
    assert price(app_world, rid, 123).status_code == 200
    assert issue(app_world, rid).status_code == 201


def test_persisted_note_blocks_cancellation_even_if_metadata_write_failed_and_retry_repairs(app_world, monkeypatch):
    db = app_world["db"]
    rid = ready(app_world)
    real = db.vendor_returns.update_one

    def fail_metadata(flt, patch, *args, **kwargs):
        if patch.get("$set", {}).get("debit_note_draft_status") == "ISSUED":
            raise RuntimeError("metadata unavailable")
        return real(flt, patch, *args, **kwargs)

    monkeypatch.setattr(db.vendor_returns, "update_one", fail_metadata)
    assert issue(app_world, rid).status_code == 503
    note = db.debit_notes.find_one({})
    assert note is not None
    # An older/lagging metadata row must not override the authoritative note.
    real({"return_id": rid}, {"$set": {"debit_note_draft_status": "DRAFT"}})
    assert status(app_world, rid, "cancelled").status_code == 409
    assert price(app_world, rid, 456).status_code == 409
    monkeypatch.setattr(db.vendor_returns, "update_one", real)
    repaired = issue(app_world, rid)
    assert repaired.status_code == 201 and repaired.json()["idempotent"] is True
    assert repaired.json()["debit_note"]["debit_note_id"] == note["debit_note_id"]
    assert db.debit_notes.count_documents({}) == 1
    assert db.vendor_returns.find_one({})["debit_note_draft_status"] == "ISSUED"


def test_ambiguous_failure_stays_fenced_even_if_retry_has_validation_error(app_world, monkeypatch):
    db = app_world["db"]
    rid = ready(app_world)
    real_insert = db.debit_notes.insert_one
    monkeypatch.setattr(db.debit_notes, "insert_one", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("unknown write outcome")))
    assert issue(app_world, rid).status_code == 500
    assert db.vendor_returns.find_one({})["debit_note_draft_status"] == "RETRY_ISSUE"
    real_seller = dn._load_seller
    monkeypatch.setattr(dn, "_load_seller", lambda *_a: {})
    assert issue(app_world, rid).status_code == 422
    assert db.vendor_returns.find_one({})["debit_note_draft_status"] == "RETRY_ISSUE"
    assert status(app_world, rid, "cancelled").status_code == 409
    assert price(app_world, rid, 456).status_code == 409
    monkeypatch.setattr(dn, "_load_seller", real_seller)
    monkeypatch.setattr(db.debit_notes, "insert_one", real_insert)
    assert issue(app_world, rid).status_code == 201
    assert db.debit_notes.count_documents({}) == 1


def test_unreadable_note_store_fails_closed_on_cancel_reopen_and_price(app_world, monkeypatch):
    db = app_world["db"]
    rid = ready(app_world)
    monkeypatch.setattr(db.debit_notes, "find_one", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("unreadable")))
    assert status(app_world, rid, "cancelled").status_code == 503
    assert price(app_world, rid, 456).status_code == 503
    db.vendor_returns.update_one({"return_id": rid}, {"$set": {"status": "cancelled", "debit_note_draft_status": "CANCELLED"}})
    assert status(app_world, rid, "draft").status_code == 503


def test_crashed_active_claim_without_note_stays_fenced_for_technical_reconciliation(app_world):
    db = app_world["db"]
    rid = ready(app_world)
    db.vendor_returns.update_one({"return_id": rid}, {"$set": {
        "debit_note_draft_status": "ISSUING", "debit_note_issue_token": "stopped-process"}})
    assert issue(app_world, rid).status_code == 409
    assert status(app_world, rid, "cancelled").status_code == 409
    assert price(app_world, rid, 456).status_code == 409
    assert db.debit_notes.count_documents({}) == 0


def test_manual_return_cannot_use_receipt_only_reopen(app_world):
    db = app_world["db"]
    rid = ready(app_world)
    db.vendor_returns.update_one({"return_id": rid}, {"$unset": {"receipt_rejection": ""}})
    assert status(app_world, rid, "cancelled").status_code == 200
    assert status(app_world, rid, "draft").status_code == 400
