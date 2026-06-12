"""F33 -- Gamified leaderboard display layer (pure presentation service).

Pure functions that decorate the raw ``aggregate_mtd`` rows emitted by
``backend/api/services/points_calculator.py`` with gamified-but-restrained
presentation metadata: tier bands, earned titles, badges, rank deltas --
plus a SERVER-SIDE privacy strip that removes rupee/revenue fields for
junior viewer roles. No DB access, no randomness, fully deterministic.

Design constraints (owner standing preference):
  - Restrained / executive presentation: tiers + titles are plain text
    keys the frontend renders as neutral chips, never multi-colour.
  - The privacy strip is present-or-absent: keys matching the rupee
    pattern are POPPED for junior viewers, never zeroed or fabricated.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from api.services.points_calculator import CATEGORY_MAX

# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------

TIER_PODIUM = "PODIUM"
TIER_CONTENDER = "CONTENDER"
TIER_BUILDING = "BUILDING"

# ---------------------------------------------------------------------------
# Privacy strip
# ---------------------------------------------------------------------------
# Junior roles must NEVER receive rupee/revenue fields on leaderboard rows.
JUNIOR_VIEWER_ROLES = frozenset(
    {
        "SALES_STAFF",
        "SALES_CASHIER",
        "CASHIER",
        "WORKSHOP_STAFF",
        "OPTOMETRIST",
    }
)

# A viewer must hold at least one of these to see rupee/revenue fields.
PRIVILEGED_VIEWER_ROLES = frozenset(
    {
        "SUPERADMIN",
        "ADMIN",
        "AREA_MANAGER",
        "STORE_MANAGER",
        "ACCOUNTANT",
    }
)

# Any key matching this pattern is stripped for junior viewers.
_SENSITIVE_KEY_RE = re.compile(
    r"revenue|amount|incentive|payout|sales_value|rupee", re.IGNORECASE
)

# ---------------------------------------------------------------------------
# Titles -- one per scorecard category, earned by the viewer's strongest
# category (normalized by that category's max so conversion/20 competes
# fairly with the /10 categories). Deterministic: ties break on the fixed
# category order below.
# ---------------------------------------------------------------------------

_TITLE_ORDER: List[str] = [
    "conversion",
    "attendance",
    "punctuality",
    "task",
    "visufit",
    "behaviour",
    "reviews",
    "kicker_1",
    "kicker_2",
]

CATEGORY_TITLES: Dict[str, str] = {
    "conversion": "Conversion Champion",
    "attendance": "Reliability Anchor",
    "punctuality": "First Through The Door",
    "task": "Task Finisher",
    "visufit": "Visufit Specialist",
    "behaviour": "Floor Professional",
    "reviews": "Customer Voice",
    "kicker_1": "Kicker Closer",
    "kicker_2": "Add-On Driver",
}

TITLE_DESCRIPTIONS: Dict[str, str] = {
    "conversion": "Highest average in walk-in conversion",
    "attendance": "Highest average in attendance",
    "punctuality": "Highest average in punctuality",
    "task": "Highest average in task completion",
    "visufit": "Highest average in Visufit usage",
    "behaviour": "Highest average in floor behaviour",
    "reviews": "Highest average in customer reviews",
    "kicker_1": "Highest average in kicker 1",
    "kicker_2": "Highest average in kicker 2",
}

BADGE_DEFINITIONS: Dict[str, str] = {
    "eligibility_100": "Full incentive eligibility for the period",
    "logged_every_day": "Scorecard logged on every day of the period",
    "top_riser": "Climbed two or more ranks since the previous period",
    "consistent_90": "Average daily total of 90 or above",
}


def tier_for_rank(rank: int, total: int) -> str:
    """Band a 1-based rank into PODIUM / CONTENDER / BUILDING.

    PODIUM    -- ranks 1-3
    CONTENDER -- the top half of the board (excluding the podium)
    BUILDING  -- everyone else
    """
    if rank <= 0 or total <= 0:
        return TIER_BUILDING
    if rank <= 3:
        return TIER_PODIUM
    half = (total + 1) // 2  # ceil(total / 2)
    if rank <= half:
        return TIER_CONTENDER
    return TIER_BUILDING


def _normalized_category_scores(row: Dict[str, Any]) -> Dict[str, float]:
    """Per-category average normalized to [0, 1] by the category max."""
    avg = row.get("avg") or {}
    out: Dict[str, float] = {}
    for cat in _TITLE_ORDER:
        mx = CATEGORY_MAX.get(cat) or 1
        try:
            val = float(avg.get(cat) or 0.0)
        except (TypeError, ValueError):
            val = 0.0
        out[cat] = val / mx
    return out


def title_for(row: Dict[str, Any]) -> Optional[str]:
    """Deterministic earned title from the score profile: the strongest
    normalized category wins; ties break on the fixed ``_TITLE_ORDER``.
    Returns None when there is no signal (all zero / missing)."""
    norm = _normalized_category_scores(row)
    best_cat: Optional[str] = None
    best_val = 0.0
    for cat in _TITLE_ORDER:  # fixed order = deterministic tie-break
        val = norm.get(cat, 0.0)
        if val > best_val:
            best_val = val
            best_cat = cat
    if best_cat is None or best_val <= 0.0:
        return None
    return CATEGORY_TITLES[best_cat]


def badge_keys_for(
    row: Dict[str, Any],
    rank: int = 0,
    prev_rank: Optional[int] = None,
    period_days: Optional[int] = None,
) -> List[str]:
    """Computed badge keys for a row. Deterministic, additive only.

    - eligibility_100 : eligibility_avg is a full 1.0 for the period
    - logged_every_day: days_logged covers every day of the period
                        (only when the caller supplies period_days)
    - top_riser       : climbed >= 2 ranks vs the previous period
    - consistent_90   : average daily total >= 90
    """
    badges: List[str] = []
    try:
        elig = float(row.get("eligibility_avg") or 0.0)
    except (TypeError, ValueError):
        elig = 0.0
    if elig >= 0.9999:
        badges.append("eligibility_100")

    days_logged = int(row.get("days_logged") or 0)
    if period_days and days_logged >= period_days:
        badges.append("logged_every_day")

    if prev_rank is not None and rank > 0 and (prev_rank - rank) >= 2:
        badges.append("top_riser")

    try:
        avg_total = float((row.get("avg") or {}).get("total") or 0.0)
    except (TypeError, ValueError):
        avg_total = 0.0
    if avg_total >= 90.0:
        badges.append("consistent_90")

    return badges


def _strip_sensitive(value: Any) -> Any:
    """Recursively remove dict keys matching the rupee/revenue pattern.
    Present-or-absent semantics: nothing is zeroed or fabricated."""
    if isinstance(value, dict):
        return {
            k: _strip_sensitive(v)
            for k, v in value.items()
            if not _SENSITIVE_KEY_RE.search(str(k))
        }
    if isinstance(value, list):
        return [_strip_sensitive(v) for v in value]
    return value


def viewer_is_privileged(viewer_roles: Iterable[str]) -> bool:
    """True when the viewer holds at least one privileged (managerial /
    finance) role. A viewer with no roles, or only junior roles, is NOT
    privileged -- most-conservative default."""
    roles = {str(r).upper() for r in (viewer_roles or [])}
    return bool(roles & PRIVILEGED_VIEWER_ROLES)


def build_leaderboard_row(
    raw_row: Dict[str, Any],
    rank: int,
    total: int,
    viewer_roles: Iterable[str],
    prev_rank: Optional[int] = None,
    period_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Decorate one raw ``aggregate_mtd`` row into a presentation row.

    Adds: rank, tier_label, title_earned, badge_keys, rank_delta.
    Strips: any rupee/revenue/incentive/payout key (recursively) when the
    viewer does not hold a privileged role.
    """
    row: Dict[str, Any] = dict(raw_row or {})

    row["rank"] = rank
    row["tier_label"] = tier_for_rank(rank, total)
    row["title_earned"] = title_for(row)
    row["badge_keys"] = badge_keys_for(
        row, rank=rank, prev_rank=prev_rank, period_days=period_days
    )
    row["rank_delta"] = (prev_rank - rank) if prev_rank is not None else None

    if not viewer_is_privileged(viewer_roles):
        row = _strip_sensitive(row)
    return row


def titles_catalog() -> List[Dict[str, Any]]:
    """Catalog of all earnable titles + badges (for the FE legend).
    Stable order; safe for any authenticated viewer (no rupee data)."""
    out: List[Dict[str, Any]] = []
    for cat in _TITLE_ORDER:
        out.append(
            {
                "kind": "title",
                "key": cat,
                "label": CATEGORY_TITLES[cat],
                "description": TITLE_DESCRIPTIONS[cat],
            }
        )
    for key, desc in BADGE_DEFINITIONS.items():
        out.append(
            {
                "kind": "badge",
                "key": key,
                "label": key.replace("_", " ").title(),
                "description": desc,
            }
        )
    return out


def leaderboard_config_defaults() -> Dict[str, Any]:
    """Default leaderboard_config sub-doc (stored on incentive_settings)."""
    return {
        "enabled": True,
        "scope_default": "store",
        "show_titles": True,
        "show_badges": True,
    }
