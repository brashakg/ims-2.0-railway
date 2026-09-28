"""Exam helpers: the Rx display renderers (re-exported from
services.rx_print_values), eye-test Rx / keratometry / exam-block
validation, the storage shapers and the camelCase converters.

Moved verbatim out of the 3,158-line api/routers/clinical.py (Wave 6
package split): no path, method, dependency, status code, response_model,
default or validation was changed.
"""

from fastapi import HTTPException
from typing import List
from .models import AutoRefEyeReading, EyeTestData


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


# ---------------------------------------------------------------------------
# Rx display renderers.
# ---------------------------------------------------------------------------
# These now live in api/services/rx_print_values.py, beside the absence rule
# they share a contract with, and are re-exported here so every existing import
# (and the tests pinning them) keeps working unchanged. They MOVED rather than
# being copied because the surfaces that most needed them -- the workshop lab
# job-card and the prescriptions print card -- were interpolating the stored
# string raw and printing a positive power with no "+", and a router is not
# somewhere another router can import from.
from ...services.rx_print_values import (  # noqa: E402,F401
    format_axis_value,
    format_rx_value,
)


def _validate_eye_test_rx(eye_label: str, eye: dict) -> None:
    """Validate the Rx powers captured on an eye-test eye dict against the
    canonical clinical ranges (SPH -25..+25, CYL -6..+6, AXIS 1-180 WHOLE,
    ADD +0.75..+4.00, all dioptric powers on the 0.25-diopter grid).

    Reuses the SINGLE source-of-truth validators in api.services.rx_validation
    so the eye-test capture path -- which auto-creates a prescription on
    completion -- can never persist an Rx the prescriptions endpoint would
    reject. Raises HTTPException(422) on a violation. None / empty / "0" values
    are tolerated (a blank cell is valid) exactly as the prescription validator
    does.

    PATIENT SAFETY (F11 + F20), both delegated to `_validate_eye_axis`:
      * a non-zero CYL with NO axis is REJECTED. A toric Rx without an axis is
        un-grindable: it used to flow on to POS and the workshop job, the lab
        ground it to a guessed axis, and the patient got headaches/blur and a
        remake. A zero / absent cylinder is unaffected -- no axis needed.
      * a FRACTIONAL axis (90.5) is REJECTED, not rounded. Validation used to
        round for the check (int(round(90.5)) -> 91) while storing the raw 90.5,
        so the stored Rx and the whole-degree workshop spec disagreed.

    `eye` carries the frontend's loose shape: sphere/sph, cylinder/cyl, axis,
    add. `_eye_value` (shared with prescriptions.py) resolves each alias pair to
    whichever key actually carries a value, so a mixed-shape payload can't slip
    a power past the checks by leaving its twin key present-but-null.
    """
    from ..prescriptions import _eye_value, _validate_eye_axis
    from ...services.rx_validation import (
        _validate_measurement,
        _validate_rx_value,
        _validate_visual_acuity,
    )

    if not isinstance(eye, dict):
        return

    def _as_str(v):
        # The shared validator takes Optional[str]; numbers stringify cleanly,
        # None passes straight through (treated as "no value").
        if v is None:
            return None
        return str(v)

    cyl = _eye_value(eye, "cylinder", "cyl")
    pairs = (
        ("sph", _eye_value(eye, "sphere", "sph")),
        ("cyl", cyl),
        ("add", _eye_value(eye, "add", "addition")),
    )
    for field_name, raw in pairs:
        try:
            _validate_rx_value(_as_str(raw), field_name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"{eye_label} {exc}")

    # PER-EYE PD. This eye-test path writes the Rx as a RAW DICT straight to
    # rx_repo.create(), so EyeData's own field validators never run on it and a
    # PD of 9999 reached a billable, dispensable prescription: a garbage
    # monocular PD decentres the lens and induces prism. The per-eye box is
    # MONOCULAR, so "pd_mono" (20-45mm) is the right bound, not the binocular
    # 40-80 one. Same rule as EyeData.validate_pd.
    try:
        _validate_measurement(_as_str(_eye_value(eye, "pd")), "pd_mono")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{eye_label} {exc}")

    # VISUAL ACUITY. Free text on the wire, a closed clinical set in reality:
    # "banana" and "20/9999" both saved with a 200 into the exam block AND into
    # the mirrored prescription, because no validator in backend/ had ever
    # looked at this field. The exam form gates it client-side, so the exposure
    # is the direct API, a device/CSV import and the integrations.
    try:
        _validate_visual_acuity(_eye_value(eye, "va", "acuity"), "VA")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{eye_label} {exc}")

    # PRISM travels as free text through a bare <input> on the Final Rx tab and
    # is persisted verbatim. Mirrors EyeData.validate_prism (0-10 dioptres).
    prism = _eye_value(eye, "prism")
    if prism is not None and str(prism).strip() != "":
        try:
            mag = float(str(prism).strip())
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=422,
                detail=f"{eye_label} prism must be a number in prism dioptres (0-10)",
            )
        if not (0.0 <= mag <= 10.0):
            raise HTTPException(
                status_code=422,
                detail=f"{eye_label} prism must be between 0 and 10 prism dioptres",
            )

    _validate_eye_axis(eye_label, cyl, eye.get("axis"), status_code=422)


