"""
IMS 2.0 - Legal Entities Router
================================
A legal entity (PAN) groups one or more stores for statutory purposes.
One entity can hold multiple GSTINs (one per state). Payroll filings
(PF/ESI/PT/TDS) and GST returns are grouped per entity.

Entity management is ADMIN/SUPERADMIN only.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timezone
import uuid
import logging

logger = logging.getLogger(__name__)

from .auth import get_current_user, require_roles

import sys
import os

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

try:
    from database.connection import get_db

    DATABASE_AVAILABLE = True
except ImportError:
    DATABASE_AVAILABLE = False

router = APIRouter()

# Entity management is highly sensitive (statutory identity of the business).
_ENTITY_ADMIN = ("ADMIN",)  # SUPERADMIN auto-passes inside require_roles


# ============================================================================
# SCHEMAS
# ============================================================================


class GstinEntry(BaseModel):
    gstin: str
    state_code: str
    state_name: Optional[str] = None


class PtRegistration(BaseModel):
    state_code: str
    registration_number: str


class PfInfo(BaseModel):
    registered: bool = False
    establishment_code: Optional[str] = None


class EsiInfo(BaseModel):
    registered: bool = False
    code: Optional[str] = None


class EntityCreate(BaseModel):
    name: str = Field(..., min_length=2)
    legal_name: Optional[str] = None
    pan: Optional[str] = None
    tan: Optional[str] = None
    registered_address: Optional[str] = None
    gstins: List[GstinEntry] = Field(default_factory=list)
    pf: PfInfo = Field(default_factory=PfInfo)
    esi: EsiInfo = Field(default_factory=EsiInfo)
    pt_registrations: List[PtRegistration] = Field(default_factory=list)
    bank_account_no: Optional[str] = None
    bank_ifsc: Optional[str] = None
    bank_name: Optional[str] = None


class EntityUpdate(BaseModel):
    name: Optional[str] = None
    legal_name: Optional[str] = None
    pan: Optional[str] = None
    tan: Optional[str] = None
    registered_address: Optional[str] = None
    gstins: Optional[List[GstinEntry]] = None
    pf: Optional[PfInfo] = None
    esi: Optional[EsiInfo] = None
    pt_registrations: Optional[List[PtRegistration]] = None
    bank_account_no: Optional[str] = None
    bank_ifsc: Optional[str] = None
    bank_name: Optional[str] = None
    is_active: Optional[bool] = None


# ============================================================================
# HELPERS
# ============================================================================


def _get_db():
    if not DATABASE_AVAILABLE:
        return None
    return get_db()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(doc: dict) -> dict:
    """Drop Mongo's _id so the doc is JSON-serializable."""
    if doc and "_id" in doc:
        doc = {k: v for k, v in doc.items() if k != "_id"}
    return doc


def resolve_entity_for_store(db, store_id: Optional[str]) -> Optional[dict]:
    """Return the entity doc that owns a store, or None.

    Reusable by payroll / finance to group records by legal entity. Looks up
    the store's entity_id and fetches the entity. Fail-soft.
    """
    if db is None or not store_id:
        return None
    try:
        store = db.get_collection("stores").find_one({"store_id": store_id})
        if not store:
            return None
        entity_id = store.get("entity_id")
        if not entity_id:
            return None
        return _clean(db.get_collection("entities").find_one({"entity_id": entity_id}) or {}) or None
    except Exception as e:  # pragma: no cover - defensive
        logger.error("resolve_entity_for_store failed: %s", e)
        return None


# ============================================================================
# ENTITY CRUD
# ============================================================================


