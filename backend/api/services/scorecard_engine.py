"""
IMS 2.0 - Scorecard + Slab-Incentive Engine (SC)
=================================================
ONE importable surface for the whole PUNE incentive chain. The business
logic that was embedded in the `points` + `payout` routers is extracted
here so it is testable in isolation and so the payroll feed has a single,
unambiguous entry point.

CONTRACT (binding, see docs/roadmap/CORRECTIONS.md):
  * P0-4  -- `get_incentive_for_payroll` REPLACES the old payroll
            `_fetch_incentive`. The payroll feed reads exactly ONE source:
            the LOCKED/PAID payout snapshot. NEVER sum two incentive paths.
  * P0-5  -- the seeded slab multiplier is 1.1 @ 14% discount (the table
            floor-walks ASCENDING: 0.11->1.4 ... 0.14->1.1). This engine
            reuses `payout_calculator.compute_multiplier` -- it does NOT
            fork the math, so the 1.1@14% behaviour is inherited verbatim.
  * P0-1  -- standalone Mongo: single-document writes only. The payroll
            feed stamp is one `find_one_and_update` on ONE snapshot doc.
            No cross-collection transaction anywhere.
  * E2    -- settings resolve global -> entity -> store via the E2 scope
            resolver (policy_engine._chain). We do NOT hand-roll a fourth
            store-walk; the entity_id lookup + scope chain is reused.

This module imports the EXISTING pure calculators (points_calculator,
payout_calculator) and re-exports their surface. It adds the Product-
Incentive Kicker rollup, the E2 settings resolver, and the payroll feed.

No emoji (Windows cp1252).
"""

from __future__ import annotations

from datetime import date as date_type, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

# Reuse -- never fork -- the existing pure calculators.
from api.services.points_calculator import (  # noqa: F401  (re-exported surface)
    CATEGORIES_FOR_TOTAL,
    CATEGORY_MAX,
    TOTAL_MAX,
    aggregate_mtd,
    apply_visufit_gate,
    compute_eligibility,
    compute_total,
    leaderboard_sort_key,
)
from api.services.payout_calculator import (  # noqa: F401  (re-exported surface)
    assemble_payout,
    compute_best_level,
    compute_individual_payouts,
    compute_manager_bonuses,
    compute_multiplier,
    compute_pools,
    compute_targets,
)


# ===========================================================================
# Daily scoring (extracted from points.py::_build_row)
# ===========================================================================


class FootfallMissingError(Exception):
    """Raised by score_daily when the conversion component must be auto-filled
    (caller supplied no explicit value) BUT no walk-in footfall exists for the
    staff/day -- so the auto-fill is undefined (None, not 0).

    N3 / CORRECTIONS.md HARDENING line 92 (binding): a missing footfall must
    NOT silently score 0 (it corrupts payout rupees -- 'Fail Loudly'). The HTTP
    layer catches this and returns 422 with a footfall-explaining message; a
    manager can override by POSTing an explicit numeric conversion value.
    """

    def __init__(self, date_str: str):
        self.date_str = date_str
        super().__init__(
            f"Footfall missing for {date_str}. Enter the walk-in count or "
            f"supply an explicit conversion score."
        )


