"""
IMS 2.0 - Display Fixtures Router (v2-2a)
=========================================
Store-scoped CRUD for the `display_fixtures` collection -- the master list
of physical display fixtures (window, wall, pillar, counter, cabinet,
gondola, drawer, fridge) per store. See docs/design/INTENT.md (Inventory ->
Display fixture system) for the business intent.

Endpoints (prefix /api/v1/display-fixtures):
  GET    /                            list, store-scoped, filterable
  GET    /{fixture_id}                single
  POST   /                            create
  PATCH  /{fixture_id}                update
  DELETE /{fixture_id}                soft-delete (refuse hard-delete if active placements)
  GET    /meta/options                dropdown values for the FE filter strip

Role gates:
  READ  -- any authenticated user with store access (validate_store_access)
  WRITE -- SUPERADMIN, ADMIN, CATALOG_MANAGER, STORE_MANAGER (last two are
           store-scoped to their own store; validate_store_access enforces).

Audit logging: every write emits a row into `audit_logs` via the existing
get_audit_repository().create() helper. Fail-soft -- the audit insert never
breaks the primary write.

Empty state on first deploy. NO seed data. The owner creates the first
fixtures via the UI (v2-2b).
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import get_current_user, require_roles
from ..dependencies import get_audit_repository, validate_store_access
from ..services.fixture_validation import (
    CATALOG_TYPES,
    FIXTURE_FLOORS,
    FIXTURE_TYPES,
    FIXTURE_ZONES,
    validate_fixture_payload,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Roles allowed to MUTATE fixtures. SUPERADMIN auto-passes inside require_roles.
_WRITE_ROLES = ("ADMIN", "CATALOG_MANAGER", "STORE_MANAGER")


# ============================================================================
# SCHEMAS
# ============================================================================


class FixtureCreate(BaseModel):
    """Payload for POST /display-fixtures."""

    store_id: str
    code: str
    name: str
    type: str
    floor: str
    zone: str
    capacity: int = Field(..., ge=1)
    merch: List[str] = Field(default_factory=list)
    lockable: Optional[bool] = False
    mannequin: Optional[bool] = None
    spotlit: Optional[bool] = None
    temp_ctrl: Optional[str] = None
    no_qr: Optional[bool] = None
    key_holder: Optional[str] = None
    notes: Optional[str] = None
    fixture_id: Optional[str] = None  # caller may pass a slug; auto-derived otherwise


class FixtureUpdate(BaseModel):
    """Payload for PATCH /display-fixtures/{fixture_id}. All fields optional."""

    code: Optional[str] = None
    name: Optional[str] = None
    type: Optional[str] = None
    floor: Optional[str] = None
    zone: Optional[str] = None
    capacity: Optional[int] = Field(None, ge=1)
    merch: Optional[List[str]] = None
    lockable: Optional[bool] = None
    mannequin: Optional[bool] = None
    spotlit: Optional[bool] = None
    temp_ctrl: Optional[str] = None
    no_qr: Optional[bool] = None
    key_holder: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


# ============================================================================
# DB HELPERS (fail-soft -- no DB must never 500)
# ============================================================================


def _get_db():
    """Raw MongoDB handle, or None when unavailable (mock / no-DB mode)."""
    try:
        from ..dependencies import get_db

        conn = get_db()
        if conn is not None and getattr(conn, "is_connected", False):
            return conn.db
    except Exception:  # noqa: BLE001
        pass
    return None


def _fixtures_coll():
    db = _get_db()
    if db is None:
        return None
    try:
        return db.get_collection("display_fixtures")
    except Exception:  # noqa: BLE001
        return None


def _placements_coll():
    """Used to count active placements before a hard-delete; cross-collection
    integrity guard."""
    db = _get_db()
    if db is None:
        return None
    try:
        return db.get_collection("display_placements")
    except Exception:  # noqa: BLE001
        return None


def _now() -> datetime:
    """UTC timestamp -- Mongo stores it as a BSON date when ingested via the
    pymongo driver."""
    return datetime.utcnow()


def _now_iso() -> str:
    """ISO string variant for fields that already store ISO strings on this
    repo (most newer routers use UTC datetime; entities.py uses ISO strings).
    Returns naive UTC ISO."""
    return datetime.utcnow().isoformat()


def _clean(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Strip Mongo _id + coerce datetime to ISO so the response JSON-encodes
    safely on every driver version."""
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


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _derive_fixture_id(code: str) -> str:
    """Slug a fixture code into a stable id, e.g. 'WD-01' -> 'wd-01'. The id
    is supposed to be human-readable on URLs; a 6-char random suffix only
    kicks in if the caller-provided code is empty (shouldn't happen since
    validate_fixture_payload requires `code`)."""
    slug = _SLUG_RE.sub("-", (code or "").lower()).strip("-")
    if not slug:
        slug = uuid.uuid4().hex[:8]
    return slug


