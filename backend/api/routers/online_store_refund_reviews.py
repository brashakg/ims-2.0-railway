"""
IMS 2.0 - Online Store : Refund Review QUEUE consumer  (Shopify refund -> GST)
==============================================================================
The ACCOUNTANT-facing consumer for `shopify_refund_review` -- the review queue
that api/services/shopify_refund.handle_shopify_refund writes to by DEFAULT
(SHOPIFY_REFUND_AUTO off). Without this surface those rows were a write-only dead
letter: a real Shopify refund landed as an invisible Mongo doc no one could see or
act on, with no GST credit note and no restock (the exact compliance gap the
module claims to close).

This router lets the accountant SEE the queue and ACT on it:
  GET  /                       list review rows (filter ?status=), store-scoped
  POST /{review_id}/confirm    POST the credit note + restock from the STORED row
                               (reuses shopify_refund.post_from_review -> the SAME
                               _issue_store_credit + _restock_good_items the AUTO
                               path uses) and stamp {status:POSTED, resolved:true}
  POST /{review_id}/reject     stamp {status:REJECTED, resolved:true} -- no posting

Mounted at /api/v1/online-store/refund-reviews. ROLE GATE (router-level +
rbac_policy.POLICY, in lock-step): ACCOUNTANT / ADMIN / SUPERADMIN (SUPERADMIN
auto-granted by require_roles). The books are the accountant's, so ACCOUNTANT is
first-class here. Non-HQ callers are FORCED to their own store scope.

FAIL-SOFT: no DB -> the list is an empty page (never a 500); confirm/reject on a
missing row is a 404; a re-confirm of a resolved row is a 409. AUDIT EVERYTHING:
confirm + reject write a chained audit_logs row (fail-soft). No emojis (cp1252).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .auth import require_roles
from ..dependencies import user_store_scope

router = APIRouter()

# The books are the accountant's -> ACCOUNTANT is first-class here (SUPERADMIN is
# auto-granted by require_roles; ADMIN is HQ). Keep in lock-step with the POLICY
# rows in rbac_policy.py for every route below.
_REVIEW_ROLES = ("ADMIN", "ACCOUNTANT")

_REVIEW_COLLECTION = "shopify_refund_review"
_RETURNS_COLLECTION = "returns"

_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500

# Statuses that can be CONFIRMED (a real order + credit note exists behind them).
# UNMATCHED has no order/credit note yet -> not confirmable (redeliver / re-drive).
_CONFIRMABLE = {"PENDING", "DISCREPANCY", "CREDIT_FAILED", "NO_CUSTOMER"}


# ---------------------------------------------------------------------------
# DB helpers (fail-soft; mirror routers/online_store_orders.py)
# ---------------------------------------------------------------------------


def _get_db():
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def _clean(doc: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(doc, dict):
        doc.pop("_id", None)
    return doc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _duplicate_has_credit_note(db, row: Dict[str, Any]) -> bool:
    """Defense-in-depth for the CONFIRM path: a post that returns 'duplicate' may
    only be treated as truly POSTED when a REAL credit note exists behind the
    refund id (the `returns` doc shows credit_note_issued == True). A stale claim
    that never issued (e.g. a no-customer refund whose row still reads
    credit_note_issued=False) must NOT falsely close the review as POSTED -- the
    GST reversal was never written. Fail CLOSED (return False) on any doubt so the
    row stays open for retry rather than silently swallowing the reversal."""
    refund_id = str(row.get("shopify_refund_id") or "").strip()
    if not refund_id:
        return False
    try:
        coll = db.get_collection(_RETURNS_COLLECTION)
        doc = coll.find_one({"shopify_refund_id": refund_id}) if coll is not None else None
    except Exception:  # noqa: BLE001
        doc = None
    return bool(doc and doc.get("credit_note_issued") is True)


# ---------------------------------------------------------------------------
# GET / -- list review rows
# ---------------------------------------------------------------------------


@router.get("")
@router.get("/")
async def list_refund_reviews(
    status: Optional[str] = Query(
        None, description="Filter by status (PENDING / DISCREPANCY / UNMATCHED / POSTED / REJECTED / ...)"
    ),
    resolved: Optional[bool] = Query(
        None, description="Filter by whether the row is resolved (posted/rejected)."
    ),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(require_roles(*_REVIEW_ROLES)),
) -> Dict[str, Any]:
    """List Shopify refund review rows, newest first. Store-scoped: a non-HQ
    caller only sees the review rows of the store(s) they manage (the row's
    billing store_id). Fail-soft: no DB -> an empty page."""
    db = _get_db()
    if db is None:
        return {"reviews": [], "total": 0, "limit": limit, "offset": offset, "db_connected": False}

    query: Dict[str, Any] = {}
    is_cross, allowed_stores = user_store_scope(current_user)
    if not is_cross:
        query["store_id"] = {"$in": sorted(allowed_stores)}
    if status:
        query["status"] = status
    if resolved is not None:
        query["resolved"] = resolved

    try:
        coll = db.get_collection(_REVIEW_COLLECTION)
        if coll is None:
            return {"reviews": [], "total": 0, "limit": limit, "offset": offset, "db_connected": True}
        total = int(coll.count_documents(query))
        cursor = coll.find(query).sort("created_at", -1).skip(offset).limit(limit)
        reviews: List[Dict[str, Any]] = [_clean(d) for d in cursor]
    except Exception:  # noqa: BLE001 - reads degrade, never 500
        return {"reviews": [], "total": 0, "limit": limit, "offset": offset, "db_connected": True}

    return {
        "reviews": reviews,
        "total": total,
        "limit": limit,
        "offset": offset,
        "db_connected": True,
    }


# ---------------------------------------------------------------------------
# Shared: load one review row (store-scoped)
# ---------------------------------------------------------------------------


def _load_review(db, review_id: str, current_user: dict) -> Dict[str, Any]:
    """Fetch one review row by review_id, enforcing store scope. 404 when absent
    or outside the caller's scope."""
    try:
        coll = db.get_collection(_REVIEW_COLLECTION)
        row = coll.find_one({"review_id": review_id}) if coll is not None else None
    except Exception:  # noqa: BLE001
        row = None
    if not row:
        raise HTTPException(status_code=404, detail="Refund review not found")
    is_cross, allowed_stores = user_store_scope(current_user)
    if not is_cross and row.get("store_id") not in allowed_stores:
        # Fail closed: do not reveal another store's row.
        raise HTTPException(status_code=404, detail="Refund review not found")
    return row


