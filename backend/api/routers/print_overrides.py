"""
IMS 2.0 - Per-entity print template content overrides
=====================================================

CRUD over the `print_template_overrides` Mongo collection. The owner edits
the customer-facing fields (declaration text, signatory name + designation,
drug licence, NCAHP UID, footer terms, etc.) PER ENTITY PER TEMPLATE through
the Print module's content editor. Statutory layouts stay code-defined; only
text content is overridable.

Why entity-scope: Better Vision Opticals Pvt Ltd and WizOpt (the two live
entities) have different declarations, different signatory designations,
different drug licences. A single store can carry orders billed under
either entity, so the override has to live one level above the store --
on the entity (PAN).

Role gate: SUPERADMIN / ADMIN can edit. STORE_MANAGER and below can READ
(so the print renderer fetches the right values when running) but cannot
write -- a content change to a declaration is a statutory compliance
change with the same risk profile as editing the HSN/GST master.

Failure modes are deliberate:
  - DB down  -> GET returns the empty-override envelope; PUT 503; the
                renderer in services/print_legal.py falls back to defaults.
  - Bad role -> 403 from _require_print_admin.
  - Bad payload -> 400 / 422 from Pydantic.
Never crash the worker.

Endpoints:
  GET    /api/v1/print-overrides?entity_id=...
  GET    /api/v1/print-overrides/{entity_id}/{template_key}
  PUT    /api/v1/print-overrides/{entity_id}/{template_key}      [ADMIN]
  DELETE /api/v1/print-overrides/{entity_id}/{template_key}      [ADMIN]
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import get_current_user
from ..services.print_legal import TEMPLATE_KEYS


router = APIRouter()


# Roles permitted to write content overrides. Entity-level content edits
# fan out across every print of that template under the entity, so the
# blast radius matches HSN/GST master edits -- ADMIN tier only.
_WRITE_ROLES = ("SUPERADMIN", "ADMIN")
_COLLECTION = "print_template_overrides"


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class OverrideFields(BaseModel):
    """The editable fields. Every key is optional; `null` means "use default".
    Empty strings are also treated as "no override" by the renderer (so the
    editor can't accidentally blank a required statutory field -- the owner
    uses DELETE to revert to defaults).
    """

    header_subtitle: Optional[str] = Field(
        None, description="Tagline under the entity name (e.g. 'Eyewear since 2014')."
    )
    declaration_text: Optional[str] = Field(
        None, description="Overrides the default CGST-compliant declaration."
    )
    signatory_name: Optional[str] = Field(
        None, description="Authorised signatory printed above [Seal]."
    )
    signatory_designation: Optional[str] = Field(
        None,
        description=(
            "Authorised signatory designation (e.g. 'Director' / "
            "'Authorised Signatory')."
        ),
    )
    drug_licence_no: Optional[str] = Field(
        None,
        description=(
            "D&C Drug Licence number (required by stores dispensing contact lenses)."
        ),
    )
    ncahp_uid: Optional[str] = Field(
        None,
        description=(
            "NCAHP UID of the practising optometrist (Rx Card only; "
            "mandatory since 2024)."
        ),
    )
    dmc_reg: Optional[str] = Field(
        None,
        description=(
            "State Medical Council registration number (e.g. 'DMC/R-4412/2014') "
            "for the practising optometrist; Rx Card only."
        ),
    )
    footer_terms: Optional[str] = Field(
        None, description="Free-text payment terms / warranty / jurisdiction note."
    )
    logo_url: Optional[str] = Field(None, description="Custom logo URL.")
    retention_years: Optional[int] = Field(
        None,
        ge=1,
        le=20,
        description="Document retention years (defaults to 7 per CGST Rule 56).",
    )
    reverse_charge_default: Optional[bool] = Field(
        None, description="Default the 'Reverse Charge' flag to Yes for this template."
    )


class OverridePut(BaseModel):
    fields: OverrideFields = Field(default_factory=OverrideFields)


class OverrideOut(BaseModel):
    override_id: str
    entity_id: str
    template_key: str
    fields: Dict[str, Any]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    created_by: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _require_print_admin(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Gate writes to SUPERADMIN/ADMIN. Reads stay open to any
    authenticated user via the routes that don't depend on this."""
    roles = current_user.get("roles", []) if current_user else []
    if not any(r in _WRITE_ROLES for r in roles):
        raise HTTPException(
            status_code=403,
            detail=(
                "Only SUPERADMIN or ADMIN can edit print template content. "
                "Entity-level content edits affect every print of this template."
            ),
        )
    return current_user


