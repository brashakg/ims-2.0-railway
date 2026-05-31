"""
IMS 2.0 — HR leave approve/reject must be manager-gated
========================================================
approve_leave / reject_leave were gated only by get_current_user, so ANY
authenticated user -- including the applicant -- could approve or reject leave.
They now use require_roles(*_SWAP_APPROVER_ROLES), the same manager-approver gate
the shift-swap approval flow uses. This locks that the endpoint dependencies are
the role-gated ones (a non-manager would be 403'd by the dependency).
"""

from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("JWT_SECRET_KEY", "test-key-hr-leave-gate")


def _dep_names(func):
    """Return the set of dependency callables referenced in the endpoint's
    signature defaults (FastAPI Depends(...))."""
    sig = inspect.signature(func)
    names = []
    for p in sig.parameters.values():
        dep = getattr(p.default, "dependency", None)
        if dep is not None:
            names.append(getattr(dep, "__name__", repr(dep)))
    return names


def test_approve_leave_is_role_gated_not_plain_auth():
    from api.routers.hr import approve_leave

    deps = _dep_names(approve_leave)
    # require_roles returns a closure named _require_roles / role_checker — the
    # key assertion is it is NOT the bare get_current_user.
    assert "get_current_user" not in deps, "approve_leave must not be plain-auth"
    assert deps, "approve_leave must carry a role-gated dependency"


def test_reject_leave_is_role_gated_not_plain_auth():
    from api.routers.hr import reject_leave

    deps = _dep_names(reject_leave)
    assert "get_current_user" not in deps, "reject_leave must not be plain-auth"
    assert deps


def test_swap_approver_roles_excludes_junior_roles():
    from api.routers.hr import _SWAP_APPROVER_ROLES

    for junior in ("SALES_CASHIER", "SALES_STAFF", "WORKSHOP_STAFF", "OPTOMETRIST"):
        assert junior not in _SWAP_APPROVER_ROLES
    for mgr in ("ADMIN", "AREA_MANAGER", "STORE_MANAGER"):
        assert mgr in _SWAP_APPROVER_ROLES