def conversion_score(
    store_id: str,
    date_str: str,
    staff_id: str,
    *,
    walkout_repo: Any,
    walkin_repo: Any,
) -> Optional[int]:
    """Module (i) conversion math, in-process (no HTTP self-call).

    conversion = round((walk_ins - walkouts + 90-day retro) / walk_ins * 20)
    clamped to [0, 20].

    N3 / CORRECTIONS.md HARDENING line 92 (binding): returns None -- NOT 0 --
    when there is no walk-in footfall (walk_ins <= 0). A silent 0 here corrupts
    payout rupees; the caller must treat None as "unscored / blocked" rather
    than zero. Also returns None when the walkout repo is unavailable so the
    caller can likewise treat that as "no auto-fill".
    """
    if walkout_repo is None:
        return None
    try:
        walkouts_today = walkout_repo.list_walkouts(
            store_id=store_id,
            date_from=date_str,
            date_to=date_str,
            limit=5000,
        )
    except Exception:  # noqa: BLE001
        walkouts_today = []
    walkouts_count = sum(
        1 for w in walkouts_today if w.get("sales_person_id") == staff_id
    )

    # Retro: prior 90 days where result_set_at falls on date_str.
    try:
        target_d = date_type.fromisoformat(date_str)
        window_from = (target_d - timedelta(days=90)).isoformat()
        window_to = (target_d - timedelta(days=1)).isoformat()
        prior = walkout_repo.list_walkouts(
            store_id=store_id,
            date_from=window_from,
            date_to=window_to,
            limit=5000,
        )
    except Exception:  # noqa: BLE001
        prior = []
    retro = 0
    for w in prior:
        if w.get("result") != "CONVERTED":
            continue
        if w.get("sales_person_id") != staff_id:
            continue
        rsa = w.get("result_set_at")
        rsa_str = (
            rsa[:10]
            if isinstance(rsa, str)
            else (rsa.date().isoformat() if isinstance(rsa, datetime) else "")
        )
        if rsa_str != date_str:
            continue
        retro += 1

    walk_ins = 0
    if walkin_repo is not None:
        try:
            today_doc = walkin_repo.get_today(store_id, date_str=date_str)
            walk_ins = int((today_doc.get("per_staff") or {}).get(staff_id, 0))
        except Exception:  # noqa: BLE001
            pass
    if walk_ins <= 0:
        # N3: missing footfall -> unscored (None), never a silent 0.
        return None
    raw = (walk_ins - walkouts_count + retro) / walk_ins * 20.0
    return int(round(max(0.0, min(20.0, raw))))


def score_daily(
    *,
    raw_scores: Dict[str, Any],
    date_str: str,
    staff_id: str,
    store_id: str,
    settings: Dict[str, Any],
    visufit_usage_pct_mtd: Optional[float],
    visufit_source: Optional[str] = None,
    conversion_provider: Optional[Callable[[], Optional[int]]] = None,
    today_str: Optional[str] = None,
    block_on_missing_footfall: bool = False,
) -> Dict[str, Any]:
    """Compose the scored row body (no DB, no audit, no log_id).

    Mirrors the old points.py::_build_row exactly:
      - auto-fill conversion when null AND the target date is today (via
        `conversion_provider`); past dates with a null conversion -> 0
      - apply the Visufit gate from settings
      - compute total /100 and snap eligibility to its band
      - snapshot the bands used (so future band edits don't rewrite history)

    Additions (SC delta):
      - tracks `visufit_source` provenance on the row

    N3 footfall correction (CORRECTIONS.md HARDENING line 92, binding):
      - when ``block_on_missing_footfall`` is True and the auto-fill is needed
        (caller supplied no explicit conversion, date is today) but the
        provider returns None (no walk-in footfall), raise FootfallMissingError
        instead of silently scoring 0. The HTTP layer turns that into a 422.
      - an explicit numeric ``conversion`` value bypasses the block entirely
        (manager override) and stamps ``conversion_missing_footfall=False``.
      - rows that DID auto-fill from real footfall carry
        ``conversion_missing_footfall=False``; the flag is only ever True on a
        legacy/lenient (non-blocking) path where a 0 was substituted for a
        missing footfall.
    """
    today = today_str or datetime.now().date().isoformat()
    scores = dict(raw_scores)

    conversion_missing_footfall = False
    explicit_conversion = scores.get("conversion") is not None
    if not explicit_conversion:
        auto = None
        if date_str == today and conversion_provider is not None:
            auto = conversion_provider()
        if auto is None:
            # No footfall (or unavailable repo / past date with no value).
            if block_on_missing_footfall and date_str == today:
                raise FootfallMissingError(date_str)
            conversion_missing_footfall = True
            scores["conversion"] = 0
        else:
            scores["conversion"] = auto

    threshold = float(settings.get("visufit_gate_threshold") or 0.9)
    enabled = bool(settings.get("visufit_gate_enabled", True))
    scored, gate_applied = apply_visufit_gate(
        scores,
        visufit_usage_pct_mtd=visufit_usage_pct_mtd,
        threshold=threshold,
        enabled=enabled,
    )

    total = compute_total(scored)
    bands = settings.get("eligibility_bands") or []
    eligibility = compute_eligibility(total, bands)

    return {
        "store_id": store_id,
        "date_str": date_str,
        "staff_id": staff_id,
        **scored,
        "total": total,
        "eligibility": eligibility,
        "eligibility_thresholds_used": {"bands": list(bands)},
        "visufit_gate_applied": gate_applied,
        "visufit_usage_pct_mtd": visufit_usage_pct_mtd,
        "visufit_source": visufit_source,
        "conversion_missing_footfall": conversion_missing_footfall,
    }


