"""
IMS 2.0 - N4 Vendor RMA Router
===============================
Return Merchandise Authorization against a vendor: raise -> authorize (record the
vendor's RMA number) -> dispatch (courier/AWB) -> reconcile vendor credit notes
(expected vs received, paisa-exact, partial supported) -> close.

This is the RMA lifecycle that ``vendor_returns.py`` (the lighter RTV/debit-note
+ F21 quarantine-link path) did NOT cover. It does NOT fork vendor_returns -- the
lifecycle engine lives in ``api/services/vendor_rma.py`` and this router only
wires RBAC, the store-IDOR guard, and the E4 maker-checker seam.

RBAC: same vendor/AP role set as vendor_returns (ADMIN / AREA_MANAGER /
STORE_MANAGER / ACCOUNTANT + SUPERADMIN via require_roles). A cashier / sales /
workshop / optometrist role can NEVER authorize an RMA or record a credit.

Store scope: every write validates ``validate_store_access`` on the RMA's store
(mirrors the merged vendor_returns IDOR guard); the list endpoint uses
``resolve_store_scope``.

Money: paisa-exact integers end-to-end. Rupee inputs are converted at the schema
edge; responses carry both the paise integer and a rupee float for display.

E4: a credit whose received amount (or variance) crosses the E2-configured tier
must carry a consumed approval token (action_type "rtv"); below the threshold the
credit is recorded directly.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import get_current_user, require_roles
from ..dependencies import get_db, resolve_store_scope, validate_store_access, user_store_scope
from ..services.vendor_rma import (
    VendorRMAEngine,
    RMA_REASONS,
    credit_requires_approval,
    paise_to_rupees,
    rupees_to_paise,
)

logger = logging.getLogger(__name__)

# Same vendor/AP role set vendor_returns hardened to. A vendor RMA + its credit
# note are financial instruments against a vendor; juniors (cashier/sales/
# workshop/optometrist/catalog) are excluded. SUPERADMIN passes via require_roles.
_VENDOR_RMA_ROLES = ("ADMIN", "AREA_MANAGER", "STORE_MANAGER", "ACCOUNTANT")

router = APIRouter()


# ============================================================================
# DB helper (mirrors vendor_returns._get_db)
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


def _engine() -> VendorRMAEngine:
    return VendorRMAEngine(db=_get_db())


# ============================================================================
# SCHEMAS
# ============================================================================

# Reason values are enforced against the service taxonomy; expose them as a
# Literal so the OpenAPI doc + FastAPI validation reject garbage at the edge.
RMAReason = Literal["DEFECTIVE", "WRONG", "EXCESS", "WARRANTY", "NON_ADAPT"]


class RMALineCreate(BaseModel):
    product_id: str
    product_name: str
    # Bounded qty / cost so a zero/negative/garbage value can't produce a bogus
    # (or negative) expected-credit amount (mirrors vendor_returns guards).
    quantity: int = Field(..., gt=0, le=100000)
    reason: RMAReason
    # Rupee unit cost; converted to integer paise at the service edge.
    unit_cost: float = Field(..., ge=0, le=10_000_000)


class RMACreate(BaseModel):
    vendor_id: str
    vendor_name: str
    store_id: str
    lines: List[RMALineCreate] = Field(..., min_length=1)
    notes: Optional[str] = None
    po_id: Optional[str] = None
    grn_id: Optional[str] = None
    return_id: Optional[str] = None


class RMAAuthorize(BaseModel):
    # The vendor's RMA authorization number the goods must travel under.
    vendor_rma_number: str = Field(..., min_length=1, max_length=120)
    notes: Optional[str] = None


class RMADispatch(BaseModel):
    carrier: str = Field(..., min_length=1, max_length=120)
    awb: str = Field(..., min_length=1, max_length=120)
    dispatch_date: Optional[str] = None  # ISO date/datetime; defaults to now
    notes: Optional[str] = None


class RMACreditNote(BaseModel):
    credit_note_number: str = Field(..., min_length=1, max_length=120)
    # Rupee amount the vendor actually credited on THIS note; converted to paise.
    received_amount: float = Field(..., ge=0, le=100_000_000)
    notes: Optional[str] = None
    # E4 maker-checker token, required only when the credit crosses a tier.
    approval_token: Optional[str] = None
    approval_request_id: Optional[str] = None


class RMAReject(BaseModel):
    reason: Optional[str] = None


class RMAClose(BaseModel):
    notes: Optional[str] = None
    # Force-close with an outstanding positive variance (audited write-off).
    write_off_variance: bool = False


# ============================================================================
# Response shaping
# ============================================================================


def _with_rupees(doc: dict) -> dict:
    """Add rupee display fields alongside the authoritative paise integers."""
    if not doc:
        return doc
    out = dict(doc)
    out["expected_credit_rupees"] = paise_to_rupees(doc.get("expected_credit_paise"))
    out["received_credit_rupees"] = paise_to_rupees(doc.get("received_credit_paise"))
    out["variance_rupees"] = paise_to_rupees(doc.get("variance_paise"))
    return out


def _http_from_result(res: dict) -> HTTPException:
    """Map an engine fail result to an HTTPException, defaulting to 400."""
    code = int(res.get("http", 400))
    detail = {k: v for k, v in res.items() if k not in ("ok", "http")}
    return HTTPException(status_code=code, detail=detail or "request failed")


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("")
@router.get("/")
async def list_rmas(
    store_id: Optional[str] = Query(None),
    vendor_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """List vendor RMAs. Store-scoped: an explicit ?store_id is validated; a
    store-role caller is pinned to their own reach; HQ roles see all."""
    eng = _engine()
    # Authorise + resolve the store filter (raises 403 on a cross-store ?store_id).
    scoped = resolve_store_scope(store_id, current_user)
    is_cross, reach = user_store_scope(current_user)
    if scoped:
        rows = eng.list(store_id=scoped, vendor_id=vendor_id, status=status,
                        skip=skip, limit=limit)
    elif is_cross:
        rows = eng.list(vendor_id=vendor_id, status=status, skip=skip, limit=limit)
    else:
        # Defensive: a store-role with no resolved store sees only its reach.
        rows = eng.list(store_ids=list(reach), vendor_id=vendor_id, status=status,
                        skip=skip, limit=limit)
    return {"rmas": [_with_rupees(r) for r in rows], "total": len(rows)}


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def raise_rma(
    body: RMACreate,
    current_user: dict = Depends(require_roles(*_VENDOR_RMA_ROLES)),
):
    """Raise a DRAFT RMA against a vendor with line items. Vendor/AP roles only.
    Store-IDOR guarded on the RMA's store."""
    validate_store_access(body.store_id, current_user)
    eng = _engine()
    if eng._coll() is None:  # noqa: SLF001 - intentional fail-soft DB probe
        raise HTTPException(status_code=503, detail="Database not available")
    lines = [
        {
            "product_id": ln.product_id,
            "product_name": ln.product_name,
            "quantity": ln.quantity,
            "reason": ln.reason,
            "unit_cost": ln.unit_cost,
        }
        for ln in body.lines
    ]
    res = eng.raise_rma(
        vendor_id=body.vendor_id,
        vendor_name=body.vendor_name,
        store_id=body.store_id,
        lines=lines,
        created_by=current_user.get("user_id"),
        notes=body.notes or "",
        po_id=body.po_id,
        grn_id=body.grn_id,
        return_id=body.return_id,
    )
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "create failed"))
    return res


