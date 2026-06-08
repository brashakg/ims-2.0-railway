"""
IMS 2.0 - E2 Policy Registry
============================
The CODE-VERSIONED catalog of tunable policy keys. This is the single source of
truth for what is configurable, its type/validation, its code default, its env
fallback, which scope levels may override it, and who may write it. A fresh DB
with zero `policy_settings` documents behaves EXACTLY as the codebase does today
because every key resolves to its registry default (or env fallback).

Adding a key here automatically surfaces a typed control in Settings (the FE
renders from GET /settings/policies/registry) with zero FE changes.

Money values are paisa-integers (50000 == Rs 500.00). Every default is sourced
from DECISIONS.md sec 2-3 locked answers; none are invented here.

No emoji in this file (Windows cp1252).
"""
from dataclasses import dataclass
from typing import Any, List, Optional

# Policy value types the FE knows how to render + the engine knows how to validate.
TYPES = {"money_paisa", "int", "float", "percent", "days", "enum", "bool", "json", "text"}


@dataclass(frozen=True)
class PolicySpec:
    key: str
    type: str
    default: Any
    scopes: tuple                      # subset of ("global","entity","store") that may override
    write_roles: tuple                 # roles allowed to write this key (fine-grained gate)
    group: str                         # FE grouping / tab bucket
    label: str
    help: str = ""
    env: Optional[str] = None          # env-var fallback name (read when no DB override)
    secret: bool = False               # value stored encrypted (per-value _encrypt_value)
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    enum: Optional[tuple] = None        # allowed values for type == "enum"
    # For pricing.category_caps.* : the code-constant key in pricing_caps that this
    # override may only LOWER (never raise). None for all other keys.
    lower_only_vs_category: Optional[str] = None


def _spec(**kw) -> PolicySpec:
    return PolicySpec(**kw)


# ---------------------------------------------------------------------------
# THE REGISTRY -- dotted-namespace keys (ENGINES.md sec 69)
# ---------------------------------------------------------------------------

