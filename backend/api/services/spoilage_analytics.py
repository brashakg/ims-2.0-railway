"""F13 -- Workshop remake justification + spoilage analytics (pure service).

Pure, DB-free helpers powering:
  - the seeded (owner-editable) remake reason-code taxonomy,
  - spoilage cost computation for a remade lens (integer paise, WAC-based
    via an injected resolver),
  - the spoilage analytics summary (remake rate + cost rollups by
    category / reason / technician) consumed by the workshop dashboard.

No Mongo imports here -- callers inject data; keeps this unit-testable.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

# Spoilage fault categories
CATEGORY_LAB_FAULT = "LAB_FAULT"
CATEGORY_STORE_FAULT = "STORE_FAULT"
CATEGORY_VENDOR_FAULT = "VENDOR_FAULT"
CATEGORY_CUSTOMER = "CUSTOMER"

VALID_CATEGORIES = (
    CATEGORY_LAB_FAULT,
    CATEGORY_STORE_FAULT,
    CATEGORY_VENDOR_FAULT,
    CATEGORY_CUSTOMER,
)

# Seeded default taxonomy -- owner-EDITABLE via
# PUT /api/v1/workshop/remake-reason-codes (stored in the
# `remake_reason_codes` singleton config doc; this list is only the seed).
DEFAULT_REASON_CODES: List[Dict[str, str]] = [
    {"code": "AXIS_ERROR", "label": "Axis error", "category": CATEGORY_LAB_FAULT},
    {"code": "POWER_ERROR", "label": "Power error", "category": CATEGORY_LAB_FAULT},
    {"code": "FITTING_ERROR", "label": "Fitting error", "category": CATEGORY_LAB_FAULT},
    {"code": "SURFACE_DEFECT", "label": "Surface defect", "category": CATEGORY_VENDOR_FAULT},
    {"code": "COATING_DEFECT", "label": "Coating defect", "category": CATEGORY_VENDOR_FAULT},
    {"code": "BREAKAGE_IN_LAB", "label": "Breakage in lab", "category": CATEGORY_LAB_FAULT},
    {"code": "WRONG_LENS_PICKED", "label": "Wrong lens picked", "category": CATEGORY_STORE_FAULT},
    {"code": "CUSTOMER_CHANGED_RX", "label": "Customer changed Rx", "category": CATEGORY_CUSTOMER},
    {"code": "OTHER", "label": "Other", "category": CATEGORY_LAB_FAULT},
]


def spoilage_cost_paise(
    job: Dict[str, Any],
    lens_cost_resolver: Callable[[Dict[str, Any]], Optional[float]],
) -> int:
    """Cost of the spoiled (remade) lens, in integer paise. Stub."""
    raise NotImplementedError


def build_spoilage_summary(
    jobs: List[Dict[str, Any]],
    window_days: int = 30,
) -> Dict[str, Any]:
    """Remake rate + spoilage cost rollups for a job window. Stub."""
    raise NotImplementedError
