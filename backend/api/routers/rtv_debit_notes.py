"""
IMS 2.0 - F20 RTV Debit Note Router
====================================
The GST-compliant DEBIT NOTE document issued to a vendor when goods are returned
(the physical RTV / N4 RMA / vendor_return already moved the stock + minted a
credit-note number). This router is an ACCOUNTING / DOCUMENT layer on top of the
existing return -- it does NOT touch the RMA state machine nor stock movement.

It REUSES (no fork):
  * the existing ``vendor_returns`` / ``vendor_rmas`` records as the source RTV,
  * the FY-scoped atomic serial + CGST/SGST/IGST split + Tally Debit-Note voucher
    from ``services/rtv_debit_note`` (which themselves mirror the sales-invoice
    serial + splitter + the Receipt-voucher builder),
  * the vendor/AP RBAC role set + the store-IDOR guard (validate_store_access /
    resolve_store_scope) from vendor_returns / vendor_rma.

RBAC: ADMIN / AREA_MANAGER / STORE_MANAGER / ACCOUNTANT (+ SUPERADMIN via
require_roles) may issue / export. A cashier / sales / workshop / optometrist can
NEVER issue a debit note. GET list/detail/print are AUTHENTICATED but store-scoped
per object in the handler.

No comms. No emoji (Windows cp1252). Money is paise-exact integers; responses
carry both paise and a rupee display field.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .auth import get_current_user, require_roles
from ..dependencies import (
    get_db,
    resolve_store_scope,
    validate_store_access,
    user_store_scope,
)
from ..services.rtv_debit_note import (
    DebitNoteEngine,
    paise_to_rupees,
    render_debit_note_html,
    tally_build_debit_note_xml,
)

logger = logging.getLogger(__name__)

# Same vendor/AP role set vendor_returns / vendor_rma hardened to. A debit note is
# a financial instrument against a vendor; juniors are excluded. SUPERADMIN passes
# via require_roles.
_DEBIT_NOTE_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT")

router = APIRouter()


# ============================================================================
# DB helpers (mirror vendor_returns / vendor_rma)
# ============================================================================


def _get_db():
    try:
        conn = get_db()
        if conn is not None and hasattr(conn, "db"):
            return conn.db
        if conn is not None and hasattr(conn, "client"):
            return conn.client.ims_db
    except Exception:  # noqa: BLE001
        pass
    return None


def _engine() -> DebitNoteEngine:
    return DebitNoteEngine(db=_get_db())


def _load_vendor(db, vendor_id: Optional[str]) -> dict:
    """Best-effort vendor lookup for the debit-note recipient block (GSTIN /
    state / address). Returns {} when absent -- the builder defaults safely."""
    if db is None or not vendor_id:
        return {}
    try:
        v = db.get_collection("vendors").find_one({"vendor_id": vendor_id}) or {}
        v.pop("_id", None)
        return v
    except Exception:  # noqa: BLE001
        return {}


def _load_seller(db, store_id: Optional[str], entity_id: Optional[str]) -> dict:
    """Best-effort issuing-entity (seller) block: name / GSTIN / state / address.
    Resolves the entity via store.entity_id when entity_id isn't given."""
    if db is None:
        return {}
    store = {}
    if store_id:
        try:
            store = db.get_collection("stores").find_one({"store_id": store_id}) or {}
        except Exception:  # noqa: BLE001
            store = {}
    eid = entity_id or store.get("entity_id")
    ent = {}
    if eid:
        try:
            ent = db.get_collection("entities").find_one({"entity_id": eid}) or {}
        except Exception:  # noqa: BLE001
            ent = {}
    return {
        "entity_id": eid,
        "name": ent.get("legal_name") or ent.get("name") or store.get("name") or "",
        "gstin": (ent.get("gstin") or store.get("gstin") or ""),
        "state_code": ent.get("state_code") or store.get("state_code") or "",
        "address": ent.get("address") or store.get("address") or "",
    }


def _load_rtv(db, source_type: str, rtv_id: str) -> Optional[dict]:
    """Load the source RTV (vendor_return or vendor_rma) by id. Returns None if
    absent. The two collections share store_id + vendor_id shape."""
    if db is None or not rtv_id:
        return None
    coll_name = "vendor_rmas" if source_type == "vendor_rma" else "vendor_returns"
    key = "rma_id" if source_type == "vendor_rma" else "return_id"
    try:
        doc = db.get_collection(coll_name).find_one({key: rtv_id})
        if doc:
            doc.pop("_id", None)
        return doc
    except Exception:  # noqa: BLE001
        return None


# ============================================================================
# SCHEMAS
# ============================================================================


class DebitNoteIssue(BaseModel):
    # The source RTV: either an existing vendor_return or a vendor_rma.
    source_type: str = Field("vendor_return", pattern="^(vendor_return|vendor_rma)$")
    rtv_id: str = Field(..., min_length=1, max_length=120)


