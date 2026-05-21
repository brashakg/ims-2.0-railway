"""
IMS 2.0 — Role-based discount cap helper
==========================================
Single source of truth for the role→max-discount-percent table from
CLAUDE.md §"Non-negotiable business rules":

    SUPERADMIN, ADMIN              → 100% (unlimited)
    AREA_MANAGER                   → 25%
    STORE_MANAGER, INVENTORY_MGR   → 20%
    OPTOMETRIST, ACCOUNTANT,
    CATALOG_MGR, CASHIER,
    WORKSHOP_STAFF                 → no discount privilege (0%)
    SALES_CASHIER, SALES_STAFF     → 10%

The `effective_discount_cap` function takes a user's roles + their
per-user override (the `discount_cap` field on the user document, set
by admin) and returns the cap that should actually be enforced.

Rule: max(per-user override, max role cap). This way:
  - SUPERADMIN gets 100 no matter what's stored on their user doc.
  - A manager can be GIVEN a higher cap than their role baseline via
    the user override, but never lower than their role baseline.
"""

from __future__ import annotations

from typing import Iterable, Optional

ROLE_DISCOUNT_CAPS: dict[str, float] = {
    "SUPERADMIN": 100.0,
    "ADMIN": 100.0,
    "AREA_MANAGER": 25.0,
    "STORE_MANAGER": 20.0,
    "INVENTORY_MANAGER": 20.0,
    "ACCOUNTANT": 0.0,
    "CATALOG_MANAGER": 0.0,
    "OPTOMETRIST": 0.0,
    "SALES_CASHIER": 10.0,
    "SALES_STAFF": 10.0,
    "CASHIER": 0.0,
    "WORKSHOP_STAFF": 0.0,
}


def role_baseline_cap(roles: Iterable[str]) -> float:
    """Returns the HIGHEST cap among the user's roles. A user with both
    SALES_STAFF (10) and STORE_MANAGER (20) gets 20."""
    if not roles:
        return 0.0
    return max((ROLE_DISCOUNT_CAPS.get(r, 0.0) for r in roles), default=0.0)


def effective_discount_cap(
    roles: Iterable[str],
    user_override: Optional[float] = None,
) -> float:
    """The cap that should actually be enforced for a given user.

    - SUPERADMIN/ADMIN always get 100, regardless of any user override.
    - Otherwise the cap is max(role baseline, user override). The user
      override lets admins grant a manager a higher cap than their role
      default, but you can never give someone LESS than their role
      baseline (e.g. you can't demote a STORE_MANAGER to 5% without
      changing their role).

    Returns a float in [0, 100].
    """
    roles_list = list(roles or [])
    if "SUPERADMIN" in roles_list or "ADMIN" in roles_list:
        return 100.0
    baseline = role_baseline_cap(roles_list)
    if user_override is None:
        return baseline
    return max(baseline, min(100.0, max(0.0, float(user_override))))
