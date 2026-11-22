"""THE rule for which expense claims are a real, committed cost (audit L3-03).

An expense is a cost from the moment it is APPROVED. Handing it to the
accountant (SENT_TO_ACCOUNTANT) and the accountant entering it in the books
(ENTERED) are bookkeeping steps on the same approved cost: they must never
take it back out of the P&L, the budget actuals, the cash-flow outflows or the
cash drawer's payouts. PAID and REIMBURSED are older stored spellings of the
same committed state and stay counted. PENDING / APPROVING (not yet decided)
and REJECTED are never a cost.

Every reader imports this tuple (never a local copy): before this module each
reader carried its own list, and all of them stopped at APPROVED / PAID, so a
completed accounting hand-off made reported profit rise.
"""

COMMITTED_EXPENSE_STATUSES = (
    "APPROVED",
    "SENT_TO_ACCOUNTANT",
    "ENTERED",
    "PAID",
    "REIMBURSED",
)

# For a Mongo ``{"status": {"$in": ...}}`` filter: legacy rows were written in
# lower case too.
COMMITTED_EXPENSE_STATUS_MATCH = list(COMMITTED_EXPENSE_STATUSES) + [
    s.lower() for s in COMMITTED_EXPENSE_STATUSES
]


def is_committed_expense(status) -> bool:
    """True when an expense row in this state is a committed cost."""
    return str(status or "").strip().upper() in COMMITTED_EXPENSE_STATUSES
