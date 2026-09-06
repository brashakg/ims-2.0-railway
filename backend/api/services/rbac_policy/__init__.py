"""
IMS 2.0 - Central RBAC Policy Registry (request-time enforced)
==============================================================

A single declarative table of every API endpoint and the role set that may
reach it, derived from the CURRENT enforcement in the routers (hardening dim 4).

WHY THIS EXISTS
---------------
Access control today is spread across four mechanisms:

  1. Per-endpoint dependencies  - ``Depends(require_roles(...))``,
     ``require_admin`` / ``require_manager`` / ``require_superadmin``.
  2. Router-level dependencies  - ``APIRouter(dependencies=[Depends(_require_admin_role)])``
     on the admin / admin_catalog / admin_extras routers, and
     ``include_router(..., dependencies=[Depends(require_roles(*_FINANCE_ROLES))])``
     on finance / hr / payroll in ``api/main.py``.
  3. Inline checks in handler bodies - e.g. POS_WRITE_ROLES on ``POST /orders``,
     ``_require_superadmin(current_user)`` on ``GET /audit/verify``.
  4. Implicit (no role check) - ``Depends(get_current_user)`` only.

That scattering makes it hard to answer "who can call X?" and easy to ship a
new endpoint with the wrong gate. This module collapses all four into one
enforcer. ``check_access`` is now consumed at request time by the middleware in
``api/middleware/rbac_enforcement.py`` as a SECOND, defense-in-depth layer that
sits ON TOP of the per-route gates (which remain in place). The registry mirrors
the route gates EXACTLY, so the enforcer is behavior-preserving: no endpoint's
effective access changes. The middleware fails OPEN on un-catalogued routes and
PASSES THROUGH on missing/invalid tokens (so the route's own ``get_current_user``
returns the canonical 401); only an authenticated caller who genuinely lacks the
role is 403'd one layer earlier. The coverage-lock test
(``tests/test_rbac_policy.py``) guarantees the table stays complete.

HOW THE TABLE WAS BUILT
-----------------------
Each route in ``api.main.app.routes`` was introspected for its dependency tree
(catching mechanisms 1 and 2 above) and, where only ``get_current_user`` was a
dependency, its handler source was read to capture inline role gates
(mechanism 3). The finance/hr/payroll router-level gate does NOT flatten into a
route's ``dependant`` in this FastAPI version, so it is applied by prefix.

POLICY ENTRY SHAPE
------------------
    {"method": "POST", "path": "/api/v1/orders",
     "allowed": [<roles>] | "AUTHENTICATED" | "PUBLIC",
     "store_scoped": bool}      # store_scoped omitted when False

  - A role list  : caller must hold at least one of these roles. SUPERADMIN is
                   listed explicitly wherever it passes (it always does via
                   ``require_roles``), so the table is self-contained.
  - "AUTHENTICATED": any logged-in user (valid JWT); no role differentiation.
  - "PUBLIC"     : reachable with no IMS auth at all. Each PUBLIC route is
                   protected by its OWN mechanism (HMAC webhook signature,
                   tokenized/OTP customer-portal link, vendor-portal path token,
                   ``SEED_SECRET`` hmac, login credentials) or is a static
                   module-info stub with no data access.

ROLE MODEL (12 canonical roles)
-------------------------------
SUPERADMIN, ADMIN, AREA_MANAGER, STORE_MANAGER, ACCOUNTANT, CATALOG_MANAGER,
OPTOMETRIST, SALES_CASHIER, SALES_STAFF, CASHIER, WORKSHOP_STAFF, plus the
read-only INVESTOR role.

CAVEATS (the request-time enforcer relies on these holding true)
----------------------------------------------------------------
  * INVESTOR write-block is a MIDDLEWARE in ``api/main.py``
    (``block_investor_writes``): an INVESTOR-only user is 403'd on every
    non-safe method app-wide, regardless of this table. INVESTOR is therefore
    NOT added to any ``allowed`` list; treat it as read-only everywhere.
  * Some "AUTHENTICATED" rows still 403 on a *data-conditional* basis the table
    cannot express: store-scope (``validate_store_access``), resource ownership
    (handoff recipient/uploader), or a discount-cap breach. Those are flagged
    ``store_scoped`` where applicable; ownership/cap conditions are documented in
    ``docs/reference/RBAC_MATRIX.md`` REVIEW section, not encoded here.
  * ``store_scoped`` means the handler additionally restricts the row to the
    caller's store(s) (HQ roles bypass). It is orthogonal to ``allowed``.

This file is GENERATED-then-curated; if routes change, re-derive it and update
``docs/reference/RBAC_MATRIX.md``. The companion test
``backend/tests/test_rbac_policy.py`` fails if any live ``/api/v1`` route is
missing here (the regression lock).

PACKAGE LAYOUT (Wave 5 pure-move split; zero behaviour change)
--------------------------------------------------------------
``_core``   - ALL_ROLES + the PUBLIC / AUTHENTICATED sentinels + ``Allowed``.
``rows_*``  - the 1,303 POLICY rows, moved verbatim in contiguous ranges.
``_lookup`` - POLICY assembly (rows spliced in ORIGINAL ORDER), the
              ``self_enforced`` pass, the method index and every lookup /
              coverage function.

Every name the flat module exposed is re-exported below, so
``from api.services import rbac_policy`` and ``rbac_policy.<anything>`` keep
working unchanged for the middleware, ``services/capabilities.py`` and the 60+
test modules that import this.
"""