def _audit(
    action: str,
    fixture_id: str,
    store_id: Optional[str],
    user: Dict[str, Any],
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    notes: Optional[str] = None,
) -> None:
    """Write an audit row for a fixture mutation. Fail-soft: any error is
    logged and swallowed so the primary write is never undone by an audit
    failure. Severity stays INFO for routine CRUD; the schema requires
    `module` so we tag everything 'inventory'."""
    try:
        repo = get_audit_repository()
        if repo is None:
            return
        repo.create(
            {
                "action": "display_fixture.{}".format(action),
                "module": "inventory",
                "entity_type": "display_fixture",
                "entity_id": fixture_id,
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
        logger.warning("[FIXTURES] audit insert failed for %s: %s", fixture_id, exc)


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.get("/meta/options")
async def fixture_meta_options(current_user: dict = Depends(get_current_user)):
    """Dropdown data for the FE filter strip + edit modal (v2-2b).
    Mounted ABOVE /{fixture_id} so a fixture called 'meta' could never shadow
    it. Two-segment paths are never captured by the single-segment route."""
    _ = current_user  # silence unused-arg lint; auth dep enforces login
    return {
        "types": list(FIXTURE_TYPES),
        "floors": list(FIXTURE_FLOORS),
        "zones": list(FIXTURE_ZONES),
        "catalog_types": list(CATALOG_TYPES),
    }


@router.get("")
@router.get("/")
async def list_fixtures(
    store_id: Optional[str] = Query(None),
    type: Optional[str] = Query(None),  # noqa: A002 - matches the request param
    floor: Optional[str] = Query(None),
    zone: Optional[str] = Query(None),
    active: bool = Query(True),
    current_user: dict = Depends(get_current_user),
):
    """List fixtures, store-scoped. active=True (default) hides soft-deleted."""
    active_store = validate_store_access(store_id, current_user)
    coll = _fixtures_coll()
    if coll is None:
        return {"fixtures": [], "total": 0}

    query: Dict[str, Any] = {}
    if active_store:
        query["store_id"] = active_store
    if active:
        query["is_active"] = {"$ne": False}
    if type:
        query["type"] = type
    if floor:
        query["floor"] = floor
    if zone:
        query["zone"] = zone
    try:
        docs = [_clean(d) for d in coll.find(query)]
        # Stable sort: floor, then code -- keeps the FE happy without a $sort
        # stage that could fight an existing index hint on the back end.
        docs.sort(key=lambda d: (d.get("floor") or "", d.get("code") or ""))
        return {"fixtures": docs, "total": len(docs)}
    except Exception as exc:  # noqa: BLE001
        logger.error("[FIXTURES] list_fixtures failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to list fixtures")


@router.get("/{fixture_id}")
async def get_fixture(fixture_id: str, current_user: dict = Depends(get_current_user)):
    """Single fixture by id. 404 when missing or not in the user's store."""
    coll = _fixtures_coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        doc = coll.find_one({"fixture_id": fixture_id})
    except Exception as exc:  # noqa: BLE001
        logger.error("[FIXTURES] get_fixture failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to get fixture")
    if not doc:
        raise HTTPException(status_code=404, detail="Fixture not found")
    # Store scoping -- non-HQ roles only see their own store's fixtures. The
    # validate_store_access call 403s if they try to look at someone else's.
    validate_store_access(doc.get("store_id"), current_user)
    return {"fixture": _clean(doc)}


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_fixture(
    payload: FixtureCreate,
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Create a new fixture. Code must be unique within the store (enforced
    by the (store_id, code) UNIQUE index). Returns the new doc."""
    coll = _fixtures_coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")

    # Store-scope: a STORE_MANAGER can only create fixtures in their own store.
    # validate_store_access 403s otherwise. SUPERADMIN/ADMIN auto-pass.
    active_store = validate_store_access(payload.store_id, current_user)

    raw = payload.model_dump(exclude_none=True)
    raw["store_id"] = active_store
    try:
        normalized = validate_fixture_payload(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    fixture_id = (payload.fixture_id or "").strip().lower() or _derive_fixture_id(
        normalized["code"]
    )

    doc = dict(normalized)
    doc["fixture_id"] = fixture_id
    now = _now()
    doc["created_at"] = now
    doc["updated_at"] = now
    doc["created_by"] = current_user.get("user_id")

    try:
        coll.insert_one(doc)
    except Exception as exc:  # noqa: BLE001
        # Most likely cause: duplicate (store_id, code). The unique index
        # raises DuplicateKeyError -- surface as 409 with a helpful body.
        msg = str(exc)
        if "duplicate key" in msg.lower() or "E11000" in msg:
            raise HTTPException(
                status_code=409,
                detail=(
                    "A fixture with code {code!r} already exists in this "
                    "store.".format(code=normalized["code"])
                ),
            )
        logger.error("[FIXTURES] create_fixture failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create fixture")

    cleaned = _clean(doc) or {}
    _audit("create", fixture_id, active_store, current_user, before=None, after=cleaned)
    return {"status": "success", "fixture": cleaned}


@router.patch("/{fixture_id}")
async def update_fixture(
    fixture_id: str,
    payload: FixtureUpdate,
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Update a fixture. PATCH semantics: any field omitted is left alone.
    Cannot change store_id (a fixture is born into a store). Returns the
    updated doc."""
    coll = _fixtures_coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")

    existing = coll.find_one({"fixture_id": fixture_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Fixture not found")
    # Store-scope guard -- STORE_MANAGER can only patch their own store.
    validate_store_access(existing.get("store_id"), current_user)

    raw = payload.model_dump(exclude_none=True)
    if not raw:
        return {"status": "no_changes", "fixture": _clean(existing)}
    # Don't let a PATCH leak in a store_id swap.
    raw.pop("store_id", None)

    try:
        normalized = validate_fixture_payload(raw, existing=existing)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # validate_fixture_payload returns the FULL canonical shape (using
    # existing for fall-back). Filter down to only the fields the caller
    # actually changed (plus updated_at). This keeps the diff in audit_logs
    # honest.
    changed: Dict[str, Any] = {}
    for k, v in normalized.items():
        if k in raw:
            changed[k] = v
    if not changed:
        return {"status": "no_changes", "fixture": _clean(existing)}
    changed["updated_at"] = _now()

    try:
        coll.update_one({"fixture_id": fixture_id}, {"$set": changed})
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "duplicate key" in msg.lower() or "E11000" in msg:
            raise HTTPException(
                status_code=409,
                detail=(
                    "A fixture with code {code!r} already exists in this "
                    "store.".format(code=changed.get("code"))
                ),
            )
        logger.error("[FIXTURES] update_fixture failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to update fixture")

    updated = coll.find_one({"fixture_id": fixture_id})
    cleaned_before = _clean(existing) or {}
    cleaned_after = _clean(updated) or {}
    _audit(
        "update",
        fixture_id,
        existing.get("store_id"),
        current_user,
        before=cleaned_before,
        after=cleaned_after,
    )
    return {"status": "success", "fixture": cleaned_after}


@router.delete("/{fixture_id}")
async def delete_fixture(
    fixture_id: str,
    current_user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    """Soft-delete a fixture (set is_active=False). Refuses if any active
    placement still references it -- the owner must move/clear the SKUs
    first. Returns 409 with the dependent placement count + a hint."""
    coll = _fixtures_coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database not available")
    existing = coll.find_one({"fixture_id": fixture_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Fixture not found")
    validate_store_access(existing.get("store_id"), current_user)

    # Refuse if any placement still references this fixture (irrespective of
    # whether the fixture is currently active -- you can't soft-delete a
    # fixture that staff still think is loaded).
    placements = _placements_coll()
    if placements is not None:
        try:
            n = placements.count_documents({"fixture_id": fixture_id})
        except Exception:  # noqa: BLE001
            n = 0
        if n:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot delete fixture: {n} placement(s) still reference "
                    "it. Move or delete those placements first.".format(n=n)
                ),
            )

    try:
        coll.update_one(
            {"fixture_id": fixture_id},
            {"$set": {"is_active": False, "updated_at": _now()}},
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("[FIXTURES] delete_fixture failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to delete fixture")

    cleaned_before = _clean(existing) or {}
    _audit(
        "delete",
        fixture_id,
        existing.get("store_id"),
        current_user,
        before=cleaned_before,
        after=None,
        notes="soft-delete (is_active=False)",
    )
    return {"status": "success", "fixture_id": fixture_id}
