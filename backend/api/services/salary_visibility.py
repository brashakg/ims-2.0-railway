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

THE SECOND COROLLARY (added 2026-08-14, learned the expensive way)
-------------------------------------------------------------------
The subtraction does not have to happen INSIDE one response. Round 1 of PR #985
made GET /finance/pnl payroll-EXCLUSIVE for a store manager and left GET
/finance/cash-flow payroll-INCLUSIVE over the SAME store and the SAME month. Two
requests, one subtraction, and the wage bill was back:

    cash-flow.expense_outflow 88360.65 - pnl.total_expenses 24450.00 = 63910.65

The sibling sweep had looked at /finance/cash-flow and cleared it because its
body carries "a grand total, no head names". THE HEAD NAME WAS NEVER THE LEAK.
Two figures over the same scope that differ only by payroll ARE the payroll,
whatever they are called. So when you make one route payroll-exclusive, the
question is not "does this other route name a salary" -- it is "does this other
route total the same expenses over a scope the same caller can ask for".

No emoji (Windows cp1252).
"""

from __future__ import annotations

import re
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


# ===========================================================================
# "Is this free-text expense head somebody's pay?"
#
# LIVED HERE SINCE 2026-08-14, and it lives here for the same reason the role
# tuple does. It was born private inside routers/finance.py; within one round
# routers/budgets.py was found emitting the identical head, by name, to the
# identical roles. A matcher that lives in one router's private namespace is a
# matcher the next router will fork or forget. Import it, never re-type it.
# ===========================================================================

# The `expenses` collection's `category` is FREE TEXT typed by whoever books the
# spend. The automated payroll run genuinely never writes there -- it writes the
# `payroll` collection -- but a PERSON can, and if anybody books "Salary" or
# "Staff wages" as an ordinary expense it reaches a store manager verbatim, by
# head and by amount. services/survival_cashflow.py already lists
# "salary"/"payroll" among its expense heads, so this shape is anticipated in
# this codebase, not hypothetical.
#
# WHAT THIS COVERS, EXACTLY: a category whose NORMALISED form (lower-cased,
# punctuation folded to spaces, runs of whitespace collapsed -- see
# ``normalise_expense_category``) is one of the strings below. So "Salary",
# " SALARY ", "Salaries & Wages" and "staff-wages" are all caught.
#
# WHAT IT DOES NOT COVER, and cannot: free text is free. "Sal Mar-26", "Ramesh
# payment", "Staff", a misspelling, a Hindi or transliterated head, or whatever
# head somebody invents next month all sail through. An exact-match list is still
# worth having because it catches the heads a person actually reaches for, and
# because the alternative -- substring or fuzzy matching -- would silently
# swallow innocent heads ("commission to broker", "salary advance recovery" from
# a customer) and quietly corrupt the manager's operating-cost panel, which is a
# worse failure than the one it prevents. The durable fix is a controlled
# expense-category list; this is the cheap guard until that exists.
PAYROLL_SHAPED_EXPENSE_CATEGORIES = frozenset(
    {
        "salary",
        "salaries",
        "salary wages",
        "salaries wages",
        "salary and wages",
        "salaries and wages",
        "wage",
        "wages",
        "staff salary",
        "staff salaries",
        "staff wage",
        "staff wages",
        "staff cost",
        "staff costs",
        "staff pay",
        "employee salary",
        "employee salaries",
        "employee cost",
        "employee costs",
        "payroll",
        "payroll cost",
        "payroll costs",
        "payroll expense",
        "payroll expenses",
        "remuneration",
        "staff remuneration",
        "staff incentive",
        "staff incentives",
        "staff bonus",
        "employee bonus",
        "staff commission",
        "gratuity",
        "pf",
        "epf",
        "pf contribution",
        "epf contribution",
        "employer pf",
        "esi",
        "esic",
        "esi contribution",
        "esic contribution",
    }
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalise_expense_category(category) -> str:
    """Lower-case, fold punctuation to spaces, collapse whitespace.

    "Salaries & Wages" -> "salaries wages"; "  STAFF-SALARY " -> "staff salary".
    A non-string (None, a number) normalises to "" and therefore never matches.
    """
    text = _NON_ALNUM.sub(" ", str(category or "").lower())
    return " ".join(text.split())


def is_payroll_shaped_expense(category) -> bool:
    """Whether this free-text expense head means 'this is somebody's pay'."""
    return normalise_expense_category(category) in PAYROLL_SHAPED_EXPENSE_CATEGORIES
