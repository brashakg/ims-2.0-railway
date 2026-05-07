"""
IMS 2.0 — Payout Calculator (Pune Incentive Module iii)
========================================================
Pure functions for monthly payout sizing + per-staff allocation +
manager bonus stacking. No side effects, no DB access.

Decisions baked in (per docs/PUNE_INCENTIVE_BUILD_PLAN.md):
  - Best-level-only: hitting L3 zeroes L1+L2 pools
  - Discount kill: avg_discount > kill_threshold → pool=0
  - Floor rounding on discount %: 11.99% → 11% bucket → 1.4×
    multiplier (NOT 1.3×). Achieved via floor(pct*100)/100.
  - Pool sizing: max(this_year_sale, target[best]) × base_rate × multiplier
  - Per-staff payout: pool × weightage × eligibility (eligibility from
    Module ii's MTD avg → band)
  - Manager bonus: STACKS with the manager's individual payout
    (decision §3) — pool × bonus_pct × manager's eligibility
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


def _round_up_to_10k(value: float) -> int:
    """Round UP to the nearest 10,000 (Excel does ceiling targets so
    'L1 = last_year_sale × 1.20' yields a clean ₹2,210,000-style
    figure, not ₹2,205,600)."""
    return int(math.ceil(value / 10_000.0) * 10_000)


def compute_targets(
    last_year_sale: float, growth_pcts: Dict[str, float]
) -> Dict[str, int]:
    """{ 'L1': 2210000, 'L2': 2300000, 'L3': 2390000 } for Pune May-26."""
    return {
        lvl: _round_up_to_10k(last_year_sale * (1 + g))
        for lvl, g in (growth_pcts or {}).items()
    }


def compute_multiplier(
    avg_discount_pct: float,
    multipliers: List[Dict],
    kill_threshold: float,
) -> float:
    """Discount kill switch + floor-rounded tier walk.

    `multipliers` is a list of { max_pct: float, multiplier: float }
    sorted ascending by max_pct. The smallest max_pct >= floored pct
    wins. If avg_discount_pct > kill_threshold, returns 0 — the entire
    payout is zeroed (Pune team called this "discount kill")."""
    if avg_discount_pct > kill_threshold + 1e-9:
        return 0.0
    floored = math.floor(avg_discount_pct * 100) / 100.0
    sorted_mult = sorted(
        multipliers or [], key=lambda t: float(t.get("max_pct", 0))
    )
    for tier in sorted_mult:
        try:
            cap = float(tier.get("max_pct"))
            mult = float(tier.get("multiplier"))
        except (TypeError, ValueError):
            continue
        if floored <= cap + 1e-9:
            return mult
    return 0.0


def compute_best_level(
    this_year_sale: float, targets: Dict[str, float]
) -> Optional[str]:
    """Return 'L3' if hit, else 'L2', else 'L1', else None."""
    if not targets:
        return None
    if this_year_sale >= targets.get("L3", float("inf")):
        return "L3"
    if this_year_sale >= targets.get("L2", float("inf")):
        return "L2"
    if this_year_sale >= targets.get("L1", float("inf")):
        return "L1"
    return None


def compute_pools(
    this_year_sale: float,
    targets: Dict[str, float],
    base_rates: Dict[str, float],
    multiplier: float,
    best_level: Optional[str],
) -> Dict[str, float]:
    """Best-level-only pool sizing.

      pool[best] = max(this_year_sale, target[best]) × base_rate × multiplier

    All other levels are 0 (so a team that hit L3 doesn't stack L1+L2+L3
    — the Pune scheme is winner-takes-all on the level dimension)."""
    pools: Dict[str, float] = {"L1": 0.0, "L2": 0.0, "L3": 0.0}
    if not best_level or multiplier <= 0:
        return pools
    target = float(targets.get(best_level, 0))
    rate = float((base_rates or {}).get(best_level, 0))
    base_value = max(float(this_year_sale), target)
    pools[best_level] = round(base_value * rate * multiplier, 2)
    return pools


def compute_individual_payouts(
    pools: Dict[str, float],
    staff_weightages: Dict[str, float],
    mtd_data: Dict[str, Dict[str, Any]],
    *,
    name_lookup: Optional[Dict[str, Optional[str]]] = None,
) -> List[Dict[str, Any]]:
    """One row per staff member with their per-level payout breakdown.

    `mtd_data` is keyed by user_id and each entry must carry an
    `eligibility` field in [0, 1]. Staff missing from `mtd_data` are
    treated as eligibility=0 (haven't logged enough days)."""
    out: List[Dict[str, Any]] = []
    for user_id, weightage in (staff_weightages or {}).items():
        slot = mtd_data.get(user_id) or {}
        eligibility = float(slot.get("eligibility") or 0.0)
        per_level: Dict[str, float] = {}
        for lvl, pool in pools.items():
            per_level[lvl] = round(
                float(pool) * float(weightage) * eligibility, 2
            )
        total_payout = round(sum(per_level.values()), 2)
        out.append({
            "user_id": user_id,
            "name": (name_lookup or {}).get(user_id) or slot.get("name"),
            "weightage": float(weightage),
            "mtd_avg_total": slot.get("mtd_avg_total"),
            "eligibility": eligibility,
            "payout_by_level": per_level,
            "total_payout": total_payout,
        })
    return out


def compute_manager_bonuses(
    pools: Dict[str, float],
    supervisor_bonuses: List[Dict],
    mtd_data: Dict[str, Dict[str, Any]],
    *,
    name_lookup: Optional[Dict[str, Optional[str]]] = None,
) -> List[Dict[str, Any]]:
    """Manager bonus = pool × bonus_pct × manager's own eligibility.
    Stacks with the manager's individual staff payout (decision §3)."""
    out: List[Dict[str, Any]] = []
    for sup in supervisor_bonuses or []:
        uid = sup.get("user_id")
        if not uid:
            continue
        slot = mtd_data.get(uid) or {}
        eligibility = float(slot.get("eligibility") or 0.0)
        bonus_pct = sup.get("bonus_pct") or {}
        bonus_by_level: Dict[str, float] = {}
        for lvl, pool in pools.items():
            pct = float(bonus_pct.get(lvl) or 0)
            bonus_by_level[lvl] = round(float(pool) * pct * eligibility, 2)
        total_bonus = round(sum(bonus_by_level.values()), 2)
        out.append({
            "user_id": uid,
            "role": sup.get("role"),
            "name": (name_lookup or {}).get(uid) or slot.get("name"),
            "eligibility": eligibility,
            "bonus_pct": dict(bonus_pct),
            "bonus_by_level": bonus_by_level,
            "total_bonus": total_bonus,
        })
    return out


def assemble_payout(
    *,
    inputs: Dict[str, float],
    settings: Dict[str, Any],
    mtd_data: Dict[str, Dict[str, Any]],
    name_lookup: Optional[Dict[str, Optional[str]]] = None,
) -> Dict[str, Any]:
    """One-call orchestrator. Inputs:
      inputs: { last_year_sale, this_year_sale, avg_discount_pct,
                visufit_usage_pct }
      settings: store-level incentive_settings doc
      mtd_data: { user_id: { eligibility, mtd_avg_total, name? } }
      name_lookup: optional id→name override (router resolves from users repo)
    """
    last_year = float(inputs.get("last_year_sale") or 0)
    this_year = float(inputs.get("this_year_sale") or 0)
    avg_disc = float(inputs.get("avg_discount_pct") or 0)
    growth_targets = settings.get("growth_targets") or {}
    base_rates = settings.get("base_rates") or {}
    multipliers = settings.get("discount_multipliers") or []
    kill_threshold = float(settings.get("discount_kill_threshold") or 0.15)
    weightages = settings.get("staff_weightages") or {}
    supervisor_bonuses = settings.get("supervisor_bonuses") or []

    targets = compute_targets(last_year, growth_targets)
    multiplier = compute_multiplier(avg_disc, multipliers, kill_threshold)
    best_level = compute_best_level(this_year, targets)
    pools = compute_pools(this_year, targets, base_rates, multiplier, best_level)

    target_envelope: Dict[str, Any] = {}
    for lvl in ("L1", "L2", "L3"):
        target_envelope[lvl] = {
            "growth": float(growth_targets.get(lvl) or 0),
            "target": targets.get(lvl, 0),
            "achieved": this_year >= targets.get(lvl, float("inf")),
        }

    multiplier_tier = _multiplier_tier_label(avg_disc, multipliers, kill_threshold)
    discount_kill_active = avg_disc > kill_threshold + 1e-9

    individuals = compute_individual_payouts(
        pools, weightages, mtd_data, name_lookup=name_lookup,
    )
    managers = compute_manager_bonuses(
        pools, supervisor_bonuses, mtd_data, name_lookup=name_lookup,
    )

    total_team_pool = round(sum(pools.values()), 2)
    grand_staff = round(sum(p["total_payout"] for p in individuals), 2)
    grand_manager = round(sum(b["total_bonus"] for b in managers), 2)

    return {
        "inputs": {
            "last_year_sale": last_year,
            "this_year_sale": this_year,
            "avg_discount_pct": avg_disc,
            "visufit_usage_pct": float(inputs.get("visufit_usage_pct") or 0),
        },
        "targets": target_envelope,
        "best_level_achieved": best_level,
        "discount_kill_active": discount_kill_active,
        "multiplier": multiplier,
        "multiplier_tier": multiplier_tier,
        "pools": pools,
        "total_team_pool": total_team_pool,
        "staff_payouts": individuals,
        "manager_bonuses": managers,
        "grand_total": {
            "staff": grand_staff,
            "manager": grand_manager,
            "all": round(grand_staff + grand_manager, 2),
        },
    }


def _multiplier_tier_label(
    avg_discount_pct: float, multipliers: List[Dict], kill_threshold: float
) -> str:
    """Human-readable tier label for the dashboard chip."""
    if avg_discount_pct > kill_threshold + 1e-9:
        return "KILLED"
    floored = math.floor(avg_discount_pct * 100) / 100.0
    sorted_mult = sorted(multipliers or [], key=lambda t: float(t.get("max_pct", 0)))
    for tier in sorted_mult:
        try:
            cap = float(tier.get("max_pct"))
        except (TypeError, ValueError):
            continue
        if floored <= cap + 1e-9:
            return f"≤{int(round(cap * 100))}%"
    return "—"
