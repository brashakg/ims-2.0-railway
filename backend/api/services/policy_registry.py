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

# N8: the survival-view seed list lives with its pure service (a stdlib-only
# module, so this import is feather-weight and cycle-proof). Importing the
# constant -- instead of copying it -- means the registry default and the
# service fallback can never drift.
from .survival_cashflow import ESSENTIAL_DEFAULT_HEADS as _SURVIVAL_DEFAULT_HEADS

# Policy value types the FE knows how to render + the engine knows how to validate.
TYPES = {
    "money_paisa",
    "int",
    "float",
    "percent",
    "days",
    "enum",
    "bool",
    "json",
    "text",
}


@dataclass(frozen=True)
class PolicySpec:
    key: str
    type: str
    default: Any
    scopes: tuple  # subset of ("global","entity","store") that may override
    write_roles: tuple  # roles allowed to write this key (fine-grained gate)
    group: str  # FE grouping / tab bucket
    label: str
    help: str = ""
    env: Optional[str] = None  # env-var fallback name (read when no DB override)
    secret: bool = False  # value stored encrypted (per-value _encrypt_value)
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    enum: Optional[tuple] = None  # allowed values for type == "enum"
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
    _spec(
        key="refund.tier.auto_below",
        type="money_paisa",
        default=50000,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Refunds & Returns",
        label="Refund auto-approve below",
        help="Refunds strictly below this amount are auto-approved (no manager).",
        minimum=0,
    ),
    _spec(
        key="refund.tier.admin_above",
        type="money_paisa",
        default=200000,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Refunds & Returns",
        label="Refund needs ADMIN above",
        help="Refunds at/above this amount require ADMIN approval.",
        minimum=0,
    ),
    _spec(
        key="refund.tier.super_above",
        type="money_paisa",
        default=1000000,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN",),
        group="Refunds & Returns",
        label="Refund needs SUPERADMIN above",
        help="Refunds at/above this amount require SUPERADMIN approval.",
        minimum=0,
    ),
    # F27 refund approval matrix. DARK by default (matrix_enabled=False) so a
    # fresh deploy behaves exactly as today -- the refund path adds NO gate until
    # the owner enables it per scope (like Fcostfloor). The matrix itself is a
    # JSON doc keyed on amount bands x reason x role; see
    # api/services/refund_approval_matrix.py (DEFAULT_MATRIX) for the shape. When
    # ON, a refund whose resolved tier is >0 must carry a CONSUMED E4 approval
    # token bound to that refund before it is recorded; money math is unchanged.
    _spec(
        key="refund.matrix_enabled",
        type="bool",
        default=False,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Refunds & Returns",
        label="Refund approval matrix enabled",
        help="Require a tiered approval (per the refund approval matrix) before "
        "a refund is recorded. OFF (default) keeps the refund path "
        "byte-identical to today; enable per scope to roll out.",
    ),
    _spec(
        key="refund.approval_matrix",
        type="json",
        default={},
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Refunds & Returns",
        label="Refund approval matrix",
        help="Tiered refund-approval rules keyed on amount bands (paisa), reason, "
        "and requesting role. Empty -> the code DEFAULT_MATRIX is used. "
        "Has no effect unless 'Refund approval matrix enabled' is on.",
    ),
    # F27: refunds always go back to the ORIGINAL tender (DECISIONS sec 6). The
    # refund path already DEFAULTS a refund to the original payment method; this
    # flag (default True) HARD-LOCKS it -- a refund_method that differs from the
    # order's original tender is rejected (422 TENDER_MISMATCH) so a cashier
    # cannot reroute a card sale to a cash refund. Turn OFF only to permit an
    # explicit tender override (every override is audit-logged on the return).
    _spec(
        key="refund.original_tender_enforce",
        type="bool",
        default=True,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Refunds & Returns",
        label="Refund to original tender (hard-lock)",
        help="Force every refund back to the order's original payment method "
        "(card -> card, UPI -> UPI, cash -> cash). When ON (default) a refund "
        "method that differs from the original tender is blocked. Turn OFF to "
        "allow an explicit, audit-logged tender override.",
    ),
    # --- Approvals & Loyalty ---
    _spec(
        key="approval.pin_validity_min",
        type="int",
        default=60,
        scopes=("global",),
        write_roles=("SUPERADMIN",),
        group="Approvals & Loyalty",
        label="Approval PIN validity (minutes)",
        help="How long an approver PIN authorization stays valid.",
        minimum=1,
        maximum=480,
    ),
    _spec(
        key="loyalty.pool_max_members",
        type="int",
        default=7,
        scopes=("global", "entity"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Approvals & Loyalty",
        label="Family wallet max members",
        help="Maximum members sharing a family loyalty pool.",
        minimum=1,
        maximum=20,
    ),
    _spec(
        key="loyalty.pool_redeem_requires_otp",
        type="bool",
        default=True,
        scopes=("global", "entity"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Approvals & Loyalty",
        label="Family wallet redeem needs OTP",
        help="Require OTP to the primary mobile before a pool redemption.",
    ),
    # --- Endless Aisle (#38) -- inter-branch fulfillment of an OOS SKU ---
    _spec(
        key="endless_aisle.enabled",
        type="bool",
        default=False,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Endless Aisle",
        label="Endless aisle enabled",
        help="Allow a store to fulfill an out-of-stock SKU from another branch "
        "(manager-only, source-accept, company-borne shipping). Off by default; "
        "every endless-aisle route 403s while off. POS pricing is never affected.",
        env="ENDLESS_AISLE_ENABLED",
    ),
    _spec(
        key="endless_aisle.eligible_store_ids",
        type="json",
        default=[],
        scopes=("global", "entity"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Endless Aisle",
        label="Endless-aisle eligible source stores",
        help="Stores that may act as a fulfillment SOURCE. Empty list = ALL stores eligible (the default).",
    ),
    # --- Cash & Variance (DECISIONS sec 8) -- paisa ---
    _spec(
        key="cash.variance.warn",
        type="money_paisa",
        default=0,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN", "STORE_MANAGER"),
        group="Cash & Variance",
        label="Cash variance warn threshold",
        help="EOD cash variance at/above this warns.",
        minimum=0,
    ),
    _spec(
        key="cash.variance.block",
        type="money_paisa",
        default=10000,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN", "STORE_MANAGER"),
        group="Cash & Variance",
        label="Cash variance block threshold",
        help="EOD cash variance at/above this soft-locks close until acknowledged.",
        minimum=0,
    ),
    _spec(
        key="cash.variance.frequency",
        type="enum",
        default="daily",
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN", "STORE_MANAGER"),
        group="Cash & Variance",
        label="Cash variance check frequency",
        enum=("daily", "shift", "weekly"),
    ),
    # --- Pricing & Promotions ---
    _spec(
        key="pricing.cost_floor_pct",
        type="float",
        default=10.0,
        scopes=("global",),
        write_roles=("SUPERADMIN",),
        group="Pricing & Promotions",
        label="Cost floor % over cost",
        help="Minimum margin over cost on a priced sell line (consumer: orders sell-path, Phase 2).",
        minimum=0,
        maximum=100,
    ),
    # Fcostfloor enable switch. Owner sign-off 2026-06-09: defaults ON
    # (global) -- the post-discount cost+pct floor enforces everywhere; a
    # store/entity override lets the orchestrator opt a store out (e.g.
    # patchy cost data). Owner rev 2 (same date): DISCOUNTED sales only --
    # a pure full-sticker sale is always allowed (~292 active SKUs sticker
    # below cost+10% ex-GST and must keep selling). Missing/zero product
    # cost always fails OPEN per line regardless of this flag (see
    # api/services/cost_floor.py).
    _spec(
        key="pricing.cost_floor_enabled",
        type="bool",
        default=True,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Pricing & Promotions",
        label="Enforce sell-price cost floor",
        help="Block a DISCOUNTED sell line whose effective post-discount "
        "price falls below cost + the cost-floor percent. Full-sticker "
        "(undiscounted) sales and lines with no known cost are never "
        "blocked.",
    ),
    _spec(
        key="promo.ceiling_pct",
        type="percent",
        default=30.0,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Pricing & Promotions",
        label="Promo cart ceiling %",
        help="Maximum overall cart-level promotional discount.",
        minimum=0,
        maximum=100,
        env="PROMO_CEILING_PCT",
    ),
    _spec(
        key="liquidation.floor_pct_over_cost",
        type="float",
        default=10.0,
        scopes=("global", "entity"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Pricing & Promotions",
        label="Liquidation floor % over cost",
        help="Minimum margin over cost for ageing auto-liquidation.",
        minimum=0,
        maximum=100,
    ),
    # Per-category caps: E2 may only LOWER the code constant in pricing_caps, never raise.
    _spec(
        key="pricing.category_caps.MASS",
        type="percent",
        default=15.0,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Pricing & Promotions",
        label="Discount cap - MASS",
        help="Store/entity may LOWER the MASS category cap (never above the code floor).",
        minimum=0,
        maximum=100,
        lower_only_vs_category="MASS",
    ),
    _spec(
        key="pricing.category_caps.PREMIUM",
        type="percent",
        default=20.0,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Pricing & Promotions",
        label="Discount cap - PREMIUM",
        help="Store/entity may LOWER the PREMIUM category cap (never above the code floor).",
        minimum=0,
        maximum=100,
        lower_only_vs_category="PREMIUM",
    ),
    _spec(
        key="pricing.category_caps.LUXURY",
        type="percent",
        default=5.0,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Pricing & Promotions",
        label="Discount cap - LUXURY",
        help="Store/entity may LOWER the LUXURY category cap (never above the code floor of 5%).",
        minimum=0,
        maximum=100,
        lower_only_vs_category="LUXURY",
    ),
    _spec(
        key="pricing.category_caps.SERVICE",
        type="percent",
        default=10.0,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Pricing & Promotions",
        label="Discount cap - SERVICE",
        help="Store/entity may LOWER the SERVICE category cap (never above the code floor).",
        minimum=0,
        maximum=100,
        lower_only_vs_category="SERVICE",
    ),
    # --- Reminders ---
    _spec(
        key="reminder.rx_expiry_days",
        type="days",
        default=30,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN", "STORE_MANAGER"),
        group="Reminders",
        label="Rx expiry reminder lead (days)",
        help="Days before Rx expiry to remind the customer.",
        minimum=1,
        maximum=365,
    ),
    # --- Operations (#34 global target ticker) ---
    # Stored as scopes=("global",) so a future store-scope override can be added
    # without a code change. milestone_pcts is the list of MTD-vs-target
    # thresholds (%) ORACLE fires a one-time floor-staff bell at; refresh_seconds
    # is the Hub ticker card poll interval.
    _spec(
        key="ticker.milestone_pcts",
        type="json",
        default=[25, 50, 75, 100],
        scopes=("global",),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Operations",
        label="Target ticker milestone thresholds (%)",
        help="MTD-vs-monthly-target percentages that fire a one-time floor-staff bell.",
    ),
    _spec(
        key="ticker.refresh_seconds",
        type="int",
        default=60,
        scopes=("global",),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Operations",
        label="Target ticker refresh interval (seconds)",
        help="How often the Hub target-ticker card re-polls the server.",
        minimum=30,
        maximum=300,
    ),
    # --- Predictive purchasing (#7) ---
    # ORACLE enqueues a reorder draft-PO PROPOSAL (human-approved, never an
    # auto-sent PO) when a SKU's projected days-of-stock-remaining falls below
    # this horizon. Lowering it makes ORACLE wait until stock is closer to
    # running out; raising it surfaces reorders earlier. Store/entity override.
    _spec(
        key="predictive_purchasing.horizon_days",
        type="days",
        default=14,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Operations",
        label="Predictive reorder horizon (days)",
        help="Surface a reorder suggestion when projected days-of-stock falls below this.",
        minimum=1,
        maximum=180,
    ),
    # --- Communications (DECISIONS sec 10) ---
    # Coexistence double-answer guard (MSG91 build, 2026-08-30). Each shop's
    # WhatsApp number runs on the shop phone AND the API, so MEGAPHONE's
    # inbound auto-reply must not answer next to a human. DEFAULT off: staff
    # reply in the ordinary WhatsApp Business app, IMS stays quiet.
    # after_hours replies only outside the store's working_hours (else the
    # shared 21:00-09:00 IST quiet window). Enforced at the single auto-reply
    # send site: api.services.whatsapp_intents.auto_reply_allowed.
    _spec(
        key="msg.auto_reply_mode",
        type="enum",
        default="off",
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Communications",
        label="WhatsApp inbound auto-reply",
        enum=("off", "after_hours", "always"),
        env="MSG_AUTO_REPLY_MODE",
        help="When IMS answers an inbound WhatsApp automatically. off "
        "(default): never - staff on the shop phone reply themselves "
        "(Coexistence-safe). after_hours: only when the store is closed "
        "(store working hours, else 9 PM - 9 AM IST). always: every "
        "inbound gets the automated reply.",
    ),
    # Voice escalation rung (MSG91 channel expansions). When ON, a P1 SYSTEM
    # task (till variance, SLA breach) that passes its acknowledgement window
    # unacked gets ONE TTS voice call to the store manager (press 1 in the
    # IVR acknowledges via the MSG91 voice webhook). The escalation LADDER
    # stays the task engine's - this only adds a rung at the single alert
    # site (task_notify.notify_escalation -> voice_escalation). DEFAULT off;
    # and like everything else it is dark until DISPATCH_MODE arms (the
    # provider SIMULATES while off).
    _spec(
        key="msg.voice_escalation",
        type="enum",
        default="off",
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Communications",
        label="Voice call on unacked P1 system tasks",
        enum=("off", "on"),
        env="MSG_VOICE_ESCALATION",
        help="off (default): no calls. on: when a P1 SYSTEM task is still "
        "unacknowledged past its SLA window, place one automated voice call "
        "to the store manager; pressing 1 acknowledges the task. Nothing is "
        "called while DISPATCH_MODE is off.",
    ),
    _spec(
        key="comms.cap_per_customer_30d",
        type="int",
        default=3,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Communications",
        label="Max automated messages / customer / 30d",
        help="Frequency soft-ceiling for automated marketing messages.",
        minimum=0,
        maximum=30,
    ),
    # --- Ageing / AR ---
    _spec(
        key="ageing.ar_buckets",
        type="json",
        default=[30, 60, 90],
        scopes=("global", "entity"),
        write_roles=("SUPERADMIN", "ADMIN", "ACCOUNTANT"),
        group="Ageing & AR",
        label="AR ageing buckets (days)",
        help="Accounts-receivable ageing bucket edges.",
    ),
    _spec(
        key="ageing.overdue_days",
        type="days",
        default=90,
        scopes=("global", "entity"),
        write_roles=("SUPERADMIN", "ADMIN", "ACCOUNTANT"),
        group="Ageing & AR",
        label="Overdue after (days)",
        minimum=1,
        maximum=365,
    ),
    _spec(
        key="inventory.idle_threshold_days",
        type="days",
        default=90,
        scopes=("global", "entity"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Ageing & AR",
        label="Idle stock threshold (days)",
        minimum=1,
        maximum=730,
    ),
    # --- Serial / integrity ---
    # F6 per-unit serial tracking: which CATEGORIES (UPPER-cased) require a unique
    # serial captured per physical unit at stock-in (hearing aids, luxury frames,
    # watches). DARK by default: an empty list forces NO serial anywhere, so a
    # fresh deploy behaves exactly as today (non-serialized category is a no-op).
    # Owner enables per scope (e.g. ["HEARING_AID","LUXURY"]).
    _spec(
        key="inventory.serialized_categories",
        type="json",
        default=[],
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Serial & Integrity",
        label="Serialized categories (per-unit serial)",
        help="Categories whose units carry a unique serial captured at stock-in "
        "and tracked through sale to warranty/recall. Empty -> feature dark.",
    ),
    _spec(
        key="serial.return_mismatch_hard_block",
        type="bool",
        default=True,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Serial & Integrity",
        label="Hard-block serial mismatch on return",
        help="Block a return when the serial does not match the sale.",
    ),
    _spec(
        key="tally.ledger_map",
        type="json",
        default={},
        scopes=("global", "entity"),
        write_roles=("SUPERADMIN", "ADMIN", "ACCOUNTANT"),
        group="Serial & Integrity",
        label="Tally ledger map",
        secret=True,
        help="Account-head mapping for the Tally export (stored encrypted).",
    ),
    # E5 wiring: the tender-routed Receipt voucher next to the Sales day-JV.
    # DARK by default -- a fresh deploy keeps the Tally export byte-identical to
    # today. When ON, GET /finance/tally/tender-receipt-jv serves Receipt
    # vouchers whose legs come from the E5 tender->ledger engine (UPI/CARD to
    # bank ledgers, voucher/loyalty/credit to liability/receivable, unknown to
    # Suspense -- never Cash), and the sales-JV response ADDITIVELY advertises
    # it via an X-Tally-Tender-Receipt header (body untouched either way).
    _spec(
        key="tally.tender_receipt_voucher",
        type="bool",
        default=False,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN", "ACCOUNTANT"),
        group="Serial & Integrity",
        label="Tally tender Receipt voucher",
        help="Offer the E5 tender-routed Receipt voucher alongside the Tally "
        "sales JV (instruments book to their mapped bank/liability "
        "ledgers, never Cash). OFF keeps the export byte-identical.",
    ),
    # --- Own-use allowances (DECISIONS sec 3) -- paisa ---
    _spec(
        key="own_use.allowance.staff",
        type="money_paisa",
        default=300000,
        scopes=("global", "entity"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Own-use Allowances",
        label="Own-use allowance - staff",
        minimum=0,
    ),
    _spec(
        key="own_use.allowance.manager",
        type="money_paisa",
        default=800000,
        scopes=("global", "entity"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Own-use Allowances",
        label="Own-use allowance - manager",
        minimum=0,
    ),
    _spec(
        key="own_use.allowance.admin",
        type="money_paisa",
        default=1500000,
        scopes=("global", "entity"),
        write_roles=("SUPERADMIN",),
        group="Own-use Allowances",
        label="Own-use allowance - admin",
        minimum=0,
    ),
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
    _spec(
        key="pm.mirror_enabled",
        type="bool",
        default=True,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Product Master",
        label="Product-master mirror enabled",
        help="Mirror new/updated products to the PIM catalog (and, when DISPATCH_MODE=live, "
        "the external Postgres/Shopify catalog). Off by default; the Mongo spine is "
        "always written regardless.",
        env="PM_MIRROR_ENABLED",
    ),
    # Hub Phase 2 PO catalog gates. NOW ON by default: the Create-PO form ships a
    # spine-product picker (every line carries a real catalogued product_id), so
    # POs can no longer carry a fabricated/placeholder line. When ON, create_po
    # refuses a PO line whose product_id is not on the `products` spine (422
    # UNKNOWN_PRODUCT) and send_po refuses to SEND until every line is catalog-
    # complete except cost (400 PO_LINES_INCOMPLETE). Set to False (global/entity/
    # store scope, or PM_PO_CATALOG_GATE=) to fall back to the legacy free-text
    # flow. The GRN ghost-stock gate is ALWAYS on (independent of this flag) -- an
    # uncatalogued received line is held, never minted as ghost.
    _spec(
        key="pm.po_catalog_gate",
        type="bool",
        default=True,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Product Master",
        label="PO catalog gate (require catalogued products on POs)",
        help="When on (default), a purchase order can only be raised/sent against "
        "products already on the catalog spine -- the Create-PO form's product "
        "picker enforces this. Turn off only to fall back to the legacy free-text "
        "Create-PO form; receiving still holds uncatalogued lines for Catalog-now "
        "regardless.",
        env="PM_PO_CATALOG_GATE",
    ),
    # Hub Phase 5: Shopify push-locks (owner DECISION C). A JSON allow-block list
    # of brands / collection handles that may NEVER be pushed to the storefront --
    # enforced fail-CLOSED as the FIRST statement inside every push fn, BEFORE the
    # dark/live gate. Default {} = nothing locked (no behaviour change). Shape:
    # {"brands": ["cartier", ...], "collections": ["clearance", ...]} (matched
    # case-insensitively). SUPERADMIN/ADMIN edit only.
    _spec(
        key="ecom.shopify_push_locks",
        type="json",
        default={},
        scopes=("global", "entity"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="E-commerce",
        label="Shopify push-locks (brands/collections never pushed)",
        help="Brands and collection handles listed here can NEVER be pushed to "
        "Shopify -- blocked inside the push function before any other gate "
        "(fail-closed). Default empty. Locking a brand does NOT auto-unpublish "
        "already-live items; it surfaces them for one-click manual unpublish.",
    ),
    # --- NBA (next best action) ---
    _spec(
        key="nba.cards_per_day",
        type="int",
        default=15,
        scopes=("global",),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="NBA",
        label="NBA call cards per day",
        minimum=1,
        maximum=100,
    ),
    _spec(
        key="nba.vip_reserved_slots",
        type="int",
        default=2,
        scopes=("global",),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="NBA",
        label="NBA VIP reserved slots",
        minimum=0,
        maximum=50,
    ),
    # --- F41 Lapsed-patient reactivation (#41) -----------------------------
    # The lapse window (months with NEITHER a confirmed order NOR a prescription
    # exam) that marks a patient "lapsed", and the per-store cap on the
    # reactivation work-list. DARK feature: builds an in-app cohort/work-list for
    # staff to act on -- it queues NO outbound customer message (WhatsApp ban;
    # STATUS COMMS DIRECTIVE 2026-06-07 -- #41 reactivation-send is DEFERRED).
    _spec(
        key="reactivation.lapse_months",
        type="int",
        default=24,
        scopes=("global",),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Reactivation",
        label="Lapsed-patient window (months)",
        minimum=6,
        maximum=60,
        help="A patient with NO confirmed order AND no prescription exam in this "
        "many months is treated as clinically lapsed and surfaces on the "
        "reactivation work-list.",
    ),
    _spec(
        key="reactivation.cohort_size",
        type="int",
        default=50,
        scopes=("global",),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Reactivation",
        label="Reactivation work-list size per store",
        minimum=1,
        maximum=500,
        help="Maximum lapsed patients surfaced per store per day on the "
        "reactivation work-list (most-lapsed first).",
    ),
    # --- F14 Non-adaptation / remake (#14) ---
    # The grace window (days from the original sale) inside which a non-adapt
    # REMAKE is free / discounted per the charge policy, and the in-window charge
    # mode + percent. A remake requested OUTSIDE the window is always chargeable
    # at the full original lens cost (the engine clamps; see non_adapt.py). These
    # govern only the charge DECISION recorded on the non-adapt record -- the
    # remake order itself, if created, still goes through the normal POS pricing /
    # payment path, so flipping these NEVER touches order money math.
    _spec(
        key="non_adapt.window_days",
        type="days",
        default=45,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Clinical",
        label="Non-adapt remake window (days)",
        minimum=1,
        maximum=180,
        help="Days from the original sale within which a non-adaptation remake "
        "is free / discounted per the charge policy. Outside this window a "
        "remake is charged at the full original lens cost.",
    ),
    _spec(
        key="non_adapt.charge_policy",
        type="enum",
        default="FREE",
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Clinical",
        label="Non-adapt in-window charge mode",
        enum=("FREE", "PERCENT", "FULL"),
        help="How an in-window non-adapt remake is charged: FREE (0), PERCENT "
        "of the original cost, or FULL. Outside the window is always FULL.",
    ),
    _spec(
        key="non_adapt.charge_percent",
        type="percent",
        default=0.0,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Clinical",
        label="Non-adapt in-window charge percent",
        minimum=0,
        maximum=100,
        help="When the in-window charge mode is PERCENT, the percent of the "
        "original lens cost charged for the remake (paise-exact, half-up).",
    ),
    # --- Clinical -> Retail Handover (F50 / #50) ---
    # Off by default. The orchestrator flips this ON per-store for the 1-2 pilot
    # stores (DECISIONS: "pilot 1-2 stores"); a fresh DB keeps the feature dark
    # everywhere. When False, POST /clinical/tests/{id}/send-to-floor is 403'd.
    _spec(
        key="clinical.handover_enabled",
        type="bool",
        default=False,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN", "STORE_MANAGER"),
        group="Clinical",
        label="Clinical->retail handover enabled",
        help="Let optometrists send a completed Rx to the sales floor "
        "(in-app bell). Enable per store for the pilot.",
    ),
    # --- F23 Blind EOD cash tally & Z-Read (#23) ---
    # The variance tolerance band (absolute paisa): a counted-vs-expected gap
    # within this band closes BALANCED; beyond it is flagged OVERAGE/SHORTAGE,
    # a written explanation becomes mandatory to lock/close, and a task lands
    # on the store manager. Default Rs 100 (owner ruling 2026-08-25: ONE band,
    # Rs 100, everywhere) -- store-scopable here in Settings > Cash Register.
    # This is the ONLY band: the Finance close reads it too (the closer-typed
    # tolerance was deleted). The reopen-roles key controls WHICH roles may
    # release the transparent soft-lock on a closed Z-Read (the router also
    # gates this; the policy lets the owner widen/narrow it per scope).
    _spec(
        key="till.variance_tolerance_paisa",
        type="money_paisa",
        default=10000,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"),
        group="Cash Register",
        label="Till variance tolerance",
        minimum=0,
        help="Absolute counted-vs-expected gap (paisa) within which an EOD "
        "cash tally is treated as BALANCED. Beyond it the variance needs a "
        "written explanation and the store manager is alerted. Default "
        "10000 = Rs 100 (owner ruling 2026-08-25).",
    ),
    _spec(
        key="till.reopen_roles",
        type="json",
        default=["SUPERADMIN", "ADMIN", "AREA_MANAGER", "STORE_MANAGER"],
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Cash Register",
        label="Till reopen roles",
        help="Roles permitted to reopen a soft-locked EOD Z-Read (with a "
        "mandatory reason; the reopen is audited).",
    ),
    # --- N8 Owner "survival" cash-flow (essential vs deferrable + min-pay) ---
    # The finance router reads BOTH keys at GLOBAL scope (the survival view is
    # an org-wide owner figure), so they are declared global-only -- declaring
    # entity/store scopes the router never resolves would be a lie in the
    # Settings UI. Keyword matching is case/space-normalized contains-match;
    # keywords of <= 3 chars (pf/esi/gst) match whole words only.
    _spec(
        key="finance.survival_essential_heads",
        type="json",
        default=list(_SURVIVAL_DEFAULT_HEADS),
        scopes=("global",),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Finance",
        label="Survival view: essential expense heads",
        help="Expense heads counted as ESSENTIAL fixed costs in the owner "
        "survival cash-flow view (matched case-insensitively against "
        "the expense category). Everything else is DEFERRABLE.",
    ),
    _spec(
        key="finance.survival_critical_vendors",
        type="json",
        default=[],
        scopes=("global",),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Finance",
        label="Survival view: critical vendors",
        help="Vendor ids or names whose bills become MUST_PAY when due "
        "within 7 days (overdue bills are MUST_PAY regardless). "
        "Empty = only the per-bill vendor_critical flag applies.",
    ),
    # --- POS EMI financing ---
    # SINGLE source of truth for the EMI annual interest rate. The order
    # add-payment endpoint (routers/orders.py) builds the EMI schedule from
    # this policy, and the POS payment screen reads the SAME key via
    # GET /settings/policies/{key}?scope=store:<id> so the on-screen quote
    # always matches what the backend charges. The POSPayment.tsx fallback
    # constant mirrors this default -- change both together.
    _spec(
        key="pos.emi_annual_rate_percent",
        type="percent",
        default=12.0,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Finance",
        label="EMI annual interest rate %",
        help="Annual interest rate used for in-store EMI schedules. The POS "
        "payment screen quotes and the order records the same rate. "
        "0 = no-cost EMI.",
        minimum=0,
        maximum=60,
    ),
    # --- HR: half-day attendance rule (owner-requested, settings-system) ---
    # DARK by default: with hr.half_day_auto = False the attendance flow behaves
    # exactly as today (status is whatever the admin marks). Turning it ON makes
    # check-in/check-out auto-derive a HALF_DAY when the worked-hours or late
    # arrival thresholds below are breached -- but ONLY downgrades a PRESENT day;
    # an explicit ABSENT/LEAVE/HOLIDAY/WEEK_OFF/manual HALF_DAY is never touched.
    _spec(
        key="hr.half_day_auto",
        type="bool",
        default=False,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="HR",
        label="Auto-mark half-day",
        help="When ON, a workday is auto-marked HALF_DAY if hours worked fall "
        "below the minimum OR check-in is after the cutoff time below. OFF "
        "(default) keeps attendance status fully manual.",
    ),
    _spec(
        key="hr.half_day_min_hours",
        type="float",
        default=4.0,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="HR",
        label="Half-day minimum hours",
        help="A checked-out workday with fewer hours worked than this is "
        "auto-marked HALF_DAY (only when 'Auto-mark half-day' is ON).",
        minimum=0.5,
        maximum=12.0,
    ),
    _spec(
        key="hr.half_day_late_after",
        type="text",
        default="13:00",
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="HR",
        label="Half-day check-in cutoff (HH:MM)",
        help="Check-in after this 24h time is auto-marked HALF_DAY (only when "
        "'Auto-mark half-day' is ON). Blank disables the late-arrival trigger.",
    ),
    # ------------------------------------------------------------------
    # Previously-unreachable keys (registered 2026-08-27). Each of these was
    # already read in code via get_policy(...) but MISSING from this registry,
    # so get_policy returned the caller's `default` before ever consulting the
    # DB scopes / env / Settings -- i.e. the setting could never be changed.
    # Every default below MIRRORS the code fallback at its call site, so a
    # fresh DB (and every existing deploy) behaves byte-identically; the keys
    # merely become genuinely tunable. Guarded by
    # tests/test_policy_registry_guard.py.
    # ------------------------------------------------------------------
    # --- Bank / cash / POS reconciliation (#16, routers/bank_reconciliation.py) ---
    _spec(
        key="reconciliation.variance_tolerance_paise",
        type="money_paisa",
        default=100,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN", "ACCOUNTANT"),
        group="Finance",
        label="Bank reconciliation match tolerance",
        help="Absolute gap (paisa) within which a bank line matches a book "
        "figure during bank/cash/POS reconciliation. Default Rs 1.",
        minimum=0,
    ),
    _spec(
        key="reconciliation.mdr_bps",
        type="int",
        default=0,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN", "ACCOUNTANT"),
        group="Finance",
        label="Digital MDR (basis points)",
        help="Merchant-discount-rate fee in basis points deducted from "
        "card/UPI/wallet takings before they hit the bank -- used to net "
        "expected digital deposits in reconciliation. 100 bps = 1%.",
        minimum=0,
        maximum=2000,
    ),
    # --- F26 leave fast-path (routers/hr.py) ---
    _spec(
        key="approval.leave_fastpath_days",
        type="days",
        default=2,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="HR",
        label="Leave fast-path window (days)",
        help="A CASUAL/SICK leave starting in fewer than this many days is "
        "routed through the urgent (PIN) approval fast path instead of the "
        "standard flow.",
        minimum=0,
        maximum=30,
    ),
    # --- Roster coverage (services/roster_engine.py; also read by TASKMASTER) ---
    _spec(
        key="hr.roster_required_optometrists",
        type="int",
        default=1,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="HR",
        label="Required optometrists per day",
        help="Minimum optometrists a published roster must cover per store per "
        "day; a shortfall surfaces as a coverage breach.",
        minimum=0,
        maximum=10,
    ),
    # --- Vendor RMA / RTV credit approval (services/vendor_rma.py) ---
    _spec(
        key="rtv.credit.approval_above",
        type="money_paisa",
        default=5_000_000,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Refunds & Returns",
        label="Vendor credit needs approval above",
        help="A return-to-vendor credit (or credit variance) at/above this "
        "amount requires a maker-checker approval before it is recorded. "
        "Default Rs 50,000.",
        minimum=0,
    ),
    # --- E3 experimental POS sell flag (routers/item_events.py). DARK by
    # default and SUPERADMIN-only: flipping it ON only opens the manual
    # /items/{id}/sell CAS endpoint -- the main POS order flow does NOT emit
    # E3 SELL events yet (E3w deferred, owner-gated), so this has no effect on
    # billing. Registered so the designed per-store gate is actually flippable.
    _spec(
        key="FF_E3_POS_SELL",
        type="bool",
        default=False,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN",),
        group="Feature Flags",
        label="E3 unit-sell endpoint (experimental)",
        help="Enables the concurrency-safe AVAILABLE->SOLD item-event endpoint. "
        "EXPERIMENTAL: nothing in the app calls it yet; POS billing is "
        "unaffected either way. Leave OFF unless instructed.",
    ),
    # --- Blind stock take (services/blind_stock_take.py) ---
    _spec(
        key="inventory.blind_count_tolerance_units",
        type="int",
        default=0,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Operations",
        label="Blind count variance tolerance (units)",
        help="Per-SKU counted-vs-expected gap (units) treated as MATCHED in a "
        "blind stock take. 0 = exact match required.",
        minimum=0,
        maximum=100,
    ),
    _spec(
        key="inventory.blind_count_reopen_roles",
        type="json",
        default=["STORE_MANAGER", "AREA_MANAGER", "ADMIN", "SUPERADMIN"],
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Operations",
        label="Blind count reopen roles",
        help="Roles permitted to reopen a locked blind stock take (ADMIN and "
        "SUPERADMIN are always permitted regardless of this list).",
    ),
    # --- Inventory balancing (services/inventory_balancing.py) ---
    _spec(
        key="inventory.balancing_window_days",
        type="days",
        default=90,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Operations",
        label="Balancing sales window (days)",
        help="Sales lookback window used to compute per-store run rates for "
        "cross-store rebalancing proposals.",
        minimum=7,
        maximum=365,
    ),
    _spec(
        key="inventory.overstock_days_cover",
        type="days",
        default=120,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Operations",
        label="Overstock threshold (days of cover)",
        help="A SKU with more days-of-cover than this is classed OVERSTOCK "
        "(a rebalancing donor).",
        minimum=1,
        maximum=730,
    ),
    _spec(
        key="inventory.understock_days_cover",
        type="days",
        default=21,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Operations",
        label="Understock threshold (days of cover)",
        help="A SKU with fewer days-of-cover than this is classed UNDERSTOCK "
        "(a rebalancing recipient).",
        minimum=1,
        maximum=365,
    ),
    _spec(
        key="inventory.target_days_cover",
        type="days",
        default=45,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Operations",
        label="Rebalancing target (days of cover)",
        help="Days-of-cover a rebalancing move tops the recipient store up to.",
        minimum=1,
        maximum=730,
    ),
    # --- Petty cash (services/petty_cash_service.py + settlement). Rupee
    # floats, matching the service math (NOT paisa -- the service operates in
    # rupees end to end). ---
    _spec(
        key="petty_cash.float_limit",
        type="float",
        default=5000.0,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Petty Cash",
        label="Float limit (rupees)",
        help="Maximum petty-cash float a store may hold.",
        minimum=0,
    ),
    _spec(
        key="petty_cash.low_balance_threshold",
        type="float",
        default=500.0,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Petty Cash",
        label="Low-balance alert threshold (rupees)",
        help="A payout dropping the float below this fires the low-balance "
        "alert to the manager.",
        minimum=0,
    ),
    _spec(
        key="petty_cash.auto_approval_threshold",
        type="float",
        default=500.0,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Petty Cash",
        label="Auto-approval threshold (rupees)",
        help="A petty-cash payout strictly above this requires a PIN-gated "
        "approval before the float is debited.",
        minimum=0,
    ),
    _spec(
        key="petty_cash.receipt_required_above",
        type="float",
        default=200.0,
        scopes=("global",),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Petty Cash",
        label="Receipt mandatory above (rupees)",
        help="Any petty-cash claim strictly above this must carry a receipt. "
        "Read at global scope only.",
        minimum=0,
    ),
    _spec(
        key="petty_cash.settlement_tolerance",
        type="float",
        default=0.0,
        scopes=("global", "entity", "store"),
        write_roles=("SUPERADMIN", "ADMIN"),
        group="Petty Cash",
        label="Settlement variance tolerance (rupees)",
        help="Counted-vs-expected gap within which an EOD petty-cash "
        "settlement closes BALANCED. 0 = exact match required.",
        minimum=0,
    ),
]

REGISTRY = {s.key: s for s in _REGISTRY_LIST}

# Union of all write roles across the registry -- the coarse RBAC-table gate for
# the PUT/DELETE endpoints (the per-key write_roles is the fine-grained gate).
ALL_WRITE_ROLES = sorted({r for s in _REGISTRY_LIST for r in s.write_roles})


def resolve_emi_annual_rate(store_id) -> float:
    """Effective EMI annual rate (%) for a store: policy
    `pos.emi_annual_rate_percent` resolved store > entity > global > registry
    default (12.0). Fail-soft to the registry default so a policy-engine hiccup
    never blocks a payment or a store read.

    THE ONE DEFINITION. Both consumers -- the order/payment engine
    (orders.py) and the store-detail read the POS screen quotes from
    (stores.py) -- import THIS function, so the rate a cashier shows a
    customer and the rate the order charges cannot drift apart. That
    screen-vs-charge divergence is the exact defect PR #997 exists to close;
    do not re-inline this in a router.
    """
    try:
        from . import policy_engine

        scope = {"store_id": store_id} if store_id else None
        return float(policy_engine.get_policy("pos.emi_annual_rate_percent", scope))
    except Exception:  # noqa: BLE001
        return 12.0  # mirrors the registry default for pos.emi_annual_rate_percent


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
