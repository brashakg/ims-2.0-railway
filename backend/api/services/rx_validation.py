"""
IMS 2.0 - Shared Prescription (Rx) value validation
====================================================
SINGLE source of truth for clinical Rx power limits + the 0.25-diopter grid /
integer-axis / cyl-requires-axis rules. Factored out of routers/prescriptions.py
(its behaviour is preserved byte-for-byte) so the POS / order-create path can
reuse the EXACT same validator the clinical paths use, instead of re-deriving
the limits. See docs/SYSTEM_INTENT.md section 4 and CLAUDE.md business rules.

  SPH (Sphere):   -20.00 to +20.00 diopters, 0.25 steps
  CYL (Cylinder):  -6.00 to  +6.00 diopters, 0.25 steps
  AXIS:            1 to 180 degrees (WHOLE number); mandatory when cyl != 0
  ADD (Addition): +0.75 to  +3.50 diopters, 0.25 steps
  PD (Pupillary Distance): 20 to 80 mm (a measurement, not Rx -> no 0.25 grid)

These are the ONLY source of truth. Endpoint-level checks call these helpers --
do NOT duplicate the ranges elsewhere. To relax a limit, change _RX_LIMITS here
AND the spec docs; never add a second copy.
"""
from typing import Optional

# ASCII only (Windows cp1252) -- no emoji / unicode in any message string.

_RX_LIMITS = {
    "sph": (-20.0, 20.0),
    "cyl": (-6.0, 6.0),
    "add": (0.75, 3.50),
    "pd": (20.0, 80.0),
}

# Dioptric fields that move on the 0.25 grid (linear measures like PD /
# base_curve / diameter are exempt from the step check).
_STEP_FIELDS = ("sph", "cyl", "add", "cl_power", "cl_cyl", "cl_add")


def _validate_rx_value(value: Optional[str], field_name: str) -> Optional[str]:
    """Validate that an Rx STRING value falls within acceptable clinical range.

    Raises ValueError on a bad value (out of range / off the 0.25 grid /
    non-numeric). Blank / None / "0" pass through unchanged (plano)."""
    if value is None or value.strip() == "" or value.strip() == "0":
        return value
    try:
        num = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{field_name} must be a valid number, got '{value}'")
    lo, hi = _RX_LIMITS.get(field_name, (-999, 999))
    if num < lo or num > hi:
        raise ValueError(
            f"{field_name} value {num} is outside the valid range ({lo} to {hi}). "
            f"Please double-check the prescription."
        )
    # SYSTEM_INTENT section 4: dioptric powers move in 0.25 steps. Reject
    # off-step values (e.g. +1.30, +0.10) that no lens is ground to. AXIS is
    # integer-checked elsewhere; linear measures (PD/base_curve/diameter) are
    # exempt from the 0.25 grid.
    if field_name in _STEP_FIELDS:
        if round(num * 100) % 25 != 0:
            raise ValueError(
                f"{field_name} value {num} must be in 0.25-diopter steps "
                f"(e.g. -1.25, 0.00, +2.50)."
            )
    return value


def _validate_rx_number(value, field_name: str):
    """Numeric (float) variant of _validate_rx_value for the 4-version Rx model,
    which stores sphere/cylinder/addition as floats. Applies the SAME ranges +
    0.25-diopter grid, so a numeric path can no longer be used to slip an
    out-of-range power past the validation the string path enforces."""
    if value is None:
        return value
    try:
        num = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{field_name} must be a valid number, got '{value}'")
    if num == 0:  # plano / no-add -- mirror the string validator's "0" pass-through
        return value
    lo, hi = _RX_LIMITS.get(field_name, (-999, 999))
    if num < lo or num > hi:
        raise ValueError(
            f"{field_name} value {num} is outside the valid range ({lo} to {hi}). "
            f"Please double-check the prescription."
        )
    if field_name in _STEP_FIELDS:
        if round(num * 100) % 25 != 0:
            raise ValueError(
                f"{field_name} value {num} must be in 0.25-diopter steps "
                f"(e.g. -1.25, 0.00, +2.50)."
            )
    return value


def _validate_axis(value, *, cyl=None) -> None:
    """AXIS is a WHOLE degree 1..180. When the cylinder is non-zero the axis is
    MANDATORY (a toric lens is un-grindable without an axis). Raises ValueError
    on a bad value. Mirrors the EyeData / validate_prescription clinical checks.

    `value` may be None, an int, or a numeric string. `cyl` (when supplied as a
    non-zero value) makes the axis required."""
    cyl_nonzero = False
    if cyl is not None and str(cyl).strip() not in ("", "0"):
        try:
            cyl_nonzero = abs(float(cyl)) > 1e-9
        except (ValueError, TypeError):
            cyl_nonzero = False

    if value is None or str(value).strip() == "":
        if cyl_nonzero:
            raise ValueError("axis (1-180) is required when cylinder is non-zero")
        return

    try:
        axis_f = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"axis must be a whole number 1-180, got '{value}'")
    if not (1 <= axis_f <= 180):
        raise ValueError(f"axis value {value} is outside the valid range (1-180).")
    if axis_f != int(axis_f):
        raise ValueError(f"axis value {value} must be a whole number (1-180).")


# ---------------------------------------------------------------------------
# Rx-required classification (which order lines must carry a prescription)
# ---------------------------------------------------------------------------
# Spectacle (Rx) lenses and contact lenses dispense a prescription power, so an
# order line for one MUST be backed by a real prescription. Frames, sunglasses,
# accessories, services, plano/zero-power readers etc. are non-Rx and exempt.
#
# We classify on the normalised item_type / category token. Kept permissive
# (substring match on lens/spectacle/contact) so the many catalog spellings all
# resolve, while FRAME / SUNGLASS / ACCESSORY / SERVICE stay exempt.

# Explicit canonical Rx-lens / contact-lens item types.
_RX_LENS_TYPES = {
    "LENS",
    "OPTICAL_LENS",
    "SPECTACLE_LENS",
    "RX_LENS",
    "PRESCRIPTION_LENS",
}
_CONTACT_LENS_TYPES = {
    "CONTACT_LENS",
    "COLORED_CONTACT_LENS",
    "COLOUR_CONTACT_LENS",
    "TORIC_CONTACT_LENS",
}


def is_rx_required_line(item_type=None, category=None) -> bool:
    """True when an order line dispenses a prescription power (spectacle Rx lens
    or contact lens) and therefore REQUIRES a prescription_id. Frame-only /
    sunglass / accessory / service lines return False.

    Resolution: item_type is the line's true nature at POS; category is a
    fallback. Both are matched case-insensitively against the canonical sets,
    then by a permissive substring check so catalog spelling variants resolve."""
    for raw in (item_type, category):
        if not raw:
            continue
        token = str(raw).strip().upper().replace("-", "_").replace(" ", "_")
        if token in _RX_LENS_TYPES or token in _CONTACT_LENS_TYPES:
            return True
        # Permissive substring: "OPTICAL LENS", "SINGLE_VISION_LENS",
        # "CONTACT LENSES", "SPECTACLE LENS" all resolve. FRAME / SUNGLASS /
        # ACCESSORY / SERVICE never contain these tokens.
        if "CONTACT_LENS" in token or token.endswith("_LENS") or token == "LENS":
            return True
    return False
