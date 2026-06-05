"""
IMS 2.0 - Online Store : Image Design Queue Router  (BVI Phase 4 -- FLAGSHIP #3)
===============================================================================
CRUD + the RAW->EDITED->APPROVED design lifecycle for `product_images` -- BVI's
ProductImage + VariantImage tables AND the design-team work queue that wraps
them, folded into IMS (BVI_MERGE_PLAN.md A.1 / Phase 4).

PUSH-DARK: every route here stores/edits image records + drives their design
lifecycle inside IMS Mongo ONLY. No Shopify network write happens in Phase 4
(the GraphQL image push that fills `shopify_image_id` is Phase 5).

One row = one image of a product (variant_id=null) or a specific variant
(variant_id set). The design QUEUE is just `GET /` filtered by status/assignee.

THE LIFECYCLE (enforced in ProductImageRepository + guarded here):
    QUEUED -> IN_PROGRESS -> REVIEW -> APPROVED | REJECTED ; REJECTED -> IN_PROGRESS
An illegal transition is a 409 (Control over Convenience / Fail Loudly). APPROVE
gates go-live, so it writes a chained `audit_logs` row (Audit Everything).

Mounted at /api/v1/online-store/images. ROLE GATE (router-level):
SUPERADMIN / ADMIN / CATALOG_MANAGER / DESIGN_MANAGER (SUPERADMIN auto-granted by
require_roles). Every route is catalogued in api/services/rbac_policy.POLICY with
this exact set (kept in lock-step -- test_rbac_policy.test_no_uncatalogued_routes
is the regression lock). The literal action sub-paths (/assign, /status, /edited)
out-rank the {image_id} param route in the policy matcher.

Routes:
  GET    /                         list (the design QUEUE; filter status / product_id /
                                    variant_id / assigned_to / kind + paging)
  POST   /                         register/queue an image
  GET    /{image_id}               fetch one
  PUT    /{image_id}               edit presentation/linkage fields
  POST   /{image_id}/assign        assign to a DESIGN_MANAGER
  POST   /{image_id}/status        transition status (valid-transition guard -> 409)
  POST   /{image_id}/edited        attach edited_url + move to REVIEW
  DELETE /{image_id}               delete

Everything is FAIL-SOFT: no DB -> reads return empty / writes 503; never 500.
"""

from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import require_roles

router = APIRouter()

# Roles allowed into the Image Design Queue. SUPERADMIN is auto-granted by
# require_roles, so it is not repeated in the tuple but IS listed in the POLICY
# rows. Keep this in lock-step with rbac_policy.POLICY for every route below.
_ECOM_ROLES = ("ADMIN", "CATALOG_MANAGER", "DESIGN_MANAGER")

# Mirror the repository/schema enums so a typo can't store an un-pushable value.
_KINDS = {"RAW", "EDITED", "FINAL"}
_SOURCES = {"UPLOAD", "SHOPIFY", "SCRAPE", "AI"}
_STATUSES = {"QUEUED", "IN_PROGRESS", "REVIEW", "APPROVED", "REJECTED"}


# ---------------------------------------------------------------------------
# DB helpers (fail-soft; mirror routers/online_store_menus.py)
# ---------------------------------------------------------------------------


def _get_db():
    """Underlying DB object (real pymongo Database or seeded MockDatabase) when
    connected, else None. Subscript access (db[name]) works on both."""
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def _repo():
    """Return a ProductImageRepository bound to the live `product_images`
    collection, or None when no DB is available (so the route can 503 cleanly)."""
    db = _get_db()
    if db is None:
        return None
    try:
        from database.repositories import ProductImageRepository

        return ProductImageRepository(db["product_images"])
    except Exception:  # noqa: BLE001
        return None


def _require_repo():
    repo = _repo()
    if repo is None:
        # No DB -> the image store is unavailable. 503 (not a false 200).
        raise HTTPException(status_code=503, detail="Image store unavailable")
    return repo


def _with_id(doc):
    """Mirror the stored image fields onto the keys the FE Design Queue reads:
    `image_id` -> `id`, the lifecycle `status` -> `design_status`, and the owner
    `assigned_to` -> `assignee_id`. Additive + non-destructive (only fills a key
    that is absent/None) so the canonical snake_case fields stay intact. Tolerates
    a list (maps each element) or a non-dict (returned untouched). Fail-soft."""
    if isinstance(doc, list):
        return [_with_id(d) for d in doc]
    if isinstance(doc, dict):
        if doc.get("id") is None and doc.get("image_id") is not None:
            doc["id"] = doc["image_id"]
        if doc.get("design_status") is None and doc.get("status") is not None:
            doc["design_status"] = doc["status"]
        if doc.get("assignee_id") is None and doc.get("assigned_to") is not None:
            doc["assignee_id"] = doc["assigned_to"]
    return doc


