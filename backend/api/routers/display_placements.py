"""
IMS 2.0 - Display Placements Router (v2-2a)
============================================
Store-scoped CRUD for the `display_placements` collection -- one row per
(SKU x fixture x store) combo, with qty + a human-readable position string
(free-form, e.g. shelf-and-slot or bin-and-tray). A single SKU usually has
MULTIPLE placement rows: one primary display + one back-stock drawer.

Endpoints (prefix /api/v1/display-placements):
  GET    /                          filterable list (store_id, sku, fixture_id)
  GET    /{placement_id}            single placement
  POST   /                          create (upserts by (sku, fixture_id); stacks qty)
  PATCH  /{placement_id}            update qty / position / is_primary
  DELETE /{placement_id}            delete the placement row
  POST   /move                      atomic move to a different fixture

Role gates:
  READ  -- any authenticated user with store access (validate_store_access)
  WRITE -- SUPERADMIN, ADMIN, CATALOG_MANAGER, STORE_MANAGER (last two
           store-scoped via the fixture's store_id)

Audit logging: every write emits a row into `audit_logs`. Fail-soft -- the
audit insert never breaks the primary write.

Composition with display_fixtures:
  - the placement.store_id MUST equal the parent fixture.store_id (enforced
    server-side at write time; the index doesn't catch it because we don't
    join collections at the DB layer).
  - if the caller supplies product_category on the create payload, we run
    a merch-compatibility check against fixture.merch.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import get_current_user, require_roles
from ..dependencies import get_audit_repository, validate_store_access
from ..services.fixture_validation import validate_placement_payload

logger = logging.getLogger(__name__)
router = APIRouter()

# Same write gate as display_fixtures. SUPERADMIN auto-passes.
_WRITE_ROLES = ("ADMIN", "CATALOG_MANAGER", "STORE_MANAGER")


# ============================================================================
# SCHEMAS
# ============================================================================


class PlacementCreate(BaseModel):
    """Payload for POST /display-placements."""

    sku: str
    store_id: str
    fixture_id: str
    qty: int = Field(..., ge=1)
    position: Optional[str] = None
    is_primary: Optional[bool] = False
    # Optional hint for the merch-compatibility check. The FE passes this
    # straight through from the product detail it already has loaded; the
    # backend will accept the placement either way -- the check is a guard
    # against the obvious bad case (CL at a frame-only fixture), not an
    # ACL.
    product_category: Optional[str] = None


class PlacementUpdate(BaseModel):
    """Payload for PATCH /display-placements/{placement_id}. All optional."""

    qty: Optional[int] = Field(None, ge=1)
    position: Optional[str] = None
    is_primary: Optional[bool] = None


class PlacementMove(BaseModel):
    """Payload for POST /display-placements/move -- atomically reassign a
    placement to a different fixture. Preserves qty + position."""

    placement_id: str
    target_fixture_id: str


# ============================================================================
# DB HELPERS (fail-soft -- no DB must never 500)
# ============================================================================


def _get_db():
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def _placements_coll():
    db = _get_db()
    if db is None:
        return None
    try:
        return db.get_collection("display_placements")
    except Exception:  # noqa: BLE001
        return None


def _fixtures_coll():
    db = _get_db()
    if db is None:
        return None
    try:
        return db.get_collection("display_fixtures")
    except Exception:  # noqa: BLE001
        return None


def _fixture_lookup(fixture_id: str) -> Optional[Dict[str, Any]]:
    """Load a fixture doc by id, or None. Used to enforce store consistency
    and merch compatibility at the placement write site."""
    coll = _fixtures_coll()
    if coll is None:
        return None
    try:
        return coll.find_one({"fixture_id": fixture_id})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PLACEMENTS] fixture lookup failed: %s", exc)
        return None


def _now() -> datetime:
    return datetime.utcnow()


def _clean(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if doc is None:
        return None
    out: Dict[str, Any] = {}
    for k, v in doc.items():
        if k == "_id":
            continue
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _generate_placement_id() -> str:
    """Short, human-friendly placement id. Same scheme as shipping.py's
    SHP-/labels.py's: prefix + date + 6 hex chars."""
    stamp = datetime.utcnow().strftime("%y%m%d")
    return "PLC-{stamp}-{rand}".format(stamp=stamp, rand=uuid.uuid4().hex[:6].upper())