# ===========================================================================
# MTD aggregation + leaderboard (thin re-exports of the pure calculator)
# ===========================================================================


def aggregate_mtd_scores(rows: List[Dict]) -> Dict[str, Dict]:
    """Per-staff MTD aggregation. Re-export of points_calculator.aggregate_mtd
    so callers depend on one surface (the engine), not the calculator directly."""
    return aggregate_mtd(rows)


def leaderboard(rows: List[Dict]) -> List[Dict]:
    """Sorted leaderboard entries (avg.total DESC, days_logged DESC)."""
    by_staff = aggregate_mtd(rows)
    items = list(by_staff.values())
    items.sort(key=leaderboard_sort_key)
    return items


# ===========================================================================
# Product-Incentive Kicker rollup
# ===========================================================================


def kicker_for(
    store_id: str,
    ym: str,
    staff_id: str,
    *,
    kicker_repo: Any,
) -> Dict[str, Any]:
    """Monthly product-incentive rollup for one staff member.

    Returns {product_incentive_amount: float, sale_count: int}. ym is the
    'YYYY-MM' rollup key. A clawback (negative incentive_amount) reduces the
    total correctly because we sum the signed amounts (DECISIONS.md s4).
    """
    if kicker_repo is None:
        return {"product_incentive_amount": 0.0, "sale_count": 0}
    try:
        entries = kicker_repo.list_for_ym(store_id, ym, staff_id=staff_id)
    except Exception:  # noqa: BLE001
        entries = []
    total = round(sum(float(e.get("incentive_amount") or 0.0) for e in entries), 2)
    return {"product_incentive_amount": total, "sale_count": len(entries)}


def kicker_totals_for_month(
    store_id: str,
    ym: str,
    *,
    kicker_repo: Any,
) -> Dict[str, float]:
    """{staff_id: signed_rupee_total} for the whole store-month. Used to fold
    product incentive into each staff payout line."""
    if kicker_repo is None:
        return {}
    try:
        return kicker_repo.staff_total_for_ym(store_id, ym) or {}
    except Exception:  # noqa: BLE001
        return {}


# ===========================================================================
# Monthly payout (wraps assemble_payout; folds in product incentive)
# ===========================================================================