def _validate_eye_test_payload(data: "EyeTestData") -> None:
    """The WHOLE clinical range gate for one exam payload, in one place.

    Both write doors -- POST /tests/{id}/complete and PUT /tests/{id}/exam --
    must apply identical checks: an amendment is a write of a medical power and
    gets exactly what the first write got. They used to repeat the same five
    calls, which is how the BINOCULAR IPD ended up gated in the browser and
    nowhere on the server. One reader now, so a rule added here lands on both.
    """
    from ...services.rx_validation import _validate_measurement

    _validate_eye_test_rx("Right eye", data.right_eye)
    _validate_eye_test_rx("Left eye", data.left_eye)
    # The three exam tabs that now reach the server. They used to be dropped in
    # the browser, so no layer -- not the form, not the transport, not a
    # Pydantic model, not a validator -- had ever looked at a lensometer or
    # auto-ref power. -9999 went in and nothing objected.
    _validate_exam_block("Lensometer", data.lensometer)
    _validate_exam_block("Auto-Ref", data.auto_ref)
    _validate_exam_block("Subjective Rx", data.subjective_rx)

    # THE BINOCULAR IPD: the single number that centres BOTH lenses in the
    # frame. It is not the per-eye monocular PD (already gated above as
    # "pd_mono", 20-45mm) -- it is its 40-80mm twin, and it was validated in the
    # browser and NOWHERE on the server, so an IPD of 9999 from a device
    # import, a CSV or a direct call reached a billable prescription and the
    # lab. zero_is_blank=False because this field is new on the exam document:
    # there is no legacy corpus of "0" to tolerate, and a 0mm IPD is
    # anatomically impossible, exactly as the form has always said.
    try:
        _validate_measurement(data.ipd, "pd", zero_is_blank=False)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"IPD: {exc}")


def _validate_keratometry(eye_label: str, eye: dict) -> None:
    """Validate an auto-refractometer eye's K readings (corneal curvature).

    K1/K2 are dioptric but unsigned and off the 0.25 grid, so they use the
    canonical "k" range in rx_validation rather than the sphere range; their
    axes are ordinary 1-180 meridians. These were bare text boxes that accepted
    any string at all, and nothing on the server had ever seen them."""
    from ...services.rx_validation import _validate_axis, _validate_measurement

    if not isinstance(eye, dict):
        return
    for key, label in (("k1", "K1"), ("k2", "K2")):
        value = eye.get(key)
        if value is None or str(value).strip() == "":
            continue
        try:
            _validate_measurement(str(value), "k")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"{eye_label} {label} {exc}")
    for key, label in (("k1_axis", "K1 axis"), ("k2_axis", "K2 axis")):
        value = eye.get(key)
        if value is None or str(value).strip() == "":
            continue
        try:
            _validate_axis(value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"{eye_label} {label}: {exc}")


def _validate_exam_block(label: str, block) -> None:
    """Range-check one exam tab (lensometer / auto-ref / subjective refraction)
    through the SAME validator the final prescription goes through."""
    if block is None:
        return
    for side, eye in (("Right eye", block.right_eye), ("Left eye", block.left_eye)):
        eye_dict = eye.model_dump(exclude_none=True)
        _validate_eye_test_rx(f"{label} {side}", eye_dict)
        if isinstance(eye, AutoRefEyeReading):
            _validate_keratometry(f"{label} {side}", eye_dict)


def _exam_blocks_for_storage(data: "EyeTestData") -> dict:
    """The exam tabs to PERSIST on the test document, snake_case, omitting the
    tabs the optometrist left untouched.

    An ABSENT tab stores nothing at all, so a quick refraction-only test writes
    exactly the document it wrote before this block existed. A tab that is
    PRESENT but whose fields are all blank still stores its (empty) shape --
    "the optometrist opened this tab and recorded nothing" is a different fact
    from "this exam predates the tab".
    """
    blocks = {}
    for key, value in (
        ("lensometer", data.lensometer),
        ("auto_ref", data.auto_ref),
        ("subjective_rx", data.subjective_rx),
        ("slit_lamp", data.slit_lamp),
    ):
        if value is not None:
            blocks[key] = value.model_dump(exclude_none=True)
    return blocks


