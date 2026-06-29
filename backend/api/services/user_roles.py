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

# Canonical recognized roles. The 11 operational roles + the read-only INVESTOR
# role. SUPERADMIN is the top of the ladder. Keep in sync with
# rbac_policy.ALL_ROLES (asserted by test_user_role_guards.py).
#
# NOTE on SALES_CASHIER: it remains RECOGNIZED here (so an existing user/JWT that
# still carries it is NOT rejected at validation time) but it is DEPRECATED and
# merged into SALES_STAFF -- see DEPRECATED_ROLE_ALIASES + normalize_roles()
# below. It is NOT in ASSIGNABLE_ROLES, so no NEW user can be given it; any write
# that passes it is silently rewritten to the survivor SALES_STAFF.
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

# Deprecated role -> survivor mapping (owner decision, backlog item #12). Sales
# Cashier and Sales Staff were functionally identical (same 10% cap, same POS /
# module gating, same geo-fence), so Sales Cashier is merged into Sales Staff.
# These aliases are still RECOGNIZED (existing tokens/users keep working) but are
# transparently normalized to the survivor everywhere roles are read or written.
DEPRECATED_ROLE_ALIASES: Dict[str, str] = {
    "SALES_CASHIER": "SALES_STAFF",
}

# The roles an admin may actually ASSIGN to a user. This is VALID_ROLES minus the
# read-only INVESTOR sentinel and minus every deprecated alias -- so a brand-new
# user can only ever receive the survivor (SALES_STAFF), never SALES_CASHIER.
ASSIGNABLE_ROLES = frozenset(
    r
    for r in VALID_ROLES
    if r not in DEPRECATED_ROLE_ALIASES
)


def normalize_role(role: str) -> str:
    """Map a single deprecated role alias to its survivor; pass through unknown /
    current roles unchanged. SALES_CASHIER -> SALES_STAFF."""
    return DEPRECATED_ROLE_ALIASES.get(role, role)


def normalize_roles(roles: Iterable[str]) -> List[str]:
    """Normalize a roles list: rewrite every deprecated alias to its survivor and
    de-duplicate while preserving order. None/empty -> []. This is the single
    helper used at the auth/read layer (decode_token) and on the user-write paths
    so SALES_CASHIER is treated as SALES_STAFF system-wide without locking out a
    user/token that still carries the old role."""
    out: List[str] = []
    seen = set()
    for r in roles or []:
        nr = normalize_role(r)
        if nr not in seen:
            out.append(nr)
            seen.add(nr)
    return out

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
    # SALES_CASHIER is deprecated (merged into SALES_STAFF, backlog #12). Roles are
    # normalized to the survivor BEFORE every level lookup, so this entry is never
    # reached in normal flow -- but it is pinned to SALES_STAFF's level (20), NOT
    # its historical 30, so any future DIRECT (un-normalized) ROLE_LEVEL lookup
    # can't misclassify the alias as more privileged than its survivor.
    "SALES_CASHIER": 20,
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


# Readable temp-password alphabet: A-Z/a-z/2-9 with the visually-ambiguous
# characters removed (no 0/O, 1/l/I) so an admin can dictate the temp over the
# phone / copy it without transcription errors. Mirrors the FE randomTempPassword
# alphabet in SetupPage.tsx so server- and (legacy) client-generated temps look
# the same. ASCII-only -> exactly 1 byte/char, so a 12-char temp is 12 bytes,
# well within BCRYPT_MAX_BYTES.
_TEMP_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"


def generate_temp_password(length: int = 12) -> str:
    """Generate a STRONG, human-readable temporary password.

    Uses ``secrets.choice`` (the cryptographically-secure RNG) over the
    unambiguous alphabet above. The default length of 12 over a 55-symbol
    alphabet is ~69 bits of entropy -- far beyond what a forced-change,
    single-use temp needs. The caller bcrypt-hashes the result and returns it to
    the admin exactly ONCE; the plaintext is never stored or logged.

    ``length`` is floored at 8 (the create/reset password floor) and the byte
    length always equals the char length (ASCII alphabet), so the result is
    always within bcrypt's 72-byte window.
    """
    import secrets

    n = max(8, int(length))
    return "".join(secrets.choice(_TEMP_PASSWORD_ALPHABET) for _ in range(n))


