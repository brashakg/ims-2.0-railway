"""
IMS 2.0 - Capability universe + route->capability resolver
==========================================================

The granularity for the per-user permissions layer (council ruling sec.2). A
CAPABILITY is a coarse VERB over a module, derived from the ``rbac_policy``
registry by collapsing every catalogued ``(method, path)`` to a single key:

    GET   under /<module>           -> "<module>:read"
    POST/PUT/PATCH/DELETE           -> "<module>:write"
    /approvals/*/approve|reject     -> "approvals:approve"  (curated, the
                                       dedicated maker-checker surface only)
    /products/qc-samples*           -> "products:qc"        (curated: the QC
                                       sampling workflow is manager-ladder
                                       gated and must NOT broaden the
                                       products:write role union -- see
                                       capability_for)
    /catalog/flags                  -> "catalog:flag"       (curated: STORE_
                                       MANAGER raises catalogue flags and must
                                       not broaden catalog:write)
    /online-store/orders/*/clear-rx-hold
                                    -> "online-store:rx-clear"  (curated: the
                                       clinical Rx-hold release is kept OUT of
                                       the shared online-store:write key --
                                       same allowed set, so no union change; a
                                       module deny still covers it via
                                       MODULE_EXTRA_DENY_CAPABILITIES)

WHY CAPABILITIES (not raw routes, not modules)
----------------------------------------------
Raw routes churn (a new endpoint means a new override key, and an override would
fail OPEN on it). Modules are too coarse (read vs write is the single most
common owner sentence -- "let them SEE finance but not EDIT it"). A verb-over-
module key is the stable middle: a brand-new route under an existing module
inherits its module's read/write capability automatically, so an existing DENY
keeps covering it -- the override can never silently fail open on a new route.

TOTALITY (the load-bearing CI guarantee, ruling sec.2 + sec.6)
--------------------------------------------------------------
``capability_for(method, path)`` returns exactly ONE key for every NON-PUBLIC
catalogued route, and ``None`` only for PUBLIC routes (which need no auth and so
cannot be the subject of a per-user override). ``test_capabilities.py`` asserts
this against the live POLICY: every catalogued non-PUBLIC route maps to exactly
one key in ``VALID_CAPABILITY_KEYS``. If that ever fails, a route shipped that
an override could not name -- which is precisely the silent-fail-open hole.

UNGRANTABLE capabilities (inviolable invariant, ruling sec.2 chain item 0)
--------------------------------------------------------------------------
Some capabilities must NEVER be grantable to a non-SUPERADMIN via the per-user
layer (they are SUPERADMIN-only by design and protected by 3 layers):

  * ``jarvis:*``  -- all AI/agent surfaces are SUPERADMIN-only, non-negotiable.
  * every capability whose routes are ALL gated to ``["SUPERADMIN"]`` only
    (audit-chain verify, ML analytics-v2, techcherry, tally regenerate, ...).

``UNGRANTABLE_CAPABILITY_KEYS`` is computed from the POLICY so it can never
drift from the actual gates. ``sanitize_permissions`` drops these for any
non-SUPERADMIN actor's target; the escalation guard rejects an attempt to grant
one; and the resolver still subtracts a DENY of one (deny is always safe).

This module is pure (no DB, no request state) and import-light so the middleware
hot-path and the CI tests can both use it cheaply.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, List, Optional, Set

from . import rbac_policy

# HTTP methods that read (everything else is a write for capability purposes).
_READ_METHODS = frozenset({"GET"})


def _module_of(path: str) -> Optional[str]:
    """The top-level module segment of an /api/v1/<module>/... path, or None.

    Skips the leading ``api`` / ``v1`` segments; the third concrete segment is
    the module (e.g. ``orders``, ``finance``, ``analytics-v2``). A path with too
    few segments (shouldn't happen for catalogued routes) yields None.
    """
    parts = [p for p in str(path).split("/") if p]
    if len(parts) >= 3 and parts[0] == "api" and parts[1] == "v1":
        return parts[2]
    return None


def capability_for(method: str, path: str) -> Optional[str]:
    """Resolve a concrete (method, path) to its single capability key.

    Returns None ONLY when the route is PUBLIC (no auth -> no override subject)
    or un-catalogued (the resolver mirrors rbac_policy.policy_for so an
    un-catalogued route also yields None -- and the middleware fail-opens it,
    same posture as the role layer). Every catalogued NON-PUBLIC route returns
    exactly one key (proven by the totality test).
    """
    entry = rbac_policy.policy_for(method, path)
    if entry is None:
        return None
    if entry["allowed"] == rbac_policy.PUBLIC:
        return None
    # Resolve against the MATCHED template path so path-params are normalised
    # identically to the role layer (a concrete id and its {param} template map
    # to the same capability).
    tmpl = str(entry["path"])
    mod = _module_of(tmpl)
    if mod is None:
        return None
    # Curated finer capability: the dedicated maker-checker approvals surface.
    # Other module-specific approve actions (expenses/hr/finance JE/...) collapse
    # to their module's :write -- that is the natural verb and keeps totality.
    if mod == "approvals" and (tmpl.endswith("/approve") or tmpl.endswith("/reject")):
        return "approvals:approve"
    # Curated finer capability: the cataloguing-QC sampling workflow. Its
    # routes are gated to the MANAGER LADDER (AREA_MANAGER/STORE_MANAGER
    # included); collapsing them to the generic products:write would add
    # those roles to that capability's role union, and the grant guard
    # (user_roles.grantable_capabilities_for) reasons FROM that union -- a
    # STORE_MANAGER would suddenly be able to grant full product create/edit
    # to anyone. One key for BOTH verbs (precedent: approvals:approve already
    # breaks the read/write shape); the sibling manager-gated GETs
    # (/products/cataloguers, /products/cataloguing-scorecard) stay
    # products:read, whose union is already AUTHENTICATED-broad, so they
    # broaden nothing.
    if tmpl.startswith("/api/v1/products/qc-samples"):
        return "products:qc"
    # Curated finer capability: releasing the clinical Rx FLAG-AND-HOLD on an
    # online order (POST /online-store/orders/{id}/clear-rx-hold). Its allowed
    # set is IDENTICAL to the generic online-store write routes (ADMIN/SUPERADMIN),
    # so the online-store:write role UNION is unchanged either way -- this is NOT
    # an escalation. But a clinical mutation must not ride the shared module key:
    # a dedicated key keeps clear-rx-hold permanently OUT of online-store:write
    # (capability-key hygiene, PR #947 follow-up 1; precedent: approvals:approve,
    # products:qc). A DENY of the 'ecommerce' module still covers it -- see
    # MODULE_EXTRA_DENY_CAPABILITIES below, so the carve-out does not open a hole
    # in the module deny.
    if mod == "online-store" and tmpl.endswith("/clear-rx-hold"):
        return "online-store:rx-clear"
    # Curated finer capability: raising a catalogue flag ("needs work",
    # owner 2026-10-12). STORE_MANAGER may flag; collapsing it to the generic
    # catalog:write would let the grant guard treat a store manager as able
    # to hand out catalogue editing. Marking a flag fixed stays catalog:write
    # (its roles already edit the catalogue).
    if tmpl == "/api/v1/catalog/flags":
        return "catalog:flag"
    # Curated finer capability: the PERMANENT delete of an archived product
    # (owner 2026-10-24, #1273). Every route under it is SUPERADMIN-only, so
    # _build_universe makes the key UNGRANTABLE: no per-user grant of the
    # generic catalog:write can ever reach it (no module deny maps 'catalog',
    # as for catalog:flag).
    if tmpl in ("/api/v1/catalog/products/{product_id}/permanent",
                "/api/v1/catalog/products/bulk-purge"):
        return "catalog:purge"
    # Curated finer capability: SUPERADMIN sets ANY user's password (owner ask
    # 2026-11-01). Its only route is SUPERADMIN-only, so _build_universe makes
    # the key UNGRANTABLE: a per-user grant of users:write (which ADMIN's
    # temporary-password reset rides) can never reach a full account takeover.
    # A deny of users:write / the users module still answers it:
    # PARENT_CAPABILITY.
    if tmpl == "/api/v1/users/{user_id}/set-password":
        return "users:set-password"
    # Curated finer capability: LOGGING a vendor return. Owner ruling
    # 2026-10-12 lets WORKSHOP_STAFF log one (unpriced); on the generic
    # vendor-returns:write key they would join the approve / ship / credit
    # union the grant guard reasons from. A stored per-user deny or grant of
    # vendor-returns:write, and a 'vendors' module deny (which denies
    # vendor-returns:write), still answer it: PARENT_CAPABILITY.
    if method.upper() == "POST" and tmpl.rstrip("/") == "/api/v1/vendor-returns":
        return "vendor-returns:create"
    if tmpl in ("/api/v1/vendor-returns/{return_id}/credit-preview",
                "/api/v1/vendor-returns/{return_id}/credit-confirmations"):
        return "vendor-returns:confirm-credit"
    if tmpl in ("/api/v1/vendor-rma/{rma_id}/credit-preview",
                "/api/v1/vendor-rma/{rma_id}/credit-note"):
        return "vendor-rma:confirm-credit"
    verb = "read" if method.upper() in _READ_METHODS else "write"
    return f"{mod}:{verb}"


# A curated key carved out of its module's generic key for the ROLE layer only.
# The route was the parent key before the carve-out, so a per-user deny or
# grant stored on the parent must keep answering it (permission_resolver):
# else a user denied vendor-returns:write could log returns again. It is also
# the one place a module deny reaches the carved-out key (the module deny names
# the parent), so the key needs no MODULE_EXTRA_DENY_CAPABILITIES entry.
PARENT_CAPABILITY: Dict[str, str] = {
    "vendor-returns:create": "vendor-returns:write",
    "vendor-returns:confirm-credit": "vendor-returns:write",
    "vendor-rma:confirm-credit": "vendor-rma:write",
    # A parent GRANT never lifts it: the resolver refuses any grant of an
    # ungrantable key, and users:set-password is SUPERADMIN-only.
    "users:set-password": "users:write",
}


def _build_universe() -> (
    "tuple[FrozenSet[str], FrozenSet[str], Dict[str, Set[str]]]"
):
    """Walk the POLICY once to derive:

      * VALID_CAPABILITY_KEYS    -- every capability key any catalogued route
                                    maps to.
      * UNGRANTABLE_CAPABILITY_KEYS -- keys whose routes are ALL SUPERADMIN-only
                                    (incl. every jarvis:* key), so the layer must
                                    never grant them to a lesser actor.
      * _CAP_ROLE_HINT           -- for each capability, the UNION of roles that
                                    can reach ANY of its routes. This IS an
                                    enforcement input: user_roles.
                                    grantable_capabilities_for uses it to decide
                                    which capabilities a non-SUPERADMIN actor may
                                    GRANT ("you can grant what your roles can
                                    reach"). Consequence: adding a new route row
                                    with a broader role list BROADENS who may
                                    grant that module's capability -- which is
                                    exactly why the manager-ladder QC routes get
                                    their own curated products:qc key instead of
                                    joining products:write.
    """
    keys: Set[str] = set()
    # For ungrantable detection: a capability is ungrantable iff EVERY route it
    # covers is gated to SUPERADMIN-only (allowed == ["SUPERADMIN"]).
    superadmin_only: Dict[str, bool] = {}
    role_hint: Dict[str, Set[str]] = {}

    for entry in rbac_policy.POLICY:
        cap = capability_for(str(entry["method"]), str(entry["path"]))
        if cap is None:
            continue
        keys.add(cap)
        allowed = entry["allowed"]
        is_su_only = isinstance(allowed, list) and allowed == ["SUPERADMIN"]
        # AND across all routes for this capability: stays True only while every
        # route seen so far is SUPERADMIN-only.
        superadmin_only[cap] = superadmin_only.get(cap, True) and is_su_only
        hint = role_hint.setdefault(cap, set())
        if isinstance(allowed, list):
            hint.update(allowed)
        elif allowed == rbac_policy.AUTHENTICATED:
            # Any logged-in user; record a sentinel so the FE knows it is broad.
            hint.add(rbac_policy.AUTHENTICATED)

    ungrantable: Set[str] = {c for c, only in superadmin_only.items() if only}
    # jarvis:* is ALWAYS ungrantable regardless of how the gates read (defence in
    # depth -- the AI surface is SUPERADMIN-only, non-negotiable per CLAUDE.md).
    ungrantable.update(c for c in keys if c.startswith("jarvis:"))

    return frozenset(keys), frozenset(ungrantable), role_hint


VALID_CAPABILITY_KEYS, UNGRANTABLE_CAPABILITY_KEYS, _CAP_ROLE_HINT = _build_universe()


def is_known_capability(key: str) -> bool:
    return key in VALID_CAPABILITY_KEYS


def is_ungrantable(key: str) -> bool:
    """True when this capability must never be granted to a non-SUPERADMIN via
    the per-user layer (SUPERADMIN-only routes + every jarvis:* key)."""
    return key in UNGRANTABLE_CAPABILITY_KEYS


def capability_roles(key: str) -> List[str]:
    """The union of roles that can reach any route of this capability (or the
    AUTHENTICATED sentinel).

    ENFORCEMENT INPUT for the grant guard: user_roles.grantable_capabilities_for
    derives what a non-SUPERADMIN actor may GRANT from this union (an actor can
    only grant capabilities their own roles reach). Broadening a capability's
    role union therefore broadens who can grant it -- keep manager-ladder route
    families on curated keys (approvals:approve, products:qc) rather than
    letting them widen a module's generic :write union. Request-time route
    ACCESS enforcement remains the resolver + role layer, not this hint."""
    return sorted(_CAP_ROLE_HINT.get(key, set()))


# ---------------------------------------------------------------------------
# module_access (legacy deny-only) -> capability DENY read-shim (ruling sec.2)
# ---------------------------------------------------------------------------
# The legacy per-user ``module_access`` map denies a frontend MODULE (pos,
# finance, ...). The capability layer reads BOTH: a module-deny is mapped, at
# READ time, to the equivalent capability denies (read+write of the module's
# capabilities). NO data migration -- the resolver simply honours the old field
# too, so an existing module_access deny keeps working unchanged.
#
# The frontend module keys (VALID_MODULE_KEYS in user_roles) are NOT 1:1 with
# the API top-level path segments, so this is an explicit, reviewed mapping. A
# module not listed here has no capability equivalent (its deny stays purely a
# frontend-nav hint, exactly as today) -- we never invent an enforcement effect
# the legacy field did not already have on its own module.
MODULE_TO_CAPABILITY_MODULES: Dict[str, List[str]] = {
    # frontend module key -> API path module(s) it gates
    "pos": ["orders", "till"],
    "clinic": ["clinical", "prescriptions"],
    "inventory": ["inventory", "transfers", "serials"],
    "customers": ["customers", "crm"],
    "vendors": ["vendors", "vendor-returns", "vendor-rma", "vendor-rebates"],
    "workshop": ["workshop", "repairs"],
    "hr": ["hr", "payroll"],
    "reports": ["reports", "analytics"],
    "finance": ["finance", "budgets", "expenses"],
    # OS-053: the Online Store module. A per-user 'ecommerce' deny maps to
    # online-store:read/write capability DENIES so the API surface is blocked
    # too, not just the nav. DENY-only mapping -- adds nothing to any grant
    # union (the rbac capability-union gotcha concerns POLICY rows, not this).
    "ecommerce": ["online-store"],
}

# Curated non-verb capability keys that belong to an api module for DENY purposes.
# A generic ``<module>:read|write`` deny does not name a curated finer key (e.g.
# the clinical ``online-store:rx-clear`` carve-out), so a module deny must add it
# explicitly or the deny would silently fail OPEN on the carved-out route (an
# 'ecommerce'-denied user could still reach clear-rx-hold). DENY-only, exactly
# like MODULE_TO_CAPABILITY_MODULES -- this never contributes to any grant union
# (the rbac capability-union gotcha concerns POLICY rows, not this map).
MODULE_EXTRA_DENY_CAPABILITIES: Dict[str, List[str]] = {
    "online-store": ["online-store:rx-clear"],
}


def module_deny_to_capability_denies(module_access: Optional[Dict[str, bool]]) -> Set[str]:
    """Translate a legacy deny-only ``module_access`` map into the set of
    capability keys it implicitly DENIES (read + write of each mapped module).

    Only ``False`` (deny) entries produce denies; the legacy field is deny-only,
    so a grant/True or absent key produces nothing. Unknown module keys and
    capability keys that do not actually exist in the universe are skipped. Pure;
    safe on None ({} out)."""
    denies: Set[str] = set()
    if not module_access:
        return denies
    for mod_key, value in module_access.items():
        if value is not False:
            continue
        for api_mod in MODULE_TO_CAPABILITY_MODULES.get(mod_key, []):
            for verb in ("read", "write"):
                cap = f"{api_mod}:{verb}"
                if cap in VALID_CAPABILITY_KEYS:
                    denies.add(cap)
            # Also deny curated finer keys (e.g. online-store:rx-clear) so the
            # module deny does not fail open on a carved-out route.
            for cap in MODULE_EXTRA_DENY_CAPABILITIES.get(api_mod, []):
                if cap in VALID_CAPABILITY_KEYS:
                    denies.add(cap)
    return denies
