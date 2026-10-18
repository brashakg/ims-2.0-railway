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

Store scope: every by-id read and write asks ``in_reach_or_404`` (another
shop's RMA is the 404 a missing one gets, as a vendor return's is); a new RMA
validates its shop with ``validate_store_access``; the list endpoint uses
``resolve_store_scope``.

Money: paisa-exact integers end-to-end. Rupee inputs are converted at the schema
edge; responses carry both the paise integer and a rupee float for display.
Supplier money (owner ruling 2026-10-01): the expected / received credit, the
variance and any write-off are the accounts roles' alone -- every response
here (reads, write results, the close refusal) goes through
services/payables_mask.strip_rma_credit, which also drops each line's unit
cost (owner ruling 2026-10-12: a manager reads items, quantities and reasons).
RECORDING that money is the accounts roles' too (owner ruling 2026-10-12):
so is PRICING the lines the expected credit is built from (R2 + R3): a
manager raises an RMA without prices, a line price from anyone else is a 403,
and the accounts roles price it on PUT /{rma_id}/prices; a credit note on an
unpriced RMA is a 422. The credit-note route is require_roles(*AP_ROLES), and a close that writes off
a variance or a reject of a DISPATCHED RMA (which forgoes its whole expected
credit) is require_payables inline. The goods side -- raise, authorize,
dispatch, reject before dispatch, close without a write-off -- stays with the
purchase roles.

E4: a credit whose received amount (or variance) crosses the E2-configured tier
must carry a consumed approval token (action_type "rtv"); below the threshold the
credit is recorded directly. The approval must come from a second person:
since the recorder is an accounts role and the accounts roles approve 'rtv'
(owner ruling 2026-10-12, R1), the approval spent here must have been
PIN-approved by someone other than the recorder and other than whoever raised
the request (SYSTEM_INTENT "Requester CANNOT approve own request").
"""

from __future__ import annotations

import logging
from typing import Annotated, List, Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import require_roles
from ..dependencies import (
    get_db,
    in_reach_or_404,
    resolve_store_scope,
    validate_store_access,
    user_store_scope,
)
from ..services.cost_mask import PURCHASE_ROLES, mask_rma
from ..services.vendor_rma import (
    PRICE_REQUIRED,
    VendorRMAEngine,
    RMA_REASONS,
    credit_requires_approval,
    paise_to_rupees,
    rma_is_priced,
    rupees_to_paise,
    RMAStatus,
)
from ..services.rtv_debit_note import debit_note_issued
from ..services.payables_mask import AP_ROLES, require_payables, strip_rma_credit
from ..services import rma_supplier_credit
from ..services.supplier_credit_evidence import SupplierCreditError

logger = logging.getLogger(__name__)

# The purchase roles (services/cost_mask), as on vendor_returns. A vendor RMA +
# its credit note are financial instruments against a vendor; juniors (cashier/
# sales/workshop/optometrist/catalog) are excluded. SUPERADMIN passes via
# require_roles.
_VENDOR_RMA_ROLES = PURCHASE_ROLES

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
    # Rupee unit cost; converted to integer paise at the service edge. The
    # basis of the expected supplier credit: supplier money, the accounts
    # roles' to write as well as to read (owner rulings 2026-10-12 R2 + R3).
    # A manager raises the RMA without it; an accounts role prices it here or
    # later (PUT /{rma_id}/prices).
    unit_cost: Optional[float] = Field(None, ge=0, le=10_000_000)


class RMACreate(BaseModel):
    vendor_id: str
    vendor_name: str
    store_id: str
    lines: List[RMALineCreate] = Field(..., min_length=1)
    notes: Optional[str] = None
    po_id: Optional[str] = None
    grn_id: Optional[str] = None
    return_id: Optional[str] = None


class RMAPrices(BaseModel):
    # One rupee price per piece for each of the RMA's lines, in their order.
    unit_costs: List[Annotated[float, Field(ge=0, le=10_000_000)]] = Field(..., min_length=1)


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
    credit_note_date: str = Field(..., min_length=10, max_length=10,
                                  description="Actual supplier credit-note date, YYYY-MM-DD")
    idempotency_key: str = Field(..., min_length=36, max_length=36,
                                 description="One UUID per supplier note; reuse unchanged on retry")
    # Rupee amount the vendor actually credited on THIS note; converted to paise.
    received_amount: float = Field(..., ge=0, le=100_000_000)
    notes: Optional[str] = None
    source_selections: List[dict] = Field(default_factory=list)
    allocations: List[dict] = Field(default_factory=list)
    # E4 maker-checker token, required only when the credit crosses a tier.
    approval_token: Optional[str] = None
    approval_request_id: Optional[str] = None


class RMACreditPreview(BaseModel):
    confirmation_id: Optional[str] = None
    source_selections: Optional[List[dict]] = None


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


def _masked(doc: dict, user: dict) -> dict:
    # An RMA's unit costs, credits and write-off are what we pay: read back by
    # the accounts roles only (owner ruling 2026-10-12, services/cost_mask).
    result = mask_rma(_with_rupees(doc), user)
    result.pop("credit_confirmation_fence", None)
    return result


def _http_from_result(res: dict, user: dict) -> HTTPException:
    """Map an engine fail result to an HTTPException, defaulting to 400. The
    detail goes through the same mask: a refused close names the variance."""
    code = int(res.get("http", 400))
    detail = mask_rma({k: v for k, v in res.items() if k not in ("ok", "http")}, user)
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
    current_user: dict = Depends(require_roles(*_VENDOR_RMA_ROLES)),
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
    return {
        "rmas": [_masked(r, current_user) for r in rows],
        "total": len(rows),
    }


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def raise_rma(
    body: RMACreate,
    current_user: dict = Depends(require_roles(*_VENDOR_RMA_ROLES)),
):
    """Raise a DRAFT RMA against a vendor with line items. Vendor/AP roles only.
    Store-IDOR guarded on the RMA's store."""
    validate_store_access(body.store_id, current_user)
    # A price on a line records the supplier money the expected credit is
    # built from: the accounts roles' alone (R2 + R3). A manager sends none.
    if any(ln.unit_cost is not None for ln in body.lines):
        require_payables(current_user, "Pricing an RMA")
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
    return mask_rma(res, current_user)