# ---------------------------------------------------------------------------
# Per-user CAPABILITY permissions (council ruling sec.2) -- grant/deny shape
# ---------------------------------------------------------------------------
# The per-user ``permissions`` field is a TWO-SIDED override layered on top of
# the role (unlike the deny-only module_access). Shape:
#
#     {"grant": {"<cap>": true, ...}, "deny": {"<cap>": true, ...}}
#
# A DENY removes a role-granted capability; a GRANT adds a role-denied one (the
# GRANT's real reach is still capped at WRITE time by the actor's level + by the
# inviolable business floors at the data layer). ``sanitize_permissions`` is the
# write-path coercion mirroring ``sanitize_module_access``:
#   * keep only KNOWN capability keys (junk dropped),
#   * DROP ungrantable keys from the GRANT side for a non-SUPERADMIN actor
#     (jarvis:* / SUPERADMIN-only) -- they can never be granted by a lesser
#     admin; a DENY of one is harmless and kept (deny is always safe),
#   * a key present on BOTH sides resolves to DENY (deny beats grant -- the
#     frozen precedence) and is removed from grant,
#   * empty sides are omitted so the stored shape is minimal.
#
# This does NOT decide WHO may grant WHAT (that is the escalation guard at the
# router, which needs the actor's roles + the prior state). sanitize only
# guarantees the stored shape is well-formed and free of impossible grants.


def sanitize_permissions(
    permissions, actor_is_superadmin: bool = False
):
    """Coerce a per-user ``permissions`` override to its well-formed shape.

    ``permissions`` is the raw ``{"grant": {...}, "deny": {...}}`` map (either
    side optional). Returns a cleaned dict with only known capability keys,
    ALL ungrantable keys stripped from GRANT (jarvis/SUPERADMIN-only can never be
    granted via the per-user layer -- not even by a SUPERADMIN actor; those
    routes are SUPERADMIN-by-role already, and this layer only ever GRANTS to a
    LESSER user), and deny-beats-grant applied. None in -> None out (the caller
    defaults to {}). Pure; never raises. ``actor_is_superadmin`` is accepted for
    call-site symmetry but does NOT relax the ungrantable rule (it is inviolable).
    """
    if permissions is None:
        return None
    # Lazy import keeps user_roles import-light + avoids a cycle at module load.
    from .capabilities import VALID_CAPABILITY_KEYS, is_ungrantable

    if not isinstance(permissions, dict):
        return {}

    def _clean_side(side, drop_ungrantable: bool):
        raw = permissions.get(side)
        out = {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                if value is not True:
                    # Only an explicit True asserts the grant/deny; anything else
                    # (False/None/junk) is dropped -- the absence IS the default.
                    continue
                if key not in VALID_CAPABILITY_KEYS:
                    continue
                if drop_ungrantable and is_ungrantable(key):
                    # An ungrantable (jarvis:* / SUPERADMIN-only) capability can
                    # never be GRANTED via this layer -- inviolable invariant.
                    # (DENY side never drops -- deny is always safe.)
                    continue
                out[key] = True
        return out

    # Ungrantable stripping on GRANT is unconditional (inviolable invariant);
    # actor_is_superadmin is intentionally NOT used to relax it.
    _ = actor_is_superadmin
    grant = _clean_side("grant", drop_ungrantable=True)
    deny = _clean_side("deny", drop_ungrantable=False)

    # Deny beats grant (frozen precedence): a key on both sides stays only in
    # deny.
    for key in list(grant.keys()):
        if key in deny:
            del grant[key]

    cleaned = {}
    if grant:
        cleaned["grant"] = grant
    if deny:
        cleaned["deny"] = deny
    return cleaned


def grantable_capabilities_for(actor_roles: Iterable[str]):
    """The set of capability keys an actor may GRANT to others: those reachable
    by the actor's own roles (you can't grant what you can't do) MINUS the
    ungrantable set (unless SUPERADMIN, who may grant anything they hold).

    Used by the WRITE-time escalation guard. SUPERADMIN -> the full grantable
    universe (everything except the structurally-ungrantable, which even a
    SUPERADMIN does not grant to a LESSER user -- a SUPERADMIN target is set by
    role assignment, not by this layer).
    """
    from .capabilities import (
        VALID_CAPABILITY_KEYS,
        capability_roles,
        is_ungrantable,
    )

    actor_set = set(actor_roles or [])
    is_super = "SUPERADMIN" in actor_set
    grantable = set()
    for cap in VALID_CAPABILITY_KEYS:
        if is_ungrantable(cap):
            # jarvis:* / SUPERADMIN-only: never grantable via the per-user layer,
            # not even by a SUPERADMIN actor (those routes are SUPERADMIN-by-role
            # already; the layer would only ever GRANT to a lesser user).
            continue
        if is_super:
            grantable.add(cap)
            continue
        # Non-super: the actor must themselves be able to reach the capability.
        cap_roles = set(capability_roles(cap))
        # AUTHENTICATED-reachable capabilities are reachable by any logged-in
        # user, so any actor may grant them.
        if "AUTHENTICATED" in cap_roles:
            grantable.add(cap)
            continue
        if actor_set & cap_roles:
            grantable.add(cap)
    return grantable
