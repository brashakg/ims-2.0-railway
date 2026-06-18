"""
IMS 2.0 - Purchase Reconciliation Router (Purchase P1 / Slice S6)
=================================================================
Accountant reconciliation console.  After ops physically receive goods and
attach the vendor invoice (slices S2/S3), the ACCOUNTANT books the invoice
into Tally, files GST, and schedules/settles the payment.  This slice
provides the API surface for that workflow.

The 4 reconciliation ticks are stored as an INLINE ``recon`` sub-document on
the ``vendor_bills`` document -- no separate collection is required.

Mounted at ``/api/v1/vendors/purchase-invoices`` (same prefix as the main
purchase_invoices router) before the catch-all vendors GET in main.py.  The
``/recon`` and ``/recon/worklists`` paths are concrete, so FastAPI resolves them
before ``/{invoice_id}``.

Routes:
  POST /api/v1/vendors/purchase-invoices/{invoice_id}/recon
        Write (or update) the 4-tick recon sub-document on a bill.
  GET  /api/v1/vendors/purchase-invoices/{invoice_id}/recon
        Read the recon sub-document from a bill.
  GET  /api/v1/vendors/recon/worklists
        Accountant console worklists:
          stock_yet_to_receive   -- open POs with unreceived residual
          vendor_returns         -- open/in-flight vendor returns
          pending_credit_notes_scheme  -- scheme/rebate CNs not yet received
          pending_credit_notes_return  -- return CNs not yet issued

Roles: ACCOUNTANT / ADMIN (SUPERADMIN auto-passes via require_roles).
All 3 endpoints are listed in rbac_policy.py so test_no_uncatalogued_routes
stays green.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .auth import get_current_user, require_roles

router = APIRouter()
logger = logging.getLogger(__name__)

# Same gate as purchase_invoices.py
_AP_ROLES = ("ADMIN", "ACCOUNTANT")

# Statuses that mean a vendor return is still open/in-flight (not resolved)
_OPEN_RETURN_STATUSES = {"created", "shipped", "received_by_vendor"}

# Vendor-bills statuses that should be excluded when building the scheme-CN
# pending list (voided / cancelled bills have no actionable ITC balance).
_REBATE_CN_APPLIED_STATUS = "APPLIED"


def _get_db():
    """Direct DB handle, identical to purchase_invoices.py._get_db()."""
    from database.connection import get_db
    return get_db().db


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ReconUpdate(BaseModel):
    """4-tick accountant reconciliation flags. All are optional so the
    accountant can tick them one at a time.  ``note`` is free text."""
    reconciled: Optional[bool] = None          # physically reconciled with vendor statement
    entered_tally: Optional[bool] = None       # entered into Tally
    filed_gst: Optional[bool] = None           # included in GST return (GSTR-2B/3B)
    payment_settled: Optional[bool] = None     # payment scheduled or made
    note: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fetch_bill(db, invoice_id: str) -> dict:
    """Return the vendor_bills doc or raise 404 / 503."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        doc = db.get_collection("vendor_bills").find_one(
            {"bill_id": invoice_id}, {"_id": 0}
        )
    except Exception as exc:
        logger.error("[RECON] DB error fetching bill %s: %s", invoice_id, exc)
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    if not doc:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return doc


def _build_recon_block(existing: dict, body: ReconUpdate, actor_id: str, now: str) -> dict:
    """Merge the incoming tick-update into the existing recon sub-document."""
    recon = dict(existing) if existing else {}

    for flag in ("reconciled", "entered_tally", "filed_gst", "payment_settled"):
        value = getattr(body, flag)
        if value is None:
            continue  # not supplied -- leave existing unchanged
        recon[flag] = value
        # Per-flag audit stamp: <flag>_by / <flag>_at
        if value:
            recon[f"{flag}_by"] = actor_id
            recon[f"{flag}_at"] = now
        else:
            # Un-ticking a flag clears the audit stamps
            recon.pop(f"{flag}_by", None)
            recon.pop(f"{flag}_at", None)

    if body.note is not None:
        recon["note"] = body.note
        recon["note_by"] = actor_id
        recon["note_at"] = now

    recon["last_updated_by"] = actor_id
    recon["last_updated_at"] = now
    return recon