@router.get("/{rma_id}")
async def get_rma(rma_id: str, current_user: dict = Depends(get_current_user)):
    """Get a single RMA. Store-IDOR guarded (cross-store -> 403)."""
    eng = _engine()
    doc = eng.get(rma_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="RMA not found")
    validate_store_access(doc.get("store_id"), current_user)
    return _with_rupees(doc)


@router.post("/{rma_id}/authorize")
async def authorize_rma(
    rma_id: str,
    body: RMAAuthorize,
    current_user: dict = Depends(require_roles(*_VENDOR_RMA_ROLES)),
):
    """Record the vendor's RMA authorization number; DRAFT -> AUTHORIZED."""
    eng = _engine()
    doc = eng.get(rma_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="RMA not found")
    validate_store_access(doc.get("store_id"), current_user)
    res = eng.authorize(
        rma_id,
        vendor_rma_number=body.vendor_rma_number,
        actor=current_user.get("user_id"),
        notes=body.notes or "",
    )
    if not res.get("ok"):
        raise _http_from_result(res)
    return res


@router.post("/{rma_id}/dispatch")
async def dispatch_rma(
    rma_id: str,
    body: RMADispatch,
    current_user: dict = Depends(require_roles(*_VENDOR_RMA_ROLES)),
):
    """Record courier dispatch (carrier / AWB / date); AUTHORIZED -> DISPATCHED."""
    eng = _engine()
    doc = eng.get(rma_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="RMA not found")
    validate_store_access(doc.get("store_id"), current_user)
    res = eng.dispatch(
        rma_id,
        carrier=body.carrier,
        awb=body.awb,
        dispatch_date=body.dispatch_date,
        actor=current_user.get("user_id"),
        notes=body.notes or "",
    )
    if not res.get("ok"):
        raise _http_from_result(res)
    return res


