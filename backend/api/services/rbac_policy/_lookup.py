"""
POLICY assembly + the request-time lookup / coverage helpers.

Everything below the POLICY literal in the flat ``api/services/rbac_policy.py``
(lines 7669-7840) moved here VERBATIM. The only thing that changed is the POLICY
literal itself: the 1,303 rows now arrive from the ``rows_*`` modules, spliced
in the ORIGINAL FILE ORDER. Order matters twice over -- ``policy_for`` scans the
per-method index built from POLICY, and the capability grant-union in
``services/capabilities.py`` walks POLICY -- so the splice order below IS the
file order and must stay that way.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ._core import AUTHENTICATED, PUBLIC
from .rows_approvals_admin import ROWS as _ROWS_APPROVALS_ADMIN
from .rows_analytics_auth_catalog import ROWS as _ROWS_ANALYTICS_AUTH_CATALOG
from .rows_clinical_crm import ROWS as _ROWS_CLINICAL_CRM
from .rows_customers_estimates import ROWS as _ROWS_CUSTOMERS_ESTIMATES
from .rows_expenses_finance import ROWS as _ROWS_EXPENSES_FINANCE
from .rows_store_features import ROWS as _ROWS_STORE_FEATURES
from .rows_ops_features import ROWS as _ROWS_OPS_FEATURES
from .rows_hr import ROWS as _ROWS_HR
from .rows_incentive_inventory import ROWS as _ROWS_INCENTIVE_INVENTORY
from .rows_items_jarvis import ROWS as _ROWS_ITEMS_JARVIS
from .rows_lens_loyalty import ROWS as _ROWS_LENS_LOYALTY
from .rows_marketing import ROWS as _ROWS_MARKETING
from .rows_online_store import ROWS as _ROWS_ONLINE_STORE
from .rows_orders_payroll import ROWS as _ROWS_ORDERS_PAYROLL
from .rows_prescriptions_products import ROWS as _ROWS_PRESCRIPTIONS_PRODUCTS
from .rows_reports_settings import ROWS as _ROWS_REPORTS_SETTINGS
from .rows_stores_tasks_users import ROWS as _ROWS_STORES_TASKS_USERS
from .rows_vendors import ROWS as _ROWS_VENDORS
from .rows_workshop_misc import ROWS as _ROWS_WORKSHOP_MISC

# ---------------------------------------------------------------------------
# POLICY - one row per (method, path). Mirrors CURRENT enforcement exactly.
# Generated from api.main.app.routes; see the package docstring for methodology.
# ORDER IS THE ORIGINAL FILE ORDER. Do not sort, dedupe or re-group.
# ---------------------------------------------------------------------------
POLICY: List[Dict[str, object]] = [
    *_ROWS_APPROVALS_ADMIN,
    *_ROWS_ANALYTICS_AUTH_CATALOG,
    *_ROWS_CLINICAL_CRM,
    *_ROWS_CUSTOMERS_ESTIMATES,
    *_ROWS_EXPENSES_FINANCE,
    *_ROWS_STORE_FEATURES,
    *_ROWS_OPS_FEATURES,
    *_ROWS_HR,
    *_ROWS_INCENTIVE_INVENTORY,
    *_ROWS_ITEMS_JARVIS,
    *_ROWS_LENS_LOYALTY,
    *_ROWS_MARKETING,
    *_ROWS_ONLINE_STORE,
    *_ROWS_ORDERS_PAYROLL,
    *_ROWS_PRESCRIPTIONS_PRODUCTS,
    *_ROWS_REPORTS_SETTINGS,
    *_ROWS_STORES_TASKS_USERS,
    *_ROWS_VENDORS,
    *_ROWS_WORKSHOP_MISC,
]


# ---------------------------------------------------------------------------
# self_enforced - rows whose route DELIBERATELY denies with a non-generic
# response the enforcer must NOT override.
# ---------------------------------------------------------------------------
# Most role-gated routes reject a wrong role with a plain 403, which the
# request-time enforcer can mirror byte-for-behaviour. A few routes reject
# differently and that difference is INTENTIONAL + relied upon:
#
#   * /api/v1/jarvis/** and /api/v1/admin/techcherry/** reject non-SUPERADMIN
#     with a 404 ("Not found") to HIDE the endpoint's existence (a deliberate
#     security feature; their tests assert 404, not 403). A generic 403 here
#     would both break those tests AND leak that the path is a real route.
#   * POST /api/v1/prescriptions[/] rejects non-clinical roles with a 403 whose
#     BODY ("...does not have clinical access") is asserted by callers/tests.
#
# For these, the enforcer does the role check but, on DENY, DEFERS to the route
# (lets the request through) so the route's own gate returns its canonical
# response. ``allowed`` is unchanged (the role-class is still correct + the
# coverage-lock / jarvis-superadmin tests still pass); only the *rejection
# delivery* is left to the route. ``self_enforced`` is auto-applied by prefix
# below for the 404-hiding families; prescription rows carry it inline.
for _entry in POLICY:
    _p = str(_entry["path"])
    if (
        _p == "/api/v1/jarvis"
        or _p.startswith("/api/v1/jarvis/")
        or _p.startswith("/api/v1/admin/techcherry/")
    ):
        _entry.setdefault("self_enforced", True)


# ---------------------------------------------------------------------------
# Lookup + matching
# ---------------------------------------------------------------------------
# Build an index once at import. Paths can contain {param} segments; we match a
# concrete request path against templated policy paths segment-by-segment and
# prefer the MOST SPECIFIC (fewest params, longest) match.

_INDEX: Dict[str, List[Dict[str, object]]] = {}
for _entry in POLICY:
    _INDEX.setdefault(str(_entry["method"]).upper(), []).append(_entry)


def _segments(path: str) -> List[str]:
    return [s for s in path.split("/") if s != ""]


def _template_matches(template: str, concrete: str) -> bool:
    """True if a concrete request path matches a (possibly templated) policy
    path. A ``{param}`` segment matches exactly one non-empty concrete segment."""
    t_segs = _segments(template)
    c_segs = _segments(concrete)
    if len(t_segs) != len(c_segs):
        return False
    for t, c in zip(t_segs, c_segs):
        if t.startswith("{") and t.endswith("}"):
            continue  # param - matches any single segment
        if t != c:
            return False
    return True


def _specificity(template: str) -> tuple:
    """Higher = more specific. Rank by (fewest params, most segments)."""
    segs = _segments(template)
    params = sum(1 for s in segs if s.startswith("{") and s.endswith("}"))
    return (-params, len(segs))


def policy_for(method: str, path: str) -> Optional[Dict[str, object]]:
    """Return the POLICY entry for a concrete (method, path), or None if the
    route is not catalogued. On multiple template matches, the most specific
    (fewest path params, then longest) wins -- so a literal
    ``/orders/summary`` beats ``/orders/{order_id}``."""
    candidates = _INDEX.get(method.upper(), [])
    # Exact (literal) match first - cheapest and unambiguous.
    for entry in candidates:
        if entry["path"] == path:
            return entry
    # Then templated matches, most specific wins.
    matches = [e for e in candidates if _template_matches(str(e["path"]), path)]
    if not matches:
        return None
    matches.sort(key=lambda e: _specificity(str(e["path"])), reverse=True)
    return matches[0]


def is_store_scoped(method: str, path: str) -> bool:
    """Whether the matched endpoint additionally restricts results to the
    caller's store(s) via validate_store_access."""
    entry = policy_for(method, path)
    return bool(entry and entry.get("store_scoped"))


