"""F13 -- Workshop remake justification + spoilage analytics (pure service).

Pure, DB-free helpers powering:
  - the seeded (owner-editable) remake reason-code taxonomy,
  - spoilage cost computation for a remade lens (integer paise, WAC-based
    via an injected resolver),
  - the spoilage analytics summary (remake rate + cost rollups by
    category / reason / technician) consumed by the workshop dashboard.

No Mongo imports here -- callers inject data; keeps this unit-testable.
The only DB-touching helpers (ensure_reason_codes / list_codes / valid_codes)
take the db handle as an argument and are strictly fail-soft: any error or an
absent DB falls back to the seeded DEFAULT_REASON_CODES taxonomy.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .non_adapt import rupees_to_paise

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

# Singleton config doc: collection `remake_reason_codes`, _id == the same name
# (mirrors the purchase_settings `_id: "default"` singleton convention).
REASON_CODES_DOC_ID = "remake_reason_codes"

# Bucket keys for entries missing a code/category (never invented by the
# router -- it always stamps both -- but legacy/hand-edited docs degrade
# gracefully instead of crashing the rollup).
UNKNOWN_BUCKET = "UNKNOWN"


# ---------------------------------------------------------------------------
# Taxonomy persistence (db injected; strictly fail-soft)
# ---------------------------------------------------------------------------


def ensure_reason_codes(db) -> bool:
    """Idempotently seed the `remake_reason_codes` singleton doc.

    Insert-only-if-absent so an owner's edited taxonomy is NEVER clobbered by
    a redeploy. Returns True only when this call performed the insert.
    Fail-soft: no DB / any error (incl. a concurrent-seed duplicate-key race)
    -> False, never raises.
    """
    if db is None:
        return False
    try:
        coll = db.get_collection(REASON_CODES_DOC_ID)
        if coll.find_one({"_id": REASON_CODES_DOC_ID}) is not None:
            return False
        coll.insert_one(
            {
                "_id": REASON_CODES_DOC_ID,
                "codes": [dict(c) for c in DEFAULT_REASON_CODES],
                "seeded_default": True,
            }
        )
        return True
    except Exception:  # noqa: BLE001 -- incl. DuplicateKeyError on a seed race
        return False


def list_codes(db) -> List[Dict[str, str]]:
    """The taxonomy as an ORDERED list (DB doc wins; seed is the fallback).

    Fail-soft: db absent / doc absent / malformed doc -> DEFAULT_REASON_CODES
    copies (callers may mutate their copy freely).
    """
    try:
        if db is not None:
            doc = db.get_collection(REASON_CODES_DOC_ID).find_one(
                {"_id": REASON_CODES_DOC_ID}
            )
            if isinstance(doc, dict):
                codes = doc.get("codes")
                if isinstance(codes, list):
                    cleaned = [
                        dict(c)
                        for c in codes
                        if isinstance(c, dict) and str(c.get("code") or "").strip()
                    ]
                    if cleaned:
                        return cleaned
    except Exception:  # noqa: BLE001
        pass
    return [dict(c) for c in DEFAULT_REASON_CODES]


def valid_codes(db) -> Dict[str, Dict[str, str]]:
    """{code -> entry} lookup for request validation (codes uppercased)."""
    return {str(c.get("code") or "").strip().upper(): c for c in list_codes(db)}


def validate_codes_payload(codes: Any) -> Optional[str]:
    """Validate a replacement taxonomy (PUT body). None == valid, else the
    human-readable error. Pure.

    Rules: non-empty list; every entry an object with a non-empty `code` and
    `label` and a `category` in VALID_CATEGORIES; codes unique (case-folded).
    """
    if not isinstance(codes, list) or not codes:
        return "codes must be a non-empty list"
    seen: set = set()
    for i, c in enumerate(codes):
        if not isinstance(c, dict):
            return f"codes[{i}] must be an object with code/label/category"
        code = str(c.get("code") or "").strip()
        label = str(c.get("label") or "").strip()
        category = str(c.get("category") or "").strip().upper()
        if not code:
            return f"codes[{i}].code is required"
        if not label:
            return f"codes[{i}].label is required"
        if category not in VALID_CATEGORIES:
            return (
                f"codes[{i}].category {category!r} is invalid; "
                f"allowed: {', '.join(VALID_CATEGORIES)}"
            )
        key = code.upper()
        if key in seen:
            return f"duplicate code {code!r}"
        seen.add(key)
    return None


def normalize_codes_payload(codes: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Canonical form for persistence (call AFTER validate_codes_payload):
    code/category uppercased, label stripped. Pure."""
    return [
        {
            "code": str(c.get("code") or "").strip().upper(),
            "label": str(c.get("label") or "").strip(),
            "category": str(c.get("category") or "").strip().upper(),
        }
        for c in codes
    ]


