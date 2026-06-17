"""
IMS 2.0 - Per-role capability DELTAS (the preset editor's single source)
========================================================================

DEVELOPER-OWNED, versioned table that drives BOTH the frontend per-user override
editor and the backend conflict validator (council ruling sec.2). For each role
it declares:

  * ``defaults``       -- the role's BASELINE capability set (informational; the
                          real baseline is still rbac_policy.check_access -- this
                          mirrors it for the FE so a toggle starts in the right
                          on/off position).
  * ``commonOverrides``-- the curated ~5-8 toggles an owner actually reaches for,
                          each a PLAIN-ENGLISH SENTENCE (never a raw key shown to
                          the owner). Shape per row:
                              key            -- the capability key written on the
                                                user (grant or deny). Hidden
                                                metadata; the owner never sees it.
                              label          -- the sentence the owner reads.
                              type           -- "toggle" | "number"
                              default        -- the role baseline for this toggle
                                                (true = role already has it; a
                                                toggle OFF writes a DENY, ON of a
                                                missing one writes a GRANT).
                              hard_floor_note -- when set, the owner-facing reason
                                                this is constrained by an
                                                inviolable business floor (shown
                                                grayed). None = freely toggleable.

ONE RULE (ruling sec.2, judge's protected note): a "preset" is a CLIENT-SIDE
template that EXPANDS to capability keys written on the user. The backend NEVER
stores or resolves a preset reference -- only the concrete grant/deny capability
keys land on the user doc. This table is the expansion source, not a stored ref.

FAIL-CLOSED: a capability with NO delta row simply does not appear as a toggle
(it defaults to the role baseline). A delta row whose key is not a real
capability is dropped by the totality test -- a toggle can never exist before its
route. ``DELTA_SCHEMA_VERSION`` bumps when the curated set changes so an audit
row records which delta vintage produced a given override.

The discount field is special: it is NOT a capability key. It edits the existing
``discount_cap`` user field (clamped by the escalation guard at write time).
"""

from __future__ import annotations

from typing import Dict, List

DELTA_SCHEMA_VERSION = 1

# Sentinel "key" for the discount-cap number field (NOT a capability; edits the
# user.discount_cap field, escalation-guarded at write).
DISCOUNT_CAP_FIELD = "__discount_cap__"


