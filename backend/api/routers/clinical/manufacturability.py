"""CLI-7 frame + lens + Rx manufacturability pre-check.

Moved verbatim out of the 3,158-line api/routers/clinical.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import Depends
from pydantic import BaseModel
from typing import Optional
from ..auth import get_current_user
from ...dependencies import get_db
from ._shared import router


# ============================================================================
# CLI-7 — Frame + lens + Rx manufacturability pre-check
# ============================================================================
# Before a workshop job is created the sales staff can ask "can we actually
# make this?".  The check compares the customer's Rx powers against the
# selected lens line's sph_range / cyl_range / add_range from the lens_catalog
# collection, and validates the frame's B-size / min-fitting-height against the
# prescribed segment-height if a segment height is known.
#
# All checks are SOFT — they return warnings/flags, never block the save.
# This is a *decision-support* tool, not an enforcement gate.  The lab may
# have an up-to-date stock beyond what the catalog records.
# Fail-soft: if lens_catalog is absent or the lens_line_id is unknown, return
# an informational "cannot verify" result rather than an error.
# ============================================================================


class ManufacturabilityRequest(BaseModel):
    """Payload for POST /clinical/manufacturability-check."""

    lens_line_id: Optional[str] = None  # ID of the lens line from lens_catalog
    right_eye: Optional[dict] = None    # Rx right eye {sph, cyl, axis, add}
    left_eye: Optional[dict] = None     # Rx left eye
    frame_b_size: Optional[str] = None  # vertical frame measurement (mm)
    segment_height: Optional[str] = None  # requested seg height (mm)

    class Config:
        populate_by_name = True


def _parse_float_safe(v) -> Optional[float]:
    """Parse a numeric field that may arrive as str or number."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _check_power_in_range(label: str, value: Optional[float], rng: dict, issues: list) -> None:
    """Append a warning to `issues` when `value` falls outside [rng.min, rng.max]."""
    if value is None or not rng:
        return
    lo = _parse_float_safe(rng.get("min"))
    hi = _parse_float_safe(rng.get("max"))
    if lo is not None and value < lo:
        issues.append(f"{label} {value:+.2f} is below the lens line minimum ({lo:+.2f})")
    if hi is not None and value > hi:
        issues.append(f"{label} {value:+.2f} exceeds the lens line maximum ({hi:+.2f})")


@router.post("/manufacturability-check")
async def manufacturability_check(
    payload: ManufacturabilityRequest,
    current_user: dict = Depends(get_current_user),
):
    """Pre-check whether a frame + lens + Rx combination is manufacturable.

    Returns:
      { feasible: bool, warnings: [str], info: str }

    `feasible` is True when no definite out-of-range issues were found.
    A True result does NOT guarantee stock availability; it only means the
    lens line's catalogue range can cover the Rx.  Fail-soft: if the lens
    line is unknown or the catalog is unavailable, returns feasible=null
    (cannot determine) with an explanatory info string.
    """
    warnings: list = []
    info_parts: list = []
    feasible = True  # optimistic default; flipped to False on hard issues
    lens_line: Optional[dict] = None

    # -- 1. Look up the lens line from the catalog (fail-soft) ----------------
    if payload.lens_line_id:
        try:
            db = get_db()
            if db is not None:
                col = db.get_collection("lens_catalog")
                lens_line = col.find_one({"lens_line_id": payload.lens_line_id}, {"_id": 0})
        except Exception:
            lens_line = None

        if lens_line is None:
            info_parts.append(f"Lens line '{payload.lens_line_id}' not found in catalog — power range cannot be verified.")
            feasible = None  # indeterminate
        else:
            info_parts.append(f"Lens: {lens_line.get('brand', '')} {lens_line.get('series', '')} {lens_line.get('index', '')}x")
    else:
        info_parts.append("No lens line selected — skipping power-range check.")

    # -- 2. Eye power range checks against the lens line ----------------------
    if lens_line:
        sph_range = lens_line.get("sph_range") or {}
        cyl_range = lens_line.get("cyl_range") or {}
        add_range  = lens_line.get("add_range") or {}
        has_add    = lens_line.get("has_add", False)

        for eye_label, eye in (("Right (OD)", payload.right_eye), ("Left (OS)", payload.left_eye)):
            if not eye:
                continue
            sph = _parse_float_safe(eye.get("sph") or eye.get("sphere"))
            cyl = _parse_float_safe(eye.get("cyl") or eye.get("cylinder"))
            add = _parse_float_safe(eye.get("add") or eye.get("addition"))

            _check_power_in_range(f"{eye_label} SPH", sph, sph_range, warnings)
            _check_power_in_range(f"{eye_label} CYL", cyl, cyl_range, warnings)
            if has_add and add is not None:
                _check_power_in_range(f"{eye_label} ADD", add, add_range, warnings)
            elif not has_add and add is not None and abs(add) > 0.01:
                warnings.append(
                    f"{eye_label}: ADD {add:+.2f} prescribed but selected lens line is single-vision (has_add=False)"
                )

    # -- 3. Frame / fitting geometry check ------------------------------------
    if payload.frame_b_size and payload.segment_height:
        b = _parse_float_safe(payload.frame_b_size)
        sh = _parse_float_safe(payload.segment_height)
        if b is not None and sh is not None:
            # Ophthalmic rule: min frame B = (2 x seg_height) + 2 mm safety
            min_b = sh * 2 + 2
            if b < min_b:
                warnings.append(
                    f"Frame B-size {b:.1f} mm may be too small for seg height {sh:.1f} mm "
                    f"(minimum recommended B = {min_b:.1f} mm)"
                )
            else:
                info_parts.append(f"Frame geometry OK: B {b:.1f} mm fits seg ht {sh:.1f} mm")

    # Any warnings flip feasible to False (when we had enough data to check)
    if warnings and feasible is True:
        feasible = False

    return {
        "feasible": feasible,
        "warnings": warnings,
        "info": "; ".join(info_parts) if info_parts else "Check complete.",
    }