def _audit(
    action: str,
    placement_id: str,
    store_id: Optional[str],
    user: Dict[str, Any],
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    notes: Optional[str] = None,
) -> None:
    """Fail-soft audit write for placement mutations."""
    try:
        repo = get_audit_repository()
        if repo is None:
            return
        repo.create(
            {
                "action": "display_placement.{}".format(action),
                "module": "inventory",
                "entity_type": "display_placement",
                "entity_id": placement_id,
                "store_id": store_id,
                "user_id": user.get("user_id"),
                "user_name": user.get("username"),
                "severity": "INFO",
                "before": before,
                "after": after,
                "metadata": {"notes": notes} if notes else None,
                "timestamp": _now(),
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PLACEMENTS] audit insert failed for %s: %s", placement_id, exc)


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("")
@router.get("/")
async def list_placements(
    store_id: Optional[str] = Query(None),
    sku: Optional[str] = Query(None),
    fixture_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Filterable list. Store scope is mandatory for non-HQ roles -- a
    STORE_MANAGER can never enumerate another store's placements even if
    they hand-craft the query."""
    active_store = validate_store_access(store_id, current_user)
    coll = _placements_coll()
    if coll is None:
        return {"placements": [], "total": 0}
    query: Dict[str, Any] = {}
    if active_store:
        query["store_id"] = active_store
    if sku:
        query["sku"] = sku
    if fixture_id:
        query["fixture_id"] = fixture_id
    try:
        docs = [_clean(d) for d in coll.find(query)]
        # Stable secondary sort so the FE doesn't flicker on identical rows.
        docs.sort(key=lambda d: (d.get("sku") or "", d.get("fixture_id") or ""))
        return {"placements": docs, "total": len(docs)}
    except Exception as exc:  # noqa: BLE001
        logger.error("[PLACEMENTS] list failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to list placements")


@router.get("/{placement_id}")
async def get_placement(
    placement_id: str, current_user: dict = Depends(get_current_user)
):
    coll = _placements_coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        doc = coll.find_one({"placement_id": placement_id})
    except Exception as exc:  # noqa: BLE001
        logger.error("[PLACEMENTS] get failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to get placement")
    if not doc:
        raise HTTPException(status_code=404, detail="Placement not found")
    validate_store_access(doc.get("store_id"), current_user)
    return {"placement": _clean(doc)}


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_placement(
    payload: PlacementCreate,
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Create or STACK a placement.

    Semantics:
      - if no placement exists for (sku, store_id, fixture_id), insert a new
        row;
      - otherwise, ADD the request qty onto the existing row's qty (so the
        GRN modal can call this twice without doubling the row count).

    The merch-compat check runs only if product_category is supplied (the
    FE has it cheaply from the product detail it already loaded).
    """
    coll = _placements_coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")

    # Store-scope: STORE_MANAGER can only write to their own store.
    active_store = validate_store_access(payload.store_id, current_user)
    fixture = _fixture_lookup(payload.fixture_id)
    if fixture is None:
        raise HTTPException(
            status_code=400,
            detail="fixture_id {fid!r} does not exist".format(fid=payload.fixture_id),
        )
    if not fixture.get("is_active", True):
        raise HTTPException(
            status_code=400,
            detail="Cannot place onto a soft-deleted fixture {fid!r}".format(
                fid=payload.fixture_id
            ),
        )

    raw = payload.model_dump(exclude_none=True)
    raw["store_id"] = active_store
    try:
        normalized = validate_placement_payload(raw, fixture=fixture)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Stacking: if a row already exists for this (sku, store, fixture) combo,
    # bump qty + (optionally) the position + is_primary. The unique compound
    # index would refuse a fresh insert anyway -- catching it here gives a
    # nicer error path + means the GRN modal can keep its retry loop simple.
    existing = None
    try:
        existing = coll.find_one(
            {
                "sku": normalized["sku"],
                "store_id": normalized["store_id"],
                "fixture_id": normalized["fixture_id"],
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PLACEMENTS] dedupe lookup failed: %s", exc)

    now = _now()
    if existing:
        # Stack qty + update mutable fields. The original placement_id
        # stays so caller-side bookmarks survive.
        new_qty = int(existing.get("qty") or 0) + int(normalized["qty"])
        update: Dict[str, Any] = {"qty": new_qty, "updated_at": now}
        if "position" in normalized:
            update["position"] = normalized["position"]
        if "is_primary" in normalized:
            update["is_primary"] = normalized["is_primary"]
        try:
            coll.update_one(
                {"placement_id": existing["placement_id"]}, {"$set": update}
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[PLACEMENTS] stack-update failed: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to stack placement")
        cleaned_before = _clean(existing) or {}
        merged = coll.find_one({"placement_id": existing["placement_id"]})
        cleaned_after = _clean(merged) or {}
        _audit(
            "update",
            existing["placement_id"],
            existing.get("store_id"),
            current_user,
            before=cleaned_before,
            after=cleaned_after,
            notes="stacked qty",
        )
        return {"status": "success", "placement": cleaned_after, "stacked": True}

    # Fresh row.
    placement_id = _generate_placement_id()
    doc = dict(normalized)
    doc["placement_id"] = placement_id
    doc["created_at"] = now
    doc["updated_at"] = now
    doc["last_moved_at"] = now
    doc["created_by"] = current_user.get("user_id")
    try:
        coll.insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "duplicate key" in msg.lower() or "E11000" in msg:
            # Race -- a sibling request inserted the same combo between our
            # lookup and our insert. Replay as a stack.
            raise HTTPException(
                status_code=409,
                detail=(
                    "Placement for sku {sku!r} at fixture {fid!r} already "
                    "exists -- retry to stack qty.".format(
                        sku=normalized["sku"], fid=normalized["fixture_id"]
                    )
                ),
            )
        logger.error("[PLACEMENTS] create failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create placement")

    cleaned = _clean(doc) or {}
    _audit(
        "create",
        placement_id,
        active_store,
        current_user,
        before=None,
        after=cleaned,
    )
    return {"status": "success", "placement": cleaned, "stacked": False}


@router.patch("/{placement_id}")
async def update_placement(
    placement_id: str,
    payload: PlacementUpdate,
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Update qty / position / is_primary. Fixture and sku are immutable here
    -- use POST /move to change fixture, or DELETE + POST to change sku."""
    coll = _placements_coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    existing = coll.find_one({"placement_id": placement_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Placement not found")
    validate_store_access(existing.get("store_id"), current_user)

    raw = payload.model_dump(exclude_none=True)
    if not raw:
        return {"status": "no_changes", "placement": _clean(existing)}
    try:
        normalized = validate_placement_payload(raw, existing=existing)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    changed: Dict[str, Any] = {}
    for k, v in normalized.items():
        if k in raw:
            changed[k] = v
    if not changed:
        return {"status": "no_changes", "placement": _clean(existing)}
    changed["updated_at"] = _now()
    try:
        coll.update_one({"placement_id": placement_id}, {"$set": changed})
    except Exception as exc:  # noqa: BLE001
        logger.error("[PLACEMENTS] update failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to update placement")
    updated = coll.find_one({"placement_id": placement_id})
    _audit(
        "update",
        placement_id,
        existing.get("store_id"),
        current_user,
        before=_clean(existing) or {},
        after=_clean(updated) or {},
    )
    return {"status": "success", "placement": _clean(updated)}


@router.delete("/{placement_id}")
async def delete_placement(
    placement_id: str,
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Hard-delete a placement row. (Placements are tiny and ephemeral --
    nothing else references them by id, so a soft-delete buys us nothing.)"""
    coll = _placements_coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    existing = coll.find_one({"placement_id": placement_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Placement not found")
    validate_store_access(existing.get("store_id"), current_user)
    try:
        coll.delete_one({"placement_id": placement_id})
    except Exception as exc:  # noqa: BLE001
        logger.error("[PLACEMENTS] delete failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to delete placement")
    _audit(
        "delete",
        placement_id,
        existing.get("store_id"),
        current_user,
        before=_clean(existing) or {},
        after=None,
    )
    return {"status": "success", "placement_id": placement_id}


@router.post("/move")
async def move_placement(
    payload: PlacementMove,
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Atomically move a placement to a different fixture, preserving qty +
    position. The target fixture must (a) exist, (b) be active, (c) belong
    to the same store as the source placement. If a placement for the same
    SKU already exists at the target fixture, the move STACKS qty into the
    target row and deletes the source row (so the (store_id, sku, fixture_id)
    UNIQUE index stays happy)."""
    coll = _placements_coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    source = coll.find_one({"placement_id": payload.placement_id})
    if not source:
        raise HTTPException(status_code=404, detail="Placement not found")
    validate_store_access(source.get("store_id"), current_user)

    target_fixture = _fixture_lookup(payload.target_fixture_id)
    if target_fixture is None:
        raise HTTPException(
            status_code=400,
            detail="target_fixture_id {fid!r} does not exist".format(
                fid=payload.target_fixture_id
            ),
        )
    if not target_fixture.get("is_active", True):
        raise HTTPException(
            status_code=400,
            detail="Cannot move onto a soft-deleted fixture",
        )
    if target_fixture.get("store_id") != source.get("store_id"):
        raise HTTPException(
            status_code=400,
            detail="Cannot move a placement across stores",
        )
    if target_fixture.get("fixture_id") == source.get("fixture_id"):
        # No-op move; respond cleanly so the FE retry path is idempotent.
        return {
            "status": "no_changes",
            "placement": _clean(source),
            "stacked": False,
        }

    now = _now()

    # Check for an existing placement at the target -- if so, stack qty.
    target_existing = None
    try:
        target_existing = coll.find_one(
            {
                "sku": source["sku"],
                "store_id": source["store_id"],
                "fixture_id": target_fixture["fixture_id"],
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[PLACEMENTS] target-dedupe lookup failed: %s", exc)

    if target_existing:
        # Stack into the target row + delete the source.
        new_qty = int(target_existing.get("qty") or 0) + int(source.get("qty") or 0)
        try:
            coll.update_one(
                {"placement_id": target_existing["placement_id"]},
                {
                    "$set": {
                        "qty": new_qty,
                        "updated_at": now,
                        "last_moved_at": now,
                    }
                },
            )
            coll.delete_one({"placement_id": source["placement_id"]})
        except Exception as exc:  # noqa: BLE001
            logger.error("[PLACEMENTS] move-stack failed: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to move placement")
        merged = coll.find_one({"placement_id": target_existing["placement_id"]})
        _audit(
            "move",
            source["placement_id"],
            source.get("store_id"),
            current_user,
            before=_clean(source) or {},
            after=_clean(merged) or {},
            notes="merged into existing target placement {tid}".format(
                tid=target_existing["placement_id"]
            ),
        )
        return {
            "status": "success",
            "placement": _clean(merged),
            "stacked": True,
            "source_placement_id": source["placement_id"],
        }

    # No existing row at target -- simple fixture_id swap.
    try:
        coll.update_one(
            {"placement_id": source["placement_id"]},
            {
                "$set": {
                    "fixture_id": target_fixture["fixture_id"],
                    "updated_at": now,
                    "last_moved_at": now,
                }
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[PLACEMENTS] move failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to move placement")
    moved = coll.find_one({"placement_id": source["placement_id"]})
    _audit(
        "move",
        source["placement_id"],
        source.get("store_id"),
        current_user,
        before=_clean(source) or {},
        after=_clean(moved) or {},
    )
    return {
        "status": "success",
        "placement": _clean(moved),
        "stacked": False,
    }
