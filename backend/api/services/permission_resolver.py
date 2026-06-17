"""
IMS 2.0 - Per-user permission resolver (frozen precedence chain)
================================================================

The single function the middleware hook calls AFTER the role decision to apply
the per-user capability layer. Implements the FROZEN precedence chain from the
council ruling (sec.2), in order:

    0. Inviolable invariants  -- jarvis/SUPERADMIN-only ungrantable (a GRANT of
       one is never honoured here; sanitize already strips it, this is belt &
       braces). INVESTOR read-only + audit immutability live in their own
       middlewares/repos and are untouched.
    1. Role default           -- rbac_policy.check_access (the input ``role_allowed``).
    2. module_access DENY      -- legacy deny-only, mapped to capability denies
       at READ time (the shim; no data migration).
    3. Capability DENY (new).
    4. Capability GRANT (new)  -- capped at actor level at WRITE time, not here.
    5. Business floors          -- DATA layer (handlers); NEVER an RBAC input.

DARK-BY-DEFAULT (the non-negotiable safety property):
    A user with NO ``permissions`` field AND no ``module_access`` deny that maps
    to this capability behaves EXACTLY as today -- the resolver returns the role
    decision unchanged. The override only ever fires when an explicit deny/grant
    names the route's capability. ``apply_user_permissions`` short-circuits to
    ``role_allowed`` whenever the request carries no override signal.

The resolver is PURE: it takes the already-computed role decision + the matched
capability + the user's stored override fields, and returns the final allow
bool. It performs NO IO. The middleware does the per-request user lookup (cached
on request.state) and feeds the stored fields in.
"""

from __future__ import annotations

from typing import Optional

from .capabilities import (
    is_ungrantable,
    module_deny_to_capability_denies,
)


def _deny_set(permissions) -> set:
    if not isinstance(permissions, dict):
        return set()
    deny = permissions.get("deny")
    return {k for k, v in deny.items() if v is True} if isinstance(deny, dict) else set()


def _grant_set(permissions) -> set:
    if not isinstance(permissions, dict):
        return set()
    grant = permissions.get("grant")
    return {k for k, v in grant.items() if v is True} if isinstance(grant, dict) else set()


def apply_user_permissions(
    role_allowed: bool,
    capability: Optional[str],
    permissions=None,
    module_access=None,
) -> bool:
    """Resolve the FINAL allow decision for one request via the frozen chain.

    Args:
      role_allowed: the role-layer decision (rbac_policy.check_access result).
      capability:   the route's single capability key, or None when the route
                    has none (PUBLIC / un-catalogued) -- then the override layer
                    cannot apply and we return role_allowed unchanged.
      permissions:  the user's ``permissions`` override ({"grant":{}, "deny":{}})
                    or None.
      module_access: the user's legacy deny-only module map or None.

    Returns the final bool. DARK: with no override signal for this capability,
    returns role_allowed unchanged (identical to today).
    """
    # No capability for this route (PUBLIC / un-catalogued): the per-user layer
    # has nothing to bind to. Behave exactly as the role layer decided.
    if capability is None:
        return role_allowed

    cap_denies = _deny_set(permissions)
    cap_grants = _grant_set(permissions)
    module_denies = module_deny_to_capability_denies(module_access)

    # --- Step 2 + 3: ANY deny wins (module-shim deny OR explicit capability
    # deny). Deny ALWAYS beats grant and beats the role default. ---
    if capability in module_denies or capability in cap_denies:
        return False

    # --- Step 0 (belt & braces) + Step 4: a GRANT adds a role-denied capability,
    # but an ungrantable capability is NEVER granted here even if one slipped
    # into the stored map. The grant only MATTERS when the role denied it
    # (role_allowed already True -> nothing to add). ---
    if not role_allowed and capability in cap_grants and not is_ungrantable(capability):
        return True

    # --- Step 1: no override fired -> the role decision stands (DARK default). ---
    return role_allowed
