"""Shared clinical plumbing: the router, the domain audit helper, the role
tuples, the conversion revenue gate and the per-object store-scope guards.

The import block below is the ORIGINAL clinical.py block, kept whole on
purpose: most names are no longer referenced in THIS file (each sub-module
imports what it needs directly), but they are part of the module surface
the single file exposed and __init__.py re-exports them, so
``from api.routers.clinical import get_db / require_rx_read`` and the tests
that monkeypatch them keep working unchanged.

Moved verbatim out of the 3,158-line api/routers/clinical.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import APIRouter, HTTPException, Depends, Query, Path
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import List, Optional
from datetime import datetime, date, timezone

from ...utils.ist import ist_date_str, ist_today
from html import escape as _html_escape
import uuid
from ..auth import get_current_user, require_roles

# Clinical eye-test READS reuse the SAME role gate as prescription reads
# (prescriptions.require_rx_read / _RX_READ_ROLES): an eye test carries the
# same medical data (SPH/CYL/AXIS, SOAP notes) + patient PII as the Rx it
# mints, so the read surface must be identical -- never a divergent copy.
from ..prescriptions import require_rx_read
from ...dependencies import (
    get_db,
    get_eye_test_queue_repository,
    get_eye_test_repository,
    get_order_repository,
    get_prescription_repository,
    get_customer_repository,
    get_store_repository,
    get_audit_repository,
    get_handoff_repository,
    get_user_repository,
    validate_store_access,
    can_access_store_scoped,
    user_store_scope,
)
from ...services import clinical_abuse as _abuse
from ...services import conversion_analytics as _conversion
from ...services.rx_print_values import first_present_rx_value, is_absent_rx_value
from ...services.data_horizon import horizon_start_iso_date, later_iso_bound

router = APIRouter()


def _audit_clinical(
    action: str,
    entity_id: Optional[str],
    current_user: dict,
    *,
    store_id: Optional[str] = None,
    detail: Optional[dict] = None,
) -> None:
    """Best-effort domain audit for a clinical/eye-test action -> append-only
    audit_logs (source="domain"). Eye-test completions (which auto-mint a
    prescription) were invisible in the Activity Log; this records who recorded
    which exam. FAIL-SOFT: any audit failure is swallowed so it can never undo
    or 500 the clinical write. ``timestamp`` is stamped explicitly (the Activity
    Log sorts + range-filters on it)."""
    try:
        audit_repo = get_audit_repository()
        if audit_repo is None:
            return
        audit_repo.create(
            {
                "action": action,
                "entity_type": "CLINICAL",
                "entity_id": entity_id,
                "store_id": store_id or current_user.get("active_store_id"),
                "user_id": current_user.get("user_id"),
                "user_name": current_user.get("full_name")
                or current_user.get("username"),
                "timestamp": datetime.utcnow(),
                "severity": "INFO",
                "source": "domain",
                "detail": detail or {},
            }
        )
    except Exception:  # noqa: BLE001 - audit must never break the clinical write
        pass


# Roles permitted to mutate the optometry queue + eye-test records. Mirrors the
# frontend Clinical route guard. SUPERADMIN auto-passes via require_roles.
_CLINICAL_ROLES = ("ADMIN", "STORE_MANAGER", "OPTOMETRIST")

# Owner ruling 2026-09-06 ("let sales staff book too"): the counter may put a
# customer into TODAY's eye-test queue from the POS customer panel. ONLY the
# add door widens -- start/status/remove and every exam/Rx write stay on
# _CLINICAL_ROLES. SALES_CASHIER is normalised to SALES_STAFF at decode_token;
# listed anyway so the gate reads the same as its rbac_policy row. Bare CASHIER
# (payment-only) stays out: the button lives behind the family-Rx read, which
# CASHIER already cannot make (_RX_READ_ROLES).
_QUEUE_ADD_ROLES = _CLINICAL_ROLES + ("SALES_STAFF", "SALES_CASHIER")

# Roles permitted to READ the saved lens-power combos (CLI-9). The clinical
# write roles plus AREA_MANAGER, who is a supervisory clinical READER across
# this router (cf. _ABUSE_VIEW_ROLES / _CONVERSION_VIEW_ROLES) but never a
# combo author. Kept byte-identical to the GET row in rbac_policy.py.
_COMBO_READ_ROLES = _CLINICAL_ROLES + ("AREA_MANAGER",)

# Roles permitted to record a redo on a prescription. Wider than the queue
# mutators on purpose: an Area Manager auditing a botched dispense should be
# able to flag a redo. SUPERADMIN auto-passes via require_roles.
_REDO_ROLES = ("OPTOMETRIST", "STORE_MANAGER", "AREA_MANAGER", "ADMIN")

# Roles permitted to see the clinical abuse-detection (fraud-control) view.
# Management only -- the optometrists being measured must NOT see their own
# scorecard. SUPERADMIN auto-passes via require_roles.
_ABUSE_VIEW_ROLES = ("STORE_MANAGER", "AREA_MANAGER", "ADMIN")

# F24 -- roles permitted to view the optometrist -> retail conversion dashboard.
# OPTOMETRIST sees only their OWN row and never the revenue figures (revenue is
# role-gated per DECISIONS sec 3). Managers see all optometrists + revenue.
# SUPERADMIN auto-passes via require_roles.
_CONVERSION_VIEW_ROLES = (
    "STORE_MANAGER",
    "AREA_MANAGER",
    "ADMIN",
    "OPTOMETRIST",
)
# Roles that may see the revenue column on the conversion dashboard. OPTOMETRIST
# is deliberately ABSENT -- revenue is stripped server-side for them (cost_mask
# philosophy: never send rupees the role can't see).
_CONVERSION_REVENUE_ROLES = {
    "SUPERADMIN",
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
}
# Cross-store roles: may query any store / the whole org scope.
_HQ_ROLES = {"SUPERADMIN", "ADMIN", "AREA_MANAGER"}


def _conversion_can_see_revenue(current_user: dict) -> bool:
    """True if the caller may see the conversion revenue figures. A pure
    OPTOMETRIST (no manager role) -> False -> revenue stripped server-side."""
    roles = set(current_user.get("roles") or [])
    return bool(roles & _CONVERSION_REVENUE_ROLES)

# Canonical queue lifecycle states. Mirrors EyeTestQueueRepository.update_status'
# allow-list so the router can reject an invalid status with a clean 400 BEFORE
# the repo silently no-ops (which used to surface as a misleading 200 "updated").
_VALID_QUEUE_STATUSES = ("WAITING", "IN_PROGRESS", "COMPLETED", "CANCELLED", "NO_SHOW")


def _store_scope_or_404(doc, current_user: dict, entity: str = "Test") -> None:
    """Per-OBJECT store-scope guard for clinical docs (same IDOR class as the
    prescriptions BUG-088 fix): a store-scoped caller may only touch an eye
    test / queue item stamped with one of THEIR stores. Raises 404 -- never
    403 -- so an out-of-scope caller cannot even confirm the record exists.

    A doc with NO store_id (legacy / pre-store-stamp / demo data) is
    deliberately left to the ROLE gate alone (fail-open on missing store_id):
    clinical roles must keep working on legacy records, and every contemporary
    write path stamps store_id at creation (add_to_queue -> start_test ->
    complete_test). NOTE this is intentionally looser than prescriptions'
    can_access_store_scoped (which hides unattributed docs from store-level
    roles) -- eye tests pre-date the store stamp and must stay servable.
    """
    store_id = (doc or {}).get("store_id")
    if store_id and not can_access_store_scoped(store_id, current_user):
        raise HTTPException(status_code=404, detail=f"{entity} not found")


def _filter_tests_by_store_scope(tests, current_user: dict):
    """Scope a LIST of eye-test docs to the caller's stores. Cross-store roles
    (SUPERADMIN/ADMIN) see everything; store-level roles only see docs stamped
    with one of their stores. Unattributed (no store_id) legacy docs stay
    visible to the role-gated caller -- same fail-open as _store_scope_or_404."""
    is_cross, stores = user_store_scope(current_user)
    if is_cross:
        return list(tests or [])
    return [
        t
        for t in (tests or [])
        if isinstance(t, dict) and (not t.get("store_id") or t.get("store_id") in stores)
    ]