def is_self_enforced(method: str, path: str) -> bool:
    """Whether the matched endpoint rejects with a non-generic response the
    request-time enforcer must NOT override (404 existence-hiding, or a
    body-specific 403). On a role denial the enforcer DEFERS to the route for
    these so its canonical response is preserved. See the ``self_enforced``
    section above for the rationale + which families carry it."""
    entry = policy_for(method, path)
    return bool(entry and entry.get("self_enforced"))


def check_access(method: str, path: str, user_roles) -> bool:
    """Decision function: may a caller holding ``user_roles`` reach (method, path)?

    Rules (mirror the routers):
      * Unknown route            -> False (deny by default; nothing un-catalogued
                                   should silently pass).
      * allowed == PUBLIC        -> True (no auth needed).
      * allowed == AUTHENTICATED -> True iff the caller has ANY role (i.e. is a
                                   logged-in user). An empty role set is treated
                                   as unauthenticated -> False.
      * allowed is a role list   -> True iff SUPERADMIN in roles OR the caller's
                                   roles intersect the allow-list.

    Does NOT evaluate store-scope / ownership / discount-cap conditions -- those
    are data-level checks the handler still performs. This answers the
    role-class question only.
    """
    entry = policy_for(method, path)
    if entry is None:
        return False
    allowed = entry["allowed"]
    roles = set(user_roles or [])
    if allowed == PUBLIC:
        return True
    if allowed == AUTHENTICATED:
        return len(roles) > 0
    # role list
    if "SUPERADMIN" in roles:
        return True
    return bool(roles & set(allowed))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Coverage helper
# ---------------------------------------------------------------------------


def uncatalogued_routes(app) -> List[Dict[str, str]]:
    """Return live ``/api/v1`` routes (method, path) that have NO POLICY entry.

    Excludes docs / openapi / static and non-/api/v1 utility routes (``/``,
    ``/health``, ``/docs`` …). HEAD is ignored (auto-paired with GET). This is
    the regression lock used by the coverage test: any new endpoint added
    without a POLICY row shows up here.
    """
    missing: List[Dict[str, str]] = []
    seen = set()
    for route in getattr(app, "routes", []):
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        if not path.startswith("/api/v1"):
            continue
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            key = (method, path)
            if key in seen:
                continue
            seen.add(key)
            if policy_for(method, path) is None:
                missing.append({"method": method, "path": path})
    missing.sort(key=lambda r: (r["path"], r["method"]))
    return missing