# Per-role curated delta rows. Seeded for the three roles the ruling names
# (SALES_CASHIER, OPTOMETRIST, STORE_MANAGER) plus the obvious neighbours; other
# roles fall back to "Reset to standard role" + the discount field only. Each
# label is a sentence; each key is a real capability (asserted by the totality
# test). hard_floor_note marks toggles whose EFFECT is further constrained by an
# inviolable DATA-layer floor (discount caps / GST / Rx ranges / geo) that no
# grant can lift -- shown grayed-with-reason in the editor.
ROLE_DELTAS: Dict[str, Dict[str, object]] = {
    "SALES_CASHIER": {
        "defaults": [
            "orders:read",
            "orders:write",
            "customers:read",
            "customers:write",
            "till:read",
            "till:write",
            "returns:read",
        ],
        "commonOverrides": [
            {
                "key": "returns:write",
                "label": "Allow processing customer returns and refunds",
                "type": "toggle",
                "default": False,
                "hard_floor_note": None,
            },
            {
                "key": "orders:write",
                "label": "Allow creating and editing sales orders (POS)",
                "type": "toggle",
                "default": True,
                "hard_floor_note": (
                    "Discount limits, MRP/offer rules and GST are always enforced "
                    "on every sale regardless of this setting."
                ),
            },
            {
                "key": "reports:read",
                "label": "Allow viewing store reports",
                "type": "toggle",
                "default": False,
                "hard_floor_note": None,
            },
            {
                "key": "customers:write",
                "label": "Allow adding and editing customer records",
                "type": "toggle",
                "default": True,
                "hard_floor_note": None,
            },
            {
                "key": "prescriptions:read",
                "label": "Allow viewing customer prescriptions",
                "type": "toggle",
                "default": True,
                "hard_floor_note": None,
            },
            {
                "key": DISCOUNT_CAP_FIELD,
                "label": "Maximum discount this person can give",
                "type": "number",
                "default": 10,
                "hard_floor_note": (
                    "Even with a higher number here, category and luxury-brand "
                    "caps still limit the actual discount on each item."
                ),
            },
        ],
    },
    "SALES_STAFF": {
        "defaults": [
            "orders:read",
            "orders:write",
            "customers:read",
            "customers:write",
        ],
        "commonOverrides": [
            {
                "key": "orders:write",
                "label": "Allow creating and editing sales orders (POS)",
                "type": "toggle",
                "default": True,
                "hard_floor_note": (
                    "Discount limits, MRP/offer rules and GST are always enforced."
                ),
            },
            {
                "key": "returns:write",
                "label": "Allow processing customer returns and refunds",
                "type": "toggle",
                "default": False,
                "hard_floor_note": None,
            },
            {
                "key": "reports:read",
                "label": "Allow viewing store reports",
                "type": "toggle",
                "default": False,
                "hard_floor_note": None,
            },
            {
                "key": DISCOUNT_CAP_FIELD,
                "label": "Maximum discount this person can give",
                "type": "number",
                "default": 10,
                "hard_floor_note": (
                    "Category and luxury-brand caps still apply on each item."
                ),
            },
        ],
    },
    "OPTOMETRIST": {
        "defaults": [
            "clinical:read",
            "clinical:write",
            "prescriptions:read",
            "prescriptions:write",
            "customers:read",
        ],
        "commonOverrides": [
            {
                "key": "prescriptions:write",
                "label": "Allow writing and editing prescriptions",
                "type": "toggle",
                "default": True,
                "hard_floor_note": (
                    "Prescription power ranges (SPH/CYL/AXIS/ADD) are always "
                    "validated regardless of this setting."
                ),
            },
            {
                "key": "clinical:write",
                "label": "Allow managing the eye-test queue and exams",
                "type": "toggle",
                "default": True,
                "hard_floor_note": None,
            },
            {
                "key": "orders:write",
                "label": "Allow creating sales orders (sell at the chair)",
                "type": "toggle",
                "default": False,
                "hard_floor_note": (
                    "Discount and pricing rules are always enforced on any sale."
                ),
            },
            {
                "key": "customers:write",
                "label": "Allow adding and editing customer records",
                "type": "toggle",
                "default": False,
                "hard_floor_note": None,
            },
        ],
    },
    "STORE_MANAGER": {
        "defaults": [
            "orders:read",
            "orders:write",
            "customers:read",
            "customers:write",
            "inventory:read",
            "inventory:write",
            "till:read",
            "till:write",
            "returns:read",
            "returns:write",
            "reports:read",
            "hr:read",
        ],
        "commonOverrides": [
            {
                "key": "reports:read",
                "label": "Allow viewing store reports",
                "type": "toggle",
                "default": True,
                "hard_floor_note": None,
            },
            {
                "key": "expenses:write",
                "label": "Allow recording and approving store expenses",
                "type": "toggle",
                "default": False,
                "hard_floor_note": (
                    "A locked accounting period still blocks expense changes."
                ),
            },
            {
                "key": "inventory:write",
                "label": "Allow adjusting stock and accepting transfers",
                "type": "toggle",
                "default": True,
                "hard_floor_note": None,
            },
            {
                "key": "returns:write",
                "label": "Allow processing customer returns and refunds",
                "type": "toggle",
                "default": True,
                "hard_floor_note": None,
            },
            {
                "key": "hr:read",
                "label": "Allow viewing staff attendance and HR records",
                "type": "toggle",
                "default": True,
                "hard_floor_note": None,
            },
            {
                "key": "vendors:read",
                "label": "Allow viewing vendor and purchase records",
                "type": "toggle",
                "default": False,
                "hard_floor_note": None,
            },
            {
                "key": DISCOUNT_CAP_FIELD,
                "label": "Maximum discount this person can give",
                "type": "number",
                "default": 20,
                "hard_floor_note": (
                    "Category and luxury-brand caps still apply on each item."
                ),
            },
        ],
    },
    "AREA_MANAGER": {
        "defaults": [
            "orders:read",
            "reports:read",
            "inventory:read",
            "inventory:write",
            "transfers:read",
            "transfers:write",
            "hr:read",
        ],
        "commonOverrides": [
            {
                "key": "reports:read",
                "label": "Allow viewing reports across stores",
                "type": "toggle",
                "default": True,
                "hard_floor_note": None,
            },
            {
                "key": "transfers:write",
                "label": "Allow approving stock transfers between stores",
                "type": "toggle",
                "default": True,
                "hard_floor_note": None,
            },
            {
                "key": "finance:read",
                "label": "Allow viewing finance dashboards",
                "type": "toggle",
                "default": False,
                "hard_floor_note": None,
            },
            {
                "key": DISCOUNT_CAP_FIELD,
                "label": "Maximum discount this person can give",
                "type": "number",
                "default": 25,
                "hard_floor_note": (
                    "Category and luxury-brand caps still apply on each item."
                ),
            },
        ],
    },
    "ACCOUNTANT": {
        "defaults": [
            "finance:read",
            "finance:write",
            "reports:read",
            "expenses:read",
            "expenses:write",
            "vendors:read",
        ],
        "commonOverrides": [
            {
                "key": "finance:write",
                "label": "Allow posting and editing finance entries",
                "type": "toggle",
                "default": True,
                "hard_floor_note": (
                    "A locked accounting period still blocks edits to that period."
                ),
            },
            {
                "key": "expenses:write",
                "label": "Allow recording and approving expenses",
                "type": "toggle",
                "default": True,
                "hard_floor_note": None,
            },
            {
                "key": "vendors:write",
                "label": "Allow recording vendor bills and payments",
                "type": "toggle",
                "default": False,
                "hard_floor_note": None,
            },
            {
                "key": "reports:read",
                "label": "Allow viewing reports",
                "type": "toggle",
                "default": True,
                "hard_floor_note": None,
            },
        ],
    },
    "CATALOG_MANAGER": {
        "defaults": [
            "catalog:read",
            "catalog:write",
            "products:read",
            "products:write",
            "inventory:read",
        ],
        "commonOverrides": [
            {
                "key": "catalog:write",
                "label": "Allow adding and editing catalog products",
                "type": "toggle",
                "default": True,
                "hard_floor_note": (
                    "MRP must always be at least the offer price; pricing caps "
                    "are always enforced."
                ),
            },
            {
                "key": "inventory:write",
                "label": "Allow adjusting stock levels",
                "type": "toggle",
                "default": False,
                "hard_floor_note": None,
            },
            {
                "key": "reports:read",
                "label": "Allow viewing reports",
                "type": "toggle",
                "default": False,
                "hard_floor_note": None,
            },
        ],
    },
    "WORKSHOP_STAFF": {
        "defaults": ["workshop:read", "workshop:write", "repairs:read"],
        "commonOverrides": [
            {
                "key": "workshop:write",
                "label": "Allow updating workshop job status and QC",
                "type": "toggle",
                "default": True,
                "hard_floor_note": None,
            },
            {
                "key": "inventory:read",
                "label": "Allow viewing stock availability",
                "type": "toggle",
                "default": False,
                "hard_floor_note": None,
            },
        ],
    },
    "CASHIER": {
        "defaults": ["till:read", "till:write"],
        "commonOverrides": [
            {
                "key": "till:write",
                "label": "Allow recording payments and till operations",
                "type": "toggle",
                "default": True,
                "hard_floor_note": None,
            },
            {
                "key": "returns:write",
                "label": "Allow processing customer returns and refunds",
                "type": "toggle",
                "default": False,
                "hard_floor_note": None,
            },
        ],
    },
}


def _delta_keys() -> set:
    """Every capability key referenced by any commonOverrides row (excluding the
    discount-cap sentinel). Used by the totality test to assert each is real."""
    keys = set()
    for role_cfg in ROLE_DELTAS.values():
        for row in role_cfg.get("commonOverrides", []):  # type: ignore[union-attr]
            k = row.get("key")
            if k and k != DISCOUNT_CAP_FIELD:
                keys.add(k)
    return keys


def deltas_for_role(role: str) -> List[dict]:
    """The curated commonOverrides rows for a role (empty list if none). The FE
    asks per the user's HIGHEST role; unknown role -> just the discount field is
    surfaced by the FE fallback."""
    cfg = ROLE_DELTAS.get(role)
    if not cfg:
        return []
    return list(cfg.get("commonOverrides", []))  # type: ignore[arg-type]


def role_default_capabilities(role: str) -> List[str]:
    """The mirrored baseline capability list for a role (informational)."""
    cfg = ROLE_DELTAS.get(role)
    if not cfg:
        return []
    return list(cfg.get("defaults", []))  # type: ignore[arg-type]
