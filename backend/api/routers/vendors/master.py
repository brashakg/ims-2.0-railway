"""Vendor master CRUD and vendor SKU aliases."""

from ._shared import (
    BaseModel,
    Depends,
    HTTPException,
    Optional,
    Query,
    _VENDOR_ROLES,
    _get_db,
    datetime,
    derive_vendor_state,
    get_audit_repository,
    get_current_user,
    get_vendor_repository,
    logger,
    require_roles,
    router,
    uuid,
)
from .models import VendorCreate, VendorUpdate


# ============================================================================
# VENDOR ENDPOINTS
# ============================================================================


# Both "" and "/" — the app uses redirect_slashes=False, so bare + slashed
# forms must both resolve. Audit Run #2: Purchase page was 404'ing because
# the frontend calls api.get('/vendors') without trailing slash.
@router.get("")
@router.get("/")
async def list_vendors(
    search: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
):
    """List all vendors with optional search"""
    vendor_repo = get_vendor_repository()

    if vendor_repo is None:
        return {"vendors": [], "total": 0}

    filter_dict = {}
    if is_active is not None:
        filter_dict["is_active"] = is_active

    if search:
        # Search in name, trade name, or mobile
        vendors = vendor_repo.search_vendors(search)
    else:
        vendors = vendor_repo.find_many(filter_dict, skip=skip, limit=limit)

    return {"vendors": vendors or [], "total": len(vendors) if vendors else 0}


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_vendor(
    vendor: VendorCreate,
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """Create a new vendor"""
    vendor_repo = get_vendor_repository()
    vendor_id = str(uuid.uuid4())

    if vendor_repo is not None:
        # Check for duplicate GSTIN
        if vendor.gstin:
            existing = vendor_repo.find_one({"gstin": vendor.gstin})
            if existing is not None:
                raise HTTPException(
                    status_code=400, detail="Vendor with this GSTIN already exists"
                )

        state_code, state_name = derive_vendor_state(vendor.gstin, vendor.state)
        vendor_repo.create(
            {
                "vendor_id": vendor_id,
                "legal_name": vendor.legal_name,
                "trade_name": vendor.trade_name,
                "vendor_type": vendor.vendor_type,
                "gstin_status": vendor.gstin_status,
                "gstin": vendor.gstin,
                "address": vendor.address,
                "city": vendor.city,
                # The GSTIN decides the state; state_code is the 2-digit GST
                # form the purchase/ITC code compares against.
                "state": state_name,
                "state_code": state_code,
                "mobile": vendor.mobile,
                "email": vendor.email,
                "vendor_code": (vendor.vendor_code or "").strip().upper() or None,
                "contact_person": vendor.contact_person,
                "credit_limit": vendor.credit_limit,
                "credit_days": vendor.credit_days,
                "is_active": True,
                "created_by": current_user.get("user_id"),
                "created_at": datetime.now().isoformat(),
            }
        )

    return {"vendor_id": vendor_id, "message": "Vendor created successfully"}


# IMPORTANT: GET /{vendor_id} is registered at the BOTTOM of this file via
# `router.add_api_route(...)`, NOT here. FastAPI matches routes in
# registration order; a `/{vendor_id}` decorator here shadowed every
# specific GET below it (`/purchase-orders`, `/grn`) — they'd resolve
# to this handler with `vendor_id="purchase-orders"` and return 404
# ("Vendor not found"). Same class of bug as the tasks.py route-order
# fix in PR #103.
async def get_vendor(vendor_id: str, current_user: dict = Depends(get_current_user)):
    """Get vendor details"""
    vendor_repo = get_vendor_repository()

    if vendor_repo is None:
        return {"vendor_id": vendor_id}

    vendor = vendor_repo.find_by_id(vendor_id)
    if vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")

    return vendor


@router.put("/{vendor_id}")
async def update_vendor(
    vendor_id: str,
    updates: VendorUpdate,
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """Update vendor details"""
    vendor_repo = get_vendor_repository()

    if vendor_repo is not None:
        existing = vendor_repo.find_by_id(vendor_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Vendor not found")

        update_data = updates.model_dump(exclude_unset=True)

        # A GSTIN identifies exactly one taxpayer; two vendor rows sharing one
        # would double-count ITC. Same guard create already had.
        if update_data.get("gstin"):
            clash = vendor_repo.find_one({"gstin": update_data["gstin"]})
            if clash is not None and clash.get("vendor_id") != vendor_id:
                raise HTTPException(
                    status_code=400, detail="Vendor with this GSTIN already exists"
                )

        # Keep state / state_code in step with the GSTIN whenever either one is
        # touched, so an edit can never leave a vendor whose typed state and
        # GSTIN disagree about how its purchases are taxed.
        if "gstin" in update_data or "state" in update_data:
            gstin = update_data.get("gstin", existing.get("gstin"))
            typed = update_data.get("state", existing.get("state"))
            state_code, state_name = derive_vendor_state(gstin, typed)
            update_data["state"] = state_name
            update_data["state_code"] = state_code

        if update_data.get("vendor_code"):
            update_data["vendor_code"] = update_data["vendor_code"].strip().upper()

        # An EXPLICIT clear (JSON `"gstin": null`) means "this vendor is not
        # registered after all". Leaving gstin_status at REGISTERED with no
        # number would be a row that contradicts itself, so clear both unless
        # the caller said otherwise. An OMITTED key still leaves the stored
        # GSTIN alone -- exclude_unset above is what keeps the two different.
        if "gstin" in update_data and update_data["gstin"] is None:
            update_data.setdefault("gstin_status", "UNREGISTERED")

        # What actually changed, captured before the bookkeeping stamps are
        # added. Recorded as a diff rather than an allowlist of "important"
        # fields: an allowlist is a second thing to keep in step with
        # VendorUpdate and goes stale in silence, and every field on this door
        # moves money or identity -- gstin re-taxes every future bill
        # (IGST <-> CGST+SGST), credit_limit and is_active gate buying at all.
        changed = {
            key: {"from": existing.get(key), "to": value}
            for key, value in update_data.items()
            if existing.get(key) != value
        }

        update_data["updated_by"] = current_user.get("user_id")
        update_data["updated_at"] = datetime.now().isoformat()

        vendor_repo.update(vendor_id, update_data)

        # Fail-soft, same contract as the portal-token audits in this file: a
        # lost audit row is bad, but blocking the correction of a wrong GSTIN --
        # the thing this endpoint exists for -- is worse.
        if changed:
            try:
                audit = get_audit_repository()
                if audit is not None:
                    audit.create(
                        {
                            "action": "vendor.update",
                            "entity_type": "vendor",
                            "entity_id": vendor_id,
                            "user_id": current_user.get("user_id"),
                            "detail": {"changed": changed},
                        }
                    )
            except Exception:
                pass

    return {"vendor_id": vendor_id, "message": "Vendor updated successfully"}


# ============================================================================
# INV-7: VENDOR SKU ALIAS — maps vendor-specific codes to IMS master products
# ============================================================================
# Problem: the same lens/frame arrives from different suppliers under different
# catalogue codes.  Without an alias map, staff must search the product master
# each time, leading to duplicate entries at goods-inward.  With this, they
# register the vendor code once and the GRN workflow can resolve it to the
# canonical IMS product_id automatically.


class VendorSkuAliasCreate(BaseModel):
    product_id: str  # IMS master product_id
    vendor_sku: str  # vendor's own catalogue / item code
    description: Optional[str] = None  # optional free-text note


@router.get("/sku-alias-lookup")
async def lookup_vendor_sku(
    vendor_id: str = Query(...),
    vendor_sku: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Resolve a vendor's SKU code to the IMS master product_id (INV-7).

    Called during GRN / goods-inward so staff never have to manually search
    the product master when receiving stock.
    """
    db = _get_db()
    if db is None:
        return {"product_id": None, "vendor_id": vendor_id, "vendor_sku": vendor_sku}

    try:
        coll = db.get_collection("vendor_sku_aliases")
        doc = coll.find_one({"vendor_id": vendor_id, "vendor_sku": vendor_sku})
        if not doc:
            return {
                "product_id": None,
                "vendor_id": vendor_id,
                "vendor_sku": vendor_sku,
                "message": "No alias found",
            }
        doc.pop("_id", None)
        return {
            "product_id": doc.get("product_id"),
            "vendor_id": vendor_id,
            "vendor_sku": vendor_sku,
            "description": doc.get("description"),
            "alias_id": doc.get("alias_id"),
        }
    except Exception as e:
        logger.warning(f"lookup_vendor_sku error: {e}")
        return {"product_id": None, "vendor_id": vendor_id, "vendor_sku": vendor_sku}


@router.get("/{vendor_id}/sku-aliases")
async def list_vendor_sku_aliases(
    vendor_id: str,
    current_user: dict = Depends(get_current_user),
):
    """List all SKU aliases registered for a vendor (INV-7)."""
    db = _get_db()
    if db is None:
        return {"aliases": [], "vendor_id": vendor_id}

    try:
        coll = db.get_collection("vendor_sku_aliases")
        docs = list(coll.find({"vendor_id": vendor_id}, {"_id": 0}))
        return {"aliases": docs, "vendor_id": vendor_id, "total": len(docs)}
    except Exception as e:
        logger.warning(f"list_vendor_sku_aliases error: {e}")
        return {"aliases": [], "vendor_id": vendor_id}


@router.post("/{vendor_id}/sku-aliases", status_code=201)
async def create_vendor_sku_alias(
    vendor_id: str,
    body: VendorSkuAliasCreate,
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """Register a vendor SKU code to an IMS master product (INV-7).

    Idempotent on (vendor_id, vendor_sku): re-posting an existing alias
    updates the product_id and description rather than creating a duplicate.
    """
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        vendor_repo = get_vendor_repository()
        if vendor_repo is not None:
            vendor = vendor_repo.find_by_id(vendor_id)
            if vendor is None:
                raise HTTPException(status_code=404, detail="Vendor not found")

        coll = db.get_collection("vendor_sku_aliases")
        now = datetime.now()

        # Upsert on (vendor_id, vendor_sku) — idempotent
        existing = coll.find_one(
            {"vendor_id": vendor_id, "vendor_sku": body.vendor_sku}
        )
        if existing:
            coll.update_one(
                {"vendor_id": vendor_id, "vendor_sku": body.vendor_sku},
                {
                    "$set": {
                        "product_id": body.product_id,
                        "description": body.description,
                        "updated_at": now.isoformat(),
                        "updated_by": current_user.get("user_id", ""),
                    }
                },
            )
            alias_id = existing.get("alias_id", "")
            action = "updated"
        else:
            alias_id = str(uuid.uuid4())
            coll.insert_one(
                {
                    "alias_id": alias_id,
                    "vendor_id": vendor_id,
                    "vendor_sku": body.vendor_sku,
                    "product_id": body.product_id,
                    "description": body.description,
                    "created_at": now.isoformat(),
                    "created_by": current_user.get("user_id", ""),
                }
            )
            action = "created"

        return {
            "alias_id": alias_id,
            "vendor_id": vendor_id,
            "vendor_sku": body.vendor_sku,
            "product_id": body.product_id,
            "action": action,
            "message": f"Vendor SKU alias {action}",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"create_vendor_sku_alias error: {e}")
        raise HTTPException(status_code=500, detail="Failed to save vendor SKU alias")


@router.delete("/{vendor_id}/sku-aliases/{alias_id}", status_code=200)
async def delete_vendor_sku_alias(
    vendor_id: str,
    alias_id: str,
    current_user: dict = Depends(require_roles(*_VENDOR_ROLES)),
):
    """Remove a vendor SKU alias (INV-7)."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    try:
        coll = db.get_collection("vendor_sku_aliases")
        result = coll.delete_one({"alias_id": alias_id, "vendor_id": vendor_id})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Alias not found")
        return {
            "alias_id": alias_id,
            "vendor_id": vendor_id,
            "message": "Alias deleted",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"delete_vendor_sku_alias error: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete alias")
