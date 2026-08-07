"""
IMS 2.0 - Capability universe + totality coverage-lock (REQUIRED PR2 deliverable)
=================================================================================
The load-bearing CI test the council ruling protects in writing (sec.2 + sec.6):

  (a) every catalogued NON-PUBLIC rbac_policy route maps to EXACTLY ONE
      capability key (else a per-user override silently fails OPEN on a new
      route -- the "owner can't create dead storage" promise's beam);
  (b) every capability either has a delta row OR is explicitly annotated as
      "not user-grantable" (the ungrantable set / the discount field).

Run: ``JWT_SECRET_KEY=test python -m pytest backend/tests/test_capabilities.py -q``
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import capabilities as C  # noqa: E402
from api.services import capability_deltas as CD  # noqa: E402
from api.services import rbac_policy as P  # noqa: E402


# ---------------------------------------------------------------------------
# (a) TOTALITY -- every non-PUBLIC route maps to exactly one known capability.
# ---------------------------------------------------------------------------

def test_every_non_public_route_maps_to_exactly_one_capability():
    """The coverage-lock. Every catalogued (method, path) that is NOT PUBLIC
    must resolve to one key in VALID_CAPABILITY_KEYS; every PUBLIC route to
    None. A failure means a route an override cannot name -> silent fail-open."""
    violations = []
    for e in P.POLICY:
        cap = C.capability_for(str(e["method"]), str(e["path"]))
        if e["allowed"] == P.PUBLIC:
            if cap is not None:
                violations.append(("PUBLIC route got a capability", e["method"], e["path"], cap))
            continue
        if cap is None:
            violations.append(("non-PUBLIC route has NO capability", e["method"], e["path"]))
        elif cap not in C.VALID_CAPABILITY_KEYS:
            violations.append(("capability not in universe", e["method"], e["path"], cap))
    assert violations == [], (
        f"{len(violations)} capability-totality violation(s):\n"
        + "\n".join(repr(v) for v in violations)
    )


def test_capability_universe_nonempty_and_verb_shaped():
    """Every key is ``<module>:read|write`` or a curated key
    (approvals:approve, products:qc, online-store:rx-clear)."""
    assert C.VALID_CAPABILITY_KEYS
    for key in C.VALID_CAPABILITY_KEYS:
        assert ":" in key, key
        verb = key.rsplit(":", 1)[1]
        assert verb in ("read", "write", "approve", "qc", "rx-clear"), key


def test_clear_rx_hold_is_curated_capability():
    """PR #947 follow-up 1: the clinical Rx-hold release is carved out to its own
    key `online-store:rx-clear` (kept OUT of the shared online-store:write key)."""
    assert (
        C.capability_for(
            "POST", "/api/v1/online-store/orders/{order_id}/clear-rx-hold"
        )
        == "online-store:rx-clear"
    )
    # A concrete id resolves identically (path-params normalised like the role layer).
    assert (
        C.capability_for("POST", "/api/v1/online-store/orders/ord-1/clear-rx-hold")
        == "online-store:rx-clear"
    )
    assert "online-store:rx-clear" in C.VALID_CAPABILITY_KEYS


def test_clear_rx_hold_carveout_does_not_broaden_online_store_write():
    """The carve-out must not widen who can reach/grant online-store:write. The
    rx-clear role union is IDENTICAL to the module write routes' set (ADMIN/
    SUPERADMIN) and a SUBSET of online-store:write's union -- so no role gains a
    new grant surface (rbac capability-union gotcha)."""
    assert C.capability_roles("online-store:rx-clear") == ["ADMIN", "SUPERADMIN"]
    write_roles = set(C.capability_roles("online-store:write"))
    assert set(C.capability_roles("online-store:rx-clear")) <= write_roles
    # ADMIN reaches online-store writes already, so rx-clear is not SUPERADMIN-only
    # -> grantable exactly like remap was under online-store:write (no escalation).
    assert not C.is_ungrantable("online-store:rx-clear")


def test_capability_resolution_matches_role_layer_path_params():
    """A concrete id path and its template both resolve to the same capability
    (params normalised identically to rbac_policy.policy_for)."""
    assert C.capability_for("PUT", "/api/v1/stores/BV-BOK-01") == "stores:write"
    assert C.capability_for("GET", "/api/v1/orders/ORD-123") == "orders:read"
    assert C.capability_for("POST", "/api/v1/orders") == "orders:write"


def test_public_route_has_no_capability():
    assert C.capability_for("POST", "/api/v1/auth/login") is None
    assert C.capability_for("GET", "/api/v1/portal/track/abc") is None


def test_approvals_approve_is_curated_capability():
    assert (
        C.capability_for("POST", "/api/v1/approvals/requests/R1/approve")
        == "approvals:approve"
    )
    assert (
        C.capability_for("POST", "/api/v1/approvals/requests/R1/reject")
        == "approvals:approve"
    )


# ---------------------------------------------------------------------------
# UNGRANTABLE -- jarvis:* + every SUPERADMIN-only capability is ungrantable.
# ---------------------------------------------------------------------------

def test_jarvis_capabilities_are_ungrantable():
    jarvis_caps = [c for c in C.VALID_CAPABILITY_KEYS if c.startswith("jarvis:")]
    assert jarvis_caps, "expected jarvis capabilities to exist"
    for c in jarvis_caps:
        assert C.is_ungrantable(c), c


def test_superadmin_only_capabilities_are_ungrantable():
    # audit:read (audit verify) + payout:write (lock / mark-paid) are SUPERADMIN
    # -only per the gates -> auto-derived as ungrantable.
    assert C.is_ungrantable("audit:read")
    assert C.is_ungrantable("payout:write")


def test_mixed_gate_capability_is_grantable():
    # analytics-v2:read has BOTH superadmin-only and AUTHENTICATED routes, so it
    # is NOT all-superadmin -> grantable.
    assert "analytics-v2:read" in C.VALID_CAPABILITY_KEYS
    assert not C.is_ungrantable("analytics-v2:read")


# ---------------------------------------------------------------------------
# (b) ANNOTATION TOTALITY -- every delta-row key is a real capability, and the
# delta table only references grantable capabilities (you cannot expose an
# ungrantable toggle to a non-superadmin owner).
# ---------------------------------------------------------------------------

def test_every_delta_key_is_a_real_capability():
    bad = [k for k in CD._delta_keys() if k not in C.VALID_CAPABILITY_KEYS]
    assert bad == [], f"delta rows reference unknown capabilities: {bad}"


def test_delta_rows_never_expose_an_ungrantable_capability():
    bad = [k for k in CD._delta_keys() if C.is_ungrantable(k)]
    assert bad == [], f"delta rows expose ungrantable capabilities: {bad}"


def test_seeded_roles_have_deltas():
    # The three roles the ruling names must have curated deltas.
    for role in ("SALES_CASHIER", "OPTOMETRIST", "STORE_MANAGER"):
        rows = CD.deltas_for_role(role)
        assert rows, role
        # Each row has the required owner-facing shape.
        for row in rows:
            assert "key" in row and "label" in row and "type" in row
            assert row["type"] in ("toggle", "number")
            assert isinstance(row["label"], str) and row["label"]


# ---------------------------------------------------------------------------
# module_access -> capability DENY shim (read-time, no migration).
# ---------------------------------------------------------------------------

def test_module_deny_maps_to_capability_denies():
    denies = C.module_deny_to_capability_denies({"pos": False})
    assert "orders:read" in denies and "orders:write" in denies
    assert "till:read" in denies and "till:write" in denies


def test_module_grant_or_absent_maps_to_nothing():
    # Deny-only: a True/grant or absent key produces no capability deny.
    assert C.module_deny_to_capability_denies({"pos": True}) == set()
    assert C.module_deny_to_capability_denies({}) == set()
    assert C.module_deny_to_capability_denies(None) == set()


def test_ecommerce_deny_maps_to_online_store_capabilities():
    """OS-053: denying the 'ecommerce' module must block the online-store API
    surface, not just the nav -- read+write of the online-store module PLUS the
    curated online-store:rx-clear carve-out (PR #947 follow-up 1: a module deny
    must not fail open on a route the generic read/write pair does not name)."""
    denies = C.module_deny_to_capability_denies({"ecommerce": False})
    assert denies == {
        "online-store:read",
        "online-store:write",
        "online-store:rx-clear",
    }


def test_module_capability_mapping_is_live():
    """Tripwire against the silent fail-OPEN: module_deny_to_capability_denies
    silently DROPS capability keys not in VALID_CAPABILITY_KEYS, so a future
    API path-prefix rename (e.g. /online-store -> /shop) would degrade a
    module deny to nav-only with zero API enforcement -- and green tests.
    Pin (a) every mapped frontend module is a real deniable module key, and
    (b) every mapped api module still produces at least one live capability."""
    from api.services.user_roles import VALID_MODULE_KEYS

    # (a) mapping keys are a subset of the deniable module keys.
    assert set(C.MODULE_TO_CAPABILITY_MODULES) <= set(VALID_MODULE_KEYS)

    # (b) each mapped api module yields >= 1 key in VALID_CAPABILITY_KEYS, and
    # each module's deny is non-empty overall.
    for mod_key, api_mods in C.MODULE_TO_CAPABILITY_MODULES.items():
        denies = C.module_deny_to_capability_denies({mod_key: False})
        assert denies, (
            f"module deny for '{mod_key}' produces NO capability denies (fail-open)"
        )
        for api_mod in api_mods:
            live = {
                f"{api_mod}:{verb}"
                for verb in ("read", "write")
                if f"{api_mod}:{verb}" in C.VALID_CAPABILITY_KEYS
            }
            assert live, (
                f"'{mod_key}' maps to api module '{api_mod}' which produces no key "
                "in VALID_CAPABILITY_KEYS -- stale path mapping, the deny fails open"
            )