@router.post("/{rma_id}/credit-note")
async def record_credit_note(
    rma_id: str,
    body: RMACreditNote,
    current_user: dict = Depends(require_roles(*_VENDOR_RMA_ROLES)),
):
    """Record a vendor credit note against a DISPATCHED RMA, reconciling
    expected-vs-received (paisa-exact, partial supported). A credit (or variance)
    over the E2 tier requires a consumed E4 approval token (action_type 'rtv')."""
    eng = _engine()
    doc = eng.get(rma_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="RMA not found")
    validate_store_access(doc.get("store_id"), current_user)

    recv_paise = rupees_to_paise(body.received_amount)
    expected = int(doc.get("expected_credit_paise") or 0)
    received_so_far = int(doc.get("received_credit_paise") or 0)
    projected_variance = expected - (received_so_far + recv_paise)

    # E4 maker-checker: a large credit (or large variance) must be approved.
    if credit_requires_approval(recv_paise, projected_variance,
                                store_id=doc.get("store_id")):
        token = (body.approval_token or "").strip() or None
        request_id = (body.approval_request_id or "").strip() or None
        if not token and not request_id:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "approval_required",
                    "message": "This credit exceeds the maker-checker threshold; "
                               "obtain an E4 approval (action_type 'rtv') and supply "
                               "approval_token.",
                },
            )
        if not _consume_rtv_approval(token, request_id, recv_paise, current_user):
            raise HTTPException(
                status_code=403,
                detail={"error": "approval_invalid",
                        "message": "approval token missing / expired / wrong tier"},
            )

    res = eng.record_credit_note(
        rma_id,
        credit_note_number=body.credit_note_number,
        received_amount=body.received_amount,
        actor=current_user.get("user_id"),
        notes=body.notes or "",
        approval_token=(body.approval_token or "").strip() or None,
    )
    if not res.get("ok"):
        raise _http_from_result(res)
    return res


@router.post("/{rma_id}/reject")
async def reject_rma(
    rma_id: str,
    body: RMAReject,
    current_user: dict = Depends(require_roles(*_VENDOR_RMA_ROLES)),
):
    """Reject an RMA from DRAFT / AUTHORIZED / DISPATCHED (terminal)."""
    eng = _engine()
    doc = eng.get(rma_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="RMA not found")
    validate_store_access(doc.get("store_id"), current_user)
    res = eng.reject(rma_id, actor=current_user.get("user_id"), reason=body.reason or "")
    if not res.get("ok"):
        raise _http_from_result(res)
    return res


@router.post("/{rma_id}/close")
async def close_rma(
    rma_id: str,
    body: RMAClose,
    current_user: dict = Depends(require_roles(*_VENDOR_RMA_ROLES)),
):
    """Close a fully-reconciled CREDIT_RECEIVED RMA (or force-close an
    outstanding variance as an audited write-off). Refuses to silently close on
    a positive variance."""
    eng = _engine()
    doc = eng.get(rma_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="RMA not found")
    validate_store_access(doc.get("store_id"), current_user)
    res = eng.close_rma(
        rma_id,
        actor=current_user.get("user_id"),
        notes=body.notes or "",
        write_off_variance=bool(body.write_off_variance),
    )
    if not res.get("ok"):
        raise _http_from_result(res)
    return res


# ============================================================================
# E4 helper
# ============================================================================


def _consume_rtv_approval(token, request_id, amount_paise, current_user) -> bool:
    """Atomically consume an E4 approval of action_type 'rtv'. SAFE DEFAULT: any
    missing token, missing engine, or error -> False (the credit stays blocked).
    Reuses the EXISTING E4 consume_approval -- it does NOT reimplement approvals.
    The approval amount is in RUPEES (E4 stores amount in rupees); pass the
    rupee value so the engine's amount<=approved guard holds."""
    if not token and not request_id:
        return False
    try:
        from ..services.approvals import ApprovalEngine

        engine = ApprovalEngine(db=_get_db())
        res = engine.consume_approval(
            consumed_by=current_user.get("user_id") or "",
            action_type="rtv",
            request_id=request_id,
            approval_token=token,
            amount=round(int(amount_paise) / 100.0, 2),
        )
        return bool(res.get("ok"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[VENDOR_RMA] rtv approval consume failed: %s", exc)
        return False
