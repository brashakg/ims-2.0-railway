"""F33 -- Gamified leaderboard display layer (pure presentation service).

Pure functions that decorate the raw ``aggregate_mtd`` rows emitted by
``backend/api/services/points_calculator.py`` with gamified-but-restrained
presentation metadata: tier bands, earned titles, badges, rank deltas --
plus a SERVER-SIDE privacy strip that removes rupee/revenue fields for
junior viewer roles. No DB access, no randomness, fully deterministic.
"""

from typing import Any, Dict, List, Optional


def tier_for_rank(rank: int, total: int) -> str:
    """Band a 1-based rank into PODIUM / CONTENDER / BUILDING."""
    raise NotImplementedError


def title_for(row: Dict[str, Any]) -> Optional[str]:
    """Deterministic earned title derived from the score profile."""
    raise NotImplementedError


def badge_keys_for(row: Dict[str, Any], rank: int = 0, prev_rank: Optional[int] = None) -> List[str]:
    """Computed badge keys for a row (e.g. eligibility_100)."""
    raise NotImplementedError


def build_leaderboard_row(
    raw_row: Dict[str, Any],
    rank: int,
    total: int,
    viewer_roles: Any,
    prev_rank: Optional[int] = None,
) -> Dict[str, Any]:
    """Presentation row: tier_label, title_earned, badge_keys, rank_delta,
    with rupee/revenue fields stripped for junior viewer roles."""
    raise NotImplementedError


def titles_catalog() -> List[Dict[str, Any]]:
    """Catalog of all earnable titles + badges (for the FE legend)."""
    return []


def leaderboard_config_defaults() -> Dict[str, Any]:
    """Default leaderboard_config sub-doc."""
    raise NotImplementedError