@router.get("/{rma_id}")
async def get_rma(
    rma_id: str, current_user: dict = Depends(require_roles(*_VENDOR_RMA_ROLES))
):
    """Get a single RMA. Another shop's is the 404 a missing one gets."""
    eng = _engine()
    doc = in_reach_or_404(eng.get(rma_id), current_user, "RMA not found")
    return _masked(doc, current_user)


@router.post("/{rma_id}/authorize")
async def authorize_rma(
    rma_id: str,
    body: RMAAuthorize,
    current_user: dict = Depends(require_roles(*_VENDOR_RMA_ROLES)),
):
    """Record the vendor's RMA authorization number; DRAFT -> AUTHORIZED."""
    eng = _engine()
    doc = in_reach_or_404(eng.get(rma_id), current_user, "RMA not found")
    res = eng.authorize(
        rma_id,
        vendor_rma_number=body.vendor_rma_number,
        actor=current_user.get("user_id"),
        notes=body.notes or "",
    )
    if not res.get("ok"):
        raise _http_from_result(res, current_user)
    return mask_rma(res, current_user)


@router.post("/{rma_id}/dispatch")
async def dispatch_rma(
    rma_id: str,
    body: RMADispatch,
    current_user: dict = Depends(require_roles(*_VENDOR_RMA_ROLES)),
):
    """Record courier dispatch (carrier / AWB / date); AUTHORIZED -> DISPATCHED."""
    eng = _engine()
    doc = in_reach_or_404(eng.get(rma_id), current_user, "RMA not found")
    res = eng.dispatch(
        rma_id,
        carrier=body.carrier,
        awb=body.awb,
        dispatch_date=body.dispatch_date,
        actor=current_user.get("user_id"),
        notes=body.notes or "",
    )
    if not res.get("ok"):
        raise _http_from_result(res, current_user)
    return mask_rma(res, current_user)