def compute_payout(
    *,
    store_id: str,
    year: int,
    month: int,
    settings: Dict[str, Any],
    inputs: Dict[str, float],
    mtd_data: Dict[str, Dict[str, Any]],
    name_lookup: Optional[Dict[str, Optional[str]]] = None,
    kicker_repo: Any = None,
) -> Dict[str, Any]:
    """One-call payout orchestration.

    Reuses payout_calculator.assemble_payout for the slab pool x weightage x
    multiplier math (P0-5: 1.1@14% inherited, never re-derived) and folds the
    Product-Incentive Kicker rupees into each staff payout line.

    `product_incentive` is a SEPARATE field per staff line so the slab payout
    and the kicker never silently merge -- the payroll feed total is
    slab_payout + manager_bonus + product_incentive.
    """
    envelope = assemble_payout(
        inputs=inputs,
        settings=settings,
        mtd_data=mtd_data,
        name_lookup=name_lookup,
    )

    ym = f"{year:04d}-{month:02d}"
    kicker_by_staff = kicker_totals_for_month(store_id, ym, kicker_repo=kicker_repo)

    # Manager-bonus lookup so we can compute a true per-staff combined total.
    bonus_by_staff: Dict[str, float] = {
        b.get("user_id"): float(b.get("total_bonus") or 0.0)
        for b in (envelope.get("manager_bonuses") or [])
        if b.get("user_id")
    }

    product_total = 0.0
    seen_staff = set()
    for line in envelope.get("staff_payouts") or []:
        uid = line.get("user_id")
        seen_staff.add(uid)
        pi = round(float(kicker_by_staff.get(uid, 0.0)), 2)
        line["product_incentive"] = pi
        product_total += pi
        bonus = bonus_by_staff.get(uid, 0.0)
        line["total_with_kicker"] = round(
            float(line.get("total_payout") or 0.0) + bonus + pi, 2
        )

    # Kicker rows for staff who have product incentive but no slab line
    # (e.g. workshop staff with a SPIFF) still surface in the rollup total.
    extra_kicker_only: List[Dict[str, Any]] = []
    for sid, amt in kicker_by_staff.items():
        if sid in seen_staff:
            continue
        amt = round(float(amt), 2)
        product_total += amt
        extra_kicker_only.append(
            {
                "user_id": sid,
                "name": (name_lookup or {}).get(sid) or sid,
                "product_incentive": amt,
                "total_with_kicker": amt,
            }
        )

    envelope["product_incentive_total"] = round(product_total, 2)
    envelope["kicker_only_payouts"] = extra_kicker_only

    grand = envelope.get("grand_total") or {}
    grand["product_incentive"] = round(product_total, 2)
    grand["all_with_kicker"] = round(
        float(grand.get("all") or 0.0) + product_total, 2
    )
    envelope["grand_total"] = grand

    envelope["store_id"] = store_id
    envelope["year"] = year
    envelope["month"] = month
    return envelope


# ===========================================================================
# Payroll feed (P0-4: the SINGLE incentive source of truth)
# ===========================================================================


def get_incentive_for_payroll(
    store_id: str,
    year: int,
    month: int,
    *,
    snapshot_repo: Any,
) -> Dict[str, float]:
    """Return {staff_id: total_incentive_rupees} from the LOCKED/PAID payout
    snapshot for (store, year, month).

    The per-staff total = slab payout + manager bonus + product incentive
    (every rupee that will be paid this month, in one number per staff).

    Returns {} when no LOCKED/PAID snapshot exists -- payroll then uses 0 and
    NEVER estimates (P0-4). This is the ONLY incentive path payroll reads; the
    old `incentives`-collection `_fetch_incentive` is retired.
    """
    if snapshot_repo is None:
        return {}
    try:
        snap = snapshot_repo.find_locked(store_id, year, month)
    except Exception:  # noqa: BLE001
        snap = None
    if not snap:
        return {}
    return incentive_map_from_snapshot(snap)


def incentive_map_from_snapshot(snap: Dict[str, Any]) -> Dict[str, float]:
    """{staff_id: rupees} from a snapshot doc. Pure -- no DB.

    Combines slab payout + manager bonus + product incentive per staff. A
    manager appears in BOTH staff_payouts and manager_bonuses (the bonus
    stacks, DECISIONS.md s3) so both are added for that staff_id.
    """
    out: Dict[str, float] = {}

    def _add(uid: Optional[str], amount: float) -> None:
        if not uid:
            return
        out[uid] = round(out.get(uid, 0.0) + float(amount or 0.0), 2)

    for line in snap.get("staff_payouts") or []:
        uid = line.get("user_id")
        _add(uid, float(line.get("total_payout") or 0.0))
        _add(uid, float(line.get("product_incentive") or 0.0))

    for bonus in snap.get("manager_bonuses") or []:
        _add(bonus.get("user_id"), float(bonus.get("total_bonus") or 0.0))

    for ko in snap.get("kicker_only_payouts") or []:
        _add(ko.get("user_id"), float(ko.get("product_incentive") or 0.0))

    return out


# ===========================================================================
# E2 settings resolution (global -> entity -> store, via the E2 scope chain)
# ===========================================================================

# Sentinel store_id values used to key non-store-scoped incentive_settings
# docs so the EXISTING unique `store_id` index on incentive_settings stays
# satisfied (one global row, one row per entity, one row per store).
GLOBAL_SCOPE_ID = "__global__"


