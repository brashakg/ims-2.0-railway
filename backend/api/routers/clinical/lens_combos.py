"""CLI-9 named lens-power combos (save-and-reuse Rx templates).

Moved verbatim out of the 3,158-line api/routers/clinical.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
import uuid
from ..auth import require_roles
from ...dependencies import (
    get_db,
    validate_store_access,
    can_access_store_scoped,
    user_store_scope,
)
from ._shared import _CLINICAL_ROLES, _COMBO_READ_ROLES, router
from .helpers import _validate_eye_test_rx


# ============================================================================
# CLI-9 — Named lens-power combos (save-and-reuse Rx templates)
# ============================================================================
# Optometrists frequently reuse the same lens-power combination for a type of
# patient (e.g. "Myopia mild SVS: -1.00/-0.50x180 both eyes" or "Bifocal
# standard: +2.00 ADD +1.50"). Saving these as named combos speeds up repeat
# Rx entry and reduces transcription errors.
#
# Storage: `lens_power_combos` Mongo collection, per-store, per-creator.
# Schema: { combo_id, store_id, created_by, name, right_eye{}, left_eye{}, pd,
#           notes, created_at, updated_at }
# No sensitive data; fail-soft if DB is absent.
# ============================================================================


class LensPowerComboCreate(BaseModel):
    """Payload for POST /clinical/lens-power-combos."""

    name: str = Field(..., min_length=1, max_length=100)
    right_eye: Optional[dict] = None
    left_eye: Optional[dict] = None
    pd: Optional[str] = None
    notes: Optional[str] = Field(None, max_length=500)

    class Config:
        populate_by_name = True


def _get_lens_power_combos_col():
    """Fail-soft collection accessor."""
    try:
        db = get_db()
        if db is None:
            return None
        return db.get_collection("lens_power_combos")
    except Exception:
        return None


@router.get("/lens-power-combos")
async def list_lens_power_combos(
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_COMBO_READ_ROLES)),
):
    """List saved lens-power combos visible to the caller.

    Returns the caller's own combos plus any combos created by anyone in the
    same store — so shared institutional templates surface automatically.
    Fail-soft: empty list if DB absent.

    The role gate is EXPLICIT (_COMBO_READ_ROLES) rather than leaning on the
    POLICY row alone. It is byte-identical to that row, so the effective access
    is unchanged -- but every sibling in this router states its own gate, and a
    gate that lives in only one layer is exactly the asymmetry that produced the
    malformed-tuple bug on the write twins.
    """
    col = _get_lens_power_combos_col()
    if col is None:
        return {"combos": [], "total": 0}
    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    flt: dict = {}
    if active_store:
        flt["store_id"] = active_store
    elif not user_store_scope(current_user)[0]:
        # A store-level caller whose store did not resolve (no ?store_id and a
        # null active_store_id -- reachable, see auth.py's token builders). An
        # empty filter would list EVERY store's templates, so fail CLOSED. Only
        # cross-store roles (SUPERADMIN/ADMIN) get the unfiltered all-stores read.
        return {"combos": [], "total": 0}
    try:
        docs = list(col.find(flt, {"_id": 0}).sort("created_at", -1).limit(200))
    except Exception:
        docs = []
    return {"combos": docs, "total": len(docs)}


@router.post("/lens-power-combos")
async def create_lens_power_combo(
    payload: LensPowerComboCreate,
    store_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_roles(*_CLINICAL_ROLES)),
):
    """Save a named lens-power combination for reuse.

    Gated to clinical roles (OPTOMETRIST / STORE_MANAGER / ADMIN / SUPERADMIN).
    The combo is visible to everyone in the same store so institutional
    templates can be shared without per-user configuration.

    A combo is Rx data that gets loaded straight into a patient's Rx, so it goes
    through the SAME canonical validation as a captured eye test (422 on a bad
    power). In particular a toric combo (non-zero CYL) must carry a whole-degree
    axis -- otherwise the un-grindable Rx is reused on every future patient the
    template is applied to.
    """
    _validate_eye_test_rx("Right eye", payload.right_eye or {})
    _validate_eye_test_rx("Left eye", payload.left_eye or {})

    col = _get_lens_power_combos_col()
    if col is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    active_store = validate_store_access(store_id, current_user) or current_user.get("active_store_id")
    # Refuse to MINT an unattributed combo. A store-less session (auth.py yields
    # active_store_id=None for a user with no store, e.g. a plain ADMIN) would
    # otherwise stamp store_id=None, and an unattributed doc is precisely the
    # shape the delete guard has to fail closed on. Never create the ambiguity
    # in the first place -- the combo is a per-store shared template, so a store
    # is part of its identity, not an optional decoration.
    if not active_store:
        raise HTTPException(
            status_code=400,
            detail="Select an active store before saving a lens-power combo",
        )
    now_iso = datetime.utcnow().isoformat()
    combo_id = str(uuid.uuid4())

    doc = {
        "combo_id": combo_id,
        "store_id": active_store,
        "created_by": current_user.get("user_id"),
        "created_by_name": current_user.get("full_name") or current_user.get("username"),
        "name": payload.name.strip(),
        "right_eye": payload.right_eye or {},
        "left_eye": payload.left_eye or {},
        "pd": payload.pd,
        "notes": payload.notes,
        "created_at": now_iso,
        "updated_at": now_iso,
    }
    try:
        col.insert_one(doc)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not save combo") from exc

    # Return without the Mongo _id
    doc.pop("_id", None)
    return doc


@router.delete("/lens-power-combos/{combo_id}")
async def delete_lens_power_combo(
    combo_id: str,
    current_user: dict = Depends(require_roles(*_CLINICAL_ROLES)),
):
    """Delete a named lens-power combo.

    Only the creator or a manager/admin may delete -- and only within their own
    store. Fail-soft 404 if the combo doesn't exist (idempotent).

    DELETE is deliberately NARROWER than the create gate even though both share
    _CLINICAL_ROLES: a combo is a SHARED store template, so one optometrist must
    not be able to bin a colleague's (or another store's) template. The role
    gate admits the clinical roles; these per-OBJECT checks -- store scope, then
    ownership -- are the data-level half the middleware never evaluates:

      * store scope  -> 404 (never 403), same existence-hiding posture as
        _store_scope_or_404, so an out-of-scope caller cannot even probe for the
        combo's existence by id.
      * ownership    -> OPTOMETRIST may delete only combos they created;
        STORE_MANAGER / ADMIN / SUPERADMIN may delete any in-scope combo (they
        own the store's shared templates).

    Deleting a combo cannot orphan clinical data: a combo is COPIED into an Rx
    when applied and nothing stores a combo_id reference, so no prescription or
    lens spec points back at it.
    """
    col = _get_lens_power_combos_col()
    if col is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        doc = col.find_one({"combo_id": combo_id}, {"_id": 0})
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not delete combo") from exc

    if not doc:
        raise HTTPException(status_code=404, detail="Combo not found")

    # Per-object store scope first -- 404 hides existence from other stores.
    # can_access_store_scoped, NOT _store_scope_or_404: the latter fails OPEN on
    # a doc with no store_id (deliberately, so pre-store-stamp legacy EYE TESTS
    # stay servable). lens_power_combos is a brand-new collection with no legacy
    # rows to protect, so an unattributed combo must be treated as OUT of scope
    # for a store-level caller -- otherwise a store manager silently bins another
    # store's shared template. Cross-store roles (SUPERADMIN/ADMIN) still pass.
    if not can_access_store_scoped(doc.get("store_id"), current_user):
        raise HTTPException(status_code=404, detail="Combo not found")

    roles = set(current_user.get("roles") or [])
    is_manager = bool(roles & {"STORE_MANAGER", "ADMIN", "SUPERADMIN"})
    # BOTH sides must be truthy AND equal. A bare `!=` compares None to None as
    # a MATCH: a combo with no created_by, deleted by a caller whose token has no
    # user_id claim (auth.py's refresh path builds it from .get(), so it can be
    # None), would satisfy the check and delete. Absent / null / empty on either
    # side is an ambiguous identity and must DENY -- an ownership test that
    # cannot identify the owner has not established ownership.
    creator = doc.get("created_by")
    caller = current_user.get("user_id")
    if not is_manager and not (creator and caller and creator == caller):
        raise HTTPException(
            status_code=403,
            detail="Only the creator or a manager may delete this combo",
        )

    try:
        result = col.delete_one({"combo_id": combo_id})
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not delete combo") from exc

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Combo not found")
    return {"deleted": combo_id}