@router.post("/{rma_id}/credit-preview")
async def preview_rma_credit(
    rma_id: str,
    body: RMACreditPreview,
    current_user: dict = Depends(require_roles(*AP_ROLES)),
):
    """Verified original bills and actual-note retry history; Accounts only."""
    require_payables(current_user, "Previewing supplier credit")
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Supplier credit data is unavailable.")
    doc = in_reach_or_404(_engine().get(rma_id), current_user, "RMA not found")
    cross, reach = user_store_scope(current_user)
    try:
        return rma_supplier_credit.preview(
            db, doc, selections=body.source_selections, confirmation_id=body.confirmation_id,
            allowed_store_ids=None if cross else set(reach))
    except SupplierCreditError as exc:
        raise HTTPException(status_code=exc.status, detail={"error": exc.code, "message": str(exc)}) from exc
    except Exception as exc:
        logger.warning("[VENDOR_RMA] credit preview unavailable", exc_info=True)
        raise HTTPException(status_code=503, detail="Original supplier credit evidence is unavailable.") from exc


@router.post("/{rma_id}/credit-note")
async def record_credit_note(
    rma_id: str,
    body: RMACreditNote,
    current_user: dict = Depends(require_roles(*AP_ROLES)),
):
    """Confirm the supplier's actual note, retaining the RMA maker-checker seam."""
    require_payables(current_user, "Recording a supplier credit note")
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Supplier credit data is unavailable.")
    doc = in_reach_or_404(_engine().get(rma_id), current_user, "RMA not found")
    payload = body.model_dump(exclude={"approval_token", "approval_request_id"})
    cross, reach = user_store_scope(current_user)
    try:
        submitted = rma_supplier_credit.canonical._input(payload, allow_zero=True)
        existing = rma_supplier_credit.existing_confirmation(db, doc, submitted)
        acknowledgement = rma_supplier_credit.existing_acknowledgement(doc, submitted)
        if acknowledgement:
            return mask_rma(rma_supplier_credit.record_zero(
                db, doc, submitted, actor_id=current_user.get("user_id")), current_user)
        if submitted["received_paise"] == 0 and submitted["allocations"]:
            raise SupplierCreditError("ZERO_ACKNOWLEDGEMENT_ONLY", "A zero acknowledgement cannot contain credit allocations.")
        if not existing or existing.get("posting_status") != "POSTED":
            if not rma_is_priced(doc):
                raise _http_from_result(dict(PRICE_REQUIRED), current_user)
            if doc.get("status") not in rma_supplier_credit.READY:
                raise HTTPException(status_code=409, detail={"error": "invalid_transition", "status": doc.get("status")})
            recv_paise = submitted["received_paise"]
            projected_variance = int(doc["expected_credit_paise"]) - int(doc.get("received_credit_paise") or 0) - recv_paise
            if credit_requires_approval(recv_paise, projected_variance, store_id=doc.get("store_id")):
                token = (body.approval_token or "").strip() or None
                request_id = (body.approval_request_id or "").strip() or None
                rma_supplier_credit.require_approval(
                    db, doc, submitted, request_id=request_id, token=token,
                    actor_id=current_user.get("user_id"),
                    consume=lambda: _consume_rtv_approval(
                        token, request_id, recv_paise, current_user,
                        store_id=doc.get("store_id"), rma_id=rma_id))
        if submitted["received_paise"] == 0:
            result = rma_supplier_credit.record_zero(db, doc, submitted, actor_id=current_user.get("user_id"))
        else:
            result = rma_supplier_credit.record(
                db, doc, payload, actor_id=current_user.get("user_id"),
                allowed_store_ids=None if cross else set(reach))
        return mask_rma(result, current_user)
    except SupplierCreditError as exc:
        raise HTTPException(status_code=exc.status, detail={"error": exc.code, "message": str(exc)}) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("[VENDOR_RMA] supplier confirmation unavailable", exc_info=True)
        raise HTTPException(status_code=503, detail="The credit result is uncertain. Retry the same note unchanged.") from exc