def _exam_header_for_storage(data: "EyeTestData") -> dict:
    """The exam header fields (date / optometrist / complaint / VDU hours) the
    form collects. Only the ones actually supplied, so an absent field never
    overwrites a stored one with a blank on a later amendment."""
    header = {}
    for key, value in (
        ("exam_date", data.exam_date),
        ("optometrist_name", data.optometrist_name),
        ("chief_complaint", data.chief_complaint),
        ("vdu_usage", data.vdu_usage),
        ("exam_step", data.exam_step),
    ):
        if value is not None and str(value).strip() != "":
            header[key] = value
    # The staff-only note is the one header field a blank may overwrite: an
    # optometrist who deletes the note means "there is no note", and the exam
    # page always sends the field, so None here means the caller was not the
    # exam page and the stored note is left alone.
    if data.internal_note is not None:
        header["internal_note"] = data.internal_note.strip()
    return header


def _axis_for_storage(eye: dict):
    """The AXIS to PERSIST for a validated eye: a whole int, or None when blank.

    Storage must agree with validation (F20). `_validate_eye_test_rx` has
    already rejected a fractional / out-of-range axis by the time this runs, so
    the only job here is to normalise the surviving shapes ("90", 90.0, 90) to
    the int 90 the Rx model (EyeData.axis: Optional[int]) and the workshop spec
    expect -- instead of writing the caller's raw value through.
    """
    if not isinstance(eye, dict):
        return None
    axis = eye.get("axis")
    if axis is None or str(axis).strip() == "":
        return None
    try:
        # float(axis) -- the SAME coercion rx_validation._validate_axis uses, so
        # anything it accepted parses here too (float(str(True)) would not).
        return int(float(axis))
    except (TypeError, ValueError):
        # Genuinely unreachable: _validate_eye_test_rx ran float(axis) on this
        # exact value first. Never fabricate a value; keep the cell blank.
        return None


def _power_for_storage(eye: dict, *keys) -> str:
    """The dioptric power / measurement to PERSIST, resolved by the SAME rule
    the validator used.

    `_validate_eye_test_rx` resolves an alias pair with `_eye_value` (first
    NON-blank key), but the Rx write used to resolve it with an
    `a or b or ""` chain (first TRUTHY). The two disagree whenever the winning
    alias is a numeric zero: a payload with `cylinder: 0` plus `cyl: "-1.50"`
    validated as non-toric (no axis demanded) and then STORED -1.50 with no
    axis -- the exact un-grindable Rx the gate exists to stop. The truthiness
    chain also dropped a genuine plano `0` (stored as "" = "not tested") and
    never looked at an `addition`-keyed near-add at all.

    Returns "" for a not-entered value, matching the stored blank-cell shape.
    """
    from ..prescriptions import _eye_value

    value = _eye_value(eye, *keys)
    return "" if value is None else str(value)


def _eye_for_rx_storage(eye: dict) -> dict:
    """One eye of the FINAL Rx, in the shape the prescriptions collection stores.

    An ABSENT power/axis means "not tested for this eye" -> stored blank. Never
    fabricate a 0.00 power or a 180 axis into a billable Rx (audit P1). A
    genuine plano "0" is preserved, because "no correction needed" is a
    finding and not an absence.

    This was written out twice per call site (once per eye) in the completion
    handler; the amend handler would have made it four copies of a rule about
    what reaches a dispensable prescription.
    """
    return {
        "sph": _power_for_storage(eye, "sphere", "sph"),
        "cyl": _power_for_storage(eye, "cylinder", "cyl"),
        "axis": _axis_for_storage(eye),
        "add": _power_for_storage(eye, "add", "addition"),
        # str(x.get("pd", "")) stored the literal "None" when the per-eye PD box
        # was left blank (the key is PRESENT with a null), which later blocked
        # every edit of that eye.
        "pd": _power_for_storage(eye, "pd"),
        "prism": (eye.get("prism") or None),
        "base": (eye.get("base") or None),
        "acuity": (eye.get("acuity") or eye.get("va") or None),
    }


def _to_camel_case(snake_str: str) -> str:
    """Convert snake_case to camelCase"""
    components = snake_str.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


def _convert_to_camel(data: dict) -> dict:
    """Convert all keys in dict from snake_case to camelCase"""
    if data is None:
        return data
    result = {}
    for key, value in data.items():
        if key.startswith("_"):
            continue
        camel_key = _to_camel_case(key)
        if isinstance(value, dict):
            result[camel_key] = _convert_to_camel(value)
        elif isinstance(value, list):
            result[camel_key] = [
                _convert_to_camel(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[camel_key] = value
    return result


def _get_empty_queue() -> List[dict]:
    """Return empty queue when database not available"""
    return []


def _get_empty_tests() -> List[dict]:
    """Return empty tests when database not available"""
    return []