def _write_audit(image: Dict, current_user: dict) -> None:
    """Write a chained audit row for an image APPROVAL (go-live gate -- Audit
    Everything). Fail-soft: any audit error is swallowed so it can never undo the
    approval that triggered it (mirrors returns.py's audit pattern)."""
    try:
        from ..dependencies import get_audit_repository

        audit = get_audit_repository()
        if audit is not None:
            audit.create(
                {
                    "action": "PRODUCT_IMAGE_APPROVED",
                    "entity_type": "product_image",
                    "entity_id": image.get("image_id"),
                    "user_id": current_user.get("user_id"),
                    "details": {
                        "product_id": image.get("product_id"),
                        "variant_id": image.get("variant_id"),
                        "kind": image.get("kind"),
                        "edited_url": image.get("edited_url"),
                        "reviewed_by": image.get("reviewed_by"),
                    },
                }
            )
    except Exception:  # noqa: BLE001 -- audit must never break the business write
        pass


# ---------------------------------------------------------------------------
# Pydantic payloads
# ---------------------------------------------------------------------------


class ImageCreate(BaseModel):
    """Register/queue an image. `product_id` + `url` are required; everything
    else is optional/additive. A fresh image enters the queue as kind=RAW,
    status=QUEUED, source=UPLOAD (repository defaults)."""

    product_id: str = Field(
        ..., description="catalog_products id this image belongs to"
    )
    url: str = Field(..., description="Asset URL (the raw/source image)")
    variant_id: Optional[str] = Field(
        None, description="catalog_variants id (null = a product-level image)"
    )
    kind: Optional[str] = Field(None, description="RAW | EDITED | FINAL (default RAW)")
    source: Optional[str] = Field(
        None, description="UPLOAD | SHOPIFY | SCRAPE | AI (default UPLOAD)"
    )
    position: Optional[int] = Field(None, ge=0, description="0-based display order")
    alt_text: Optional[str] = None
    design_notes: Optional[str] = None


class ImageUpdate(BaseModel):
    """Patch an image's presentation/linkage fields. Lifecycle fields (status /
    assigned_to / edited_url / reviewed_by / approved_at) are NOT patchable here
    -- use the dedicated action routes. Only provided keys change."""

    url: Optional[str] = None
    kind: Optional[str] = None
    source: Optional[str] = None
    position: Optional[int] = Field(None, ge=0)
    alt_text: Optional[str] = None
    design_notes: Optional[str] = None
    variant_id: Optional[str] = None


class AssignIn(BaseModel):
    """Assign (or, with null, unassign) the image to a DESIGN_MANAGER."""

    assigned_to: Optional[str] = Field(
        None, description="User id of the DESIGN_MANAGER (null = unassign)"
    )


class StatusIn(BaseModel):
    """Transition the image's lifecycle status. Guarded by the valid-transition
    graph -- an illegal move is a 409."""

    status: str = Field(..., description="Target status (see lifecycle graph)")