@router.put("/{rma_id}/prices")
async def price_rma(
    rma_id: str,
    body: RMAPrices,
    current_user: dict = Depends(require_roles(*AP_ROLES)),
):
    """Set the price per piece of each of an RMA's lines, and so its expected
    credit. The accounts roles only (owner rulings 2026-10-12 R2 + R3: a
    return's price is supplier money, the accounts roles' to write and to
    read); a manager raises the RMA without it. Allowed until the first
    credit note is recorded, and not under an issued GST debit note."""
    require_payables(current_user, "Pricing an RMA")
    eng = _engine()
    doc = in_reach_or_404(eng.get(rma_id), current_user, "RMA not found")
    if debit_note_issued(_get_db(), rma_id):
        raise HTTPException(
            status_code=409,
            detail="The GST debit note was issued at this RMA's prices; they stay.",
        )
    res = eng.price_lines(
        rma_id,
        unit_costs_paise=[rupees_to_paise(cost) for cost in body.unit_costs],
        actor=current_user.get("user_id"),
    )
    if not res.get("ok"):
        raise _http_from_result(res, current_user)
    return mask_rma(res, current_user)


# Before dispatch the goods are still in the shop: rejecting is the goods side.
# Once DISPATCHED they are with the supplier on its RMA number and a credit is
# expected -- rejecting then forgoes that whole credit (a write-off of all of
# it), so it is the accounts roles' alone, like close_rma's write-off (owner
# ruling 2026-10-12, R2).
_REJECT_BEFORE_DISPATCH = frozenset({RMAStatus.DRAFT.value, RMAStatus.AUTHORIZED.value})


@router.post("/{rma_id}/reject")
async def reject_rma(
    rma_id: str,
    body: RMAReject,
    current_user: dict = Depends(require_roles(*_VENDOR_RMA_ROLES)),
):
    """Reject an RMA from DRAFT / AUTHORIZED / DISPATCHED (terminal). Before
    dispatch it is the goods side (the purchase roles); rejecting a DISPATCHED
    RMA forgoes its expected credit, so that is the accounts roles' alone
    (owner ruling 2026-10-12)."""
    eng = _engine()
    doc = in_reach_or_404(eng.get(rma_id), current_user, "RMA not found")
    actor = current_user.get("user_id")
    status = doc.get("status")
    if status in _REJECT_BEFORE_DISPATCH:
        # Pinned to the state just read (the engine's one-from-state guard): a
        # dispatch landing in between makes this a 409, never a reject of the
        # dispatched RMA by someone the accounts check below never saw.
        res = eng._transition(  # noqa: SLF001 - the engine's atomic from-state guard
            rma_id, from_status=status, to_status=RMAStatus.REJECTED.value,
            actor=actor, notes=body.reason or "", audit_action="rma_rejected",
        )
    else:
        if status == RMAStatus.DISPATCHED.value:
            require_payables(current_user, "Rejecting a dispatched RMA (forgoing its credit)")
        # DISPATCHED (accounts) -- or a state no reject leaves, which the engine
        # answers 409 invalid_transition with nothing written.
        res = eng.reject(rma_id, actor=actor, reason=body.reason or "")
    if not res.get("ok"):
        raise _http_from_result(res, current_user)
    return mask_rma(res, current_user)


