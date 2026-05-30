"""
IMS 2.0 - AI Change-Proposal API
==================================
SUPERADMIN-EXCLUSIVE endpoints for the AI change-proposal review loop
(SYSTEM_INTENT.md section 8).

    AI enqueues a suggestion (PENDING)
      -> Superadmin lists + reviews
      -> approve  -> auto-executes IF reversible (Tier-1 whitelist),
                     otherwise ADVISORY (approval recorded, human acts)
      -> reject   -> REJECTED with reason
      -> every transition writes an immutable before/after audit_logs row.

*** STRICTLY SUPERADMIN ONLY - NO EXCEPTIONS ***
Like the agents router, non-superadmin callers get 404 (deliberate: the
endpoint's existence is not leaked).

Mounted at /api/v1/jarvis/proposals (see main.py). The route paths here are
relative to that prefix, e.g. GET /api/v1/jarvis/proposals.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List
import logging

from .auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================================
# SUPERADMIN GUARD
# ============================================================================


def require_superadmin(current_user: dict = Depends(get_current_user)):
    """Strict SUPERADMIN-only access guard (mirrors agents.py)."""
    if "SUPERADMIN" not in current_user.get("roles", []):
        raise HTTPException(status_code=404, detail="Not found")
    return current_user


# ============================================================================
# HELPERS
# ============================================================================


def _get_db():
    """Resolve the live Mongo DB (seeded-aware), matching the agents router."""
    try:
        import sys
        import os

        sys.path.insert(
            0,
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ),
        )
        from database.connection import get_seeded_db

        return get_seeded_db()
    except Exception as e:  # fail-soft - no DB just yields empty results
        logger.debug("[PROPOSALS] _get_db failed: %s", e)
        return None


def _store():
    """Build a ProposalStore over the live DB. Fail-soft to a null store."""
    from agents.proposals import ProposalStore

    return ProposalStore(db=_get_db())


def _reviewer(user: dict) -> str:
    return user.get("email") or user.get("username") or user.get("user_id") or "superadmin"


# ============================================================================
# REQUEST SCHEMAS
# ============================================================================


class RejectRequest(BaseModel):
    reason: str = Field("", description="Why the proposal was declined")


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get(
    "/proposals",
    summary="List AI change-proposals",
    description=(
        "Returns the AI change-proposal queue newest-first, optionally "
        "filtered by status (PENDING / APPROVED / REJECTED / EXECUTED / "
        "FAILED). SUPERADMIN-only - non-superadmin callers get 404."
    ),
)
async def list_proposals(
    status: Optional[str] = Query(
        None,
        description="Filter by lifecycle status",
        pattern="^(PENDING|APPROVED|REJECTED|EXECUTED|FAILED)$",
    ),
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(require_superadmin),
):
    from agents.proposals import REVERSIBLE_TYPES

    proposals = _store().list(status=status, limit=limit)
    return {
        "proposals": proposals,
        "total": len(proposals),
        "filter_status": status,
        # Surface the whitelist so the UI can label types without hardcoding.
        "reversible_types": sorted(REVERSIBLE_TYPES),
    }


@router.get(
    "/proposals/{proposal_id}",
    summary="Get a single AI change-proposal",
)
async def get_proposal(proposal_id: str, user: dict = Depends(require_superadmin)):
    proposal = _store().get(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    # Coerce datetimes for a clean JSON shape.
    from agents.proposals import _jsonable

    return _jsonable(proposal)


@router.post(
    "/proposals/{proposal_id}/approve",
    summary="Approve an AI change-proposal",
    description=(
        "Approve a PENDING proposal. If the proposal type is in the "
        "reversible Tier-1 whitelist, the change is AUTO-EXECUTED via the "
        "existing domain services and a before/after audit row is written "
        "(status -> EXECUTED, or FAILED if execution errors). Otherwise the "
        "approval is recorded as ADVISORY (status -> APPROVED) and a human "
        "performs the change. SUPERADMIN-only."
    ),
)
async def approve_proposal(proposal_id: str, user: dict = Depends(require_superadmin)):
    result = _store().approve(proposal_id, reviewed_by=_reviewer(user))
    if not result.get("ok"):
        err = result.get("error")
        if err == "not_found":
            raise HTTPException(status_code=404, detail="Proposal not found")
        if err == "not_pending":
            raise HTTPException(
                status_code=409,
                detail=f"Proposal is not PENDING (status={result.get('status')})",
            )
        raise HTTPException(status_code=400, detail=str(err or "approval failed"))
    return result


@router.post(
    "/proposals/{proposal_id}/reject",
    summary="Reject an AI change-proposal",
    description=(
        "Decline a PENDING proposal with an optional reason. Records the "
        "decision in an immutable audit row and sets status -> REJECTED. "
        "SUPERADMIN-only."
    ),
)
async def reject_proposal(
    proposal_id: str,
    body: RejectRequest = RejectRequest(),
    user: dict = Depends(require_superadmin),
):
    result = _store().reject(
        proposal_id, reviewed_by=_reviewer(user), reason=body.reason
    )
    if not result.get("ok"):
        err = result.get("error")
        if err == "not_found":
            raise HTTPException(status_code=404, detail="Proposal not found")
        if err == "not_pending":
            raise HTTPException(
                status_code=409,
                detail=f"Proposal is not PENDING (status={result.get('status')})",
            )
        raise HTTPException(status_code=400, detail=str(err or "reject failed"))
    return result