def _coll():
    """Return the overrides Mongo collection, or None when the DB is offline."""
    try:
        from database.connection import (
            get_db,
        )  # local import keeps import-time fail-soft

        db = get_db()
        if db and db.is_connected:
            return db.get_collection(_COLLECTION)
    except Exception:  # pragma: no cover - defensive
        pass
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scrub(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Drop Mongo's _id from a doc for JSON serialisation."""
    if doc is None:
        return None
    doc.pop("_id", None)
    return doc


def _validate_template_key(template_key: str) -> str:
    key = str(template_key or "").strip().lower()
    if key not in TEMPLATE_KEYS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unknown template_key '{0}'. Allowed: {1}".format(
                    template_key, ", ".join(TEMPLATE_KEYS)
                )
            ),
        )
    return key


def _validate_entity_id(entity_id: str) -> str:
    eid = str(entity_id or "").strip()
    if not eid:
        raise HTTPException(status_code=422, detail="entity_id is required")
    return eid


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("")
async def list_overrides(
    entity_id: str = Query(..., description="Entity (PAN) whose overrides to list."),
    current_user: dict = Depends(get_current_user),  # noqa: ARG001 - auth gate
) -> Dict[str, Any]:
    """Every override row for an entity. Read-open to any authenticated user
    so the print renderer can resolve overrides without elevating role."""
    _ = current_user  # ensure dependency is exercised (auth required)
    eid = _validate_entity_id(entity_id)
    coll = _coll()
    if coll is None:
        return {"entity_id": eid, "overrides": [], "total": 0}
    rows: List[Dict[str, Any]] = []
    try:
        for doc in coll.find({"entity_id": eid}):
            scrubbed = _scrub(doc)
            if scrubbed is not None:
                rows.append(scrubbed)
    except Exception:  # pragma: no cover - defensive
        rows = []
    rows.sort(key=lambda d: d.get("template_key", ""))
    return {"entity_id": eid, "overrides": rows, "total": len(rows)}


@router.get("/{entity_id}/{template_key}")
async def get_override(
    entity_id: str,
    template_key: str,
    current_user: dict = Depends(get_current_user),  # noqa: ARG001 - auth gate
) -> Dict[str, Any]:
    """Fetch one override row. Returns an empty `fields: {}` shape when no
    row exists yet (so the editor UI can pre-populate from sensible defaults)."""
    _ = current_user
    eid = _validate_entity_id(entity_id)
    key = _validate_template_key(template_key)
    coll = _coll()
    if coll is None:
        return {
            "override_id": "",
            "entity_id": eid,
            "template_key": key,
            "fields": {},
            "exists": False,
        }
    try:
        doc = coll.find_one({"entity_id": eid, "template_key": key})
    except Exception:  # pragma: no cover - defensive
        doc = None
    if doc is None:
        return {
            "override_id": "",
            "entity_id": eid,
            "template_key": key,
            "fields": {},
            "exists": False,
        }
    scrubbed = _scrub(doc) or {}
    scrubbed["exists"] = True
    return scrubbed


