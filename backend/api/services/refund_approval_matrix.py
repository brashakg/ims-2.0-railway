"""F27 -- Refund approval matrix (pure tier resolution).

A CONFIGURABLE matrix that decides, for a given refund, WHICH approval tier
(or none) is required before the refund can be processed. The decision is keyed
on three axes:

  * refund AMOUNT (integer paise) -- banded ascending; bigger refunds need a
    higher tier.
  * refund REASON -- e.g. DEFECTIVE / CHANGE_OF_MIND / PRICE_MATCH / GOODWILL.
    Some reasons (goodwill, price-match) are riskier and escalate sooner.
  * requesting ROLE -- a cashier's refund needs approval sooner than a
    manager's (a manager may be auto-cleared up to a higher floor).

The matrix is persisted as an E2 policy document (key ``refund.approval_matrix``)
so it is per-store / per-entity overridable, with a sane seeded default. The
``refund.matrix_enabled`` flag (default False) keeps the whole gate DARK until
the owner turns it on (mirrors the Fcostfloor rollout pattern).

Matrix shape (the E2 policy ``value``)::

    {
        "currency": "INR",
        # Optional per-role floor: refunds at/below this paise amount by this
        # role need NO approval regardless of reason band. Role keys are the
        # canonical role strings (e.g. "SALES_CASHIER", "STORE_MANAGER").
        "role_floor_paise": {
            "SALES_CASHIER": 0,
            "STORE_MANAGER": 500000,     # manager auto-cleared up to Rs 5,000
            "AREA_MANAGER": 2000000,
            "ADMIN": null,               # null == no ceiling, never needs approval
            "SUPERADMIN": null,
        },
        # Ascending amount bands. The FIRST band whose `max_paise` is >= the
        # refund amount (or whose max_paise is null == open-ended) decides the
        # BASE tier. `tier` 0 means "no approval required".
        "bands": [
            {"max_paise": 100000,  "tier": 0},   # <= Rs 1,000  -> none
            {"max_paise": 500000,  "tier": 1},   # <= Rs 5,000  -> tier 1
            {"max_paise": 2000000, "tier": 2},   # <= Rs 20,000 -> tier 2
            {"max_paise": null,    "tier": 3},   # above        -> tier 3
        ],
        # Per-reason tier bump (added to the band tier). Lets risky reasons
        # escalate one tier sooner. Missing reason == 0 bump.
        "reason_bump": {
            "DEFECTIVE": 0,
            "CHANGE_OF_MIND": 0,
            "PRICE_MATCH": 1,
            "GOODWILL": 1,
        },
        # Max tier the matrix can ever demand (clamps band+bump). Mirrors the
        # number of E4 approval tiers available.
        "max_tier": 3,
    }

``required_tier`` is a PURE function -- it touches no DB and is unit-tested in
isolation. The router layer resolves the matrix via E2 ``get_policy`` and the
flag, then calls this with plain values.
"""

from __future__ import annotations

from typing import Any, Optional

# A refund needing tier 0 needs no approval at all.
NO_APPROVAL_TIER = 0


def required_tier(
    amount_paise: int,
    reason: Optional[str],
    role: Optional[str],
    matrix: dict[str, Any],
) -> int:
    """Return the E4 approval tier required for a refund, or 0 for none.

    Pure: no DB, no I/O. See module docstring for the ``matrix`` shape.

    Args:
        amount_paise: refund amount in integer paise (>= 0).
        reason: refund reason code (case-insensitive); may be None.
        role: requesting user's canonical role string; may be None.
        matrix: the resolved E2 matrix policy value.

    Returns:
        Integer tier in ``[0, matrix["max_tier"]]``. 0 == no approval needed.
    """
    raise NotImplementedError  # implemented incrementally after skeleton commit