from __future__ import annotations  # noqa: F401  # re-exported (was on the flat module)

import sys
from types import ModuleType
from typing import Dict, List, Optional, Union  # noqa: F401  # re-exported

from ._core import (  # noqa: F401  # re-exported: see _RbacPolicyModule
    ALL_ROLES,
    PUBLIC,
    AUTHENTICATED,
    Allowed,
)
from ._lookup import (  # noqa: F401  # re-exported: see _RbacPolicyModule
    POLICY,
    _INDEX,
    _entry,
    _p,
    _segments,
    _template_matches,
    _specificity,
    policy_for,
    is_store_scoped,
    is_self_enforced,
    check_access,
    uncatalogued_routes,
)

_SUBMODULE_NAMES = (
    "_core",
    "rows_approvals_admin",
    "rows_analytics_auth_catalog",
    "rows_clinical_crm",
    "rows_customers_estimates",
    "rows_expenses_finance",
    "rows_store_features",
    "rows_ops_features",
    "rows_hr",
    "rows_incentive_inventory",
    "rows_items_jarvis",
    "rows_lens_loyalty",
    "rows_marketing",
    "rows_online_store",
    "rows_orders_payroll",
    "rows_prescriptions_products",
    "rows_reports_settings",
    "rows_stores_tasks_users",
    "rows_vendors",
    "rows_workshop_misc",
    "_lookup",
)


class _RbacPolicyModule(ModuleType):
    """Fan a ``setattr`` on this package out to the sub-modules that bind it.

    ``tests/test_rbac_enforcement.py`` patches by package path
    (``monkeypatch.setattr(rbac_policy, "policy_for", ...)``). On the flat module
    that set the module global, so the OTHER functions in the same file
    (``is_store_scoped``, ``is_self_enforced``, ``check_access``) saw the fake
    too. Forwarding the write into ``_lookup`` preserves exactly that.
    """

    def __setattr__(self, name, value):
        ModuleType.__setattr__(self, name, value)
        for mod in _submodules():
            if name in vars(mod):
                ModuleType.__setattr__(mod, name, value)


def _submodules():
    prefix = __name__ + "."
    return [
        sys.modules[prefix + n] for n in _SUBMODULE_NAMES if prefix + n in sys.modules
    ]


sys.modules[__name__].__class__ = _RbacPolicyModule