# ============================================================================
# Response shaping
# ============================================================================


def _with_rupees(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return doc
    out = dict(doc)
    totals = dict(doc.get("totals") or {})
    out["totals_rupees"] = {
        "taxable": paise_to_rupees(totals.get("taxable_paise")),
        "cgst": paise_to_rupees(totals.get("cgst_paise")),
        "sgst": paise_to_rupees(totals.get("sgst_paise")),
        "igst": paise_to_rupees(totals.get("igst_paise")),
        "tax": paise_to_rupees(totals.get("tax_paise")),
        "grand_total": paise_to_rupees(totals.get("grand_total_paise")),
    }
    return out


def _http_from_result(res: dict) -> HTTPException:
    code = int(res.get("http", 400))
    detail = {k: v for k, v in res.items() if k not in ("ok", "http", "debit_note")}
    return HTTPException(status_code=code, detail=detail or "request failed")


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("")
@router.get("/")
async def list_debit_notes(
    store_id: Optional[str] = Query(None),
    vendor_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """List issued debit notes. Store-scoped: an explicit ?store_id is validated;
    a store-role caller is pinned to their reach; HQ roles see all."""
    eng = _engine()
    scoped = resolve_store_scope(store_id, current_user)
    is_cross, reach = user_store_scope(current_user)
    if scoped:
        rows = eng.list(store_id=scoped, vendor_id=vendor_id, skip=skip, limit=limit)
    elif is_cross:
        rows = eng.list(vendor_id=vendor_id, skip=skip, limit=limit)
    else:
        rows = eng.list(store_ids=list(reach), vendor_id=vendor_id, skip=skip, limit=limit)
    return {"debit_notes": [_with_rupees(r) for r in rows], "total": len(rows)}


@router.post("/issue", status_code=201)
async def issue_debit_note(
    body: DebitNoteIssue,
    current_user: dict = Depends(require_roles(*_DEBIT_NOTE_ROLES)),
):
    """Issue the GST debit note for an existing RTV (vendor_return / vendor_rma).
    Idempotent: re-issuing for the same RTV returns the first note (no new
    serial). Store-IDOR guarded on the source RTV's store."""
    db = _get_db()
    eng = _engine()
    if eng._coll() is None:  # noqa: SLF001 - intentional fail-soft DB probe
        raise HTTPException(status_code=503, detail="Database not available")

    rtv = _load_rtv(db, body.source_type, body.rtv_id)
    if rtv is None:
        raise HTTPException(status_code=404, detail="source RTV not found")
    validate_store_access(rtv.get("store_id"), current_user)

    vendor = _load_vendor(db, rtv.get("vendor_id"))
    seller = _load_seller(db, rtv.get("store_id"), rtv.get("entity_id"))
    res = eng.issue(
        rtv,
        vendor,
        actor=current_user.get("user_id"),
        seller=seller,
    )
    if not res.get("ok"):
        raise _http_from_result(res)
    return {
        "idempotent": bool(res.get("idempotent")),
        "debit_note": _with_rupees(res.get("debit_note")),
    }


@router.get("/{debit_note_id}")
async def get_debit_note(
    debit_note_id: str, current_user: dict = Depends(get_current_user)
):
    """Get a single debit note. Store-IDOR guarded (cross-store -> 403)."""
    eng = _engine()
    doc = eng.get(debit_note_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Debit note not found")
    validate_store_access(doc.get("store_id"), current_user)
    return _with_rupees(doc)


@router.get("/{debit_note_id}/print", response_class=HTMLResponse)
async def print_debit_note(
    debit_note_id: str, current_user: dict = Depends(get_current_user)
):
    """Printable GST debit-note HTML. Store-IDOR guarded."""
    eng = _engine()
    doc = eng.get(debit_note_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Debit note not found")
    validate_store_access(doc.get("store_id"), current_user)
    return HTMLResponse(content=render_debit_note_html(doc))


@router.get("/{debit_note_id}/tally", response_class=PlainTextResponse)
async def export_debit_note_tally(
    debit_note_id: str,
    current_user: dict = Depends(require_roles(*_DEBIT_NOTE_ROLES)),
):
    """Tally import XML carrying the Debit Note voucher (balanced; debits ==
    credits). Vendor/AP roles only. Store-IDOR guarded."""
    eng = _engine()
    doc = eng.get(debit_note_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Debit note not found")
    validate_store_access(doc.get("store_id"), current_user)
    try:
        xml = tally_build_debit_note_xml(doc)
    except ValueError as e:
        # An unbalanced voucher fails loudly (would be rejected by Tally anyway).
        raise HTTPException(status_code=409, detail={"error": str(e)})
    return PlainTextResponse(content=xml, media_type="application/xml")
