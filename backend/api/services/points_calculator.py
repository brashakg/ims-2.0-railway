"""
IMS 2.0 — Points Calculator (Module ii)
========================================
Pure functions for daily-points scoring. No side effects, no DB access.
Keeps the router skinny and the tests fast.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# 9-category point ceilings — must match Excel exactly.
CATEGORY_MAX = {
    "attendance": 10,
    "conversion": 20,
    "task": 10,
    "visufit": 10,
    "punctuality": 10,
    "behaviour": 10,
    "kicker_1": 10,
    "kicker_2": 10,
    "reviews": 10,
}
TOTAL_MAX = sum(CATEGORY_MAX.values())  # 100

CATEGORIES_FOR_TOTAL = [
    "attendance",
    "conversion",
    "task",
    "visufit",
    "punctuality",
    "behaviour",
    "kicker_1",
    "kicker_2",
    "reviews",
]


def compute_total(scores: Dict[str, int]) -> int:
    """Sum of the 9 category fields. Missing keys treated as 0."""
    return sum(int(scores.get(k) or 0) for k in CATEGORIES_FOR_TOTAL)


def apply_visufit_gate(
    scores: Dict[str, int],
    *,
    visufit_usage_pct_mtd: Optional[float],
    threshold: float,
    enabled: bool,
) -> Tuple[Dict[str, int], bool]:
    """If the gate is enabled and MTD usage is below threshold,
    override `visufit` to 0. Returns (possibly mutated scores,
    gate_applied flag).

    `visufit_usage_pct_mtd` is a fraction in [0, 1]. None means we
    don't have data yet (e.g. first day of the month) — gate not
    applied in that case to avoid penalizing for missing data.
    """
    out = dict(scores)
    if not enabled:
        return out, False
    if visufit_usage_pct_mtd is None:
        return out, False
    if visufit_usage_pct_mtd >= threshold:
        return out, False
    out["visufit"] = 0
    return out, True


def compute_eligibility(total: int, bands: List[Dict]) -> float:
    """Walk the bands, return the value of the first band that contains
    `total`. Bands are { min, max, value } where the interval is
    [min, max). If nothing matches, returns 0.0 (most conservative)."""
    for b in bands or []:
        try:
            mn = float(b.get("min", 0))
            mx = float(b.get("max", 0))
            val = float(b.get("value", 0.0))
        except (TypeError, ValueError):
            continue
        if mn <= total < mx:
            return val
    return 0.0


def aggregate_mtd(rows: List[Dict]) -> Dict[str, Dict]:
    """Group `rows` (active points_log docs) by staff_id; build per-
    staff MTD stats: averages per category, total avg, days_logged,
    eligibility_avg.

    Output shape:
        { staff_id: {
            staff_id, staff_name,
            days_logged: int,
            avg: { attendance: float, ..., total: float },
            eligibility_avg: float,
        }, ... }
    """
    by_staff: Dict[str, Dict] = {}
    for r in rows:
        sid = r.get("staff_id") or ""
        if not sid:
            continue
        if sid not in by_staff:
            by_staff[sid] = {
                "staff_id": sid,
                "staff_name": r.get("staff_name"),
                "rows": [],
            }
        by_staff[sid]["rows"].append(r)
        if r.get("staff_name"):
            by_staff[sid]["staff_name"] = r["staff_name"]

    out: Dict[str, Dict] = {}
    for sid, slot in by_staff.items():
        rows_for_staff = slot["rows"]
        days = len(rows_for_staff)
        if days == 0:
            continue
        avg: Dict[str, float] = {}
        for cat in CATEGORIES_FOR_TOTAL:
            avg[cat] = round(
                sum(int(r.get(cat) or 0) for r in rows_for_staff) / days, 2
            )
        avg["total"] = round(
            sum(int(r.get("total") or 0) for r in rows_for_staff) / days, 2
        )
        elig_sum = sum(float(r.get("eligibility") or 0.0) for r in rows_for_staff)
        out[sid] = {
            "staff_id": sid,
            "staff_name": slot["staff_name"],
            "days_logged": days,
            "avg": avg,
            "eligibility_avg": round(elig_sum / days, 4),
        }
    return out


def leaderboard_sort_key(entry: Dict) -> Tuple:
    """Sort entries by avg.total DESC, tie-broken by days_logged DESC."""
    avg_total = float((entry.get("avg") or {}).get("total") or 0)
    days = int(entry.get("days_logged") or 0)
    return (-avg_total, -days, entry.get("staff_id") or "")
