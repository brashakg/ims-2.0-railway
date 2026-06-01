"""
IMS 2.0 - User role validation + privilege-escalation guards
============================================================
Single source of truth for the USER-MANAGEMENT side of RBAC:

  * VALID_ROLES        - the canonical assignable role set (mirrors
                         rbac_policy.ALL_ROLES + the read-only INVESTOR role,
                         WITHOUT redefining that owned table).
  * ROLE_LEVEL         - numeric privilege ladder (higher == more power),
                         mirrors frontend ROLE_HIERARCHY in
                         frontend/src/pages/settings/settingsTypes.ts.
  * can_assign_roles   - a privilege-escalation guard: an actor may only
                         create/promote a target to roles at or BELOW the
                         actor's own highest level. This is the SERVER-SIDE
                         enforcement that previously lived only in the React UI
                         (ASSIGNABLE_ROLES) and was therefore trivially
                         bypassable by hitting the API directly.
  * sanitize_module_access - the per-user module override is DENY-ONLY. The
                         role is the ceiling. This drops any True/grant entries
                         and unknown keys so a malicious payload can never
                         ESCALATE a user above what their role allows.

NOTE: This intentionally does NOT import from auth.py / rbac_policy.py to keep
the user-management module self-contained; the role set is asserted against
rbac_policy.ALL_ROLES by a regression test instead.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

# Canonical assignable roles. The 11 operational roles + the read-only INVESTOR
# role. SUPERADMIN is the top of the ladder. Keep in sync with
# rbac_policy.ALL_ROLES (asserted by test_user_role_guards.py).
VALID_ROLES = frozenset(
    {
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
        # DESIGN_MANAGER: lowest-privilege ecom design-queue role (BVI Phase 1).
        # Mirrors rbac_policy.ALL_ROLES (asserted in sync by test_user_role_guards).
        "DESIGN_MANAGER",
        "INVESTOR",
    }
)

# Numeric privilege ladder. Higher == more powerful. Mirrors the frontend
# ROLE_HIERARCHY; an actor can only assign roles at or below their own level.
ROLE_LEVEL: Dict[str, int] = {
    "SUPERADMIN": 100,
    "ADMIN": 90,
    "AREA_MANAGER": 70,
    "STORE_MANAGER": 60,
    "ACCOUNTANT": 50,
    "CATALOG_MANAGER": 50,
    "OPTOMETRIST": 40,
    "SALES_CASHIER": 30,
    "CASHIER": 25,
    "SALES_STAFF": 20,
    "WORKSHOP_STAFF": 20,
    # DESIGN_MANAGER sits at the staff tier (ecom design-queue only); ADMIN+ can
    # assign it. Additive -- does not change any existing role's level.
    "DESIGN_MANAGER": 20,
    "INVESTOR": 10,
}

# Valid per-user module-access keys (mirrors MODULE_KEYS in
# frontend/src/context/ModuleContext.tsx). Any key outside this set is dropped.
VALID_MODULE_KEYS = frozenset(
    {
        "pos",
        "clinic",
        "inventory",
        "customers",
        "vendors",
        "workshop",
        "hr",
        "reports",
        "finance",
    }
)

# bcrypt only consumes the first 72 BYTES of a password and silently ignores the
# rest. Two different long passwords that share a 72-byte prefix would both
# authenticate -- a well-known footgun. We reject anything longer so the stored
# hash unambiguously covers the whole secret.
BCRYPT_MAX_BYTES = 72


def highest_level(roles: Iterable[str]) -> int:
    """Highest privilege level among `roles`. Unknown roles count as 0.
    Empty -> 0."""
    levels = [ROLE_LEVEL.get(r, 0) for r in (roles or [])]
    return max(levels) if levels else 0


def validate_roles(roles: Iterable[str]) -> Tuple[bool, List[str]]:
    """Return (ok, invalid_roles). ok is True only when every role is in
    VALID_ROLES. Order-preserving, de-duplicated invalid list."""
    invalid: List[str] = []
    seen = set()
    for r in roles or []:
        if r not in VALID_ROLES and r not in seen:
            invalid.append(r)
            seen.add(r)
    return (not invalid, invalid)


def can_assign_roles(
    actor_roles: Iterable[str], target_roles: Iterable[str]
) -> Tuple[bool, Optional[str]]:
    """Privilege-escalation guard.

    An actor may only grant roles at or BELOW their own highest privilege level.
    SUPERADMIN may assign anything (it sits at the top). Returns (ok, reason).
    On failure `reason` names the offending role for a clean 403/400 message.

    This blocks:
      - an ADMIN promoting anyone (incl. themselves) to SUPERADMIN,
      - an AREA_MANAGER minting an ADMIN,
      - any "assign a role strictly above me" escalation.
    """
    actor_role_set = set(actor_roles or [])
    if "SUPERADMIN" in actor_role_set:
        return (True, None)
    actor_level = highest_level(actor_role_set)
    for r in target_roles or []:
        # Unknown role -> treat as un-assignable (caller should validate_roles
        # first, but be defensive here too).
        target_level = ROLE_LEVEL.get(r)
        if target_level is None:
            return (False, r)
        if target_level > actor_level:
            return (False, r)
    return (True, None)


def sanitize_module_access(
    module_access: Optional[Dict[str, bool]],
) -> Optional[Dict[str, bool]]:
    """Coerce the per-user module override to its DENY-ONLY contract.

    The override may only RESTRICT (False) a module the role would otherwise
    allow; it can never GRANT. So:
      - keys not in VALID_MODULE_KEYS are dropped (junk can't be persisted),
      - any value of True is dropped (a grant is meaningless / could be read as
        an escalation -- the role is the ceiling),
      - only explicit False entries survive.

    None in -> None out (caller decides the default, usually {}).
    """
    if module_access is None:
        return None
    cleaned: Dict[str, bool] = {}
    for key, value in module_access.items():
        if key not in VALID_MODULE_KEYS:
            continue
        # Only deny entries are meaningful; a truthy/grant value is dropped.
        if value is False:
            cleaned[key] = False
    return cleaned


def password_within_bcrypt_limit(password: str) -> bool:
    """True when the password fits bcrypt's 72-byte input window (so the stored
    hash covers the whole secret, no silent truncation)."""
    return len(password.encode("utf-8")) <= BCRYPT_MAX_BYTES
