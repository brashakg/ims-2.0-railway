"""
Shared primitives for the RBAC policy registry.

Moved verbatim out of the flat ``api/services/rbac_policy.py`` (lines 88-112)
so the ``rows_*`` data modules can import the sentinels without importing the
package ``__init__`` -- which imports THEM, so that would be a cycle. Not one
value here changed; see the package docstring for the split map.
"""

from __future__ import annotations

from typing import List, Union

# All 11 operational roles (INVESTOR excluded - read-only via middleware, never
# an allow-list member). SUPERADMIN is a member of every gate implicitly.
ALL_ROLES: List[str] = [
    "SUPERADMIN",
    "ADMIN",
    "AREA_MANAGER",
    "STORE_MANAGER",
    "ACCOUNTANT",
    "CATALOG_MANAGER",
    "OPTOMETRIST",
    "SALES_CASHIER",
    "SALES_STAFF",
    "CASHIER",
    "WORKSHOP_STAFF",
    # DESIGN_MANAGER (lowest-privilege ecom design-queue role, BVI Phase 1).
    # Added to the matrix for the "Online Store" module; does not change any
    # existing route gate. See routers/online_store.py + BVI_MERGE_PLAN.md.
    "DESIGN_MANAGER",
]

# Sentinel allow-values.
PUBLIC = "PUBLIC"
AUTHENTICATED = "AUTHENTICATED"

Allowed = Union[List[str], str]
