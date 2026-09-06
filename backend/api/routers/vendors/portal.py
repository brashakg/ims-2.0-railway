"""Vendor portal token issue / list / revoke."""

from ._shared import (
    BaseModel,
    Depends,
    HTTPException,
    Optional,
    get_audit_repository,
    get_current_user,
    get_vendor_portal_token_repository,
    get_vendor_repository,
    router,
)


# ============================================================================
# VENDOR PORTAL TOKEN ENDPOINTS
# ============================================================================
# Issue / list / revoke long-lived bearer tokens that grant a single vendor
# access to the public `/vendor-portal/{token_id}/...` surface. Only
# SUPERADMIN / ADMIN can mint or revoke (token === credential, treat
# generation like creating an API key).


class PortalTokenIssueRequest(BaseModel):
    ttl_days: Optional[int] = 365


def _require_admin(current_user: dict) -> None:
    """Refuse if the caller isn't SUPERADMIN or ADMIN."""
    roles = current_user.get("roles", []) or []
    if not any(r in roles for r in ["SUPERADMIN", "ADMIN"]):
        raise HTTPException(
            status_code=403, detail="Only SUPERADMIN/ADMIN can manage portal tokens"
        )


@router.post("/{vendor_id}/portal-token", status_code=201)
async def issue_portal_token(
    vendor_id: str,
    body: Optional[PortalTokenIssueRequest] = None,
    current_user: dict = Depends(get_current_user),
):
    """Mint a fresh portal token for a vendor.

    Returns the token_id (the bearer secret) plus a ready-to-share URL.
    Existing active tokens for the vendor are NOT auto-revoked; admin
    rotates manually via DELETE.
    """
    _require_admin(current_user)

    vendor_repo = get_vendor_repository()
    if vendor_repo is None:
        raise HTTPException(status_code=503, detail="Vendor storage unavailable")
    vendor = vendor_repo.find_by_id(vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")

    token_repo = get_vendor_portal_token_repository()
    if token_repo is None:
        raise HTTPException(status_code=503, detail="Portal token storage unavailable")

    ttl_days = (body.ttl_days if body and body.ttl_days else 365) or 365
    token = token_repo.issue(
        vendor_id=vendor_id,
        vendor_name=vendor.get("trade_name") or vendor.get("legal_name") or vendor_id,
        created_by=current_user.get("user_id") or "system",
        ttl_days=int(ttl_days),
    )

    # Audit
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "vendor.portal_token_issue",
                    "entity_type": "vendor",
                    "entity_id": vendor_id,
                    "user_id": current_user.get("user_id"),
                    "detail": {
                        "token_id": token.get("token_id"),
                        "ttl_days": ttl_days,
                        "vendor_name": token.get("vendor_name"),
                    },
                }
            )
    except Exception:
        pass

    return {
        "token_id": token.get("token_id"),
        "vendor_id": vendor_id,
        "vendor_name": token.get("vendor_name"),
        "expires_at": (
            token.get("expires_at").isoformat()
            if hasattr(token.get("expires_at"), "isoformat")
            else token.get("expires_at")
        ),
        # Frontend convenience — admin can copy this URL and email it
        # to the lab. Backend doesn't enforce anything about the host
        # part; the relative path is what matters for the API.
        "portal_path": f"/vendor-portal/{token.get('token_id')}",
        "message": "Vendor portal token issued",
    }


@router.get("/{vendor_id}/portal-tokens")
async def list_portal_tokens(
    vendor_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Return all tokens (active + revoked) for a vendor."""
    _require_admin(current_user)
    repo = get_vendor_portal_token_repository()
    if repo is None:
        return {"vendor_id": vendor_id, "tokens": []}
    rows = repo.list_for_vendor(vendor_id)
    # Mask the token_id in the list view to prevent shoulder-surfing —
    # admin sees the prefix only, can copy from the issue response.
    masked = []
    for r in rows:
        tid = r.get("token_id") or ""
        masked.append(
            {
                "token_id_prefix": tid[:8] + "..." if tid else "",
                "vendor_id": r.get("vendor_id"),
                "vendor_name": r.get("vendor_name"),
                "active": r.get("active", False),
                "created_at": (
                    r.get("created_at").isoformat()
                    if hasattr(r.get("created_at"), "isoformat")
                    else r.get("created_at")
                ),
                "created_by": r.get("created_by"),
                "expires_at": (
                    r.get("expires_at").isoformat()
                    if hasattr(r.get("expires_at"), "isoformat")
                    else r.get("expires_at")
                ),
                "last_used_at": (
                    r.get("last_used_at").isoformat()
                    if hasattr(r.get("last_used_at"), "isoformat")
                    else r.get("last_used_at")
                ),
                "use_count": r.get("use_count", 0),
            }
        )
    return {"vendor_id": vendor_id, "tokens": masked}


@router.delete("/{vendor_id}/portal-token/{token_id}")
async def revoke_portal_token(
    vendor_id: str,
    token_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Revoke (deactivate) a portal token. Token row is kept for audit."""
    _require_admin(current_user)
    repo = get_vendor_portal_token_repository()
    if repo is None:
        raise HTTPException(status_code=503, detail="Portal token storage unavailable")
    doc = repo.find_by_id(token_id)
    if doc is None or doc.get("vendor_id") != vendor_id:
        raise HTTPException(status_code=404, detail="Token not found")
    if not repo.revoke(token_id, current_user.get("user_id") or "system"):
        raise HTTPException(status_code=500, detail="Failed to revoke token")
    try:
        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "vendor.portal_token_revoke",
                    "entity_type": "vendor",
                    "entity_id": vendor_id,
                    "user_id": current_user.get("user_id"),
                    "detail": {"token_id": token_id},
                }
            )
    except Exception:
        pass
    return {
        "token_id": token_id,
        "vendor_id": vendor_id,
        "active": False,
        "message": "Token revoked",
    }
