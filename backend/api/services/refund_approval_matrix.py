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

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# A refund needing tier 0 needs no approval at all.
NO_APPROVAL_TIER = 0

# E2 policy keys (registered in policy_registry).
MATRIX_KEY = "refund.approval_matrix"
ENABLED_KEY = "refund.matrix_enabled"

# E4 action_type for a matrix-gated refund approval. Distinct from the existing
# "refund" action so a refund-matrix token cannot be cross-consumed by another
# code path (and vice-versa). Registered in approvals.ACTION_TYPES.
ACTION_TYPE = "REFUND_APPROVAL_MATRIX"

# Map an integer matrix tier to the E4 string tier (approvals._TIER_ROLES).
# tier 0 -> no approval. tier 1 -> any manager ("auto"). tier 2 -> ADMIN+
# ("admin"). tier 3 -> SUPERADMIN only ("super"). Higher ints clamp to "super".
_INT_TO_E4_TIER: Dict[int, str] = {1: "auto", 2: "admin", 3: "super"}


def int_to_e4_tier(tier: int) -> Optional[str]:
    """Translate a matrix integer tier to the E4 ``required_tier`` string, or
    None when no approval is required (tier 0). Any tier >= 3 -> 'super'."""
    if tier <= NO_APPROVAL_TIER:
        return None
    if tier in _INT_TO_E4_TIER:
        return _INT_TO_E4_TIER[tier]
    return "super"  # clamp anything above the top defined tier to SUPERADMIN


# The seeded default matrix. Mirrors the E4 amount tiers + the SYSTEM_INTENT
# "control over convenience" posture: a plain cashier needs approval for any
# non-trivial refund; managers are auto-cleared up to a higher floor; risky
# reasons (goodwill / price-match) escalate one tier sooner.
DEFAULT_MATRIX: Dict[str, Any] = {
    "currency": "INR",
    "role_floor_paise": {
        "SALES_CASHIER": 0,         # any refund needs approval per the bands
        "CASHIER": 0,
        "SALES_STAFF": 0,
        "STORE_MANAGER": 500000,    # auto-cleared up to Rs 5,000
        "AREA_MANAGER": 2000000,    # auto-cleared up to Rs 20,000
        "ADMIN": None,              # HQ -- never needs a refund approval
        "SUPERADMIN": None,
    },
    "bands": [
        {"max_paise": 100000, "tier": 0},    # <= Rs 1,000  -> none
        {"max_paise": 500000, "tier": 1},    # <= Rs 5,000  -> tier 1 (manager)
        {"max_paise": 2000000, "tier": 2},   # <= Rs 20,000 -> tier 2 (admin)
        {"max_paise": None, "tier": 3},      # above        -> tier 3 (super)
    ],
    "reason_bump": {
        "DEFECTIVE": 0,
        "CHANGE_OF_MIND": 0,
        "PRICE_MATCH": 1,
        "GOODWILL": 1,
    },
    "max_tier": 3,
}


def _max_tier(matrix: Dict[str, Any]) -> int:
    try:
        return int(matrix.get("max_tier", 3))
    except (TypeError, ValueError):
        return 3


def _band_tier(amount_paise: int, bands: List[Dict[str, Any]]) -> int:
    """First ascending band whose max_paise >= amount (None == open-ended)."""
    for band in bands or []:
        if not isinstance(band, dict):
            continue
        cap = band.get("max_paise")
        try:
            tier = int(band.get("tier", 0))
        except (TypeError, ValueError):
            tier = 0
        if cap is None:
            return tier
        try:
            if amount_paise <= int(cap):
                return tier
        except (TypeError, ValueError):
            continue
    return 0  # no band matched (e.g. empty bands) -> no approval


