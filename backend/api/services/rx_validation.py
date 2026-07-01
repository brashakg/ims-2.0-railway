"""
IMS 2.0 - Shared Prescription (Rx) value validation
====================================================
SINGLE source of truth for clinical Rx power limits + the 0.25-diopter grid /
integer-axis / cyl-requires-axis rules. Factored out of routers/prescriptions.py
(its behaviour is preserved) so the POS / order-create path can reuse the EXACT
same validator the clinical paths use, instead of re-deriving the limits. See
docs/SYSTEM_INTENT.md section 4 and CLAUDE.md business rules.

Owner-approved "wider extremes" realistic limits (2026-06):

  SPH (Sphere):   -25.00 to +25.00 diopters, 0.25 steps
  CYL (Cylinder):  -6.00 to  +6.00 diopters, 0.25 steps
  AXIS:            1 to 180 degrees (WHOLE number); MANDATORY when cyl != 0
  ADD (Addition): +0.75 to  +4.00 diopters, 0.25 steps (PLUS-ONLY)
  PD (Pupillary Distance): 40 to 80 mm (a measurement, not Rx -> no 0.25 grid)
  CL Base Curve (base_curve): 8.0 to 9.5 mm
  CL Diameter (diameter):    13.0 to 15.0 mm

These are the ONLY source of truth. Endpoint-level checks call these helpers --
do NOT duplicate the ranges elsewhere. To relax a limit, change _RX_LIMITS here
AND the spec docs (+ the frontend constants/rxLimits.ts twin); never add a
second copy. The frontend mirror is frontend/src/constants/rxLimits.ts -- keep
the two in lockstep.

Sign handling: every numeric parse below goes through float()/_coerce_float,
which natively accepts a leading '+' (float('+5.00') == 5.0). A signed string
like "+5.00" / "-0.75" therefore round-trips through this validator with its
sign intact -- do NOT add a regex that would reject the '+'.
"""
from typing import Optional

# ASCII only (Windows cp1252) -- no emoji / unicode in any message string.

_RX_LIMITS = {
    "sph": (-25.0, 25.0),
    "cyl": (-6.0, 6.0),
    "add": (0.75, 4.00),
    # PD comes in two shapes: a BINOCULAR (total) PD of 40-80 mm, and the
    # per-eye MONOCULAR PD (~half the binocular, ~20-45 mm) captured in each
    # eye's `pd` field. The canonical owner limit (40-80) is the binocular one;
    # a monocular per-eye value must NOT be rejected for being < 40.
    "pd": (40.0, 80.0),          # binocular / total PD (IPD)
    "pd_mono": (20.0, 45.0),     # per-eye monocular PD
    # Contact-lens millimetre measurements (fit params). Not dioptric -> no
    # 0.25 grid; a plain range check only. cl_power/cl_cyl/cl_add reuse the
    # sph/cyl/add dioptric limits below (see _CL_ALIASES).
    "base_curve": (8.0, 9.5),
    "diameter": (13.0, 15.0),
}

# Dioptric fields that move on the 0.25 grid (linear measures like PD /
# base_curve / diameter are exempt from the step check).
_STEP_FIELDS = ("sph", "cyl", "add", "cl_power", "cl_cyl", "cl_add")

# Contact-lens dioptric powers reuse the spectacle dioptric limits: cl_power ~
# sph range, cl_cyl ~ cyl range, cl_add ~ add range.
_CL_ALIASES = {"cl_power": "sph", "cl_cyl": "cyl", "cl_add": "add"}


def _limits_for(field_name: str):
    """Resolve the (lo, hi) tuple for a field, mapping CL dioptric aliases onto
    their spectacle equivalents. Unknown fields get a permissive default."""
    key = _CL_ALIASES.get(field_name, field_name)
    return _RX_LIMITS.get(key, (-999, 999))


def _coerce_float(value, field_name: str) -> float:
    """float() a value, accepting a leading '+' (float('+5') == 5.0). Raises a
    ValueError with a clear message on a non-numeric string."""
    try:
        return float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{field_name} must be a valid number, got '{value}'")


