"""SOAP note read / save (CLI-11).

Moved verbatim out of the 3,158-line api/routers/clinical.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import HTTPException, Depends
from datetime import datetime
from ..auth import require_roles
from ..prescriptions import require_rx_read
from ...dependencies import get_eye_test_repository
from ._shared import _CLINICAL_ROLES, _audit_clinical, _store_scope_or_404, router
from .models import SoapNote
from .helpers import _convert_to_camel


# ============================================================================
# SOAP NOTE ENDPOINTS (CLI-11)
# ============================================================================


@router.get("/tests/{test_id}/soap-note")
async def get_soap_note(
    test_id: str,
    current_user: dict = Depends(require_rx_read),
):
    """Return the structured SOAP exam note for a completed eye test.

    CLI-11: the SOAP note (Subjective / Objective / Assessment / Plan + Dx
    codes) is stored under the ``soap_note`` key on the eye_test document.
    Returns the note as-is (snake_case) wrapped in ``{soapNote: {...}}``.
    Returns 404 when the test does not exist; returns ``{soapNote: null}`` when
    the test exists but no SOAP note has been saved yet (a refraction-only
    test).

    A SOAP note is the most sensitive clinical artifact we store (exam
    narrative + Dx codes), so the read is role-gated to the prescription-read
    set (require_rx_read) and store-scoped per object (404-hide cross-store) --
    it was previously readable by ANY authenticated role in ANY store (P1).
    """
    test_repo = get_eye_test_repository()
    if test_repo is None:
        return {"soapNote": None}

    test = test_repo.find_by_id(test_id)
    if test is None:
        raise HTTPException(status_code=404, detail="Test not found")
    _store_scope_or_404(test, current_user)

    raw = test.get("soap_note")
    if not raw:
        return {"soapNote": None}

    # Convert to camelCase for the frontend before returning.
    return {"soapNote": _convert_to_camel(raw)}


@router.post("/tests/{test_id}/soap-note")
async def save_soap_note(
    test_id: str,
    note: SoapNote,
    current_user: dict = Depends(require_roles(*_CLINICAL_ROLES)),
):
    """Save (or replace) the SOAP exam note on an existing eye test.

    CLI-11: allows post-completion charting without re-opening the test
    completion flow.  The whole note is replaced atomically; send the full
    document including unchanged fields.  Stamps ``recorded_by`` /
    ``recorded_at`` automatically from the current user + now when not already
    present in the payload.

    Gated to the same roles that can complete a test
    (OPTOMETRIST / STORE_MANAGER / ADMIN / SUPERADMIN).
    """
    test_repo = get_eye_test_repository()
    if test_repo is None:
        raise HTTPException(
            status_code=503, detail="Clinical service unavailable"
        )

    test = test_repo.find_by_id(test_id)
    if test is None:
        raise HTTPException(status_code=404, detail="Test not found")
    # Cross-store IDOR guard: a store-scoped clinician may only chart on their
    # OWN store's tests (404-hide otherwise).
    _store_scope_or_404(test, current_user)

    now_iso = datetime.utcnow().isoformat()
    note_dict = note.model_dump(exclude_none=True)
    note_dict.setdefault("recorded_by", current_user.get("user_id", ""))
    note_dict.setdefault("recorded_at", now_iso)

    ok = test_repo.save_soap_note(test_id, note_dict)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to save SOAP note")

    _audit_clinical(
        "SOAP_NOTE_SAVED",
        test_id,
        current_user,
        store_id=test.get("store_id"),
        detail={
            "customer_id": test.get("customer_id"),
            "dx_codes": [d.get("code") for d in note_dict.get("dx_codes") or []],
        },
    )

    return {
        "message": "SOAP note saved",
        "testId": test_id,
        "soapNote": _convert_to_camel(note_dict),
    }
