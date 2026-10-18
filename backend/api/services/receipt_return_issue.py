"""Receipt-return issue fences; ordinary manual returns keep their old flow."""
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from pymongo import ReturnDocument


ISSUE_FENCES = ("ISSUING", "RETRY_ISSUE", "ISSUED")


def unissued_filter():
    """Shared atomic condition for cancellation, reopening and repricing."""
    return {
        "debit_note_draft_status": {"$nin": list(ISSUE_FENCES)},
        "issued_debit_note_id": None,
    }


def persisted_note(db, return_id):
    """Authoritative read: unknown is never permission to cancel or reprice."""
    try:
        doc = db.get_collection("debit_notes").find_one({"rtv_ref_id": return_id})
        if doc is not None:
            doc.pop("_id", None)
        return doc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not verify the debit note. Retry shortly.") from exc


def require_unissued(db, doc):
    if (persisted_note(db, doc["return_id"]) is not None
            or doc.get("debit_note_draft_status") in ISSUE_FENCES
            or doc.get("issued_debit_note_id")):
        raise HTTPException(status_code=409, detail="This return's debit note is issued or being issued; its cancellation and prices are locked.")


def claim_issue(db, doc):
    """Exclusive issue claim; a handled ambiguous failure permits issue retry.

    A process that dies while ISSUING keeps its fence. With no persisted note,
    that uncommon case needs technical reconciliation, never a TTL takeover
    that could let an old worker issue after a cancellation.
    """
    existing = persisted_note(db, doc["return_id"])
    if existing is not None:
        return None, doc, existing
    if (doc.get("status") in ("draft", "cancelled")
            or doc.get("return_type") not in ("credit_note", "replacement")):
        raise HTTPException(status_code=409, detail="Confirm this return's credit or replacement choice before issuing a debit note.")
    token = uuid4().hex
    coll = db.get_collection("vendor_returns")
    try:
        current = coll.find_one_and_update(
            {"return_id": doc["return_id"], "receipt_rejection": True,
             "status": doc.get("status"), "return_type": doc.get("return_type"),
             "debit_note_draft_status": {"$nin": ["ISSUING", "ISSUED"]},
             "issued_debit_note_id": None},
            {"$set": {"debit_note_draft_status": "ISSUING", "debit_note_issue_token": token,
                      "debit_note_issue_at": datetime.now(timezone.utc).isoformat()}},
            return_document=ReturnDocument.AFTER,
        )
    except Exception as exc:
        finish_failed_issue(db, doc["return_id"], token, retry_required=True)
        raise HTTPException(status_code=503, detail="Could not reserve this return for issue. Retry issuing it.") from exc
    if current is None:
        raise HTTPException(status_code=409, detail="The return changed or its debit note is already being issued. Refresh before retrying.")
    return token, current, None


def finish_failed_issue(db, return_id, token, *, retry_required):
    """Known validation resets; ambiguous persistence keeps a durable fence."""
    try:
        db.get_collection("vendor_returns").update_one(
            {"return_id": return_id, "debit_note_issue_token": token,
             "debit_note_draft_status": "ISSUING"},
            {"$set": {"debit_note_draft_status": "RETRY_ISSUE" if retry_required else "DRAFT",
                      "debit_note_issue_uncertain": bool(retry_required)},
             "$unset": {"debit_note_issue_token": "", "debit_note_issue_at": ""}},
        )
    except Exception:
        # An uncertain cleanup must leave the original ISSUING fence intact.
        pass


def finish_issued(db, return_id, note):
    """The real note wins over lagging draft metadata, including issue retries."""
    try:
        result = db.get_collection("vendor_returns").update_one(
            {"return_id": return_id, "receipt_rejection": True},
            {"$set": {"debit_note_draft_status": "ISSUED",
                      "issued_debit_note_id": note.get("debit_note_id")},
             "$unset": {"debit_note_issue_token": "", "debit_note_issue_at": "",
                        "debit_note_issue_uncertain": ""}},
        )
        if result.matched_count != 1:
            raise RuntimeError("Receipt return disappeared during issue")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="The debit note was issued, but its return link needs a retry. Issue again to recover the same note.") from exc