def _validate_rx_value(value: Optional[str], field_name: str) -> Optional[str]:
    """Validate that an Rx STRING value falls within acceptable clinical range.

    Raises ValueError on a bad value (out of range / off the 0.25 grid /
    non-numeric). Blank / None / "0" pass through unchanged (plano). A leading
    '+' is accepted (float('+5.00') == 5.0) so a signed string never trips."""
    if value is None or value.strip() == "" or value.strip() == "0":
        return value
    num = _coerce_float(value, field_name)
    lo, hi = _limits_for(field_name)
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
    out-of-range power past the validation the string path enforces. A leading
    '+' in a string value is accepted (float('+5') == 5.0)."""
    if value is None:
        return value
    # A blank / whitespace string is "not entered" -- mirror the string path.
    if isinstance(value, str) and value.strip() == "":
        return value
    num = _coerce_float(value, field_name)
    if num == 0:  # plano / no-add -- mirror the string validator's "0" pass-through
        return value
    lo, hi = _limits_for(field_name)
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


def _validate_measurement(value, field_name: str):
    """Range-only check for a linear millimetre measurement (PD / base_curve /
    diameter): no 0.25 grid, no sign rules. Blank / None passes. A leading '+'
    is accepted. Raises ValueError when out of range."""
    if value is None:
        return value
    if isinstance(value, str) and value.strip() in ("", "0"):
        # A blank measurement is "not recorded"; unlike a dioptric power, "0"
        # is not a meaningful PD/BC/DIA so treat it as not-entered too.
        return value
    num = _coerce_float(value, field_name)
    lo, hi = _limits_for(field_name)
    if num < lo or num > hi:
        raise ValueError(
            f"{field_name} value {num} is outside the valid range ({lo} to {hi}). "
            f"Please double-check the prescription."
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
# Owner decision 2026-06-18 ("block Rx lenses, allow contacts"): a SPECTACLE
# (optical / single-vision / bifocal / progressive) lens dispenses a ground
# prescription power, so an order line for one MUST be backed by a real
# prescription. CONTACT LENSES are EXEMPT from the hard Rx-required gate so a
# repeat daily-disposable / colored-contact sale is never blocked at POS.
# Frames, sunglasses, accessories, services, plano readers are non-Rx and exempt.
#
# We classify on the normalised item_type / category token. Kept permissive
# (substring match on lens/contact) so the many catalog spellings resolve, while
# FRAME / SUNGLASS / WATCH / ACCESSORY / SERVICE stay exempt.

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
    """True when an order line is a SPECTACLE / prescription LENS that MUST be
    backed by a prescription_id at POS. Frame / sunglass / accessory / service
    AND contact-lens lines return False.

    Owner decision 2026-06-18 ("block Rx lenses, allow contacts"): contact
    lenses are EXEMPT from the hard Rx-required gate (a repeat daily-disposable
    or colored-contact sale must not be blocked). NOTE: clinical power-range
    validation (sph/cyl/add/axis) still runs on EVERY line upstream of this
    check -- this gate is ONLY about *requiring a linked prescription*.

    Resolution: item_type is the line's true nature at POS; category is a
    fallback. Both are matched case-insensitively; contact takes precedence so a
    CL is never mis-classified as a required spectacle lens."""
    tokens = []
    for raw in (item_type, category):
        if raw:
            tokens.append(str(raw).strip().upper().replace("-", "_").replace(" ", "_"))
    # Contact lenses are EXEMPT: if EITHER field says contact, the line is a CL
    # and carries no hard prescription requirement.
    for token in tokens:
        if token in _CONTACT_LENS_TYPES or "CONTACT" in token:
            return False
    # Spectacle / prescription lens -> prescription_id mandatory. After the CL
    # exemption above, any remaining "LENS" token is a spectacle Rx lens
    # (OPTICAL_LENS / SPECTACLE_LENS / RX_LENS(ES) / *_LENS / LENS). FRAME /
    # SUNGLASS / WATCH / ACCESSORY / SERVICE contain no "LENS" token.
    for token in tokens:
        if token in _RX_LENS_TYPES or "LENS" in token:
            return True
    return False