class EditedIn(BaseModel):
    """Attach the designer's edited asset + move IN_PROGRESS -> REVIEW."""

    edited_url: str = Field(..., description="URL of the designer's edited asset")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_enum(value: Optional[str], allowed: set, label: str) -> Optional[str]:
    """Uppercase + validate an enum value, or 400. None passes through."""
    if value is None:
        return None
    v = value.strip().upper()
    if v not in allowed:
        raise HTTPException(
            status_code=400, detail=f"{label} must be one of {sorted(allowed)}"
        )
    return v


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("")
async def list_images(
    status: Optional[str] = Query(None, description="Filter by lifecycle status"),
    product_id: Optional[str] = Query(None),
    variant_id: Optional[str] = Query(None),
    assigned_to: Optional[str] = Query(None),
    kind: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    skip: int = Query(0, ge=0),
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """List images -- THE DESIGN QUEUE (filtered, fail-soft). No DB -> empty list,
    never 500."""
    repo = _repo()
    if repo is None:
        return {"images": [], "count": 0, "db_connected": False}
    # Validate filter enums (a bad value is a 400, not a silently-empty list).
    status_f = _validate_enum(status, _STATUSES, "status")
    kind_f = _validate_enum(kind, _KINDS, "kind")
    rows = repo.list(
        status=status_f,
        product_id=product_id,
        variant_id=variant_id,
        assigned_to=assigned_to,
        kind=kind_f,
        skip=skip,
        limit=limit,
    )
    return {"images": _with_id(rows), "count": len(rows), "db_connected": True}


@router.post("", status_code=201)
async def create_image(
    payload: ImageCreate,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Register/queue an image (PUSH-DARK -- stored in IMS only). Enters the queue
    as kind=RAW, status=QUEUED (repository defaults) unless overridden."""
    repo = _require_repo()

    product_id = (payload.product_id or "").strip()
    url = (payload.url or "").strip()
    if not product_id:
        raise HTTPException(status_code=400, detail="product_id is required")
    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    data: Dict = {
        "product_id": product_id,
        "url": url,
        "variant_id": payload.variant_id,
        "kind": _validate_enum(payload.kind, _KINDS, "kind"),
        "source": _validate_enum(payload.source, _SOURCES, "source"),
        "position": payload.position,
        "alt_text": payload.alt_text,
        "design_notes": payload.design_notes,
        "submitted_by": current_user.get("user_id"),
    }
    # Drop None so the repository's sensible defaults apply.
    data = {k: v for k, v in data.items() if v is not None}
    created = repo.create(data)
    if created is None:
        raise HTTPException(status_code=500, detail="Failed to create image")
    return {"image": _with_id(created)}


@router.get("/{image_id}")
async def get_image(
    image_id: str,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    repo = _require_repo()
    doc = repo.get_by_id(image_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Image not found")
    return {"image": _with_id(doc)}


@router.put("/{image_id}")
async def update_image(
    image_id: str,
    payload: ImageUpdate,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Patch an image's presentation/linkage fields. Lifecycle fields are NOT
    editable here -- use /assign, /status, /edited."""
    repo = _require_repo()
    if repo.get_by_id(image_id) is None:
        raise HTTPException(status_code=404, detail="Image not found")

    data = payload.model_dump(exclude_none=True)
    if "kind" in data:
        data["kind"] = _validate_enum(data["kind"], _KINDS, "kind")
    if "source" in data:
        data["source"] = _validate_enum(data["source"], _SOURCES, "source")
    if not data:
        # Nothing to change -> return the unchanged doc (idempotent no-op).
        return {"image": _with_id(repo.get_by_id(image_id)), "updated": False}

    repo.update(image_id, data)
    return {"image": _with_id(repo.get_by_id(image_id)), "updated": True}


@router.delete("/{image_id}")
async def delete_image(
    image_id: str,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    repo = _require_repo()
    if repo.get_by_id(image_id) is None:
        raise HTTPException(status_code=404, detail="Image not found")
    ok = repo.delete(image_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to delete image")
    return {"deleted": True, "image_id": image_id}


# ---------------------------------------------------------------------------
# Lifecycle action routes (literal sub-paths -- out-rank the {image_id} param)
# ---------------------------------------------------------------------------


@router.post("/{image_id}/assign")
async def assign_image(
    image_id: str,
    payload: AssignIn,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Assign the image to a DESIGN_MANAGER (or unassign with null). Does not
    itself change status -- that is an explicit /status move."""
    repo = _require_repo()
    if repo.get_by_id(image_id) is None:
        raise HTTPException(status_code=404, detail="Image not found")
    updated = repo.assign(image_id, payload.assigned_to)
    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to assign image")
    return {"image": _with_id(updated)}


@router.post("/{image_id}/status")
async def set_image_status(
    image_id: str,
    payload: StatusIn,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Transition the image's lifecycle status, enforcing the valid-transition
    graph (QUEUED->IN_PROGRESS->REVIEW->APPROVED|REJECTED; REJECTED->IN_PROGRESS).

    An unknown target status is a 400; a known-but-ILLEGAL transition (or a
    same-state no-op) is a 409 (Fail Loudly). Approving an image writes a chained
    audit row, since approval gates go-live (Audit Everything).
    """
    repo = _require_repo()
    existing = repo.get_by_id(image_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Image not found")

    target = _validate_enum(payload.status, _STATUSES, "status")  # 400 if unknown
    updated = repo.set_status(image_id, target, by=current_user.get("user_id"))
    if updated is None:
        # Known status but the transition is not allowed from the current state.
        raise HTTPException(
            status_code=409,
            detail=(
                f"Illegal status transition: " f"{existing.get('status')} -> {target}"
            ),
        )
    # Approval gates go-live -> chained audit row.
    if target == "APPROVED":
        _write_audit(updated, current_user)
    return {"image": _with_id(updated)}


@router.post("/{image_id}/edited")
async def attach_edited_image(
    image_id: str,
    payload: EditedIn,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Attach the designer's edited asset (`edited_url`) + move IN_PROGRESS ->
    REVIEW. Requires the image to be IN_PROGRESS; from any other state this is a
    409 (you can't submit for review work that wasn't started)."""
    repo = _require_repo()
    existing = repo.get_by_id(image_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Image not found")

    edited_url = (payload.edited_url or "").strip()
    if not edited_url:
        raise HTTPException(status_code=400, detail="edited_url is required")

    updated = repo.attach_edited(image_id, edited_url, by=current_user.get("user_id"))
    if updated is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot attach edited asset: image must be IN_PROGRESS "
                f"(current: {existing.get('status')})"
            ),
        )
    return {"image": _with_id(updated)}


async def _fetch_image_bytes(url: str) -> bytes:
    """Fetch the RAW image bytes from its stored url -- an http(s) URL (durable
    storage / Shopify CDN) or a local server path. Raises on failure (the
    auto-edit route fail-softs)."""
    u = (url or "").strip()
    if not u:
        raise RuntimeError("image has no source url")
    if u.startswith("http://") or u.startswith("https://"):
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(u)
        if resp.status_code != 200:
            raise RuntimeError(f"could not fetch raw image ({resp.status_code})")
        return resp.content
    with open(u.lstrip("/"), "rb") as fh:  # local path, e.g. /uploads/...
        return fh.read()


@router.post("/{image_id}/auto-edit")
async def auto_edit_image(
    image_id: str,
    current_user: dict = Depends(require_roles(*_ECOM_ROLES)),
) -> Dict:
    """Auto-clean a RAW product photo via the configured DETERMINISTIC image
    editor (background -> fixed backdrop, auto-crop, synthetic shadow; product
    pixels preserved) and submit it for human review. The `council-implement`
    step of the image-editing integration.

    Slots into the existing queue: QUEUED/REJECTED -> IN_PROGRESS -> (edit) ->
    attach_edited -> REVIEW. The human APPROVE gate is untouched.

    SAFE BY DESIGN:
      * Idempotent -- an APPROVED image is never re-edited (409); one already
        EDITED + in REVIEW is returned as-is (no double-spend).
      * Fail-soft -- no provider configured, or a provider/storage/fetch error,
        KEEPS the RAW image and returns a clear status (HTTP 200, never a 500);
        the work simply stays available for manual editing.
      * Non-generative -- services/image_editor.py only does cut-out + static
        backdrop + synthetic shadow; it cannot repaint the product.
    """
    repo = _require_repo()
    img = repo.get_by_id(image_id)
    if img is None:
        raise HTTPException(status_code=404, detail="Image not found")

    status = (img.get("status") or "").upper()
    if status == "APPROVED":
        raise HTTPException(
            status_code=409, detail="Image is APPROVED -- it will not be re-edited."
        )
    if status == "REVIEW" and img.get("edited_url"):
        return {
            "auto_edit": "skipped",
            "reason": "already edited and awaiting review",
            "image": _with_id(img),
        }

    from ..services.image_editor import EditSpec, get_image_editor
    from ..services.object_storage import get_object_storage

    editor = get_image_editor()
    if not editor.available():
        return {
            "auto_edit": "skipped",
            "reason": (
                "No image editor configured. Set IMAGE_EDIT_PROVIDER=photoroom + "
                "PHOTOROOM_API_KEY (or install the self-host rembg provider)."
            ),
            "image": _with_id(img),
        }

    storage = get_object_storage()
    user_id = current_user.get("user_id")
    try:
        # attach_edited requires IN_PROGRESS; move there only from a legal state.
        if status in ("QUEUED", "REJECTED"):
            repo.set_status(image_id, "IN_PROGRESS", by=user_id)

        raw = await _fetch_image_bytes(img.get("url"))
        edited = await editor.edit(raw, EditSpec.from_env())
        key = f"{img.get('product_id') or 'product'}/{image_id}.png"
        edited_url = storage.put(key, edited, "image/png")

        updated = repo.attach_edited(image_id, edited_url, by=user_id)
        if updated is None:
            return {
                "auto_edit": "failed",
                "reason": "could not attach edited asset (image not IN_PROGRESS)",
                "edited_url": edited_url,
                "image": _with_id(img),
            }
        return {
            "auto_edit": "ok",
            "provider": editor.name,
            "storage": storage.name,
            "image": _with_id(updated),
        }
    except Exception as e:  # noqa: BLE001 -- auto-edit must never break the queue
        return {
            "auto_edit": "failed",
            "reason": str(e)[:300],
            "image": _with_id(repo.get_by_id(image_id) or img),
        }
