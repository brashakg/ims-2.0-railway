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
    # F27 refund approval matrix. DARK by default (matrix_enabled=False) so a
    # fresh deploy behaves exactly as today -- the refund path adds NO gate until
    # the owner enables it per scope (like Fcostfloor). The matrix itself is a
    # JSON doc keyed on amount bands x reason x role; see
    # api/services/refund_approval_matrix.py (DEFAULT_MATRIX) for the shape. When
    # ON, a refund whose resolved tier is >0 must carry a CONSUMED E4 approval
    # token bound to that refund before it is recorded; money math is unchanged.
    _spec(key="refund.matrix_enabled", type="bool", default=False,
          scopes=("global", "entity", "store"), write_roles=("SUPERADMIN", "ADMIN"),
          group="Refunds & Returns", label="Refund approval matrix enabled",
          help="Require a tiered approval (per the refund approval matrix) before "
               "a refund is recorded. OFF (default) keeps the refund path "
               "byte-identical to today; enable per scope to roll out."),
    _spec(key="refund.approval_matrix", type="json", default={},
          scopes=("global", "entity", "store"), write_roles=("SUPERADMIN", "ADMIN"),
          group="Refunds & Returns", label="Refund approval matrix",
          help="Tiered refund-approval rules keyed on amount bands (paisa), reason, "
               "and requesting role. Empty -> the code DEFAULT_MATRIX is used. "
               "Has no effect unless 'Refund approval matrix enabled' is on."),

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
    # Fcostfloor enable switch. Owner sign-off 2026-06-09: defaults ON
    # (global) -- the post-discount cost+pct floor enforces everywhere; a
    # store/entity override lets the orchestrator opt a store out (e.g.
    # patchy cost data). Owner rev 2 (same date): DISCOUNTED sales only --
    # a pure full-sticker sale is always allowed (~292 active SKUs sticker
    # below cost+10% ex-GST and must keep selling). Missing/zero product
    # cost always fails OPEN per line regardless of this flag (see
    # api/services/cost_floor.py).
    _spec(key="pricing.cost_floor_enabled", type="bool", default=True,
          scopes=("global", "entity", "store"),
          write_roles=("SUPERADMIN", "ADMIN"),
          group="Pricing & Promotions", label="Enforce sell-price cost floor",
          help="Block a DISCOUNTED sell line whose effective post-discount "
               "price falls below cost + the cost-floor percent. Full-sticker "
               "(undiscounted) sales and lines with no known cost are never "
               "blocked."),
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

    # --- Predictive purchasing (#7) ---
    # ORACLE enqueues a reorder draft-PO PROPOSAL (human-approved, never an
    # auto-sent PO) when a SKU's projected days-of-stock-remaining falls below
    # this horizon. Lowering it makes ORACLE wait until stock is closer to
    # running out; raising it surfaces reorders earlier. Store/entity override.
    _spec(key="predictive_purchasing.horizon_days", type="days", default=14,
          scopes=("global", "entity", "store"), write_roles=("SUPERADMIN", "ADMIN"),
          group="Operations", label="Predictive reorder horizon (days)",
          help="Surface a reorder suggestion when projected days-of-stock falls below this.",
          minimum=1, maximum=180),

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
    # E5 wiring: the tender-routed Receipt voucher next to the Sales day-JV.
    # DARK by default -- a fresh deploy keeps the Tally export byte-identical to
    # today. When ON, GET /finance/tally/tender-receipt-jv serves Receipt
    # vouchers whose legs come from the E5 tender->ledger engine (UPI/CARD to
    # bank ledgers, voucher/loyalty/credit to liability/receivable, unknown to
    # Suspense -- never Cash), and the sales-JV response ADDITIVELY advertises
    # it via an X-Tally-Tender-Receipt header (body untouched either way).
    _spec(key="tally.tender_receipt_voucher", type="bool", default=False,
          scopes=("global", "entity", "store"),
          write_roles=("SUPERADMIN", "ADMIN", "ACCOUNTANT"),
          group="Serial & Integrity", label="Tally tender Receipt voucher",
          help="Offer the E5 tender-routed Receipt voucher alongside the Tally "
               "sales JV (instruments book to their mapped bank/liability "
               "ledgers, never Cash). OFF keeps the export byte-identical."),

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
    # ON by default (unification step-9): mirrors the product-master spine to the
    # INTERNAL Mongo PIM catalog (catalog_products / catalog_variants) so the
    # canonical product and its PIM shadow stay in sync from creation. The Mongo
    # `products` spine is ALWAYS written (single-doc, atomic, source of truth)
    # regardless of this flag; the secondary mirror is best-effort + FAIL-SOFT
    # (a mirror error never fails the create). A live EXTERNAL (Postgres/BVI/
    # Shopify) write additionally requires NEXUS DISPATCH_MODE=live, which
    # defaults to `off`, so flipping this ON NEVER causes an external write on a
    # fresh deploy -- only the internal PIM shadow is written.
    _spec(key="pm.mirror_enabled", type="bool", default=True,
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

    # --- F41 Lapsed-patient reactivation (#41) -----------------------------
    # The lapse window (months with NEITHER a confirmed order NOR a prescription
    # exam) that marks a patient "lapsed", and the per-store cap on the
    # reactivation work-list. DARK feature: builds an in-app cohort/work-list for
    # staff to act on -- it queues NO outbound customer message (WhatsApp ban;
    # STATUS COMMS DIRECTIVE 2026-06-07 -- #41 reactivation-send is DEFERRED).
    _spec(key="reactivation.lapse_months", type="int", default=24,
          scopes=("global",), write_roles=("SUPERADMIN", "ADMIN"),
          group="Reactivation", label="Lapsed-patient window (months)",
          minimum=6, maximum=60,
          help="A patient with NO confirmed order AND no prescription exam in this "
               "many months is treated as clinically lapsed and surfaces on the "
               "reactivation work-list."),
    _spec(key="reactivation.cohort_size", type="int", default=50,
          scopes=("global",), write_roles=("SUPERADMIN", "ADMIN"),
          group="Reactivation", label="Reactivation work-list size per store",
          minimum=1, maximum=500,
          help="Maximum lapsed patients surfaced per store per day on the "
               "reactivation work-list (most-lapsed first)."),

    # --- F14 Non-adaptation / remake (#14) ---
    # The grace window (days from the original sale) inside which a non-adapt
    # REMAKE is free / discounted per the charge policy, and the in-window charge
    # mode + percent. A remake requested OUTSIDE the window is always chargeable
    # at the full original lens cost (the engine clamps; see non_adapt.py). These
    # govern only the charge DECISION recorded on the non-adapt record -- the
    # remake order itself, if created, still goes through the normal POS pricing /
    # payment path, so flipping these NEVER touches order money math.
    _spec(key="non_adapt.window_days", type="days", default=45,
          scopes=("global", "entity", "store"), write_roles=("SUPERADMIN", "ADMIN"),
          group="Clinical", label="Non-adapt remake window (days)",
          minimum=1, maximum=180,
          help="Days from the original sale within which a non-adaptation remake "
               "is free / discounted per the charge policy. Outside this window a "
               "remake is charged at the full original lens cost."),
    _spec(key="non_adapt.charge_policy", type="enum", default="FREE",
          scopes=("global", "entity", "store"), write_roles=("SUPERADMIN", "ADMIN"),
          group="Clinical", label="Non-adapt in-window charge mode",
          enum=("FREE", "PERCENT", "FULL"),
          help="How an in-window non-adapt remake is charged: FREE (0), PERCENT "
               "of the original cost, or FULL. Outside the window is always FULL."),
    _spec(key="non_adapt.charge_percent", type="percent", default=0.0,
          scopes=("global", "entity", "store"), write_roles=("SUPERADMIN", "ADMIN"),
          group="Clinical", label="Non-adapt in-window charge percent",
          minimum=0, maximum=100,
          help="When the in-window charge mode is PERCENT, the percent of the "
               "original lens cost charged for the remake (paise-exact, half-up)."),

    # --- Clinical -> Retail Handover (F50 / #50) ---
    # Off by default. The orchestrator flips this ON per-store for the 1-2 pilot
    # stores (DECISIONS: "pilot 1-2 stores"); a fresh DB keeps the feature dark
    # everywhere. When False, POST /clinical/tests/{id}/send-to-floor is 403'd.
    _spec(key="clinical.handover_enabled", type="bool", default=False,
          scopes=("global", "entity", "store"),
          write_roles=("SUPERADMIN", "ADMIN", "STORE_MANAGER"),
          group="Clinical", label="Clinical->retail handover enabled",
          help="Let optometrists send a completed Rx to the sales floor "
               "(in-app bell). Enable per store for the pilot."),

    # --- F23 Blind EOD cash tally & Z-Read (#23) ---
    # The variance tolerance band (absolute paisa): a counted-vs-expected gap
    # within this band closes BALANCED; beyond it is flagged OVERAGE/SHORTAGE.
    # Default 0 = exact-match required (any gap is flagged); raise it per store
    # to allow a small rounding band. The reopen-roles key controls WHICH roles
    # may release the transparent soft-lock on a closed Z-Read (the router also
    # gates this; the policy lets the owner widen/narrow it per scope).
    _spec(key="till.variance_tolerance_paisa", type="money_paisa", default=0,
          scopes=("global", "entity", "store"),
          write_roles=("SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"),
          group="Cash Register", label="Till variance tolerance",
          minimum=0,
          help="Absolute counted-vs-expected gap (paisa) within which an EOD "
               "cash tally is treated as BALANCED. 0 = exact match required."),
    _spec(key="till.reopen_roles", type="json",
          default=["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
          scopes=("global", "entity", "store"),
          write_roles=("SUPERADMIN", "ADMIN"),
          group="Cash Register", label="Till reopen roles",
          help="Roles permitted to reopen a soft-locked EOD Z-Read (with a "
               "mandatory reason; the reopen is audited)."),
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
