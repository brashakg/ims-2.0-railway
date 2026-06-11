"""
IMS 2.0 - Feature #16: Bank / Cash / POS reconciliation router
==============================================================
HTTP surface for ``api/services/bank_reconciliation.py``. Reconciles three money
trails per store + date-range -- CASH (from the #23 till Z-read close), POS
DIGITAL (card/UPI/wallet via E5 reconcile_window, net of MDR), and BANK statement
lines -- into a run with matched / unmatched / variance, a soft-lock, and a
manager sign-off.

READ-ONLY against orders + till docs (POS capture UNCHANGED). All writes land on
the new ``bank_reconciliations`` + ``bank_statement_lines`` collections.

Every route store-scopes via ``validate_store_access`` (a store reconciles its
OWN books) and gates by role -- a cashier can NEVER run or sign off a
reconciliation. Mirrors rbac_policy POLICY.

No emoji (Windows cp1252).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import get_current_user
from ..dependencies import validate_store_access
from ..services import bank_reconciliation as svc

router = APIRouter(tags=["bank-reconciliation"])

# Who may run + lock + sign-off a reconciliation: finance + store management.
# A cashier / sales / workshop role is intentionally absent -> 403.
_RECON_ROLES = {"ACCOUNTANT", "STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"}
# Sign-off is a management attestation: managers + admin (NOT a plain accountant
# clerk running the match -- separation of who-runs vs who-attests is allowed but
# we keep sign-off to the same finance/manager set for a small chain).
_SIGNOFF_ROLES = {"STORE_MANAGER", "AREA_MANAGER", "ADMIN", "ACCOUNTANT", "SUPERADMIN"}


def _get_db():
    from database.connection import get_db

    return get_db().db


def _roles(user: Dict[str, Any]) -> set:
    return {str(r).upper() for r in (user.get("roles", []) or [])}


def _require(user: Dict[str, Any], allowed: set, what: str):
    if not (_roles(user) & allowed):
        raise HTTPException(status_code=403, detail=f"not permitted to {what}")


def _raise(res: Dict[str, Any]):
    raise HTTPException(status_code=int(res.get("http", 400)), detail=res.get("error", "error"))


def _engine():
    return svc.BankReconciliationEngine(_get_db())


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RunBody(BaseModel):
    store_id: str = Field(..., description="Store whose books to reconcile")
    window_start: str = Field(..., description="IST day YYYY-MM-DD (inclusive)")
    window_end: str = Field(..., description="IST day YYYY-MM-DD (inclusive)")
    tolerance_paise: Optional[int] = Field(None, ge=0, description="Match tolerance; default from E2 policy")
    mdr_bps: Optional[int] = Field(None, ge=0, le=2000, description="MDR fee bps for digital; default from E2 policy")


class BankLineBody(BaseModel):
    store_id: str = Field(..., description="Store the line belongs to")
    value_date: str = Field(..., description="Bank value date YYYY-MM-DD")
    amount: Optional[float] = Field(None, description="Amount in rupees (or use amount_paise)")
    amount_paise: Optional[int] = Field(None, description="Amount in integer paise")
    kind: str = Field(svc.KIND_CASH, description="CASH or POS_DIGITAL")
    tender: Optional[str] = Field(None, description="For digital: CARD/UPI/WALLET")
    reference: Optional[str] = Field(None, description="Bank reference / UTR / narration")


def _policy_tolerance(store_id: str) -> int:
    try:
        from ..services.policy_engine import get_policy

        return int(get_policy("reconciliation.variance_tolerance_paise", {"store_id": store_id}, default=100))
    except Exception:  # noqa: BLE001
        return 100


def _policy_mdr_bps(store_id: str) -> int:
    try:
        from ..services.policy_engine import get_policy

        return int(get_policy("reconciliation.mdr_bps", {"store_id": store_id}, default=0))
    except Exception:  # noqa: BLE001
        return 0


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/reconciliations")
async def run_reconciliation(
    body: RunBody,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Run a bank/cash/POS reconciliation for a store + window. Finance/manager only."""
    _require(current_user, _RECON_ROLES, "run a reconciliation")
    validate_store_access(body.store_id, current_user)
    tol = body.tolerance_paise if body.tolerance_paise is not None else _policy_tolerance(body.store_id)
    mdr = body.mdr_bps if body.mdr_bps is not None else _policy_mdr_bps(body.store_id)
    res = _engine().run_reconciliation(
        body.store_id, body.window_start, body.window_end, int(tol), int(mdr), current_user
    )
    if not res.get("ok"):
        _raise(res)
    return res["run"]