def _entity_scope_id(entity_id: str) -> str:
    return f"__entity__:{entity_id}"


def scope_chain_for(store_id: Optional[str], entity_id: Optional[str]) -> List[str]:
    """Ordered (most-specific-first) list of incentive_settings doc keys for a
    store. Reuses the E2 scope resolver (policy_engine._chain) so the
    store->entity->global walk -- including the store_id -> entity_id lookup
    and the dirty-data fall-through to global -- is identical to E2. Falls back
    to a local walk only if the E2 engine can't be imported.
    """
    chain: List[str] = []
    try:
        from api.services import policy_engine as pe

        scope: Dict[str, Any] = {}
        if store_id:
            scope["store_id"] = store_id
        elif entity_id:
            scope["entity_id"] = entity_id
        addrs = pe._chain(scope) if (store_id or entity_id) else ["global"]
        for addr in addrs:
            if addr == "global":
                chain.append(GLOBAL_SCOPE_ID)
            elif addr.startswith("entity:"):
                chain.append(_entity_scope_id(addr.split(":", 1)[1]))
            elif addr.startswith("store:"):
                chain.append(addr.split(":", 1)[1])
        # If the E2 chain couldn't resolve the entity (dirty data) but the
        # caller passed one explicitly, slot it in between store and global.
        if entity_id and _entity_scope_id(entity_id) not in chain:
            insert_at = max(len(chain) - 1, 0)
            chain.insert(insert_at, _entity_scope_id(entity_id))
        return chain
    except Exception:  # noqa: BLE001
        # Fail-soft local walk (no E2 engine available).
        if store_id:
            chain.append(store_id)
        if entity_id:
            chain.append(_entity_scope_id(entity_id))
        chain.append(GLOBAL_SCOPE_ID)
        return chain


# Keys that are MERGE-able from broader scopes onto the resolved doc. These are
# the calculator inputs that an entity / global default can supply.
_RESOLVABLE_KEYS = (
    "eligibility_bands",
    "growth_targets",
    "base_rates",
    "discount_kill_threshold",
    "discount_multipliers",
    "staff_weightages",
    "supervisor_bonuses",
    "visufit_gate_threshold",
    "visufit_gate_enabled",
)


def resolve_settings(
    store_id: Optional[str],
    entity_id: Optional[str] = None,
    *,
    settings_repo: Any,
    defaults: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve incentive_settings global -> entity -> store.

    Walks the E2 scope chain (store first). The MOST-specific scope that
    defines a key wins; broader scopes fill in keys the narrower ones omit.
    Code defaults backstop anything still absent so a fresh DB never crashes
    the calculator.

    A store with NO settings doc (and no entity/global override) gets the code
    defaults -- identical to today's `get_for_store(store)` behaviour.
    """
    base: Dict[str, Any] = dict(defaults or {})
    if not base and settings_repo is not None:
        try:
            base = dict(settings_repo._defaults(store_id or GLOBAL_SCOPE_ID))
        except Exception:  # noqa: BLE001
            base = {}

    if settings_repo is None:
        base["store_id"] = store_id
        return base

    # Fetch each scope doc once. Walk BROADEST -> NARROWEST so narrower keys
    # overwrite. (chain is most-specific-first, so reverse it.)
    chain = scope_chain_for(store_id, entity_id)
    resolved: Dict[str, Any] = dict(base)
    sources: Dict[str, str] = {k: "default" for k in _RESOLVABLE_KEYS}
    for scope_key in reversed(chain):
        try:
            doc = settings_repo.get_raw(scope_key)
        except Exception:  # noqa: BLE001
            doc = None
        if not doc:
            continue
        scope_label = (
            "global"
            if scope_key == GLOBAL_SCOPE_ID
            else ("entity" if scope_key.startswith("__entity__:") else "store")
        )
        for k in _RESOLVABLE_KEYS:
            if k in doc and doc.get(k) is not None:
                resolved[k] = doc[k]
                sources[k] = scope_label

    resolved["store_id"] = store_id
    resolved["_resolution_sources"] = sources
    return resolved
