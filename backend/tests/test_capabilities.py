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
    (approvals:approve, products:qc)."""
    assert C.VALID_CAPABILITY_KEYS
    for key in C.VALID_CAPABILITY_KEYS:
        assert ":" in key, key
        verb = key.rsplit(":", 1)[1]
        assert verb in ("read", "write", "approve", "qc"), key


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
