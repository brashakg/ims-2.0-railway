"""Cross-store inventory balancing (Feature #1).

PURE READ-ONLY analytics + proposal engine. NEVER mutates stock and NEVER
executes a transfer. For each product, computes per-store velocity over an
N-day window, classifies each (product, store) as DEAD / OVERSTOCK / HEALTHY /
UNDERSTOCK / STOCKOUT, then proposes unit moves from overstock/dead donor
stores to the highest-velocity understock/stockout recipient store.

A manager acts on a proposal via the EXISTING transfers flow. This module does
not import or call any transfer-execution code.
"""

from __future__ import annotations

import math
from typing import Any

# E2 policy keys + fail-soft defaults.
POLICY_WINDOW_DAYS = "inventory.balancing_window_days"
POLICY_OVERSTOCK_DAYS_COVER = "inventory.overstock_days_cover"
POLICY_UNDERSTOCK_DAYS_COVER = "inventory.understock_days_cover"
POLICY_TARGET_DAYS_COVER = "inventory.target_days_cover"

DEFAULT_WINDOW_DAYS = 90
DEFAULT_OVERSTOCK_DAYS_COVER = 120
DEFAULT_UNDERSTOCK_DAYS_COVER = 21
DEFAULT_TARGET_DAYS_COVER = 45

# Classification labels.
DEAD = "DEAD"
OVERSTOCK = "OVERSTOCK"
HEALTHY = "HEALTHY"
UNDERSTOCK = "UNDERSTOCK"
STOCKOUT = "STOCKOUT"


def classify_store_stock(*args: Any, **kwargs: Any) -> list[dict]:
    """Stub: classify each (product, store) stat dict. Fleshed out below."""
    return []


def propose_moves(*args: Any, **kwargs: Any) -> list[dict]:
    """Stub: pure proposal engine over per-(product, store) stats. Returns []."""
    return []