@router.get("")
@router.get("/")
async def list_entities(
    include_inactive: bool = Query(False),
    current_user: dict = Depends(get_current_user),
):
    """List legal entities (any authenticated user can read the org structure)."""
    db = _get_db()
    if db is None:
        return {"entities": [], "total": 0}
    try:
        query = {} if include_inactive else {"is_active": True}
        entities = [_clean(e) for e in db.get_collection("entities").find(query)]
        return {"entities": entities, "total": len(entities)}
    except Exception as e:
        logger.error("list_entities failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to list entities")


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_entity(
    payload: EntityCreate,
    current_user: dict = Depends(require_roles(*_ENTITY_ADMIN)),
):
    """Create a legal entity (ADMIN/SUPERADMIN)."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        coll = db.get_collection("entities")
        doc = payload.model_dump()
        doc.update(
            {
                "entity_id": f"ent_{uuid.uuid4().hex[:12]}",
                "is_active": True,
                "created_at": _now(),
                "updated_at": _now(),
                "created_by": current_user.get("user_id"),
            }
        )
        coll.insert_one(doc)
        return {"status": "success", "entity": _clean(doc)}
    except Exception as e:
        logger.error("create_entity failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to create entity")


@router.get("/{entity_id}")
async def get_entity(entity_id: str, current_user: dict = Depends(get_current_user)):
    db = _get_db()
    if db is None:
        return {"entity": None}
    try:
        entity = db.get_collection("entities").find_one({"entity_id": entity_id})
        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")
        return {"entity": _clean(entity)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_entity failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to get entity")


@router.put("/{entity_id}")
async def update_entity(
    entity_id: str,
    payload: EntityUpdate,
    current_user: dict = Depends(require_roles(*_ENTITY_ADMIN)),
):
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        coll = db.get_collection("entities")
        existing = coll.find_one({"entity_id": entity_id})
        if not existing:
            raise HTTPException(status_code=404, detail="Entity not found")
        updates = {k: v for k, v in payload.model_dump().items() if v is not None}
        if not updates:
            return {"status": "no_changes", "entity": _clean(existing)}
        updates["updated_at"] = _now()
        coll.update_one({"entity_id": entity_id}, {"$set": updates})
        return {"status": "success", "entity": _clean(coll.find_one({"entity_id": entity_id}))}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("update_entity failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to update entity")


# ============================================================================
# STORE <-> ENTITY MAPPING
# ============================================================================


@router.get("/{entity_id}/stores")
async def list_entity_stores(
    entity_id: str, current_user: dict = Depends(get_current_user)
):
    """Stores that belong to a legal entity."""
    db = _get_db()
    if db is None:
        return {"stores": [], "total": 0}
    try:
        stores = [
            _clean(s) for s in db.get_collection("stores").find({"entity_id": entity_id})
        ]
        return {"entity_id": entity_id, "stores": stores, "total": len(stores)}
    except Exception as e:
        logger.error("list_entity_stores failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to list entity stores")


@router.post("/{entity_id}/stores/{store_id}")
async def assign_store_to_entity(
    entity_id: str,
    store_id: str,
    current_user: dict = Depends(require_roles(*_ENTITY_ADMIN)),
):
    """Assign a store to a legal entity (ADMIN/SUPERADMIN)."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        entity = db.get_collection("entities").find_one({"entity_id": entity_id})
        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")
        result = db.get_collection("stores").update_one(
            {"store_id": store_id}, {"$set": {"entity_id": entity_id}}
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Store not found")
        return {"status": "success", "entity_id": entity_id, "store_id": store_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("assign_store_to_entity failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to assign store")


@router.delete("/{entity_id}/stores/{store_id}")
async def unassign_store_from_entity(
    entity_id: str,
    store_id: str,
    current_user: dict = Depends(require_roles(*_ENTITY_ADMIN)),
):
    """Remove a store's entity assignment (ADMIN/SUPERADMIN)."""
    db = _get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database not available")
    try:
        result = db.get_collection("stores").update_one(
            {"store_id": store_id, "entity_id": entity_id},
            {"$set": {"entity_id": None}},
        )
        if result.matched_count == 0:
            raise HTTPException(
                status_code=404, detail="Store not found or not assigned to this entity"
            )
        return {"status": "success", "store_id": store_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("unassign_store_from_entity failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to unassign store")