# ---------------------------------------------------------------------------
# Spoilage cost (pure; resolver injected)
# ---------------------------------------------------------------------------


def spoilage_cost_paise(
    job: Dict[str, Any],
    lens_cost_resolver: Optional[Callable[[Dict[str, Any]], Optional[float]]],
) -> int:
    """Cost of the spoiled (remade) lens, in integer paise (>= 0).

    `lens_cost_resolver(job)` returns the lens cost in RUPEES (typically the
    product's `cost_price`, which IS the weighted-average cost -- maintained
    by purchase_match.moving_average_cost true-ups at invoice booking) or
    None when unresolvable. None-safe + total: a missing resolver, a raising
    resolver, None, or junk all collapse to 0 -- a spoilage record must never
    fail because costing data is incomplete.
    """
    if lens_cost_resolver is None:
        return 0
    try:
        rupees = lens_cost_resolver(job)
    except Exception:  # noqa: BLE001
        return 0
    if rupees is None:
        return 0
    try:
        paise = rupees_to_paise(float(rupees))
    except (TypeError, ValueError):
        return 0
    return max(0, paise)


# ---------------------------------------------------------------------------
# Analytics summary (pure, plain dicts in / plain dicts out)
# ---------------------------------------------------------------------------


def build_spoilage_summary(
    jobs: List[Dict[str, Any]],
    *,
    window_days: int = 90,
) -> Dict[str, Any]:
    """Remake rate + spoilage cost rollups over the given jobs. PURE.

    The caller (router) selects the job window; this just aggregates:
      total_jobs                 -- len(jobs)
      jobs_with_remake           -- jobs carrying >= 1 remake_reasons entry
      total_remakes              -- total remake_reasons entries (a job remade
                                    twice bleeds margin twice)
      remake_rate_pct            -- jobs_with_remake / total_jobs * 100, 1dp
      spoilage_cost_total_paise  -- sum of entry cost_paise
      by_category / by_reason    -- {key: {count, cost_paise}}
      by_technician              -- keyed on the job's technician_id
                                    (assign_technician's field); UNASSIGNED
                                    when the job was never assigned
      top_reasons                -- [{reason_code, count, cost_paise}] sorted
                                    by count desc (cost desc, code asc ties)
    `window_days` is echoed into the payload for the dashboard caption.
    """
    clean_jobs = [j for j in (jobs or []) if isinstance(j, dict)]
    total_jobs = len(clean_jobs)

    jobs_with_remake = 0
    total_remakes = 0
    cost_total = 0
    by_category: Dict[str, Dict[str, int]] = {}
    by_reason: Dict[str, Dict[str, int]] = {}
    by_technician: Dict[str, Dict[str, int]] = {}

    def _bump(bucket: Dict[str, Dict[str, int]], key: str, cost: int) -> None:
        row = bucket.setdefault(key, {"count": 0, "cost_paise": 0})
        row["count"] += 1
        row["cost_paise"] += cost

    for job in clean_jobs:
        entries = [
            e for e in (job.get("remake_reasons") or []) if isinstance(e, dict)
        ]
        if not entries:
            continue
        jobs_with_remake += 1
        technician = str(job.get("technician_id") or "").strip() or "UNASSIGNED"
        for entry in entries:
            total_remakes += 1
            try:
                cost = max(0, int(entry.get("cost_paise") or 0))
            except (TypeError, ValueError):
                cost = 0
            cost_total += cost
            reason = (
                str(entry.get("reason_code") or "").strip().upper() or UNKNOWN_BUCKET
            )
            category = (
                str(entry.get("category") or "").strip().upper() or UNKNOWN_BUCKET
            )
            _bump(by_reason, reason, cost)
            _bump(by_category, category, cost)
            _bump(by_technician, technician, cost)

    remake_rate_pct = (
        round(100.0 * jobs_with_remake / total_jobs, 1) if total_jobs else 0.0
    )

    top_reasons = [
        {"reason_code": code, "count": row["count"], "cost_paise": row["cost_paise"]}
        for code, row in by_reason.items()
    ]
    top_reasons.sort(key=lambda r: (-r["count"], -r["cost_paise"], r["reason_code"]))

    return {
        "window_days": int(window_days),
        "total_jobs": total_jobs,
        "jobs_with_remake": jobs_with_remake,
        "total_remakes": total_remakes,
        "remake_rate_pct": remake_rate_pct,
        "spoilage_cost_total_paise": cost_total,
        "by_category": by_category,
        "by_reason": by_reason,
        "by_technician": by_technician,
        "top_reasons": top_reasons,
    }