@router.post("/{rma_id}/close")
async def close_rma(
    rma_id: str,
    body: RMAClose,
    current_user: dict = Depends(require_roles(*_VENDOR_RMA_ROLES)),
):
    """Close a fully-reconciled CREDIT_RECEIVED RMA (or force-close an
    outstanding variance as an audited write-off). Refuses to silently close on
    a positive variance. Closing is the goods side (the purchase roles); a
    write-off records supplier money, so it is the accounts roles' alone
    (owner ruling 2026-10-12)."""
    if body.write_off_variance:
        require_payables(current_user, "Writing off an RMA's credit variance")
    eng = _engine()
    doc = in_reach_or_404(eng.get(rma_id), current_user, "RMA not found")
    res = eng.close_rma(
        rma_id,
        actor=current_user.get("user_id"),
        notes=body.notes or "",
        write_off_variance=bool(body.write_off_variance),
    )
    if not res.get("ok"):
        raise _http_from_result(res, current_user)
    return mask_rma(res, current_user)


# ============================================================================
# E4 helper
# ============================================================================


_APPROVAL_INVALID = {
    "error": "approval_invalid",
    "message": "approval missing / expired / wrong tier / not bound to this RMA or store",
}
_SECOND_PERSON = {
    "error": "cannot_approve_own",
    "message": "A supplier credit above the approval threshold needs a second "
               "person: the approval must come from someone other than whoever "
               "records the credit and whoever raised the request.",
}


def _rtv_second_person(request_id, current_user) -> Optional[dict]:
    """None when the named 'rtv' approval was PIN-approved by someone other than
    both the person recording this credit and the person who raised the
    request; else the 403 detail.

    Why here: owner ruling 2026-10-12 makes the accounts roles the only ones
    who record an RMA credit (R2) AND the ones an 'rtv' approval is routed to
    at every tier (R1), so the recorder can always approve. Without this check
    one accountant (or admin) raises, approves and spends their own approval --
    the E4 two-person control on a large supplier credit gone.

    Read before the consume, so a refused approval is not spent. The read
    cannot go stale: approve() sets reviewed_by in the same atomic op that
    leaves REQUESTED, nothing moves a request back to REQUESTED, and the consume
    matches APPROVED only -- so a request APPROVED here keeps this reviewer. One
    not APPROVED yet is refused like any unusable approval. Fail closed: no
    engine, no request, no reviewer or no recorder id -> refused."""
    try:
        from ..services.approvals import ApprovalEngine, ApprovalStatus

        req = ApprovalEngine(db=_get_db()).get(request_id)
        approved = ApprovalStatus.APPROVED.value
    except Exception as exc:  # noqa: BLE001
        logger.warning("[VENDOR_RMA] rtv approval read failed: %s", exc)
        return dict(_APPROVAL_INVALID)
    if not req or req.get("status") != approved:
        return dict(_APPROVAL_INVALID)
    recorder = current_user.get("user_id")
    approver = req.get("reviewed_by")
    if not recorder or not approver or approver in (recorder, req.get("requested_by")):
        return dict(_SECOND_PERSON)
    return None


def _consume_rtv_approval(token, request_id, amount_paise, current_user,
                          *, store_id=None, rma_id=None) -> bool:
    """Atomically consume an E4 approval of action_type 'rtv'. SAFE DEFAULT: any
    missing identifier, missing engine, or error -> False (the credit stays
    blocked). Reuses the EXISTING E4 consume_approval -- it does NOT reimplement
    approvals. The approval amount is in RUPEES (E4 stores amount in rupees);
    pass the rupee value so the engine's amount<=approved guard holds.

    P1-2: passes ``expected_store_id`` + ``expected_context={"rma_id": ...}`` so
    consume_approval re-validates that the approval was minted for THIS store and
    THIS RMA (rolling back on a mismatch). A token bound to store-A/RMA-A is
    therefore rejected here when replayed on store-B or RMA-B."""
    if not request_id:
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
            expected_store_id=store_id,
            expected_context={"rma_id": rma_id} if rma_id else None,
        )
        return bool(res.get("ok"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[VENDOR_RMA] rtv approval consume failed: %s", exc)
        return False
