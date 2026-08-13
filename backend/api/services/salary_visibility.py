"""IMS 2.0 - Who may see somebody else's pay. ONE definition, used everywhere.

OWNER RULING 2026-08-09, verbatim: "nobody except admin/superadmin should see
anyone elses salary." He was offered an ACCOUNTANT carve-out for payslip
printing and declined it, accepting that an ADMIN must run payroll each month.

WHY THIS MODULE EXISTS (the lesson that produced it)
----------------------------------------------------
PR #974 closed the per-EMPLOYEE salary reads on routers/payroll.py, which owns
the rule in ``_SALARY_CROSS_EMPLOYEE_ROLES``. An audit then found the same rule
defeated by ARITHMETIC on other routers that never heard about it:

    AN AGGREGATE THAT CONTAINS EXACTLY ONE PERSON IS THAT PERSON'S SALARY.

The business runs 4 stores with 1-5 people each. A per-store payroll total IS an
individual's pay packet at that size, and a two-person store gives up the second
person to one subtraction against the reader's own payslip (which every employee
may legitimately read via /hr/me/payslip). routers/payroll.py:350-355 already
argued exactly this for its own totals-only exports; finance.py and payout.py
never got the memo.

A rule that lives in one router's private tuple is a rule the next router will
miss. This module is that tuple's new home so finance, payout, users and payroll
all read ONE definition. It is pure: no DB, no request state, no imports beyond
typing, so anything may depend on it.

THE COROLLARY, which is the part that keeps getting missed
-----------------------------------------------------------
Gating a ROUTE is not the same as gating the FIGURE. Any response that leaves a
payroll-INCLUSIVE number beside a payroll-EXCLUSIVE one is defeated by one
subtraction, whatever the route's role gate says. When you hide a salary figure,
hide every figure it can be recovered from -- see ``PAYROLL_DERIVED_PNL_FIELDS``
in routers/finance.py for the worked example.

No emoji (Windows cp1252).
"""

from __future__ import annotations

from typing import Iterable, Tuple

# The ONLY roles that may see another person's pay, in any shape: a payslip, a
# salary register, a store's payroll total, a per-person incentive line, or a
# figure any of those can be subtracted out of.
SALARY_ADMIN_ROLES: Tuple[str, ...] = ("SUPERADMIN", "ADMIN")

# Plain English, shown to a store manager verbatim by the frontend. It says what
# is restricted and what to do next -- not "403" and not "forbidden".
SALARY_RESTRICTED_MESSAGE = (
    "Pay and payroll figures are restricted to administrators. "
    "Please ask an administrator."
)


def _roles_of(user: dict) -> Iterable[str]:
    """Tolerant role extraction: the ``roles`` list, else a single active role.

    Mirrors services/cost_mask._roles_of. A session shape without ``roles``
    (some agent/service callers carry only ``activeRole``) must not be read as
    "no roles, therefore not an admin" ONLY by accident -- it genuinely is not
    an admin, but we check the fallback so a legitimate admin session is not
    locked out of its own screens.
    """
    user = user or {}
    roles = user.get("roles")
    if not roles:
        single = user.get("activeRole") or user.get("active_role")
        roles = [single] if single else []
    return [r for r in roles if r]


def is_salary_admin(user: dict) -> bool:
    """True when this caller may see other people's pay. Fails CLOSED.

    ``None``, ``{}``, a role-less session and an unknown role all return False,
    so a caller we cannot identify never receives salary data.
    """
    return any(r in SALARY_ADMIN_ROLES for r in _roles_of(user))