def required_tier(
    amount_paise: int,
    reason: Optional[str],
    role: Optional[str],
    matrix: Dict[str, Any],
) -> int:
    """Return the matrix approval tier required for a refund, or 0 for none.

    Pure: no DB, no I/O. See module docstring for the ``matrix`` shape.

    Resolution:
      1. Per-role floor: if the role has a floor and amount <= floor (or the
         floor is None == HQ), NO approval is required -- return 0 regardless of
         band/reason. A higher-trust role is auto-cleared up to its floor.
      2. Otherwise pick the ascending amount BAND tier, add the per-reason bump,
         and clamp to ``[0, max_tier]``.

    Args:
        amount_paise: refund amount in integer paise (>= 0).
        reason: refund reason code (case-insensitive); may be None.
        role: requesting user's canonical role string; may be None.
        matrix: the resolved E2 matrix policy value.

    Returns:
        Integer tier in ``[0, matrix["max_tier"]]``. 0 == no approval needed.
    """
    if not isinstance(matrix, dict):
        matrix = DEFAULT_MATRIX
    try:
        amt = int(amount_paise or 0)
    except (TypeError, ValueError):
        amt = 0
    if amt < 0:
        amt = 0

    max_tier = _max_tier(matrix)

    # 1. Per-role floor (auto-clear). A None floor means "never needs approval".
    role_floor = matrix.get("role_floor_paise") or {}
    if role and role in role_floor:
        floor = role_floor.get(role)
        if floor is None:
            return NO_APPROVAL_TIER
        try:
            if amt <= int(floor):
                return NO_APPROVAL_TIER
        except (TypeError, ValueError):
            pass  # malformed floor -> fall through to the bands (fail-safe = gate)

    # 2. Band tier + reason bump, clamped.
    base = _band_tier(amt, matrix.get("bands") or [])
    bump = 0
    if reason:
        reason_bump = matrix.get("reason_bump") or {}
        try:
            bump = int(reason_bump.get(str(reason).strip().upper(), 0))
        except (TypeError, ValueError):
            bump = 0
    tier = base + bump
    if tier < NO_APPROVAL_TIER:
        tier = NO_APPROVAL_TIER
    if tier > max_tier:
        tier = max_tier
    return tier


# ---------------------------------------------------------------------------
# DB-side resolver (the router seam). Pure required_tier stays DB-free above.
# ---------------------------------------------------------------------------


def matrix_enabled(scope: Optional[dict] = None) -> bool:
    """Is the F27 gate ON for this scope? DARK by default (flag default False).
    Fail-soft -> False (no gate) when E2 is unavailable."""
    try:
        from api.services.policy_engine import get_policy

        return bool(get_policy(ENABLED_KEY, scope or None, default=False))
    except Exception:  # noqa: BLE001 - fail-soft: a policy read error never gates
        logger.debug("[F27] matrix_enabled read failed; treating as dark", exc_info=True)
        return False


def resolve_matrix(scope: Optional[dict] = None) -> Dict[str, Any]:
    """Resolve the effective matrix from E2 (store > entity > global > default).
    Fail-soft -> DEFAULT_MATRIX."""
    try:
        from api.services.policy_engine import get_policy

        val = get_policy(MATRIX_KEY, scope or None, default=DEFAULT_MATRIX)
        return val if isinstance(val, dict) and val else DEFAULT_MATRIX
    except Exception:  # noqa: BLE001
        logger.debug("[F27] resolve_matrix read failed; using default", exc_info=True)
        return DEFAULT_MATRIX


def required_tier_for_refund(
    amount_paise: int,
    reason: Optional[str],
    role: Optional[str],
    *,
    store_id: Optional[str] = None,
    entity_id: Optional[str] = None,
) -> Optional[str]:
    """End-to-end resolver used by the refund path. Reads the flag + matrix from
    E2 for the (store/entity) scope and returns the E4 ``required_tier`` string
    ('auto'/'admin'/'super') a refund needs, or None when NO approval is required
    (gate dark, or below the floor). Never raises -- a resolution error fails
    OPEN (None == no gate) so a transient E2 outage can never wedge the till."""
    scope: Dict[str, Any] = {}
    if store_id:
        scope["store_id"] = store_id
    if entity_id:
        scope["entity_id"] = entity_id
    if not matrix_enabled(scope or None):
        return None  # DARK -- behaves exactly as today
    matrix = resolve_matrix(scope or None)
    try:
        tier_int = required_tier(amount_paise, reason, role, matrix)
    except Exception:  # noqa: BLE001 - defensive; pure fn shouldn't raise
        logger.warning("[F27] required_tier raised; failing open (no gate)", exc_info=True)
        return None
    return int_to_e4_tier(tier_int)