@router.get("/reconciliations")
async def list_reconciliations(
    store_id: str = Query(..., description="Store to list runs for"),
    limit: int = Query(50, ge=1, le=200),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """List reconciliation runs for a store (newest first). Finance/manager only."""
    _require(current_user, _RECON_ROLES, "view reconciliations")
    validate_store_access(store_id, current_user)
    db = _get_db()
    if db is None:
        return {"items": []}
    try:
        cur = (
            db.get_collection(svc.RECON_COLLECTION)
            .find({"store_id": store_id}, {"cash.matched": 0, "digital.matched": 0})
            .sort("created_at", -1)
            .limit(int(limit))
        )
        items = [dict(d) for d in cur]
    except Exception:  # noqa: BLE001
        items = []
    return {"items": items}


@router.get("/reconciliations/{run_id}")
async def get_reconciliation(
    run_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """One reconciliation run (full match detail). Finance/manager only + store-scoped."""
    _require(current_user, _RECON_ROLES, "view a reconciliation")
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="db unavailable")
    run = db.get_collection(svc.RECON_COLLECTION).find_one({"_id": run_id})
    if not run:
        raise HTTPException(status_code=404, detail="reconciliation not found")
    validate_store_access(run.get("store_id"), current_user)
    return run


@router.post("/reconciliations/{run_id}/lock")
async def lock_reconciliation(
    run_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Soft-lock a run (OPEN -> LOCKED). Finance/manager only + store-scoped + atomic."""
    _require(current_user, _RECON_ROLES, "lock a reconciliation")
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="db unavailable")
    run = db.get_collection(svc.RECON_COLLECTION).find_one({"_id": run_id})
    if not run:
        raise HTTPException(status_code=404, detail="reconciliation not found")
    validate_store_access(run.get("store_id"), current_user)
    updated = _engine().acquire_lock(run_id, current_user)
    if updated is None:
        raise HTTPException(status_code=409, detail="not open (already locked or signed off)")
    return updated


@router.post("/reconciliations/{run_id}/sign-off")
async def sign_off_reconciliation(
    run_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Manager/finance sign-off (atomic + audited) + store-scoped."""
    _require(current_user, _SIGNOFF_ROLES, "sign off a reconciliation")
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="db unavailable")
    run = db.get_collection(svc.RECON_COLLECTION).find_one({"_id": run_id})
    if not run:
        raise HTTPException(status_code=404, detail="reconciliation not found")
    validate_store_access(run.get("store_id"), current_user)
    res = _engine().sign_off(run_id, run.get("store_id"), current_user)
    if not res.get("ok"):
        _raise(res)
    return res["run"]


@router.post("/bank-lines")
async def add_bank_line(
    body: BankLineBody,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Add a single bank statement line (manual entry / CSV row). Finance/manager only."""
    _require(current_user, _RECON_ROLES, "add a bank statement line")
    validate_store_access(body.store_id, current_user)
    res = _engine().add_bank_line(
        body.store_id,
        {
            "value_date": body.value_date,
            "amount": body.amount,
            "amount_paise": body.amount_paise,
            "kind": body.kind,
            "tender": body.tender,
            "reference": body.reference,
        },
        current_user,
    )
    if not res.get("ok"):
        _raise(res)
    return res["line"]