_REGISTRY_LIST: List[PolicySpec] = [
    # --- Refunds & Returns (DECISIONS sec 6) -- paisa ---
    _spec(key="refund.tier.auto_below", type="money_paisa", default=50000,
          scopes=("global", "entity", "store"), write_roles=("SUPERADMIN", "ADMIN"),
          group="Refunds & Returns", label="Refund auto-approve below",
          help="Refunds strictly below this amount are auto-approved (no manager).",
          minimum=0),
    _spec(key="refund.tier.admin_above", type="money_paisa", default=200000,
          scopes=("global", "entity", "store"), write_roles=("SUPERADMIN", "ADMIN"),
          group="Refunds & Returns", label="Refund needs ADMIN above",
          help="Refunds at/above this amount require ADMIN approval.", minimum=0),
    _spec(key="refund.tier.super_above", type="money_paisa", default=1000000,
          scopes=("global", "entity", "store"), write_roles=("SUPERADMIN",),
          group="Refunds & Returns", label="Refund needs SUPERADMIN above",
          help="Refunds at/above this amount require SUPERADMIN approval.", minimum=0),

    # --- Approvals & Loyalty ---
    _spec(key="approval.pin_validity_min", type="int", default=60,
          scopes=("global",), write_roles=("SUPERADMIN",),
          group="Approvals & Loyalty", label="Approval PIN validity (minutes)",
          help="How long an approver PIN authorization stays valid.", minimum=1, maximum=480),
    _spec(key="loyalty.pool_max_members", type="int", default=7,
          scopes=("global", "entity"), write_roles=("SUPERADMIN", "ADMIN"),
          group="Approvals & Loyalty", label="Family wallet max members",
          help="Maximum members sharing a family loyalty pool.", minimum=1, maximum=20),
    _spec(key="loyalty.pool_redeem_requires_otp", type="bool", default=True,
          scopes=("global", "entity"), write_roles=("SUPERADMIN", "ADMIN"),
          group="Approvals & Loyalty", label="Family wallet redeem needs OTP",
          help="Require OTP to the primary mobile before a pool redemption."),

    # --- Cash & Variance (DECISIONS sec 8) -- paisa ---
    _spec(key="cash.variance.warn", type="money_paisa", default=0,
          scopes=("global", "entity", "store"),
          write_roles=("SUPERADMIN", "ADMIN", "STORE_MANAGER"),
          group="Cash & Variance", label="Cash variance warn threshold",
          help="EOD cash variance at/above this warns.", minimum=0),
    _spec(key="cash.variance.block", type="money_paisa", default=10000,
          scopes=("global", "entity", "store"),
          write_roles=("SUPERADMIN", "ADMIN", "STORE_MANAGER"),
          group="Cash & Variance", label="Cash variance block threshold",
          help="EOD cash variance at/above this soft-locks close until acknowledged.", minimum=0),
    _spec(key="cash.variance.frequency", type="enum", default="daily",
          scopes=("global", "entity", "store"),
          write_roles=("SUPERADMIN", "ADMIN", "STORE_MANAGER"),
          group="Cash & Variance", label="Cash variance check frequency",
          enum=("daily", "shift", "weekly")),

    # --- Pricing & Promotions ---
    _spec(key="pricing.cost_floor_pct", type="float", default=10.0,
          scopes=("global",), write_roles=("SUPERADMIN",),
          group="Pricing & Promotions", label="Cost floor % over cost",
          help="Minimum margin over cost on a priced sell line (consumer: orders sell-path, Phase 2).",
          minimum=0, maximum=100),
    _spec(key="promo.ceiling_pct", type="percent", default=30.0,
          scopes=("global", "entity", "store"), write_roles=("SUPERADMIN", "ADMIN"),
          group="Pricing & Promotions", label="Promo cart ceiling %",
          help="Maximum overall cart-level promotional discount.", minimum=0, maximum=100,
          env="PROMO_CEILING_PCT"),
    _spec(key="liquidation.floor_pct_over_cost", type="float", default=10.0,
          scopes=("global", "entity"), write_roles=("SUPERADMIN", "ADMIN"),
          group="Pricing & Promotions", label="Liquidation floor % over cost",
          help="Minimum margin over cost for ageing auto-liquidation.", minimum=0, maximum=100),
    # Per-category caps: E2 may only LOWER the code constant in pricing_caps, never raise.
    _spec(key="pricing.category_caps.MASS", type="percent", default=15.0,
          scopes=("global", "entity", "store"), write_roles=("SUPERADMIN", "ADMIN"),
          group="Pricing & Promotions", label="Discount cap - MASS",
          help="Store/entity may LOWER the MASS category cap (never above the code floor).",
          minimum=0, maximum=100, lower_only_vs_category="MASS"),
    _spec(key="pricing.category_caps.PREMIUM", type="percent", default=20.0,
          scopes=("global", "entity", "store"), write_roles=("SUPERADMIN", "ADMIN"),
          group="Pricing & Promotions", label="Discount cap - PREMIUM",
          help="Store/entity may LOWER the PREMIUM category cap (never above the code floor).",
          minimum=0, maximum=100, lower_only_vs_category="PREMIUM"),
    _spec(key="pricing.category_caps.LUXURY", type="percent", default=5.0,
          scopes=("global", "entity", "store"), write_roles=("SUPERADMIN", "ADMIN"),
          group="Pricing & Promotions", label="Discount cap - LUXURY",
          help="Store/entity may LOWER the LUXURY category cap (never above the code floor of 5%).",
          minimum=0, maximum=100, lower_only_vs_category="LUXURY"),
    _spec(key="pricing.category_caps.SERVICE", type="percent", default=10.0,
          scopes=("global", "entity", "store"), write_roles=("SUPERADMIN", "ADMIN"),
          group="Pricing & Promotions", label="Discount cap - SERVICE",
          help="Store/entity may LOWER the SERVICE category cap (never above the code floor).",
          minimum=0, maximum=100, lower_only_vs_category="SERVICE"),

    # --- Reminders ---
    _spec(key="reminder.rx_expiry_days", type="days", default=30,
          scopes=("global", "entity", "store"),
          write_roles=("SUPERADMIN", "ADMIN", "STORE_MANAGER"),
          group="Reminders", label="Rx expiry reminder lead (days)",
          help="Days before Rx expiry to remind the customer.", minimum=1, maximum=365),

    # --- Operations (#34 global target ticker) ---
    # Stored as scopes=("global",) so a future store-scope override can be added
    # without a code change. milestone_pcts is the list of MTD-vs-target
    # thresholds (%) ORACLE fires a one-time floor-staff bell at; refresh_seconds
    # is the Hub ticker card poll interval.
    _spec(key="ticker.milestone_pcts", type="json", default=[25, 50, 75, 100],
          scopes=("global",), write_roles=("SUPERADMIN", "ADMIN"),
          group="Operations", label="Target ticker milestone thresholds (%)",
          help="MTD-vs-monthly-target percentages that fire a one-time floor-staff bell."),
    _spec(key="ticker.refresh_seconds", type="int", default=60,
          scopes=("global",), write_roles=("SUPERADMIN", "ADMIN"),
          group="Operations", label="Target ticker refresh interval (seconds)",
          help="How often the Hub target-ticker card re-polls the server.",
          minimum=30, maximum=300),

    # --- Communications (DECISIONS sec 10) ---
    _spec(key="comms.cap_per_customer_30d", type="int", default=3,
          scopes=("global", "entity", "store"), write_roles=("SUPERADMIN", "ADMIN"),
          group="Communications", label="Max automated messages / customer / 30d",
          help="Frequency soft-ceiling for automated marketing messages.", minimum=0, maximum=30),

    # --- Ageing / AR ---
    _spec(key="ageing.ar_buckets", type="json", default=[30, 60, 90],
          scopes=("global", "entity"), write_roles=("SUPERADMIN", "ADMIN", "ACCOUNTANT"),
          group="Ageing & AR", label="AR ageing buckets (days)",
          help="Accounts-receivable ageing bucket edges."),
    _spec(key="ageing.overdue_days", type="days", default=90,
          scopes=("global", "entity"), write_roles=("SUPERADMIN", "ADMIN", "ACCOUNTANT"),
          group="Ageing & AR", label="Overdue after (days)", minimum=1, maximum=365),
    _spec(key="inventory.idle_threshold_days", type="days", default=90,
          scopes=("global", "entity"), write_roles=("SUPERADMIN", "ADMIN"),
          group="Ageing & AR", label="Idle stock threshold (days)", minimum=1, maximum=730),

    # --- Serial / integrity ---
    _spec(key="serial.return_mismatch_hard_block", type="bool", default=True,
          scopes=("global", "entity", "store"), write_roles=("SUPERADMIN", "ADMIN"),
          group="Serial & Integrity", label="Hard-block serial mismatch on return",
          help="Block a return when the serial does not match the sale."),
    _spec(key="tally.ledger_map", type="json", default={},
          scopes=("global", "entity"), write_roles=("SUPERADMIN", "ADMIN", "ACCOUNTANT"),
          group="Serial & Integrity", label="Tally ledger map", secret=True,
          help="Account-head mapping for the Tally export (stored encrypted)."),

    # --- Own-use allowances (DECISIONS sec 3) -- paisa ---
    _spec(key="own_use.allowance.staff", type="money_paisa", default=300000,
          scopes=("global", "entity"), write_roles=("SUPERADMIN", "ADMIN"),
          group="Own-use Allowances", label="Own-use allowance - staff", minimum=0),
    _spec(key="own_use.allowance.manager", type="money_paisa", default=800000,
          scopes=("global", "entity"), write_roles=("SUPERADMIN", "ADMIN"),
          group="Own-use Allowances", label="Own-use allowance - manager", minimum=0),
    _spec(key="own_use.allowance.admin", type="money_paisa", default=1500000,
          scopes=("global", "entity"), write_roles=("SUPERADMIN",),
          group="Own-use Allowances", label="Own-use allowance - admin", minimum=0),

    # --- Product Master (PM / N5) ---
    # OFF by default: gates the catalog/external (Postgres/BVI/Shopify) mirror of
    # the product-master triple-write. The Mongo `products` spine is ALWAYS
    # written (single-doc, atomic, source of truth) regardless of this flag; only
    # the secondary best-effort mirror is gated. A live EXTERNAL write additionally
    # requires NEXUS DISPATCH_MODE=live, so a fresh deploy NEVER mirrors externally.
    _spec(key="pm.mirror_enabled", type="bool", default=False,
          scopes=("global", "entity", "store"), write_roles=("SUPERADMIN", "ADMIN"),
          group="Product Master", label="Product-master mirror enabled",
          help="Mirror new/updated products to the PIM catalog (and, when DISPATCH_MODE=live, "
               "the external Postgres/Shopify catalog). Off by default; the Mongo spine is "
               "always written regardless.",
          env="PM_MIRROR_ENABLED"),

    # --- NBA (next best action) ---
    _spec(key="nba.cards_per_day", type="int", default=15,
          scopes=("global",), write_roles=("SUPERADMIN", "ADMIN"),
          group="NBA", label="NBA call cards per day", minimum=1, maximum=100),
    _spec(key="nba.vip_reserved_slots", type="int", default=2,
          scopes=("global",), write_roles=("SUPERADMIN", "ADMIN"),
          group="NBA", label="NBA VIP reserved slots", minimum=0, maximum=50),
]

REGISTRY = {s.key: s for s in _REGISTRY_LIST}

# Union of all write roles across the registry -- the coarse RBAC-table gate for
# the PUT/DELETE endpoints (the per-key write_roles is the fine-grained gate).
ALL_WRITE_ROLES = sorted({r for s in _REGISTRY_LIST for r in s.write_roles})


def spec_to_public(s: PolicySpec) -> dict:
    """Schema row the FE renders. Secrets never expose their value here (the value
    lives in the per-scope GET responses, masked)."""
    return {
        "key": s.key,
        "type": s.type,
        "default": None if s.secret else s.default,
        "scopes": list(s.scopes),
        "write_roles": list(s.write_roles),
        "group": s.group,
        "label": s.label,
        "help": s.help,
        "secret": s.secret,
        "minimum": s.minimum,
        "maximum": s.maximum,
        "enum": list(s.enum) if s.enum else None,
    }


def registry_public() -> List[dict]:
    return [spec_to_public(s) for s in _REGISTRY_LIST]
