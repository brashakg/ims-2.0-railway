"""
IMS 2.0 - Per-user permission resolver + sanitize_permissions (pure)
====================================================================
Locks the FROZEN precedence chain (council ruling sec.2) and the DARK-BY-DEFAULT
safety property: a user with no ``permissions`` field behaves EXACTLY as today.

Run: ``JWT_SECRET_KEY=test python -m pytest backend/tests/test_permission_resolver.py -q``
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("JWT_SECRET_KEY", "test")
os.environ.setdefault("ENVIRONMENT", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.services import user_roles as UR  # noqa: E402
from api.services.permission_resolver import apply_user_permissions as A  # noqa: E402


# ---------------------------------------------------------------------------
# DARK BY DEFAULT -- the non-negotiable safety property (regression test).
# ---------------------------------------------------------------------------

def test_dark_no_permissions_field_identical_to_role_decision():
    """A user with NO permissions and NO module_access gets EXACTLY the role
    decision back, for both allow and deny, on every capability. This is the
    proof that absent permissions == today's behaviour."""
    for cap in ("orders:write", "finance:read", "jarvis:read", "reports:read"):
        assert A(True, cap, None, None) is True, cap
        assert A(False, cap, None, None) is False, cap
        # empty maps are equally dark
        assert A(True, cap, {}, {}) is True, cap
        assert A(False, cap, {}, {}) is False, cap


def test_dark_when_capability_is_none():
    # PUBLIC / un-catalogued route -> no capability -> role decision unchanged,
    # even if the user happens to carry overrides.
    assert A(True, None, {"deny": {"orders:write": True}}, None) is True
    assert A(False, None, {"grant": {"orders:write": True}}, None) is False


# ---------------------------------------------------------------------------
# DENY subtract / GRANT add / precedence.
# ---------------------------------------------------------------------------

def test_capability_deny_subtracts_a_role_allow():
    assert A(True, "orders:write", {"deny": {"orders:write": True}}, None) is False


def test_capability_grant_adds_a_role_denied():
    assert A(False, "reports:read", {"grant": {"reports:read": True}}, None) is True


def test_deny_always_beats_grant():
    perms = {"grant": {"orders:write": True}, "deny": {"orders:write": True}}
    assert A(True, "orders:write", perms, None) is False
    assert A(False, "orders:write", perms, None) is False


def test_grant_of_unrelated_capability_does_not_leak():
    # A grant of A must not affect capability B.
    assert A(False, "finance:write", {"grant": {"reports:read": True}}, None) is False


def test_ungrantable_grant_never_takes_effect_in_resolver():
    # Belt & braces: even if jarvis:read slipped into the grant map, the
    # resolver refuses to honour it (inviolable invariant, chain step 0).
    assert A(False, "jarvis:read", {"grant": {"jarvis:read": True}}, None) is False
    # ...but a DENY of an ungrantable is honoured (deny is always safe).
    assert A(True, "jarvis:read", {"deny": {"jarvis:read": True}}, None) is False


# ---------------------------------------------------------------------------
# module_access deny shim -- legacy deny honoured at READ time, no migration.
# ---------------------------------------------------------------------------

def test_module_deny_shim_subtracts():
    assert A(True, "orders:write", None, {"pos": False}) is False
    assert A(True, "till:read", None, {"pos": False}) is False
    # an unmapped capability under a denied module is untouched
    assert A(True, "finance:read", None, {"pos": False}) is True


def test_module_deny_beats_capability_grant():
    # Module deny (step 2) is a deny and beats a grant (step 4).
    assert (
        A(False, "orders:write", {"grant": {"orders:write": True}}, {"pos": False})
        is False
    )


# ---------------------------------------------------------------------------
# sanitize_permissions -- drop junk + ungrantable-grant; deny beats grant.
# ---------------------------------------------------------------------------

def test_sanitize_drops_unknown_keys():
    out = UR.sanitize_permissions(
        {"grant": {"bogus:thing": True}, "deny": {"also:fake": True}}
    )
    assert out == {}


def test_sanitize_keeps_known_grant_and_deny():
    out = UR.sanitize_permissions(
        {"grant": {"reports:read": True}, "deny": {"orders:write": True}}
    )
    assert out == {"grant": {"reports:read": True}, "deny": {"orders:write": True}}


def test_sanitize_drops_ungrantable_grant_for_non_superadmin():
    out = UR.sanitize_permissions(
        {"grant": {"jarvis:read": True, "reports:read": True}},
        actor_is_superadmin=False,
    )
    assert out == {"grant": {"reports:read": True}}


def test_sanitize_drops_jarvis_grant_even_for_superadmin_actor():
    # jarvis is structurally ungrantable via the per-user layer for EVERYONE.
    out = UR.sanitize_permissions(
        {"grant": {"jarvis:read": True}}, actor_is_superadmin=True
    )
    assert out == {}


def test_sanitize_keeps_ungrantable_deny():
    # A DENY of jarvis is harmless (deny is always safe) and is kept.
    out = UR.sanitize_permissions({"deny": {"jarvis:read": True}})
    assert out == {"deny": {"jarvis:read": True}}


def test_sanitize_deny_beats_grant_dedup():
    out = UR.sanitize_permissions(
        {"grant": {"orders:write": True}, "deny": {"orders:write": True}}
    )
    assert out == {"deny": {"orders:write": True}}


def test_sanitize_none_passthrough():
    assert UR.sanitize_permissions(None) is None
    assert UR.sanitize_permissions("nonsense") == {}


def test_sanitize_drops_non_true_values():
    out = UR.sanitize_permissions(
        {"grant": {"reports:read": False, "orders:read": None}}
    )
    assert out == {}


# ---------------------------------------------------------------------------
# grantable_capabilities_for -- escalation ceiling source.
# ---------------------------------------------------------------------------

def test_grantable_excludes_ungrantable_for_superadmin():
    g = UR.grantable_capabilities_for(["SUPERADMIN"])
    assert "jarvis:read" not in g and "audit:read" not in g
    # but a normal capability IS grantable by superadmin
    assert "reports:read" in g


def test_grantable_for_sales_cashier_limited_to_own_reach():
    g = UR.grantable_capabilities_for(["SALES_CASHIER"])
    # SALES_CASHIER can reach orders (POS) -> orders:write grantable
    assert "orders:write" in g
    # but cannot reach finance writes -> not grantable
    assert "finance:write" not in g
    # never an ungrantable
    assert "jarvis:read" not in g