# ---------------------------------------------------------------------------
# POST /{invoice_id}/recon  -- write / update recon ticks
# ---------------------------------------------------------------------------


@router.post("/purchase-invoices/{invoice_id}/recon", status_code=200)
async def upsert_recon(
    invoice_id: str,
    body: ReconUpdate,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Write (or update) the recon sub-document on a purchase invoice.

    Idempotent: posting the same flags again just refreshes the audit stamps.
    Returns the updated recon block so the frontend can reflect it immediately.
    404 if the invoice does not exist; 503 if the DB is down.
    """
    db = _get_db()
    doc = _fetch_bill(db, invoice_id)

    actor_id = current_user.get("user_id") or current_user.get("id", "unknown")
    now = _now_iso()
    existing_recon = doc.get("recon") or {}
    new_recon = _build_recon_block(existing_recon, body, actor_id, now)

    try:
        db.get_collection("vendor_bills").update_one(
            {"bill_id": invoice_id},
            {"$set": {"recon": new_recon}},
        )
    except Exception as exc:
        logger.error("[RECON] DB error updating recon for %s: %s", invoice_id, exc)
        raise HTTPException(status_code=503, detail="Database unavailable") from exc

    return {"invoice_id": invoice_id, "recon": new_recon}


# ---------------------------------------------------------------------------
# GET /{invoice_id}/recon  -- read recon block
# ---------------------------------------------------------------------------


@router.get("/purchase-invoices/{invoice_id}/recon", status_code=200)
async def get_recon(
    invoice_id: str,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Return the recon sub-document for a purchase invoice.

    Returns an empty recon block (all flags False, no timestamps) when no
    reconciliation has been done yet.  404 if the invoice does not exist.
    """
    db = _get_db()
    doc = _fetch_bill(db, invoice_id)
    recon = doc.get("recon") or {}
    # Ensure all 4 flag keys are present for a predictable frontend shape
    for flag in ("reconciled", "entered_tally", "filed_gst", "payment_settled"):
        recon.setdefault(flag, False)
    return {"invoice_id": invoice_id, "recon": recon}


# ---------------------------------------------------------------------------
# GET /recon/worklists  -- accountant console worklists
# ---------------------------------------------------------------------------

# PO statuses that still have goods to arrive (mirrors vendors.py _RECEIVABLE_PO_STATUSES)
_RECEIVABLE_PO_STATUSES = {"APPROVED", "PARTIALLY_RECEIVED", "PENDING"}


def _safe_list(db, collection: str, query: dict, limit: int = 200) -> list:
    """Tolerant, fail-soft query: returns [] when collection is absent or query errors."""
    if db is None:
        return []
    try:
        return list(
            db.get_collection(collection)
            .find(query, {"_id": 0})
            .sort("created_at", -1)
            .limit(limit)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RECON] fail-soft query on %s: %s", collection, exc)
        return []


def _stock_yet_to_receive(db, store_id: Optional[str]) -> list:
    """Open POs with at least one unreceived line.  Mirrors the residual logic
    in vendors.py::goods_receipt_cockpit, summarised per PO (not per-vendor)."""
    if db is None:
        return []
    flt: dict = {"status": {"$in": list(_RECEIVABLE_PO_STATUSES)}}
    if store_id:
        flt["delivery_store_id"] = store_id
    try:
        pos = list(
            db.get_collection("purchase_orders")
            .find(flt, {"_id": 0})
            .sort("created_at", -1)
            .limit(300)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RECON] fail-soft query on purchase_orders: %s", exc)
        return []

    result = []
    for po in pos:
        header_recv = po.get("received_qty_by_product") or {}
        total_pending = 0
        open_lines = []
        for it in (po.get("items") or []):
            pid = it.get("product_id")
            ordered = it.get("ordered_qty", it.get("quantity", 0)) or 0
            recv = it.get("received_qty")
            if recv is None:
                recv = header_recv.get(pid, 0)
            recv = recv or 0
            if ordered and recv < ordered:
                residual = ordered - recv
                total_pending += residual
                open_lines.append(
                    {
                        "product_id": pid,
                        "product_name": it.get("product_name"),
                        "sku": it.get("sku"),
                        "ordered_qty": ordered,
                        "received_qty": recv,
                        "pending_qty": residual,
                    }
                )
        if open_lines:
            result.append(
                {
                    "po_id": po.get("po_id"),
                    "po_number": po.get("po_number"),
                    "vendor_id": po.get("vendor_id"),
                    "status": po.get("status"),
                    "expected_date": po.get("expected_date"),
                    "delivery_store_id": po.get("delivery_store_id"),
                    "total_pending_qty": total_pending,
                    "open_lines": open_lines,
                }
            )
    return result


def _pending_return_credit_notes(db, store_id: Optional[str]) -> list:
    """Vendor return CNs not yet received/applied.
    A return is 'open' if its status is in _OPEN_RETURN_STATUSES and
    return_type == 'credit_note' (replacements are tracked separately).
    """
    if db is None:
        return []
    flt: dict = {
        "status": {"$in": list(_OPEN_RETURN_STATUSES)},
        "return_type": "credit_note",
    }
    if store_id:
        flt["store_id"] = store_id
    rows = _safe_list(db, "vendor_returns", flt)
    out = []
    for r in rows:
        out.append(
            {
                "return_id": r.get("return_id"),
                "vendor_id": r.get("vendor_id"),
                "vendor_name": r.get("vendor_name"),
                "store_id": r.get("store_id"),
                "status": r.get("status"),
                "total_value": r.get("total_value"),
                "credit_note_number": r.get("credit_note_number"),
                "created_at": r.get("created_at"),
            }
        )
    return out


def _pending_vendor_returns_open(db, store_id: Optional[str]) -> list:
    """All open/in-flight vendor returns (both credit_note + replacement types)."""
    if db is None:
        return []
    flt: dict = {"status": {"$in": list(_OPEN_RETURN_STATUSES)}}
    if store_id:
        flt["store_id"] = store_id
    rows = _safe_list(db, "vendor_returns", flt)
    out = []
    for r in rows:
        out.append(
            {
                "return_id": r.get("return_id"),
                "vendor_id": r.get("vendor_id"),
                "vendor_name": r.get("vendor_name"),
                "store_id": r.get("store_id"),
                "return_type": r.get("return_type"),
                "status": r.get("status"),
                "total_value": r.get("total_value"),
                "credit_note_number": r.get("credit_note_number"),
                "created_at": r.get("created_at"),
            }
        )
    return out


def _pending_scheme_cns(db, vendor_id: Optional[str]) -> list:
    """Scheme / volume-rebate credit notes that have not been marked received.
    These live in ``vendor_debit_notes`` (CREDIT_NOTES_COLLECTION in rebate_engine.py)
    with ``source == 'VOLUME_REBATE'``.  A CN is 'pending' if it has no
    ``cn_received_at`` field (the accountant physically receives the paper CN from
    the vendor and ticks it here; we do not currently auto-set this flag, so all
    VOLUME_REBATE CNs without cn_received_at are shown as pending).
    """
    if db is None:
        return []
    flt: dict = {
        "source": "VOLUME_REBATE",
        "cn_received_at": {"$exists": False},
    }
    if vendor_id:
        flt["vendor_id"] = vendor_id
    rows = _safe_list(db, "vendor_debit_notes", flt)
    out = []
    for r in rows:
        out.append(
            {
                "credit_note_number": r.get("credit_note_number"),
                "vendor_id": r.get("vendor_id"),
                "vendor_name": r.get("vendor_name"),
                "amount": r.get("amount"),
                "amount_paise": r.get("amount_paise"),
                "rebate_id": r.get("rebate_id"),
                "created_at": r.get("created_at"),
            }
        )
    # backlog #4: resolve vendor_id -> name where the CN didn't store one.
    try:
        from ..services.name_resolver import vendor_name_map

        vmap = vendor_name_map(db, [o.get("vendor_id") for o in out if not o.get("vendor_name")])
        for o in out:
            vid = o.get("vendor_id")
            if not o.get("vendor_name") and vid and str(vid) in vmap:
                o["vendor_name"] = vmap[str(vid)]
    except Exception:  # noqa: BLE001
        pass
    return out


@router.get("/recon/worklists", status_code=200)
async def get_recon_worklists(
    store_id: Optional[str] = Query(None, description="Filter by store (optional)"),
    vendor_id: Optional[str] = Query(None, description="Filter scheme CNs by vendor"),
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Accountant reconciliation console -- 4 worklists in one payload.

    All lists are fail-soft: if the underlying collection is absent or the DB is
    down, the list is returned empty (never a 500) so the console stays usable.

    Lists:
      stock_yet_to_receive        -- open POs with unreceived lines, per PO
      vendor_returns              -- open / in-flight vendor returns (all types)
      pending_credit_notes_scheme -- scheme/rebate CNs not yet received (VOLUME_REBATE)
      pending_credit_notes_return -- open vendor-return CNs not yet issued
    """
    db = _get_db()  # May be None when DB is down; each helper handles that.

    stock_yet_to_receive = _stock_yet_to_receive(db, store_id)
    vendor_returns = _pending_vendor_returns_open(db, store_id)
    pending_cns_scheme = _pending_scheme_cns(db, vendor_id)
    pending_cns_return = _pending_return_credit_notes(db, store_id)

    return {
        "stock_yet_to_receive": stock_yet_to_receive,
        "vendor_returns": vendor_returns,
        "pending_credit_notes_scheme": pending_cns_scheme,
        "pending_credit_notes_return": pending_cns_return,
    }


@router.post("/recon/credit-notes/{credit_note_number}/mark-received", status_code=200)
async def mark_scheme_cn_received(
    credit_note_number: str,
    current_user: dict = Depends(require_roles(*_AP_ROLES)),
):
    """Mark a scheme / volume-rebate credit note as physically RECEIVED.

    Closes the loop on the 'pending scheme CNs' worklist: that list shows every
    VOLUME_REBATE credit note WITHOUT a ``cn_received_at`` field; without a way
    to set it, those rows would pile up forever (honest-but-un-actionable). The
    accountant, on receiving the paper CN from the vendor, ticks it here -> the
    row drops off the worklist. Idempotent (re-marking just refreshes the stamp).
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    coll = db.get_collection("vendor_debit_notes")
    flt = {"credit_note_number": credit_note_number, "source": "VOLUME_REBATE"}
    try:
        existing = coll.find_one(flt, {"_id": 0})
    except Exception as exc:  # noqa: BLE001
        logger.error("[RECON] DB error finding scheme CN %s: %s", credit_note_number, exc)
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    if not existing:
        raise HTTPException(status_code=404, detail="Scheme credit note not found")
    now = _now_iso()
    try:
        coll.update_one(
            flt,
            {
                "$set": {
                    "cn_received_at": now,
                    "cn_received_by": current_user.get("user_id"),
                }
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[RECON] DB error marking scheme CN %s received: %s",
            credit_note_number,
            exc,
        )
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return {
        "credit_note_number": credit_note_number,
        "cn_received_at": now,
        "received": True,
    }