# ---------------------------------------------------------------------------
# POST /{review_id}/confirm
# ---------------------------------------------------------------------------


@router.post("/{review_id}/confirm")
async def confirm_refund_review(
    review_id: str,
    current_user: dict = Depends(require_roles(*_REVIEW_ROLES)),
) -> Dict[str, Any]:
    """Confirm a review row: POST the GST credit note + restock from the STORED
    row (via shopify_refund.post_from_review -> the same in-store machinery the
    AUTO path uses), then stamp {status:POSTED, resolved:true, resolved_by,
    resolved_at, return_id}.

    409 if the row is already resolved or not in a confirmable state; 404 if
    absent / out of scope. Idempotent: the underlying claim-first insert catches a
    double-confirm as 'duplicate'."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Refund reviews unavailable (no DB)")

    row = _load_review(db, review_id, current_user)
    if row.get("resolved"):
        raise HTTPException(status_code=409, detail="This refund review is already resolved")
    status = str(row.get("status") or "").upper()
    if status not in _CONFIRMABLE:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Refund review in status {status or 'UNKNOWN'} cannot be confirmed "
                "(only a matched refund with a computed credit note can be posted)."
            ),
        )
    if not row.get("credit_note"):
        raise HTTPException(
            status_code=409,
            detail="This refund review has no computed credit note to post.",
        )

    try:
        from ..services.shopify_refund import post_from_review

        result = post_from_review(db, row)
    except Exception as exc:  # noqa: BLE001 - post_from_review is fail-soft; belt-and-braces
        result = {"status": "error", "error": str(exc)}

    post_status = str(result.get("status") or "")
    if post_status == "duplicate":
        # A 'duplicate' only counts as POSTED when a real credit note actually
        # exists behind it. Re-read the returns doc: a duplicate whose row was
        # never credited (credit_note_issued != True) is a stale claim, NOT a
        # posted refund -> keep the review open instead of falsely closing it.
        posted = _duplicate_has_credit_note(db, row)
    else:
        posted = post_status == "credited"
    new_status = "POSTED" if posted else "CREDIT_FAILED"
    update = {
        "status": new_status,
        "resolved": posted,
        "resolved_by": current_user.get("user_id"),
        "resolved_at": _now(),
        "updated_at": _now(),
        "return_id": result.get("return_id"),
        "post_result": post_status,
    }
    try:
        coll = db.get_collection(_REVIEW_COLLECTION)
        if coll is not None:
            coll.update_one({"review_id": review_id}, {"$set": update})
    except Exception:  # noqa: BLE001
        pass

    _write_audit("SHOPIFY_REFUND_REVIEW_CONFIRM", review_id, row, result, current_user)

    if not posted:
        # The post could not issue the credit note (e.g. no customer / ledger
        # error). Surface it so the accountant retries after fixing the cause.
        raise HTTPException(
            status_code=422,
            detail=(
                "Credit note could not be issued for this refund "
                f"({post_status or 'error'}). The row stays open for retry."
            ),
        )

    return {"review_id": review_id, "status": new_status, "result": result}


# ---------------------------------------------------------------------------
# POST /{review_id}/reject
# ---------------------------------------------------------------------------


@router.post("/{review_id}/reject")
async def reject_refund_review(
    review_id: str,
    current_user: dict = Depends(require_roles(*_REVIEW_ROLES)),
) -> Dict[str, Any]:
    """Reject a review row: stamp {status:REJECTED, resolved:true} without posting
    anything. 409 if already resolved; 404 if absent / out of scope."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Refund reviews unavailable (no DB)")

    row = _load_review(db, review_id, current_user)
    if row.get("resolved"):
        raise HTTPException(status_code=409, detail="This refund review is already resolved")

    update = {
        "status": "REJECTED",
        "resolved": True,
        "resolved_by": current_user.get("user_id"),
        "resolved_at": _now(),
        "updated_at": _now(),
    }
    try:
        coll = db.get_collection(_REVIEW_COLLECTION)
        if coll is not None:
            coll.update_one({"review_id": review_id}, {"$set": update})
    except Exception:  # noqa: BLE001
        pass

    _write_audit("SHOPIFY_REFUND_REVIEW_REJECT", review_id, row, {"status": "rejected"}, current_user)
    return {"review_id": review_id, "status": "REJECTED"}


# ---------------------------------------------------------------------------
# Audit (fail-soft)
# ---------------------------------------------------------------------------


def _write_audit(
    action: str, review_id: str, row: Dict[str, Any], result: Dict[str, Any], current_user: dict
) -> None:
    """Chained audit row for a confirm / reject. Fail-soft: any audit error is
    swallowed (mirrors online_store_orders._write_remap_audit)."""
    try:
        from ..dependencies import get_audit_repository

        audit = get_audit_repository()
        if audit is None:
            return
        audit.create(
            {
                "action": action,
                "entity_type": "shopify_refund_review",
                "entity_id": review_id,
                "store_id": row.get("store_id"),
                "user_id": current_user.get("user_id"),
                "details": {
                    "shopify_refund_id": row.get("shopify_refund_id"),
                    "order_id": row.get("order_id"),
                    "gross_refund": row.get("gross_refund"),
                    "prior_status": row.get("status"),
                    "post_status": (result or {}).get("status"),
                    "return_id": (result or {}).get("return_id"),
                    "credit_note_issued": (result or {}).get("credit_note_issued"),
                },
            }
        )
    except Exception:  # noqa: BLE001 -- audit must never break the action
        pass