@router.put("/{entity_id}/{template_key}")
async def upsert_override(
    entity_id: str,
    template_key: str,
    payload: OverridePut,
    current_user: dict = Depends(_require_print_admin),
) -> Dict[str, Any]:
    """Upsert the override doc for (entity_id, template_key).

    Returns the persisted document. Fields set to `None` (or omitted) are
    not stored -- the default flows through at render time.
    """
    eid = _validate_entity_id(entity_id)
    key = _validate_template_key(template_key)
    coll = _coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    # Trim empty strings on write; the renderer also ignores them but
    # storing them is wasteful and surfaces in the editor as "set" when
    # the owner thought they cleared it.
    fields_dict: Dict[str, Any] = {}
    for k, v in payload.fields.model_dump(exclude_none=True).items():
        if isinstance(v, str) and not v.strip():
            continue
        fields_dict[k] = v

    now = _now_iso()
    actor = str(current_user.get("username") or current_user.get("user_id") or "")

    try:
        existing = coll.find_one({"entity_id": eid, "template_key": key})
    except Exception:  # pragma: no cover - defensive
        existing = None

    if existing:
        update_doc = {
            "fields": fields_dict,
            "updated_at": now,
            "updated_by": actor,
        }
        try:
            coll.update_one(
                {"entity_id": eid, "template_key": key},
                {"$set": update_doc},
            )
        except Exception as e:  # pragma: no cover - defensive
            raise HTTPException(status_code=503, detail="Database write failed") from e
        try:
            doc = coll.find_one({"entity_id": eid, "template_key": key})
        except Exception:  # pragma: no cover - defensive
            doc = None
        result = _scrub(doc) or {
            "override_id": existing.get("override_id", ""),
            "entity_id": eid,
            "template_key": key,
            "fields": fields_dict,
            "updated_at": now,
            "updated_by": actor,
        }
        result["exists"] = True
        return result

    new_id = str(uuid.uuid4())
    new_doc: Dict[str, Any] = {
        "_id": new_id,
        "override_id": new_id,
        "entity_id": eid,
        "template_key": key,
        "fields": fields_dict,
        "created_at": now,
        "updated_at": now,
        "created_by": actor,
        "updated_by": actor,
    }
    try:
        coll.insert_one(new_doc)
    except Exception as e:  # pragma: no cover - defensive
        raise HTTPException(status_code=503, detail="Database write failed") from e
    result = _scrub(dict(new_doc))
    if result is not None:
        result["exists"] = True
    else:  # pragma: no cover - belt-and-braces
        result = {
            "override_id": new_id,
            "entity_id": eid,
            "template_key": key,
            "fields": fields_dict,
            "exists": True,
        }
    return result


@router.delete("/{entity_id}/{template_key}")
async def delete_override(
    entity_id: str,
    template_key: str,
    current_user: dict = Depends(_require_print_admin),  # noqa: ARG001 - auth gate
) -> Dict[str, Any]:
    """Revert to defaults. Removes the override row entirely; the next render
    of this entity/template uses sensible CGST/NCAHP defaults again."""
    _ = current_user
    eid = _validate_entity_id(entity_id)
    key = _validate_template_key(template_key)
    coll = _coll()
    if coll is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        res = coll.delete_one({"entity_id": eid, "template_key": key})
        deleted = bool(getattr(res, "deleted_count", 0))
    except Exception as e:  # pragma: no cover - defensive
        raise HTTPException(status_code=503, detail="Database write failed") from e
    return {
        "deleted": deleted,
        "entity_id": eid,
        "template_key": key,
    }


# Re-export the template key catalog for the editor UI.
@router.get("/_meta/templates")
async def list_template_keys(
    current_user: dict = Depends(get_current_user),  # noqa: ARG001 - auth gate
) -> Dict[str, Any]:
    """Surface the canonical list of editable templates + their addressable
    fields so the frontend doesn't hard-code the list."""
    _ = current_user
    field_specs: List[Dict[str, Any]] = []
    schema = OverrideFields.model_json_schema()
    properties = schema.get("properties", {}) or {}
    for fname, fdef in properties.items():
        field_specs.append(
            {
                "name": fname,
                "label": fname.replace("_", " ").title(),
                "description": fdef.get("description", ""),
                "type": fdef.get("type", "string"),
            }
        )
    return {
        "templates": [
            {
                "key": k,
                "label": k.replace("_", " ").title(),
            }
            for k in TEMPLATE_KEYS
        ],
        "fields": field_specs,
    }
